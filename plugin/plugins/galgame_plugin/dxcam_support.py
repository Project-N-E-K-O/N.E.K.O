from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable

from .install_tasks import update_install_task_state
from .memory_reader import is_windows_platform
from .tesseract_support import _compute_phase_progress, _emit_progress


def _find_python_executable() -> str:
    """
    Return a Python executable that can run pip.

    In packaged builds, sys.executable may be projectneko_server.exe, which
    refuses self-execution with "-m". Prefer real Python interpreters instead.
    """

    def _is_usable(path: str) -> bool:
        try:
            subprocess.run(
                [path, "--version"],
                check=True,
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception:
            return False

    base = getattr(sys, "_base_executable", None)
    if base and os.path.isfile(base) and _is_usable(base):
        return base

    exe_dir = Path(sys.executable).parent
    for candidate in ["python.exe", "python3.exe"]:
        candidate_path = exe_dir / candidate
        if candidate_path.is_file() and _is_usable(str(candidate_path)):
            return str(candidate_path)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        for candidate in ["python.exe", "python3.exe"]:
            candidate_path = Path(meipass) / candidate
            if candidate_path.is_file() and _is_usable(str(candidate_path)):
                return str(candidate_path)

    for candidate in ["python", "python3"]:
        found = shutil.which(candidate)
        if found and _is_usable(found):
            return found

    raise RuntimeError(
        "找不到可用的 Python 解释器，无法安装 pip 依赖。"
        "请安装 Python 3.9+ 并添加到 PATH，或联系开发者获取内置依赖的版本。"
        f"当前程序路径: {sys.executable}"
    )


DXCAM_PACKAGE_NAME = "dxcam"
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def _purge_module(module_name: str) -> None:
    for name in list(sys.modules.keys()):
        if name == module_name or name.startswith(f"{module_name}."):
            sys.modules.pop(name, None)


def _module_origin(module_name: str) -> str:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return ""
    return str(getattr(spec, "origin", "") or "") if spec is not None else ""


def inspect_dxcam_installation(
    *,
    platform_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checker = platform_fn or is_windows_platform
    supported = bool(checker())
    detected_path = _module_origin(DXCAM_PACKAGE_NAME) if supported else ""
    runtime_error = ""
    detail = "installed" if detected_path else "missing"
    if not supported:
        detail = "unsupported_platform"
    elif detected_path:
        try:
            importlib.import_module(DXCAM_PACKAGE_NAME)
        except Exception as exc:
            detail = "broken_runtime"
            runtime_error = str(exc)
    return {
        "install_supported": supported,
        "installed": detail == "installed",
        "can_install": supported and detail != "installed",
        "detected_path": detected_path,
        "package_name": DXCAM_PACKAGE_NAME,
        "target_dir": "current_python_environment",
        "detail": detail,
        "runtime_error": runtime_error,
    }


def _run_pip_install(*, timeout_seconds: float) -> None:
    temp_root = Path(tempfile.gettempdir()) / "neko-galgame-dxcam-install"
    temp_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["TMP"] = str(temp_root)
    env["TEMP"] = str(temp_root)
    env["TMPDIR"] = str(temp_root)
    command = [
        _find_python_executable(),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--disable-pip-version-check",
        DXCAM_PACKAGE_NAME,
    ]
    subprocess.run(
        command,
        check=True,
        timeout=timeout_seconds,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _localized_dxcam_install_error(exc: Exception) -> str:
    stderr_text = ""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr_text = str(getattr(exc, "stderr", "") or getattr(exc, "output", "") or "").strip()
    combined = " ".join(part for part in [stderr_text, str(exc or "").strip()] if part).strip()
    if "No module named pip" in combined:
        return "DXcam 安装失败：当前 Python 环境缺少 pip，无法安装截图依赖。"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "DXcam 安装超时：请检查网络、代理或 PyPI 访问状态后重试。"
    if isinstance(exc, subprocess.CalledProcessError):
        return "DXcam 安装失败：pip 安装 dxcam 没有成功完成，请检查网络、代理、防火墙或当前 Python 环境权限。"
    if "DXcam install is only supported on Windows" in combined:
        return "DXcam 安装失败：DXcam 截图后端仅支持 Windows。"
    return "DXcam 安装失败：安装截图依赖时发生未知异常，请查看插件日志后重试。"


async def install_dxcam(
    *,
    logger,
    timeout_seconds: float,
    force: bool = False,
    platform_fn: Callable[[], bool] | None = None,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    install_status = inspect_dxcam_installation(platform_fn=platform_fn)
    if not install_status["install_supported"]:
        raise RuntimeError("DXcam install is only supported on Windows")
    if install_status["installed"] and not force:
        result = {
            **install_status,
            "already_installed": True,
            "summary": f"DXcam installed: {install_status['detected_path']}",
            "release_name": "DXcam",
            "asset_name": DXCAM_PACKAGE_NAME,
        }
        completed = {
            "status": "completed",
            "phase": "completed",
            "message": "DXcam is already installed",
            "progress": 1.0,
            "target_dir": str(install_status.get("target_dir") or ""),
            "detected_path": str(install_status.get("detected_path") or ""),
            "release_name": "DXcam",
            "asset_name": DXCAM_PACKAGE_NAME,
        }
        if task_id:
            update_install_task_state(task_id, kind="dxcam", **completed)
        await _emit_progress(progress_callback, completed)
        return result

    installing = {
        "status": "running",
        "phase": "installing",
        "message": "Installing DXcam dependency",
        "progress": _compute_phase_progress("installing"),
        "target_dir": "current_python_environment",
        "detected_path": "",
        "release_name": "DXcam",
        "asset_name": DXCAM_PACKAGE_NAME,
        "error": "",
    }
    if task_id:
        update_install_task_state(task_id, kind="dxcam", **installing)
    await _emit_progress(progress_callback, installing)

    try:
        await asyncio.to_thread(_run_pip_install, timeout_seconds=timeout_seconds)
        _purge_module(DXCAM_PACKAGE_NAME)
        importlib.invalidate_caches()
        verifying = {
            "status": "running",
            "phase": "verifying",
            "message": "Verifying DXcam dependency",
            "progress": _compute_phase_progress("verifying"),
            "target_dir": "current_python_environment",
            "detected_path": "",
            "release_name": "DXcam",
            "asset_name": DXCAM_PACKAGE_NAME,
            "error": "",
        }
        if task_id:
            update_install_task_state(task_id, kind="dxcam", **verifying)
        await _emit_progress(progress_callback, verifying)

        result_status = inspect_dxcam_installation(platform_fn=platform_fn)
        if not result_status["installed"]:
            raise RuntimeError(f"DXcam installation is incomplete: {result_status.get('detail') or 'unknown'}")
        result = {
            **result_status,
            "already_installed": False,
            "summary": "DXcam dependency installed",
            "release_name": "DXcam",
            "asset_name": DXCAM_PACKAGE_NAME,
        }
        completed = {
            "status": "completed",
            "phase": "completed",
            "message": "DXcam installation completed",
            "progress": 1.0,
            "target_dir": str(result_status.get("target_dir") or ""),
            "detected_path": str(result_status.get("detected_path") or ""),
            "release_name": "DXcam",
            "asset_name": DXCAM_PACKAGE_NAME,
            "error": "",
        }
        if task_id:
            update_install_task_state(task_id, kind="dxcam", **completed)
        await _emit_progress(progress_callback, completed)
        return result
    except Exception as exc:
        if logger is not None:
            logger.exception("DXcam install failed: {}", exc)
        error_message = _localized_dxcam_install_error(exc)
        failed = {
            "status": "failed",
            "phase": "failed",
            "message": error_message,
            "progress": _compute_phase_progress("failed"),
            "target_dir": "current_python_environment",
            "release_name": "DXcam",
            "asset_name": DXCAM_PACKAGE_NAME,
            "error": error_message,
        }
        if task_id:
            update_install_task_state(task_id, kind="dxcam", **failed)
        await _emit_progress(progress_callback, failed)
        raise RuntimeError(error_message) from exc
