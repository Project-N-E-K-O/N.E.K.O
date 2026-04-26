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

当用户的 app-data 目录没有覆盖文件时，源码运行使用这个本地开发缓存。如果用户想覆盖，仍然可以放到：

```text
<app data>/embedding_models/local-text-retrieval-v1/
```

## 跨平台 Nightly 构建

跨平台 nightly 工作流（`.github/workflows/build-desktop.yml`）在 Windows、macOS、Linux 上用 Nuitka 构建后端。在调用 Nuitka 之前会先用钉死的 `EMBEDDING_MODEL_REVISION` 跑 `scripts/prepare_embedding_model.py`，把 `data/embedding_models/` 一起打进 standalone 产物。构建完成后会校验所有必需文件（`tokenizer.json`、fp32 和 int8 两个 ONNX 变体、以及对应的 `*.onnx_data` sidecar）存在且非空，再上传 artifact。

`specs/launcher.spec` 也声明了同一个 `data/embedding_models/` 目录，因此本地手动跑 PyInstaller 也能在 prepare 脚本预先填好该目录后正确打包资源。
