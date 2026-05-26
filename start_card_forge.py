from __future__ import annotations

import platform
import subprocess
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
FORGE_SERVER_ROOT = PROJECT_ROOT / "local_server" / "card_forge_server"
FRONTEND_ROOT = PROJECT_ROOT / "card-forge"


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


# 这个一键启动脚本目前只支持 Windows：依赖 powershell.exe / CREATE_NEW_CONSOLE
# 弹三个独立 cmd 窗口。其他平台没有等价的"打开三个新终端 + 在每个里跑一条命令"
# 的统一 API，强行启动会直接抛 FileNotFoundError 或 ValueError，没有任何价值，
# 所以早判退出并打印手动步骤，避免误以为脚本只是"卡住了"。
#
# 注意：这里抛 RuntimeError 而不是 SystemExit。SystemExit 继承 BaseException 不是
# Exception，会绕过 __main__ 块里的 `except Exception` 分支，少了一次 "Press Enter"
# 暂停 —— 双击运行时窗口会瞬间关闭，用户根本看不到提示。
def _ensure_windows() -> None:
    if platform.system() == "Windows":
        return
    msg = "\n".join([
        "start_card_forge.py 目前只支持 Windows "
        "(依赖 powershell.exe 和 CREATE_NEW_CONSOLE 弹独立窗口)。",
        "在 macOS / Linux 上请分别在三个终端里手动执行：",
        f"  1) cd {PROJECT_ROOT} && uv run launcher.py",
        f"  2) cd {FORGE_SERVER_ROOT} && uv run server.py",
        f"  3) cd {FRONTEND_ROOT} && npm run dev",
    ])
    raise RuntimeError(msg)


def main() -> int:
    _ensure_windows()
    ensure_path(PROJECT_ROOT / "launcher.py", "N.E.K.O launcher")
    ensure_path(FORGE_SERVER_ROOT / "server.py", "Card forge server")
    ensure_path(FRONTEND_ROOT / "package.json", "Card forge frontend")

    print("=" * 52)
    print("   Neko Card Forge - One Click Startup")
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

    print("[2/3] Opening card forge server window (port 3001)...")
    launch_window(
        "Neko Card Forge Server - 3001",
        FORGE_SERVER_ROOT,
        "uv run server.py",
    )

    time.sleep(2)

    print("[3/3] Opening card-forge frontend window (port 5173)...")
    launch_window(
        "Neko Card Forge Frontend - 5173",
        FRONTEND_ROOT,
        "npm run dev",
    )

    print()
    print("=" * 52)
    print("   Startup commands have been sent to 3 windows.")
    print("=" * 52)
    print("URLs:")
    print("  card-forge:   http://localhost:5173")
    print("  N.E.K.O main: http://localhost:48911")
    print("  Forge server: http://localhost:3001/health")
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
