# GitHub Actions：校验知识预构建索引

> **工作流说明（2026-08-24）**：此示例只验证既有制品，不生成向量、不加载本地 Embedding 模型。若 CI 还负责生成制品，应另行运行 `scripts/build_knowledge_pack_index.py`，并固定与目标客户端一致的已审阅提交和 Python 3.11 环境。

下面的工作流适合知识包发布仓库：先由受控的上游步骤生成三个制品，再使用 N.E.K.O 的同版本代码和 Python 3.11 重新校验，最后上传为不可变构建产物。示例只负责校验和归档，不把来源不明的向量提升为 `trusted_market`；信任状态仍由客户端的可信市场交接链决定。

假设待发布文件为：

```text
dist/example.neko-knowledge.json
dist/example.neko-knowledge.index.json
dist/example.neko-knowledge.vectors.f16
```

```yaml
name: verify-knowledge-index

on:
  workflow_dispatch:
  push:
    tags: ['knowledge-v*']

jobs:
  verify:
    runs-on: ubuntu-latest
    permissions:
      contents: read

    steps:
      - name: Check out the knowledge-package repository
        uses: actions/checkout@v4
        with:
          path: source

      - name: Check out a reviewed N.E.K.O contract revision
        uses: actions/checkout@v4
        with:
          repository: Project-N-E-K-O/N.E.K.O
          # Replace this marker with the full commit SHA used by the target release.
          ref: REPLACE_WITH_REVIEWED_COMMIT_SHA
          path: neko

      - uses: astral-sh/setup-uv@v6
        with:
          python-version: '3.11'
          enable-cache: true

      - name: Install the locked project environment
        run: uv sync --project neko --frozen --python 3.11

      - name: Validate all three artifacts
        shell: bash
        env:
          PYTHONPATH: neko
        run: |
          uv run --project neko --python 3.11 python - <<'PY'
          from hashlib import sha256
          from pathlib import Path

          from knowledge.prebuilt_index import validate_prebuilt_index

          root = Path('source/dist')
          pack = (root / 'example.neko-knowledge.json').read_bytes()
          manifest = (root / 'example.neko-knowledge.index.json').read_bytes()
          vectors = (root / 'example.neko-knowledge.vectors.f16').read_bytes()
          digest = lambda value: sha256(value).hexdigest()

          validated = validate_prebuilt_index(
              pack,
              manifest,
              vectors,
              expected_pack_sha256=digest(pack),
              expected_manifest_sha256=digest(manifest),
              expected_vectors_sha256=digest(vectors),
          )
          print(f'validated {len(validated.chunks)} chunks')
          print(f'knowledge_sha256={validated.pack_sha256}')
          print(f'index_manifest_sha256={validated.manifest_sha256}')
          print(f'vectors_sha256={validated.vectors_sha256}')
          PY

      - name: Upload the immutable artifact set
        uses: actions/upload-artifact@v4
        with:
          name: example-knowledge-${{ github.ref_name }}
          if-no-files-found: error
          retention-days: 30
          path: |
            source/dist/example.neko-knowledge.json
            source/dist/example.neko-knowledge.index.json
            source/dist/example.neko-knowledge.vectors.f16
```

必须先把 `REPLACE_WITH_REVIEWED_COMMIT_SHA` 替换为目标客户端版本所使用、已审阅的完整提交 SHA，不能跟随浮动分支。正式发布时，市场版本描述符必须使用这次校验输出的三个摘要，并记录每个文件的精确字节数。不要对单个制品使用可变 URL；任一文件变化都应产生新的版本和一整套新摘要。
