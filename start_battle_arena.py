from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
MATCH_SERVER_ROOT = PROJECT_ROOT / "local_server" / "battle_arena_server"
FRONTEND_ROOT = PROJECT_ROOT / "battle-arena"


def ps_quote(path: Path) -> str:
    """Quote a filesystem path for a PowerShell single-quoted string."""
    return "'" + str(path).replace("'", "''") + "'"


def launch_window(title: str, cwd: Path, command: str) -> None:
    safe_title = title.replace("'", "''")
    ps_command = (
        f"$Host.UI.RawUI.WindowTitle = '{safe_title}'; "
        f"Set-Location -LiteralPath {ps_quote(cwd)}; "
        f"{command}"
    )
    subprocess.Popen(
        ["powershell.exe", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def ensure_path(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def main() -> int:
    ensure_path(PROJECT_ROOT / "launcher.py", "N.E.K.O launcher")
    ensure_path(MATCH_SERVER_ROOT / "server.py", "Battle arena match server")
    ensure_path(FRONTEND_ROOT / "package.json", "Battle arena frontend")

    print("=" * 52)
    print("   Neko Battle Arena - One Click Startup")
    print("=" * 52)
    print(f"Project root: {PROJECT_ROOT}")
    print()

    print("[1/3] Opening N.E.K.O main server window (port 48911)...")
    launch_window(
        "N.E.K.O Main Server - 48911",
        PROJECT_ROOT,
        "uv run .\\launcher.py",
    )

    time.sleep(3)

    print("[2/3] Opening matchmaking server window (port 3001)...")
    launch_window(
        "Neko Battle Arena Match Server - 3001",
        MATCH_SERVER_ROOT,
        "uv run server.py",
    )

    time.sleep(2)

    print("[3/3] Opening battle-arena frontend window (port 5173)...")
    launch_window(
        "Neko Battle Arena Frontend - 5173",
        FRONTEND_ROOT,
        "npm run dev",
    )

    print()
    print("=" * 52)
    print("   Startup commands have been sent to 3 windows.")
    print("=" * 52)
    print("URLs:")
    print("  battle-arena: http://localhost:5173")
    print("  N.E.K.O main: http://localhost:48911")
    print("  Match server: http://localhost:3001/health")
    print()
    print("Keep the three opened command windows running while testing.")
    print("Press Enter to close this launcher window...")
    input()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[startup error] {exc}", file=sys.stderr)
        print("Press Enter to close this launcher window...")
        input()
        raise
