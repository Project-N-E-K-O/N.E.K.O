from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[4]
MODEL_DIRECTORY = "main_logic/asr_client/speaker_shadow/models"
MODEL_FILENAME = "campplus-zh-en-advanced.onnx"


def test_hatch_artifacts_exclude_downloaded_campplus_weights() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["hatch"]["build"]["targets"]
    for target in ("wheel", "sdist"):
        excludes = targets[target]["exclude"]
        assert f"{MODEL_DIRECTORY}/*.onnx" in excludes
        assert f"{MODEL_DIRECTORY}/*.part" in excludes


def test_unit_ci_installs_pinned_frontend_parity_oracle() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/unit-tests.yml").read_text(encoding="utf-8")
    )
    runs = [step.get("run") for step in workflow["jobs"]["unit-pytest"]["steps"]]

    assert "uv pip install kaldi-native-fbank==1.22.3" in runs


def test_pyinstaller_bundles_campplus_and_requires_onnxruntime() -> None:
    spec = (ROOT / "specs" / "launcher.spec").read_text(encoding="utf-8")

    assert spec.count(f"'{MODEL_DIRECTORY}'") == 2
    assert "speaker_shadow_assets_present" in spec
    assert re.search(
        r"pkg == ['\"]onnxruntime['\"] and speaker_shadow_assets_present",
        spec,
    )


def test_nuitka_workflows_prepare_and_verify_one_campplus_weight() -> None:
    desktop = (ROOT / ".github/workflows/build-desktop.yml").read_text(
        encoding="utf-8"
    )
    linux = (ROOT / ".github/workflows/build-desktop-linux.yml").read_text(
        encoding="utf-8"
    )
    include_option = f"--include-data-dir={MODEL_DIRECTORY}={MODEL_DIRECTORY}"

    assert desktop.count(include_option) == 2
    assert linux.count(include_option) == 1
    for workflow in (desktop, linux):
        assert "scripts/prepare_speaker_model.py" in workflow
        assert f"hashFiles('{MODEL_DIRECTORY}/manifest.json')" in workflow
        assert '--asset-dir "$speaker_shadow_assets" --offline' in workflow
        assert "CAM++ weight must be packaged exactly once" in workflow
        assert "obsolete data/speaker_models must not be packaged" in workflow
        assert "THIRD_PARTY_NOTICES.md" in workflow


def test_docker_builds_prepare_natively_then_verify_offline() -> None:
    workflow = (ROOT / ".github/workflows/docker-multi-arch.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("run: python3 scripts/prepare_speaker_model.py") == 2
    assert workflow.count(f"path: {MODEL_DIRECTORY}/*.onnx") == 2
    assert workflow.count(f"hashFiles('{MODEL_DIRECTORY}/manifest.json')") == 2

    for name in ("Dockerfile", "Dockerfile.full"):
        dockerfile = (ROOT / "docker" / name).read_text(encoding="utf-8")
        copy_index = dockerfile.index("COPY --chown=neko:neko . /app")
        verify_index = dockerfile.index(
            "python3 scripts/prepare_speaker_model.py --offline"
        )
        sync_index = dockerfile.index("uv sync --frozen", verify_index)
        assert copy_index < verify_index < sync_index, name
        assert f"/app/{MODEL_DIRECTORY}/THIRD_PARTY_NOTICES.md" in dockerfile
        assert "test ! -e /app/data/speaker_models" in dockerfile
        assert f"find /app -type f -name {MODEL_FILENAME}" in dockerfile


def test_ignore_rules_keep_only_reviewable_campplus_metadata() -> None:
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert f"/{MODEL_DIRECTORY}/*" in gitignore
    assert f"!/{MODEL_DIRECTORY}/manifest.json" in gitignore
    assert f"!/{MODEL_DIRECTORY}/THIRD_PARTY_NOTICES.md" in gitignore

    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    patterns = {
        line.strip().rstrip("/")
        for line in dockerignore.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "/data/speaker_models" in patterns
    assert not any(MODEL_DIRECTORY in pattern for pattern in patterns)


def test_legacy_voice_identity_and_model_locations_are_absent() -> None:
    voice_identity = ROOT / "main_logic" / "voice_identity"
    assert not (voice_identity / "runtime.py").exists()
    assert not (voice_identity / "campplus.py").exists()
    assert not (ROOT / "data" / "speaker_models").exists()
    assert not (ROOT / "tools" / "voice_eval").exists()
    assert not (ROOT / "main_logic" / "asr_client" / "campplus.py").exists()
    assert not (ROOT / "main_logic" / "asr_client" / "speaker_shadow.py").exists()
