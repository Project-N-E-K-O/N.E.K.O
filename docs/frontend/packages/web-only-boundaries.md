### Web-only 边界说明：`components` 与 `web-bridge`

本页用于明确：哪些内容 **不能被当作跨端 packages 复用**，以及它们在整体分层中的职责边界。\n
---

### 1. `@project_neko/components`：当前是 Web UI 组件库（不保证 RN 可运行）

- **位置**：`@N.E.K.O/frontend/packages/components`\n- **现状依赖**：\n  - `react-dom`（Portal）\n  - `.css` 样式文件\n  - `window/document/navigator` 等浏览器 API\n- **结论**：\n  - 该包**可以**用于 React Web 与 legacy HTML（通过 UMD bundles），但**不能默认用于 iOS/Android RN**。\n  - RN 如果要共享 UI，需要：新增 `index.native.ts` + RN 组件实现 + `package.json` 条件导出（`react-native`），并把 DOM/ReactDOM 依赖隔离到 web 入口。\n
典型 Web-only 例子（非完整清单）：\n- `StatusToast`：创建 DOM 容器并 `createPortal`。\n- `ChatContainer`：使用 `navigator.mediaDevices.getDisplayMedia` 做截图。\n
---

### 2. `@project_neko/web-bridge`：唯一允许“跨界”的 window 绑定层（Web-only）

- **位置**：`@N.E.K.O/frontend/packages/web-bridge`\n- **职责**：把 packages 的能力暴露到 `window`，供 legacy HTML/原生 JS 使用：\n  - `window.request`（axios 实例）\n  - `window.showStatusToast / window.showAlert/showConfirm/showPrompt`\n  - `window.buildApiUrl/buildStaticUrl/buildWebSocketUrl`\n  - `window.createRealtimeClient` 等\n- **边界**：\n  - 允许读写 `window`、监听全局事件（这是 bridge 的定义域）。\n  - 不应把 bridge 逻辑分散到 `common/request/realtime/...` 这类基础包。\n  - **RN native 侧不应依赖该包**（除非你明确要在 Expo Web 复用 window API）。\n
legacy HTML 引入顺序建议（避免依赖未就绪）：\n- i18n（如需 `window.t`）\n- request bundle\n- web-bridge bundle（提供 `window.request` 等）\n- React/ReactDOM UMD\n- components.css + components UMD\n
---

### 3. 与“同步到 N.E.K.O.-RN”的关系（方案 A）

当前同步策略强调：\n- 公共规范在 `@N.E.K.O/docs/frontend` 维护（SSOT）\n- RN 侧 docs 仅引用入口页，不复制正文\n- packages 同步采用镜像拷贝时：目标目录应视为生成物\n
因此：\n- `components`/`web-bridge` 在 RN repo 的存在意义主要是“对齐接口/复用 types/为未来做结构预留”，而不是直接在 iOS/Android 运行。\n
