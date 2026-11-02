"""How the app looks: dark and neutral, with rounded widgets drawn from small images generated here, so the build needs no image files or extra packages."""
import base64
import ctypes
import math
import struct
import tkinter as tk
import tkinter.font as tkfont
import zlib
from tkinter import ttk

FONT = "Microsoft JhengHei UI"
BG, CARD, RAISED, LINE, HOVER = "#0a0a0a", "#141414", "#262626", "#2e2e2e", "#1f1f1f"
INK, MUTED, DIM, LIGHT, ON_LIGHT = "#fafafa", "#a3a3a3", "#636363", "#e5e5e5", "#171717"
SELECT, WARN_BG, DANGER = "#404040", "#2b2310", "#f87171"
STATE_COLORS = {"synced": "#34d399", "plain": "#fbbf24", "none": "#525252", "other": "#f87171"}
TEXT = dict(bg=CARD, fg=INK, insertbackground=INK, selectbackground=SELECT, selectforeground=INK, inactiveselectbackground=RAISED, relief="flat", borderwidth=0, highlightthickness=0)


def _rgb(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def _blend(under, over, amount):
    return [u + (o - u) * amount for u, o in zip(under, over)]


def _inside(x, y, w, h, r):
    """How much of pixel (x, y) falls inside a w by h rectangle with corner radius r, from 0 to 1."""
    dx, dy = abs(x + 0.5 - w / 2) - w / 2 + r, abs(y + 0.5 - h / 2) - h / 2 + r
    return min(max(0.5 + r - math.hypot(max(dx, 0), max(dy, 0)) - min(max(dx, dy), 0), 0), 1)


def _on_line(x, y, points, half):
    """How much of pixel (x, y) falls on the line through `points`, `half` pixels thick on each side."""
    nearest = math.inf
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        t = max(0, min(1, ((x + 0.5 - ax) * (bx - ax) + (y + 0.5 - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2)))
        nearest = min(nearest, math.hypot(x + 0.5 - ax - t * (bx - ax), y + 0.5 - ay - t * (by - ay)))
    return min(max(0.5 + half - nearest, 0), 1)


def _photo(root, w, h, pixels):
    """`pixels` is w by h RGB triples, row by row."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    raw = b"".join(b"\0" + bytes(pixels[y * w * 3:(y + 1) * w * 3]) for y in range(h))
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return tk.PhotoImage(master=root, data=base64.b64encode(png).decode())


def _pixels(w, h, radius, fill, border=None, line=1, on=BG, mark=None, gap=0):
    """A rounded rectangle on a solid `on` colour, so it needs nothing from whatever ttk paints behind it.
    `mark` is (points, half thickness, colour) drawn on top; `gap` adds empty columns on the right."""
    fill, on, border = _rgb(fill), _rgb(on), _rgb(border or fill)
    pixels = []
    for y in range(h):
        for x in range(w + gap):
            color = on
            if x < w:
                color = _blend(color, border, _inside(x, y, w, h, radius))
                color = _blend(color, fill, _inside(x - line, y - line, w - 2 * line, h - 2 * line, max(radius - line, 0)))
                if mark:
                    color = _blend(color, _rgb(mark[2]), _on_line(x, y, mark[0], mark[1]))
            pixels += [round(v) for v in color]
    return pixels


def _grow(pixels, size, by):
    """Repeat the middle row and column of a square image `by` times. ttk stretches by tiling that part, so a 1-pixel middle costs a draw per pixel and freezes the window."""
    mid = size // 2 * 3
    rows = [pixels[y * size * 3:(y + 1) * size * 3] for y in range(size)]
    rows = [r[:mid] + r[mid:mid + 3] * by + r[mid + 3:] for r in rows]
    return [v for r in rows[:size // 2] + [rows[size // 2]] * by + rows[size // 2 + 1:] for v in r]


def apply(root, scale):
    """Create the theme and switch to it. Its images have to outlive this call, so `root` holds on to them."""
    def px(value):
        return max(1, round(value * scale))

    def keep(image):
        root.theme_images.append(image)
        return image

    def box(name, normal, *states, radius=8, on=BG, sticky="nswe", grow=96, **options):
        """A stretchable element: `normal` and the look of each state are (fill, border)."""
        r = px(radius)
        size = 2 * r + 1

        def patch(look):
            return keep(_photo(root, size + grow, size + grow, _grow(_pixels(size, size, r, look[0], look[1], px(1), on), size, grow + 1)))

        s.element_create(name, "image", patch(normal), *[(*st[:-1], patch(st[-1])) for st in states], border=r, padding=0, sticky=sticky, **{"width": size, "height": size, **options})

    def strip(name, top=None, bottom=None, fill=BG, size=96):
        """A flat element with a hairline along its top or bottom."""
        rows = [top] * px(1) * bool(top) + [fill] * size + [bottom] * px(1) * bool(bottom)
        image = keep(_photo(root, size, len(rows), [v for row in rows for v in _rgb(row) * size]))
        s.element_create(name, "image", image, border=(1, px(1) if top else 1, 1, px(1) if bottom else 1), padding=(0, px(1) if top else 0, 0, 0), sticky="nswe", width=1, height=1)

    root.theme_images = []
    s = ttk.Style(root)
    s.theme_create("lyricstool", parent="clam")
    s.theme_use("lyricstool")
    base = (FONT, 10)
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        tkfont.nametofont(name).configure(family=FONT, size=10)
    line = tkfont.Font(font=base).metrics("linespace")

    s.configure(".", background=BG, foreground=INK, font=base, bordercolor=LINE, lightcolor=BG, darkcolor=BG, troughcolor=BG, selectbackground=SELECT, selectforeground=INK, insertcolor=INK)
    s.configure("Muted.TLabel", foreground=MUTED)
    s.configure("Title.TLabel", font=(FONT, 10, "bold"))
    s.configure("Heading.TLabel", font=(FONT, 20, "bold"))
    s.configure("CardNum.TLabel", background=CARD, font=(FONT, 24, "bold"))
    s.configure("CardName.TLabel", background=CARD, foreground=MUTED)

    box("Card.border", (CARD, RAISED), ("selected", (CARD, MUTED)), ("active", (CARD, DIM)), ("focus", (CARD, DIM)), radius=10)
    s.layout("Card.TFrame", [("Card.border", {"sticky": "nswe"})])

    box("Plain.border", (CARD, LINE), ("disabled", (BG, RAISED)), ("pressed", (RAISED, SELECT)), ("active", (HOVER, SELECT)), ("focus", (CARD, MUTED)))
    box("Accent.border", (LIGHT, LIGHT), ("disabled", (RAISED, RAISED)), ("pressed", ("#d4d4d4", "#d4d4d4")), ("active", (INK, INK)))
    for style, element in (("TButton", "Plain.border"), ("Accent.TButton", "Accent.border")):
        s.layout(style, [(element, {"sticky": "nswe", "children": [("Button.padding", {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]})]})])
    s.configure("TButton", padding=(px(14), px(6)), anchor="center")
    s.map("TButton", foreground=[("disabled", DIM)])
    s.configure("Accent.TButton", foreground=ON_LIGHT, font=(FONT, 10, "bold"))
    s.map("Accent.TButton", foreground=[("disabled", DIM)])

    box("Field.border", (CARD, LINE), ("focus", (CARD, MUTED)))
    s.layout("TEntry", [("Field.border", {"sticky": "nswe", "children": [("Entry.padding", {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]})]})])
    s.configure("TEntry", padding=(px(10), px(6)), insertwidth=px(1))

    w, h = px(24), px(18)
    chevron = ([(w * 0.34, h * 0.40), (w * 0.5, h * 0.60), (w * 0.66, h * 0.40)], px(1) * 0.6, MUTED)
    s.element_create("Combobox.downarrow", "image", keep(_photo(root, w, h, _pixels(w, h, 0, CARD, on=CARD, mark=chevron))), sticky="", padding=0)
    s.layout("TCombobox", [("Field.border", {"sticky": "nswe", "children": [("Combobox.downarrow", {"side": "right", "sticky": ""}),
                                                                            ("Combobox.padding", {"sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]})])
    s.configure("TCombobox", padding=(px(10), px(6), 0, px(6)))
    s.map("TCombobox", foreground=[("readonly", INK)], selectbackground=[("readonly", CARD)], selectforeground=[("readonly", INK)])
    s.configure("ComboboxPopdownFrame", background=LINE, relief="flat", borderwidth=1)
    for option, value in (("background", CARD), ("foreground", INK), ("selectBackground", RAISED), ("selectForeground", INK), ("font", base), ("relief", "flat"), ("borderWidth", 0), ("highlightThickness", 0)):
        root.option_add(f"*TCombobox*Listbox.{option}", value)

    size, gap = px(16), px(8)
    tick = ([(size * 0.25, size * 0.52), (size * 0.43, size * 0.70), (size * 0.76, size * 0.31)], size * 0.075, ON_LIGHT)

    def indicator(fill, border, mark=None):
        return keep(_photo(root, size + gap, size, _pixels(size, size, px(4), fill, border, px(1), mark=mark, gap=gap)))

    empty, empty_hover = indicator(CARD, DIM), indicator(HOVER, MUTED)
    ticked, ticked_hover = indicator(LIGHT, LIGHT, tick), indicator(INK, INK, (tick[0], tick[1], ON_LIGHT))
    s.element_create("Checkbutton.indicator", "image", empty, ("selected", "active", ticked_hover), ("selected", ticked), ("active", empty_hover), ("focus", empty_hover), sticky="", padding=0)
    s.layout("TCheckbutton", [("Checkbutton.padding", {"sticky": "nswe", "children": [("Checkbutton.indicator", {"side": "left", "sticky": ""}), ("Checkbutton.label", {"side": "left", "sticky": "nswe"})]})])
    s.configure("TCheckbutton", padding=(0, px(3)))

    box("Notebook.tab", (BG, None), ("selected", (LIGHT, None)), ("active", (RAISED, None)))
    strip("Notebook.client", top=LINE)
    s.layout("TNotebook.Tab", [("Notebook.tab", {"sticky": "nswe", "children": [("Notebook.padding", {"sticky": "nswe", "children": [("Notebook.label", {"sticky": ""})]})]})])
    s.layout("TNotebook", [("Notebook.client", {"sticky": "nswe"})])
    s.configure("TNotebook", tabmargins=(0, 0, 0, px(10)))
    s.configure("TNotebook.Tab", padding=(px(16), px(6)))
    s.map("TNotebook.Tab", foreground=[("selected", ON_LIGHT)])

    s.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=INK, rowheight=line + px(10), borderwidth=0, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
    s.map("Treeview", background=[("selected", RAISED)], foreground=[("selected", INK)])
    strip("Treeheading.cell", bottom=LINE, fill=CARD)
    s.configure("Treeview.Heading", background=CARD, foreground=MUTED, padding=(px(6), px(5)), relief="flat", borderwidth=0, bordercolor=CARD, lightcolor=CARD, darkcolor=CARD)
    s.map("Treeview.Heading", background=[("active", CARD)])

    box("Vertical.Scrollbar.thumb", (SELECT, None), ("disabled", (CARD, None)), ("active", (DIM, None)), radius=4, on=CARD, height=px(28))  # ttk disables a scrollbar with nothing to scroll
    strip("Vertical.Scrollbar.trough", fill=CARD)
    s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])

    box("Horizontal.Progressbar.trough", (RAISED, None), radius=3, sticky="ew")
    box("Horizontal.Progressbar.pbar", (LIGHT, None), radius=3, sticky="nsw")
    s.layout("Horizontal.TProgressbar", [("Horizontal.Progressbar.trough", {"sticky": "ew", "children": [("Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"})]})])

    s.configure("TPanedwindow", background=BG)
    s.configure("Sash", sashthickness=px(12), gripcount=0, background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG, troughcolor=BG)


def dark_titlebar(window):
    """Ask Windows for a title bar that matches. Windows 10 ignores the exact colour and older versions ignore both, and neither is worth failing over."""
    try:
        for attribute, value in ((20, 1), (35, 0x000a0a0a)):  # DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_CAPTION_COLOR
            ctypes.windll.dwmapi.DwmSetWindowAttribute(int(window.frame(), 16), attribute, ctypes.byref(ctypes.c_int(value)), 4)
    except (OSError, AttributeError, ValueError):
        pass
