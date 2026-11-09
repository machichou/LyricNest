"""The window. Everything slow runs through App.start on a worker thread and reports back over a queue."""
import json
import logging
import os
import queue
import re
import threading
import time
import sys
import tkinter as tk
from tkinter import filedialog, ttk

from . import __author__, __version__, audio, dialogs, i18n, jobs, theme
from .i18n import stored_text, tr
from .store import save_config
from .theme import BG, DANGER, FONT, LINE, MUTED, STATE_COLORS, WARN_BG

log = logging.getLogger("lyricstool")
WHY = {"never": "Not looked up yet", "found": "Found, waiting for review", "notfound": "No lyrics found", "instrumental": "A source says instrumental or no lyrics", "dismissed": "Removed from the list",
       "busy": "Source busy, to be asked again", "unsupported": "Format not supported for writing", "unreadable": "Damaged or unreadable file"}
CARDS = {"synced": ("Synced lyrics", ("synced",)), "plain": ("Plain lyrics only", ("plain",)), "none": ("No lyrics", ("none",)), "other": ("Unsupported or damaged", ("unsupported", "unreadable"))}
CARD_COLUMNS = (("Artist", 100, True), ("Title", 150, True), ("Status", 170, False), ("Album", 100, True), ("Format", 60, False), ("Quality", 100, False))


REVIEW_HINT = "Select a song on the left to see its source, anything to check, what will be removed and the full lyrics.\n\nClick ☑ or press Space to tick or untick."
BACKUP_RISK = """With backups off, the original tags are not saved before lyrics are written:

• Wrong lyrics, once written, cannot be undone from Backups; they have to be fixed by hand.
• Replaced plain lyrics and removed credits cannot be recovered.

Audio is not affected, and the checks before and after writing still run.
Existing backups are not affected."""


def split_names(text):
    """Commas in any of the forms an input method gives."""
    return [x.strip() for x in re.split("[,，、]", text) if x.strip()]


def parse_aliases(text):
    """The alias box: one artist per line, 'tag text = name, name'."""
    aliases = {}
    for line in text.replace("＝", "=").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            names = split_names(v)
            if k.strip() and names:
                aliases[k.strip()] = names
    return aliases


def track_names(r):
    """A file without tags, or one that could not be read, still needs a line of its own."""
    return r["artist"] or r["album_artist"], r["title"] or os.path.basename(r["path"])


class App:
    def __init__(self, root, store, cfg, autoscan=True):
        self.root, self.store, self.cfg = root, store, cfg
        self.events = queue.Queue()
        self.ctx = None
        self.thread = None
        self.closing = False
        self.closed = False
        self.started = 0.0
        self.warned = set()
        self.review_rows = []
        root.title(f"{tr('LyricsBox')} {__version__}")
        self.scale = root.winfo_fpixels("1i") / 96
        width = min(self.px(1180), root.winfo_screenwidth() - 40)
        height = min(self.px(760), root.winfo_screenheight() - 100)
        root.geometry(f"{width}x{height}")
        root.minsize(min(self.px(960), width), min(self.px(620), height))
        root.configure(bg=BG)
        icon = os.path.join(getattr(sys, "_MEIPASS", os.path.join(os.path.dirname(__file__), "..")), "assets", "icon.ico")
        try:
            root.iconbitmap(default=icon)  # default: the dialogs get it too
        except tk.TclError:  # only cosmetic; never a reason not to start
            pass
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self.on_ui_error
        theme.apply(root, self.scale)
        self.build()
        self.refresh_all()
        root.update_idletasks()  # the title bar can only be changed once the window exists
        theme.dark_titlebar(root)
        root.after(100, self.poll)
        if autoscan and os.path.isdir(cfg.get("library") or ""):
            root.after(300, self.scan)
        elif not cfg.get("library"):
            self.nb.select(self.tab_settings)

    def px(self, value):
        """Layout sizes are written for a 100% display."""
        return int(value * self.scale)

    # ---- layout ----
    def build(self):
        self.v_backup = tk.BooleanVar(value=self.cfg["backup_enabled"])  # one setting, ticked from either the review page or the settings page
        ttk.Label(self.root, text=tr("LyricsBox"), style="Heading.TLabel").pack(anchor="w", padx=self.px(24), pady=(self.px(16), self.px(10)))
        bar = ttk.Frame(self.root, padding=(self.px(24), self.px(10)))
        bar.pack(side="bottom", fill="x")  # before the tabs, so a small window squeezes them and not this
        tk.Frame(self.root, height=1, bg=LINE).pack(side="bottom", fill="x")
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=self.px(24), pady=(0, self.px(12)))
        self.tab_overview = self.tab("Overview")
        self.tab_lookup = self.tab("Look Up Lyrics")
        self.tab_review = self.tab("Review & Write")
        self.tab_backup = self.tab("Backups")
        self.tab_settings = self.tab("Settings")
        self.build_overview()
        self.build_lookup()
        self.build_review()
        self.build_backup()
        self.build_settings()
        self.nb.bind("<<NotebookTabChanged>>", self.on_tab)

        self.status = ttk.Label(bar, text=tr("Ready"), width=60, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.eta = ttk.Label(bar, text="", style="Muted.TLabel")
        self.eta.pack(side="left", padx=10)
        self.bar = ttk.Progressbar(bar, length=260, maximum=1)
        self.bar.pack(side="left", padx=10)
        self.btn_stop = ttk.Button(bar, text=tr("Stop"), command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")

    def tab(self, name):
        f = ttk.Frame(self.nb, padding=(0, self.px(14), 0, 0))
        self.nb.add(f, text=tr(name))
        return f

    def card(self, parent):
        """A rounded panel for a list or text box; the padding keeps its square contents clear of the corners."""
        return ttk.Frame(parent, style="Card.TFrame", padding=self.px(5))

    def fill_with_scrollbar(self, widget):
        sb = ttk.Scrollbar(widget.master, command=widget.yview)
        widget.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")  # first, so a narrow window squeezes the list and not this
        widget.pack(side="left", fill="both", expand=True)

    def reading_pane(self, parent):
        t = tk.Text(parent, wrap="word", state="disabled", padx=12, pady=10, font=(FONT, 10), width=38, **theme.TEXT)
        t.tag_configure("head", font=(FONT, 11, "bold"))
        t.tag_configure("muted", foreground=MUTED)
        self.fill_with_scrollbar(t)
        return t

    def show_text(self, pane, *parts):
        """Replace what a reading pane shows; `parts` are (text, tag) pairs."""
        pane.configure(state="normal")
        pane.delete("1.0", "end")
        for text, tag in parts:
            pane.insert("end", text, tag)
        pane.configure(state="disabled")

    def build_overview(self):
        f = self.tab_overview
        top = ttk.Frame(f)
        top.pack(fill="x")
        self.btn_scan = ttk.Button(top, text=tr("Rescan"), command=self.scan)
        self.btn_scan.pack(side="right")
        ttk.Button(top, text=tr("Export songs without synced lyrics"), command=self.export_missing).pack(side="right", padx=8)
        self.lib_label = ttk.Label(top, style="Muted.TLabel")  # packed last, so a long path gets cut instead of the buttons
        self.lib_label.pack(side="left")

        cards = ttk.Frame(f)
        cards.pack(fill="x", pady=(12, 8))
        self.card_vals, self.card_frames, self.open_state = {}, {}, None
        for i, (key, (name, _)) in enumerate(CARDS.items()):
            c = self.card_frames[key] = ttk.Frame(cards, style="Card.TFrame", padding=(16, 12), takefocus=True, cursor="hand2")
            c.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 8, 0))
            cards.columnconfigure(i, weight=1, uniform="c")
            stripe = tk.Frame(c, width=self.px(5), bg=STATE_COLORS[key], cursor="hand2")  # ties the card to its colour in the bar below
            stripe.pack(side="left", fill="y", padx=(0, self.px(12)))
            self.card_vals[key] = ttk.Label(c, text="–", style="CardNum.TLabel", cursor="hand2")
            self.card_vals[key].pack(anchor="w")
            label = ttk.Label(c, text=tr(name), style="CardName.TLabel", cursor="hand2")
            label.pack(anchor="w")
            for w in (c, stripe, self.card_vals[key], label):
                w.bind("<Button-1>", lambda e, k=key: self.open_card(k))
                w.bind("<Enter>", lambda e, c=c: c.state(["active"]))
                w.bind("<Leave>", lambda e, c=c: c.state(["!active"]))
            for sequence in ("<Return>", "<space>"):
                c.bind(sequence, lambda e, k=key: self.open_card(k))
        self.stack = tk.Canvas(f, height=self.px(8), bg=BG, highlightthickness=0)
        self.stack.pack(fill="x", pady=(4, 12))
        self.stack.bind("<Configure>", lambda e: self.draw_stack())
        self.stack_counts = {}

        self.lists = lists = ttk.Frame(f)
        lists.pack(fill="both", expand=True)
        lists.columnconfigure(0, weight=1, uniform="l")
        lists.columnconfigure(1, weight=1, uniform="l")
        lists.rowconfigure(1, weight=1)
        ttk.Label(lists, text=tr("Why songs lack synced lyrics"), style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(lists, text=tr("Artists with the most songs not found"), style="Title.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 6))
        self.reason_tree = self.simple_tree(lists, "Reason", 0)
        self.artist_tree = self.simple_tree(lists, "Artist", 1, padx=(12, 0))
        self.build_card_list(f)

    def build_card_list(self, f):
        """What takes the place of the two lists above when a card is clicked: its songs on the left, the selected song's details on the right."""
        self.card_view = ttk.Frame(f)
        top = ttk.Frame(self.card_view)
        top.pack(fill="x", pady=(0, self.px(8)))
        self.card_title = ttk.Label(top, style="Title.TLabel")
        self.card_title.pack(side="left")
        ttk.Button(top, text=tr("Back"), command=lambda: self.open_card(self.open_state)).pack(side="right")
        self.v_card_search = tk.StringVar()
        self.v_card_search.trace_add("write", lambda *a: self.fill_card_list())
        ttk.Entry(top, textvariable=self.v_card_search, width=24).pack(side="right", padx=self.px(8))
        ttk.Label(top, text=tr("Search: ")).pack(side="right")
        pane = ttk.PanedWindow(self.card_view, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = self.card(pane)
        self.card_tree = self.make_tree(left, CARD_COLUMNS, "browse")
        self.fill_with_scrollbar(self.card_tree)
        self.card_tree.bind("<<TreeviewSelect>>", self.show_track)
        pane.add(left, weight=3)
        right = self.card(pane)
        self.card_detail = self.reading_pane(right)
        pane.add(right, weight=2)
        self.card_rows = {}

    def make_tree(self, parent, columns, selectmode):
        """`columns` are (name, width, stretch)."""
        t = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings", selectmode=selectmode)
        for name, width, stretch in columns:
            t.heading(name, text=tr(name), anchor="w")
            t.column(name, width=self.px(width), anchor="w", stretch=stretch)
        return t

    def simple_tree(self, parent, first_column, col, padx=0):
        wrap = self.card(parent)
        wrap.grid(row=1, column=col, sticky="nsew", padx=padx)
        t = self.make_tree(wrap, ((first_column, 300, True), ("Songs", 70, False)), "none")
        t.pack(fill="both", expand=True)
        return t

    def build_lookup(self):
        f = self.tab_lookup
        ttk.Label(f, text=tr("What to look up"), style="Title.TLabel").pack(anchor="w")
        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(6, 4))
        self.v_never = tk.BooleanVar(value=True)
        self.v_notfound = tk.BooleanVar(value=False)
        self.v_instr = tk.BooleanVar(value=False)
        for var, text in ((self.v_never, "Not looked up yet (and songs a busy source left)"), (self.v_notfound, "Not found before (and songs removed from the list)"), (self.v_instr, "Songs marked instrumental")):
            ttk.Checkbutton(opts, text=tr(text), variable=var, command=self.count_targets).pack(side="left", padx=(0, 18))
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(2, 8))
        ttk.Label(row, text=tr("Only where artist, album, title or path contains:")).pack(side="left")
        self.v_filter = tk.StringVar()
        self.v_filter.trace_add("write", lambda *a: self.count_targets())
        ttk.Entry(row, textvariable=self.v_filter, width=30).pack(side="left", padx=6)
        self.target_label = ttk.Label(row, text="", style="Muted.TLabel")
        self.target_label.pack(side="left", padx=8)
        self.btn_lookup = ttk.Button(row, text=tr("Start lookup"), style="Accent.TButton", command=self.lookup)
        self.btn_lookup.pack(side="right")
        ttk.Label(f, text=tr("Results go to Review & Write first; no file is written yet. Stopping or closing loses nothing: the next lookup carries on from there."), style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        wrap = self.card(f)
        wrap.pack(fill="both", expand=True)
        self.log_box = tk.Text(wrap, wrap="word", state="disabled", padx=10, pady=8, font=(FONT, 10), **theme.TEXT)
        self.log_box.tag_configure("found", foreground=STATE_COLORS["synced"])
        self.log_box.tag_configure("notfound", foreground=DANGER)
        self.fill_with_scrollbar(self.log_box)

    def build_review(self):
        f = self.tab_review
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        self.v_kind = tk.StringVar(value=tr("All"))
        kind = ttk.Combobox(top, textvariable=self.v_kind, values=(tr("All"), *map(tr, jobs.KIND_NAMES.values())), state="readonly", width=12)
        kind.pack(side="left")
        kind.bind("<<ComboboxSelected>>", lambda e: self.fill_review())
        self.v_flagged = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text=tr("Only songs to check"), variable=self.v_flagged, command=self.fill_review).pack(side="left", padx=12)
        ttk.Label(top, text=tr("Search: ")).pack(side="left")
        self.v_search = tk.StringVar()
        self.v_search.trace_add("write", lambda *a: self.fill_review())
        ttk.Entry(top, textvariable=self.v_search, width=24).pack(side="left", padx=(0, 12))
        self.review_count = ttk.Label(top, text="", style="Muted.TLabel")
        self.review_count.pack(side="left")

        bot = ttk.Frame(f)
        bot.pack(side="bottom", fill="x", pady=(self.px(10), 0))  # before the list, so a small window squeezes the list and not the write button
        pane = ttk.PanedWindow(f, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = self.card(pane)
        self.tree = self.make_tree(left, (("✓", 30, False), ("Artist", 100, True), ("Title", 140, True), ("Type", 100, False), ("Source", 130, False), ("Lines", 44, False), ("Action", 110, True)), "extended")
        self.tree.tag_configure("flag", background=WARN_BG)
        self.fill_with_scrollbar(self.tree)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<space>", self.toggle_selected)
        self.tree.bind("<<TreeviewSelect>>", self.show_preview)
        pane.add(left, weight=3)
        right = self.card(pane)
        self.preview = self.reading_pane(right)
        self.preview.tag_configure("gone", foreground=DANGER, overstrike=True)
        self.show_text(self.preview, (tr(REVIEW_HINT), "muted"))
        pane.add(right, weight=2)

        ttk.Button(bot, text=tr("Tick all"), command=lambda: self.check_view(True)).pack(side="left")
        ttk.Button(bot, text=tr("Untick all"), command=lambda: self.check_view(False)).pack(side="left", padx=6)
        ttk.Button(bot, text=tr("Remove selected"), command=self.dismiss_selected).pack(side="left", padx=6)
        ttk.Button(bot, text=tr("Export list"), command=self.export_review).pack(side="left", padx=6)
        self.btn_write = ttk.Button(bot, text=tr("Back up and write"), style="Accent.TButton", command=self.write)
        self.btn_write.pack(side="right")
        self.chk_backup_review = ttk.Checkbutton(bot, text=tr("Back up first"), variable=self.v_backup, command=self.on_backup_switch)
        self.chk_backup_review.pack(side="right", padx=self.px(12))

    def build_backup(self):
        f = self.tab_backup
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text=tr("Refresh"), command=self.fill_backups).pack(side="right")
        self.backup_label = ttk.Label(top, style="Muted.TLabel")
        self.backup_label.pack(side="left")
        bot = ttk.Frame(f)
        bot.pack(side="bottom", fill="x", pady=(self.px(10), 0))
        pane = ttk.PanedWindow(f, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = self.card(pane)
        self.batch_tree = self.make_tree(left, (("Batch", 240, True), ("Time", 140, False), ("Songs", 60, False)), "browse")
        self.fill_with_scrollbar(self.batch_tree)
        self.batch_tree.bind("<<TreeviewSelect>>", self.show_batch)
        pane.add(left, weight=2)
        right = self.card(pane)
        self.batch_files = self.make_tree(right, (("Files in this batch (select some to restore only those)", 200, True),), "extended")
        self.fill_with_scrollbar(self.batch_files)
        pane.add(right, weight=3)
        ttk.Label(bot, text=tr("Restoring puts lyrics and other tags back as they were before writing; files whose audio differs from the backup are skipped."), style="Muted.TLabel").pack(side="left")
        self.btn_restore = ttk.Button(bot, text=tr("Restore batch"), style="Accent.TButton", command=lambda: self.restore(False))
        self.btn_restore.pack(side="right")
        self.btn_restore_some = ttk.Button(bot, text=tr("Restore selected"), command=lambda: self.restore(True))
        self.btn_restore_some.pack(side="right", padx=8)

    def build_settings(self):
        f = self.tab_settings
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, self.px(14)))
        ttk.Label(top, text=tr("LyricsBox {version}   by {author}   built with help from Claude", version=__version__, author=__author__), style="Muted.TLabel").pack(side="left")
        ttk.Button(top, text=tr("Save settings"), style="Accent.TButton", command=self.save_settings).pack(side="right")
        ttk.Button(top, text=tr("Open log folder"), command=lambda: os.startfile(os.path.dirname(self.store.path))).pack(side="right", padx=self.px(8))
        self.v_lang = tk.StringVar(value=i18n.LANGUAGES[i18n.LANG])  # at the top, where a small window cannot squeeze it out of view
        lang = ttk.Combobox(top, textvariable=self.v_lang, values=list(i18n.LANGUAGES.values()), state="readonly", width=10)
        lang.pack(side="right", padx=(0, self.px(16)))
        lang.bind("<<ComboboxSelected>>", self.on_language)
        ttk.Label(top, text=tr("Language:")).pack(side="right", padx=(0, self.px(6)))

        cols = ttk.Frame(f)
        cols.pack(fill="both", expand=True)
        cols.rowconfigure(0, weight=1)
        cols.columnconfigure(0, weight=1, uniform="side")
        cols.columnconfigure(2, weight=1, uniform="side")
        left, right = ttk.Frame(cols), ttk.Frame(cols)
        left.grid(row=0, column=0, sticky="nsew")
        tk.Frame(cols, width=1, bg=LINE).grid(row=0, column=1, sticky="ns", padx=self.px(24))
        right.grid(row=0, column=2, sticky="nsew")

        left.columnconfigure(0, weight=1)
        self.v_paths = {}
        for i, (key, name, kind) in enumerate((("library", "Music folder", "dir"), ("backup_root", "Backup folder", "dir"),
                                               ("lrclib_db", "LRCLIB local database (optional; looked up online when not set)", "file"), ("lrcget_db", "LRCGET database (optional)", "file"))):
            ttk.Label(left, text=tr(name), style="Title.TLabel").grid(row=3 * i, column=0, columnspan=2, sticky="w", pady=(self.px(14) if i else 0, self.px(5)))
            v = tk.StringVar(value=self.cfg.get(key, ""))
            self.v_paths[key] = v
            ttk.Entry(left, textvariable=v).grid(row=3 * i + 1, column=0, sticky="ew")
            ttk.Button(left, text=tr("Browse"), command=lambda k=key, t=kind: self.pick(k, t)).grid(row=3 * i + 1, column=1, padx=(self.px(8), 0))
        self.chk_backup = ttk.Checkbutton(left, text=tr("Back up the original tags before writing"), variable=self.v_backup, command=self.on_backup_switch)
        self.chk_backup.grid(row=5, column=0, columnspan=2, sticky="w", pady=(self.px(5), 0))
        head = ttk.Frame(left)
        head.grid(row=12, column=0, columnspan=2, sticky="w", pady=(self.px(14), self.px(5)))
        ttk.Label(head, text=tr("Folder names to skip"), style="Title.TLabel").pack(side="left")
        ttk.Label(head, text=tr("e.g. Audiobooks, Podcast, downloading"), style="Muted.TLabel").pack(side="left", padx=(self.px(10), 0))
        self.v_excluded = tk.StringVar(value=", ".join(self.cfg["excluded"]))
        ttk.Entry(left, textvariable=self.v_excluded).grid(row=13, column=0, columnspan=2, sticky="ew")

        ttk.Label(right, text=tr("Lyrics sources"), style="Title.TLabel").pack(anchor="w", pady=(0, self.px(3)))
        row = ttk.Frame(right)
        row.pack(anchor="w")
        self.v_sources = {}
        for i, (key, name) in enumerate((("sidecar", ".lrc 外掛檔"), ("lrcget", "LRCGET"), ("lrclib", "LRCLIB"), ("netease", "網易雲"), ("qq", "QQ 音樂"), ("kugou", "酷狗"))):
            self.v_sources[key] = tk.BooleanVar(value=self.cfg["sources"].get(key, True))
            ttk.Checkbutton(row, text=stored_text(name), variable=self.v_sources[key]).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, self.px(22)))
        ttk.Label(right, text=tr("Lyrics processing"), style="Title.TLabel").pack(anchor="w", pady=(self.px(14), self.px(3)))
        self.v_tw = tk.BooleanVar(value=self.cfg["to_traditional"])
        ttk.Checkbutton(right, text=tr("Convert Chinese to Traditional (Taiwan), except lyrics with kana"), variable=self.v_tw).pack(anchor="w")
        self.v_credits = tk.BooleanVar(value=self.cfg["remove_credits"])
        ttk.Checkbutton(right, text=tr("Remove credits and other lines that are not lyrics"), variable=self.v_credits).pack(anchor="w")
        ttk.Label(right, text=tr("Composer, copyright, the title and artist at the top, intro cues"), style="Muted.TLabel").pack(anchor="w", padx=(self.px(24), 0))
        self.v_skip_albums = tk.BooleanVar(value=self.cfg["skip_albums"])
        ttk.Checkbutton(right, text=tr("Skip an album when its first {n} songs are all not found", n=jobs.ALBUM_SAMPLE), variable=self.v_skip_albums).pack(anchor="w")
        ttk.Label(right, text=tr("Instrumental counts as a miss; one hit in the first {n} keeps the whole album", n=jobs.ALBUM_SAMPLE), style="Muted.TLabel").pack(anchor="w", padx=(self.px(24), 0))

        row = ttk.Frame(right)
        row.pack(fill="x", pady=(self.px(14), self.px(5)))
        ttk.Label(row, text=tr("Artist aliases"), style="Title.TLabel").pack(side="left")
        ttk.Button(row, text=tr("Copy prompt for an AI"), command=self.copy_alias_prompt).pack(side="right")
        for line in ("One artist per line. Tags containing the left side are also searched as the right.", "e.g. lisa ono = 小野リサ, Lisa Ono, 小野丽莎, 小野麗莎, Ono Lisa", "       Ichiko Aoba = 青葉市子, 青叶市子, あおばいちこ"):
            ttk.Label(right, text=tr(line), style="Muted.TLabel").pack(anchor="w", pady=(0, self.px(6)))  # separate labels: a single multi-line one has no line spacing to set
        wrap = self.card(right)
        wrap.pack(fill="both", expand=True)
        self.alias_box = tk.Text(wrap, height=4, wrap="none", padx=8, pady=6, font=(FONT, 10), undo=True, **theme.TEXT)
        self.fill_with_scrollbar(self.alias_box)
        self.alias_box.insert("1.0", "\n".join(f"{k} = {', '.join(v)}" for k, v in self.cfg["aliases"].items()))

    # ---- background jobs ----
    def start(self, name, fn, done=None):
        if self.thread and self.thread.is_alive():
            dialogs.showinfo(tr("LyricsBox"), tr("A job is running. Wait for it to finish, or click Stop first."))
            return False
        ctx = jobs.Ctx(lambda kind, payload: self.events.put((kind, payload)))
        self.ctx, self.started, self.warned = ctx, time.monotonic(), set()
        self.set_running(True, name)

        def work():
            try:
                self.events.put(("done", (done, fn(ctx))))
            except ValueError as e:
                self.events.put(("failed", str(e)))
            except Exception as e:  # shown to the user and logged; the app keeps running
                log.exception("job %s failed", name)
                self.events.put(("failed", tr("{name} failed: {e}\nSee the log file for details.", name=name, e=e)))

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True

    def set_running(self, running, text=None):
        self.status.configure(text=text or tr("Ready"))
        self.btn_stop.configure(state="normal" if running else "disabled")
        state = "disabled" if running else "normal"
        for b in (self.btn_scan, self.btn_lookup, self.btn_restore, self.btn_restore_some):
            b.configure(state=state)
        self.update_write_button(running)
        if not running:
            self.bar.configure(value=0)
            self.eta.configure(text="")

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.write_log(*payload)
                elif kind == "status":
                    self.status.configure(text=payload)
                elif kind == "progress":
                    self.on_progress(*payload)
                elif kind == "limited":  # the dialog waits for an answer; opened from here it would stop the log and progress meanwhile
                    self.root.after_idle(self.warn_limited, payload)
                elif kind == "done":
                    self.set_running(False)
                    self.refresh_all()
                    if payload[0] and not self.closing:
                        payload[0](payload[1])
                    self.finish_close()
                elif kind == "failed":
                    self.set_running(False)
                    self.write_log(payload)
                    self.refresh_all()
                    if not self.closing:
                        dialogs.showerror(tr("LyricsBox"), payload)
                    self.finish_close()
                if self.closed:
                    return
        except queue.Empty:
            pass
        finally:  # an error in any handler above must not end the polling, or the window stops updating for good
            if not self.closed:
                self.root.after(100, self.poll)

    def on_progress(self, done, total, text):
        self.bar.configure(maximum=max(total, 1), value=done)
        self.status.configure(text=f"{done}/{total}  {text}"[:90])
        if 5 <= done < total:
            left = (time.monotonic() - self.started) / done * (total - done)
            self.eta.configure(text=tr("{n:.1f} h left", n=left / 3600) if left >= 3600 else tr("{n:.0f} min left", n=max(left / 60, 1)))

    def warn_limited(self, label):
        """Once per job and source: carrying on while the main sources refuse mostly piles up songs to ask again."""
        if label in self.warned or self.closing:
            return
        self.warned.add(label)
        text = tr("{label} keeps refusing lookups, probably after too many in a short time, and is paused for 10 minutes.\n\nThe other sources carry on, and songs from this time are asked again at the end. It is best to stop now and look up again later (in an hour, say).\n\nStop the lookup now?", label=stored_text(label))
        if dialogs.askyesno(tr("Source is limiting lookups"), text) and self.thread and self.thread.is_alive():
            self.stop()

    def stop(self):
        if self.ctx:
            self.ctx.cancel()
            self.status.configure(text=tr("Stopping (after the current song)…"))
            self.btn_stop.configure(state="disabled")

    def on_close(self):
        if self.thread and self.thread.is_alive():
            if not dialogs.askyesno(tr("LyricsBox"), tr("A job is running. Stop it and close?\nWhat is done so far is kept.")):
                return
            self.closing = True
            self.stop()
            self.root.after(35000, self.root.destroy)  # a request stuck on the network must not hold the window open forever
            return
        self.root.destroy()

    def finish_close(self):
        if self.closing:
            self.closed = True
            self.root.destroy()

    def on_ui_error(self, exc, val, tb):
        log.error("UI error", exc_info=(exc, val, tb))
        dialogs.showerror(tr("LyricsBox"), tr("Something went wrong: {e}\nSee the log file for details.", e=val))

    def write_log(self, text, tag=""):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S  ") + text + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ---- actions ----
    def scan(self):
        self.start(tr("Scan music folder"), lambda ctx: jobs.run_scan(self.store, self.cfg, ctx))

    def statuses(self):
        out = []
        if self.v_never.get():
            out += ["never", "busy"]
        if self.v_notfound.get():
            out += ["notfound", "dismissed"]
        if self.v_instr.get():
            out.append("instrumental")
        return out

    def count_targets(self):
        st = self.statuses()
        n = len(self.store.lookup_targets(st, self.v_filter.get().strip())) if st else 0
        self.target_label.configure(text=tr("Matches: {n}", n=n))

    def lookup(self):
        st = self.statuses()
        if not st:
            dialogs.showinfo(tr("LyricsBox"), tr("Tick at least one kind of song to look up."))
            return
        text = self.v_filter.get().strip()
        self.start(tr("Look Up Lyrics"), lambda ctx: jobs.run_lookup(self.store, self.cfg, ctx, st, text), self.after_lookup)

    def after_lookup(self, counts):
        if counts.get("found"):
            self.nb.select(self.tab_review)

    def write(self):
        items = self.store.selected_items()
        if not items:
            dialogs.showinfo(tr("LyricsBox"), tr("No songs are ticked for writing."))
            return
        flagged = sum(1 for i in items if json.loads(i["flags"]))
        on = self.cfg["backup_enabled"]
        msg = tr("Write lyrics to the ticked songs ({n})?\n\n", n=len(items)) + (tr("The original tags will be backed up to:\n{folder}", folder=os.path.abspath(self.cfg['backup_root'])) if on else tr("Backups are off; this cannot be undone."))
        if flagged:
            msg += tr("\n\nSongs to check among them: {n}.", n=flagged)
        if dialogs.askyesno(tr("Back up and write" if on else "Write"), msg):
            self.start(tr("Write lyrics"), lambda ctx: jobs.run_write(self.store, self.cfg, ctx), self.after_write)

    def after_write(self, r):
        text = tr("Written: {n}. Lyrics and audio all passed verification.", n=r['ok'])
        if r["failed"] or r["stale"]:
            text += tr("\nFailed: {failed}. Skipped because the file changed: {stale}. None of these files were touched. Details are in the log on the Look Up Lyrics tab.", failed=len(r['failed']), stale=len(r['stale']))
        dialogs.showinfo(tr("Write complete"), text)
        self.fill_backups()

    def restore(self, only_selected):
        sel = self.batch_tree.selection()
        if not sel:
            dialogs.showinfo(tr("LyricsBox"), tr("Select a backup batch first."))
            return
        folder = sel[0]
        paths = None
        if only_selected:
            paths = set(self.batch_files.selection())
            if not paths:
                dialogs.showinfo(tr("LyricsBox"), tr("Select the files to restore on the right first."))
                return
        n = len(paths) if paths else len(self.batch_files.get_children())
        if dialogs.askyesno(tr("Restore"), tr("Restore these songs ({n}) to how they were before this batch was written?", n=n)):
            self.start(tr("Restore"), lambda ctx: jobs.run_restore(self.store, self.cfg, ctx, folder, paths),
                       lambda r: dialogs.showinfo(tr("Restore complete"), tr("Restored: {done}. Skipped: {skipped}.", done=r['done'], skipped=len(r['skipped']))))

    def copy_alias_prompt(self):
        text, n = jobs.alias_prompt(self.store.artists(), parse_aliases(self.alias_box.get("1.0", "end")))
        if not n:
            dialogs.showinfo(tr("LyricsBox"), tr("Every artist in the library already has aliases."))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        dialogs.showinfo(tr("LyricsBox"), tr("Prompt copied. It lists the artists without aliases ({n}).\n\nPaste it into the AI you use (ChatGPT or Claude, say), paste each line of its reply into Artist aliases below, then click Save settings.", n=n))

    def save_settings(self):
        if self.thread and self.thread.is_alive():
            dialogs.showinfo(tr("LyricsBox"), tr("A job is running. Wait for it to finish, or click Stop, before saving settings."))
            return
        aliases = parse_aliases(self.alias_box.get("1.0", "end"))
        new = dict(self.cfg)
        new.update({k: v.get().strip() for k, v in self.v_paths.items()})
        new["excluded"] = split_names(self.v_excluded.get())
        new["sources"] = {k: v.get() for k, v in self.v_sources.items()}
        new["to_traditional"] = self.v_tw.get()
        new["remove_credits"] = self.v_credits.get()
        new["skip_albums"] = self.v_skip_albums.get()
        new["aliases"] = aliases
        if not os.path.isdir(new["library"]):
            dialogs.showerror(tr("LyricsBox"), tr("Music folder not found."))
            return
        if new["backup_enabled"] and not new["backup_root"]:
            dialogs.showerror(tr("LyricsBox"), tr("Set a backup folder."))
            return
        changed = new["library"] != self.cfg["library"] or new["excluded"] != self.cfg["excluded"]
        rescan = changed or new["remove_credits"] != self.cfg["remove_credits"]  # a scan also adds or withdraws the cleanup suggestions
        self.cfg.clear()
        self.cfg.update(new)
        save_config(self.cfg)
        self.refresh_all()
        if changed:
            self.store.db.execute("delete from tracks")
            self.store.commit()
        if rescan:
            self.scan()
        else:
            dialogs.showinfo(tr("LyricsBox"), tr("Settings saved."))

    def on_backup_switch(self):
        """Takes effect at once, unlike the rest of the settings page: beside the write button, a tick has to mean what it shows."""
        if not self.v_backup.get() and not dialogs.confirm_risk(tr("Turn off backups"), tr(BACKUP_RISK), tr("Turn off backups")):
            self.v_backup.set(True)
            return
        self.cfg["backup_enabled"] = self.v_backup.get()
        save_config(self.cfg)
        self.update_write_button()

    def on_language(self, e=None):
        """Saved at once, like the backup tick; the window shows the new language from the next start."""
        lang = next(k for k, name in i18n.LANGUAGES.items() if name == self.v_lang.get())
        if lang != self.cfg["language"]:
            self.cfg["language"] = lang
            save_config(self.cfg)
            dialogs.showinfo(tr("LyricsBox"), tr("The new language is used from the next time LyricsBox starts."))

    def pick(self, key, kind):
        p = filedialog.askdirectory() if kind == "dir" else filedialog.askopenfilename()
        if p:
            self.v_paths[key].set(os.path.normpath(p))

    def export_missing(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=tr("songs without synced lyrics.csv"), filetypes=[("CSV", "*.csv")])
        if p:
            jobs.export_missing(self.store.missing_rows(), p)

    def export_review(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=tr("review list.csv"), filetypes=[("CSV", "*.csv")])
        if p:
            jobs.export_review(self.store.pending_items(), p)

    # ---- review list ----
    def visible_rows(self):
        kind = {tr(name): k for k, name in jobs.KIND_NAMES.items()}.get(self.v_kind.get())
        needle = self.v_search.get().strip().lower()
        out = []
        for r in self.review_rows:
            if kind and r["kind"] != kind:
                continue
            if self.v_flagged.get() and not json.loads(r["flags"]):
                continue
            if needle and needle not in f"{r['artist']} {r['title']} {r['album']}".lower():
                continue
            out.append(r)
        return out

    def fill_review(self):
        self.review_rows = self.store.pending_items()
        keep = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for r in self.visible_rows():
            iid = str(r["id"])
            self.tree.insert("", "end", iid=iid, values=("☑" if r["selected"] else "☐", r["artist"], r["title"], tr(jobs.KIND_NAMES[r["kind"]]), stored_text(r["source"]), r["lines"], jobs.action_text(r)),
                             tags=("flag",) if json.loads(r["flags"]) else ())
            if iid in keep:
                self.tree.selection_add(iid)
        if not self.tree.selection():  # the song it was showing may be gone from the list
            self.show_text(self.preview, (tr(REVIEW_HINT), "muted"))
        self.update_write_button()

    def update_write_button(self, running=None):
        running = self.thread is not None and self.thread.is_alive() if running is None else running
        n = sum(1 for r in self.review_rows if r["selected"])
        self.btn_write.configure(text=tr("Back up and write ({n})" if self.cfg['backup_enabled'] else "Write ({n})", n=n), state="normal" if n and not running else "disabled")
        self.review_count.configure(text=tr("{total} in list, {n} ticked", total=len(self.review_rows), n=n))
        self.nb.tab(self.tab_review, text=tr("Review & Write ({n})", n=len(self.review_rows)) if self.review_rows else tr("Review & Write"))

    def on_tree_click(self, e):
        if self.tree.identify_region(e.x, e.y) == "cell" and self.tree.identify_column(e.x) == "#1":
            iid = self.tree.identify_row(e.y)
            if iid:
                self.flip([iid])

    def toggle_selected(self, e):
        self.flip(list(self.tree.selection()))
        return "break"

    def flip(self, iids):
        if iids:
            on = self.tree.set(iids[0], "✓") == "☐"
            self.store.set_selected([int(i) for i in iids], on)
            for i in iids:
                self.tree.set(i, "✓", "☑" if on else "☐")
            self.review_rows = self.store.pending_items()
            self.update_write_button()

    def check_view(self, on):
        self.store.set_selected([r["id"] for r in self.visible_rows()], on)
        self.fill_review()

    def dismiss_selected(self):
        rows = {str(r["id"]): r for r in self.review_rows}
        sel = self.tree.selection()
        for i in sel:
            self.store.set_item_state(int(i), "dismissed")
            if rows[str(i)]["kind"] == "new":
                self.store.save_lookup(rows[str(i)]["path"], "dismissed")
        if sel:
            self.refresh_all()

    def show_preview(self, e=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = next((x for x in self.review_rows if str(x["id"]) == sel[0]), None)
        if r is None:
            return
        parts = [(f"{r['artist']} - {r['title']}\n", "head"), (f"{r['album']}\n", "muted")]
        if r["kind"] == "new":
            parts.append((tr("Source: {source}\nMatched: {matched} ({found:.1f} s; the file is {length:.1f} s)\n", source=stored_text(r['source']), matched=r['matched'],
                             found=r['matched_length'], length=r['length']), "muted"))
        parts += [(tr("Check: {flag}\n", flag=jobs.flag_text(flag)), "") for flag in json.loads(r["flags"])]
        removed = json.loads(r["removed"])
        if removed:
            parts += [(tr("\nTo be removed:\n"), "muted")] + [(line + "\n", "gone") for line in removed]
        parts += [(tr("\nLyrics after writing:\n"), "muted"), (r["lrc"], "")]
        self.show_text(self.preview, *parts)

    # ---- backups ----
    def fill_backups(self):
        self.backup_label.configure(text=tr("Backup folder: {folder}", folder=self.cfg['backup_root']) + ("" if self.cfg["backup_enabled"] else tr("   Backups are off; songs written from now on will not be listed here")))
        keep = self.batch_tree.selection()  # a refresh after restoring must not lose the batch being worked on
        self.batch_tree.delete(*self.batch_tree.get_children())
        self.batch_files.delete(*self.batch_files.get_children())
        if os.path.isdir(self.cfg["backup_root"]):
            for b in jobs.list_backups(self.cfg["backup_root"]):
                self.batch_tree.insert("", "end", iid=b["folder"], values=(b["name"], time.strftime("%Y-%m-%d %H:%M", time.localtime(b["at"])), b["count"]))
        if keep and self.batch_tree.exists(keep[0]):
            self.batch_tree.selection_set(keep[0])
            self.show_batch()

    def show_batch(self, e=None):
        self.batch_files.delete(*self.batch_files.get_children())
        sel = self.batch_tree.selection()
        if sel:
            for p, r in sorted(audio.read_index(sel[0]).items()):
                if r.get("result") in ("ok", "pending"):
                    self.batch_files.insert("", "end", iid=p, values=(p,))

    # ---- what's behind the overview cards ----
    def open_card(self, key):
        """Clicking a card lists its songs; clicking it again, or 返回, goes back."""
        self.open_state = None if key == self.open_state else key
        for k, frame in self.card_frames.items():
            frame.state(["selected" if k == self.open_state else "!selected"])
        (self.lists if self.open_state else self.card_view).pack_forget()
        (self.card_view if self.open_state else self.lists).pack(fill="both", expand=True)
        # Having lyrics, what an old lookup once said no longer matters. Widths start over, or columns stretched to fill without 狀況 push it out of view when it returns.
        self.card_tree.configure(displaycolumns=[c[0] for c in CARD_COLUMNS if key != "synced" or c[0] != "Status"])
        for name, width, _ in CARD_COLUMNS:
            self.card_tree.column(name, width=self.px(width))
        self.v_card_search.set("")  # its trace fills the list

    def fill_card_list(self):
        key = self.open_state
        if not key:
            return
        needle = self.v_card_search.get().strip().lower()
        keep = self.card_tree.selection()
        self.card_tree.delete(*self.card_tree.get_children())
        self.card_rows = {}
        for r in self.store.tracks_in(CARDS[key][1]):
            artist, title = track_names(r)
            if needle in f"{artist} {title} {r['album']} {r['path']}".lower():
                self.card_rows[r["path"]] = r
                self.card_tree.insert("", "end", iid=r["path"], values=(artist, title, tr(WHY[r["why"]]), r["album"], r["ext"].lstrip(".").upper(), r["quality"] or ""))
        self.card_title.configure(text=tr("{name}   {n:,}", name=tr(CARDS[key][0]), n=len(self.card_rows)))
        if keep and self.card_tree.exists(keep[0]):
            self.card_tree.selection_set(keep[0])
        else:
            self.show_track()

    def show_track(self, e=None):
        sel = self.card_tree.selection()
        r = self.card_rows.get(sel[0]) if sel else None
        t = self.store.track(r["path"]) if r else None
        if t is None:
            self.show_text(self.card_detail, (tr("Select a song on the left to see its details and lyrics."), "muted"))
            return
        artist, title = track_names(r)
        facts = [x for x in (r["ext"].lstrip(".").upper(), r["quality"], f"{r['size'] / 1e6:.1f} MB") if x]
        if r["length"]:
            facts.insert(0, f"{int(r['length']) // 60}:{int(r['length']) % 60:02d}")
        parts = [(f"{artist} - {title}\n" if artist else f"{title}\n", "head"), (f"{r['album']}\n" if r["album"] else "", "muted"), (tr("   ").join(facts) + f"\n{r['path']}\n", "muted")]
        if r["state"] != "synced":
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])) if r["at"] else ""
            extra = tr(" · ").join(x for x in (stored_text(r["source"] or ""), when) if x)
            parts.append((tr("\nStatus: {why}", why=tr(WHY[r['why']])) + (tr(" ({extra})", extra=extra) if extra else "") + "\n", ""))
        if r["error"]:
            parts.append((tr("Error: {error}\n", error=r['error']), ""))
        if r["state"] not in ("unsupported", "unreadable"):
            parts += [(tr("\nEmbedded lyrics:\n"), "muted"), (t["lyrics"] or tr("(none)"), "")]
        self.show_text(self.card_detail, *parts)

    # ---- overview ----
    def refresh_all(self):
        self.lib_label.configure(text=tr("Music folder: {folder}", folder=self.cfg.get('library') or tr("not set; choose one on the Settings tab")))
        c = self.store.counts()
        vals = {"synced": c.get("synced", 0), "plain": c.get("plain", 0), "none": c.get("none", 0), "other": c.get("unsupported", 0) + c.get("unreadable", 0)}
        total = sum(vals.values())
        for k, v in vals.items():
            pct = f"  {v / total:.0%}" if total and k != "other" else ""
            self.card_vals[k].configure(text=f"{v:,}{pct}")
        self.stack_counts = vals
        self.draw_stack()
        self.reason_tree.delete(*self.reason_tree.get_children())
        for k, v in sorted(self.store.reasons().items(), key=lambda x: -x[1]):
            self.reason_tree.insert("", "end", values=(tr(WHY[k]), f"{v:,}"))
        self.artist_tree.delete(*self.artist_tree.get_children())
        for r in self.store.top_missing():
            self.artist_tree.insert("", "end", values=(r["artist"], r["n"]))
        self.fill_card_list()
        self.count_targets()
        self.fill_review()
        self.on_tab()

    def draw_stack(self):
        c = self.stack
        c.delete("all")
        total = sum(self.stack_counts.values())
        if not total:
            return
        w, x = c.winfo_width(), 0.0
        for k in ("synced", "plain", "none", "other"):
            dx = w * self.stack_counts.get(k, 0) / total
            if dx > 0:
                c.create_rectangle(x, 0, x + dx, 16, fill=STATE_COLORS[k], width=0)
            x += dx

    def on_tab(self, e=None):
        if self.nb.select() == str(self.tab_backup):
            self.fill_backups()
