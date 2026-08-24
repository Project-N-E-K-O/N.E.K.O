"""Prepare embedding model assets for Testbench packaging (wraps repo script)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parents[3]
_PREPARE = _PROJECT / "scripts" / "prepare_embedding_model.py"

# Pinned to match .github/workflows/build-desktop.yml
_DEFAULT_REPO = "jinaai/jina-embeddings-v5-text-nano-retrieval"
_DEFAULT_REVISION = "ac5d898c8d382b17167c33e5c8af644a3519b47d"
_DEFAULT_PROFILE = "local-text-retrieval-v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-if-present", action="store_true")
    parser.add_argument("--variant", default="both", choices=("fp32", "int8", "both"))
    args = parser.parse_args()
    out = _PROJECT / "data" / "embedding_models" / _DEFAULT_PROFILE
    marker = out / ".prepared.json"
    tokenizer = out / "tokenizer.json"
    fp32 = out / "onnx" / "model.onnx"
    int8 = out / "onnx" / "model_quantized.onnx"
    if args.skip_if_present and marker.is_file() and tokenizer.is_file():
        if args.variant == "int8" and int8.is_file():
            print(f"[prepare_embedding] skip, already present: {out}")
            return 0
        if args.variant == "fp32" and fp32.is_file():
            print(f"[prepare_embedding] skip, already present: {out}")
            return 0
        if args.variant == "both" and fp32.is_file() and int8.is_file():
            print(f"[prepare_embedding] skip, already present: {out}")
            return 0
    if not _PREPARE.is_file():
        print(f"[prepare_embedding] missing {_PREPARE}", file=sys.stderr)
        return 1
    # Prefer China-friendly mirror when unset (same fallback chain as prepare script).
    import os

    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    cmd = [
        sys.executable,
        str(_PREPARE),
        "--repo",
        _DEFAULT_REPO,
        "--revision",
        _DEFAULT_REVISION,
        "--profile-id",
        _DEFAULT_PROFILE,
        "--output-root",
        str(_PROJECT / "data" / "embedding_models"),
        "--variant",
        args.variant,
    ]
    print("[prepare_embedding]", " ".join(cmd))
    return subprocess.call(cmd, cwd=str(_PROJECT))


if __name__ == "__main__":
    raise SystemExit(main())
