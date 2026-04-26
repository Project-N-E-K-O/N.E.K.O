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

Source runs use this bundled development cache when no user override exists in the app data directory. A user override can still be placed under:

```text
<app data>/embedding_models/local-text-retrieval-v1/
```

## PyInstaller Nightly Builds

Linux and macOS nightly builds use `launcher.py` through `specs/launcher.spec`. The workflow downloads the model assets before invoking PyInstaller and packages `data/embedding_models/` into the final `N.E.K.O` directory.

The workflow also checks that the packaged artifact contains the profile folder before upload.
