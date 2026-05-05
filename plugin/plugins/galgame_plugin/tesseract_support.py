from __future__ import annotations

import asyncio
import hashlib
import inspect
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .install_tasks import update_install_task_state
from .memory_reader import is_windows_platform

TESSERACT_EXECUTABLE = "tesseract.exe"
DEFAULT_TESSERACT_LANGUAGES = "chi_sim+jpn+eng"
DEFAULT_TESSDATA_COMMIT = "87416418657359cb625c412a48b6e1d6d41c29bd"
DEFAULT_TESSDATA_BASE_URL = (
    "https://cdn.jsdelivr.net/gh/tesseract-ocr/"
    f"tessdata_fast@{DEFAULT_TESSDATA_COMMIT}"
)
DEFAULT_TESSERACT_INSTALLER_URL = (
    "https://ghproxy.com/https://github.com/UB-Mannheim/tesseract/"
    "releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
)
DEFAULT_TESSERACT_INSTALLER_SHA256 = (
    "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
)
DEFAULT_TESSDATA_SHA256 = {
    "chi_sim": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
    "jpn": "1f5de9236d2e85f5fdf4b3c500f2d4926f8d9449f28f5394472d9e8d83b91b4d",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}
ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def _required_languages(languages: str) -> list[str]:
    items = [str(item).strip() for item in str(languages or "").split("+")]
    normalized = [item for item in items if item]
    return normalized or ["chi_sim", "jpn", "eng"]


def _expand_candidate_path(raw_path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(raw_path)))


def _candidate_path_from_env(env_name: str, *parts: str) -> Path | None:
    base = str(os.getenv(env_name) or "").strip()
    if not base:
        return None
    return Path(base).joinpath(*parts)


def _iter_tesseract_candidates(
    configured_path: str,
    *,
    install_target_dir_raw: str = "",
    prioritize_install_target: bool = False,
) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(candidate: Path | None) -> None:
        if candidate is None:
            return
        key = os.path.normcase(str(candidate))
        if key in seen:
            return
        seen.add(key)
        candidates.append(candidate)

    if prioritize_install_target:
        install_target_dir = str(install_target_dir_raw or "").strip()
        if install_target_dir:
            _add(_expand_candidate_path(f"{install_target_dir}/{TESSERACT_EXECUTABLE}"))

    configured = str(configured_path or "").strip()
    if configured:
        _add(_expand_candidate_path(configured))

    if not prioritize_install_target:
        install_target_dir = str(install_target_dir_raw or "").strip()
        if install_target_dir:
            _add(_expand_candidate_path(f"{install_target_dir}/{TESSERACT_EXECUTABLE}"))

    path_hit = shutil.which(TESSERACT_EXECUTABLE)
    if path_hit:
        _add(Path(path_hit))

    _add(
        _candidate_path_from_env(
            "LOCALAPPDATA",
            "Programs",
            "N.E.K.O",
            "Tesseract-OCR",
            TESSERACT_EXECUTABLE,
        )
    )
    _add(
        _candidate_path_from_env(
            "LOCALAPPDATA",
            "Programs",
            "Tesseract-OCR",
            TESSERACT_EXECUTABLE,
        )
    )
    _add(
        _candidate_path_from_env(
            "ProgramFiles",
            "Tesseract-OCR",
            TESSERACT_EXECUTABLE,
        )
    )
    _add(
        _candidate_path_from_env(
            "ProgramFiles(x86)",
            "Tesseract-OCR",
            TESSERACT_EXECUTABLE,
        )
    )
    return candidates


def resolve_tesseract_path(configured_path: str, *, install_target_dir_raw: str = "", prioritize_install_target: bool = False) -> str:
    for candidate in _iter_tesseract_candidates(
        configured_path,
        install_target_dir_raw=install_target_dir_raw,
        prioritize_install_target=prioritize_install_target,
    ):
        if candidate.is_file():
            return str(candidate)
    return ""


def default_tesseract_install_target_raw() -> str:
    if is_windows_platform():
        return "%LOCALAPPDATA%/Programs/N.E.K.O/Tesseract-OCR"
    return ""


def resolve_tesseract_install_target(raw_target_dir: str) -> Path:
    normalized = str(raw_target_dir or "").strip() or default_tesseract_install_target_raw()
    if not normalized:
        return Path()
    return Path(os.path.expanduser(os.path.expandvars(normalized)))


def resolve_tessdata_dir(executable_path: str) -> Path:
    if not executable_path:
        return Path()
    return Path(executable_path).resolve().parent / "tessdata"


def inspect_tesseract_installation(
    *,
    configured_path: str,
    install_target_dir_raw: str,
    languages: str = DEFAULT_TESSERACT_LANGUAGES,
    platform_fn: Callable[[], bool] | None = None,
    prioritize_install_target: bool = False,
) -> dict[str, Any]:
    checker = platform_fn or is_windows_platform
    supported = bool(checker())
    target_dir = resolve_tesseract_install_target(install_target_dir_raw)
    expected_executable_path = str(target_dir / TESSERACT_EXECUTABLE) if target_dir.parts else ""
    detected_path = ""
    tessdata_dir = Path()
    missing_languages = _required_languages(languages)
    if supported:
        detected_path = resolve_tesseract_path(
            configured_path,
            install_target_dir_raw=install_target_dir_raw,
            prioritize_install_target=prioritize_install_target,
        )
        if detected_path:
            tessdata_dir = resolve_tessdata_dir(detected_path)
            missing_languages = [
                language
                for language in _required_languages(languages)
                if not (tessdata_dir / f"{language}.traineddata").is_file()
            ]
    installed = bool(detected_path) and not missing_languages
    detail = "installed" if installed else "missing"
    if detected_path and missing_languages:
        detail = "missing_languages"
    if not supported:
        detail = "unsupported_platform"
    return {
        "install_supported": supported,
        "installed": installed,
        "can_install": supported and not installed,
        "detected_path": detected_path,
        "target_dir": str(target_dir) if target_dir.parts else "",
        "expected_executable_path": expected_executable_path,
        "tessdata_dir": str(tessdata_dir) if tessdata_dir else "",
        "required_languages": _required_languages(languages),
        "missing_languages": missing_languages,
        "detail": detail,
    }


def _default_install_manifest(languages: str) -> dict[str, Any]:
    required_languages = _required_languages(languages)
    language_assets: list[dict[str, str]] = []
    for language in required_languages:
        asset = {
            "name": f"{language}.traineddata",
            "url": f"{DEFAULT_TESSDATA_BASE_URL}/{language}.traineddata",
        }
        sha256 = DEFAULT_TESSDATA_SHA256.get(language)
        if sha256:
            asset["sha256"] = sha256
        language_assets.append(asset)
    return {
        "name": "Tesseract OCR for Windows",
        "installer": {
            "name": "tesseract-ocr-w64-setup-5.4.0.20240606.exe",
            "url": DEFAULT_TESSERACT_INSTALLER_URL,
            "sha256": DEFAULT_TESSERACT_INSTALLER_SHA256,
            "silent_args": [
                "/VERYSILENT",
                "/SUPPRESSMSGBOXES",
                "/NORESTART",
                "/SP-",
            ],
        },
        "languages": language_assets,
    }


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
            return 0.10 + (0.50 * ratio)
        return 0.10
    if phase == "installing":
        return 0.72
    if phase == "languages":
        return 0.85
    if phase == "verifying":
        return 0.95
    if phase in {"completed", "failed"}:
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


async def _download_file(
    client: httpx.AsyncClient,
    *,
    url: str,
    destination: Path,
    timeout_seconds: float,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    phase: str = "downloading",
    message: str = "",
    target_dir: str = "",
    asset_name: str = "",
    release_name: str = "",
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
                "phase": phase,
                "message": message,
                "progress": _compute_phase_progress(
                    phase,
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
                update_install_task_state(task_id, kind="tesseract", **initial_progress)
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
                        "phase": phase,
                        "message": message,
                        "progress": _compute_phase_progress(
                            phase,
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
                        update_install_task_state(task_id, kind="tesseract", **chunk_progress)
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


async def _load_install_manifest(
    *,
    manifest_url: str,
    timeout_seconds: float,
    languages: str,
    client: httpx.AsyncClient,
) -> dict[str, Any]:
    if not str(manifest_url or "").strip():
        return _default_install_manifest(languages)
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
        raise RuntimeError("tesseract install manifest returned an invalid payload")
    return payload


def _run_tesseract_installer(installer_path: Path, target_dir: Path, silent_args: list[str], timeout_seconds: float) -> None:
    command = [str(installer_path), *silent_args, f"/DIR={target_dir}"]
    subprocess.run(
        command,
        check=True,
        timeout=timeout_seconds,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


async def install_tesseract(
    *,
    logger,
    configured_path: str,
    install_target_dir_raw: str,
    manifest_url: str,
    timeout_seconds: float,
    languages: str = DEFAULT_TESSERACT_LANGUAGES,
    force: bool = False,
    platform_fn: Callable[[], bool] | None = None,
    client_factory: Callable[[], Awaitable[httpx.AsyncClient] | httpx.AsyncClient] | None = None,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    install_status = inspect_tesseract_installation(
        configured_path=configured_path,
        install_target_dir_raw=install_target_dir_raw,
        languages=languages,
        platform_fn=platform_fn,
    )
    if not install_status["install_supported"]:
        raise RuntimeError("Tesseract install is only supported on Windows")
    if install_status["installed"] and not force:
        result = {
            **install_status,
            "already_installed": True,
            "summary": f"Tesseract installed: {install_status['detected_path']}",
            "release_name": "",
            "asset_name": "",
        }
        if task_id:
            update_install_task_state(
                task_id,
                kind="tesseract",
                status="completed",
                phase="completed",
                message="Tesseract is already installed",
                progress=1.0,
                target_dir=str(install_status.get("target_dir") or ""),
                detected_path=str(install_status.get("detected_path") or ""),
            )
        await _emit_progress(
            progress_callback,
            {
                "status": "completed",
                "phase": "completed",
                "message": "Tesseract is already installed",
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

    target_dir = resolve_tesseract_install_target(install_target_dir_raw)
    if not target_dir:
        raise RuntimeError("missing Tesseract install target directory")

    if task_id:
        update_install_task_state(
            task_id,
            kind="tesseract",
            status="running",
            phase="metadata",
            message="Fetching Tesseract install metadata",
            progress=_compute_phase_progress("metadata"),
            target_dir=str(target_dir),
        )
    await _emit_progress(
        progress_callback,
        {
            "status": "running",
            "phase": "metadata",
            "message": "Fetching Tesseract install metadata",
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
        manifest = await _load_install_manifest(
            manifest_url=manifest_url,
            timeout_seconds=timeout_seconds,
            languages=languages,
            client=client,
        )
        release_name = str(manifest.get("name") or "Tesseract OCR")
        installer = manifest.get("installer")
        installer_obj = installer if isinstance(installer, dict) else {}
        installer_url = str(installer_obj.get("url") or "").strip()
        installer_name = str(installer_obj.get("name") or Path(installer_url).name or "tesseract-installer.exe").strip()
        if not installer_url:
            raise RuntimeError("tesseract install manifest is missing installer.url")
        silent_args = [
            str(item).strip()
            for item in installer_obj.get("silent_args", [])
            if str(item).strip()
        ]
        if not silent_args:
            silent_args = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"]
        languages_obj = manifest.get("languages")
        language_assets = languages_obj if isinstance(languages_obj, list) else []

        tmp_dir = Path(tempfile.mkdtemp(prefix="neko-tesseract-"))
        try:
            installer_path = tmp_dir / installer_name
            installer_download = await _download_file(
                client,
                url=installer_url,
                destination=installer_path,
                timeout_seconds=timeout_seconds,
                task_id=task_id,
                progress_callback=progress_callback,
                phase="downloading",
                message=f"Downloading {installer_name}",
                target_dir=str(target_dir),
                asset_name=installer_name,
                release_name=release_name,
                expected_sha256=_asset_sha256(installer_obj),
            )

            installing_progress = {
                "status": "running",
                "phase": "installing",
                "message": "Running Tesseract installer",
                "progress": _compute_phase_progress("installing"),
                "downloaded_bytes": int(installer_download["downloaded_bytes"]),
                "total_bytes": int(installer_download["total_bytes"]),
                "resume_from": int(installer_download["resume_from"]),
                "target_dir": str(target_dir),
                "detected_path": "",
                "release_name": release_name,
                "asset_name": installer_name,
                "error": "",
            }
            if task_id:
                update_install_task_state(task_id, kind="tesseract", **installing_progress)
            await _emit_progress(progress_callback, installing_progress)
            await asyncio.to_thread(
                _run_tesseract_installer,
                installer_path,
                target_dir,
                silent_args,
                timeout_seconds,
            )

            tessdata_dir = target_dir / "tessdata"
            tessdata_dir.mkdir(parents=True, exist_ok=True)
            for language_asset in language_assets:
                if not isinstance(language_asset, dict):
                    continue
                asset_name = str(language_asset.get("name") or "").strip()
                asset_url = str(language_asset.get("url") or "").strip()
                if not asset_name or not asset_url:
                    continue
                await _download_file(
                    client,
                    url=asset_url,
                    destination=tessdata_dir / asset_name,
                    timeout_seconds=timeout_seconds,
                    task_id=task_id,
                    progress_callback=progress_callback,
                    phase="languages",
                    message=f"Downloading {asset_name}",
                    target_dir=str(target_dir),
                    asset_name=asset_name,
                    release_name=release_name,
                    expected_sha256=_asset_sha256(language_asset),
                )

            verifying_progress = {
                "status": "running",
                "phase": "verifying",
                "message": "Verifying Tesseract installation",
                "progress": _compute_phase_progress("verifying"),
                "downloaded_bytes": int(installer_download["downloaded_bytes"]),
                "total_bytes": int(installer_download["total_bytes"]),
                "resume_from": int(installer_download["resume_from"]),
                "target_dir": str(target_dir),
                "detected_path": "",
                "release_name": release_name,
                "asset_name": installer_name,
                "error": "",
            }
            if task_id:
                update_install_task_state(task_id, kind="tesseract", **verifying_progress)
            await _emit_progress(progress_callback, verifying_progress)

            result_status = inspect_tesseract_installation(
                configured_path=configured_path,
                install_target_dir_raw=install_target_dir_raw,
                languages=languages,
                platform_fn=platform_fn,
                prioritize_install_target=True,
            )
            if not result_status["installed"]:
                raise RuntimeError(
                    "Tesseract installation is incomplete: "
                    + str(result_status.get("detail") or "unknown")
                )
            result = {
                **result_status,
                "already_installed": False,
                "summary": f"Tesseract installed to {result_status['detected_path']}",
                "release_name": release_name,
                "asset_name": installer_name,
            }
            completed_progress = {
                "status": "completed",
                "phase": "completed",
                "message": "Tesseract installation completed",
                "progress": 1.0,
                "downloaded_bytes": int(installer_download["downloaded_bytes"]),
                "total_bytes": int(installer_download["total_bytes"]),
                "resume_from": int(installer_download["resume_from"]),
                "target_dir": str(result_status.get("target_dir") or target_dir),
                "detected_path": str(result_status.get("detected_path") or ""),
                "release_name": release_name,
                "asset_name": installer_name,
                "error": "",
            }
            if task_id:
                update_install_task_state(task_id, kind="tesseract", **completed_progress)
            await _emit_progress(progress_callback, completed_progress)
            return result
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception as exc:
                if logger is not None:
                    logger.warning("Tesseract temp cleanup failed: {}", exc)
    except Exception as exc:
        error_message = str(exc)
        if task_id:
            update_install_task_state(
                task_id,
                kind="tesseract",
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
        raise
    finally:
        if owned_client and client is not None:
            await client.aclose()
