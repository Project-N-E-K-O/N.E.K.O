# 知识包订阅交接接口（可信市场 v1）

> **首发契约（2026-08-24）**：市场交付协议、知识包 Schema、索引清单、Embedding 输入与分块规则统一为 v1。Main Server 校验预构建制品时不会加载本地 Embedding 模型，也不会调用 Memory Server。

## 定位

知识包是纯数据制品，不是可执行插件。Main Server 统一负责存储和检索；Market Bridge 只解析可信目录、受限下载并把制品交给 Main Server 二次校验。

```text
可信市场 protocol v1 descriptor
  → Market Bridge 校验 HTTPS、主机、大小和 SHA-256
  → POST /api/public-knowledge/subscriptions/apply
  → Main Server 校验 Schema v1、确定性 chunk 和向量绑定
  → 原子替换 source:community.<pack_id>
  → 索引有效则 Hybrid，否则原文仍以 BM25 激活
```

## 三制品

| 制品 | 后缀 | 必需性 |
| --- | --- | --- |
| 原始知识 | `.neko-knowledge.json` | 必需，唯一真实来源 |
| 索引清单 | `.neko-knowledge.index.json` | 与 vectors 同时存在或同时缺失 |
| 向量矩阵 | `.neko-knowledge.vectors.f16` | 与 manifest 同时存在或同时缺失 |

原始包必须是规范 UTF-8 JSON，`schema_version=1`，声明 `material_type=knowledge|corpus`，且不含 `collection_id`。每条词条仍只有 `title / terms / tags / summary / content`。

索引损坏、缺失或不兼容只导致 BM25 降级；原始包无效才导致订阅失败。

## 市场版本描述符 v1

```json
{
  "protocol_version": 1,
  "package_id": 42,
  "remote_id": "knowledge/example-pack",
  "pack_id": "example-pack",
  "material_type": "knowledge",
  "version": "1.0.0",
  "channel": "stable",
  "artifacts": {
    "knowledge": {"url": "https://…/example.neko-knowledge.json", "sha256": "…", "bytes": 1024},
    "index_manifest": {"url": "https://…/example.neko-knowledge.index.json", "sha256": "…", "bytes": 2048},
    "vectors": {"url": "https://…/example.neko-knowledge.vectors.f16", "sha256": "…", "bytes": 4096}
  }
}
```

浏览器只提交 `package_id`、`version` 和 `channel`。本地 Bridge 必须从配置的市场 API 获取描述符，不能信任浏览器提供的 URL、摘要或内容用途。Bridge 和 Main Server 都会校验市场登记的 `material_type` 与原始包一致。

## Main Server 交接

可信三制品使用 loopback、Bridge Token、CSRF 和 Origin 保护的 multipart 端点：

```text
POST /api/public-knowledge/subscriptions/apply
```

字段包括订阅元数据、`pack`，以及必须成对出现的可选 `index_manifest` 和 `vectors`。不提供索引制品时，同一端点完成 raw-only 安装并以 BM25 激活；不再维护第二套订阅协议。

严格校验顺序：

1. 校验原始包规范字节、Schema v1、身份、市场登记用途、容量和 SHA-256。
2. 从五字段原文重新派生完整 chunk 序列。
3. 逐项比对 `chunk_id`、`content_hash` 和连续 `vector_index`。
4. 校验固定模型契约、清单摘要、向量摘要和精确文件长度。
5. 以 `<f2` 解码，要求 256 维、有限数值、每行非零。
6. 严格事务导入全部 ready 向量；任一行未绑定则整体回滚索引导入。

## 运行边界

> **更正（2026-08-21）**：旧版契约曾声明 `corpus`“永不自动注入”。现行运行策略已允许 `corpus` 包参与普通聊天的自动素材选择，并通过按包开关及独立高阈值控制误召回；制品格式、可信校验和订阅交接协议不受此策略更正影响。

- 所有包写入同一个公共知识数据库，不再按 meme/corpora 分库。
- `material_type` 是用途策略；`domain:meme` 是可选主题标签。
- `knowledge` 包可由用户开启自动上下文；新安装的 `corpus` 包默认开启，二者均可由用户按包关闭或重新开启。
- 自动上下文共用一次 BM25、Query Embedding 和 RRF 候选池，再按材料类型的独立高阈值筛选；弱相关候选不会仅凭低分语义相似度注入。
- 社区包默认 `prebuilt_only`。可信索引可直接 Hybrid，否则 BM25。
- 本机维护向量必须由用户按包明确开启。
- 本机向量实现只由 Main Server 在进程组合入口绑定；知识域仅使用中立接口，未绑定时以 `provider_unconfigured` 安全关闭向量路径。
- 显式查询始终走同一 BM25、Query Embedding 和 RRF 路径。
- 不写用户记忆，不持久化用户对话，不修改 Memory Server 业务 API。

## 市场接线

```text
POST /market/knowledge/subscribe
GET  /market/knowledge/tasks/{task_id}
GET  /market/knowledge/subscriptions
POST /market/knowledge/unsubscribe
```

安装结果以本地 `packs.json` 为准；市场账户同步失败不回滚已经安全落地的知识包。
