from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODEL_DIRECTORY = ROOT / "main_logic" / "voice_identity" / "pvad" / "models"
PVAD_SHA256 = "2114fd3c3fa87b560eaf4cad6a6e1a0a73aefba08da05521a27bfe2382ef4bdd"
PACKAGED_MODEL_ARGUMENT = (
    "--include-data-dir=main_logic/voice_identity/pvad/models="
    "main_logic/voice_identity/pvad/models"
)


def test_bundled_pvad_has_pinned_bytes_and_provenance() -> None:
    model = MODEL_DIRECTORY / "pvad.onnx"
    notices = (MODEL_DIRECTORY / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    license_text = (MODEL_DIRECTORY / "LICENSE").read_text(encoding="utf-8")

    assert model.stat().st_size == 3_940_567
    assert hashlib.sha256(model.read_bytes()).hexdigest() == PVAD_SHA256
    assert PVAD_SHA256 in notices
    assert "https://huggingface.co/FireRedTeam/FireRedChat-pvad" in notices
    assert "Apache License" in license_text
    assert "Version 2.0" in license_text


def test_every_desktop_build_packages_the_pvad_resource_directory() -> None:
    linux_workflow = (ROOT / ".github/workflows/build-desktop-linux.yml").read_text(
        encoding="utf-8"
    )
    desktop_workflow = (ROOT / ".github/workflows/build-desktop.yml").read_text(
        encoding="utf-8"
    )

    assert linux_workflow.count(PACKAGED_MODEL_ARGUMENT) == 1
    # The combined desktop workflow has one Unix/macOS shell and one Windows cmd path.
    assert desktop_workflow.count(PACKAGED_MODEL_ARGUMENT) == 2


def test_optional_ecapa_assets_are_not_bundled_with_the_application() -> None:
    bundled_names = {path.name for path in MODEL_DIRECTORY.iterdir() if path.is_file()}
    assert "ecapa-speaker-v1.onnx" not in bundled_names
    assert "fbank-80x201-f32.bin" not in bundled_names
