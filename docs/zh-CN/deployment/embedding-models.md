# 本地嵌入模型资源

N.E.K.O 在运行时使用一个匿名的嵌入 profile id：

```text
local-text-retrieval-v1
```

不要把具体的上游模型名写进配置文件或记忆缓存字段。该 profile id 是向量维度、池化方式、tokenizer 行为和量化方式的兼容性契约。如果未来的模型与现有向量不兼容，请直接升 profile id，例如 `local-text-retrieval-v2`。

## 开发环境准备

安装可选的运行时依赖：

```bash
uv sync --extra embeddings
```

把模型文件下载到匿名 profile 目录。下面的示例将一个 Hugging Face ONNX 仓库镜像到 `data/embedding_models/local-text-retrieval-v1/`：

```bash
uv run python scripts/prepare_embedding_model.py \
  --repo jinaai/jina-embeddings-v5-text-nano-retrieval \
  --revision ac5d898c8d382b17167c33e5c8af644a3519b47d \
  --profile-id local-text-retrieval-v1 \
  --output-root data/embedding_models \
  --variant both
```

`--revision` 必须是 40 个字符的小写十六进制 commit SHA。`main` 这种分支引用以及 tag（tag 在上游也可能被 force-push）一律拒绝——profile id 是缓存兼容性契约，背后的权重/tokenizer 不能在多次构建之间漂移。脚本会把 `(repo, revision)` 写入 profile 目录下的 `.prepared.json`，如果下次跑发现这两个不匹配，会强制重新下载，避免旧文件泄漏到新 pin 的产物里。

最终的目录结构必须是：

```text
data/embedding_models/local-text-retrieval-v1/
  tokenizer.json
  onnx/
    model.onnx
    model.onnx_data
    model_quantized.onnx
    model_quantized.onnx_data
```

当用户的 app-data profile 不存在**或不完整**时，源码运行使用这个本地开发缓存。运行时只要 app-data profile 没通过完整性校验就会回退到 bundle——常见情况包括缺少 `tokenizer.json`、缺少 `onnx/<model>.onnx_data` sidecar、下载中断留下的 0 字节文件，或只放了与运行时实际选中的量化方式不匹配的变体（要 int8 但只有 fp32，或反过来）。用户覆盖目录仍然可以放到：

```text
<app data>/embedding_models/local-text-retrieval-v1/
```

## 跨平台 Nightly 构建

跨平台 nightly 工作流（`.github/workflows/build-desktop.yml`）在 Windows、macOS、Linux 上用 Nuitka 构建后端。在调用 Nuitka 之前会先用钉死的 `EMBEDDING_MODEL_REVISION` 跑 `scripts/prepare_embedding_model.py`，并把 `tiktoken` 的 o200k_base 缓存预热到 `data/tiktoken_cache/`，然后把两个目录一起打进 standalone 产物。构建完成后会校验所有必需的 embedding 文件（`tokenizer.json`、fp32 和 int8 两个 ONNX 变体、以及对应的 `*.onnx_data` sidecar）存在且非空，并校验 tiktoken 缓存中至少有一个 blob。

`specs/launcher.spec` 同时声明了 `data/embedding_models/` 与 `data/tiktoken_cache/`，因此本地手动跑 PyInstaller 想要复现 nightly 的离线行为时，必须先把这两个目录都填好。前者跑 `scripts/prepare_embedding_model.py`，后者用以下命令预热：

```bash
mkdir -p data/tiktoken_cache
TIKTOKEN_CACHE_DIR="$(pwd)/data/tiktoken_cache" \
  uv run python -c "import tiktoken; tiktoken.get_encoding('o200k_base')"
```

如果 `data/embedding_models/` 已经存在但 `onnxruntime` / `tokenizers` 收集失败，spec 文件会直接中止构建——只打权重不带推理后端的产物会在首次调用时把向量功能 sticky-disable，是个无声坑。
