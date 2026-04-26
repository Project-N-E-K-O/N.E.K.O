# Local Embedding Model Assets

N.E.K.O uses an anonymous embedding profile id at runtime:

```text
local-text-retrieval-v1
```

Do not store the concrete upstream model name in config or memory cache fields. The profile id is the compatibility contract for vector dimensions, pooling, tokenizer behavior, and quantization. If a future model is not compatible with existing vectors, bump the profile id, for example `local-text-retrieval-v2`.

## Development Setup

Install the optional runtime dependencies:

```bash
uv sync --extra embeddings
```

Download model files into the anonymous profile folder. This example mirrors a Hugging Face ONNX repository into `data/embedding_models/local-text-retrieval-v1/`:

```bash
uv run python scripts/prepare_embedding_model.py \
  --repo jinaai/jina-embeddings-v5-text-nano-retrieval \
  --revision ac5d898c8d382b17167c33e5c8af644a3519b47d \
  --profile-id local-text-retrieval-v1 \
  --output-root data/embedding_models \
  --variant both
```

`--revision` must be a 40-char lowercase hex commit SHA. Branch refs like `main` and tags (which can be force-pushed upstream) are rejected — the profile id is the cache compatibility contract and the weights/tokenizer behind it must not drift between runs. If the (repo, revision) recorded in `.prepared.json` differs from a previous run, the script forces a re-download so stale files cannot leak across pins.

The resulting layout must be:

```text
data/embedding_models/local-text-retrieval-v1/
  tokenizer.json
  onnx/
    model.onnx
    model.onnx_data
    model_quantized.onnx
    model_quantized.onnx_data
```

Source runs use this bundled development cache when the user's app-data profile is absent **or incomplete**. The runtime falls back to the bundle whenever the app-data profile fails its completeness check — common cases include a missing `tokenizer.json`, a missing `onnx/<model>.onnx_data` sidecar, zero-byte residue from an interrupted download, or only the wrong quantization variant for the runtime that resolved (fp32 files when the runtime needs int8, or vice versa). A user override can still be placed under:

```text
<app data>/embedding_models/local-text-retrieval-v1/
```

## Cross-Platform Nightly Builds

The cross-platform nightly workflow (`.github/workflows/build-desktop.yml`) builds the backend with Nuitka on Windows, macOS, and Linux. Before invoking Nuitka it runs `scripts/prepare_embedding_model.py` against the pinned `EMBEDDING_MODEL_REVISION`, warms the `tiktoken` o200k_base cache into `data/tiktoken_cache/`, and bundles both directories into the standalone artifact. After build it verifies that every required embedding file (`tokenizer.json`, both fp32 and int8 ONNX variants, and their `*.onnx_data` sidecars) is present and non-empty, and that the tiktoken cache has at least one blob.

`specs/launcher.spec` declares the same `data/embedding_models/` and `data/tiktoken_cache/` directories, so a manual PyInstaller run can match the nightly's offline behavior — but only if both directories were populated beforehand. Run `scripts/prepare_embedding_model.py` to fill the embedding profile, and warm the tiktoken cache with:

```bash
mkdir -p data/tiktoken_cache
TIKTOKEN_CACHE_DIR="$(pwd)/data/tiktoken_cache" \
  uv run python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
```

The spec file refuses to build when `data/embedding_models/` is present but `onnxruntime` / `tokenizers` cannot be collected — shipping weights without the runtime that loads them would silently sticky-disable vectors at first use.
