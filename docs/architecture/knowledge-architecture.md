# Knowledge / Corpus 架构

> **架构更新（2026-08-24）**：公共知识域通过中立的本地 Embedding 门面使用进程内模型；具体实现只在 Main Server 和维护脚本的组合入口绑定。知识模块不再反向依赖记忆业务模块，也不会把知识正文、索引或查询发送给 Memory Server。未绑定提供方时向量能力安全关闭，检索继续降级为 BM25。

历史上的 `moegirl`、`meme` 和 `public-knowledge` 命名只代表早期数据来源或实现目录，不再构成当前产品架构。

当前系统只有一个公共知识数据库和一个检索入口：

- `material_type=knowledge`：事实、解释、定义和梗义；
- `material_type=corpus`：回复、对话和写作参考；
- `domain:meme`：可选主题标签，只影响回答风格，不创建独立数据库或检索接口。

正式版本只识别最终统一数据库路径 `knowledge/knowledge.db`。本 PR 开发期间出现过的
`public-knowledge/knowledge.db`、`moegirl-knowledge/knowledge.db` 和
`corpora/knowledge.db` 不属于已发布格式；运行时不会读取、改写、迁移或删除这些目录。
开发者若运行过早期分支，应删除本地测试数据并从原知识包重新导入。

## Embedding 所有权与进程组合

本地 ONNX Embedding 的实现仍由 `memory/embeddings.py` 维护，但跨业务调用只面向
`utils/local_embedding_runtime.py` 的中立接口。Main Server 启动知识索引器前，以及构建、评估、重建知识索引的独立脚本启动时，由 `memory/local_embedding_provider.py` 在进程边界完成绑定：

```text
Main Server / 知识维护脚本（组合入口）
  → memory/local_embedding_provider.py（绑定实现）
  → utils/local_embedding_runtime.py（中立门面）
  ← knowledge/indexer.py、vector_index.py、service.py（只依赖门面）
```

这是代码复用，不是数据或业务耦合。公共知识与用户记忆分别拥有数据库、生命周期、容量预算和失败语义；知识域不调用 Memory Server API。中立门面尚未绑定时返回 `provider_unconfigured`，不会隐式导入记忆模块或启动模型，知识查询仍可使用 BM25。

相关文档：

- [Knowledge / Corpus 知识包编写与发布](knowledge-corpus-authoring.md)
- [知识包暂存与激活](knowledge-pack-staging.md)
- [知识包订阅交接接口](knowledge-package-subscription-contract.md)
- [预构建知识向量索引](knowledge-prebuilt-index.md)
