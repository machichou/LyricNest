"""The work the UI starts on a background thread: scan, look up, write, restore. No Tk in here, so all of it can be tested alone."""
import csv
import glob
import json
import logging
import os
import re
import string
import threading
import time
from queue import Empty, Queue

from . import audio
from .clean import INSTRUMENTAL_TITLE, clean_lrc, is_synced, last_stamp, lyric_state, squash, strip_credits, sung_lines
from .i18n import ZH, stored_text, tr
from .sources import Busy, Lane, Paused, artist_parts, build_sources, make_query
from .store import text_hash

log = logging.getLogger("lyricstool")
MAX_PASSES = 3
SONG_SECONDS = 45  # a source that has not answered a song by then counts as busy for it, and the song is asked again later
ALBUM_SAMPLE = 3  # an album is given up on only when this many of its songs all come back without lyrics, so one odd track does not settle it
# A song asked again waits this long for its resting source. Rests after a refusal are at most this long; a longer one means the source
# was suspended for refusing again and again, and the round ends rather than wait it out song by song.
RETRY_PATIENCE = 180
INSTRUMENTAL_NOTE = "；純音樂："  # in a busy song's lookup row, after the sources that still owe an answer
TITLE_NOTE = "歌名標示"  # in the lookup row of a song its title calls instrumental, where a source's name goes otherwise
KIND_NAMES = {"new": "New lyrics", "cleanup": "Credit cleanup"}
# Stored as [code] or [code, number] and worded when shown, so a flag reads in the language of the day.
FLAGS = {"length_diff": "Length differs by {n:.1f} s", "few_lines": "Only {n} lines", "ends_early": "Lyrics end before the middle of the song", "masked": "Some words are masked (*)",
         "title_differs": "Title is not an exact match", "replaces_plain": "Replaces plain lyrics",
         "said_instrumental": "A source or an earlier lookup says instrumental"}


class Ctx:
    """How a job talks to whoever started it; `emit` is any callable taking (kind, payload)."""

    def __init__(self, emit=lambda kind, payload: None):
        self.emit = emit
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def cancelled(self):
        return self._cancel.is_set()

    def sleep(self, seconds):
        self._cancel.wait(seconds)

    def wait(self, seconds, template, stop=lambda: False):
        for left in range(int(seconds), 0, -1):
            if stop():
                return
            self.emit("status", template.format(left))
            if self._cancel.wait(1):
                return

    def log(self, text, tag=""):
        """`tag` is a lookup status, for the window to colour the line by."""
        log.info(text)
        self.emit("log", (text, tag))

    def status(self, text):
        self.emit("status", text)

    def progress(self, done, total, text=""):
        self.emit("progress", (done, total, text))


def excluded(path, library, names):
    """Excluded by exact folder name below the library root, so 'tea' does not also hide a folder called 'teardrop'."""
    try:
        rel = os.path.relpath(os.path.dirname(path), library)
    except ValueError:  # another drive, so not in the library at all
        return True
    if rel == os.pardir or rel.startswith(os.pardir + os.sep):
        return True
    wanted = {n.strip().lower() for n in names if n.strip()}
    return any(part.lower() in wanted for part in rel.split(os.sep))


def walk_audio(library, names):
    wanted = {n.strip().lower() for n in names if n.strip()}
    for root, dirs, files in os.walk(library):
        dirs[:] = [d for d in dirs if d.lower() not in wanted]
        for f in files:
            if f.endswith(audio.TMP_SUFFIX):
                try:
                    os.remove(os.path.join(root, f))  # our own leftover from a crash mid-write; the original is intact
                except OSError:
                    pass
            elif os.path.splitext(f)[1].lower() in audio.AUDIO:
                yield os.path.join(root, f)


def run_scan(store, cfg, ctx):
    """Read tags of new or changed files. Read-only on music; unchanged files are not opened again."""
    library = cfg["library"]
    if not os.path.isdir(library):
        raise ValueError(tr("Music folder not found: {folder}", folder=library))
    ctx.status(tr("Listing files…"))
    known = store.known_files()
    paths = list(walk_audio(library, cfg["excluded"]))
    if not paths and known:
        raise ValueError(tr("No music files found in {folder}; the drive may not be connected. Existing data was kept.", folder=library))
    changed = 0
    for n, path in enumerate(paths, 1):
        if ctx.cancelled():
            break
        ext = os.path.splitext(path)[1].lower()
        try:
            st = os.stat(path)
        except OSError:
            continue
        if known.get(path) == (st.st_size, st.st_mtime):
            continue
        changed += 1
        scan_one(store, path, ext, st)
        if changed % 50 == 0:
            store.commit()
            ctx.progress(n, len(paths), tr("Read {n}/{total}", n=n, total=len(paths)))
    if not ctx.cancelled():
        gone = set(known) - set(paths)
        store.drop_tracks(gone)  # review items stay: if a drive dropped out mid-walk, they come back with the files on the next scan
        store.db.execute("delete from items where state='pending' and kind='new' and path in (select path from tracks where state='synced')")
        if cfg["remove_credits"]:
            n_clean = suggest_cleanups(store)
        else:
            n_clean = 0
            store.db.execute("delete from items where state='pending' and kind='cleanup'")
        ctx.log(tr("Scan done. Songs: {total}, updated: {changed}, removed: {gone}, credit cleanups suggested: {clean}", total=len(paths), changed=changed, gone=len(gone), clean=n_clean))
    store.commit()


def scan_one(store, path, ext, st):
    if ext not in audio.WRITABLE:
        store.put_track(path, ext, st.st_size, st.st_mtime, {"quality": audio.stream_quality(path)}, "unsupported")
        return
    try:
        info = audio.read_info(path)
        store.put_track(path, ext, st.st_size, st.st_mtime, info, lyric_state(info["lyrics"]))
    except Exception as e:  # a damaged file must not stop the scan; it is listed as unreadable instead
        store.put_track(path, ext, st.st_size, st.st_mtime, {}, "unreadable", f"{e.__class__.__name__}: {e}")


def suggest_cleanups(store):
    """Credit lines inside lyrics that are already embedded. Too few sung lines left means the lyric is mostly credits: left alone."""
    n = 0
    for t in store.db.execute("select path,lyrics from tracks where state='synced'").fetchall():
        lrc, gone = strip_credits(t["lyrics"])
        if gone and is_synced(lrc) and len(sung_lines(lrc)) >= 3:
            if store.put_item(t["path"], "cleanup", "現有歌詞", lrc, gone, [], len(sung_lines(lrc)), "", 0, text_hash(t["lyrics"])) == "added":
                n += 1
        elif not gone:
            store.drop_pending(t["path"], "cleanup")
    return n


def flags_for(track, hit, cleaned):
    """Reasons a person should look twice; nothing here blocks the track."""
    flags = []
    if hit.matched_length and abs(hit.matched_length - track["length"]) > 1.5:
        flags.append(["length_diff", round(abs(hit.matched_length - track["length"]), 1)])
    if cleaned.sung < 8:
        flags.append(["few_lines", cleaned.sung])
    if track["length"] and last_stamp(cleaned.lrc) < track["length"] / 2:
        flags.append(["ends_early"])
    if any("*" in line for line in sung_lines(cleaned.lrc)):
        flags.append(["masked"])
    if squash(hit.matched_title) != squash(track["title"]):
        flags.append(["title_differs"])
    if track["state"] == "plain":
        flags.append(["replaces_plain"])
    return flags


def flag_text(flag):
    code, *n = flag
    return tr(FLAGS[code], n=n[0] if n else None)


def upgrade_flags(store):
    """Before 1.12 a flag was stored as its Chinese wording, which stayed Chinese in the English window; each becomes its code."""
    wordings = [(code, re.compile("".join(re.escape(text) + (r"([\d.]+)" if field else "") for text, field, _, _ in string.Formatter().parse(ZH[w]))))
                for code, w in FLAGS.items()]

    def code_of(flag):
        for code, wording in wordings:
            m = wording.fullmatch(flag)
            if m:
                return [code, *map(json.loads, m.groups())]  # "17" comes back an int, "2.0" a float, as they were before being worded
        raise ValueError(f"unknown flag in the database: {flag}")

    for r in store.db.execute("select id,flags from items").fetchall():
        flags = json.loads(r["flags"])
        if any(isinstance(f, str) for f in flags):
            store.db.execute("update items set flags=? where id=?", (json.dumps([code_of(f) if isinstance(f, str) else f for f in flags]), r["id"]))
    store.db.commit()


def action_text(item):
    """What writing this row will do, then anything worth a second look."""
    if item["kind"] != "new":
        return tr("Remove credits: {n}", n=len(json.loads(item['removed'])))
    action = tr("Replace plain lyrics" if item["track_state"] == "plain" else "Write new lyrics")
    flags = [flag_text(f) for f in json.loads(item["flags"])]
    return tr("; ").join([action] + [f for f in flags if f != tr(FLAGS["replaces_plain"])])


def lookup_one(sources, track, cfg, store, ctx, failed=None, lanes=None):
    """Ask every source at once; the first usable answer wins and becomes an item to review, and the others skip what is left of the song.
    `failed` maps each source that could not answer to why. `lanes` keep each source to one song at a time across calls."""
    if INSTRUMENTAL_TITLE.search(track["title"] or ""):
        store.save_lookup(track["path"], "instrumental", TITLE_NOTE)
        return "instrumental"
    q = make_query(track["artist"] or track["album_artist"], track["title"], track["album"], track["length"], track["path"], cfg["aliases"])
    failed = {} if failed is None else failed
    own = lanes is None
    lanes = lanes or {s: Lane(s) for s in sources}
    settled, answers = threading.Event(), Queue()
    for s in sources:
        lanes[s].ask(q, settled, answers)
    prev = store.db.execute("select status,source from lookups where path=?", (track["path"],)).fetchone()
    # asked again because a source was busy: an earlier "instrumental" from the others still stands if this one finds nothing either
    waiting, instrumental = list(sources), read_busy_note(prev["source"])[1] if prev and prev["status"] == "busy" else ""
    deadline = time.monotonic() + SONG_SECONDS
    try:
        while waiting:
            if ctx.cancelled():
                return None
            try:
                s, hit = answers.get(timeout=0.2)
            except Empty:
                if time.monotonic() > deadline:
                    failed.update(dict.fromkeys(waiting, "timed out"))
                    break
                continue
            waiting.remove(s)
            if isinstance(hit, Exception):
                if not isinstance(hit, Busy):
                    log.error("source %s failed on %s", s.label, track["path"], exc_info=hit)
                failed[s] = "paused" if isinstance(hit, Paused) else "busy" if isinstance(hit, Busy) else "error"
                continue
            if hit is None:
                continue
            if hit.instrumental:  # one source's word is not final: sources disagree often enough that the others still get asked
                instrumental = instrumental or hit.source
                continue
            cleaned = clean_lrc(hit.lrc, track["title"], q.names, cfg["to_traditional"], cfg["remove_credits"])
            if cleaned.sung < 3:
                continue
            source = hit.source + ("（已轉繁體）" if cleaned.converted else "")
            flags = flags_for(track, hit, cleaned)
            if instrumental or (prev and prev["status"] == "instrumental"):
                flags.append(["said_instrumental"])  # then a found song is often another song of the same name, or the sung version
            result = store.put_item(track["path"], "new", source, cleaned.lrc, cleaned.removed, flags, cleaned.sung,
                                    f"{hit.matched_artist} - {hit.matched_title}", hit.matched_length, text_hash(track["lyrics"]))
            status = "dismissed" if result == "dismissed" else "found"
            store.save_lookup(track["path"], status, hit.source)
            return status
    finally:
        settled.set()
        if own:
            for lane in lanes.values():
                lane.close()
    status = "busy" if failed else "instrumental" if instrumental else "notfound"
    note = "、".join(s.label for s in failed) + (INSTRUMENTAL_NOTE + instrumental if instrumental else "") if failed else instrumental
    store.save_lookup(track["path"], status, note)
    return status


def read_busy_note(note):
    """(labels of the sources that still owe an answer, the source that said instrumental) from a busy song's lookup row."""
    owed, _, instrumental = (note or "").partition(INSTRUMENTAL_NOTE)
    return (owed.split("、") if owed else []), instrumental


def resting(sources):
    """Seconds until every one of these sources is back in use."""
    now = time.monotonic()
    return max([0] + [s.lim.suspended_until - now for s in sources if hasattr(s, "lim")])


def retry_wait(queue):
    """Seconds until every source these tracks are waiting on is back in use; at least a minute, so a brief hiccup gets a moment to pass."""
    return max([60] + [resting(failed) for _, failed in queue])


def run_lookup(store, cfg, ctx, statuses=("never", "busy"), text=""):
    """Throttled tracks are asked again at the end, up to MAX_PASSES rounds, without anyone rerunning anything."""
    every = store.lookup_targets(statuses, text)
    tagged = lambda t: t["title"] and (t["artist"] or t["album_artist"])
    targets = [t for t in every if tagged(t)]
    untagged = [t["path"] for t in every if not tagged(t)]
    if untagged:
        ctx.log(tr("Skipped for lack of an artist or title tag ({n}):", n=len(untagged)))
        for path in untagged:
            ctx.log(f"  {path}")
    if not targets:
        ctx.log(tr("No songs to look up"))
        return {}
    sources = build_sources(cfg, ctx)
    if not sources:
        raise ValueError(tr("No lyrics sources are on. Turn some on in Settings."))
    ctx.log(tr("Sources asked together: {sources}", sources=tr(", ").join(stored_text(s.label) for s in sources)))
    counts = {}
    total = len(targets)

    def owed_by(t):  # left busy last time: only the sources that still owed an answer, unless none of them is switched on any more
        owed = read_busy_note(t["source"])[0] if t["status"] == "busy" else []
        return [s for s in sources if s.label in owed] or sources

    queue = [(t, owed_by(t)) for t in targets]
    lanes = {s: Lane(s) for s in sources}
    albums, given_up = {}, set()  # by folder: [songs answered, songs nothing had lyrics for]
    try:
        for round_no in range(1, MAX_PASSES + 1):
            retry = []
            for n, (t, asking) in enumerate(queue, 1):
                if ctx.cancelled():
                    left = len(retry) + (len(queue) - n + 1 if round_no > 1 else 0)  # after the first round, songs not reached are still busy
                    if left:
                        counts["busy"] = left
                    ctx.log(tr("Stopped. Results are saved, and the next lookup carries on from here: {summary}", summary=summary(counts)))
                    return counts
                if not os.path.exists(t["path"]):
                    continue
                album = os.path.dirname(t["path"])
                if album in given_up:
                    store.save_lookup(t["path"], "notfound", "整張專輯略過")
                    counts["skipped"] = counts.get("skipped", 0) + 1
                    continue
                if round_no > 1:
                    rest = resting(asking)
                    if rest > RETRY_PATIENCE:
                        ctx.log(tr("{sources} still resting; the songs left ({n}) wait for the next round" if round_no < MAX_PASSES else "{sources} still resting; the songs left ({n}) wait for the next lookup",
                                   sources=tr(", ").join(stored_text(s.label) for s in asking), n=len(queue) - n + 1))
                        retry += queue[n - 1:]
                        break
                    if rest:  # asked while resting, the song would be marked busy at once without the source ever being asked
                        ctx.wait(rest + 1, tr("Waiting for sources to recover; asking again in {} s"))
                ctx.progress(total - len(queue) + n - 1, total, f"{t['artist']} - {t['title']}")
                failed = {}
                status = lookup_one(asking, t, cfg, store, ctx, failed, lanes)
                if status is None:  # stopped mid-song
                    if round_no > 1:
                        retry.append((t, asking))
                    continue
                why = tr(" ({reasons})", reasons=tr(", ").join(f"{stored_text(s.label)} {tr(reason)}" for s, reason in failed.items())) if status == "busy" else ""
                ctx.log(tr("{status}{why}: {artist} - {title}", status=tr(LABELS[status]), why=why, artist=t['artist'] or t['album_artist'], title=t['title']), status)
                if status == "busy":
                    retry.append((t, failed))  # the other sources already answered, so only these get asked again
                    continue
                counts[status] = counts.get(status, 0) + 1
                if cfg["skip_albums"]:
                    tally = albums.setdefault(album, [0, 0])
                    tally[0] += 1
                    tally[1] += status in ("notfound", "instrumental")
                    # decided once: albums that are mostly sung but miss a few songs are common, and a share of misses gave up on them too
                    if tally == [ALBUM_SAMPLE, ALBUM_SAMPLE]:
                        given_up.add(album)
                        ctx.log(tr("Skipping the album (first {n} songs not found): {album}", n=ALBUM_SAMPLE, album=t['album'] or os.path.basename(album)))
            ctx.progress(total - len(retry), total, "")
            queue = retry
            if not queue:
                break
            if round_no < MAX_PASSES:
                wait = retry_wait(queue)
                ctx.log(tr("Songs to ask again because a source was busy: {n}. Trying again in about {minutes:.0f} min (round {round})", n=len(queue), minutes=max(wait / 60, 1), round=round_no + 1))
                ctx.wait(wait, tr("Waiting for sources to recover; asking again in {} s"))
    finally:
        for lane in lanes.values():
            lane.close()
    if queue:
        counts["busy"] = len(queue)
    ctx.log(tr("Lookup done: {summary}", summary=summary(counts)))
    return counts


LABELS = {"found": "Found", "notfound": "No lyrics found", "instrumental": "Instrumental", "busy": "To ask again", "skipped": "Album skipped", "dismissed": "Same as lyrics removed before"}


def summary(counts):
    return tr(", ").join(tr("{label}: {n}", label=tr(LABELS[k]), n=v) for k, v in counts.items()) or tr("no results")


def run_write(store, cfg, ctx):
    """Write everything ticked. Each file is rechecked first: the library keeps changing while lists sit waiting."""
    items = store.selected_items()
    if not items:
        ctx.log(tr("No songs are ticked for writing"))
        return {"ok": 0, "failed": [], "stale": []}
    folder, backup = "", None
    if cfg["backup_enabled"]:
        folder = os.path.join(os.path.abspath(cfg["backup_root"]), time.strftime("app_%Y-%m-%d_%H%M%S"))
        backup = audio.Backup(folder)
        ctx.log(tr("Backup folder: {folder}", folder=folder))
    else:
        ctx.log(tr("Backups are off; this write cannot be undone"))
    result = {"ok": 0, "failed": [], "stale": [], "folder": folder}
    for n, it in enumerate(items, 1):
        if ctx.cancelled():
            ctx.log(tr("Stopped. Every song written passed verification; the rest stay on the list"))
            break
        path = it["path"]
        ctx.progress(n - 1, len(items), os.path.basename(path))
        reason = stale_reason(store, cfg, it)
        if reason:
            store.set_item_state(it["id"], "stale")
            if reason == tr("File was replaced"):
                store.save_lookup(path, "never")
            result["stale"].append((path, reason))
            continue
        try:
            audio.write_lyrics(path, it["lrc"], backup)
        except Exception as e:  # one bad file must not stop the batch; it stays on the list to try again, original untouched
            log.exception("write failed: %s", path)
            result["failed"].append((path, str(e)))
            continue
        store.set_item_state(it["id"], "written")
        result["ok"] += 1
        st = os.stat(path)
        scan_one(store, path, os.path.splitext(path)[1].lower(), st)
        store.commit()
    ctx.progress(len(items), len(items), "")
    ctx.log(tr("Write done: {ok} written, {failed} failed, {stale} skipped because the file changed", ok=result['ok'], failed=len(result['failed']), stale=len(result['stale'])))
    for path, why in result["failed"] + result["stale"]:
        ctx.log(tr("  {why}: {path}", why=why, path=path))
    return result


def stale_reason(store, cfg, it):
    path = it["path"]
    if not os.path.exists(path):
        return tr("File not found")
    if excluded(path, cfg["library"], cfg["excluded"]):
        return tr("In a skipped folder")
    try:
        info = audio.read_info(path)
    except Exception as e:
        return tr("Cannot read the file ({error})", error=e.__class__.__name__)
    t = store.track(path)
    if it["kind"] == "new":
        if is_synced(info["lyrics"]):
            return tr("Already has synced lyrics")
        if t and abs(info["length"] - t["length"]) > 1:
            return tr("File was replaced")
    if text_hash(info["lyrics"]) != it["base_hash"]:
        return tr("Lyrics were changed")
    return None


def list_backups(root):
    """Every batch under the root with a header index, including ones made before this app existed."""
    out = []
    for index in glob.glob(os.path.join(glob.escape(root), "*", "index.csv")):
        folder = os.path.dirname(index)
        try:
            rows = audio.read_index(folder)
        except (OSError, ValueError, KeyError, csv.Error):
            continue
        if not rows or not os.path.isdir(os.path.join(folder, "headers")):
            continue
        ok = [r for r in rows.values() if r.get("result") in ("ok", "pending")]
        out.append({"folder": folder, "name": os.path.basename(folder), "count": len(ok), "at": os.path.getmtime(index)})
    return sorted(out, key=lambda b: -b["at"])


def run_restore(store, cfg, ctx, folder, paths=None):
    """Restore a whole batch, or just `paths`. Files whose audio changed since the backup are skipped, never overwritten."""
    rows = [r for p, r in audio.read_index(folder).items() if r.get("result") in ("ok", "pending") and (paths is None or p in paths)]
    done, skipped = 0, []
    for n, r in enumerate(rows, 1):
        if ctx.cancelled():
            break
        ctx.progress(n - 1, len(rows), os.path.basename(r["path"]))
        try:
            why = audio.restore_file(folder, r)
        except Exception as e:
            log.exception("restore failed: %s", r["path"])
            why = str(e)
        if why:
            skipped.append((r["path"], why))
            continue
        done += 1
        path = os.path.normpath(r["path"])  # older batches mixed / and \ in their paths
        scan_one(store, path, os.path.splitext(path)[1].lower(), os.stat(path))
        store.db.execute("delete from lookups where path=?", (path,))  # asked about again, since these lyrics were just rejected
        store.commit()
    ctx.progress(len(rows), len(rows), "")
    ctx.log(tr("Restore done: {done} restored, {skipped} skipped", done=done, skipped=len(skipped)))
    for p, why in skipped:
        ctx.log(tr("  {why}: {path}", why=why, path=p))
    return {"done": done, "skipped": skipped}


def export_review(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(map(tr, ["Ticked", "Type", "Artist", "Title", "Album", "Source", "Matched", "Lines", "Flags", "Removed lines", "Path"]))
        for r in rows:
            w.writerow([tr("Yes" if r["selected"] else "No"), tr(KIND_NAMES[r["kind"]]), r["artist"], r["title"], r["album"], stored_text(r["source"]), r["matched"], r["lines"],
                        tr("; ").join(map(flag_text, json.loads(r["flags"]))), " | ".join(json.loads(r["removed"])), r["path"]])


def export_missing(rows, path):
    names = {"never": "Not looked up yet", "notfound": "No lyrics found", "instrumental": "Instrumental or no lyrics", "busy": "Source busy, to ask again", "found": "Waiting for review", "dismissed": "Removed from the list"}
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(map(tr, ["Artist", "Title", "Album", "Current lyrics", "Format", "Lookup result", "Path"]))
        for r in rows:
            state = {"plain": "Plain text", "none": "None", "unsupported": "Unsupported format", "unreadable": "Unreadable"}[r["state"]]
            w.writerow([r["artist"], r["title"], r["album"], tr(state), r["ext"], tr(names[r["status"]]), r["path"]])


# Written for how aliases are used: only the first two names are searched (LRCLIB takes the tag and the first), every name is compared
# loosely against the artist a source returns, and Simplified/Traditional, case and accents are already handled. So the other name goes
# first, the tag second, and guesses are worse than nothing.
ALIAS_PROMPT = """Please put together other names for the artists below, for a lyrics lookup tool. The tool searches LRCLIB, NetEase Cloud Music, QQ Music, Kugou and similar platforms with these names, but searches with only the first two names of each artist.

Output format: one artist per line, written as "artist = name1, name2, name3".
- Left of the equals sign: the name exactly as I give it, not changed in any way.
- name1: the name music platforms most often list this artist under that differs from the left, such as the name in its original script, the official English name or the official Chinese name.
- name2: the name on the left, copied exactly.
- name3 onwards: other spellings platforms really use; leave them out if there are none.

Only list artists that really have another name. Do not list:
- Names that differ only in Simplified or Traditional Chinese, letter case, accents or spaces (the tool handles those).
- Chinese transliterations of Western artists, unless the record label or platforms officially use them.
- Birth names, nicknames, band members, former band names, or names that are merely similar.
- Anything you are not sure of. Better to list too few than to guess: a wrong name makes the tool pick up someone else's lyrics.

You may look things up online, but output only these lines: no headings, numbering, explanations, links or sources. If the list is long, output it in parts and I will reply "continue".

Correct examples:
Masayoshi Takanaka = 高中正義, Masayoshi Takanaka
TOUMING MAGAZINE = 透明雜誌, TOUMING MAGAZINE
椎名林檎 = Sheena Ringo, 椎名林檎, Shiina Ringo
HYUKOH = 혁오, HYUKOH

Examples not to output (the reason in brackets):
Arctic Monkeys = 北極猴子, Arctic Monkeys (only a Chinese transliteration)
Frank Sinatra = Francis Albert Sinatra, Frank Sinatra (a birth name platforms do not use)

Here are my artists:

"""


def alias_prompt(artists, aliases):
    """Text to paste into any AI chat, asking for aliases of every artist that has none yet; and how many artists it names.
    Tags naming several artists are split, as lookups split them, and an artist counts as covered as lookups match aliases."""
    keys = [k.lower() for k in aliases]
    names = dict.fromkeys(p for a in artists for p in artist_parts(a))
    wanted = sorted((n for n in names if not any(k in n.lower() for k in keys)), key=str.casefold)
    return tr(ALIAS_PROMPT) + "\n".join(wanted), len(wanted)
