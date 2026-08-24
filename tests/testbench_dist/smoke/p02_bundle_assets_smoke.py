"""Assert frozen one-dir contains tiktoken cache + embedding profile files."""
from __future__ import annotations

import sys
from pathlib import Path

_DIST = Path(__file__).resolve().parents[1]
_INTERNAL = _DIST / "output" / "pyinstaller" / "Testbench" / "_internal"


def main() -> int:
    failures: list[str] = []
    checks = [
        _INTERNAL / "data" / "tiktoken_cache",
        _INTERNAL / "data" / "embedding_models" / "local-text-retrieval-v1" / "tokenizer.json",
        _INTERNAL / "data" / "embedding_models" / "local-text-retrieval-v1" / "onnx" / "model_quantized.onnx",
        _INTERNAL / "data" / "embedding_models" / "local-text-retrieval-v1" / "onnx" / "model_quantized.onnx_data",
        _INTERNAL / "testbench" / "static" / "app.js",
        _INTERNAL / "config" / "api_providers.json",
    ]
    for path in checks:
        if path.is_dir():
            if not any(path.iterdir()):
                failures.append(f"empty dir: {path.relative_to(_INTERNAL)}")
            else:
                print(f"[OK] dir {path.relative_to(_INTERNAL)}")
        elif path.is_file() and path.stat().st_size > 0:
            print(f"[OK] file {path.relative_to(_INTERNAL)} ({path.stat().st_size} bytes)")
        else:
            failures.append(f"missing/empty: {path.relative_to(_INTERNAL)}")

    # fp32 is optional but report it
    fp32 = _INTERNAL / "data" / "embedding_models" / "local-text-retrieval-v1" / "onnx" / "model.onnx"
    if fp32.is_file():
        print(f"[OK] optional fp32 {fp32.name} ({fp32.stat().st_size} bytes)")
    else:
        print("[WARN] optional fp32 model.onnx not bundled (int8 present is enough for auto quant)")

    if failures:
        print("[FAIL] p02_bundle_assets")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("[OK] p02_bundle_assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
