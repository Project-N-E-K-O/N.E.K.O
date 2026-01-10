### `@project_neko/realtime`（跨端 WebSocket 客户端：重连 + 心跳 + 事件）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/realtime`\n- **职责**：提供跨端 Realtime(WebSocket) 客户端构造器：\n  - 连接状态机（idle/connecting/open/closing/closed/reconnecting）\n  - 心跳（interval + payload）\n  - 断线重连（指数退避 + jitter + 最大尝试次数 + shouldReconnect hook）\n  - 事件分发（text/json/binary/message/open/close/error/state）\n- **非目标**：不负责“业务协议”；不自动连接（除非宿主显式调用 connect）。\n
---

#### Public API（推荐用法）

- `import { createRealtimeClient } from "@project_neko/realtime";`\n- Web 便利入口：\n  - `import { createWebRealtimeClient } from "@project_neko/realtime/web";`（或 `index.web.ts`）\n- RN 便利入口：\n  - `import { createNativeRealtimeClient } from "@project_neko/realtime";`（native 入口导出）\n
---

#### Entry points & exports

- `index.ts`\n  - 导出 types、`createRealtimeClient`、以及 URL helper（`buildWebSocketUrlFromBase` 等）。\n- `index.web.ts`\n  - 提供 `createWebRealtimeClient()`：\n    - 优先使用 `window.buildWebSocketUrl`（若页面引入 `web-bridge`）\n    - 否则回退到 `location` 推导 ws base\n- `index.native.ts`\n  - 提供 `createNativeRealtimeClient()`：\n    - RN 环境通常没有 `location`，建议显式传 `url/buildUrl`。\n- `package.json` 条件导出：\n  - `exports["."]`：react-native / default\n  - `exports["./web"]`：web 便利入口\n
---

#### Key modules

- `src/client.ts`\n  - 核心：`createRealtimeClient(options)`。\n  - 特性：\n    - `webSocketCtor` 可注入（解决某些环境没有全局 WebSocket 的情况）\n    - `connect()` 只在 idle/closed 时生效（防止重复 connect 打断心跳）\n    - `handleMessage()`：字符串走 text/json；非字符串走 binary（兼容 Blob/ArrayBuffer/TypedArray/RN polyfill）\n- `src/url.ts`\n  - `buildWebSocketUrlFromBase(base, path)`：统一 http/https/ws/wss → ws/wss\n  - `defaultWebSocketBaseFromLocation()`：仅浏览器可用，RN 返回空字符串\n- `src/types.ts`\n  - 事件 map、options（heartbeat/reconnect）等\n
---

#### Platform Notes

- **Web**：可直接用全局 WebSocket；也可用 `web-bridge` 提供的 URL builder。\n- **React Native**：如果 WebSocket polyfill 行为不同，建议显式传 `webSocketCtor`。\n- **legacy HTML+JS**：通过 Vite 构建产物（UMD/ES）供 `<script>` 使用；也可通过 `web-bridge` 暴露到 `window.createRealtimeClient`。\n
---

#### Sync to N.E.K.O.-RN Notes

- RN 侧同步目录：`N.E.K.O.-RN/packages/project-neko-realtime`。\n- 目标目录视为生成物；如需改动请回到 `@N.E.K.O/frontend/packages/realtime`。\n
