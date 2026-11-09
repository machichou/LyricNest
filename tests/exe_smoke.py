"""Launch the packaged exe for real on throwaway data: exe_smoke.py <exe> <scratch_dir>. Saves a screenshot and checks start, file properties, single instance and clean exit.
Runs its own copy and only looks at that copy's windows: this test closes what it finds, and a copy someone has open must not be among it."""
import ctypes
import ctypes.wintypes as wt
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lyricstool import __author__, __version__
from winshot import save_window

exe, scratch = sys.argv[1:3]
shutil.rmtree(scratch, ignore_errors=True)
data, lib = os.path.join(scratch, "data"), os.path.join(scratch, "music")
os.makedirs(data)
os.makedirs(lib)
exe = shutil.copy(exe, os.path.join(scratch, "LyricNest.exe"))
mine = os.path.normcase(os.path.abspath(exe))
subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=duration=1", "-metadata", "artist=陳綺貞", "-metadata", "title=太陽", os.path.join(lib, "a.flac")], check=True)
with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as fh:
    json.dump({"library": lib, "backup_root": os.path.join(scratch, "bak"), "lrclib_db": "", "lrcget_db": "", "language": "zh-TW"}, fh)  # the window titles looked for below are the Chinese ones
env = dict(os.environ, LYRICSTOOL_DATA=data)
u = ctypes.windll.user32
k = ctypes.windll.kernel32
k.OpenProcess.restype = wt.HANDLE
k.QueryFullProcessImageNameW.argtypes = (wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
k.CloseHandle.argtypes = (wt.HANDLE,)
ctypes.windll.shcore.SetProcessDpiAwareness(1)


def title(hwnd):
    buf = ctypes.create_unicode_buffer(200)
    u.GetWindowTextW(hwnd, buf, 200)
    return buf.value


def image(hwnd):
    pid = wt.DWORD()
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf, size = ctypes.create_unicode_buffer(600), wt.DWORD(600)
    k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    k.CloseHandle(h)
    return buf.value


def properties(path):
    """What Windows shows under the file's Properties."""
    cmd = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; (Get-Item -LiteralPath '{path}').VersionInfo | Select-Object CompanyName,LegalCopyright,ProductName,ProductVersion | ConvertTo-Json"
    return json.loads(subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, encoding="utf-8-sig", check=True).stdout)


def windows():
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def visit(hwnd, _):
        if u.IsWindowVisible(hwnd) and title(hwnd).startswith("LyricNest") and os.path.normcase(image(hwnd)) == mine:
            found.append(hwnd)
        return True

    u.EnumWindows(visit, 0)
    return found


def wait_for(test, seconds):
    """The onefile exe is a small bootloader plus the real program, so windows are found by title, not by the started process."""
    end = time.time() + seconds
    while time.time() < end:
        hits = [w for w in windows() if test(title(w))]
        if hits:
            return hits[0]
        time.sleep(0.5)
    return None


def end_process_tree(proc):
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)


props = properties(exe)
print("properties:", props)
assert (props["CompanyName"], props["ProductVersion"]) == (__author__, __version__) and __author__ in props["LegalCopyright"]

t0 = time.time()
p = subprocess.Popen([exe], env=env)
second = None
try:
    hwnd = wait_for(lambda t: t != "LyricNest", 40)
    print("window after", round(time.time() - t0, 1), "s:", bool(hwnd))
    assert hwnd, "no window"
    time.sleep(3)  # let the automatic scan finish
    print("title:", title(hwnd))
    assert title(hwnd) == f"LyricNest {__version__}"
    save_window(hwnd, os.path.join(scratch, "exe.png"))

    second = subprocess.Popen([exe], env=env)
    notice = wait_for(lambda t: t == "LyricNest", 25)
    print("second copy shows a notice:", bool(notice), "| main windows:", sum(title(w) != "LyricNest" for w in windows()))
    assert notice, "second copy gave no notice"
    u.PostMessageW(notice, 0x0010, 0, 0)
    print("second copy exit:", second.wait(15))

    u.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE, what the X button sends
    print("exit code:", p.wait(20))
finally:
    for proc in (p, second):
        if proc and proc.poll() is None:
            print("still running, ending it")
            end_process_tree(proc)
log = os.path.join(data, "app.log")
text = open(log, encoding="utf-8").read() if os.path.exists(log) else ""
print("log:", text[-600:])
assert "ERROR" not in text and "Traceback" not in text
print("no errors logged")
