"""Warm the local faster-whisper model into the HuggingFace cache.

First utterance otherwise blocks on a multi-hundred-MB download. Mainland
networks should set HF_ENDPOINT=https://hf-mirror.com before running this.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default=os.environ.get("NEKO_WHISPER_MODEL", ""),
        help="faster-whisper model size (default: auto by device / NEKO_WHISPER_MODEL)",
    )
    parser.add_argument(
        "--device",
        default=os.environ.get("NEKO_WHISPER_DEVICE", "auto"),
        help="cpu / cuda / auto (default: auto / NEKO_WHISPER_DEVICE)",
    )
    args = parser.parse_args(argv)

    if not (os.environ.get("HF_ENDPOINT") or "").strip():
        # Prefer mirror when unset; huggingface.co often times out in CN.
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

    t0 = time.time()
    print(
        f"Warming faster-whisper model={args.model!r} device={args.device!r} "
        f"HF_ENDPOINT={os.environ.get('HF_ENDPOINT')}",
        flush=True,
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    from scripts.prepare_cuda_asr_path import prepare_cuda_asr_path
    from main_logic.asr_client.workers.faster_whisper import (
        _default_model_factory,
        _resolve_device,
        _resolve_model_size,
    )

    cuda_dir = prepare_cuda_asr_path()
    if cuda_dir:
        print(f"CUDA DLL dir={cuda_dir}", flush=True)
    if args.model:
        os.environ["NEKO_WHISPER_MODEL"] = str(args.model)
    else:
        os.environ.pop("NEKO_WHISPER_MODEL", None)
    os.environ["NEKO_WHISPER_DEVICE"] = str(args.device)
    preferred_device, preferred_compute = _resolve_device(args.device)
    model_size = _resolve_model_size(preferred_device)
    print(
        f"Preferred model={model_size!r} device={preferred_device} "
        f"compute_type={preferred_compute}",
        flush=True,
    )
    # Factory probes CUDA and falls back to CPU when the runtime is incomplete.
    _default_model_factory()
    print(f"OK warmed in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
