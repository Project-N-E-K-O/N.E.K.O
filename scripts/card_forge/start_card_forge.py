from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FORGE_SERVER_ROOT = PROJECT_ROOT / "local_server" / "card_forge_server"
FRONTEND_ROOT = PROJECT_ROOT / "frontend" / "card-forge"


def ps_quote(path: Path) -> str:
    """Quote a filesystem path for a PowerShell single-quoted string."""
    return "'" + str(path).replace("'", "''") + "'"


def launch_window(
    title: str,
    cwd: Path,
    command: str,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    safe_title = title.replace("'", "''")
    ps_command = (
        f"$Host.UI.RawUI.WindowTitle = '{safe_title}'; "
        f"Set-Location -LiteralPath {ps_quote(cwd)}; "
        f"{command}"
    )
    subprocess.Popen(
        ["powershell.exe", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_command],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        env=dict(env) if env is not None else None,
    )


def ensure_path(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path}")


def ensure_frontend_dependencies(frontend_root: Path = FRONTEND_ROOT) -> None:
    """Install the locked Card Forge frontend dependencies when Vite is absent."""
    vite_launcher = frontend_root / "node_modules" / ".bin" / "vite.cmd"
    if vite_launcher.is_file():
        return

    package_lock = frontend_root / "package-lock.json"
    if not package_lock.is_file():
        raise RuntimeError(
            f"Card forge package lock not found: {package_lock}. "
            "Restore package-lock.json before starting."
        )

    print("[preflight] Card forge dependencies are missing; running npm ci...")
    try:
        subprocess.run(["npm.cmd", "ci"], cwd=frontend_root, check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "npm.cmd not found. Install Node.js/npm and retry the Card Forge launcher."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"npm ci failed for Card Forge (exit code {exc.returncode})."
        ) from exc

    if not vite_launcher.is_file():
        raise RuntimeError(
            f"npm ci completed but the Vite launcher is still missing: {vite_launcher}"
        )


def _valid_port(value: object) -> int | None:
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def _port_config_path(environment: Mapping[str, str]) -> Path:
    home = Path.home()
    system = platform.system()
    if system == "Windows":
        base = Path(environment.get("APPDATA") or home / "AppData" / "Roaming")
    elif system == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(environment.get("XDG_CONFIG_HOME") or home / ".config")
    return base / "N.E.K.O" / "port_config.json"


def resolve_configured_port(
    port_name: str,
    default_port: int,
    *,
    environment: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> int:
    """Match the desktop port precedence used by config.network and Vite."""
    selected_environment = os.environ if environment is None else environment
    for key in (f"NEKO_{port_name}", port_name):
        port = _valid_port(selected_environment.get(key))
        if port is not None:
            return port

    try:
        payload = json.loads(
            (config_path or _port_config_path(selected_environment)).read_text(
                encoding="utf-8"
            )
        )
        port = _valid_port(
            payload.get(port_name) if isinstance(payload, dict) else None
        )
        if port is not None:
            return port
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return default_port


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
        f"  3) cd {FRONTEND_ROOT} && npm ci && npm run dev",
    ])
    raise RuntimeError(msg)


def main() -> int:
    _ensure_windows()
    ensure_path(PROJECT_ROOT / "launcher.py", "N.E.K.O launcher")
    ensure_path(FORGE_SERVER_ROOT / "server.py", "Card forge server")
    ensure_path(FRONTEND_ROOT / "package.json", "Card forge frontend")
    ensure_frontend_dependencies()

    main_server_port = resolve_configured_port("MAIN_SERVER_PORT", 48911)
    card_forge_port = resolve_configured_port("CARD_FORGE_PORT", 3001)
    child_env = os.environ.copy()
    child_env["NEKO_MAIN_SERVER_PORT"] = str(main_server_port)
    child_env["NEKO_CARD_FORGE_PORT"] = str(card_forge_port)

    print("=" * 52)
    print("   Neko Card Forge - One Click Startup")
    print("=" * 52)
    print(f"Project root: {PROJECT_ROOT}")
    print()

    print(f"[1/3] Opening N.E.K.O main server window (port {main_server_port})...")
    launch_window(
        f"N.E.K.O Main Server - {main_server_port}",
        PROJECT_ROOT,
        "uv run .\\launcher.py",
        env=child_env,
    )

    time.sleep(3)

    print(f"[2/3] Opening card forge server window (port {card_forge_port})...")
    launch_window(
        f"Neko Card Forge Server - {card_forge_port}",
        FORGE_SERVER_ROOT,
        "uv run server.py",
        env=child_env,
    )

    time.sleep(2)

    print("[3/3] Opening card-forge frontend window (port 5173)...")
    launch_window(
        "Neko Card Forge Frontend - 5173",
        FRONTEND_ROOT,
        "npm run dev",
        env=child_env,
    )

    print()
    print("=" * 52)
    print("   Startup commands have been sent to 3 windows.")
    print("=" * 52)
    print("URLs:")
    print("  card-forge:   http://localhost:5173")
    print(f"  N.E.K.O main: http://localhost:{main_server_port}")
    print(f"  Forge server: http://localhost:{card_forge_port}/health")
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
