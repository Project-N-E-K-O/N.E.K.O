from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

import httpx

from .install_tasks import update_install_task_state
from .memory_reader import (
    TEXTRACTOR_EXECUTABLE,
    is_windows_platform,
    resolve_textractor_path,
)

DEFAULT_TEXTRACTOR_RELEASE_API_URL = (
    "https://api.github.com/repos/Artikash/Textractor/releases/latest"
)
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_progress(
    progress_callback: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    maybe_awaitable = progress_callback(dict(payload))
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


def _compute_phase_progress(
    phase: str,
    *,
    downloaded_bytes: int = 0,
    total_bytes: int = 0,
) -> float:
    if phase == "metadata":
        return 0.05
    if phase == "downloading":
        if total_bytes > 0:
            ratio = min(1.0, max(0.0, downloaded_bytes / total_bytes))
            return 0.10 + (0.70 * ratio)
        return 0.10
    if phase == "extracting":
        return 0.85
    if phase == "verifying":
        return 0.95
    if phase == "completed":
        return 1.0
    if phase == "failed":
        return 1.0
    return 0.0


def _extract_total_bytes(response: httpx.Response, *, resume_from: int) -> int:
    content_range = response.headers.get("Content-Range", "").strip()
    if "/" in content_range:
        total_part = content_range.rsplit("/", 1)[-1].strip()
        if total_part.isdigit():
            return int(total_part)
    content_length = response.headers.get("Content-Length", "").strip()
    if content_length.isdigit():
        length = int(content_length)
        if response.status_code == 206:
            return resume_from + length
        return length
    return 0


def _normalize_sha256(value: object) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    if len(text) == 64 and all(char in "0123456789abcdef" for char in text):
        return text
    return ""


def _asset_sha256(asset: dict[str, Any]) -> str:
    for key in ("sha256", "digest", "checksum"):
        digest = _normalize_sha256(asset.get(key))
        if digest:
            return digest
    return ""


def _verify_file_sha256(path: Path, expected_sha256: str) -> bool:
    expected = _normalize_sha256(expected_sha256)
    if not expected:
        return False
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            if not chunk:
                break
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"downloaded file checksum mismatch for {path.name}: "
            f"expected sha256 {expected}, got {actual}"
        )
    return True


def default_textractor_install_target_raw() -> str:
    if is_windows_platform():
        return "%LOCALAPPDATA%/Programs/Textractor"
    return ""


def resolve_textractor_install_target(raw_target_dir: str) -> Path:
    normalized = str(raw_target_dir or "").strip() or default_textractor_install_target_raw()
    if not normalized:
        return Path()
    return Path(os.path.expanduser(os.path.expandvars(normalized)))


def inspect_textractor_installation(
    *,
    configured_path: str,
    install_target_dir_raw: str,
    platform_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checker = platform_fn or is_windows_platform
    supported = bool(checker())
    target_dir = resolve_textractor_install_target(install_target_dir_raw)
    expected_executable_path = str(target_dir / TEXTRACTOR_EXECUTABLE) if target_dir else ""
    detected_path = ""
    if supported:
        detected_path = resolve_textractor_path(
            configured_path,
            install_target_dir_raw=install_target_dir_raw,
        )
    installed = bool(detected_path)
    detail = "installed" if installed else "missing"
    if not supported:
        detail = "unsupported_platform"
    return {
        "install_supported": supported,
        "installed": installed,
        "can_install": supported and not installed,
        "detected_path": detected_path,
        "target_dir": str(target_dir) if target_dir else "",
        "expected_executable_path": expected_executable_path,
        "detail": detail,
    }


def _asset_preference(name: str) -> tuple[int, int, str]:
    lowered = name.lower()
    is_64bit = sys.maxsize > 2**32
    score = 0
    if lowered.endswith(".zip"):
        score += 100
    if "textractor" in lowered:
        score += 60
    if "cli" in lowered:
        score += 20
    if is_64bit and any(token in lowered for token in ("x64", "win64", "64-bit", "amd64")):
        score += 15
    if (not is_64bit) and any(token in lowered for token in ("x86", "win32", "32-bit")):
        score += 15
    if "portable" in lowered:
        score += 5
    return (-score, len(lowered), lowered)


def _candidate_assets(release_payload: dict[str, Any]) -> list[dict[str, str]]:
    assets_obj = release_payload.get("assets")
    if not isinstance(assets_obj, list):
        return []
    candidates: list[dict[str, str]] = []
    fallback: list[dict[str, str]] = []
    for asset in assets_obj:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").strip()
        url = str(asset.get("browser_download_url") or "").strip()
        if not name or not url or not name.lower().endswith(".zip"):
            continue
        entry = {"name": name, "url": url}
        expected_sha256 = _asset_sha256(asset)
        if expected_sha256:
            entry["sha256"] = expected_sha256
        lowered = name.lower()
        if "source code" in lowered:
            continue
        fallback.append(entry)
        if "textractor" in lowered:
            candidates.append(entry)
    pool = candidates or fallback
    return sorted(pool, key=lambda item: _asset_preference(item["name"]))


def _safe_extract_archive(archive_path: Path, staging_root: Path) -> Path:
    staging_root_resolved = staging_root.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if not info.filename or info.is_dir():
                continue
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError(f"unsafe archive member: {info.filename}")
            destination = staging_root.joinpath(*member.parts)
            resolved = destination.resolve()
            if not resolved.is_relative_to(staging_root_resolved):
                raise RuntimeError(f"unsafe extraction target: {info.filename}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info, "r") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)

    exe_candidates = sorted(
        staging_root.rglob(TEXTRACTOR_EXECUTABLE),
        key=lambda item: (len(item.parts), str(item).lower()),
    )
    if not exe_candidates:
        raise RuntimeError("archive does not contain TextractorCLI.exe")
    return exe_candidates[0].parent


def _copy_install_tree(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for child in source_dir.iterdir():
        destination = target_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination, dirs_exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, destination)


async def _download_file(
    client: httpx.AsyncClient,
    *,
    url: str,
    destination: Path,
    timeout_seconds: float,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    release_name: str = "",
    asset_name: str = "",
    target_dir: str = "",
    expected_sha256: str = "",
) -> dict[str, int | bool]:
    async def _run_download(*, allow_resume: bool) -> dict[str, int | bool]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        existing_size = destination.stat().st_size if destination.exists() else 0
        request_headers = {
            "Accept": "application/octet-stream",
            "User-Agent": "N.E.K.O/galgame_plugin",
        }
        if allow_resume and existing_size > 0:
            request_headers["Range"] = f"bytes={existing_size}-"

        async with client.stream(
            "GET",
            url,
            headers=request_headers,
            timeout=timeout_seconds,
        ) as response:
            if response.status_code == 416 and allow_resume and existing_size > 0:
                destination.unlink(missing_ok=True)
                return await _run_download(allow_resume=False)

            response.raise_for_status()

            resumed = allow_resume and existing_size > 0 and response.status_code == 206
            resume_from = existing_size if resumed else 0
            total_bytes = _extract_total_bytes(response, resume_from=resume_from)
            open_mode = "ab" if resumed else "wb"
            downloaded_bytes = resume_from
            initial_progress = {
                "status": "running",
                "phase": "downloading",
                "message": f"Downloading {asset_name}",
                "progress": _compute_phase_progress(
                    "downloading",
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                ),
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes,
                "resume_from": resume_from,
                "target_dir": target_dir,
                "detected_path": "",
                "release_name": release_name,
                "asset_name": asset_name,
                "error": "",
            }
            if task_id:
                update_install_task_state(task_id, **initial_progress)
            await _emit_progress(progress_callback, initial_progress)
            last_progress_emit_at = time.monotonic()

            with destination.open(open_mode) as handle:
                async for chunk in response.aiter_bytes():
                    if not chunk:
                        continue
                    handle.write(chunk)
                    downloaded_bytes += len(chunk)
                    now = time.monotonic()
                    should_emit = (
                        total_bytes <= 0
                        or downloaded_bytes >= total_bytes
                        or (now - last_progress_emit_at) >= 0.2
                    )
                    if not should_emit:
                        continue
                    last_progress_emit_at = now
                    chunk_progress = {
                        "status": "running",
                        "phase": "downloading",
                        "message": f"Downloading {asset_name}",
                        "progress": _compute_phase_progress(
                            "downloading",
                            downloaded_bytes=downloaded_bytes,
                            total_bytes=total_bytes,
                        ),
                        "downloaded_bytes": downloaded_bytes,
                        "total_bytes": total_bytes,
                        "resume_from": resume_from,
                        "target_dir": target_dir,
                        "detected_path": "",
                        "release_name": release_name,
                        "asset_name": asset_name,
                        "error": "",
                    }
                    if task_id:
                        update_install_task_state(task_id, **chunk_progress)
                    await _emit_progress(progress_callback, chunk_progress)

            result = {
                "downloaded_bytes": downloaded_bytes,
                "total_bytes": total_bytes if total_bytes > 0 else downloaded_bytes,
                "resume_from": resume_from,
                "resumed": resumed,
                "sha256_verified": False,
            }
            if _normalize_sha256(expected_sha256):
                result["sha256_verified"] = _verify_file_sha256(destination, expected_sha256)
            return result

    return await _run_download(allow_resume=True)


async def install_textractor(
    *,
    logger,
    configured_path: str,
    install_target_dir_raw: str,
    release_api_url: str,
    timeout_seconds: float,
    force: bool = False,
    platform_fn: Callable[[], bool] | None = None,
    client_factory: Callable[[], Awaitable[httpx.AsyncClient] | httpx.AsyncClient] | None = None,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    install_status = inspect_textractor_installation(
        configured_path=configured_path,
        install_target_dir_raw=install_target_dir_raw,
        platform_fn=platform_fn,
    )
    if not install_status["install_supported"]:
        raise RuntimeError("Textractor install is only supported on Windows")
    if install_status["installed"] and not force:
        result = {
            **install_status,
            "already_installed": True,
            "summary": f"Textractor installed: {install_status['detected_path']}",
            "release_name": "",
            "asset_name": "",
        }
        if task_id:
            update_install_task_state(
                task_id,
                status="completed",
                phase="completed",
                message="Textractor is already installed",
                progress=1.0,
                target_dir=str(install_status.get("target_dir") or ""),
                detected_path=str(install_status.get("detected_path") or ""),
            )
        await _emit_progress(
            progress_callback,
            {
                "status": "completed",
                "phase": "completed",
                "message": "Textractor is already installed",
                "progress": 1.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "resume_from": 0,
                "target_dir": str(install_status.get("target_dir") or ""),
                "detected_path": str(install_status.get("detected_path") or ""),
                "release_name": "",
                "asset_name": "",
            },
        )
        return result

    target_dir = resolve_textractor_install_target(install_target_dir_raw)
    if not target_dir:
        raise RuntimeError("missing Textractor install target directory")
    release_endpoint = str(release_api_url or "").strip() or DEFAULT_TEXTRACTOR_RELEASE_API_URL
    if task_id:
        update_install_task_state(
            task_id,
            status="running",
            phase="metadata",
            message="Fetching latest Textractor release metadata",
            progress=_compute_phase_progress("metadata"),
            target_dir=str(target_dir),
        )
    await _emit_progress(
        progress_callback,
        {
            "status": "running",
            "phase": "metadata",
            "message": "Fetching latest Textractor release metadata",
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
        release_response = await client.get(
            release_endpoint,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "N.E.K.O/galgame_plugin",
            },
            timeout=timeout_seconds,
        )
        release_response.raise_for_status()
        release_payload = release_response.json()
        if not isinstance(release_payload, dict):
            raise RuntimeError("release metadata returned an invalid payload")
        assets = _candidate_assets(release_payload)
        if not assets:
            raise RuntimeError("no Textractor zip assets found in release metadata")

        release_name = str(
            release_payload.get("name")
            or release_payload.get("tag_name")
            or release_payload.get("html_url")
            or ""
        )
        errors: list[str] = []
        tmp_dir = Path(tempfile.mkdtemp(prefix="neko-textractor-"))
        try:
            for asset in assets:
                asset_name = asset["name"]
                archive_path = tmp_dir / asset_name
                try:
                    if task_id:
                        update_install_task_state(
                            task_id,
                            status="running",
                            phase="downloading",
                            message=f"Downloading {asset_name}",
                            progress=_compute_phase_progress("downloading"),
                            release_name=release_name,
                            asset_name=asset_name,
                            target_dir=str(target_dir),
                            detected_path="",
                            error="",
                        )
                    await _emit_progress(
                        progress_callback,
                        {
                            "status": "running",
                            "phase": "downloading",
                            "message": f"Downloading {asset_name}",
                            "progress": _compute_phase_progress("downloading"),
                            "downloaded_bytes": 0,
                            "total_bytes": 0,
                            "resume_from": 0,
                            "target_dir": str(target_dir),
                            "detected_path": "",
                            "release_name": release_name,
                            "asset_name": asset_name,
                        },
                    )
                    download_result = await _download_file(
                        client,
                        url=asset["url"],
                        destination=archive_path,
                        timeout_seconds=timeout_seconds,
                        task_id=task_id,
                        progress_callback=progress_callback,
                        release_name=release_name,
                        asset_name=asset_name,
                        target_dir=str(target_dir),
                        expected_sha256=asset.get("sha256", ""),
                    )
                    download_progress = {
                        "status": "running",
                        "phase": "downloading",
                        "message": f"Downloaded {asset_name}",
                        "progress": _compute_phase_progress(
                            "downloading",
                            downloaded_bytes=int(download_result["downloaded_bytes"]),
                            total_bytes=int(download_result["total_bytes"]),
                        ),
                        "downloaded_bytes": int(download_result["downloaded_bytes"]),
                        "total_bytes": int(download_result["total_bytes"]),
                        "resume_from": int(download_result["resume_from"]),
                        "target_dir": str(target_dir),
                        "detected_path": "",
                        "release_name": release_name,
                        "asset_name": asset_name,
                    }
                    if task_id:
                        update_install_task_state(task_id, **download_progress)
                    await _emit_progress(progress_callback, download_progress)

                    extracting_progress = {
                        "status": "running",
                        "phase": "extracting",
                        "message": f"Extracting {asset_name}",
                        "progress": _compute_phase_progress("extracting"),
                        "downloaded_bytes": int(download_result["downloaded_bytes"]),
                        "total_bytes": int(download_result["total_bytes"]),
                        "resume_from": int(download_result["resume_from"]),
                        "target_dir": str(target_dir),
                        "detected_path": "",
                        "release_name": release_name,
                        "asset_name": asset_name,
                    }
                    if task_id:
                        update_install_task_state(task_id, **extracting_progress)
                    await _emit_progress(progress_callback, extracting_progress)
                    extraction_root = tmp_dir / f"extract-{asset_name}"
                    source_dir = await asyncio.to_thread(
                        _safe_extract_archive,
                        archive_path,
                        extraction_root,
                    )
                    install_staging_dir = target_dir.parent / (target_dir.name + ".staging")
                    if install_staging_dir.exists():
                        await asyncio.to_thread(
                            shutil.rmtree, install_staging_dir, ignore_errors=True
                        )
                    await asyncio.to_thread(
                        _copy_install_tree, source_dir, install_staging_dir
                    )

                    verifying_progress = {
                        "status": "running",
                        "phase": "verifying",
                        "message": "Verifying Textractor installation",
                        "progress": _compute_phase_progress("verifying"),
                        "downloaded_bytes": int(download_result["downloaded_bytes"]),
                        "total_bytes": int(download_result["total_bytes"]),
                        "resume_from": int(download_result["resume_from"]),
                        "target_dir": str(target_dir),
                        "detected_path": "",
                        "release_name": release_name,
                        "asset_name": asset_name,
                    }
                    if task_id:
                        update_install_task_state(task_id, **verifying_progress)
                    await _emit_progress(progress_callback, verifying_progress)

                    staging_exe = install_staging_dir / TEXTRACTOR_EXECUTABLE
                    if not staging_exe.is_file():
                        await asyncio.to_thread(
                            shutil.rmtree, install_staging_dir, ignore_errors=True
                        )
                        raise RuntimeError(
                            "TextractorCLI.exe is still missing after extraction"
                        )

                    if target_dir.exists():
                        await asyncio.to_thread(
                            shutil.rmtree, target_dir, ignore_errors=True
                        )
                    await asyncio.to_thread(shutil.move, str(install_staging_dir), str(target_dir))

                    result_status = inspect_textractor_installation(
                        configured_path=configured_path,
                        install_target_dir_raw=install_target_dir_raw,
                        platform_fn=platform_fn,
                    )
                    result = {
                        **result_status,
                        "already_installed": False,
                        "summary": f"Textractor installed to {result_status['detected_path']}",
                        "release_name": release_name,
                        "asset_name": asset_name,
                    }
                    completed_progress = {
                        "status": "completed",
                        "phase": "completed",
                        "message": "Textractor installation completed",
                        "progress": 1.0,
                        "downloaded_bytes": int(download_result["downloaded_bytes"]),
                        "total_bytes": int(download_result["total_bytes"]),
                        "resume_from": int(download_result["resume_from"]),
                        "target_dir": str(result_status.get("target_dir") or target_dir),
                        "detected_path": str(result_status.get("detected_path") or ""),
                        "release_name": release_name,
                        "asset_name": asset_name,
                        "error": "",
                    }
                    if task_id:
                        update_install_task_state(task_id, **completed_progress)
                    await _emit_progress(progress_callback, completed_progress)
                    return result
                except Exception as exc:
                    if logger is not None:
                        logger.warning("Textractor install candidate failed: {} -> {}", asset_name, exc)
                    errors.append(f"{asset_name}: {exc}")
                    continue
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as exc:
                if logger is not None:
                    logger.warning("Textractor temp cleanup failed: {}", exc)
        error_message = "; ".join(errors) or "Textractor install failed"
        if task_id:
            update_install_task_state(
                task_id,
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
                "release_name": release_name,
                "asset_name": "",
                "error": error_message,
            },
        )
        raise RuntimeError(error_message)
    finally:
        if owned_client and client is not None:
            await client.aclose()
