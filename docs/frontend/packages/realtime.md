### `@project_neko/realtime`（跨端 WebSocket 客户端：重连 + 心跳 + 事件）

#### Overview

- **位置**：`@N.E.K.O/frontend/packages/realtime`
- **职责**：提供跨端 Realtime(WebSocket) 客户端构造器：
  - 连接状态机（idle/connecting/open/closing/closed/reconnecting）
  - 心跳（interval + payload）
  - 断线重连（指数退避 + jitter + 最大尝试次数 + shouldReconnect hook）
  - 事件分发（text/json/binary/message/open/close/error/state）
- **非目标**：不负责“业务协议”；不自动连接（除非宿主显式调用 connect）。

---

#### Public API（推荐用法）

- `import { createRealtimeClient } from "@project_neko/realtime";`
- Web 便利入口：
  - `import { createWebRealtimeClient } from "@project_neko/realtime/web";`（或 `index.web.ts`）
- RN 便利入口：
  - `import { createNativeRealtimeClient } from "@project_neko/realtime";`（native 入口导出）

---

#### Entry points & exports

- `index.ts`
  - 导出 types、`createRealtimeClient`、以及 URL helper（`buildWebSocketUrlFromBase` 等）。
- `index.web.ts`
  - 提供 `createWebRealtimeClient()`：
    - 优先使用 `window.buildWebSocketUrl`（若页面引入 `web-bridge`）
    - 否则回退到 `location` 推导 ws base
- `index.native.ts`
  - 提供 `createNativeRealtimeClient()`：
    - RN 环境通常没有 `location`，建议显式传 `url/buildUrl`。
- `package.json` 条件导出：
  - `exports["."]`：react-native / default
  - `exports["./web"]`：web 便利入口

---

#### Key modules

- `src/client.ts`
  - 核心：`createRealtimeClient(options)`。
  - 特性：
    - `webSocketCtor` 可注入（解决某些环境没有全局 WebSocket 的情况）
    - `connect()` 只在 idle/closed 时生效（防止重复 connect 打断心跳）
    - `handleMessage()`：字符串走 text/json；非字符串走 binary（兼容 Blob/ArrayBuffer/TypedArray/RN polyfill）
- `src/url.ts`
  - `buildWebSocketUrlFromBase(base, path)`：统一 http/https/ws/wss → ws/wss
  - `defaultWebSocketBaseFromLocation()`：仅浏览器可用，RN 返回空字符串
- `src/types.ts`
  - 事件 map、options（heartbeat/reconnect）等

---

#### Platform Notes

- **Web**：可直接用全局 WebSocket；也可用 `web-bridge` 提供的 URL builder。
- **React Native**：如果 WebSocket polyfill 行为不同，建议显式传 `webSocketCtor`。
- **legacy HTML+JS**：通过 Vite 构建产物（UMD/ES）供 `<script>` 使用；也可通过 `web-bridge` 暴露到 `window.createRealtimeClient`。

---

#### 与 ChatContainer 集成示例

以下示例展示如何将 `@project_neko/realtime` 与 `@project_neko/components` 的 `ChatContainer` 组件集成，实现实时文本对话：

```tsx
import { useState, useEffect, useRef, useCallback } from "react";
import { ChatContainer } from "@project_neko/components";
import type { ChatMessage } from "@project_neko/components";
import { createRealtimeClient, buildWebSocketUrlFromBase } from "@project_neko/realtime";
import type { RealtimeClient, RealtimeConnectionState } from "@project_neko/realtime";

function ChatApp() {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<RealtimeConnectionState>("idle");
  const clientRef = useRef<RealtimeClient | null>(null);
  const messageIdRef = useRef(0);

  // 生成消息 ID
  const generateMessageId = useCallback(() => {
    messageIdRef.current += 1;
    return `msg-${Date.now()}-${messageIdRef.current}`;
  }, []);

  // 添加消息到列表
  const addChatMessage = useCallback((role: ChatMessage["role"], content: string) => {
    const msg: ChatMessage = {
      id: generateMessageId(),
      role,
      content,
      createdAt: Date.now(),
    };
    setChatMessages((prev) => [...prev, msg]);
  }, [generateMessageId]);

  // 处理服务器消息
  const handleServerMessage = useCallback((json: unknown) => {
    const msg = json as Record<string, unknown>;
    const type = msg?.type as string | undefined;

    if (type === "transcript" || type === "user_transcript") {
      const content = (msg.content || msg.text) as string;
      if (content) addChatMessage("user", content);
    } else if (type === "assistant_text" || type === "response.done") {
      const content = (msg.content || msg.text || msg.transcript) as string;
      if (content) addChatMessage("assistant", content);
    }
  }, [addChatMessage]);

  // 初始化 WebSocket 客户端
  useEffect(() => {
    const client = createRealtimeClient({
      path: "/ws/chat",
      buildUrl: (path) => buildWebSocketUrlFromBase("ws://localhost:48911", path),
      heartbeat: { intervalMs: 30_000, payload: { action: "ping" } },
      reconnect: { enabled: true },
    });
    clientRef.current = client;

    const offState = client.on("state", ({ state }) => setConnectionStatus(state));
    const offJson = client.on("json", ({ json }) => handleServerMessage(json));

    client.connect();

    return () => {
      offState();
      offJson();
      client.disconnect();
    };
  }, [handleServerMessage]);

  return (
    <ChatContainer
      externalMessages={chatMessages}
      connectionStatus={connectionStatus}
      onSendMessage={(text, images) => {
        if (clientRef.current && connectionStatus === "open") {
          clientRef.current.sendJson({
            action: "send_text",
            text,
            images,
          });
        }
      }}
    />
  );
}
```

关键要点：
- **连接状态同步**：将 `realtimeState` 传递给 `ChatContainer` 以显示连接指示器
- **消息处理**：通过 `json` 事件接收服务器消息，解析后添加到 `externalMessages`
- **发送消息**：通过 `onSendMessage` 回调使用 `client.sendJson()` 发送

详细规范参见：[Chat Text Conversation Feature Spec](../spec/chat-text-conversation.md)

---

#### Sync to N.E.K.O.-RN Notes

- RN 侧同步目录：`N.E.K.O.-RN/packages/project-neko-realtime`。
- 目标目录视为生成物；如需改动请回到 `@N.E.K.O/frontend/packages/realtime`。

