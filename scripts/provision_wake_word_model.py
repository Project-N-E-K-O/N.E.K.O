"""Download the pinned upstream KWS asset into an explicit external directory."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


MODEL_NAME = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/{MODEL_NAME}.tar.bz2"
# Observed upstream release bytes, pinned to reject changed/corrupt downloads.
MODEL_SHA256 = "68447f4fbc67e70eee3a93961f36e81e98f47aef73ce7e7ca00885c6cd3616a6"
ASSETS = (
    "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
    "decoder-epoch-13-avg-2-chunk-8-left-64.onnx",
    "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx",
    "tokens.txt",
)


def provision(destination: Path, archive: Path | None = None) -> None:
    """Extract only the four expected regular files after authenticating bytes."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neko-kws-") as temporary:
        source = archive or Path(temporary) / "model.tar.bz2"
        if archive is None:
            request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "NEKO-model-provisioner"})
            with urllib.request.urlopen(request, timeout=30) as response, source.open("wb") as out:
                size = 0
                while block := response.read(1024 * 1024):
                    size += len(block)
                    if size > 64 * 1024 * 1024:
                        raise ValueError("Model download exceeds budget")
                    out.write(block)
        with source.open("rb") as data:
            digest = hashlib.file_digest(data, "sha256").hexdigest()
        if digest != MODEL_SHA256:
            raise ValueError("Model archive SHA-256 mismatch")
        with tarfile.open(source, "r:bz2") as bundle:
            for name in ASSETS:
                member = bundle.getmember(f"{MODEL_NAME}/{name}")
                if not member.isfile() or member.size > 16 * 1024 * 1024:
                    raise ValueError("Unexpected model asset")
                with bundle.extractfile(member) as data, (Path(temporary) / name).open("wb") as out:
                    shutil.copyfileobj(data, out)
            for name in ASSETS:
                staged = destination / (name + ".download")
                shutil.copyfile(Path(temporary) / name, staged)
                staged.replace(destination / name)
    print(f"Model ready: {destination}")
    print(f"Set NEKO_WAKE_WORD_MODEL_DIR to {destination}")
    print("Install the optional runtime with: uv sync --extra wake-word")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="Reuse an already downloaded, hash-verified release")
    args = parser.parse_args()
    provision(args.model_dir, args.archive)


if __name__ == "__main__":
    main()
