# @project_neko/realtime

跨平台 Realtime(WebSocket) 客户端封装，目标：

- React Web（`N.E.K.O/frontend`）
- 旧版模板页（`templates/*.html` + `static/*.js`，通过 UMD 全局变量使用）
- React Native（`N.E.K.O.-RN`）

## 核心设计

- **核心实现不依赖 DOM 类型**：使用 `WebSocketLike` 结构类型，兼容 Web 与 RN 的 WebSocket 实现
- **通用能力**：心跳、自动重连、消息分流（text/json/binary）
- **URL 构建可注入**：Web 推荐使用 `window.buildWebSocketUrl`（由 `@project_neko/web-bridge` 注入）

## Web（React / 现代前端）用法

```ts
import { createRealtimeClient } from "@project_neko/realtime";

const client = createRealtimeClient({
  path: "/ws/my_catgirl",
  buildUrl: window.buildWebSocketUrl, // 若 web-bridge 已注入
  heartbeat: { intervalMs: 30_000, payload: { action: "ping" } },
  reconnect: { enabled: true },
});

client.on("json", ({ json }) => {
  // json 为 unknown，建议上层做协议类型收窄
  console.log("json", json);
});

client.connect();
```

## 旧版 HTML/JS（UMD）用法

构建后会产出：

- `/static/bundles/realtime.js`（UMD，全局 `window.ProjectNekoRealtime`）
- `/static/bundles/realtime.es.js`（ESM）

示例：

```js
var client = window.ProjectNekoRealtime.createRealtimeClient({
  path: "/ws/my_catgirl",
  buildUrl: window.buildWebSocketUrl, // 若引入了 web-bridge
});
client.on("json", function (evt) {
  console.log(evt.json);
});
client.connect();
```

## React Native 用法

RN 侧通常需要显式传入 `url`（因为没有 `location`）：

```ts
import { createNativeRealtimeClient } from "@project_neko/realtime";

const client = createNativeRealtimeClient({
  url: "wss://example.com/ws/my_catgirl",
  heartbeat: { intervalMs: 30_000 },
  reconnect: { enabled: true },
});

client.on("text", ({ text }) => console.log(text));
client.connect();
```

## 注意事项

- **鉴权**：浏览器 WebSocket 通常不能自定义 headers。建议使用：
  - Cookie（同域）
  - URL query（短期 token）
  - WebSocket subprotocol（如后端支持）
- **二进制消息**：Web 可能收到 `Blob/ArrayBuffer`；RN 可能收到平台特定对象。上层按实际类型处理。


