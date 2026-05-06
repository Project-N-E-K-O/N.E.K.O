from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

import httpx

from utils.config_manager import get_config_manager

from .memory_reader import is_windows_platform
from .tesseract_support import _compute_phase_progress, _emit_progress
from .install_tasks import update_install_task_state

RAPIDOCR_PACKAGE_NAME = "rapidocr_onnxruntime"
DEFAULT_RAPIDOCR_ENGINE_TYPE = "onnxruntime"
DEFAULT_RAPIDOCR_LANG_TYPE = "ch"
DEFAULT_RAPIDOCR_MODEL_TYPE = "mobile"
DEFAULT_RAPIDOCR_OCR_VERSION = "PP-OCRv5"
DEFAULT_RAPIDOCR_PIP_SPEC = "rapidocr_onnxruntime"
DEFAULT_ONNXRUNTIME_PIP_SPEC = "onnxruntime"
_INSTALL_STATE_NAME = "install_state.json"
# RapidOCR builds 3 onnxruntime sessions (det/cls/rec); cap intra-op threads
# so each inference burst doesn't saturate every logical core on the host.
_RAPIDOCR_INFERENCE_THREAD_LIMIT = 4
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]
_RAPIDOCR_IMPORT_CONTEXT_LOCK = threading.RLock()


def _expand_candidate_path(raw_path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(raw_path)))


def _app_runtimes_root() -> Path:
    return get_config_manager().app_docs_dir / "runtimes" / "galgame_plugin"


def default_rapidocr_install_target_raw() -> str:
    if is_windows_platform():
        return str(_app_runtimes_root() / "RapidOCR")
    return ""


def default_rapidocr_install_target_raw_legacy() -> str:
    if is_windows_platform():
        return "%LOCALAPPDATA%/Programs/N.E.K.O/RapidOCR"
    return ""


def resolve_rapidocr_install_target(raw_target_dir: str) -> Path:
    normalized = str(raw_target_dir or "").strip()
    if normalized:
        return _expand_candidate_path(normalized)

    target = _app_runtimes_root() / "RapidOCR"
    if not target.exists():
        legacy_raw = default_rapidocr_install_target_raw_legacy()
        if legacy_raw:
            legacy_target = _expand_candidate_path(legacy_raw)
            legacy_package_dir = legacy_target / "runtime" / "site-packages" / RAPIDOCR_PACKAGE_NAME
            if legacy_package_dir.exists():
                return legacy_target
    return target


def resolve_rapidocr_runtime_dir(raw_target_dir: str) -> Path:
    target_dir = resolve_rapidocr_install_target(raw_target_dir)
    return target_dir / "runtime" if target_dir else Path()


def resolve_rapidocr_site_packages_dir(raw_target_dir: str) -> Path:
    runtime_dir = resolve_rapidocr_runtime_dir(raw_target_dir)
    return runtime_dir / "site-packages" if runtime_dir else Path()


def resolve_rapidocr_model_cache_dir(raw_target_dir: str) -> Path:
    target_dir = resolve_rapidocr_install_target(raw_target_dir)
    return target_dir / "models" if target_dir else Path()


def _rapidocr_install_state_path(raw_target_dir: str) -> Path:
    target_dir = resolve_rapidocr_install_target(raw_target_dir)
    return target_dir / _INSTALL_STATE_NAME if target_dir else Path()


def rapidocr_selected_model_name(
    *,
    ocr_version: str,
    lang_type: str,
    model_type: str,
) -> str:
    return "/".join(
        [
            str(ocr_version or DEFAULT_RAPIDOCR_OCR_VERSION).strip() or DEFAULT_RAPIDOCR_OCR_VERSION,
            str(lang_type or DEFAULT_RAPIDOCR_LANG_TYPE).strip() or DEFAULT_RAPIDOCR_LANG_TYPE,
            str(model_type or DEFAULT_RAPIDOCR_MODEL_TYPE).strip() or DEFAULT_RAPIDOCR_MODEL_TYPE,
        ]
    )


def _default_install_manifest(
    *,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
) -> dict[str, Any]:
    return {
        "name": "RapidOCR ONNXRuntime",
        "packages": [
            {"name": RAPIDOCR_PACKAGE_NAME, "spec": DEFAULT_RAPIDOCR_PIP_SPEC},
            {"name": "onnxruntime", "spec": DEFAULT_ONNXRUNTIME_PIP_SPEC},
        ],
        "engine_type": engine_type,
        "lang_type": lang_type,
        "model_type": model_type,
        "ocr_version": ocr_version,
    }


def _normalize_sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return text
    return ""


def _package_hash_args(package: dict[str, Any]) -> list[str]:
    hashes: list[str] = []
    seen: set[str] = set()
    for key in ("sha256", "digest", "checksum"):
        digest = _normalize_sha256(package.get(key))
        if digest and digest not in seen:
            seen.add(digest)
            hashes.append(f"--hash=sha256:{digest}")
    hashes_obj = package.get("hashes")
    if isinstance(hashes_obj, list):
        for item in hashes_obj:
            digest = _normalize_sha256(item)
            if digest and digest not in seen:
                seen.add(digest)
                hashes.append(f"--hash=sha256:{digest}")
    return hashes


def _rapidocr_package_install_plan(
    packages_obj: object,
) -> tuple[list[str], list[str], bool]:
    display_specs: list[str] = []
    package_args: list[str] = []
    package_hashes: list[list[str]] = []
    if isinstance(packages_obj, list):
        for item in packages_obj:
            if not isinstance(item, dict):
                continue
            spec = str(item.get("spec") or "").strip()
            if not spec:
                continue
            hashes = _package_hash_args(item)
            display_specs.append(spec)
            package_args.extend([spec, *hashes])
            package_hashes.append(hashes)
    if not display_specs:
        return (
            [DEFAULT_RAPIDOCR_PIP_SPEC, DEFAULT_ONNXRUNTIME_PIP_SPEC],
            [DEFAULT_RAPIDOCR_PIP_SPEC, DEFAULT_ONNXRUNTIME_PIP_SPEC],
            False,
        )
    has_any_hash = any(package_hashes)
    if has_any_hash and not all(package_hashes):
        raise RuntimeError(
            "rapidocr install manifest must include sha256 hashes for every package "
            "when package hashes are used"
        )
    return package_args, display_specs, has_any_hash


def _localized_rapidocr_install_error(
    exc: Exception,
    *,
    phase: str,
    target_dir: Path,
    package_specs: list[str] | None = None,
) -> str:
    packages = ", ".join(package_specs or []) or f"{DEFAULT_RAPIDOCR_PIP_SPEC}, {DEFAULT_ONNXRUNTIME_PIP_SPEC}"
    target_dir_text = str(target_dir) if target_dir else "未解析"

    stderr_text = ""
    if isinstance(exc, subprocess.CalledProcessError):
        stderr_text = str(getattr(exc, "stderr", "") or getattr(exc, "output", "") or "").strip()
    combined_message = " ".join(
        part for part in [stderr_text, str(exc or "").strip()] if part
    ).strip()

    if "No module named pip" in combined_message:
        return (
            "RapidOCR 安装失败：插件当前使用的 Python 运行时缺少 pip 模块，"
            "因此无法下载 OCR 依赖。请先修复该 Python 环境的 pip，"
            "或升级到包含 pip 的 N.E.K.O 运行环境后重试。"
        )
    if "No module named ensurepip" in combined_message:
        return (
            "RapidOCR 安装失败：插件 Python 环境同时缺少 pip 和 ensurepip，"
            "无法自动补齐安装工具。请重建或替换 N.E.K.O 的 Python 运行环境后重试。"
        )
    if "ensurepip" in combined_message and "PermissionError" in combined_message:
        return (
            "RapidOCR 安装失败：插件在自动补齐 pip 时被文件权限拦截，"
            "通常是临时目录或 Python 运行环境目录不可写。"
            "请用管理员权限修复该 Python 环境，或重新安装/重建 N.E.K.O 运行环境后重试。"
        )

    if isinstance(exc, subprocess.TimeoutExpired):
        return (
            "RapidOCR 安装超时：在限定时间内未能完成运行时依赖安装或模型预热。"
            f"安装目录：{target_dir_text}。请检查网络连接和磁盘读写权限后重试。"
        )

    if isinstance(exc, subprocess.CalledProcessError):
        return (
            "RapidOCR 安装失败：插件在安装 OCR 运行时依赖时执行 pip 命令失败。"
            f"目标目录：{target_dir_text}；依赖：{packages}。"
            "常见原因包括无法访问 PyPI、代理或防火墙拦截、安装目录没有写权限，"
            "或者当前 Python 环境中的 pip 不可用。请先检查网络和权限后重试。"
        )

    if isinstance(exc, httpx.HTTPStatusError):
        status_code = getattr(getattr(exc, "response", None), "status_code", "unknown")
        return (
            "RapidOCR 安装失败：获取安装清单时服务器返回了异常状态。"
            f"HTTP 状态码：{status_code}。请稍后重试，或检查安装源地址是否可访问。"
        )

    if isinstance(exc, httpx.RequestError):
        return (
            "RapidOCR 安装失败：获取安装清单时网络请求没有成功完成。"
            "请检查当前网络、代理设置或防火墙策略后重试。"
        )

    if isinstance(exc, PermissionError):
        return (
            "RapidOCR 安装失败：没有权限写入插件隔离安装目录。"
            f"目标目录：{target_dir_text}。请确认目录权限或更换安装位置后重试。"
        )

    message = str(exc or "").strip()
    if "RapidOCR install is only supported on Windows" in message:
        return "RapidOCR 安装失败：当前仅支持在 Windows 上执行自动安装。"
    if "missing RapidOCR install target directory" in message:
        return "RapidOCR 安装失败：没有解析到有效的安装目录，请检查插件配置。"
    if message.startswith("RapidOCR installation is incomplete"):
        return (
            "RapidOCR 安装未完成：运行时依赖已经下载，但初始化校验没有通过。"
            "请重试；如果仍然失败，请查看插件日志中的原始错误详情。"
        )
    if "missing RapidOCR site-packages directory" in message:
        return (
            "RapidOCR 安装失败：依赖安装目录不完整，无法加载插件隔离的 site-packages。"
            "请重试安装。"
        )
    if "RapidOCR runtime class not found" in message:
        return (
            "RapidOCR 安装失败：运行时包已下载，但没有找到可用的 RapidOCR 主类。"
            "这通常表示安装包不完整或版本异常，请重试安装。"
        )

    if phase == "metadata":
        return (
            "RapidOCR 安装失败：准备安装信息时发生异常。"
            "请检查网络或安装清单配置后重试。"
        )
    if phase == "verifying":
        return (
            "RapidOCR 安装失败：运行时预热或模型校验没有通过。"
            "请重试；如果持续失败，请查看插件日志中的原始错误详情。"
        )
    return "RapidOCR 安装失败：安装运行时依赖时发生未知异常，请查看插件日志后重试。"


async def _load_install_manifest(
    *,
    manifest_url: str,
    timeout_seconds: float,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    if not str(manifest_url or "").strip():
        return _default_install_manifest(
            engine_type=engine_type,
            lang_type=lang_type,
            model_type=model_type,
            ocr_version=ocr_version,
        )
    response = await client.get(
        str(manifest_url).strip(),
        headers={
            "Accept": "application/json",
            "User-Agent": "N.E.K.O/galgame_plugin",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("rapidocr install manifest returned an invalid payload")
    return payload


def _purge_modules(prefixes: tuple[str, ...]) -> None:
    for name in list(sys.modules.keys()):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(name, None)


@contextmanager
def _rapidocr_import_context(
    *,
    site_packages_dir: Path,
    model_cache_dir: Path,
) -> Iterator[None]:
    with _RAPIDOCR_IMPORT_CONTEXT_LOCK:
        inserted = False
        old_model_dir = os.environ.get("RAPIDOCR_MODEL_DIR")
        old_model_home = os.environ.get("RAPIDOCR_MODEL_HOME")
        dll_handles: list[Any] = []
        if site_packages_dir:
            site_packages_dir.mkdir(parents=True, exist_ok=True)
            site_path = str(site_packages_dir)
            if site_path not in sys.path:
                sys.path.insert(0, site_path)
                inserted = True
            if hasattr(os, "add_dll_directory"):
                for candidate in (
                    site_packages_dir,
                    site_packages_dir / "onnxruntime",
                    site_packages_dir / "onnxruntime" / "capi",
                ):
                    if candidate.is_dir():
                        try:
                            dll_handles.append(os.add_dll_directory(str(candidate)))
                        except OSError:
                            continue
        if model_cache_dir:
            model_cache_dir.mkdir(parents=True, exist_ok=True)
            os.environ["RAPIDOCR_MODEL_DIR"] = str(model_cache_dir)
            os.environ["RAPIDOCR_MODEL_HOME"] = str(model_cache_dir)
        try:
            yield
        finally:
            for handle in dll_handles:
                try:
                    handle.close()
                except Exception:
                    pass
            if old_model_dir is None:
                os.environ.pop("RAPIDOCR_MODEL_DIR", None)
            else:
                os.environ["RAPIDOCR_MODEL_DIR"] = old_model_dir
            if old_model_home is None:
                os.environ.pop("RAPIDOCR_MODEL_HOME", None)
            else:
                os.environ["RAPIDOCR_MODEL_HOME"] = old_model_home
            if inserted:
                try:
                    sys.path.remove(str(site_packages_dir))
                except ValueError:
                    pass


def _rapidocr_package_dir(raw_target_dir: str) -> Path:
    site_packages_dir = resolve_rapidocr_site_packages_dir(raw_target_dir)
    return site_packages_dir / RAPIDOCR_PACKAGE_NAME if site_packages_dir else Path()


def _build_runtime_constructor_kwargs(
    runtime_class: type[Any],
    *,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
    model_cache_dir: Path,
) -> dict[str, Any]:
    try:
        parameters = inspect.signature(runtime_class).parameters
    except (TypeError, ValueError):
        return {}
    kwargs: dict[str, Any] = {}
    direct_values = {
        "engine_type": engine_type,
        "lang_type": lang_type,
        "model_type": model_type,
        "ocr_version": ocr_version,
        "det_model_type": model_type,
        "cls_model_type": model_type,
        "rec_model_type": model_type,
        "cache_dir": str(model_cache_dir),
        "model_dir": str(model_cache_dir),
        "models_dir": str(model_cache_dir),
        "model_root": str(model_cache_dir),
    }
    for key, value in direct_values.items():
        if key in parameters:
            kwargs[key] = value
    return kwargs


_SESSION_OPTIONS_PATCH_TLS = threading.local()
_SESSION_OPTIONS_PATCH_LOCK = threading.Lock()
_SESSION_OPTIONS_PATCH_INSTALLED = False


def _ensure_session_options_patch_installed() -> None:
    """Patch ort.SessionOptions.__init__ once; the patch only acts on threads that opted in."""
    global _SESSION_OPTIONS_PATCH_INSTALLED
    if _SESSION_OPTIONS_PATCH_INSTALLED:
        return
    with _SESSION_OPTIONS_PATCH_LOCK:
        if _SESSION_OPTIONS_PATCH_INSTALLED:
            return
        try:
            import onnxruntime as _ort
        except Exception:
            return
        options_cls = getattr(_ort, "SessionOptions", None)
        if options_cls is None:
            return
        orig_init = options_cls.__init__

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            orig_init(self, *args, **kwargs)
            cap = getattr(_SESSION_OPTIONS_PATCH_TLS, "cap", None)
            if cap is None:
                return
            intra, inter = cap
            if getattr(self, "intra_op_num_threads", 0) == 0:
                self.intra_op_num_threads = intra
            if getattr(self, "inter_op_num_threads", 0) == 0:
                self.inter_op_num_threads = inter

        options_cls.__init__ = _patched_init
        _SESSION_OPTIONS_PATCH_INSTALLED = True


@contextmanager
def _onnxruntime_intra_op_thread_cap(limit: int) -> Iterator[None]:
    """Clamp SessionOptions thread counts on the calling thread only."""
    _ensure_session_options_patch_installed()
    prev = getattr(_SESSION_OPTIONS_PATCH_TLS, "cap", None)
    _SESSION_OPTIONS_PATCH_TLS.cap = (limit, 1)
    try:
        yield
    finally:
        if prev is None:
            try:
                del _SESSION_OPTIONS_PATCH_TLS.cap
            except AttributeError:
                pass
        else:
            _SESSION_OPTIONS_PATCH_TLS.cap = prev


def load_rapidocr_runtime(
    *,
    install_target_dir_raw: str,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
    force_reload: bool = False,
) -> tuple[Any, dict[str, str]]:
    site_packages_dir = resolve_rapidocr_site_packages_dir(install_target_dir_raw)
    model_cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    if not site_packages_dir:
        raise RuntimeError("missing RapidOCR site-packages directory")
    with _rapidocr_import_context(
        site_packages_dir=site_packages_dir,
        model_cache_dir=model_cache_dir,
    ):
        if force_reload:
            _purge_modules((RAPIDOCR_PACKAGE_NAME,))
        importlib.invalidate_caches()
        module = importlib.import_module(RAPIDOCR_PACKAGE_NAME)
        runtime_class = getattr(module, "RapidOCR", None)
        if runtime_class is None:
            raise RuntimeError("RapidOCR runtime class not found")
        with _onnxruntime_intra_op_thread_cap(_RAPIDOCR_INFERENCE_THREAD_LIMIT):
            runtime = runtime_class(
                **_build_runtime_constructor_kwargs(
                    runtime_class,
                    engine_type=engine_type,
                    lang_type=lang_type,
                    model_type=model_type,
                    ocr_version=ocr_version,
                    model_cache_dir=model_cache_dir,
                )
            )
    metadata = {
        "detected_path": str(Path(getattr(module, "__file__", "")).resolve().parent),
        "model_cache_dir": str(model_cache_dir),
        "selected_model": rapidocr_selected_model_name(
            ocr_version=ocr_version,
            lang_type=lang_type,
            model_type=model_type,
        ),
    }
    return runtime, metadata


def _write_install_state(
    *,
    raw_target_dir: str,
    metadata: dict[str, Any],
) -> None:
    state_path = _rapidocr_install_state_path(raw_target_dir)
    if not state_path:
        return
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def inspect_rapidocr_installation(
    *,
    install_target_dir_raw: str,
    engine_type: str = DEFAULT_RAPIDOCR_ENGINE_TYPE,
    lang_type: str = DEFAULT_RAPIDOCR_LANG_TYPE,
    model_type: str = DEFAULT_RAPIDOCR_MODEL_TYPE,
    ocr_version: str = DEFAULT_RAPIDOCR_OCR_VERSION,
    platform_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checker = platform_fn or is_windows_platform
    supported = bool(checker())
    target_dir = resolve_rapidocr_install_target(install_target_dir_raw)
    runtime_dir = resolve_rapidocr_runtime_dir(install_target_dir_raw)
    site_packages_dir = resolve_rapidocr_site_packages_dir(install_target_dir_raw)
    model_cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    package_dir = _rapidocr_package_dir(install_target_dir_raw)
    install_state_path = _rapidocr_install_state_path(install_target_dir_raw)
    selected_model = rapidocr_selected_model_name(
        ocr_version=ocr_version,
        lang_type=lang_type,
        model_type=model_type,
    )
    detail = "missing"
    detected_path = str(package_dir) if package_dir.exists() else ""
    install_state: dict[str, Any] = {}
    runtime_error = ""

    if supported and install_state_path.is_file():
        try:
            install_state_payload = json.loads(install_state_path.read_text(encoding="utf-8"))
            if isinstance(install_state_payload, dict):
                install_state = install_state_payload
        except (OSError, ValueError, TypeError):
            install_state = {}

    if not supported:
        detail = "unsupported_platform"
    elif not package_dir.exists():
        detail = "missing"
    elif not install_state_path.is_file() or not model_cache_dir.exists():
        detail = "missing_models"
    else:
        try:
            _runtime, runtime_meta = load_rapidocr_runtime(
                install_target_dir_raw=install_target_dir_raw,
                engine_type=engine_type,
                lang_type=lang_type,
                model_type=model_type,
                ocr_version=ocr_version,
                force_reload=False,
            )
            detected_path = str(runtime_meta.get("detected_path") or detected_path)
            detail = "installed"
        except Exception as exc:
            detail = "broken_runtime"
            runtime_error = str(exc)

    installed = detail == "installed"
    return {
        "install_supported": supported,
        "installed": installed,
        "can_install": supported and not installed,
        "detected_path": detected_path,
        "target_dir": str(target_dir) if target_dir else "",
        "runtime_dir": str(runtime_dir) if runtime_dir else "",
        "site_packages_dir": str(site_packages_dir) if site_packages_dir else "",
        "model_cache_dir": str(model_cache_dir) if model_cache_dir else "",
        "selected_model": str(install_state.get("selected_model") or selected_model),
        "engine_type": str(install_state.get("engine_type") or engine_type),
        "lang_type": str(install_state.get("lang_type") or lang_type),
        "model_type": str(install_state.get("model_type") or model_type),
        "ocr_version": str(install_state.get("ocr_version") or ocr_version),
        "detail": detail,
        "runtime_error": runtime_error,
    }


def _blank_test_image() -> Any:
    import numpy as np
    from PIL import Image

    return np.asarray(Image.new("RGB", (64, 32), "white"))


def _warmup_rapidocr(
    *,
    install_target_dir_raw: str,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
) -> dict[str, str]:
    runtime, metadata = load_rapidocr_runtime(
        install_target_dir_raw=install_target_dir_raw,
        engine_type=engine_type,
        lang_type=lang_type,
        model_type=model_type,
        ocr_version=ocr_version,
        force_reload=True,
    )
    test_image = _blank_test_image()
    _ = runtime(test_image)
    return metadata


def _rapidocr_temp_env(*, temp_root: Path) -> dict[str, str]:
    temp_root.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    temp_value = str(temp_root)
    env["TMP"] = temp_value
    env["TEMP"] = temp_value
    env["TMPDIR"] = temp_value
    return env


def _run_subprocess(
    command: list[str],
    *,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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


def _ensure_pip_available(*, timeout_seconds: float, temp_root: Path) -> None:
    env = _rapidocr_temp_env(temp_root=temp_root)
    try:
        _run_subprocess([sys.executable, "-m", "pip", "--version"], timeout_seconds=timeout_seconds, env=env)
        return
    except subprocess.CalledProcessError as exc:
        message = " ".join(
            part for part in [str(getattr(exc, "stderr", "") or "").strip(), str(exc).strip()] if part
        )
        if "No module named pip" not in message:
            raise

    _run_subprocess([sys.executable, "-m", "ensurepip", "--upgrade"], timeout_seconds=timeout_seconds, env=env)
    _run_subprocess([sys.executable, "-m", "pip", "--version"], timeout_seconds=timeout_seconds, env=env)


def _run_pip_install(
    *,
    site_packages_dir: Path,
    packages: list[str],
    timeout_seconds: float,
    require_hashes: bool = False,
) -> None:
    temp_root = site_packages_dir.parent / "tmp"
    env = _rapidocr_temp_env(temp_root=temp_root)
    _ensure_pip_available(timeout_seconds=timeout_seconds, temp_root=temp_root)
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--disable-pip-version-check",
        "--no-warn-script-location",
        "--target",
        str(site_packages_dir),
    ]
    if require_hashes:
        command.append("--require-hashes")
    command.extend(packages)
    _run_subprocess(command, timeout_seconds=timeout_seconds, env=env)


async def install_rapidocr(
    *,
    logger,
    install_target_dir_raw: str,
    manifest_url: str,
    timeout_seconds: float,
    engine_type: str = DEFAULT_RAPIDOCR_ENGINE_TYPE,
    lang_type: str = DEFAULT_RAPIDOCR_LANG_TYPE,
    model_type: str = DEFAULT_RAPIDOCR_MODEL_TYPE,
    ocr_version: str = DEFAULT_RAPIDOCR_OCR_VERSION,
    force: bool = False,
    platform_fn: Callable[[], bool] | None = None,
    client_factory: Callable[[], Awaitable[httpx.AsyncClient] | httpx.AsyncClient] | None = None,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    install_status = inspect_rapidocr_installation(
        install_target_dir_raw=install_target_dir_raw,
        engine_type=engine_type,
        lang_type=lang_type,
        model_type=model_type,
        ocr_version=ocr_version,
        platform_fn=platform_fn,
    )
    if not install_status["install_supported"]:
        raise RuntimeError("RapidOCR install is only supported on Windows")
    if install_status["installed"] and not force:
        result = {
            **install_status,
            "already_installed": True,
            "summary": f"RapidOCR installed: {install_status['detected_path']}",
            "release_name": "RapidOCR ONNXRuntime",
            "asset_name": RAPIDOCR_PACKAGE_NAME,
        }
        if task_id:
            update_install_task_state(
                task_id,
                kind="rapidocr",
                status="completed",
                phase="completed",
                message="RapidOCR is already installed",
                progress=1.0,
                target_dir=str(install_status.get("target_dir") or ""),
                detected_path=str(install_status.get("detected_path") or ""),
            )
        await _emit_progress(
            progress_callback,
            {
                "status": "completed",
                "phase": "completed",
                "message": "RapidOCR is already installed",
                "progress": 1.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "resume_from": 0,
                "target_dir": str(install_status.get("target_dir") or ""),
                "detected_path": str(install_status.get("detected_path") or ""),
                "release_name": "RapidOCR ONNXRuntime",
                "asset_name": RAPIDOCR_PACKAGE_NAME,
            },
        )
        return result

    target_dir = resolve_rapidocr_install_target(install_target_dir_raw)
    if not target_dir:
        raise RuntimeError("missing RapidOCR install target directory")
    runtime_dir = resolve_rapidocr_runtime_dir(install_target_dir_raw)
    site_packages_dir = resolve_rapidocr_site_packages_dir(install_target_dir_raw)
    model_cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)

    if task_id:
        update_install_task_state(
            task_id,
            kind="rapidocr",
            status="running",
            phase="metadata",
            message="Fetching RapidOCR install metadata",
            progress=_compute_phase_progress("metadata"),
            target_dir=str(target_dir),
        )
    await _emit_progress(
        progress_callback,
        {
            "status": "running",
            "phase": "metadata",
            "message": "Fetching RapidOCR install metadata",
            "progress": _compute_phase_progress("metadata"),
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "resume_from": 0,
            "target_dir": str(target_dir),
            "detected_path": "",
            "release_name": "",
            "asset_name": "",
        },
    )

    owned_client = False
    client: httpx.AsyncClient | None = None
    current_phase = "metadata"
    package_specs: list[str] = []
    package_install_args: list[str] = []
    require_package_hashes = False
    install_succeeded = False
    if client_factory is None:
        client = httpx.AsyncClient(
            timeout=timeout_seconds,
            trust_env=True,
            follow_redirects=True,
        )
        owned_client = True
    else:
        maybe_client = client_factory()
        client = await maybe_client if hasattr(maybe_client, "__await__") else maybe_client

    try:
        manifest = await _load_install_manifest(
            manifest_url=manifest_url,
            timeout_seconds=timeout_seconds,
            engine_type=engine_type,
            lang_type=lang_type,
            model_type=model_type,
            ocr_version=ocr_version,
            client=client,
        )
        release_name = str(manifest.get("name") or "RapidOCR ONNXRuntime")
        package_install_args, package_specs, require_package_hashes = (
            _rapidocr_package_install_plan(manifest.get("packages"))
        )
        asset_name = ", ".join(package_specs)

        runtime_dir.mkdir(parents=True, exist_ok=True)
        site_packages_dir.mkdir(parents=True, exist_ok=True)
        model_cache_dir.mkdir(parents=True, exist_ok=True)

        installing_progress = {
            "status": "running",
            "phase": "installing",
            "message": "Installing RapidOCR runtime packages",
            "progress": _compute_phase_progress("installing"),
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "resume_from": 0,
            "target_dir": str(target_dir),
            "detected_path": "",
            "release_name": release_name,
            "asset_name": asset_name,
            "error": "",
        }
        if task_id:
            update_install_task_state(task_id, kind="rapidocr", **installing_progress)
        await _emit_progress(progress_callback, installing_progress)

        current_phase = "installing"
        await asyncio.to_thread(
            _run_pip_install,
            site_packages_dir=site_packages_dir,
            packages=package_install_args,
            timeout_seconds=timeout_seconds,
            require_hashes=require_package_hashes,
        )

        verifying_progress = {
            "status": "running",
            "phase": "verifying",
            "message": "Warming up RapidOCR runtime",
            "progress": _compute_phase_progress("verifying"),
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "resume_from": 0,
            "target_dir": str(target_dir),
            "detected_path": "",
            "release_name": release_name,
            "asset_name": asset_name,
            "error": "",
        }
        if task_id:
            update_install_task_state(task_id, kind="rapidocr", **verifying_progress)
        await _emit_progress(progress_callback, verifying_progress)

        current_phase = "verifying"
        runtime_meta = await asyncio.to_thread(
            _warmup_rapidocr,
            install_target_dir_raw=install_target_dir_raw,
            engine_type=engine_type,
            lang_type=lang_type,
            model_type=model_type,
            ocr_version=ocr_version,
        )
        _write_install_state(
            raw_target_dir=install_target_dir_raw,
            metadata={
                "engine_type": engine_type,
                "lang_type": lang_type,
                "model_type": model_type,
                "ocr_version": ocr_version,
                "selected_model": runtime_meta["selected_model"],
                "detected_path": runtime_meta["detected_path"],
                "model_cache_dir": runtime_meta["model_cache_dir"],
            },
        )

        result_status = inspect_rapidocr_installation(
            install_target_dir_raw=install_target_dir_raw,
            engine_type=engine_type,
            lang_type=lang_type,
            model_type=model_type,
            ocr_version=ocr_version,
            platform_fn=platform_fn,
        )
        if not result_status["installed"]:
            raise RuntimeError(
                "RapidOCR installation is incomplete: "
                + str(result_status.get("detail") or "unknown")
            )
        result = {
            **result_status,
            "already_installed": False,
            "summary": f"RapidOCR installed to {result_status['target_dir']}",
            "release_name": release_name,
            "asset_name": asset_name,
        }
        completed_progress = {
            "status": "completed",
            "phase": "completed",
            "message": "RapidOCR installation completed",
            "progress": 1.0,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "resume_from": 0,
            "target_dir": str(result_status.get("target_dir") or target_dir),
            "detected_path": str(result_status.get("detected_path") or ""),
            "release_name": release_name,
            "asset_name": asset_name,
            "error": "",
        }
        if task_id:
            update_install_task_state(task_id, kind="rapidocr", **completed_progress)
        await _emit_progress(progress_callback, completed_progress)
        install_succeeded = True
        return result
    except Exception as exc:
        if logger is not None:
            logger.exception("RapidOCR install failed during {}: {}", current_phase, exc)
            if isinstance(exc, subprocess.CalledProcessError):
                stdout_text = str(getattr(exc, "stdout", "") or "").strip()
                stderr_text = str(getattr(exc, "stderr", "") or "").strip()
                if stdout_text:
                    logger.error("RapidOCR pip stdout during {}:\n{}", current_phase, stdout_text)
                if stderr_text:
                    logger.error("RapidOCR pip stderr during {}:\n{}", current_phase, stderr_text)
        error_message = _localized_rapidocr_install_error(
            exc,
            phase=current_phase,
            target_dir=target_dir,
            package_specs=package_specs,
        )
        if task_id:
            update_install_task_state(
                task_id,
                kind="rapidocr",
                status="failed",
                phase="failed",
                message=error_message,
                progress=_compute_phase_progress("failed"),
                target_dir=str(target_dir),
                error=error_message,
            )
        await _emit_progress(
            progress_callback,
            {
                "status": "failed",
                "phase": "failed",
                "message": error_message,
                "progress": _compute_phase_progress("failed"),
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "resume_from": 0,
                "target_dir": str(target_dir),
                "detected_path": "",
                "release_name": "",
                "asset_name": "",
                "error": error_message,
            },
        )
        raise RuntimeError(error_message) from exc
    finally:
        if owned_client and client is not None:
            await client.aclose()
        if not install_succeeded:
            try:
                _purge_modules((RAPIDOCR_PACKAGE_NAME,))
            except Exception as exc:
                if logger is not None:
                    logger.warning("RapidOCR module cleanup failed: {}", exc)
