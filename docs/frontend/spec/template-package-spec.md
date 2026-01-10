### Package Spec 模板（适合 packages 规范说明）

> 复制本模板创建 `docs/frontend/packages/<pkg>.md`，或用于新 package 设计评审。

---

### 1. Overview

- **包名**：`@project_neko/<name>`
- **位置**：`@N.E.K.O/frontend/packages/<name>`
- **一句话职责**：

### 2. Goals / Non-goals

- **Goals**：
- **Non-goals**：

---

### 3. Responsibilities & Boundaries（职责边界）

- **应该做**：
- **不应该做**：
  - 是否允许依赖 DOM？
  - 是否允许依赖 React？
  - 是否允许读写 `window`？（通常仅 `web-bridge`）

---

### 4. Public API（对外 API 面）

- **入口与导出**：
  - `index.ts`：
  - `index.web.ts`（如有）：
  - `index.native.ts`（如有）：
- **推荐用法**：
  - `import { ... } from "@project_neko/<name>";`

---

### 5. Entry points & conditional exports（关键：多端解析）

#### 5.1 文件入口

- `index.ts`（默认入口，要求 SSR/Metro 安全）
- `index.web.ts`（Web 便利层/默认实例）
- `index.native.ts`（RN 便利层/默认实例）

#### 5.2 package.json 约定

- `react-native` 字段：Metro 优先
- `browser` 字段：Web bundler 优先（如适用）
- `exports` 条件导出：推荐写法与限制

---

### 6. Key modules（关键模块说明）

按目录分节，不要求逐文件：

- `src/<module>`：
  - 目标/核心职责
  - 关键类型/关键函数
  - 常见坑

---

### 7. Platform Notes（跨端差异）

- **Web**：
- **React Native**：
- **legacy HTML+JS（UMD）**：

---

### 8. Sync to N.E.K.O.-RN Notes（同步策略）

当前策略：以 `@N.E.K.O/frontend/packages` 为源；RN 侧通过脚本同步（镜像拷贝）。

- **同步脚本**：`@N.E.K.O.-RN/scripts/sync-neko-packages.js`
- **是否允许 RN 侧手改**：
- **Overlay 是否需要**：

---

### 9. Testing / Typecheck

- **typecheck**：
  - `tsc --noEmit`
  - `tsconfig.native.json`（lib 不含 DOM）
- **tests**：
  - Vitest/Jest 覆盖点

