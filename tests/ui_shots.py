"""Drive the real window on throwaway data: a screenshot of each tab, then every flow a person can click through, including failures and closing mid-job.
ui_shots.py <out_dir>. No music is touched; the window stays behind other windows; exits non-zero if any check fails."""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import tkinter.ttk
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from winshot import save_window

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
d = tempfile.mkdtemp()
os.environ["LYRICSTOOL_DATA"] = d
from lyricstool import audio, dialogs, i18n, jobs, store as store_mod
from lyricstool.app import App, BACKUP_RISK
from lyricstool.sources import Hit

i18n.use("zh-TW")  # the texts checked below are the Chinese ones
lib =os.path.join(d, "music")
os.makedirs(lib)
for i, (artist, title) in enumerate((("陳綺貞", "太陽"), ("Lisa Ono", "Undecided"), ("大象體操", "Shadow"), ("椎名林檎", "宗教"))):
    p = os.path.join(lib, f"{i}.flac")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-metadata", f"artist={artist}", "-metadata", f"title={title}", p], check=True)
LRC = "\n".join(f"[00:{i:02d}.00]第 {i} 行歌詞" for i in range(10, 40))
back = audio.Backup(os.path.join(d, "bak", "app_sample"))
audio.write_lyrics(os.path.join(lib, "3.flac"), "[00:01.00]作曲 : 某人\n[00:02.00]Piano: 某人\n" + LRC, back)

cfg = store_mod.default_config()
cfg.update(library=lib, backup_root=os.path.join(d, "bak"), lrclib_db="", lrcget_db="")
store = store_mod.Store()
jobs.run_scan(store, cfg, jobs.Ctx())
none = store_mod.text_hash("")
store.put_item(os.path.join(lib, "0.flac"), "new", "LRCLIB（本機）", LRC, [], [], 30, "陳綺貞 - 太陽", 1.0, none)
store.put_item(os.path.join(lib, "1.flac"), "new", "網易雲（已轉繁體）", LRC, ["Undecided - 小野麗莎", "Piano: Don Grusin"], [["length_diff", 2.8], ["title_differs"]], 30, "小野リサ - Undecided", 3.8, none)
store.save_lookup(os.path.join(lib, "2.flac"), "notfound")

ctypes.windll.shcore.SetProcessDpiAwareness(1)  # as lyricstool_main does, so the shots look like the real thing
root = tk.Tk()
app = App(root, store, cfg, autoscan=False)
root.update()
root.lower()  # behind whatever someone is doing on this computer; off-screen would not work, Windows stops painting it there


def shot(name):
    root.update()
    root.update_idletasks()
    root.update()
    root.after(300)
    root.update()
    save_window(int(root.frame(), 16), os.path.join(out, name + ".png"))


failures = []


def check(name, got, want):
    print(("ok  " if got == want else "FAIL"), name, "" if got == want else f"got {got!r}, wanted {want!r}")
    if got != want:
        failures.append(name)


def pump(seconds=25):
    """Run the window until the background job and its messages are all done, the way mainloop would."""
    end = time.time() + seconds
    while (app.thread and app.thread.is_alive()) or not app.events.empty():
        root.update()
        time.sleep(0.02)
        if time.time() > end:
            raise TimeoutError("job did not finish")
    for _ in range(15):  # poll runs on a 100 ms timer
        root.update()
        time.sleep(0.02)


def buttons_in(widget):
    return [w for child in widget.winfo_children() for w in ([child] if isinstance(child, tkinter.ttk.Button) else buttons_in(child))]


def answer(label, picture=None):
    """Once the dialog about to open is up, press its button the way a person would. It shows for a moment, the one thing here that does."""
    def go():
        wins = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
        if not wins or not wins[0].grab_current():  # the grab is the last step before it waits for an answer
            root.after(50, go)
            return
        win = wins[0]
        win.lower()
        if picture:
            win.update()
            win.after(300)
            win.update()
            save_window(int(win.frame(), 16), os.path.join(out, picture + ".png"))
        check("Enter and Esc work in it", all(win.bind(key) for key in ("<Return>", "<Escape>")), True)  # only bound: a window without the system's focus gets no keys, and this one must not take it
        next(b for b in buttons_in(win) if b.cget("text") == label).invoke()
    root.after(50, go)


def rows():
    return len(app.tree.get_children())


def cards():
    return {k: v.cget("text").split()[0] for k, v in app.card_vals.items()}


class Answers:
    label = "假來源"

    def find(self, q):
        return Hit("假來源", LRC, matched_artist=q.artist, matched_title=q.title, matched_length=q.length)


class Slow:
    label = "很慢的來源"

    def find(self, q):
        time.sleep(1.5)
        return None


shot("1_overview")
check("first cards", cards(), {"synced": "1", "plain": "0", "none": "3", "other": "0"})
app.nb.select(app.tab_lookup)
app.write_log("來源順序：LRCLIB（線上） → 網易雲 → 酷狗")
app.write_log("查詢完成：找到 94 首、查無歌詞 41 首")
shot("2_lookup")
check("targets counted", app.target_label.cget("text"), "符合 2 首")
app.nb.select(app.tab_review)
kids = app.tree.get_children()
app.tree.selection_set(kids[-1])
app.show_preview()
shot("3_review")
app.nb.select(app.tab_backup)
root.update()
app.fill_backups()
check("old batch listed", len(app.batch_tree.get_children()), 1)
app.batch_tree.selection_set(app.batch_tree.get_children()[0])
app.show_batch()
shot("4_backup")
app.nb.select(app.tab_settings)
shot("5_settings")
check("buttons beside the paths say browse", [b.cget("text") for b in buttons_in(app.tab_settings) if b.cget("text") == "瀏覽"], ["瀏覽"] * 4)

# The real dialogs.
for label, want in (("是", True), ("否", False)):
    answer(label)
    check(f"question answered with {label}", dialogs.askyesno("歌詞工具", "確定要寫入 3 首歌曲嗎？"), want)
for label, want in (("取消", False), ("關閉備份", True)):
    answer(label, "7_backup_risk" if label == "取消" else None)
    check(f"risk answered with {label}", dialogs.confirm_risk("關閉備份", BACKUP_RISK, "關閉備份"), want)
answer("確定")
dialogs.showinfo("歌詞工具", "完成")
check("no dialog left open", [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)], [])

with mock.patch("lyricstool.app.dialogs") as box, mock.patch("lyricstool.app.filedialog") as files:
    box.askyesno.return_value = True
    app.nb.select(app.tab_review)

    # Finding things in the list.
    check("all rows", rows(), 3)
    check("action column says what writing will do", {app.tree.set(i, "Title"): app.tree.set(i, "Action") for i in app.tree.get_children()},
          {"太陽": "寫入新歌詞", "Undecided": "寫入新歌詞；長度差 2.8 秒；歌名不完全相同", "宗教": "刪除 2 行製作名單"})
    app.v_kind.set("清理製作名單")
    app.fill_review()
    check("filter by kind", rows(), 1)
    app.v_kind.set("全部")
    app.v_flagged.set(True)
    app.fill_review()
    check("flagged only", rows(), 1)
    app.v_flagged.set(False)
    app.v_search.set("lisa")
    check("search is live and ignores case", rows(), 1)
    app.v_search.set("")

    # Ticking.
    app.check_view(False)
    check("nothing ticked disables writing", (str(app.btn_write.cget("state")), app.btn_write.cget("text")), ("disabled", "備份並寫入 0 首"))
    app.check_view(True)
    kids = app.tree.get_children()
    app.tree.selection_set(kids[0])
    root.update()
    app.toggle_selected(None)
    check("space unticks", (len(store.selected_items()), app.btn_write.cget("text")), (2, "備份並寫入 2 首"))
    app.toggle_selected(None)
    check("space ticks again", len(store.selected_items()), 3)
    export = os.path.join(d, "review.csv")
    files.asksaveasfilename.return_value = export
    app.export_review()
    check("list exported", os.path.exists(export), True)
    missing = os.path.join(d, "missing.csv")
    files.asksaveasfilename.return_value = missing
    app.export_missing()
    check("missing list exported", os.path.exists(missing), True)

    # Removing from the list is remembered across a new lookup.
    target = next(i for i in app.tree.get_children() if app.tree.set(i, "Title") == "太陽")
    app.tree.selection_set(target)
    root.update()
    app.dismiss_selected()
    check("removed from list", rows(), 2)
    check("its details go with it", "從左側選取歌曲" in app.preview.get("1.0", "end"), True)
    check("reason shown", any(app.reason_tree.set(i, "Reason") == "已從清單移除" for i in app.reason_tree.get_children()), True)

    # Looking up, with a source that answers.
    app.v_notfound.set(True)
    app.count_targets()
    check("wider scope counted", app.target_label.cget("text"), "符合 3 首")
    with mock.patch.object(jobs, "build_sources", lambda c, x: [Answers()]):
        check("lookup started", app.lookup() is None, True)
        check("buttons locked during a job", str(app.btn_lookup.cget("state")), "disabled")
        pump()
    check("jumped to review", app.nb.select(), str(app.tab_review))
    check("dismissed one stays out, others in", rows(), 3)
    check("buttons back after the job", str(app.btn_lookup.cget("state")), "normal")
    check("log shows each track", "找到：" in app.log_box.get("1.0", "end"), True)

    # Writing through the button: asks first, then reports.
    box.askyesno.return_value = False
    app.write()
    pump()
    check("answering no writes nothing", audio.read_info(os.path.join(lib, "1.flac"))["lyrics"], "")
    box.askyesno.return_value = True
    app.write()
    pump()
    check("written", cards(), {"synced": "3", "plain": "0", "none": "1", "other": "0"})
    check("tag on disk", audio.read_info(os.path.join(lib, "1.flac"))["lyrics"], LRC)
    check("result reported", box.showinfo.call_args[0][0], "寫入完成")
    check("list emptied", rows(), 0)

    # Restoring through the button, one file then the rest.
    app.nb.select(app.tab_backup)
    root.update()
    newest = app.batch_tree.get_children()[0]
    app.batch_tree.selection_set(newest)
    app.show_batch()
    files_in_batch = app.batch_files.get_children()
    check("batch files listed", len(files_in_batch), 3)
    app.batch_files.selection_set(files_in_batch[0])
    app.restore(True)
    pump()
    check("restored one", cards()["synced"], "2")
    app.restore(False)
    pump()
    check("restored all", cards(), {"synced": "1", "plain": "0", "none": "3", "other": "0"})

    # Clicking a card lists its songs and each song's details; clicking again goes back.
    def click_card(key):
        app.card_vals[key].event_generate("<Button-1>")
        root.update()

    def detail():
        return app.card_detail.get("1.0", "end")

    app.nb.select(app.tab_overview)
    root.update()
    app.card_vals["none"].event_generate("<Enter>")
    check("a card shows it can be clicked", app.card_frames["none"].instate(["active"]), True)
    app.card_vals["none"].event_generate("<Leave>")
    click_card("none")
    check("card opens its list", (app.card_view.winfo_ismapped(), app.lists.winfo_ismapped(), len(app.card_tree.get_children())), (1, 0, 3))
    check("says what and how many", app.card_title.cget("text"), "沒有歌詞　3 首")
    check("hint before anything is picked", "從左側選取歌曲" in detail(), True)
    picked = next(i for i in app.card_tree.get_children() if app.card_tree.set(i, "Title") == "Shadow")
    app.card_tree.selection_set(picked)
    root.update()
    shot("9_card_list")
    check("details of a song without lyrics", all(x in detail() for x in ("大象體操 - Shadow", picked, "FLAC", "16/44.1kHz", "0:01", "（無）")), True)
    check("audio quality in the list", app.card_tree.set(picked, "Quality"), "16/44.1kHz")
    check("why it has none", app.card_tree.set(picked, "Status") in detail(), True)
    app.v_card_search.set("SHADOW")
    check("search in the list", (len(app.card_tree.get_children()), app.card_title.cget("text")), (1, "沒有歌詞　1 首"))
    check("details stay while the song is listed", "大象體操 - Shadow" in detail(), True)
    app.v_card_search.set("沒有這首歌")
    check("nothing found, details go too", (len(app.card_tree.get_children()), "從左側選取歌曲" in detail()), (0, True))
    click_card("synced")
    check("another card takes over, search cleared", (app.open_state, len(app.card_tree.get_children()), app.v_card_search.get()), ("synced", 1, ""))
    check("marked as the open one", (app.card_frames["synced"].instate(["selected"]), app.card_frames["none"].instate(["selected"])), (True, False))
    check("no lookup column once it has lyrics", "Status" in app.card_tree.cget("displaycolumns"), False)
    app.card_tree.selection_set(app.card_tree.get_children()[0])
    root.update()
    check("details show the embedded lyrics", all(x in detail() for x in ("椎名林檎 - 宗教", "第 10 行歌詞", "內嵌歌詞")), True)
    extra = [os.path.join(lib, "broken.flac"), os.path.join(lib, "other.ogg")]
    for p in extra:
        open(p, "wb").close()
    click_card("other")
    app.scan()
    pump()
    check("files without tags are listed by file name", sorted(app.card_tree.set(i, "Title") for i in app.card_tree.get_children()), ["broken.flac", "other.ogg"])
    app.card_tree.selection_set(os.path.join(lib, "broken.flac"))
    root.update()
    x, _, w, _ = app.card_tree.bbox(os.path.join(lib, "broken.flac"), "Status")
    check("the reason is in view after a card without that column", 0 < x + w <= app.card_tree.winfo_width(), True)
    check("a damaged file says what is wrong", all(x in detail() for x in ("檔案損壞或無法讀取", "錯誤：")) and "內嵌歌詞" not in detail(), True)
    for p in extra:
        os.remove(p)
    app.scan()
    pump()
    check("the open list follows a rescan", (len(app.card_tree.get_children()), "從左側選取歌曲" in detail()), (0, True))
    click_card("other")
    check("clicking it again goes back", (app.open_state, app.card_view.winfo_ismapped(), app.lists.winfo_ismapped(), app.card_frames["other"].instate(["selected"])), (None, 0, 1, False))
    click_card("plain")
    next(b for b in buttons_in(app.card_view) if b.cget("text") == "返回").invoke()
    root.update()
    check("back button goes back", (app.open_state, app.lists.winfo_ismapped()), (None, 1))
    check("cards can be opened from the keyboard", all(app.card_frames["none"].bind(k) for k in ("<Return>", "<space>")), True)

    # Settings: the button beside a path only fills it in; nothing is saved or scanned until 儲存設定.
    outside, inside = os.path.join(d, "bak"), os.path.join(lib, "bak")
    files.askdirectory.return_value = inside
    app.pick("backup_root", "dir")
    check("picking fills the field and saves nothing", (app.v_paths["backup_root"].get(), cfg["backup_root"], app.thread is None or not app.thread.is_alive()), (inside, outside, True))
    app.save_settings()
    check("saved by 儲存設定, inside the library too", (cfg["backup_root"], store_mod.load_config()["backup_root"]), (inside, inside))
    files.askdirectory.return_value = ""
    app.pick("backup_root", "dir")
    check("closing the folder window leaves the field as it was", app.v_paths["backup_root"].get(), inside)
    app.v_paths["backup_root"].set(outside)
    app.save_settings()

    # Bad input is refused and changes nothing.
    app.v_paths["backup_root"].set("")
    box.showerror.reset_mock()
    app.save_settings()
    check("empty backup folder refused", (box.showerror.called, cfg["backup_root"]), (True, outside))
    app.v_paths["backup_root"].set(outside)
    app.v_paths["library"].set(os.path.join(d, "nowhere"))
    box.showerror.reset_mock()
    app.save_settings()
    check("missing library refused", (box.showerror.called, cfg["library"]), (True, lib))
    app.v_paths["library"].set(lib)
    app.alias_box.delete("1.0", "end")
    app.alias_box.insert("1.0", "甲 = 乙, 丙\n沒有等號的行\n丁 =\n戊 ＝ 己，庚、辛\n")
    app.save_settings()
    check("aliases parsed, with commas as any input method types them", cfg["aliases"], {"甲": ["乙", "丙"], "戊": ["己", "庚", "辛"]})
    check("settings saved to disk", store_mod.load_config()["aliases"], {"甲": ["乙", "丙"], "戊": ["己", "庚", "辛"]})
    app.v_excluded.set("甲，乙、丙, 丁")
    app.save_settings()
    pump()
    check("folder names parsed the same way", cfg["excluded"], ["甲", "乙", "丙", "丁"])
    app.v_excluded.set("")
    app.v_skip_albums.set(True)
    app.save_settings()
    pump()
    check("album skip saved", cfg["skip_albums"], True)
    app.copy_alias_prompt()
    check("prompt for an AI copied, naming the artists without aliases", root.clipboard_get().split("\n\n")[-1].split("\n"), ["Lisa Ono", "大象體操", "椎名林檎", "陳綺貞"])
    check("and says how many", "4 位" in box.showinfo.call_args[0][1], True)
    app.v_skip_albums.set(False)
    app.save_settings()
    pump()

    # A source that keeps refusing asks once per job whether to stop.
    box.askyesno.reset_mock()
    box.askyesno.return_value = False
    log_when_asked = []
    box.askyesno.side_effect = lambda *a: log_when_asked.append("問題之後的記錄" in app.log_box.get("1.0", "end")) or False
    app.events.put(("limited", "網易雲"))
    app.events.put(("limited", "網易雲"))
    app.events.put(("log", "問題之後的記錄"))
    pump()
    box.askyesno.side_effect = None
    check("rate limit asked about once", box.askyesno.call_count, 1)
    check("the question names the source", "網易雲" in box.askyesno.call_args[0][1], True)
    check("the log keeps going while the question waits", log_when_asked, [True])

    # Progress wording.
    app.started = time.monotonic() - 60
    app.on_progress(10, 20, "某首歌")
    check("time left in minutes", app.eta.cget("text"), "剩餘 1 分鐘")
    app.started = time.monotonic() - 7200
    app.on_progress(10, 20, "某首歌")
    check("time left in hours", app.eta.cget("text"), "剩餘 2.0 小時")
    shot("8_progress")
    app.set_running(False)

    # A job that blows up reports it, and the app carries on.
    box.showerror.reset_mock()
    with mock.patch.object(jobs, "run_scan", side_effect=RuntimeError("boom")):
        app.scan()
        pump()
    check("job failure reported", box.showerror.called, True)
    check("usable after a failure", str(app.btn_scan.cget("state")), "normal")
    app.scan()
    pump()
    check("scan works again", cards()["synced"], "1")

    # The credits switch: off withdraws the cleanup suggestions, on brings them back, and the list says what they are.
    app.nb.select(app.tab_review)
    app.v_kind.set("清理製作名單")
    app.fill_review()
    check("cleanup suggested again", rows(), 1)
    first = app.tree.get_children()[0]
    check("says what it is", (app.tree.set(first, "Type"), app.tree.set(first, "Source")), ("清理製作名單", "現有歌詞"))
    shot("6_cleanup")
    app.v_credits.set(False)
    app.save_settings()
    pump()
    check("switch off withdraws it", (rows(), cfg["remove_credits"]), (0, False))
    check("switch saved to disk", store_mod.load_config()["remove_credits"], False)
    app.v_credits.set(True)
    app.save_settings()
    pump()
    check("switch on brings it back", rows(), 1)
    app.v_kind.set("全部")

    # The backup switch: turning it off warns, and writing without backups leaves no batch behind.
    app.nb.select(app.tab_review)
    root.update()
    check("the tick beside the write button is showing", app.chk_backup_review.winfo_ismapped(), 1)
    shot("10_backup_tick")
    box.confirm_risk.return_value = False
    app.chk_backup_review.invoke()
    check("turning backups off warns, cancelling keeps them on", (box.confirm_risk.call_args[0][1], app.v_backup.get(), cfg["backup_enabled"], app.btn_write.cget("text")), (i18n.tr(BACKUP_RISK), True, True, "備份並寫入 1 首"))
    app.v_excluded.set("還沒儲存的修改")
    box.confirm_risk.return_value = True
    app.chk_backup_review.invoke()
    check("going ahead takes effect at once", (app.v_backup.get(), cfg["backup_enabled"], store_mod.load_config()["backup_enabled"]), (False, False, False))
    check("and saves nothing else that was still being edited", store_mod.load_config()["excluded"], [])
    app.v_excluded.set("")
    check("the settings page shows the same tick", app.chk_backup.instate(["selected"]), False)
    check("the write button stops promising a backup", app.btn_write.cget("text"), "寫入 1 首")
    app.nb.select(app.tab_backup)
    root.update()
    check("the backup page says so", "備份已關閉" in app.backup_label.cget("text"), True)
    batches = len(jobs.list_backups(cfg["backup_root"]))
    box.askyesno.reset_mock()
    box.askyesno.return_value = True
    app.write()
    check("the question before writing says so", "備份已關閉" in box.askyesno.call_args[0][1], True)
    pump()
    check("written without a new batch", (audio.read_info(os.path.join(lib, "3.flac"))["lyrics"], len(jobs.list_backups(cfg["backup_root"]))), (LRC, batches))
    box.confirm_risk.reset_mock()
    app.chk_backup.invoke()
    check("turning them back on asks nothing, from either page", (box.confirm_risk.called, app.v_backup.get(), cfg["backup_enabled"], app.chk_backup_review.instate(["selected"])), (False, True, True, True))
    check("and the button promises it again", app.btn_write.cget("text"), "備份並寫入 0 首")

    # A bug in a completion handler must not freeze the window.
    box.showerror.reset_mock()
    app.events.put(("done", (lambda r: 1 / 0, None)))
    pump()
    check("handler error shown", box.showerror.called, True)
    app.events.put(("log", "still alive"))
    pump()
    check("window still updating", "still alive" in app.log_box.get("1.0", "end"), True)

    # Stop button.
    with mock.patch.object(jobs, "build_sources", lambda c, x: [Slow()]):
        app.v_notfound.set(True)
        app.lookup()
        app.stop()
        pump()
    check("stopped cleanly", str(app.btn_stop.cget("state")), "disabled")

    # Only one job at a time.
    with mock.patch.object(jobs, "build_sources", lambda c, x: [Slow()]):
        store.db.execute("delete from lookups")
        store.commit()
        app.lookup()
        box.showinfo.reset_mock()
        check("second job refused", app.start("x", lambda ctx: None), False)
        check("and explained", box.showinfo.called, True)

        # Closing during a job: asks, stops at a safe point, then the window goes away.
        box.askyesno.return_value = False
        app.on_close()
        check("answering no keeps it open", app.closing, False)
        box.askyesno.return_value = True
        app.on_close()
        end = time.time() + 20
        while not app.closed and time.time() < end:
            root.update()
            time.sleep(0.02)
        check("closing mid-job shuts the window", app.closed, True)
        try:
            root.winfo_exists()
            check("window really gone", False, True)
        except tk.TclError:
            check("window really gone", True, True)

shutil.rmtree(d, ignore_errors=True)
print("FAILED: " + ", ".join(failures) if failures else "all UI checks passed")
sys.exit(1 if failures else 0)
