"""Message boxes in the app's own look, called like tkinter.messagebox."""
import tkinter as tk
from tkinter import ttk

from . import theme
from .i18n import tr


def _ask(title, text, buttons):
    """`buttons` are (label, answer) from left to right; the last is highlighted and takes Enter first. Closing the window or Esc answers False."""
    root = tk._default_root
    if root is None:  # the window has already closed, and asking tkinter would open a new one
        return False
    scale = root.winfo_fpixels("1i") / 96
    win = tk.Toplevel(root, bg=theme.BG)
    win.withdraw()
    win.title(title)
    win.resizable(False, False)
    answer = [False]

    def close(value):
        answer[0] = value
        win.destroy()

    body = ttk.Frame(win, padding=round(24 * scale))
    body.pack(fill="both", expand=True)
    ttk.Label(body, text=text, wraplength=round(420 * scale), justify="left").pack(anchor="w")
    row = ttk.Frame(body)
    row.pack(anchor="e", pady=(round(20 * scale), 0))
    for i, (label, value) in enumerate(buttons):
        highlighted = i == len(buttons) - 1
        button = ttk.Button(row, text=label, style="Accent.TButton" if highlighted else "TButton", command=lambda v=value: close(v))
        button.pack(side="left", padx=(round(8 * scale) if i else 0, 0))
    win.protocol("WM_DELETE_WINDOW", lambda: close(False))
    win.bind("<Escape>", lambda e: close(False))
    win.bind("<Return>", lambda e: (e.widget if isinstance(e.widget, ttk.Button) else button).invoke())  # whichever button Tab has moved to

    win.update_idletasks()
    if root.state() == "normal":
        win.transient(root)
        x, y, w, h = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height()
    else:  # tied to a minimized window it would stay hidden, and it may be reporting a finished job
        x, y, w, h = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
    win.geometry(f"+{x + (w - win.winfo_reqwidth()) // 2}+{y + (h - win.winfo_reqheight()) // 3}")
    win.deiconify()
    win.wait_visibility()
    theme.dark_titlebar(win)
    win.grab_set()
    button.focus_set()
    win.wait_window()
    return answer[0]


def showinfo(title, text):
    _ask(title, text, [(tr("OK"), None)])


showerror = showinfo


def askyesno(title, text):
    return _ask(title, text, [(tr("No"), False), (tr("Yes"), True)])


def confirm_risk(title, text, action):
    """True if the person goes ahead with `action`. The safe answer is the highlighted one, so Enter does no harm."""
    return _ask(title, text, [(action, True), (tr("Cancel"), False)])
