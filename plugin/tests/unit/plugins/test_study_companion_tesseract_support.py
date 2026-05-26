from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
from PIL import Image
import pytest

from plugin.plugins.study_companion import tesseract_support as tess

pytestmark = pytest.mark.unit


def test_tesseract_path_resolution_and_install_inspection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "Tesseract-OCR" / tess.TESSERACT_EXECUTABLE
    tessdata = exe.parent / "tessdata"
    tessdata.mkdir(parents=True)
    exe.write_text("fake", encoding="utf-8")
    (tessdata / "eng.traineddata").write_text("fake", encoding="utf-8")
    monkeypatch.setattr(tess.shutil, "which", lambda name: "")

    detected = tess.resolve_tesseract_path(str(exe))
    status = tess.inspect_tesseract_installation(
        configured_path=str(exe),
        install_target_dir_raw="",
        languages="eng+jpn",
        platform_fn=lambda: True,
    )
    unsupported = tess.inspect_tesseract_installation(
        configured_path=str(exe),
        install_target_dir_raw="",
        languages="eng",
        platform_fn=lambda: False,
    )

    assert detected == str(exe)
    assert status["installed"] is False
    assert status["detail"] == "missing_languages"
    assert status["missing_languages"] == ["jpn"]
    assert unsupported["detail"] == "unsupported_platform"


def test_tesseract_manifest_progress_headers_and_hash_helpers(tmp_path: Path) -> None:
    manifest = tess._default_install_manifest("eng+custom")
    response = httpx.Response(
        206,
        headers={"Content-Range": "bytes 5-9/20", "Content-Length": "5"},
    )
    payload = b"payload"
    target = tmp_path / "asset.bin"
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    assert [item["name"] for item in manifest["languages"]] == [
        "eng.traineddata",
        "custom.traineddata",
    ]
    assert tess._compute_phase_progress("metadata") == 0.05
    assert tess._compute_phase_progress("downloading", downloaded_bytes=5, total_bytes=10) == 0.35
    assert tess._compute_phase_progress("unknown") == 0.0
    assert tess._extract_total_bytes(response, resume_from=5) == 20
    assert tess._normalize_sha256(f"sha256:{digest.upper()}") == digest
    assert tess._asset_sha256({"checksum": digest}) == digest
    assert tess._verify_file_sha256(target, digest) is True

    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"bad")
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        tess._verify_file_sha256(bad, digest)
    assert not bad.exists()


@pytest.mark.asyncio
async def test_tesseract_progress_callbacks_support_sync_and_async() -> None:
    sync_events: list[dict[str, Any]] = []
    async_events: list[dict[str, Any]] = []

    def sync_callback(payload: dict[str, Any]) -> None:
        sync_events.append(payload)

    async def async_callback(payload: dict[str, Any]) -> None:
        async_events.append(payload)

    await tess._emit_progress(sync_callback, {"phase": "sync"})
    await tess._emit_install_progress(
        async_callback,
        {"phase": "async"},
        task_id="task",
        plugin_id="study",
    )

    assert sync_events == [{"phase": "sync"}]
    assert async_events == [{"phase": "async", "task_id": "task", "plugin_id": "study"}]


@pytest.mark.asyncio
async def test_tesseract_install_short_circuits_existing_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exe = tmp_path / "tesseract.exe"
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    exe.write_text("fake", encoding="utf-8")
    (tessdata / "eng.traineddata").write_text("fake", encoding="utf-8")
    monkeypatch.setattr(tess.shutil, "which", lambda name: "")
    events: list[dict[str, Any]] = []

    result = await tess.install_tesseract(
        logger=None,
        configured_path=str(exe),
        install_target_dir_raw="",
        manifest_url="",
        timeout_seconds=1.0,
        languages="eng",
        platform_fn=lambda: True,
        progress_callback=lambda payload: events.append(payload),
    )

    assert result["already_installed"] is True
    assert events[-1]["phase"] == "completed"
    assert events[-1]["detected_path"] == str(exe)


def test_tesseract_image_preparation_scoring_and_temporary_command(monkeypatch: pytest.MonkeyPatch) -> None:
    image = Image.new("RGB", (20, 10), "white")
    prepared = tess._prepare_ocr_image(image)
    score = tess._score_ocr_text("abc 猫")
    pytesseract = SimpleNamespace(
        pytesseract=SimpleNamespace(tesseract_cmd="original")
    )

    with tess._temporary_tesseract_cmd(pytesseract, "custom"):
        assert pytesseract.pytesseract.tesseract_cmd == "custom"

    assert prepared.mode == "L"
    assert prepared.size == (60, 30)
    assert score[0] > 0 and score[1] == 1
    assert pytesseract.pytesseract.tesseract_cmd == "original"
