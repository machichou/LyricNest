"""Text rules: turn raw LRC from any source into what gets embedded, and spot credit lines in lyrics already embedded."""
import re
import unicodedata
from dataclasses import dataclass

from opencc import OpenCC

S2TW = OpenCC("s2tw")
T2S = OpenCC("t2s")
KANA = re.compile(r"[぀-ヿ]")
KANGXI = re.compile("[⼀-⿕]")
STAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
STAMPED = re.compile(r"^((?:\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]\s*)+)(.*)$")
OFFSET = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.I)
SYNCED_LINE = re.compile(r"^\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", re.M)
MIN_LINES = 3

# Sources put credits on timed lines; left in, they play as lyrics and make instrumentals look sung.
# A value that opens with a quote mark is someone speaking in the song (她說：「…」), not a credit.
NOT_QUOTE = r"(?![\s]*[「『\"“”'])"
CREDIT_ZH = re.compile(
    r"^(?:词|詞|曲|OP|SP|OP/SP|feat|歌手|专辑|專輯)\s*[:：]" + NOT_QUOTE +
    r"|^[^:：「」]{0,30}(?:作词|作詞|作曲|词曲|詞曲|编曲|編曲|制作|製作|混音|母带|母帶|吉他|贝斯|貝斯|鼓手|鼓组|鼓組|键盘|鍵盤|钢琴|鋼琴|"
    r"弦乐|弦樂|絃樂|提琴|长笛|長笛|琵琶|小号|小號|萨克斯|薩克斯|合成器|和声|和聲|人声|人聲|主唱|演唱|配唱|录音|錄音|监制|監製|出品|发行|發行|"
    r"厂牌|廠牌|版权|版權|策划|策劃|企划|企劃|统筹|統籌|封面|编程|編程|数字编辑|數位編輯|音频编辑|音頻編輯|唸白|唱片公司|手风琴|手風琴|"
    r"经纪|經紀|宣传|宣傳|推广|推廣|文案|铃鼓|鈴鼓|打击乐|打擊樂|长号|長號|童声|童聲)"
    r"[^:：「」]{0,40}[:：]" + NOT_QUOTE, re.I)
CREDIT_DRUM = re.compile(r"^鼓\s*(?:drums?)?\s*[:：]\s*\S", re.I)
CREDIT_EN = re.compile(
    r"^[^:：]{0,90}\b(?:lyrics?|lyricist|compose[rd]?|produce[rd]|co-producer|arranged|arranger|written by|bass|guitars?|drums?|keyboards?|vocals?|"
    r"piano|flute|trumpet|trombone|saxophone|horns?|flugelhorn|violin|viola|cello|strings|percussion|maracas?|chorus|concertmaster|engineer(?:ing)?|"
    r"tracking|recording|recorded|mixing|mixed|mastering|mastered|mix assistant|audio edited|art direction|design|photography|manufactured|"
    r"liner notes|record company|copyright|promotion|coordinator|programmed|publisher|publishing|vibraphone|tambourine|special thanks)\b[^:：]{0,40}[:：]\s*" + NOT_QUOTE + r"\S", re.I)
# The other way round, "陳綺貞: Acoustic Guitar": only when the instrument is all that follows, since a sung line goes on after it.
CREDIT_PLAYER = re.compile(
    r"^[^:：]{1,40}[:：]\s*(?:(?:acoustic|electric|nylon|steel|upright|double|lead|rhythm|backing|additional|grand)\s+)?"
    r"(?:guitars?|bass|drums|piano|cello|violin|viola|keyboards?|synths?|synthesizers?|percussion|vocals?|trumpet|saxophone|flute|vibraphone|organ|harmonica)\s*$", re.I)
RIGHTS = re.compile(r"^[【©℗️\s]*(?:版[權权]所有|未[經经]許?可?.*使用)")
CUE = re.compile(r"^[（(\[【<]*\s*(?:前奏|間奏|间奏|尾奏|歡呼聲|欢呼声|掌聲|掌声|music|instrumental|interlude|intro|outro|solo|純音樂|纯音乐)\s*[）)\]】>]*$", re.I)
PLACEHOLDER = ("纯音乐", "純音樂", "此歌曲为没有填词", "没歌词", "沒歌詞", "暂无歌词", "暫無歌詞")
# Sources hand an instrumental the sung version's lyrics, or another song's of the same name; a title that says instrumental settles it.
INSTRUMENTAL_TITLE = re.compile(r"[(\[（【-][^)\]）】]*(?:\binstrumentals?\b|\binst\b|\boff[ -]?vocal\b|\bkaraoke\b|演奏版|演奏曲|伴奏|純音樂|纯音乐|カラオケ)", re.I)


def is_credit(text):
    t = text.strip()
    if RIGHTS.match(t):
        return True
    if ":" not in t and "：" not in t:  # every other pattern needs a colon, and this runs on every line of the library
        return False
    return bool(CREDIT_ZH.match(t) or CREDIT_DRUM.match(t) or CREDIT_EN.match(t) or CREDIT_PLAYER.match(t))


def fold(s):
    """Lowercase, no accents, Simplified, punctuation to spaces: for comparing names across sources."""
    s = "".join(ch for ch in unicodedata.normalize("NFKD", T2S.convert(s or "").lower()) if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def squash(s):
    return fold(s).replace(" ", "")


def is_synced(lyrics):
    return len(SYNCED_LINE.findall(lyrics or "")) >= MIN_LINES


def lyric_state(lyrics):
    return "synced" if is_synced(lyrics) else "plain" if (lyrics or "").strip() else "none"


def _parse(lrc):
    """(seconds, stamp, text) per stamp, in file order, one stamp per entry."""
    out = []
    for line in (lrc or "").lstrip("﻿").splitlines():
        m = STAMPED.match(line.strip())
        if m:
            for mm, ss, ff in STAMP.findall(m[1]):
                seconds = int(mm) * 60 + int(ss) + (int(ff) / 10 ** len(ff) if ff else 0)
                out.append((seconds, f"[{int(mm):02d}:{ss}.{(ff + '00')[:2]}]", m[2].rstrip()))
    return out


def sung_lines(lrc):
    return [t for _, _, t in _parse(lrc) if t.strip()]


def same_words(a, b):
    """The same lyrics again, from another source or cleaned by other rules: the timing, script and a few lines differ, the words do not."""
    old, new = {fold(t) for t in sung_lines(a)}, [fold(t) for t in sung_lines(b)]
    return bool(new) and sum(t in old for t in new) >= 0.8 * len(new)


def last_stamp(lrc):
    return max((s for s, _, _ in _parse(lrc)), default=0.0)


def has_offset(lrc):
    m = OFFSET.search(lrc or "")
    return bool(m) and int(m[1]) != 0


def is_placeholder(lrc):
    head = "".join(sung_lines(lrc)[:3])
    return any(p in head for p in PLACEHOLDER)


@dataclass
class Cleaned:
    lrc: str
    removed: list
    sung: int
    converted: bool


def _is_header(seconds, text, index, title, names):
    """A title or artist shown over the intro, not sung; later in the song the same words may be sung."""
    t, key = squash(text), squash(title)
    if not t:
        return False
    if index < 3 and re.search(r"\s-|-\s", text) and ((key and key in t) or any(squash(n) and squash(n) in t for n in names)):
        return True  # the artist alone is enough: Kugou censors titles, as in "*王 - 蛋堡"
    if index < 3 and re.match(r"^by\s.*\s-\s", text, re.I):
        return True
    if index < 3 and seconds < 3:
        if key and (t == key or (t.startswith(key) and re.search(r"[(（\[【]", text))):
            return True
        if any(t == squash(n) for n in names):
            return True
    return False


def clean_lrc(raw, title="", names=(), convert=True, strip=True):
    """Keep timed lines only, one stamp per line, in time order, without broken text and, when `strip`, without credits, cues or headers.
    `sung` never counts those three, kept or not, so credits alone still do not pass for a sung song."""
    text = KANGXI.sub(lambda m: unicodedata.normalize("NFKC", m[0]), raw or "")
    entries = sorted(_parse(text), key=lambda e: e[0])
    kept, removed, sung_seen = [], [], 0
    for seconds, stamp, line in entries:
        body = line.strip()
        not_sung = body and (is_credit(body) or CUE.match(body) or _is_header(seconds, body, sung_seen, title, names))
        if "�" in body or (not_sung and strip):
            removed.append(body)
            continue
        if body and not not_sung:
            sung_seen += 1
        kept.append(stamp + line)
    lrc = "\n".join(kept)
    converted = False
    if convert and not KANA.search(lrc):
        out = S2TW.convert(lrc)
        converted, lrc = out != lrc, out
    return Cleaned(lrc, removed, sung_seen, converted)


def strip_credits(lyrics):
    """Remove only credit lines from lyrics already embedded; every other line stays exactly as it was."""
    keep, gone = [], []
    for line in (lyrics or "").splitlines():
        m = STAMPED.match(line.strip())
        if m and is_credit(m[2]):
            gone.append(m[2].strip())
        else:
            keep.append(line)
    return "\n".join(keep), gone
