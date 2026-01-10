### `@project_neko/request`（跨端请求库：Axios + Token 刷新 + 队列）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/request`\n- **职责**：提供可复用的请求客户端构建能力：\n  - Axios instance\n  - access token 自动注入\n  - 401 自动 refresh（通过 `refreshApi` 注入）\n  - refresh 期间新请求排队（避免并发重复刷新）\n  -（可选）请求/响应日志（开发默认开）\n- **非目标**：不内置业务 refresh API（由宿主注入）；不绑定 `window`（那是 `web-bridge` 的职责）。\n
---

#### Public API（推荐用法）

- **创建客户端（跨端）**：\n  - `import { createRequestClient } from "@project_neko/request";`\n- **Web 默认 token 存储**：\n  - `import { WebTokenStorage } from "@project_neko/request";`\n- **RN 默认 token 存储**：\n  - `import { NativeTokenStorage } from "@project_neko/request";`（仅 native 入口）\n- **Web 便利默认实例**：\n  - `import { request } from "@project_neko/request/web";`（或 `index.web.ts` 导出）\n
---

#### Entry points & exports（关键）

- `index.ts`（默认入口，必须 Web/SSR/Metro 安全）\n  - 只导出 **Web 安全**内容：`createRequestClient`、`WebTokenStorage`、`storage` 抽象（默认回退 web）。\n- `index.web.ts`\n  - 提供 Web 端 `request` 默认实例（baseURL=`/api`）与默认 refresh 实现（示例逻辑）。\n- `index.native.ts`\n  - 提供 `createNativeRequestClient({ baseURL, refreshApi })`，并使用 `NativeTokenStorage`。\n- `package.json` 条件导出：\n  - `exports["."]` 同时声明 `react-native`、`browser`、`default`。\n  - **要点**：避免 Metro/Expo Web 解析到 RN 依赖。\n
---

#### Key modules

- `createClient.ts`\n  - 核心：`createRequestClient(options: RequestClientConfig)`。\n  - 处理链路：\n    - request interceptor：注入 token；若 `RequestQueue.isRefreshing` 则挂起并入队\n    - axios-auth-refresh：401 时触发 refresh；成功后 flush 队列；失败则清 token 并 reject 队列\n  - 日志：通过 `__NEKO_VITE_MODE__ / __NEKO_VITE_NODE_ENV__ / __DEV__ / process.env.NODE_ENV` best-effort 判断开发模式。\n- `src/request-client/requestQueue.ts`\n  - `RequestQueue`：管理 refresh 期间的挂起请求（resolve/reject），避免并发刷新。\n- `src/request-client/tokenStorage.web.ts`\n  - **纯 Web** TokenStorage：严禁引入 RN 依赖（避免 Expo Web/Metro 解析失败）。\n- `src/request-client/tokenStorage.ts`\n  - 同时包含 `WebTokenStorage` + `NativeTokenStorage`；其中 native 部分通过动态 import 加载 AsyncStorage storage。\n  - **注意**：默认入口 `index.ts` 刻意不导出这个文件里的 `NativeTokenStorage`。\n- `src/storage/*`\n  - `webStorage.ts`：localStorage。\n  - `nativeStorage.ts`：动态 import `@react-native-async-storage/async-storage`，并提供 `__resetNativeStorageInternal` 便于测试。\n  - `index.web.ts / index.native.ts / index.ts`：平台选择与回退策略。\n
---

#### Platform Notes（常见坑）

- **Expo Web/Metro 解析问题**：\n  - Web 入口必须避免静态引入任何 RN-only 模块（例如 AsyncStorage、react-native）。\n  - 因此 Web 的 tokenStorage 使用 `tokenStorage.web.ts` 单独文件隔离。\n- **SSR**：\n  - `webStorage.ts` 依赖 `localStorage`，只能在浏览器环境调用；默认入口不应在 SSR 初始化阶段直接触发它。\n
---

#### Sync to N.E.K.O.-RN Notes

- RN 侧同步目录：`N.E.K.O.-RN/packages/project-neko-request`。\n- 镜像拷贝策略下，目标目录视为生成物；RN 侧不要直接修改同步来的源码。\n
---

#### Testing / Typecheck

- `tsconfig.native.json`：用于确保 native build 不被 DOM 类型污染。\n- `__mocks__/async-storage.ts` + `vitest.config.ts`：为测试提供 AsyncStorage stub。\n
