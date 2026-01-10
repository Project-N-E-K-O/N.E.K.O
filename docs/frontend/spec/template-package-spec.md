### Package Spec 模板（适合 packages 规范说明）

> 复制本模板创建 `docs/frontend/packages/<pkg>.md`，或用于新 package 设计评审。

---

### 1. Overview

- **包名**：`@project_neko/<name>`\n- **位置**：`@N.E.K.O/frontend/packages/<name>`\n- **一句话职责**：\n
### 2. Goals / Non-goals

- **Goals**：\n- **Non-goals**：\n
---

### 3. Responsibilities & Boundaries（职责边界）

- **应该做**：\n- **不应该做**：\n  - 是否允许依赖 DOM？\n  - 是否允许依赖 React？\n  - 是否允许读写 `window`？（通常仅 `web-bridge`）\n
---

### 4. Public API（对外 API 面）

- **入口与导出**：\n  - `index.ts`：\n  - `index.web.ts`（如有）：\n  - `index.native.ts`（如有）：\n- **推荐用法**：\n  - `import { ... } from "@project_neko/<name>";`\n
---

### 5. Entry points & conditional exports（关键：多端解析）

#### 5.1 文件入口

- `index.ts`（默认入口，要求 SSR/Metro 安全）\n- `index.web.ts`（Web 便利层/默认实例）\n- `index.native.ts`（RN 便利层/默认实例）\n
#### 5.2 package.json 约定

- `react-native` 字段：Metro 优先\n- `browser` 字段：Web bundler 优先（如适用）\n- `exports` 条件导出：推荐写法与限制\n
---

### 6. Key modules（关键模块说明）

按目录分节，不要求逐文件：

- `src/<module>`：\n  - 目标/核心职责\n  - 关键类型/关键函数\n  - 常见坑\n
---

### 7. Platform Notes（跨端差异）

- **Web**：\n- **React Native**：\n- **legacy HTML+JS（UMD）**：\n
---

### 8. Sync to N.E.K.O.-RN Notes（同步策略）

当前策略：以 `@N.E.K.O/frontend/packages` 为源；RN 侧通过脚本同步（镜像拷贝）。

- **同步脚本**：`@N.E.K.O.-RN/scripts/sync-neko-packages.js`\n- **是否允许 RN 侧手改**：\n- **Overlay 是否需要**：\n
---

### 9. Testing / Typecheck

- **typecheck**：\n  - `tsc --noEmit`\n  - `tsconfig.native.json`（lib 不含 DOM）\n- **tests**：\n  - Vitest/Jest 覆盖点\n
