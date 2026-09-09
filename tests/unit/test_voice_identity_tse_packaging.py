"""The desktop carries the trusted manifest, never optional TSE weights."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARGUMENT = "--include-data-files=main_logic/voice_identity/tse/release_manifest.json=main_logic/voice_identity/tse/release_manifest.json"


def test_each_desktop_shell_includes_trusted_tse_manifest():
    assert (ROOT / ".github/workflows/build-desktop-linux.yml").read_text(encoding="utf-8").count(ARGUMENT) == 1
    assert (ROOT / ".github/workflows/build-desktop.yml").read_text(encoding="utf-8").count(ARGUMENT) == 2


def test_tse_package_contains_no_bundled_weights_or_training_state():
    directory = ROOT / "main_logic/voice_identity/tse"
    assert not list(directory.rglob("*.onnx"))
    assert not list(directory.rglob("*.ckpt"))
    assert not list(directory.rglob("*.zip"))
