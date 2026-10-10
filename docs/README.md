# docs/

The public documentation site, [project-neko.online](https://project-neko.online) (player handbook, guides, architecture, API reference and plugin development), is maintained in the [N.E.K.O.WIKI repository](https://github.com/Project-N-E-K-O/N.E.K.O.WIKI). Send documentation changes there.

This directory keeps only material that belongs next to the code:

| Path | Contents |
| --- | --- |
| `design/` | Design and implementation records, RFCs and proposals |
| `records/`, `zh-CN/records/`, `ja/records/` | Incident records and the records index |
| `benchmarks/` | Dated runtime measurements |
| `development/` | Development notes; `voice-readiness.md` is read by the app at runtime |
| `zh-CN/guide/openclaw_guide*.md`, `zh-CN/guide/assets/` | OpenClaw guide served by the app and bundled into desktop builds |
| `README_en.md`, `README_ja.md`, `README_ru.md` | Translations of the root README |
| `*.md` at this level | Scoped implementation plans and records |

Keep the runtime-read paths stable: `main_routers/agent_router.py`, `main_routers/voice_identity_router.py` and the desktop build workflows reference them directly.

---

# docs/（中文说明）

公开文档站 [project-neko.online](https://project-neko.online)（玩家手册、指南、架构、API 参考与插件开发）已迁移到 [N.E.K.O.WIKI 仓库](https://github.com/Project-N-E-K-O/N.E.K.O.WIKI) 维护，文档修改请提交到那里。

本目录只保留需要和代码放在一起的内容：设计记录（`design/`）、事故记录（`records/`）、基准数据（`benchmarks/`）、开发笔记（`development/`，其中 `voice-readiness.md` 由应用运行时读取）、应用内提供并打包进桌面版的 OpenClaw 教程（`zh-CN/guide/openclaw_guide*.md` 与 `zh-CN/guide/assets/`），以及根 README 的翻译版。运行时读取的路径必须保持稳定。
