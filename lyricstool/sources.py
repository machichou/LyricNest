"""Where lyrics come from. Each source answers one track strictly: artist, title and length must all agree."""
import base64
import json
import os
import pathlib
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import yaml
from opencc import OpenCC
from rapidfuzz import fuzz

from queue import Queue

from .i18n import stored_text, tr

from .clean import T2S, fold, has_offset, is_placeholder, last_stamp, sung_lines

S2T = OpenCC("s2t")
UA = "LyricNest/1.0 (personal library, slow)"
MAX_DIFF = 3
SUSPEND_SECONDS = 600
QUICK_RETRY = 5  # seconds; a song can afford this much while the other sources are still answering it
NOISE = re.compile(r"\s*[\(\[][^\)\]]*(remaster|explicit|feat|bonus|deluxe|mono|stereo|version|live|cd only|acoustic|demo|cover)[^\)\]]*[\)\]]"
                   r"|\s*[\(\[]\d{8}[\)\]]|\s+-\s+.*(remaster|explicit|mono|stereo|version|live|edit).*$", re.I)
# "01 心不在焉", "01. 後話", "16 大禹": a track number the tagger copied from the file name. Two digits before Latin letters are
# left alone, as they are usually the title itself ("21 Guns", "99 Problems").
TRACK_NO = re.compile(r"^(?:0\d|\d{1,2}\.|\d{2}(?=\s+[^\x00-\x7f]))\s+")
# A language version is sung in other words, so it must not pass for the original, which loose title matching would allow.
# shortcut: only these languages are recognised; add one when a version in another language turns up.
LANGUAGES = re.compile(r"japanese|korean|chinese|mandarin|cantonese|english|spanish|tagalog|thai|french|portuguese|日本语|日文|中文|国语|粤语|韩文|英文|台语")
FEAT_IN_BRACKETS = re.compile(r"\s*[(\[（]\s*((?:feat\.?|ft\.?|featuring|with)\s+[^)\]）]+)[)\]）]", re.I)
SPLIT_ARTISTS =re.compile(r"\s*(?:,|&|、|/|;|；| x | X | feat\.? | ft\.? | featuring | with )\s*", re.I)


class Busy(Exception):
    """The source is throttling or unreachable; the track should be asked again later."""


class Paused(Busy):
    """The source is resting after refusing several songs in a row; the song is asked again at the end of the run."""


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


def artist_parts(artist):
    """Each artist in the tag, the lead first; "A (feat. B)" splits like "A feat. B"."""
    flat = FEAT_IN_BRACKETS.sub(r" \1", artist or "")
    return [p.strip() for p in SPLIT_ARTISTS.split(flat) if p.strip()]


def names_for(artist, aliases):
    """Every spelling worth searching for, as most sources search only the first two: each artist's aliases, then the artist
    as tagged, lead first, so a guest's alias never pushes the lead out; an alias for the whole act goes before all of them."""
    keys = {k.lower(): names for k, names in aliases.items()}
    parts = artist_parts(artist)
    out = [n for k, names in keys.items() if k in (artist or "").lower() and not any(k in p.lower() for p in parts) for n in names]
    for p in parts:
        out += [n for k, names in keys.items() if k in p.lower() for n in names] + [p]
    out += [T2S.convert(p) for p in parts] + [artist or "", T2S.convert(artist or "")]  # after every artist: a Simplified twin must not take the guest's turn
    return list(dict.fromkeys(x for x in out if x))


def titles_for(title):
    """The title, without a track number or live/version noise, and each side of 'A / B'."""
    base = NOISE.sub("", TRACK_NO.sub("", title)).strip() or title
    return list(dict.fromkeys(x.strip() for x in [title, base, *re.split(r"\s+/\s+", base)] if x.strip()))


def make_query(artist, title, album, length, path, aliases):
    return Query(artist, title, album, length, path, names_for(artist, aliases), titles_for(title))


def matches(q, artist_text, song_text, duration):
    if not duration or abs(duration - q.length) > MAX_DIFF:
        return False
    a = fold(artist_text)
    if not any(fold(n) and (fold(n) in a or (len(fold(n)) >= 3 and fuzz.partial_ratio(fold(n), a) >= 90)) for n in q.names):
        return False
    if set(LANGUAGES.findall(fold(song_text))) != set(LANGUAGES.findall(fold(q.title))):
        return False
    s = fold(NOISE.sub("", song_text) or song_text)
    return any(fuzz.token_set_ratio(fold(t), s) >= 85 for t in q.titles)


def usable(lrc, length):
    """Enough timed words, none past the end of the track, not a 'pure music' placeholder, no timing offset we would have to guess at."""
    if has_offset(lrc) or is_placeholder(lrc):
        return False
    return len(sung_lines(lrc)) >= 3 and last_stamp(lrc) <= length + 2


_lane = threading.local()


def _settled():
    """True once another source has already found the song this lane is working on."""
    song = getattr(_lane, "settled", None)
    return song is not None and song.is_set()


class Lane:
    """A source's own thread: it never has two requests out at once, and the job moves on without waiting for it."""

    def __init__(self, source):
        self.source, self.asks = source, Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def ask(self, q, settled, answers):
        self.asks.put((q, settled, answers))

    def close(self):
        self.asks.put(None)

    def _run(self):
        while (ask := self.asks.get()) is not None:
            q, _lane.settled, answers = ask
            try:
                answers.put((self.source, None if _settled() else self.source.find(q)))
            except Exception as e:  # handed to the job, which logs it and counts the source as failed
                answers.put((self.source, e))


class Limiter:
    """Spaces requests, waits out throttling with visible messages, and stops asking a source that keeps refusing."""

    def __init__(self, label, delay, backoff, ctx):
        self.label, self.delay, self.backoff, self.ctx = label, delay, backoff, ctx
        self.fails = 0
        self.suspended_until = 0.0

    def call(self, fn, optional=False):
        """A refusal is retried on the spot only when the wait is short. A longer wait is taken as a rest between songs,
        so no song sits waiting for a source that cannot answer it in time. `optional`: one try only, and a refusal answers None
        instead of counting against the source."""
        if time.monotonic() < self.suspended_until:
            raise Paused(self.label)
        for attempt, wait in enumerate(([] if optional else self.backoff) + [None]):
            self.ctx.sleep(self.delay)
            if self.ctx.cancelled() or _settled():
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
            if wait > QUICK_RETRY:
                self.suspended_until = time.monotonic() + wait
                self.ctx.status(tr("{source} is busy; resting {wait} s while the other sources carry on", source=stored_text(self.label), wait=wait))
                break
            self.ctx.wait(wait, tr("{source} is busy; trying again in {{}} s (attempt {n})", source=stored_text(self.label), n=attempt + 1), stop=_settled)
            if self.ctx.cancelled() or _settled():
                raise Busy(self.label)
            self.ctx.status(tr("{source} trying again (attempt {n})…", source=stored_text(self.label), n=attempt + 1))  # the request can take up to its 30-second timeout; the countdown must not sit at 1 meanwhile
        self.fails += 1
        if self.fails >= 3:
            self.suspended_until = time.monotonic() + SUSPEND_SECONDS
            self.fails = 0
            self.ctx.log(tr("{source} keeps refusing, so it is paused for 10 minutes while the other sources carry on; songs from this time are asked again automatically", source=stored_text(self.label)))
            self.ctx.emit("limited", self.label)
        raise Busy(self.label)


def _get_json(url, timeout=30, **headers):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
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
    c = sqlite3.connect(pathlib.Path(path).as_uri() + "?mode=ro&immutable=1", uri=True, check_same_thread=False)  # opened by the job, read by the source's lane, one call at a time
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
        # One quick retry and a short timeout: its refusals usually pass within seconds, and since every source is asked at once,
        # three seconds here cost little, while without the retry most songs ended up marked busy.
        self.lim = Limiter("LRCLIB", 0.7, [3], ctx)

    def _get(self, endpoint, params, optional=False):
        url = f"https://lrclib.net/api/{endpoint}?" + urllib.parse.urlencode(params)
        return self.lim.call(lambda: _get_json(url, timeout=10, **{"User-Agent": UA}), optional)

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
            # at times the search answers code 200 with `result` as a string, then normally again later: that is no answer, not "no such song"
            if not d or d.get("code") != 200 or not isinstance(d.get("result", {}), dict):
                raise Retry()
            return d
        return self.lim.call(fetch)

    def find(self, q):
        instrumental, seen = None, set()
        for name in q.names[:2]:
            # search/get/web refused this endpoint alone for over half an hour ("操作频繁", code 405) while cloudsearch kept answering
            d = self._get("https://music.163.com/api/cloudsearch/pc?" + urllib.parse.urlencode({"s": f"{name} {q.search_title}", "type": 1, "limit": 20}))
            for s in (d.get("result") or {}).get("songs") or []:
                who = "/".join(a["name"] for a in s.get("ar") or [])
                dur = (s.get("dt") or 0) / 1000
                if s["id"] in seen or not matches(q, who + " " + " ".join(s.get("alia") or []), s["name"], dur):
                    continue  # both names often find the same song, and every request counts toward NetEase's limit
                seen.add(s["id"])
                ly = self._get(f"https://music.163.com/api/song/lyric?id={s['id']}&lv=1&tv=-1")
                lrc = (ly.get("lrc") or {}).get("lyric") or ""
                if usable(lrc, q.length):
                    return Hit(self.label, lrc, matched_artist=who, matched_title=s["name"], matched_length=round(dur, 1))
                if ly.get("nolyric") or is_placeholder(lrc):
                    instrumental = instrumental or Hit(self.label, instrumental=True, matched_artist=who, matched_title=s["name"], matched_length=round(dur, 1))
        return instrumental


class QQMusic:
    label = "QQ 音樂"
    REFERER = "https://y.qq.com/"

    def __init__(self, ctx):
        self.lim = Limiter("QQ 音樂", 2.0, [15], ctx)

    def _get(self, url):
        return self.lim.call(lambda: _get_json(url, Referer=self.REFERER)) or {}

    def find(self, q):
        for name in q.names[:2]:
            found = self._get("https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg?" + urllib.parse.urlencode({"key": f"{name} {q.search_title}", "format": "json"}))
            for s in ((found.get("data") or {}).get("song") or {}).get("itemlist") or []:
                if not matches(q, s.get("singer") or "", s.get("name") or "", q.length):  # artist and title first: the length costs a request per song
                    continue
                song = (self._get(f"https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?songmid={s['mid']}&format=json").get("data") or [{}])[0]  # the search gives no length
                who = "/".join(a.get("name", "") for a in song.get("singer") or [])
                if not matches(q, who, song.get("name") or "", song.get("interval") or 0):
                    continue
                d = self._get(f"https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid={s['mid']}&format=json&g_tk=5381")
                try:
                    lrc = base64.b64decode(d.get("lyric") or "").decode("utf-8", "replace")
                except ValueError:
                    continue
                if usable(lrc, q.length):
                    return Hit(self.label, lrc, matched_artist=who, matched_title=song["name"], matched_length=song["interval"])
        return None


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
    """Cheapest first, so online services are only asked about what nothing local knows; among those, NetEase first, as it found most."""
    out = []
    on = cfg["sources"]
    if on.get("sidecar"):
        out.append(Sidecar())
    if on.get("lrcget") and cfg.get("lrcget_db") and os.path.exists(cfg["lrcget_db"]):
        try:
            out.append(Lrcget(cfg["lrcget_db"]))
        except sqlite3.Error as e:
            ctx.log(tr("Cannot open the LRCGET database, skipped: {e}", e=e))
    local = None
    if on.get("lrclib") and cfg.get("lrclib_db") and os.path.exists(cfg["lrclib_db"]):
        try:
            local = LocalLrclib(cfg["lrclib_db"])
            out.append(local)
        except sqlite3.Error as e:
            ctx.log(tr("Cannot open the local LRCLIB database, looking up online instead: {e}", e=e))
    if on.get("netease"):
        out.append(NetEase(ctx))
    if on.get("lrclib") and local is None:
        out.append(OnlineLrclib(ctx))
    if on.get("qq"):
        out.append(QQMusic(ctx))
    if on.get("kugou"):
        out.append(Kugou(ctx))
    return out
