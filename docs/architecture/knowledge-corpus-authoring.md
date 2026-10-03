# Knowledge / Corpus 知识包编写与发布

> **首发契约（2026-08-24）**：知识包格式、市场交付、预构建索引、Embedding 输入和分块规则统一从 v1 起步。构建、评估和重建知识索引的命令会在自身进程入口显式绑定本地 Embedding 提供方；仅执行制品校验不加载模型，提供方未配置或不可用时安全降级为 BM25。

## 统一模型

公共知识只使用一个数据库和一套检索接口。`material_type` 表示内容用途，不表示数据库、索引或工具：

> **更正（2026-08-21）**：旧版文档曾写明 `corpus`“永不自动注入”且“无法开启自动上下文”，该描述已失效。现行架构允许 `corpus` 包参与普通聊天的自动素材选择：新安装的 `corpus` 包默认开启，用户可按包关闭或重新开启；候选仍需通过直接匹配、词法与语义双路阈值，或带候选差距要求的高阈值语义筛选，避免弱相关素材进入上下文。

| 类型 | 内容 | 普通聊天 | 显式公共知识查询 |
| --- | --- | --- | --- |
| `knowledge` | 事实、定义、解释、梗义、出处 | 用户启用包后，可通过直接匹配或高置信混合检索自动注入 | BM25 + Embedding + RRF |
| `corpus` | 回复范例、对话样例、写作与语气素材 | 新安装包默认参与自动素材选择，用户可按包关闭或重新开启 | BM25 + Embedding + RRF |

`meme` 不再是集合或第三种材料类型。它只是可选主题标签 `domain:meme`：梗的含义属于 `knowledge`，梗式回复样例属于 `corpus`。该标签只影响结果交给模型时的表达策略，不改变存储和检索路径。

## 知识包 Schema v1

Schema v1 的包根只能包含 `schema_version`、`pack_id`、`material_type`、`source` 和 `entries`：

```json
{
  "schema_version": 1,
  "pack_id": "example-meme-knowledge",
  "material_type": "knowledge",
  "source": {
    "name": "Example Publisher",
    "homepage": "https://example.invalid",
    "license": "CC0-1.0"
  },
  "entries": [
    {
      "title": "示例梗",
      "terms": {
        "alias": ["示例别名"],
        "recognition": ["这是什么梗"]
      },
      "tags": ["domain:meme"],
      "summary": "一句简短、可核验的解释。",
      "content": "完整含义、出处与使用边界。"
    }
  ]
}
```

Corpus 包只需把 `material_type` 改为 `corpus`，正文可写成“用户输入 / 参考回复”。每条词条仍严格只允许：

```text
title / terms / tags / summary / content
```

`chunks`、哈希、向量、模型 ID 和索引状态都是系统派生数据，不能写入原始包。

`collection_id` 不属于首发格式；内容用途只由 `material_type` 表达。发布者必须从规范 Schema v1 原始包生成摘要和可选索引。

## 原始包与预构建索引

原始 `.neko-knowledge.json` 是唯一真实来源，必须存在。预构建索引是可选性能缓存：

```text
<pack>.neko-knowledge.json
<pack>.neko-knowledge.index.json
<pack>.neko-knowledge.vectors.f16
```

当前固定契约：

```text
embedding_model_id      = local-text-retrieval-v1-256d-int8-mlen1024
embedding_input_version = 1
chunker_version         = 1
embedding_dimensions    = 256
vector_encoding         = float16-le-row-major
```

构建并验证：

```powershell
uv run --python 3.11 python scripts/build_knowledge_pack_index.py dist/example.neko-knowledge.json --output-dir dist
uv run --python 3.11 python scripts/build_knowledge_pack_index.py dist/example.neko-knowledge.json --verify --manifest dist/example.neko-knowledge.index.json --vectors dist/example.neko-knowledge.vectors.f16
```

不带 `--verify` 的构建命令会加载本地固定模型并生成向量；`--verify` 只重新派生 chunk 并检查摘要、清单和矩阵，不加载 Embedding 模型。索引缺失或校验失败不阻止原文安装：包立即以 BM25 工作。社区包只有在用户明确允许本机维护向量后才进入本地 Embedding 队列。

## 运行路径

```text
导入 Schema v1 包
  → 同一 knowledge/knowledge.db
  → entries + FTS + knowledge_chunks

普通聊天
  → 对内置来源和已授权知识包运行一次共享的 BM25 + Embedding + RRF 检索
  → knowledge 与 corpus 分别使用更严格的自动上下文阈值筛选
  → 每轮最多注入 1 条 knowledge 与受总上限约束的 corpus 素材

显式查询
  → 一次 BM25 + 一次 Query Embedding
  → 同一候选池内 RRF
  → material_type 仅用于过滤或回答策略
  → 一次 LLM 回复
```

Knowledge 与 corpus 共用一次 Query Embedding、一个融合候选池和一次 LLM 请求，不会按材料类型重复调用。

自动上下文没有梗专用模糊规则。直接标题、别名或识别词匹配之外，系统只接受达到独立高阈值的双路或语义候选；纯语义候选还必须与下一候选保持足够分差。短语容易与普通句子冲突时，作者仍应提供更明确的 `terms.recognition`（例如“上头是什么意思”），而不是依赖系统删除语气词或替换代词后猜测含义。

## 本地管理 API

```text
GET  /api/public-knowledge/status
GET  /api/public-knowledge/entries
GET  /api/public-knowledge/entry
GET  /api/public-knowledge/packs
GET  /api/public-knowledge/packs/jobs
GET  /api/public-knowledge/diagnostics/recent
POST /api/public-knowledge/entry/disabled
POST /api/public-knowledge/packs/import
POST /api/public-knowledge/packs/jobs/cancel
POST /api/public-knowledge/packs/material-type
POST /api/public-knowledge/packs/auto-context
POST /api/public-knowledge/packs/index-policy
POST /api/public-knowledge/packs/remove
```

写接口仅接受通过本地 CSRF 与 Origin 校验的请求。修改包用途只更新来源级策略，不改写原始词条。`knowledge` 与 `corpus` 都可通过 `packs/auto-context` 按包开启或关闭自动上下文；包首次切换为 `corpus` 时会按现行策略默认开启。可信市场的 `/subscriptions/apply` 交接端点另见订阅契约，不是供浏览器直接提交任意 URL 的导入接口。

完整制品协议另见：

- [知识包订阅交接接口](knowledge-package-subscription-contract.md)
- [预构建知识向量索引](knowledge-prebuilt-index.md)
- [GitHub Actions 发布示例](knowledge-prebuilt-index-github-actions.md)
