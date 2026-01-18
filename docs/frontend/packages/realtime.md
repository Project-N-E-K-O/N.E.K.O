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

以下示例展示如何将 `@project_neko/realtime` 与 `@project_neko/components` 的 `ChatContainer` 组件集成，实现实时文本对话。

**重要**：消息协议必须与 `templates/index.html` + `static/app.js`（Legacy 实现）保持一致。

```tsx
import { useState, useEffect, useRef, useCallback } from "react";
import { ChatContainer } from "@project_neko/components";
import type { ChatMessage } from "@project_neko/components";
import { createRealtimeClient, buildWebSocketUrlFromBase } from "@project_neko/realtime";
import type { RealtimeClient, RealtimeConnectionState } from "@project_neko/realtime";

function ChatApp() {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<RealtimeConnectionState>("idle");
  const [isTextSessionActive, setIsTextSessionActive] = useState(false);
  const clientRef = useRef<RealtimeClient | null>(null);
  const messageIdRef = useRef(0);
  const assistantTextBuffer = useRef<string>("");

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

  // Flush 累积的 AI 响应
  const flushAssistantBuffer = useCallback(() => {
    const text = assistantTextBuffer.current.trim();
    if (text) {
      addChatMessage("assistant", text);
      assistantTextBuffer.current = "";
    }
  }, [addChatMessage]);

  // 处理服务器消息（与 Legacy 协议一致）
  const handleServerMessage = useCallback((json: unknown) => {
    const msg = json as Record<string, unknown>;
    const type = msg?.type as string | undefined;

    if (type === "session_started") {
      // Session 启动成功
      setIsTextSessionActive(true);
    } else if (type === "gemini_response") {
      // AI 流式响应
      const text = msg.text as string | undefined;
      const isNewMessage = msg.isNewMessage as boolean | undefined;

      if (isNewMessage && assistantTextBuffer.current) {
        flushAssistantBuffer();
      }
      if (text) {
        assistantTextBuffer.current += text;
      }
    } else if (type === "user_transcript") {
      // 用户语音转录
      const content = msg.text as string;
      if (content) addChatMessage("user", content);
    } else if (type === "system") {
      // 系统消息
      const data = msg.data as string | undefined;
      if (data === "turn end") {
        flushAssistantBuffer();
      }
    }
  }, [addChatMessage, flushAssistantBuffer]);

  // 初始化 WebSocket 客户端
  useEffect(() => {
    const client = createRealtimeClient({
      path: "/ws/lanlan_name",
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

  // 检测是否为移动端
  const isMobile = useCallback(() => {
    return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
      navigator.userAgent
    );
  }, []);

  // 发送文本 session 初始化（与 Legacy 一致）
  const ensureTextSession = useCallback(async () => {
    if (isTextSessionActive) return true;

    const client = clientRef.current;
    if (!client || connectionStatus !== "open") return false;

    return new Promise<boolean>((resolve) => {
      const off = client.on("json", ({ json }) => {
        const msg = json as Record<string, unknown>;
        if (msg?.type === "session_started") {
          off();
          setIsTextSessionActive(true);
          resolve(true);
        }
      });

      // 发送 start_session（Legacy 协议）
      client.sendJson({
        action: "start_session",
        input_type: "text",
        new_session: false,
      });

      setTimeout(() => {
        off();
        resolve(false);
      }, 15000);
    });
  }, [isTextSessionActive, connectionStatus]);

  return (
    <ChatContainer
      externalMessages={chatMessages}
      connectionStatus={connectionStatus}
      onSendMessage={async (text, images) => {
        const client = clientRef.current;
        if (!client || connectionStatus !== "open") return;

        // 确保 session 已启动
        const sessionOk = await ensureTextSession();
        if (!sessionOk) return;

        // 先发送截图（每张单独发送，使用 stream_data action）
        if (images && images.length > 0) {
          for (const imgBase64 of images) {
            client.sendJson({
              action: "stream_data",
              data: imgBase64,
              input_type: isMobile() ? "camera" : "screen",
            });
          }
          addChatMessage("user", `📸 [已发送${images.length}张截图]`);
        }

        // 再发送文本（使用 stream_data action）
        if (text.trim()) {
          client.sendJson({
            action: "stream_data",
            data: text,
            input_type: "text",
          });
          addChatMessage("user", text);
        }
      }}
    />
  );
}
```

关键要点：
- **Session 初始化**：首次发送消息前需要发送 `start_session` action
- **消息格式**：使用 `action: "stream_data"` + `input_type: "text"/"screen"/"camera"`
- **流式响应**：累积 `gemini_response` 消息，在 `turn end` 时 flush
- **连接状态同步**：将 `connectionStatus` 传递给 `ChatContainer` 以显示连接指示器

#### 截图/拍照发送流程

`ChatContainer` 组件内置了截图/拍照功能，根据平台自动选择采集方式：

| 平台 | API | input_type |
|------|-----|------------|
| 桌面端 | `navigator.mediaDevices.getDisplayMedia` | `"screen"` |
| 移动端 | `navigator.mediaDevices.getUserMedia` | `"camera"` |

**图片处理规范**：
- 最大尺寸：1280×720（等比缩放）
- 格式：JPEG（质量 0.8）
- 每次最多 5 张待发送截图

**发送协议**：
```json
{
  "action": "stream_data",
  "data": "data:image/jpeg;base64,/9j/4AAQ...",
  "input_type": "screen"  // 或 "camera"
}
```

**注意**：每张截图作为单独的 `stream_data` 消息发送，而非批量发送。

详细规范参见：[Chat Text Conversation Feature Spec](../spec/chat-text-conversation.md)

---

#### Sync to N.E.K.O.-RN Notes

- RN 侧同步目录：`N.E.K.O.-RN/packages/project-neko-realtime`。
- 目标目录视为生成物；如需改动请回到 `@N.E.K.O/frontend/packages/realtime`。

