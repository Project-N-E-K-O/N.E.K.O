from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .memory_reader import is_windows_platform

from ._model_registry import (
    DEFAULT_RAPIDOCR_ENGINE_TYPE,
    DEFAULT_RAPIDOCR_LANG_TYPE,
    DEFAULT_RAPIDOCR_MODEL_TYPE,
    DEFAULT_RAPIDOCR_OCR_VERSION,
    RAPIDOCR_PACKAGE_NAME,
    _RAPIDOCR_MODELSCOPE_BASE,
    missing_rapidocr_model_files,
    rapidocr_selected_model_name,
    required_rapidocr_model_files,
)
from ._paths import (
    _rapidocr_install_state_path,
    resolve_rapidocr_install_target,
    resolve_rapidocr_model_cache_dir,
    resolve_rapidocr_runtime_dir,
    resolve_rapidocr_site_packages_dir,
)
from . import _runtime


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
    package_dir = _runtime._rapidocr_package_dir(install_target_dir_raw)
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

    # Legacy install_state.json holds metadata about which model variant the
    # plugin-isolated install picked. Read it as a hint for callers; the bundled
    # path (post-refactor) never writes it, so absence is fine.
    if supported and install_state_path.is_file():
        try:
            install_state_payload = json.loads(install_state_path.read_text(encoding="utf-8"))
            if isinstance(install_state_payload, dict):
                install_state = install_state_payload
        except (OSError, ValueError, TypeError):
            install_state = {}

    # rapidocr-onnxruntime is now bundled into the main program (see
    # pyproject.toml [dependency-groups] galgame). Treat either source as
    # "package present": main interpreter import OR legacy plugin-isolated dir.
    bundled_spec = None
    try:
        bundled_spec = importlib.util.find_spec(RAPIDOCR_PACKAGE_NAME)
    except (ImportError, ValueError):
        bundled_spec = None

    if not supported:
        detail = "unsupported_platform"
    elif bundled_spec is not None:
        # Bundled main-program path: trust find_spec instead of constructing a
        # full RapidOCR runtime (which inits an ONNX session) on every status
        # probe. inspect_*_installation gets called from the bridge poll on a
        # short cache TTL — running ORT init repeatedly would hammer CPU even
        # when OCR is disabled. Real OCR errors will still surface from
        # OcrReaderManager when capture/recognition is actually attempted.
        detail = "installed"
        spec_origin = getattr(bundled_spec, "origin", None) or ""
        if spec_origin:
            detected_path = str(Path(spec_origin).resolve().parent)
        # Non-bundled (ocr_version, lang_type) combos require additional
        # ONNX files that the wheel doesn't ship. Surface that as its own
        # state so the UI can offer an explicit, opt-in download instead
        # of "installed but silently broken at first capture".
        missing = missing_rapidocr_model_files(
            install_target_dir_raw=install_target_dir_raw,
            ocr_version=ocr_version,
            lang_type=lang_type,
            model_type=model_type,
        )
        if missing:
            detail = "missing_model_files"
    elif not package_dir.exists():
        detail = "missing"
    else:
        # Legacy plugin-isolated install: still validated by full runtime load
        # since this path is for upgrade users with potentially-stale installs
        # that may legitimately be broken. Frequency is low (only when bundled
        # path is unavailable AND legacy dir exists).
        try:
            _rapidocr_runtime, runtime_meta = _runtime.load_rapidocr_runtime(
                install_target_dir_raw=install_target_dir_raw,
                engine_type=engine_type,
                lang_type=lang_type,
                model_type=model_type,
                ocr_version=ocr_version,
            )
            detected_path = str(runtime_meta.get("detected_path") or detected_path)
            detail = "installed"
            # Same missing-models check as the bundled branch above. Without
            # this, an upgrade user on a legacy plugin-isolated install with
            # explicit `lang_type=japan` would land on `installed` even when
            # `japan_PP-OCRv4_rec_mobile.onnx` is absent — `can_download_models`
            # would stay False and OCR would silently fall back to the
            # bundled ch model with no UI affordance to fix it.
            legacy_missing = missing_rapidocr_model_files(
                install_target_dir_raw=install_target_dir_raw,
                ocr_version=ocr_version,
                lang_type=lang_type,
                model_type=model_type,
            )
            if legacy_missing:
                detail = "missing_model_files"
        except Exception as exc:
            detail = "broken_runtime"
            runtime_error = str(exc)

    installed = detail == "installed"
    missing_files = missing_rapidocr_model_files(
        install_target_dir_raw=install_target_dir_raw,
        ocr_version=ocr_version,
        lang_type=lang_type,
        model_type=model_type,
    )
    total_size_estimate = sum(int(f.get("size") or 0) for f in missing_files)
    return {
        "install_supported": supported,
        "installed": installed,
        # rapidocr-onnxruntime is now bundled into the main program (see
        # pyproject.toml [dependency-groups] galgame). When it's not importable
        # the user is on a source install without `uv sync --group galgame` —
        # no in-app install action exists anymore (HTTP routes removed in this
        # refactor), so `can_install` stays False to keep the UI button hidden.
        "can_install": False,
        # `can_download_models` is True only when the package is present but
        # the user-selected language pack isn't on disk yet — that's the only
        # condition under which the download UX is meaningful.
        "can_download_models": detail == "missing_model_files",
        "detected_path": detected_path,
        "target_dir": str(target_dir) if target_dir else "",
        "runtime_dir": str(runtime_dir) if runtime_dir else "",
        "site_packages_dir": str(site_packages_dir) if site_packages_dir else "",
        "model_cache_dir": str(model_cache_dir) if model_cache_dir else "",
        "selected_model": selected_model,
        "engine_type": engine_type,
        "lang_type": lang_type,
        "model_type": model_type,
        "ocr_version": ocr_version,
        "detail": detail,
        "runtime_error": runtime_error,
        "missing_model_files": missing_files,
        "missing_model_total_size": total_size_estimate,
        "model_download_source": _RAPIDOCR_MODELSCOPE_BASE,
    }


# ====== Model download ======

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_model_progress(
    progress_callback: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    maybe = progress_callback(dict(payload))
    if inspect.isawaitable(maybe):
        await maybe


def _verify_model_sha256(path: Path, expected_sha256: str) -> None:
    expected = (expected_sha256 or "").strip().lower()
    if not expected:
        return
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"downloaded model checksum mismatch for {path.name}: expected {expected}, got {actual}"
        )

async def download_rapidocr_models(
    *,
    logger,
    install_target_dir_raw: str,
    ocr_version: str,
    lang_type: str,
    model_type: str = DEFAULT_RAPIDOCR_MODEL_TYPE,
    timeout_seconds: float = 180.0,
    force: bool = False,
    task_id: str | None = None,
    plugin_id: str = "galgame_plugin",
    progress_callback: ProgressCallback | None = None,
    before_completed_callback: Callable[[], Awaitable[None] | None] | None = None,
) -> dict[str, Any]:
    """Download all model files required for the (ocr_version, lang_type) selection.

    Bundled (PP-OCRv4 + ch) is a no-op. Otherwise downloads each missing file
    from the original ModelScope URL in the model registry.
    Files are written into model_cache_dir, verified with SHA256, and reported
    through progress events.
    Failures preserve specific error text (HTTP status, timeout, network) so
    the UI can show actionable copy.
    """
    from .install_tasks import update_install_task_state  # local import: avoid cycle

    async def _before_completed() -> None:
        if before_completed_callback is None:
            return
        result = before_completed_callback()
        if inspect.isawaitable(result):
            await result

    async def _before_completed_safely() -> None:
        try:
            await _before_completed()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("failed to run rapidocr_models completion callback", exc_info=True)

    cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    if not cache_dir:
        raise RuntimeError("missing RapidOCR model cache directory")
    cache_dir.mkdir(parents=True, exist_ok=True)

    required = required_rapidocr_model_files(
        install_target_dir_raw=install_target_dir_raw,
        ocr_version=ocr_version,
        lang_type=lang_type,
        model_type=model_type,
    )
    if not required:
        await _before_completed_safely()
        if task_id:
            update_install_task_state(
                task_id,
                kind="rapidocr_models",
                plugin_id=plugin_id,
                status="completed",
                phase="completed",
                message="No download needed for bundled ch + PP-OCRv4 models",
                progress=1.0,
                target_dir=str(cache_dir),
            )
        await _emit_model_progress(
            progress_callback,
            {
                "status": "completed",
                "phase": "completed",
                "message": "No download needed for bundled ch + PP-OCRv4 models",
                "progress": 1.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "target_dir": str(cache_dir),
            },
        )
        return {"downloaded": [], "skipped_bundled": True, "target_dir": str(cache_dir)}

    pending = required if force else [
        spec for spec in required
        if not (spec["target_path"] and Path(spec["target_path"]).is_file())
    ]
    total_bytes = sum(int(spec.get("size") or 0) for spec in pending)

    if not pending:
        already_present_message = "All required RapidOCR models already on disk"
        await _before_completed_safely()
        if task_id:
            update_install_task_state(
                task_id,
                kind="rapidocr_models",
                plugin_id=plugin_id,
                status="completed",
                phase="completed",
                message=already_present_message,
                progress=1.0,
                target_dir=str(cache_dir),
            )
        # Emit a streaming completion event too — the bundled-no-op branch
        # above does this; the cache-hit branch was missing it, so SSE
        # subscribers stayed in `running` until timeout when a re-trigger
        # found everything already on disk.
        await _emit_model_progress(
            progress_callback,
            {
                "status": "completed",
                "phase": "completed",
                "message": already_present_message,
                "progress": 1.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "target_dir": str(cache_dir),
            },
        )
        return {"downloaded": [], "already_present": True, "target_dir": str(cache_dir)}

    downloaded_bytes = 0
    downloaded: list[str] = []
    downloaded_sources: dict[str, str] = {}
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        trust_env=True,
        follow_redirects=True,
    ) as client:
        for index, spec in enumerate(pending):
            asset_name = spec["name"]
            destination = Path(spec["target_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = destination.with_suffix(destination.suffix + ".part")
            source = "modelscope"
            url = str(spec["url"])
            headers = {
                "Accept": "application/octet-stream",
                "User-Agent": "N.E.K.O/galgame_plugin",
            }
            source_label = "ModelScope"
            running_message = (
                f"Downloading {asset_name} from {source_label} "
                f"({index + 1}/{len(pending)})"
            )
            if task_id:
                update_install_task_state(
                    task_id,
                    kind="rapidocr_models",
                    plugin_id=plugin_id,
                    status="running",
                    phase="downloading",
                    message=running_message,
                    progress=(downloaded_bytes / total_bytes) if total_bytes else 0.0,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    target_dir=str(cache_dir),
                    asset_name=asset_name,
                    source=source,
                )
            await _emit_model_progress(
                progress_callback,
                {
                    "status": "running",
                    "phase": "downloading",
                    "message": running_message,
                    "progress": (downloaded_bytes / total_bytes) if total_bytes else 0.0,
                    "downloaded_bytes": downloaded_bytes,
                    "total_bytes": total_bytes,
                    "target_dir": str(cache_dir),
                    "asset_name": asset_name,
                    "source": source,
                },
            )

            tmp_path.unlink(missing_ok=True)
            try:
                async with client.stream("GET", url, headers=headers) as response:
                    response.raise_for_status()
                    asset_total = int(response.headers.get("Content-Length") or spec.get("size") or 0)
                    asset_downloaded = 0
                    last_emit = 0.0
                    with tmp_path.open("wb") as fh:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            asset_downloaded += len(chunk)
                            now = downloaded_bytes + asset_downloaded
                            # Throttle progress emission to ~1% steps to keep
                            # the SSE stream cheap.
                            if total_bytes and (now - last_emit) > max(64 * 1024, total_bytes // 100):
                                last_emit = float(now)
                                if task_id:
                                    update_install_task_state(
                                        task_id,
                                        kind="rapidocr_models",
                                        plugin_id=plugin_id,
                                        status="running",
                                        phase="downloading",
                                        message=running_message,
                                        progress=(now / total_bytes) if total_bytes else 0.0,
                                        downloaded_bytes=now,
                                        total_bytes=total_bytes,
                                        target_dir=str(cache_dir),
                                        asset_name=asset_name,
                                        source=source,
                                    )
                                await _emit_model_progress(
                                    progress_callback,
                                    {
                                        "status": "running",
                                        "phase": "downloading",
                                        "message": running_message,
                                        "progress": (now / total_bytes) if total_bytes else 0.0,
                                        "downloaded_bytes": now,
                                        "total_bytes": total_bytes,
                                        "target_dir": str(cache_dir),
                                        "asset_name": asset_name,
                                        "source": source,
                                    },
                                )
                _verify_model_sha256(tmp_path, str(spec.get("sha256") or ""))
                # Path.replace = os.replace, unconditionally overwrites the
                # destination atomically on both POSIX and Windows (Python
                # 3.3+). The previous explicit unlink-then-replace created a
                # race window where the destination briefly didn't exist
                # — load_rapidocr_runtime / inspect_rapidocr_installation
                # could observe the file as missing during force=True
                # re-downloads. The atomic replace covers both new-file and
                # overwrite cases.
                tmp_path.replace(destination)
                downloaded_bytes += int(spec.get("size") or asset_downloaded)
                downloaded.append(asset_name)
                downloaded_sources[asset_name] = source
            except Exception as exc:  # noqa: BLE001 - report the configured source
                tmp_path.unlink(missing_ok=True)
                logger.warning(
                    "failed to download RapidOCR model %s from %s",
                    asset_name,
                    source_label,
                    exc_info=True,
                )
                err_message = (
                    f"failed to download {asset_name} from {source_label}: "
                    f"{type(exc).__name__}: {exc}"
                )
                # Without these, the SSE stream and persisted task state stay in
                # `running` until the client times out; the user sees a download
                # that "never finishes" instead of an explicit failure.
                if task_id:
                    try:
                        update_install_task_state(
                            task_id,
                            kind="rapidocr_models",
                            plugin_id=plugin_id,
                            status="failed",
                            phase="failed",
                            message=err_message,
                            progress=(downloaded_bytes / total_bytes) if total_bytes else 0.0,
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=total_bytes,
                            target_dir=str(cache_dir),
                            asset_name=asset_name,
                            error=err_message,
                        )
                    except Exception:
                        logger.warning("failed to persist rapidocr_models failure state", exc_info=True)
                try:
                    await _emit_model_progress(
                        progress_callback,
                        {
                            "status": "failed",
                            "phase": "failed",
                            "message": err_message,
                            "error": err_message,
                            "progress": (downloaded_bytes / total_bytes) if total_bytes else 0.0,
                            "downloaded_bytes": downloaded_bytes,
                            "total_bytes": total_bytes,
                            "target_dir": str(cache_dir),
                            "asset_name": asset_name,
                        },
                    )
                except Exception:
                    logger.warning("failed to emit rapidocr_models failure progress", exc_info=True)
                raise RuntimeError(err_message) from exc

    await _before_completed_safely()

    if task_id:
        update_install_task_state(
            task_id,
            kind="rapidocr_models",
            plugin_id=plugin_id,
            status="completed",
            phase="completed",
            message=f"Downloaded {len(downloaded)} model file(s)",
            progress=1.0,
            downloaded_bytes=total_bytes,
            total_bytes=total_bytes,
            target_dir=str(cache_dir),
        )
    await _emit_model_progress(
        progress_callback,
        {
            "status": "completed",
            "phase": "completed",
            "message": f"Downloaded {len(downloaded)} model file(s)",
            "progress": 1.0,
            "downloaded_bytes": total_bytes,
            "total_bytes": total_bytes,
            "target_dir": str(cache_dir),
        },
    )
    result: dict[str, Any] = {
        "downloaded": downloaded,
        "target_dir": str(cache_dir),
        "sources": downloaded_sources,
    }
    unique_sources = set(downloaded_sources.values())
    if len(unique_sources) == 1:
        result["source"] = next(iter(unique_sources))
    return result
