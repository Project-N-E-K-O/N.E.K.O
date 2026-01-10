### 前端 packages 多端兼容与同步文档

本目录用于文档化 `@N.E.K.O/frontend/packages/*` 的 **多端（React Web / legacy HTML+JS / React Native）兼容设计**，以及以 `@N.E.K.O/frontend/packages` 为**源**，向 `@N.E.K.O.-RN/packages` **迁移/同步代码**的维护流程。

### 最新更新（2026-01-10）

- **类型导入修复** - 修复 `@project_neko/common` 的 `Unsubscribe` 类型导入错误 → [详情](./SUMMARY-type-import-fix.md)
- **Chat 组件同步** - 从 N.E.K.O.-RN 同步 ChatContainer 和 ChatInput 组件 → [详情](./SYNC-chat-components-2026-01-10.md)
- **TinyEmitter 重构** - 将事件发射器提取到 common 包 → [详情](./SUMMARY-tinyemitter-refactor.md)
- **Metro/Vite 依赖修复** - 修复多端开发环境依赖问题 → [详情](./SUMMARY-metro-vite-dependency-fix.md)

### 文档归属与同步策略（与 N.E.K.O.-RN 的关系）

- **公共部分（Single Source of Truth）**：统一维护在本目录（`@N.E.K.O/docs/frontend`）。
  - 包括：packages 分层/入口规范、跨端契约、同步策略与常见坑等。
- **RN 专属部分**：维护在 `@N.E.K.O.-RN/docs/**`（Expo/Metro、原生模块、RN 页面结构与调试发布等）。
- **同步方式（方案 A）**：RN 文档仅做“入口页链接”引用本目录内容，不复制正文，避免双份漂移。
  - 若上游文档文件名/路径变更，需同步更新 RN 的入口链接页（并可通过 RN 侧的链接检查脚本发现问题）。

### 阅读顺序

- `packages-multi-platform.md`：packages 的分层原则与入口规范（index / index.web / index.native / exports）。
- `packages-sync-to-neko-rn.md`：同步到 `N.E.K.O.-RN` 的目录映射、同步脚本、overlay 策略与常见坑。

### 目录导航

- **Spec 规范（适合 Cursor 工作区）**：`./spec/README.md`
  - Feature 模板：`./spec/template-feature-spec.md`
  - Package 模板：`./spec/template-package-spec.md`
- **Packages 文档（逐包说明）**：`./packages/README.md`

