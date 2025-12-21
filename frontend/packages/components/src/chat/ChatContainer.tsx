import React, { useState } from "react";
import type { ChatMessage } from "./types";
import MessageList from "./MessageList";
import ChatInput from "./ChatInput";

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
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "sys-1",
      role: "system",
      content: "欢迎来到 React 聊天系统（迁移 Demo）",
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
      content: `你刚刚说的是：${text}`,
      createdAt: Date.now(),
    };

    setMessages((prev) => [...prev, userMsg, botMsg]);
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        maxWidth: 600,
        height: "100%",
        margin: "0 auto",
        border: "1px solid #ddd",
      }}
    >
      <MessageList messages={messages} />
      <ChatInput onSend={handleSend} />
    </div>
  );
}
