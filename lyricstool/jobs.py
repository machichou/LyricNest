"""The work the UI starts on a background thread: scan, look up, write, restore. No Tk in here, so all of it can be tested alone."""
import csv
import glob
import json
import logging
import os
import threading
import time

from . import audio
from .clean import clean_lrc, is_synced, last_stamp, lyric_state, squash, strip_credits, sung_lines
from .sources import Busy, build_sources, make_query
from .store import text_hash

log = logging.getLogger("lyricstool")
MAX_PASSES = 3
KIND_NAMES = {"new": "新歌詞", "cleanup": "清理製作名單"}


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

    def wait(self, seconds, template):
        for left in range(int(seconds), 0, -1):
            self.emit("status", template.format(left))
            if self._cancel.wait(1):
                return

    def log(self, text):
        log.info(text)
        self.emit("log", text)

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
        raise ValueError(f"找不到音樂資料夾：{library}")
    ctx.status("正在列出檔案…")
    known = store.known_files()
    paths = list(walk_audio(library, cfg["excluded"]))
    if not paths and known:
        raise ValueError(f"{library} 中找不到任何音樂檔，可能是磁碟未連接。原有資料已保留。")
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
            ctx.progress(n, len(paths), f"已讀取 {n}/{len(paths)} 首")
    if not ctx.cancelled():
        gone = set(known) - set(paths)
        store.drop_tracks(gone)  # review items stay: if a drive dropped out mid-walk, they come back with the files on the next scan
        store.db.execute("delete from items where state='pending' and kind='new' and path in (select path from tracks where state='synced')")
        if cfg["remove_credits"]:
            n_clean = suggest_cleanups(store)
        else:
            n_clean = 0
            store.db.execute("delete from items where state='pending' and kind='cleanup'")
        ctx.log(f"掃描完成：{len(paths)} 首，更新 {changed} 首，移除 {len(gone)} 首，建議清理製作名單 {n_clean} 首")
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
        flags.append(f"長度差 {abs(hit.matched_length - track['length']):.1f} 秒")
    if cleaned.sung < 8:
        flags.append(f"僅 {cleaned.sung} 行")
    if track["length"] and last_stamp(cleaned.lrc) < track["length"] / 2:
        flags.append("歌詞在歌曲中段前就結束")
    if any("*" in line for line in sung_lines(cleaned.lrc)):
        flags.append("部分文字被遮蔽（*）")
    if squash(hit.matched_title) != squash(track["title"]):
        flags.append("歌名不完全相同")
    if track["state"] == "plain":
        flags.append("會取代純文字歌詞")
    return flags


def action_text(item):
    """What writing this row will do, then anything worth a second look."""
    if item["kind"] != "new":
        return f"刪除 {len(json.loads(item['removed']))} 行製作名單"
    action = "取代純文字歌詞" if item["track_state"] == "plain" else "寫入新歌詞"
    return "；".join([action] + [f for f in json.loads(item["flags"]) if f != "會取代純文字歌詞"])


def lookup_one(sources, track, cfg, store, ctx, failed=None):
    """Ask each source in turn; the first usable answer wins and becomes an item to review. `failed` collects sources that could not answer."""
    q = make_query(track["artist"] or track["album_artist"], track["title"], track["album"], track["length"], track["path"], cfg["aliases"])
    failed = [] if failed is None else failed
    instrumental = ""
    for s in sources:
        if ctx.cancelled():
            return None
        try:
            hit = s.find(q)
        except Busy:
            failed.append(s)
            continue
        except Exception:
            log.exception("source %s failed on %s", s.label, track["path"])
            failed.append(s)
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
        result = store.put_item(track["path"], "new", source, cleaned.lrc, cleaned.removed, flags_for(track, hit, cleaned), cleaned.sung,
                                f"{hit.matched_artist} - {hit.matched_title}", hit.matched_length, text_hash(track["lyrics"]))
        status = "dismissed" if result == "dismissed" else "found"
        store.save_lookup(track["path"], status, hit.source)
        return status
    status = "instrumental" if instrumental else "busy" if failed else "notfound"
    store.save_lookup(track["path"], status, instrumental)
    return status


def retry_wait(queue):
    """Seconds until every source these tracks are waiting on is back in use; at least a minute, so a brief hiccup gets a moment to pass."""
    now = time.monotonic()
    return max([60] + [s.lim.suspended_until - now for _, failed in queue for s in failed if hasattr(s, "lim")])


def run_lookup(store, cfg, ctx, statuses=("never", "busy"), text=""):
    """Throttled tracks are asked again at the end, up to MAX_PASSES rounds, without anyone rerunning anything."""
    every = store.lookup_targets(statuses, text)
    targets = [t for t in every if t["title"] and (t["artist"] or t["album_artist"])]
    skipped = len(every) - len(targets)
    if skipped:
        ctx.log(f"{skipped} 首缺少歌手或歌名標籤，已略過")
    if not targets:
        ctx.log("沒有需要查詢的歌曲")
        return {}
    sources = build_sources(cfg, ctx)
    if not sources:
        raise ValueError("沒有可用的歌詞來源，請至「設定」開啟。")
    ctx.log("來源順序：" + " → ".join(s.label for s in sources))
    counts = {}
    total = len(targets)
    queue = [(t, sources) for t in targets]
    for round_no in range(1, MAX_PASSES + 1):
        retry = []
        for n, (t, asking) in enumerate(queue, 1):
            if ctx.cancelled():
                ctx.log(f"已停止。查詢結果已儲存，下次會接續查詢：{summary(counts)}")
                return counts
            if not os.path.exists(t["path"]):
                continue
            ctx.progress(total - len(queue) + n - 1, total, f"{t['artist']} - {t['title']}")
            failed = []
            status = lookup_one(asking, t, cfg, store, ctx, failed)
            if status is None:
                continue
            ctx.log(f"{LABELS.get(status, status)}：{t['artist'] or t['album_artist']} - {t['title']}")
            if status == "busy":
                retry.append((t, failed))  # the other sources already answered, so only these get asked again
            else:
                counts[status] = counts.get(status, 0) + 1
        ctx.progress(total - len(retry), total, "")
        queue = retry
        if not queue:
            break
        if round_no < MAX_PASSES:
            wait = retry_wait(queue)
            ctx.log(f"{len(queue)} 首因來源忙碌需要重查，約 {max(wait / 60, 1):.0f} 分鐘後自動重試（第 {round_no + 1} 輪）")
            ctx.wait(wait, "等待來源恢復，{} 秒後重查")
    if queue:
        counts["busy"] = len(queue)
    ctx.log(f"查詢完成：{summary(counts)}")
    return counts


LABELS = {"found": "找到", "notfound": "查無歌詞", "instrumental": "純音樂", "busy": "來源忙碌待重查", "dismissed": "與先前移除的歌詞相同"}


def summary(counts):
    return "、".join(f"{LABELS.get(k, k)} {v} 首" for k, v in counts.items()) or "沒有結果"


def run_write(store, cfg, ctx):
    """Write everything ticked. Each file is rechecked first: the library keeps changing while lists sit waiting."""
    items = store.selected_items()
    if not items:
        ctx.log("尚未勾選要寫入的歌曲")
        return {"ok": 0, "failed": [], "stale": []}
    folder, backup = "", None
    if cfg["backup_enabled"]:
        folder = os.path.join(os.path.abspath(cfg["backup_root"]), time.strftime("app_%Y-%m-%d_%H%M%S"))
        backup = audio.Backup(folder)
        ctx.log(f"備份資料夾：{folder}")
    else:
        ctx.log("備份已關閉，本次寫入後無法還原")
    result = {"ok": 0, "failed": [], "stale": [], "folder": folder}
    for n, it in enumerate(items, 1):
        if ctx.cancelled():
            ctx.log("已停止。已寫入的歌曲皆通過驗證，其餘仍保留在清單上")
            break
        path = it["path"]
        ctx.progress(n - 1, len(items), os.path.basename(path))
        reason = stale_reason(store, cfg, it)
        if reason:
            store.set_item_state(it["id"], "stale")
            if reason == "檔案已被替換":
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
    ctx.log(f"寫入完成：成功 {result['ok']} 首、失敗 {len(result['failed'])} 首、因檔案已變動略過 {len(result['stale'])} 首")
    for path, why in result["failed"] + result["stale"]:
        ctx.log(f"  {why}：{path}")
    return result


def stale_reason(store, cfg, it):
    path = it["path"]
    if not os.path.exists(path):
        return "檔案不存在"
    if excluded(path, cfg["library"], cfg["excluded"]):
        return "位於略過的資料夾內"
    try:
        info = audio.read_info(path)
    except Exception as e:
        return f"無法讀取檔案（{e.__class__.__name__}）"
    t = store.track(path)
    if it["kind"] == "new":
        if is_synced(info["lyrics"]):
            return "已經有同步歌詞"
        if t and abs(info["length"] - t["length"]) > 1:
            return "檔案已被替換"
    if text_hash(info["lyrics"]) != it["base_hash"]:
        return "歌詞已被修改"
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
    ctx.log(f"還原完成：{done} 首，略過 {len(skipped)} 首")
    for p, why in skipped:
        ctx.log(f"  {why}：{p}")
    return {"done": done, "skipped": skipped}


def export_review(rows, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["勾選", "行為類型", "歌手", "歌名", "專輯", "來源", "比對結果", "行數", "標記", "刪除的內容", "路徑"])
        for r in rows:
            w.writerow(["是" if r["selected"] else "否", KIND_NAMES[r["kind"]], r["artist"], r["title"], r["album"], r["source"], r["matched"], r["lines"],
                        "；".join(json.loads(r["flags"])), " | ".join(json.loads(r["removed"])), r["path"]])


def export_missing(rows, path):
    names = {"never": "尚未查詢", "notfound": "查無歌詞", "instrumental": "純音樂或無歌詞", "busy": "來源忙碌待重查", "found": "待審閱", "dismissed": "已從清單移除"}
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["歌手", "歌名", "專輯", "目前歌詞", "格式", "查詢結果", "路徑"])
        for r in rows:
            state = {"plain": "純文字", "none": "沒有", "unsupported": "不支援的格式", "unreadable": "無法讀取"}.get(r["state"], r["state"])
            w.writerow([r["artist"], r["title"], r["album"], state, r["ext"], names.get(r["status"], r["status"]), r["path"]])
