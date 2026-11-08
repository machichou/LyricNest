"""Read and write embedded lyrics. Writes never touch audio bytes: the new file is built and checked first, then swapped in atomically."""
import csv
import hashlib
import io
import os
import pathlib
import time

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, USLT
from mutagen.mp3 import MP3

from .i18n import tr

WRITABLE = (".flac", ".mp3")
AUDIO = WRITABLE + (".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus", ".wma", ".alac", ".ape", ".dsf")
TMP_SUFFIX = ".lyricstmp"
INDEX_FIELDS = ["header_file", "path", "orig_size", "audio_sha1", "result"]


class WriteError(Exception):
    pass


def split_flac(data):
    if data[:4] != b"fLaC":
        raise WriteError(tr("不是標準的 FLAC 檔案"))
    i = 4
    while True:
        if i + 4 > len(data):
            raise WriteError(tr("FLAC 標頭損壞"))
        h = data[i]
        i += 4 + int.from_bytes(data[i + 1:i + 4], "big")
        if h & 0x80:
            return data[:i], data[i:]


def split_mp3(data):
    if data[:3] != b"ID3" or len(data) < 10:
        return b"", data
    size = 0
    for b in data[6:10]:
        size = (size << 7) | (b & 0x7F)
    end = 10 + size + (10 if data[5] & 0x10 else 0)
    return data[:end], data[end:]


def split(data, ext):
    return split_flac(data) if ext == ".flac" else split_mp3(data)


def _first(tags, key):
    v = tags.get(key)
    return (v[0] if v else "") or ""


def _id3_text(tags, key):
    frame = tags.get(key) if tags else None
    return str(frame.text[0]) if frame and frame.text else ""


def _quality(info):
    """The way players show it, like 24/48.0kHz. Lossy audio has no bit depth (AAC only reports one), so its bitrate takes that place."""
    depth = 0 if str(getattr(info, "codec", "")).startswith("mp4a") else getattr(info, "bits_per_sample", 0)
    kbps = (getattr(info, "bitrate", 0) or 0) // 1000
    rate = getattr(info, "sample_rate", 0)
    return "/".join(x for x in (str(depth) if depth else f"{kbps}kbps" if kbps else "", f"{rate / 1000:.1f}kHz" if rate else "") if x)


def stream_quality(path):
    """For the formats that are only counted, not written."""
    try:
        f = mutagen.File(path)
        return "" if f is None else _quality(f.info)  # `if f` would not do: a file without tags counts as empty
    except Exception:  # a damaged file must not stop the scan, and this column is not worth more than a blank
        return ""


def read_info(path):
    """Tags, length, audio quality and embedded lyrics. Raises if the file cannot be parsed."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".flac":
        f = FLAC(path)
        t = f.tags or {}
        return {"artist": _first(t, "ARTIST"), "album_artist": _first(t, "ALBUMARTIST"), "title": _first(t, "TITLE"), "album": _first(t, "ALBUM"),
                "length": f.info.length, "quality": _quality(f.info), "lyrics": _first(t, "LYRICS")}
    if ext == ".mp3":
        f = MP3(path)
        t = f.tags
        uslt = [v.text for k, v in (t.items() if t else []) if k.startswith("USLT")]
        return {"artist": _id3_text(t, "TPE1"), "album_artist": _id3_text(t, "TPE2"), "title": _id3_text(t, "TIT2"), "album": _id3_text(t, "TALB"),
                "length": f.info.length, "quality": _quality(f.info), "lyrics": uslt[0] if uslt else ""}
    raise WriteError(tr("不支援 {ext} 格式", ext=ext))


def _render_flac(data, lrc):
    buf = io.BytesIO(data)
    f = FLAC(buf)
    if f.tags is None:
        f.add_tags()
    f["LYRICS"] = lrc
    buf.seek(0)
    f.save(buf)
    return buf.getvalue()


def _render_mp3(data, lrc):
    """Only the ID3v2 block is rebuilt, in its own version; everything after it is copied byte for byte."""
    head, body = split_mp3(data)
    try:
        tags = ID3(io.BytesIO(head), translate=False) if head else ID3()
        version = tags.version[1] if head else 4
    except ID3NoHeaderError:
        tags, version = ID3(), 4
    if version == 2:
        tags, version = ID3(io.BytesIO(head)), 4
    tags.delall("USLT")
    tags.add(USLT(encoding=3 if version == 4 else 1, lang="und", desc="", text=lrc))
    out = io.BytesIO()
    tags.save(out, v1=0, v2_version=version, padding=lambda info: 1024)
    return out.getvalue() + body


def render(data, ext, lrc):
    return _render_flac(data, lrc) if ext == ".flac" else _render_mp3(data, lrc)


def embedded(data, ext):
    if ext == ".flac":
        return _first(FLAC(io.BytesIO(data)).tags or {}, "LYRICS")
    head = split_mp3(data)[0]
    tags = ID3(io.BytesIO(head)) if head else {}
    uslt = [v.text for k, v in tags.items() if k.startswith("USLT")]
    return uslt[0] if uslt else ""


def _patiently(action):
    """A player or virus scanner can hold a file for a moment; only a lock that lasts counts as failure."""
    for attempt in range(4):
        try:
            return action()
        except PermissionError:
            if attempt == 3:
                raise WriteError(tr("檔案被其他程式占用（可能正在播放或上傳）"))
            time.sleep(0.5 * (attempt + 1))


def _read(path):
    return _patiently(pathlib.Path(path).read_bytes)


def _replace(path, data):
    """Temp file next to the original, flushed to disk, then swapped in; a crash leaves either the old file or the new one."""
    tmp = path + TMP_SUFFIX
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _patiently(lambda: os.replace(tmp, path))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


class Backup:
    """One batch: the original tag block of every file plus an index, in the layout older batches already use."""

    def __init__(self, folder):
        self.folder = folder
        self.headers = os.path.join(folder, "headers")
        os.makedirs(self.headers, exist_ok=True)
        self.index = os.path.join(folder, "index.csv")
        self._new = not os.path.exists(self.index)

    def save(self, path, head, size, sha):
        name = hashlib.sha1(path.encode("utf-8")).hexdigest() + ".hdr"
        with open(os.path.join(self.headers, name), "wb") as fh:
            fh.write(head)
            fh.flush()
            os.fsync(fh.fileno())
        self.mark(name, path, size, sha, "pending")
        return name

    def mark(self, name, path, size, sha, result):
        with open(self.index, "a", encoding="utf-8-sig" if self._new else "utf-8", newline="") as fh:
            w = csv.writer(fh)
            if self._new:
                w.writerow(INDEX_FIELDS)
                self._new = False
            w.writerow([name, path, size, sha, result])
            fh.flush()
            os.fsync(fh.fileno())


def write_lyrics(path, lrc, backup):
    """Back up (unless `backup` is None), build, check, swap, check again. Raises WriteError and leaves the original in place on any failure."""
    if not lrc.strip():
        raise WriteError(tr("歌詞內容為空"))
    ext = os.path.splitext(path)[1].lower()
    if ext not in WRITABLE:
        raise WriteError(tr("不支援 {ext} 格式", ext=ext))
    orig = _read(path)
    head, body = split(orig, ext)
    sha = hashlib.sha1(body).hexdigest()
    new = render(orig, ext, lrc)
    if hashlib.sha1(split(new, ext)[1]).hexdigest() != sha or embedded(new, ext) != lrc:
        raise WriteError(tr("檢查未通過，未寫入檔案"))
    name = backup.save(path, head, len(orig), sha) if backup else None
    try:
        _replace(path, new)
        back = _read(path)
        if hashlib.sha1(split(back, ext)[1]).hexdigest() != sha or embedded(back, ext) != lrc:
            _replace(path, orig)
            raise WriteError(tr("寫入後驗證失敗，已還原"))
    except (WriteError, OSError) as e:
        if backup:
            backup.mark(name, path, len(orig), sha, f"FAIL {e}")
        raise WriteError(str(e)) from e
    if backup:
        backup.mark(name, path, len(orig), sha, "ok")


def read_index(folder):
    """Latest row per path; older batches have the same columns."""
    rows = {}
    with open(os.path.join(folder, "index.csv"), encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if r.get("path") and r.get("header_file"):
                rows[r["path"]] = r
    return rows


def restore_file(folder, row):
    """Put a saved tag block back, only if the audio is still what was backed up. Returns None or the reason it was skipped."""
    path, ext = row["path"], os.path.splitext(row["path"])[1].lower()
    if not os.path.exists(path):
        return tr("檔案不存在")
    hdr = os.path.join(folder, "headers", row["header_file"])
    if not os.path.exists(hdr):
        return tr("備份檔不存在")
    body = split(_read(path), ext)[1]
    if hashlib.sha1(body).hexdigest() != row["audio_sha1"]:
        return tr("音訊與備份時不同")
    with open(hdr, "rb") as fh:
        head = fh.read()
    _replace(path, head + body)
    return None
