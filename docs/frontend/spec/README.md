### Spec-Driven Docs（SDD）规范（适配 Cursor 工作区）

本目录用于存放 **`@N.E.K.O/docs/frontend` 的公共规范（Single Source of Truth）**：以文档驱动跨端 packages 的设计、同步策略、以及后续需求/协议/实现的演进。

该规范面向 **Cursor 多仓库工作区**的使用方式（同一工作区同时打开 `N.E.K.O` 与 `N.E.K.O.-RN`），并与当前“方案 A：RN 仅引用上游文档，不复制正文”的策略兼容。

---

### 1. 文档分层（建议）

在 `docs/frontend` 下建议用三类文档表达不同目的（不要混写）：

- **Spec（规范/契约）**：必须稳定、可审查、可被 AI/人类共同执行的“真理源”。
  - 例：packages 分层规则、入口 exports 约定、跨端协议字段、状态机、错误码。
  - 位置：`docs/frontend/spec/*`
- **Reference（参考/说明）**：对现有实现的解释与定位（帮助快速读懂代码）。
  - 例：每个 package 的职责边界、关键模块解释、常见坑。
  - 位置：`docs/frontend/packages/*`
- **Guide（指南/流程）**：操作步骤与验收清单（如何做事）。
  - 例：如何同步 packages、如何在 legacy HTML 引入 bundles、如何跑 typecheck。
  - 位置：可放在 `docs/frontend/*` 或未来扩展 `docs/frontend/guide/*`

---

### 2. 更新流程（必须）

- **先文档后代码**：任何会影响跨端行为/入口导出/协议字段/同步策略的改动，必须先更新 Spec。
- **文档与代码必须可互相定位**：文档里给出明确的文件路径与关键模块名称（不要只描述“某处”）。
- **公共部分只在上游维护**：`@N.E.K.O/docs/frontend/*` 为公共 SSOT；RN 侧 docs 仅引用入口页链接（方案 A）。

---

### 3. Cursor 使用约定（强烈建议）

#### 3.1 路径与链接

- 统一用 `@N.E.K.O/...` 或 `@N.E.K.O.-RN/...` 的“仓库根相对路径”来表达位置（人类/AI 都好找）。
  - 示例：`@N.E.K.O/frontend/packages/request/createClient.ts`
- 同仓库内链接：使用相对路径（例如 `../packages/request.md`）。
- 跨仓库链接（同一 Cursor 工作区）：允许使用“跨仓库相对路径”（例如 RN 的入口页链接到 `../../N.E.K.O/docs/frontend/...`）。
  - 注意：这种链接在 GitHub 网页端可能无法跳转，但在 Cursor 本地工作区可用。

#### 3.2 代码引用方式

为了让读者快速定位，文档中建议至少包含以下之一：

- **文件路径 + 关键符号名**：如 `createRequestClient()`、`RequestQueue`、`createRealtimeClient()`。
- **关键“入口文件”列表**：`index.ts` / `index.web.ts` / `index.native.ts` / `package.json`。
- 如果需要贴代码片段：只贴最小必要片段，并注明来源文件与用途（避免整文件复制）。

---

### 4. 模板

- `template-feature-spec.md`：功能 spec 模板（适合写协议/状态机/用户故事）。
- `template-package-spec.md`：package 规范模板（适合写职责边界/入口与 exports/平台矩阵）。

