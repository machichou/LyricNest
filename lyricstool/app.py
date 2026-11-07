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

from . import __author__, __version__, audio, dialogs, jobs, theme
from .store import save_config
from .theme import BG, DANGER, FONT, LINE, MUTED, STATE_COLORS, WARN_BG

log = logging.getLogger("lyricstool")
WHY = {"never": "尚未查詢", "found": "已找到，待審閱", "notfound": "查無歌詞", "instrumental": "來源標示為純音樂或無歌詞", "dismissed": "已從清單移除",
       "busy": "來源忙碌，待重新查詢", "unsupported": "不支援寫入此格式", "unreadable": "檔案損壞或無法讀取"}
CARDS = {"synced": ("同步歌詞（動態顯示）", ("synced",)), "plain": ("僅有純文字歌詞", ("plain",)), "none": ("沒有歌詞", ("none",)), "other": ("不支援或已損壞", ("unsupported", "unreadable"))}
CARD_COLUMNS = (("歌手", 100, True), ("歌名", 150, True), ("狀況", 170, False), ("專輯", 100, True), ("格式", 50, False), ("音質", 100, False))


REVIEW_HINT = "從左側選取歌曲，即可查看來源、需留意事項、將刪除的內容與完整歌詞。\n\n點選 ☑ 或按空白鍵可切換勾選。"
BACKUP_RISK = """關閉備份後，寫入歌詞前將不會保存原本的標籤：

• 寫入錯誤的歌詞時，無法透過「備份還原」復原，只能手動修正。
• 被取代的純文字歌詞與刪除的製作名單將無法復原。

音訊不受影響，寫入前後的檢查也會照常進行。
現有的備份不會受到影響。"""


def split_names(text):
    """Commas in any of the forms an input method gives."""
    return [x.strip() for x in re.split("[,，、]", text) if x.strip()]


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
        root.title(f"歌詞工具 {__version__}")
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
        ttk.Label(self.root, text="歌詞工具", style="Heading.TLabel").pack(anchor="w", padx=self.px(24), pady=(self.px(16), self.px(10)))
        bar = ttk.Frame(self.root, padding=(self.px(24), self.px(10)))
        bar.pack(side="bottom", fill="x")  # before the tabs, so a small window squeezes them and not this
        tk.Frame(self.root, height=1, bg=LINE).pack(side="bottom", fill="x")
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=self.px(24), pady=(0, self.px(12)))
        self.tab_overview = self.tab("總覽")
        self.tab_lookup = self.tab("查詢歌詞")
        self.tab_review = self.tab("審閱與寫入")
        self.tab_backup = self.tab("備份還原")
        self.tab_settings = self.tab("設定")
        self.build_overview()
        self.build_lookup()
        self.build_review()
        self.build_backup()
        self.build_settings()
        self.nb.bind("<<NotebookTabChanged>>", self.on_tab)

        self.status = ttk.Label(bar, text="待命中", width=60, anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        self.eta = ttk.Label(bar, text="", style="Muted.TLabel")
        self.eta.pack(side="left", padx=10)
        self.bar = ttk.Progressbar(bar, length=260, maximum=1)
        self.bar.pack(side="left", padx=10)
        self.btn_stop = ttk.Button(bar, text="停止", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left")

    def tab(self, name):
        f = ttk.Frame(self.nb, padding=(0, self.px(14), 0, 0))
        self.nb.add(f, text=name)
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
        self.btn_scan = ttk.Button(top, text="重新掃描", command=self.scan)
        self.btn_scan.pack(side="right")
        ttk.Button(top, text="匯出缺少同步歌詞的歌曲", command=self.export_missing).pack(side="right", padx=8)
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
            label = ttk.Label(c, text=name, style="CardName.TLabel", cursor="hand2")
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
        ttk.Label(lists, text="缺少同步歌詞的原因", style="Title.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 6))
        ttk.Label(lists, text="查無歌詞最多的歌手", style="Title.TLabel").grid(row=0, column=1, sticky="w", padx=(12, 0), pady=(0, 6))
        self.reason_tree = self.simple_tree(lists, "原因", 0)
        self.artist_tree = self.simple_tree(lists, "歌手", 1, padx=(12, 0))
        self.build_card_list(f)

    def build_card_list(self, f):
        """What takes the place of the two lists above when a card is clicked: its songs on the left, the selected song's details on the right."""
        self.card_view = ttk.Frame(f)
        top = ttk.Frame(self.card_view)
        top.pack(fill="x", pady=(0, self.px(8)))
        self.card_title = ttk.Label(top, style="Title.TLabel")
        self.card_title.pack(side="left")
        ttk.Button(top, text="返回", command=lambda: self.open_card(self.open_state)).pack(side="right")
        self.v_card_search = tk.StringVar()
        self.v_card_search.trace_add("write", lambda *a: self.fill_card_list())
        ttk.Entry(top, textvariable=self.v_card_search, width=24).pack(side="right", padx=self.px(8))
        ttk.Label(top, text="搜尋：").pack(side="right")
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
            t.heading(name, text=name, anchor="w")
            t.column(name, width=self.px(width), anchor="w", stretch=stretch)
        return t

    def simple_tree(self, parent, first_column, col, padx=0):
        wrap = self.card(parent)
        wrap.grid(row=1, column=col, sticky="nsew", padx=padx)
        t = self.make_tree(wrap, ((first_column, 300, True), ("首數", 70, False)), "none")
        t.pack(fill="both", expand=True)
        return t

    def build_lookup(self):
        f = self.tab_lookup
        ttk.Label(f, text="查詢範圍", style="Title.TLabel").pack(anchor="w")
        opts = ttk.Frame(f)
        opts.pack(fill="x", pady=(6, 4))
        self.v_never = tk.BooleanVar(value=True)
        self.v_notfound = tk.BooleanVar(value=False)
        self.v_instr = tk.BooleanVar(value=False)
        for var, text in ((self.v_never, "尚未查詢（含先前來源忙碌的歌曲）"), (self.v_notfound, "先前查無歌詞（含已從清單移除的歌曲）"), (self.v_instr, "標示為純音樂的歌曲")):
            ttk.Checkbutton(opts, text=text, variable=var, command=self.count_targets).pack(side="left", padx=(0, 18))
        row = ttk.Frame(f)
        row.pack(fill="x", pady=(2, 8))
        ttk.Label(row, text="僅查詢歌手、專輯、歌名或路徑包含：").pack(side="left")
        self.v_filter = tk.StringVar()
        self.v_filter.trace_add("write", lambda *a: self.count_targets())
        ttk.Entry(row, textvariable=self.v_filter, width=30).pack(side="left", padx=6)
        self.target_label = ttk.Label(row, text="", style="Muted.TLabel")
        self.target_label.pack(side="left", padx=8)
        self.btn_lookup = ttk.Button(row, text="開始查詢", style="Accent.TButton", command=self.lookup)
        self.btn_lookup.pack(side="right")
        ttk.Label(f, text="查詢結果會先列入「審閱與寫入」，不會直接寫入檔案。中途停止或關閉程式不會遺失進度，下次會接續查詢。", style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        wrap = self.card(f)
        wrap.pack(fill="both", expand=True)
        self.log_box = tk.Text(wrap, wrap="word", state="disabled", padx=10, pady=8, font=(FONT, 10), **theme.TEXT)
        self.fill_with_scrollbar(self.log_box)

    def build_review(self):
        f = self.tab_review
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        self.v_kind = tk.StringVar(value="全部")
        kind = ttk.Combobox(top, textvariable=self.v_kind, values=("全部", *jobs.KIND_NAMES.values()), state="readonly", width=12)
        kind.pack(side="left")
        kind.bind("<<ComboboxSelected>>", lambda e: self.fill_review())
        self.v_flagged = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="只顯示需留意的歌曲", variable=self.v_flagged, command=self.fill_review).pack(side="left", padx=12)
        ttk.Label(top, text="搜尋：").pack(side="left")
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
        self.tree = self.make_tree(left, (("✓", 30, False), ("歌手", 100, True), ("歌名", 140, True), ("行為類型", 100, False), ("來源", 130, False), ("行數", 44, False), ("行為", 110, True)), "extended")
        self.tree.tag_configure("flag", background=WARN_BG)
        self.fill_with_scrollbar(self.tree)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<space>", self.toggle_selected)
        self.tree.bind("<<TreeviewSelect>>", self.show_preview)
        pane.add(left, weight=3)
        right = self.card(pane)
        self.preview = self.reading_pane(right)
        self.preview.tag_configure("gone", foreground=DANGER, overstrike=True)
        self.show_text(self.preview, (REVIEW_HINT, "muted"))
        pane.add(right, weight=2)

        ttk.Button(bot, text="全部勾選", command=lambda: self.check_view(True)).pack(side="left")
        ttk.Button(bot, text="全部取消", command=lambda: self.check_view(False)).pack(side="left", padx=6)
        ttk.Button(bot, text="移除選取的歌曲", command=self.dismiss_selected).pack(side="left", padx=6)
        ttk.Button(bot, text="匯出清單", command=self.export_review).pack(side="left", padx=6)
        self.btn_write = ttk.Button(bot, text="備份並寫入", style="Accent.TButton", command=self.write)
        self.btn_write.pack(side="right")
        self.chk_backup_review = ttk.Checkbutton(bot, text="寫入前先備份", variable=self.v_backup, command=self.on_backup_switch)
        self.chk_backup_review.pack(side="right", padx=self.px(12))

    def build_backup(self):
        f = self.tab_backup
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, 8))
        ttk.Button(top, text="重新整理", command=self.fill_backups).pack(side="right")
        self.backup_label = ttk.Label(top, style="Muted.TLabel")
        self.backup_label.pack(side="left")
        bot = ttk.Frame(f)
        bot.pack(side="bottom", fill="x", pady=(self.px(10), 0))
        pane = ttk.PanedWindow(f, orient="horizontal")
        pane.pack(fill="both", expand=True)
        left = self.card(pane)
        self.batch_tree = self.make_tree(left, (("批次", 240, True), ("時間", 140, False), ("首數", 60, False)), "browse")
        self.fill_with_scrollbar(self.batch_tree)
        self.batch_tree.bind("<<TreeviewSelect>>", self.show_batch)
        pane.add(left, weight=2)
        right = self.card(pane)
        self.batch_files = self.make_tree(right, (("此批次的檔案（可多選，只還原選取的項目）", 200, True),), "extended")
        self.fill_with_scrollbar(self.batch_files)
        pane.add(right, weight=3)
        ttk.Label(bot, text="還原會將歌詞等標籤恢復為寫入前的狀態；音訊與備份時不同的檔案會略過。", style="Muted.TLabel").pack(side="left")
        self.btn_restore = ttk.Button(bot, text="還原整批", style="Accent.TButton", command=lambda: self.restore(False))
        self.btn_restore.pack(side="right")
        self.btn_restore_some = ttk.Button(bot, text="還原選取項目", command=lambda: self.restore(True))
        self.btn_restore_some.pack(side="right", padx=8)

    def build_settings(self):
        f = self.tab_settings
        top = ttk.Frame(f)
        top.pack(fill="x", pady=(0, self.px(14)))
        ttk.Label(top, text=f"歌詞工具 {__version__}　作者：{__author__}　由 Claude 協助開發", style="Muted.TLabel").pack(side="left")
        ttk.Button(top, text="儲存設定", style="Accent.TButton", command=self.save_settings).pack(side="right")
        ttk.Button(top, text="開啟記錄檔資料夾", command=lambda: os.startfile(os.path.dirname(self.store.path))).pack(side="right", padx=self.px(8))

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
        for i, (key, name, kind) in enumerate((("library", "音樂資料夾", "dir"), ("backup_root", "備份資料夾", "dir"),
                                               ("lrclib_db", "LRCLIB 本機資料庫（選填，未設定時查詢線上資料）", "file"), ("lrcget_db", "LRCGET 資料庫（選填）", "file"))):
            ttk.Label(left, text=name, style="Title.TLabel").grid(row=3 * i, column=0, columnspan=2, sticky="w", pady=(self.px(14) if i else 0, self.px(5)))
            v = tk.StringVar(value=self.cfg.get(key, ""))
            self.v_paths[key] = v
            ttk.Entry(left, textvariable=v).grid(row=3 * i + 1, column=0, sticky="ew")
            ttk.Button(left, text="瀏覽", command=lambda k=key, t=kind: self.pick(k, t)).grid(row=3 * i + 1, column=1, padx=(self.px(8), 0))
        self.chk_backup = ttk.Checkbutton(left, text="寫入前先備份原本的標籤", variable=self.v_backup, command=self.on_backup_switch)
        self.chk_backup.grid(row=5, column=0, columnspan=2, sticky="w", pady=(self.px(5), 0))
        head = ttk.Frame(left)
        head.grid(row=12, column=0, columnspan=2, sticky="w", pady=(self.px(14), self.px(5)))
        ttk.Label(head, text="略過的資料夾名稱", style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="例如：有聲書, Podcast, downloading", style="Muted.TLabel").pack(side="left", padx=(self.px(10), 0))
        self.v_excluded = tk.StringVar(value=", ".join(self.cfg["excluded"]))
        ttk.Entry(left, textvariable=self.v_excluded).grid(row=13, column=0, columnspan=2, sticky="ew")

        ttk.Label(right, text="歌詞來源", style="Title.TLabel").pack(anchor="w", pady=(0, self.px(3)))
        row = ttk.Frame(right)
        row.pack(anchor="w")
        self.v_sources = {}
        for i, (key, name) in enumerate((("sidecar", ".lrc 外掛檔"), ("lrcget", "LRCGET"), ("lrclib", "LRCLIB"), ("netease", "網易雲"), ("qq", "QQ 音樂"), ("kugou", "酷狗"))):
            self.v_sources[key] = tk.BooleanVar(value=self.cfg["sources"].get(key, True))
            ttk.Checkbutton(row, text=name, variable=self.v_sources[key]).grid(row=i // 3, column=i % 3, sticky="w", padx=(0, self.px(22)))
        ttk.Label(right, text="歌詞處理", style="Title.TLabel").pack(anchor="w", pady=(self.px(14), self.px(3)))
        self.v_tw = tk.BooleanVar(value=self.cfg["to_traditional"])
        ttk.Checkbutton(right, text="轉換為台灣繁體（含假名的歌詞除外）", variable=self.v_tw).pack(anchor="w")
        self.v_credits = tk.BooleanVar(value=self.cfg["remove_credits"])
        ttk.Checkbutton(right, text="移除製作名單等非歌詞內容", variable=self.v_credits).pack(anchor="w")
        ttk.Label(right, text="包括作曲、版權、開頭的歌名與歌手、前奏標示", style="Muted.TLabel").pack(anchor="w", padx=(self.px(24), 0))
        self.v_skip_albums = tk.BooleanVar(value=self.cfg["skip_albums"])
        ttk.Checkbutton(right, text=f"專輯前 {jobs.ALBUM_SAMPLE} 首都找不到時，略過整張專輯", variable=self.v_skip_albums).pack(anchor="w")
        ttk.Label(right, text=f"純音樂也算找不到；前 {jobs.ALBUM_SAMPLE} 首有任何一首找到，整張都會查；略過的歌記成查無歌詞", style="Muted.TLabel").pack(anchor="w", padx=(self.px(24), 0))

        ttk.Label(right, text="歌手別名", style="Title.TLabel").pack(anchor="w", pady=(self.px(14), self.px(5)))
        for line in ("歌手標籤包含等號左側的文字時，也會用右側的名稱查詢。每行一位歌手。", "例如：lisa ono = 小野リサ, Lisa Ono, 小野丽莎, 小野麗莎, Ono Lisa", "　　　Ichiko Aoba = 青葉市子, 青叶市子, あおばいちこ"):
            ttk.Label(right, text=line, style="Muted.TLabel").pack(anchor="w", pady=(0, self.px(6)))  # separate labels: a single multi-line one has no line spacing to set
        wrap = self.card(right)
        wrap.pack(fill="both", expand=True)
        self.alias_box = tk.Text(wrap, height=4, wrap="none", padx=8, pady=6, font=(FONT, 10), undo=True, **theme.TEXT)
        self.fill_with_scrollbar(self.alias_box)
        self.alias_box.insert("1.0", "\n".join(f"{k} = {', '.join(v)}" for k, v in self.cfg["aliases"].items()))

    # ---- background jobs ----
    def start(self, name, fn, done=None):
        if self.thread and self.thread.is_alive():
            dialogs.showinfo("歌詞工具", "目前有工作正在進行，請等待完成或先按「停止」。")
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
                self.events.put(("failed", f"{name}時發生錯誤：{e}\n詳細資訊請見記錄檔。"))

        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        return True

    def set_running(self, running, text="待命中"):
        self.status.configure(text=text)
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
                    self.write_log(payload)
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
                        dialogs.showerror("歌詞工具", payload)
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
            self.eta.configure(text=f"剩餘 {left / 3600:.1f} 小時" if left >= 3600 else f"剩餘 {max(left / 60, 1):.0f} 分鐘")

    def warn_limited(self, label):
        """Once per job and source: carrying on while the main sources refuse mostly piles up songs to ask again."""
        if label in self.warned or self.closing:
            return
        self.warned.add(label)
        text = (f"{label} 一直拒絕查詢，可能是短時間內查太多次，已暫停使用 10 分鐘。\n\n"
                "其他來源會繼續查，這段期間的歌會留到最後再補問。建議先停止，過一段時間（例如一小時）再查。\n\n要現在停止查詢嗎？")
        if dialogs.askyesno("來源被限流", text) and self.thread and self.thread.is_alive():
            self.stop()

    def stop(self):
        if self.ctx:
            self.ctx.cancel()
            self.status.configure(text="正在停止（完成目前這首後結束）…")
            self.btn_stop.configure(state="disabled")

    def on_close(self):
        if self.thread and self.thread.is_alive():
            if not dialogs.askyesno("歌詞工具", "目前有工作正在進行，要停止並關閉程式嗎？\n已完成的部分會保留。"):
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
        dialogs.showerror("歌詞工具", f"發生錯誤：{val}\n詳細資訊請見記錄檔。")

    def write_log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S  ") + text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    # ---- actions ----
    def scan(self):
        self.start("掃描音樂資料夾", lambda ctx: jobs.run_scan(self.store, self.cfg, ctx))

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
        self.target_label.configure(text=f"符合 {n} 首")

    def lookup(self):
        st = self.statuses()
        if not st:
            dialogs.showinfo("歌詞工具", "請至少勾選一種查詢範圍。")
            return
        text = self.v_filter.get().strip()
        self.start("查詢歌詞", lambda ctx: jobs.run_lookup(self.store, self.cfg, ctx, st, text), self.after_lookup)

    def after_lookup(self, counts):
        if counts.get("found"):
            self.nb.select(self.tab_review)

    def write(self):
        items = self.store.selected_items()
        if not items:
            dialogs.showinfo("歌詞工具", "尚未勾選要寫入的歌曲。")
            return
        flagged = sum(1 for i in items if json.loads(i["flags"]))
        on = self.cfg["backup_enabled"]
        msg = f"確定要寫入 {len(items)} 首歌曲嗎？\n\n" + (f"原本的標籤會先備份至：\n{os.path.abspath(self.cfg['backup_root'])}" if on else "備份已關閉，寫入後無法還原。")
        if flagged:
            msg += f"\n\n其中 {flagged} 首需要留意。"
        if dialogs.askyesno("備份並寫入" if on else "寫入", msg):
            self.start("寫入歌詞", lambda ctx: jobs.run_write(self.store, self.cfg, ctx), self.after_write)

    def after_write(self, r):
        text = f"已成功寫入 {r['ok']} 首，歌詞與音訊皆通過驗證。"
        if r["failed"] or r["stale"]:
            text += f"\n{len(r['failed'])} 首寫入失敗，{len(r['stale'])} 首因檔案已變動而略過，這些檔案都沒有被更動。詳細資訊請見「查詢歌詞」分頁的紀錄。"
        dialogs.showinfo("寫入完成", text)
        self.fill_backups()

    def restore(self, only_selected):
        sel = self.batch_tree.selection()
        if not sel:
            dialogs.showinfo("歌詞工具", "請先選取一個備份批次。")
            return
        folder = sel[0]
        paths = None
        if only_selected:
            paths = set(self.batch_files.selection())
            if not paths:
                dialogs.showinfo("歌詞工具", "請先在右側選取要還原的檔案。")
                return
        n = len(paths) if paths else len(self.batch_files.get_children())
        if dialogs.askyesno("還原", f"確定要將 {n} 首歌曲還原為此批次寫入前的狀態嗎？"):
            self.start("還原", lambda ctx: jobs.run_restore(self.store, self.cfg, ctx, folder, paths),
                       lambda r: dialogs.showinfo("還原完成", f"已還原 {r['done']} 首，略過 {len(r['skipped'])} 首。"))

    def save_settings(self):
        if self.thread and self.thread.is_alive():
            dialogs.showinfo("歌詞工具", "目前有工作正在進行，請等待完成或先按「停止」，再儲存設定。")
            return
        aliases = {}
        for line in self.alias_box.get("1.0", "end").replace("＝", "=").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                names = split_names(v)
                if k.strip() and names:
                    aliases[k.strip()] = names
        new = dict(self.cfg)
        new.update({k: v.get().strip() for k, v in self.v_paths.items()})
        new["excluded"] = split_names(self.v_excluded.get())
        new["sources"] = {k: v.get() for k, v in self.v_sources.items()}
        new["to_traditional"] = self.v_tw.get()
        new["remove_credits"] = self.v_credits.get()
        new["skip_albums"] = self.v_skip_albums.get()
        new["aliases"] = aliases
        if not os.path.isdir(new["library"]):
            dialogs.showerror("歌詞工具", "找不到音樂資料夾。")
            return
        if new["backup_enabled"] and not new["backup_root"]:
            dialogs.showerror("歌詞工具", "請設定備份資料夾。")
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
            dialogs.showinfo("歌詞工具", "設定已儲存。")

    def on_backup_switch(self):
        """Takes effect at once, unlike the rest of the settings page: beside the write button, a tick has to mean what it shows."""
        if not self.v_backup.get() and not dialogs.confirm_risk("關閉備份", BACKUP_RISK, "關閉備份"):
            self.v_backup.set(True)
            return
        self.cfg["backup_enabled"] = self.v_backup.get()
        save_config(self.cfg)
        self.update_write_button()

    def pick(self, key, kind):
        p = filedialog.askdirectory() if kind == "dir" else filedialog.askopenfilename()
        if p:
            self.v_paths[key].set(os.path.normpath(p))

    def export_missing(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="缺少同步歌詞的歌曲.csv", filetypes=[("CSV", "*.csv")])
        if p:
            jobs.export_missing(self.store.missing_rows(), p)

    def export_review(self):
        p = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="審閱清單.csv", filetypes=[("CSV", "*.csv")])
        if p:
            jobs.export_review(self.store.pending_items(), p)

    # ---- review list ----
    def visible_rows(self):
        kind = {name: k for k, name in jobs.KIND_NAMES.items()}.get(self.v_kind.get())
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
            self.tree.insert("", "end", iid=iid, values=("☑" if r["selected"] else "☐", r["artist"], r["title"], jobs.KIND_NAMES[r["kind"]], r["source"], r["lines"], jobs.action_text(r)),
                             tags=("flag",) if json.loads(r["flags"]) else ())
            if iid in keep:
                self.tree.selection_add(iid)
        if not self.tree.selection():  # the song it was showing may be gone from the list
            self.show_text(self.preview, (REVIEW_HINT, "muted"))
        self.update_write_button()

    def update_write_button(self, running=None):
        running = self.thread is not None and self.thread.is_alive() if running is None else running
        n = sum(1 for r in self.review_rows if r["selected"])
        self.btn_write.configure(text=f"{'備份並寫入' if self.cfg['backup_enabled'] else '寫入'} {n} 首", state="normal" if n and not running else "disabled")
        self.review_count.configure(text=f"共 {len(self.review_rows)} 首，已勾選 {n} 首")
        self.nb.tab(self.tab_review, text=f"審閱與寫入（{len(self.review_rows)}）" if self.review_rows else "審閱與寫入")

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
            parts.append((f"來源：{r['source']}\n比對結果：{r['matched']}（{r['matched_length']:.1f} 秒，檔案為 {r['length']:.1f} 秒）\n", "muted"))
        parts += [(f"需留意：{flag}\n", "") for flag in json.loads(r["flags"])]
        removed = json.loads(r["removed"])
        if removed:
            parts += [("\n將刪除的內容：\n", "muted")] + [(line + "\n", "gone") for line in removed]
        parts += [("\n寫入後的歌詞：\n", "muted"), (r["lrc"], "")]
        self.show_text(self.preview, *parts)

    # ---- backups ----
    def fill_backups(self):
        self.backup_label.configure(text=f"備份資料夾：{self.cfg['backup_root']}" + ("" if self.cfg["backup_enabled"] else "　備份已關閉，之後寫入的歌曲不會列在此處"))
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
        self.card_tree.configure(displaycolumns=[c[0] for c in CARD_COLUMNS if key != "synced" or c[0] != "狀況"])
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
                self.card_tree.insert("", "end", iid=r["path"], values=(artist, title, WHY.get(r["why"], r["why"]), r["album"], r["ext"].lstrip(".").upper(), r["quality"] or ""))
        self.card_title.configure(text=f"{CARDS[key][0]}　{len(self.card_rows):,} 首")
        if keep and self.card_tree.exists(keep[0]):
            self.card_tree.selection_set(keep[0])
        else:
            self.show_track()

    def show_track(self, e=None):
        sel = self.card_tree.selection()
        r = self.card_rows.get(sel[0]) if sel else None
        t = self.store.track(r["path"]) if r else None
        if t is None:
            self.show_text(self.card_detail, ("從左側選取歌曲，即可查看詳細資料與歌詞。", "muted"))
            return
        artist, title = track_names(r)
        facts = [x for x in (r["ext"].lstrip(".").upper(), r["quality"], f"{r['size'] / 1e6:.1f} MB") if x]
        if r["length"]:
            facts.insert(0, f"{int(r['length']) // 60}:{int(r['length']) % 60:02d}")
        parts = [(f"{artist} - {title}\n" if artist else f"{title}\n", "head"), (f"{r['album']}\n" if r["album"] else "", "muted"), ("　".join(facts) + f"\n{r['path']}\n", "muted")]
        if r["state"] != "synced":
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["at"])) if r["at"] else ""
            extra = "，".join(x for x in (r["source"], when) if x)
            parts.append((f"\n狀況：{WHY.get(r['why'], r['why'])}" + (f"（{extra}）" if extra else "") + "\n", ""))
        if r["error"]:
            parts.append((f"錯誤：{r['error']}\n", ""))
        if r["state"] not in ("unsupported", "unreadable"):
            parts += [("\n內嵌歌詞：\n", "muted"), (t["lyrics"] or "（無）", "")]
        self.show_text(self.card_detail, *parts)

    # ---- overview ----
    def refresh_all(self):
        self.lib_label.configure(text=f"音樂資料夾：{self.cfg.get('library') or '尚未設定，請至「設定」分頁選擇'}")
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
            self.reason_tree.insert("", "end", values=(WHY.get(k, k), f"{v:,}"))
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
