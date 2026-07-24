from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]


def test_pyinstaller_bundles_voice_turn_assets():
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")
    assert "add_data('data/vad_models', 'data/vad_models')" in spec
    assert "voice_turn_assets_present" in spec
    assert re.search(
        r"pkg == ['\"]onnxruntime['\"] and voice_turn_assets_present",
        spec,
    )


def test_nuitka_workflows_prepare_bundle_and_verify_voice_turn_assets():
    for relative in (
        ".github/workflows/build-desktop.yml",
        ".github/workflows/build-desktop-linux.yml",
    ):
        workflow = (ROOT / relative).read_text(encoding="utf-8")
        assert "tools/voice_eval/prepare_voice_turn_assets.py" in workflow
        assert "--include-data-dir=data/vad_models=data/vad_models" in workflow
        assert "--asset-dir dist/Xiao8/data/vad_models --offline" in workflow
        assert "hashFiles('data/vad_models/manifest.json')" in workflow
