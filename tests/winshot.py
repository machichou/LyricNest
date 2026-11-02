"""Saves one window to a PNG through PrintWindow, so a test window never has to come forward over what someone is doing on this computer."""
import ctypes
import ctypes.wintypes as wt

from PIL import Image


class BitmapInfo(ctypes.Structure):
    _fields_ = [("size", wt.DWORD), ("w", wt.LONG), ("h", wt.LONG), ("planes", wt.WORD), ("bits", wt.WORD), ("comp", wt.DWORD), ("img", wt.DWORD),
                ("xp", wt.LONG), ("yp", wt.LONG), ("used", wt.DWORD), ("imp", wt.DWORD)]


def save_window(hwnd, path):
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    dc = u.GetDC(hwnd)
    mem = g.CreateCompatibleDC(dc)
    bmp = g.CreateCompatibleBitmap(dc, w, h)
    g.SelectObject(mem, bmp)
    u.PrintWindow(hwnd, mem, 2)  # 2 = PW_RENDERFULLCONTENT
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(BitmapInfo(ctypes.sizeof(BitmapInfo), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)), 0)
    Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB").save(path)
    g.DeleteObject(bmp)
    g.DeleteDC(mem)
    u.ReleaseDC(hwnd, dc)
