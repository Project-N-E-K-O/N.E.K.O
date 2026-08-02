from __future__ import annotations

import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
MODEL_GLOB = "main_logic/asr_client/endpointing/models/*.onnx"
PART_GLOB = "main_logic/asr_client/endpointing/models/*.part"


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
    probe.write_bytes(b"must not ship")
    try:
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
    assert result.returncode == 0, result.stderr

    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert not any(name.endswith((".onnx", ".part")) for name in archive.namelist())

    sdist = next(tmp_path.glob("*.tar.gz"))
    with tarfile.open(sdist, "r:gz") as archive:
        assert not any(
            member.name.endswith((".onnx", ".part"))
            for member in archive.getmembers()
        )
