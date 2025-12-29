import React, { useState } from "react";
import type { ChatMessage } from "./types";
import MessageList from "./MessageList";
import ChatInput from "./ChatInput";

import { useT, tOrDefault } from "../i18n";

/** 生成跨环境安全的 id */
function generateId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }

  // fallback：RFC4122 v4-ish
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export default function ChatContainer() {
  const t = useT();
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "sys-1",
      role: "system",
      content: tOrDefault(t, "chat.welcome", "欢迎来到 React 聊天系统（迁移 Demo）"),
      createdAt: Date.now(),
    },
  ]);

  function handleSend(text: string) {
    const userMsg: ChatMessage = {
      id: generateId(),
      role: "user",
      content: text,
      createdAt: Date.now(),
    };

    const botMsg: ChatMessage = {
      id: generateId(),
      role: "assistant",
      content: `${tOrDefault(t, "chat.response_prefix", "你刚刚说的是：")}${text}`,
      createdAt: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg, botMsg]);
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        maxWidth: 400, // Match template width or keep flexible? Template is 400px fixed usually, but let's stick to max-width
        height: 500, // Match template height
        margin: "0 auto",

        // Fluent Design Acrylic 材质
        background: "rgba(255, 255, 255, 0.65)",
        backdropFilter: "saturate(180%) blur(20px)",
        WebkitBackdropFilter: "saturate(180%) blur(20px)",
        borderRadius: 8,
        border: "1px solid rgba(255, 255, 255, 0.18)",
        boxShadow: "0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)",
        overflow: "hidden",
        position: "relative", // Ensure context for absolute positioning if needed
      }}
    >
      {/* Header */}
      <div
        style={{
          height: 48,
          background: "rgba(255, 255, 255, 0.5)",
          borderBottom: "1px solid rgba(0, 0, 0, 0.06)",
          display: "flex",
          alignItems: "center",
          padding: "0 16px",
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: "0.875rem", fontWeight: 600, color: "rgba(0, 0, 0, 0.9)" }}>
          {tOrDefault(t, "chat.title", "💬 Chat")}
        </span>
      </div>

      <MessageList messages={messages} />
      <ChatInput onSend={handleSend} />
    </div>
  );
}
