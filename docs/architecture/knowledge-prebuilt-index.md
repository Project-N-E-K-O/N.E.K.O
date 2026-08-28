# 预构建知识向量索引

> **首发契约（2026-08-24）**：市场交付协议、原始知识包 Schema、索引清单、Embedding 输入与分块规则统一从 v1 起步；知识包自身的发布版本另行使用 SemVer。

## 目的与默认行为

预构建索引让可信市场在发布知识包时一并提供已经生成的向量，避免普通用户安装后立刻消耗 CPU 和内存。它是可选的派生制品，不是知识正文的一部分。

知识包的五字段正文始终是原始来源。包根的 `material_type` 只声明这些正文应按事实知识还是参考语料使用，不进入词条或向量字段。无索引、索引不可信或索引校验失败时，知识仍正常安装并使用 BM25。社区包只有在用户显式开启“允许本机维护向量”后，才可在本机补算或重建向量。

## 三制品契约

一次完整的可信市场 protocol v1 发布包含：

1. `*.neko-knowledge.json`：规范 JSON 知识正文。
2. `*.neko-knowledge.index.json`：规范 JSON 索引清单。
3. `*.neko-knowledge.vectors.f16`：无文件头的向量矩阵。

三个文件分别计算 SHA-256。市场版本描述符携带每个文件的 URL、字节数和摘要；本地 Bridge 下载后校验一次，Main Server 在写入暂存库前再独立校验一次。索引清单与向量必须同时提供、同时通过或同时放弃，不接受“半个索引”。

## 固定 v1 索引格式

索引清单只接受以下固定值：

| 字段 | 值 |
| --- | --- |
| `index_schema_version` | `1` |
| `embedding_model_id` | `local-text-retrieval-v1-256d-int8-mlen1024` |
| `embedding_input_version` | `1` |
| `chunker_version` | `1` |
| `embedding_dimensions` | `256` |
| `vector_encoding` | `float16-le-row-major` |

其余字段是：

```json
{
  "pack_id": "example-pack",
  "pack_sha256": "知识正文制品摘要",
  "vectors_sha256": "向量矩阵制品摘要",
  "chunk_count": 2,
  "chunks": [
    {"chunk_id": "…", "content_hash": "…", "vector_index": 0},
    {"chunk_id": "…", "content_hash": "…", "vector_index": 1}
  ]
}
```

清单必须按 UTF-8、JSON 键排序、无多余空白的规范形式编码。向量矩阵按 `vector_index` 顺序连续存放，每行正好 `256 × 2` 字节。所有向量必须是有限数且不能为全零。

## 本地校验顺序

校验在任何数据库写入前完成：

1. 限制正文、清单和向量文件大小，并校验三个 SHA-256。
2. 解析规范 JSON，严格拒绝额外字段和错误类型。
3. 使用本地五字段模型重新验证知识包。
4. 使用本地 `embedding_input_version` 和 `chunker_version` 重新确定性分块。
5. 要求清单逐项、逐序与本地派生的 `chunk_id`、`content_hash` 完全一致。
6. 校验模型 ID、维度、编码、向量字节长度、有限数和非零范数。
7. 将通过校验的 chunk 与向量一起写入暂存数据库，再原子激活来源。

任一步失败都不会尝试“修复”市场向量，也不会把不完整向量写入在线库。正文有效时任务记录不含原文的降级原因，并以 BM25 激活。
这套校验只重新派生确定性 chunk 并验证既有向量，不加载本地 Embedding 模型；因此模型被禁用或未绑定不会妨碍制品验证。

## 信任与本机维护

`trusted_market` 表示制品来自本地允许的市场交接链，并且完成了上述本地二次校验；它不是发布者可在知识 JSON 中自行填写的字段。本地文件导入和任意旁路下载都不能声明该信任级别。

每个已安装包记录以下可观察状态：

- `index_origin`：例如可信市场预构建索引或无索引。
- `index_trust`：索引的信任边界。
- `index_validation`：缺失、通过或拒绝等校验结果。
- `index_fallback_reason`：不含正文与异常详情的降级代码。
- `local_embedding_enabled`：用户是否授权本机维护该包的向量。

关闭本机维护不会删除已经通过校验的可信预构建向量；它只阻止本地模型为该社区包补算或重建。模型或分块契约变化导致旧索引不兼容时，知识继续走 BM25，等待市场发布兼容索引或用户显式开启本机维护。

## 发布建议

- 三个制品必须来自同一次不可变构建，不要在上传后单独替换其中一个。
- 发布者应保存模型 ID、分块版本和三个摘要作为构建证明。
- 生成制品时用项目 Python 3.11 环境运行 `scripts/build_knowledge_pack_index.py`；脚本会在自身进程入口绑定固定的本地模型实现。
- 发布前调用同一脚本的 `--verify`，或直接调用 `knowledge.prebuilt_index.validate_prebuilt_index` 做一次不加载模型的独立校验。
- CI 示例见 [GitHub Actions：校验知识预构建索引](knowledge-prebuilt-index-github-actions.md)。
