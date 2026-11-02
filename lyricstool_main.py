"""Program entry: logging, one instance at a time, then the window. `--selftest out.json` proves a packaged build has everything it needs."""
import json
import logging
import logging.handlers
import os
import sys
import tkinter as tk

from lyricstool import dialogs, store as store_mod, theme


def setup_logging():
    h = logging.handlers.RotatingFileHandler(os.path.join(store_mod.data_dir(), "app.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(h)
    logging.getLogger().setLevel(logging.INFO)


def single_instance():
    """Two copies writing tags at once is the one thing that must never happen; the OS drops this lock if we crash."""
    import msvcrt
    fh = open(os.path.join(store_mod.data_dir(), "app.lock"), "w")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return None
    return fh


def selftest(out):
    """Everything PyInstaller could have left out, plus HTTPS certificates."""
    import urllib.request
    import wave
    from lyricstool.audio import stream_quality
    from lyricstool.clean import clean_lrc
    from lyricstool.sources import _lrcget_lrc, matches, make_query
    r = {"tw": clean_lrc("[00:01.00]头发\n[00:02.00]b\n[00:03.00]c", "x").lrc.startswith("[00:01.00]頭髮")}
    r["match"] = matches(make_query("陳綺貞", "太陽", "", 200, "", {}), "陈绮贞", "太阳", 200)
    r["yaml"] = _lrcget_lrc("lines:\n- text: hi\n  start_ms: 61500\n") == "[01:01.50]hi"
    s = store_mod.Store(os.path.join(os.path.dirname(os.path.abspath(out)), "selftest.sqlite"))
    r["sqlite"] = isinstance(s.counts(), dict)
    sample = os.path.join(os.path.dirname(os.path.abspath(out)), "selftest.wav")
    with wave.open(sample, "wb") as w:  # the reader for this format is only imported when a file needs it
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    r["formats"] = stream_quality(sample) == "16/8.0kHz"
    root = tk.Tk()
    root.withdraw()
    r["tk"] = bool(tk.font.families())
    root.destroy()
    try:
        req = urllib.request.Request("https://lrclib.net/api/get?artist_name=TV+Girl&track_name=Not+Allowed&album_name=&duration=0", headers={"User-Agent": "LyricsTool selftest"})
        try:
            urllib.request.urlopen(req, timeout=20).read()
            r["https"] = True
        except urllib.error.HTTPError as e:
            r["https"] = e.code in (400, 404)
    except Exception as e:
        r["https"] = repr(e)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(r, fh)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        selftest(sys.argv[2])
        return
    setup_logging()
    lock = single_instance()
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # has to come before the window exists, or text is blurry on scaled displays
    except (OSError, AttributeError):
        pass
    root = tk.Tk()
    if lock is None:
        root.withdraw()
        theme.apply(root, root.winfo_fpixels("1i") / 96)
        dialogs.showinfo("歌詞工具", "歌詞工具已在執行中。")
        return
    from lyricstool.app import App
    App(root, store_mod.Store(), store_mod.load_config())
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logging.getLogger().exception("fatal")
        raise
