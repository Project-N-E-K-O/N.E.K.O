"""Steps that only exist on Linux. Every function is a no-op (or returns False/{}) elsewhere.

* drop the OS page cache before a cold start (needs password-less sudo)
* run the desktop shell under Xvfb on a machine without a display
* collect machine details from /proc and lspci
Windows equivalents are described in README.md (reboot, or --cold-cmd with Sysinternals RAMMap).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

IS_LINUX = sys.platform.startswith("linux")
XVFB_DISPLAY = ":99"
# N.E.K.O.-PC relaunches itself once on Linux to add these; passing them up front keeps the CDP port reachable.
ELECTRON_X11_FLAGS = [
    "--ozone-platform=x11", "--disable-accelerated-video-decode", "--disable-accelerated-video-encode",
    "--neko-linux-x11-flags-applied", "--disable-features=VaapiVideoDecoder,VaapiVideoEncoder",
]


def can_drop_caches() -> bool:
    if not IS_LINUX or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0


def drop_caches() -> None:
    subprocess.run(["sync"], check=False)
    subprocess.run(["sudo", "-n", "sh", "-c", "echo 3 > /proc/sys/vm/drop_caches"], check=False)


def start_xvfb(log_path: Path):
    if not IS_LINUX:
        raise SystemExit("--xvfb is Linux only")
    exe = shutil.which("Xvfb")
    if not exe:
        raise SystemExit("Xvfb not installed")
    out = open(log_path, "ab")
    proc = subprocess.Popen([exe, XVFB_DISPLAY, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"], stdout=out, stderr=subprocess.STDOUT)
    import time
    time.sleep(1)
    return proc


def machine_details() -> dict:
    if not IS_LINUX:
        return {}
    info = {}

    def run(cmd):
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None

    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu_name"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    info["kernel"] = run(["uname", "-r"])
    info["free_h"] = run(["free", "-h"])
    info["gpu"] = (run(["sh", "-c", "lspci 2>/dev/null | grep -iE 'vga|3d|display'"]) or "none detected")
    info["virt"] = run(["systemd-detect-virt"])
    info["rootfs"] = run(["sh", "-c", "df -T / | tail -1 | awk '{print $2}'"])
    return info
