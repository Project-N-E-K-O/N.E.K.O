"""OS-level helpers for the visit T1~T5 probe (Windows only, not product code).

Subcommands (all coordinates are physical pixels, the process is per-monitor DPI aware):
  monitors                       -> JSON list of monitors
  shot OUT.png X Y W H           -> screenshot of the region (+40 px margin) only
  pet                            -> JSON {hwnd, rect, exstyle} of the Electron Pet window (full-monitor top-level window)
  hit X Y [WAIT_S]               -> move the cursor to (X, Y), wait, report Pet WS_EX_TRANSPARENT (click-through) state
  findwin TITLE                  -> {hwnd, rect} of a visible top-level window with that title, or null
  cursor                         -> current cursor position
  setcursor X Y                  -> move the cursor
  compare BG.png FG.png BG2.png W H      -> diff stats of the probe region inside region shots
  composite BG.png FG.png PACK.png W H   -> expected-vs-observed composite error for the unpacked overlay
"""

import ctypes
import ctypes.wintypes as wt
import json
import sys
import time

user32 = ctypes.windll.user32
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    user32.SetProcessDPIAware()

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000
WS_EX_TOPMOST = 0x8


def monitors():
    out = []
    MONITORENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_int, wt.HMONITOR, wt.HDC, ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, lprc, data):
        r = lprc.contents
        dpi_x = ctypes.c_uint()
        dpi_y = ctypes.c_uint()
        try:
            ctypes.windll.shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y))
        except Exception:
            dpi_x.value = 96
        out.append({"rect": [r.left, r.top, r.right - r.left, r.bottom - r.top], "scale": dpi_x.value / 96})
        return 1

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


def _window_pid(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _proc_name(pid):
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(1024)
    ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    ctypes.windll.kernel32.CloseHandle(h)
    return buf.value


def pet_window():
    mons = [m["rect"] for m in monitors()]
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        name = _proc_name(_window_pid(hwnd)).lower()
        if not name.endswith("electron.exe"):
            return True
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        rect = [r.left, r.top, r.right - r.left, r.bottom - r.top]
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        found.append({"hwnd": int(hwnd), "rect": rect, "exstyle": ex, "title": buf.value, "fullMonitor": rect in mons})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    def near_full(w):
        x, y, ww, hh = w["rect"]
        for mx, my, mw, mh in mons:
            if abs(x - mx) <= 4 and abs(y - my) <= 4 and abs(ww - mw) <= 4 and abs(hh - mh) <= 4:
                return True
        return False

    full = [w for w in found if near_full(w)]
    return (full[0] if full else None), found


def find_window(title):
    """Visible top-level window with this exact title -> {hwnd, rect}, or None."""
    hwnd = user32.FindWindowW(None, title)
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return None
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return {"hwnd": int(hwnd), "rect": [r.left, r.top, r.right - r.left, r.bottom - r.top]}


def exstyle(hwnd):
    return user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("pad", ctypes.c_byte * 32)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def move_to(x, y):
    """Injected absolute mouse move (SendInput). Unlike SetCursorPos it goes through low-level mouse hooks,
    which is how Electron's setIgnoreMouseEvents(true, {forward: true}) forwards moves to the page."""
    SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
    vx, vy = user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    vw, vh = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK = 0x1, 0x8000, 0x4000
    inp = _INPUT(type=0)
    inp.mi = _MOUSEINPUT(int((x - vx) * 65535 / max(1, vw - 1)), int((y - vy) * 65535 / max(1, vh - 1)), 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, 0, None)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def cursor():
    p = wt.POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return [p.x, p.y]


def hit(x, y, wait_s=1.2):
    pet, _ = pet_window()
    if not pet:
        return {"err": "pet window not found"}
    # wiggle with injected input so the page sees real mouse moves at the target point
    move_to(x - 3, y)
    time.sleep(0.1)
    move_to(x, y)
    time.sleep(wait_s)
    ex = exstyle(pet["hwnd"])
    # Functional check: which top-level window would receive a click here. In compatibility mode the Pet is a
    # per-pixel-alpha layered window, so transparent pixels pass through without WS_EX_TRANSPARENT ever being set.
    hit_root = user32.GetAncestor(user32.WindowFromPoint(wt.POINT(x, y)), 2)
    return {
        "x": x, "y": y, "exstyle": hex(ex),
        "wsExTransparent": bool(ex & WS_EX_TRANSPARENT),
        "hitWindowIsPet": int(hit_root or 0) == int(pet["hwnd"]),
        "clickThrough": int(hit_root or 0) != int(pet["hwnd"]),
    }


SHOT_MARGIN = 40


def shot(path, x, y, w, h):
    """Screenshot only the probe region (+margin); full-screen images are never written to disk."""
    from PIL import ImageGrab

    m = SHOT_MARGIN
    img = ImageGrab.grab(bbox=(x - m, y - m, x + w + m, y + h + m), all_screens=True)
    img.save(path)
    return {"path": path, "size": img.size, "origin": [x - m, y - m]}


def _load(path):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB")).astype("int32")


def compare(bg, fg, bg2, x, y, w, h):
    import numpy as np

    a, b, c = _load(bg), _load(fg), _load(bg2)
    sl = (slice(y, y + h), slice(x, x + w))
    d_fg = np.abs(b[sl] - a[sl]).max(axis=2)
    d_bg = np.abs(c[sl] - a[sl]).max(axis=2)
    return {
        "region": [x, y, w, h],
        "bgStable_meanAbs": float(d_bg.mean()),
        "bgStable_pixelsOver8": int((d_bg > 8).sum()),
        "fg_vs_bg_meanAbs": float(d_fg.mean()),
        "fg_vs_bg_max": int(d_fg.max()),
        "fg_vs_bg_pixelsOver8": int((d_fg > 8).sum()),
        "fg_mean_rgb": [float(v) for v in b[sl].reshape(-1, 3).mean(axis=0)],
        "bg_mean_rgb": [float(v) for v in a[sl].reshape(-1, 3).mean(axis=0)],
        "pixels": int(w * h),
    }


def composite(bg, fg, pack, x, y, w, h, margin=3):
    """Expected screen = min(premul, a) + bg * (1 - a), sampled nearest from the packed image."""
    import numpy as np

    B, F, PK = _load(bg), _load(fg), _load(pack)
    ph, pw = PK.shape[0] // 2, PK.shape[1]
    top, bot = PK[:ph], PK[ph:]
    ys = np.arange(y, y + h)
    xs = np.arange(x, x + w)
    ty = np.clip(((ys - y + 0.5) / h * ph).astype(int), 0, ph - 1)
    tx = np.clip(((xs - x + 0.5) / w * pw).astype(int), 0, pw - 1)
    prem = top[ty][:, tx]
    alpha = bot[ty][:, tx][:, :, :1].astype("float64") / 255.0
    rgb = np.minimum(prem, bot[ty][:, tx][:, :, :1])
    bgr = B[y : y + h, x : x + w]
    expected = rgb + bgr * (1 - alpha)
    observed = F[y : y + h, x : x + w]
    err = np.abs(observed - expected).max(axis=2)[margin:-margin, margin:-margin]
    # also: how far the observed image is from "no overlay" (sanity: overlay actually visible)
    vis = np.abs(observed - bgr).max(axis=2)[margin:-margin, margin:-margin]
    # opaque-white / opaque-black failure signatures in the alpha==0 column band
    a0 = (alpha[:, :, 0] < 0.02)[margin:-margin, margin:-margin]
    d0 = np.abs(observed - bgr).max(axis=2)[margin:-margin, margin:-margin]
    return {
        "region": [x, y, w, h],
        "meanAbsErr": float(err.mean()),
        "p99AbsErr": float(np.percentile(err, 99)),
        "maxAbsErr": int(err.max()),
        "pixelsErrOver8": int((err > 8).sum()),
        "pixels": int(err.size),
        "overlayVisible_meanAbsDiff": float(vis.mean()),
        "alpha0_region_meanAbsDiffFromBg": float(d0[a0].mean()) if a0.any() else None,
    }


def main(argv):
    cmd = argv[1]
    if cmd == "monitors":
        r = monitors()
    elif cmd == "shot":
        r = shot(argv[2], *map(int, argv[3:7]))
    elif cmd == "pet":
        p, allw = pet_window()
        r = {"pet": p, "electronWindows": allw}
    elif cmd == "hit":
        r = hit(int(argv[2]), int(argv[3]), float(argv[4]) if len(argv) > 4 else 1.2)
    elif cmd == "findwin":
        r = find_window(argv[2])
    elif cmd == "cursor":
        r = cursor()
    elif cmd == "setcursor":
        move_to(int(argv[2]), int(argv[3]))
        r = cursor()
    elif cmd == "compare":
        r = compare(argv[2], argv[3], argv[4], SHOT_MARGIN, SHOT_MARGIN, *map(int, argv[5:7]))
    elif cmd == "composite":
        r = composite(argv[2], argv[3], argv[4], SHOT_MARGIN, SHOT_MARGIN, *map(int, argv[5:7]))
    else:
        raise SystemExit("unknown command " + cmd)
    print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv)
