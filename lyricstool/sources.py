"""Where lyrics come from. Each source answers one track strictly: artist, title and length must all agree."""
import base64
import json
import os
import pathlib
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import yaml
from opencc import OpenCC
from rapidfuzz import fuzz

from .clean import T2S, fold, has_offset, is_placeholder, last_stamp, sung_lines

S2T = OpenCC("s2t")
UA = "LyricsTool/1.0 (personal library, slow)"
MAX_DIFF = 3
SUSPEND_SECONDS = 600
NOISE = re.compile(r"\s*[\(\[][^\)\]]*(remaster|explicit|feat|bonus|deluxe|mono|stereo|version|live|cd only)[^\)\]]*[\)\]]"
                   r"|\s+-\s+.*(remaster|explicit|mono|stereo|version|live).*$", re.I)
SPLIT_ARTISTS = re.compile(r"\s*(?:,|&|、|/|;|；| x | X | feat\.? | ft\.? | featuring | with )\s*", re.I)


class Busy(Exception):
    """The source is throttling or unreachable; the track should be asked again later."""


class Retry(Exception):
    pass


@dataclass
class Query:
    artist: str
    title: str
    album: str
    length: float
    path: str
    names: list
    titles: list

    @property
    def search_title(self):
        """Without live/version noise, when that differs from the tag."""
        return self.titles[1] if len(self.titles) > 1 else self.titles[0]


@dataclass
class Hit:
    source: str
    lrc: str = ""
    instrumental: bool = False
    matched_artist: str = ""
    matched_title: str = ""
    matched_length: float = 0.0


def names_for(artist, aliases):
    """Every spelling worth searching for: configured aliases first, then the tag split at feat/&//, with Simplified forms."""
    low = (artist or "").lower()
    out = []
    for key, names in aliases.items():
        if key.lower() in low:
            out += names
    parts = [p.strip() for p in SPLIT_ARTISTS.split(artist or "") if p.strip()]
    for p in parts + [artist or ""]:
        out += [p, T2S.convert(p)]
    return list(dict.fromkeys(x for x in out if x))


def titles_for(title):
    """The title, without live/version noise, and each side of 'A / B'."""
    base = NOISE.sub("", title).strip() or title
    return list(dict.fromkeys(x.strip() for x in [title, base, *re.split(r"\s+/\s+", base)] if x.strip()))


def make_query(artist, title, album, length, path, aliases):
    return Query(artist, title, album, length, path, names_for(artist, aliases), titles_for(title))


def matches(q, artist_text, song_text, duration):
    if not duration or abs(duration - q.length) > MAX_DIFF:
        return False
    a = fold(artist_text)
    if not any(fold(n) and (fold(n) in a or (len(fold(n)) >= 3 and fuzz.partial_ratio(fold(n), a) >= 90)) for n in q.names):
        return False
    s = fold(NOISE.sub("", song_text) or song_text)
    return any(fuzz.token_set_ratio(fold(t), s) >= 85 for t in q.titles)


def usable(lrc, length):
    """Enough timed words, none past the end of the track, not a 'pure music' placeholder, no timing offset we would have to guess at."""
    if has_offset(lrc) or is_placeholder(lrc):
        return False
    return len(sung_lines(lrc)) >= 3 and last_stamp(lrc) <= length + 2


class Limiter:
    """Spaces requests, waits out throttling with visible messages, and stops asking a source that keeps refusing."""

    def __init__(self, label, delay, backoff, ctx):
        self.label, self.delay, self.backoff, self.ctx = label, delay, backoff, ctx
        self.fails = 0
        self.suspended_until = 0.0

    def call(self, fn, optional=False):
        """`optional`: one try only, and a refusal answers None instead of counting against the source."""
        if time.monotonic() < self.suspended_until:
            raise Busy(self.label)
        for attempt, wait in enumerate(([] if optional else self.backoff) + [None]):
            self.ctx.sleep(self.delay)
            if self.ctx.cancelled():
                raise Busy(self.label)
            try:
                result = fn()
                self.fails = 0
                return result
            except Retry:
                if optional:
                    return None
            if wait is None:
                break
            self.ctx.wait(wait, f"{self.label} 暫時無法查詢（來源忙碌），{{}} 秒後重試（第 {attempt + 1} 次）")
            if self.ctx.cancelled():
                raise Busy(self.label)
            self.ctx.status(f"{self.label} 重試中（第 {attempt + 1} 次）…")  # the request can take up to its 30-second timeout; the countdown must not sit at 1 meanwhile
        self.fails += 1
        if self.fails >= 3:
            self.suspended_until = time.monotonic() + SUSPEND_SECONDS
            self.fails = 0
            self.ctx.log(f"{self.label} 連續無法查詢，暫停使用 10 分鐘，其他來源繼續查詢；這段期間的歌曲之後會自動重查")
        raise Busy(self.label)


def _get_json(url, **headers):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise Retry() from e
    except (OSError, json.JSONDecodeError) as e:
        raise Retry() from e


class Sidecar:
    label = ".lrc 外掛檔"

    def find(self, q):
        p = os.path.splitext(q.path)[0] + ".lrc"
        if not os.path.exists(p):
            return None
        with open(p, "rb") as fh:
            raw = fh.read()
        try:
            text = raw.decode("utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
        except UnicodeDecodeError:  # Big5, GBK and Shift-JIS cannot be told apart reliably, and garbled lyrics are worse than none
            return None
        return Hit(self.label, text, matched_artist=q.artist, matched_title=q.title, matched_length=q.length) if usable(text, q.length) else None


def _open_ro(path):
    c = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro&immutable=1", uri=True)
    c.execute("pragma query_only=1")
    return c


def _norm(s):
    """Same shape as LRCLIB's *_lower columns."""
    out = []
    for ch in (s or "").lower():
        if ch in "'’`":
            continue
        if ord(ch) < 0x250:
            ch = "".join(x for x in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(x))
        out.append(ch if ch.isalnum() or ch.isspace() or unicodedata.combining(ch) else " ")
    return " ".join("".join(out).split())


def _forms(values):
    return list(dict.fromkeys(v for x in values for v in (_norm(x), _norm(T2S.convert(x)), _norm(S2T.convert(x))) if v))


class Lrcget:
    label = "LRCGET"

    def __init__(self, path):
        self.c = _open_ro(path)
        self.c.execute("select 1 from lyricsfiles limit 1")

    def find(self, q):
        for title in _forms(q.titles):
            for artist in _forms(q.names):
                for r in self.c.execute("select track_artist_name,track_title,track_duration,lyricsfile,instrumental from lyricsfiles "
                                        "where track_title_lower=? and track_artist_name_lower=?", (title, artist)):
                    if not r[2] or abs(r[2] - q.length) > MAX_DIFF or not r[3]:
                        continue
                    if r[4]:
                        return Hit(self.label, instrumental=True, matched_artist=r[0], matched_title=r[1], matched_length=r[2])
                    lrc = _lrcget_lrc(r[3])
                    if usable(lrc, q.length):
                        return Hit(self.label, lrc, matched_artist=r[0], matched_title=r[1], matched_length=r[2])
        return None


def _lrcget_lrc(text):
    out = []
    for line in (yaml.safe_load(text) or {}).get("lines") or []:
        ms = line.get("start_ms")
        if ms is not None:
            out.append(f"[{ms // 60000:02d}:{ms // 1000 % 60:02d}.{ms // 10 % 100:02d}]{line.get('text') or ''}")
    return "\n".join(out)


class LocalLrclib:
    label = "LRCLIB（本機）"

    def __init__(self, path):
        self.c = _open_ro(path)
        self.c.execute("select 1 from tracks limit 1")

    def find(self, q):
        rows = {}
        for f in _forms(q.titles):
            for a in _forms(q.names):
                for r in self.c.execute("select id,name,artist_name,duration,last_lyrics_id from tracks where name_lower=? and artist_name_lower=?", (f, a)):
                    rows[r[0]] = r
        instrumental = None
        for r in sorted(rows.values(), key=lambda r: abs((r[3] or 1e9) - q.length)):
            if not r[3] or abs(r[3] - q.length) > MAX_DIFF:
                continue
            for lid, synced, instr in sorted(self.c.execute("select id,synced_lyrics,instrumental from lyrics where track_id=?", (r[0],)).fetchall(), key=lambda x: x[0] != r[4]):
                if instr:
                    instrumental = instrumental or Hit(self.label, instrumental=True, matched_artist=r[2], matched_title=r[1], matched_length=r[3])
                elif usable(synced, q.length):
                    return Hit(self.label, synced, matched_artist=r[2], matched_title=r[1], matched_length=r[3])
        return instrumental


class OnlineLrclib:
    label = "LRCLIB（線上）"

    def __init__(self, ctx):
        self.lim = Limiter("LRCLIB", 0.7, [5, 15], ctx)  # its 503s are frequent and short-lived; waiting longer per call only stalls every track

    def _get(self, endpoint, params, optional=False):
        url = f"https://lrclib.net/api/{endpoint}?" + urllib.parse.urlencode(params)
        return self.lim.call(lambda: _get_json(url, **{"User-Agent": UA}), optional)

    def find(self, q):
        name = q.names[0] if q.names else q.artist
        # The exact lookup can refuse (503) for hours while search keeps working, so a refusal goes straight on to search.
        d = self._get("get", {"artist_name": q.artist, "track_name": q.title, "album_name": q.album, "duration": round(q.length)}, optional=True)
        if d and d.get("instrumental"):
            return Hit(self.label, instrumental=True, matched_artist=d.get("artistName", ""), matched_title=d.get("trackName", ""), matched_length=d.get("duration") or 0)
        if d and usable(d.get("syncedLyrics"), q.length):
            return Hit(self.label, d["syncedLyrics"], matched_artist=d.get("artistName", ""), matched_title=d.get("trackName", ""), matched_length=d.get("duration") or 0)
        for n in dict.fromkeys([q.artist, name]):
            for x in self._get("search", {"artist_name": n, "track_name": q.search_title}) or []:
                if not x.get("instrumental") and matches(q, x.get("artistName", ""), x.get("trackName", ""), x.get("duration")) and usable(x.get("syncedLyrics"), q.length):
                    return Hit(self.label, x["syncedLyrics"], matched_artist=x["artistName"], matched_title=x["trackName"], matched_length=x["duration"])
        return None


class NetEase:
    label = "網易雲"

    def __init__(self, ctx):
        self.lim = Limiter("網易雲", 4.0, [60, 180], ctx)

    def _get(self, url):
        def fetch():
            d = _get_json(url, Referer="https://music.163.com/")
            if not d or d.get("code") != 200:
                raise Retry()
            return d
        return self.lim.call(fetch)

    def find(self, q):
        instrumental = None
        for name in q.names[:2]:
            d = self._get("https://music.163.com/api/search/get/web?" + urllib.parse.urlencode({"s": f"{name} {q.search_title}", "type": 1, "limit": 20}))
            for s in (d.get("result") or {}).get("songs") or []:
                art = "/".join(a["name"] for a in s.get("artists") or []) + " " + " ".join(s.get("alias") or [])
                dur = (s.get("duration") or 0) / 1000
                if not matches(q, art, s["name"], dur):
                    continue
                ly = self._get(f"https://music.163.com/api/song/lyric?id={s['id']}&lv=1&tv=-1")
                lrc = (ly.get("lrc") or {}).get("lyric") or ""
                who = "/".join(a["name"] for a in s.get("artists") or [])
                if usable(lrc, q.length):
                    return Hit(self.label, lrc, matched_artist=who, matched_title=s["name"], matched_length=round(dur, 1))
                if ly.get("nolyric") or is_placeholder(lrc):
                    instrumental = instrumental or Hit(self.label, instrumental=True, matched_artist=who, matched_title=s["name"], matched_length=round(dur, 1))
        return instrumental


class Kugou:
    label = "酷狗"

    def __init__(self, ctx):
        self.lim = Limiter("酷狗", 2.0, [15], ctx)

    def find(self, q):
        for name in q.names[:2]:
            url = "https://krcs.kugou.com/search?" + urllib.parse.urlencode({"ver": 1, "man": "yes", "client": "pc", "keyword": f"{name} {q.search_title}", "duration": int(q.length * 1000), "hash": ""})
            k = self.lim.call(lambda: _get_json(url)) or {}
            for cand in (k.get("candidates") or [])[:12]:
                if not matches(q, cand.get("singer") or "", cand.get("song") or "", (cand.get("duration") or 0) / 1000):
                    continue
                dl = "https://lyrics.kugou.com/download?" + urllib.parse.urlencode({"ver": 1, "client": "pc", "id": cand["id"], "accesskey": cand["accesskey"], "fmt": "lrc", "charset": "utf8"})
                d = self.lim.call(lambda: _get_json(dl)) or {}
                try:
                    lrc = base64.b64decode(d.get("content") or "").decode("utf-8", "replace")
                except ValueError:
                    continue
                if usable(lrc, q.length):
                    return Hit(self.label, lrc, matched_artist=cand.get("singer") or "", matched_title=cand.get("song") or "", matched_length=round(cand["duration"] / 1000, 1))
        return None


def build_sources(cfg, ctx):
    """Cheapest first, so online services are only asked about what nothing local knows."""
    out = []
    on = cfg["sources"]
    if on.get("sidecar"):
        out.append(Sidecar())
    if on.get("lrcget") and cfg.get("lrcget_db") and os.path.exists(cfg["lrcget_db"]):
        try:
            out.append(Lrcget(cfg["lrcget_db"]))
        except sqlite3.Error as e:
            ctx.log(f"無法開啟 LRCGET 資料庫，已略過：{e}")
    local = None
    if on.get("lrclib") and cfg.get("lrclib_db") and os.path.exists(cfg["lrclib_db"]):
        try:
            local = LocalLrclib(cfg["lrclib_db"])
            out.append(local)
        except sqlite3.Error as e:
            ctx.log(f"無法開啟 LRCLIB 本機資料庫，改為查詢線上資料：{e}")
    if on.get("lrclib") and local is None:
        out.append(OnlineLrclib(ctx))
    if on.get("netease"):
        out.append(NetEase(ctx))
    if on.get("kugou"):
        out.append(Kugou(ctx))
    return out
