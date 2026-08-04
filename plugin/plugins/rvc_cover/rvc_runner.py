"""Subprocess wrapper around the vendored N.E.K.O vendor/rvc infer_cli.

Never uses the user's original D:\\RVC as cwd/output — work only under the
configured rvc_root (default: <repo>/vendor/rvc).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class RvcInferConfig:
    rvc_root: Path
    python_path: Path
    model_name: str
    index_path: str = ""
    f0_up_key: int = 0
    f0_method: str = "rmvpe"
    device: str = "cuda:0"
    index_rate: float = 0.0
    filter_radius: int = 3
    resample_sr: int = 0
    rms_mix_rate: float = 1.0
    protect: float = 0.33
    timeout_seconds: int = 600


def resolve_f0_method(rvc_root: Path, preferred: str) -> str:
    method = str(preferred or "harvest").strip().lower() or "harvest"
    if method != "rmvpe":
        return method
    candidates = (
        rvc_root / "assets" / "rmvpe" / "rmvpe.pt",
        rvc_root / "assets" / "rmvpe" / "rmvpe.onnx",
    )
    if any(path.is_file() for path in candidates):
        return "rmvpe"
    return "harvest"


def build_infer_command(
    cfg: RvcInferConfig,
    *,
    input_path: Path,
    output_path: Path,
    f0_method: str | None = None,
) -> list[str]:
    method = f0_method or resolve_f0_method(cfg.rvc_root, cfg.f0_method)
    index_path = str(cfg.index_path or "").strip()
    cmd = [
        str(cfg.python_path),
        str(cfg.rvc_root / "tools" / "infer_cli.py"),
        "--input_path",
        str(input_path),
        "--opt_path",
        str(output_path),
        "--model_name",
        cfg.model_name,
        "--f0up_key",
        str(int(cfg.f0_up_key)),
        "--f0method",
        method,
        "--index_rate",
        str(float(cfg.index_rate)),
        "--filter_radius",
        str(int(cfg.filter_radius)),
        "--resample_sr",
        str(int(cfg.resample_sr)),
        "--rms_mix_rate",
        str(float(cfg.rms_mix_rate)),
        "--protect",
        str(float(cfg.protect)),
        "--device",
        str(cfg.device),
    ]
    # subprocess argv list can carry ""; only interactive shells choke on it.
    cmd.extend(["--index_path", index_path])
    return cmd


def run_infer(
    cfg: RvcInferConfig,
    *,
    input_path: Path,
    output_path: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run RVC CLI; raises RuntimeError on non-zero exit or missing output."""
    if not cfg.python_path.is_file():
        raise RuntimeError(f"RVC python not found: {cfg.python_path}")
    infer_cli = cfg.rvc_root / "tools" / "infer_cli.py"
    if not infer_cli.is_file():
        raise RuntimeError(f"RVC infer_cli not found: {infer_cli}")
    model_path = cfg.rvc_root / "assets" / "weights" / cfg.model_name
    if not model_path.is_file():
        raise RuntimeError(f"RVC model not found: {model_path}")
    if not input_path.is_file():
        raise RuntimeError(f"RVC input audio missing: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    f0_method = resolve_f0_method(cfg.rvc_root, cfg.f0_method)
    cmd = build_infer_command(
        cfg,
        input_path=input_path,
        output_path=output_path,
        f0_method=f0_method,
    )

    # Force env roots into the vendored tree so infer never writes under D:\RVC.
    env = os.environ.copy()
    logs_dir = cfg.rvc_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    (cfg.rvc_root / "configs" / "inuse").mkdir(parents=True, exist_ok=True)
    env["weight_root"] = str(cfg.rvc_root / "assets" / "weights")
    env["index_root"] = str(logs_dir)
    env["outside_index_root"] = str(cfg.rvc_root / "assets" / "indices")
    # Prefer vendored ffmpeg next to rvc_root (copied by setup_rvc_vendor.ps1).
    env["PATH"] = str(cfg.rvc_root) + os.pathsep + env.get("PATH", "")
    # Avoid dotenv accidentally loading a foreign install if present on PATH.
    env["PYTHONPATH"] = str(cfg.rvc_root) + os.pathsep + env.get("PYTHONPATH", "")
    if env_extra:
        env.update(env_extra)

    completed = subprocess.run(
        cmd,
        cwd=str(cfg.rvc_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=max(30, int(cfg.timeout_seconds)),
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"RVC infer failed (code={completed.returncode}): {detail[:800]}"
        )
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise RuntimeError(f"RVC infer produced no output: {output_path}")
    return completed


def list_weight_models(rvc_root: Path) -> list[str]:
    weights = rvc_root / "assets" / "weights"
    if not weights.is_dir():
        return []
    return sorted(
        path.name
        for path in weights.iterdir()
        if path.is_file() and path.suffix.lower() == ".pth"
    )


def validate_rvc_install(cfg: RvcInferConfig) -> list[str]:
    """Return human-readable problems; empty list means ready."""
    problems: list[str] = []
    if not cfg.rvc_root.is_dir():
        problems.append(f"rvc_root missing: {cfg.rvc_root}")
    if not cfg.python_path.is_file():
        problems.append(f"python_path missing: {cfg.python_path}")
    infer_cli = cfg.rvc_root / "tools" / "infer_cli.py"
    if not infer_cli.is_file():
        problems.append(f"infer_cli missing: {infer_cli}")
    model_path = cfg.rvc_root / "assets" / "weights" / cfg.model_name
    if not model_path.is_file():
        problems.append(f"model missing: {model_path}")
    hubert = cfg.rvc_root / "assets" / "hubert" / "hubert_base.pt"
    if not hubert.is_file():
        problems.append(f"hubert_base.pt missing: {hubert}")
    return problems


def format_cmd_for_log(cmd: Sequence[str]) -> str:
    return " ".join(str(part) for part in cmd)
