from __future__ import annotations

import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MODEL_GLOB = "main_logic/asr_client/endpointing/models/*.onnx"
PART_GLOB = "main_logic/asr_client/endpointing/models/*.part"
PVAD_MODELS = "main_logic/voice_identity_service/pvad/models"


def _assert_distributed_model_manifest(names):
    # pVAD is intentionally bundled; endpointing downloads and unfinished
    # downloads must still never ship. Keep the exception to one exact path.
    assert {name for name in names if name.endswith((".onnx", ".part"))} == {
        f"{PVAD_MODELS}/pvad.onnx"
    }
    assert f"{PVAD_MODELS}/LICENSE" in names
    assert f"{PVAD_MODELS}/THIRD_PARTY_NOTICES.md" in names


def test_hatch_artifacts_explicitly_exclude_local_endpointing_weights(tmp_path):
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    targets = config["tool"]["hatch"]["build"]["targets"]
    for target in ("wheel", "sdist"):
        excludes = targets[target]["exclude"]
        assert MODEL_GLOB in excludes
        assert PART_GLOB in excludes

    probe = (
        ROOT
        / "main_logic"
        / "asr_client"
        / "endpointing"
        / "models"
        / "artifact-contract-probe.onnx"
    )
    partial_probe = probe.with_suffix(".part")
    try:
        probe.write_bytes(b"must not ship")
        partial_probe.write_bytes(b"unfinished download must not ship")
        result = subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--sdist",
                "--out-dir",
                str(tmp_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=180,
        )
    finally:
        probe.unlink(missing_ok=True)
        partial_probe.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr

    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        _assert_distributed_model_manifest(set(archive.namelist()))

    sdist = next(tmp_path.glob("*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        # sdist members live beneath the generated project-version directory.
        _assert_distributed_model_manifest(
            {member.name.partition("/")[2] for member in archive.getmembers()}
        )
