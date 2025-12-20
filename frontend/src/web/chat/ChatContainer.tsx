import { useState } from "react";
import { ChatMessage } from "./types";
import MessageList from "./MessageList";
import ChatInput from "./ChatInput";

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
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      createdAt: Date.now(),
    };

    const botMsg: ChatMessage = {
      id: crypto.randomUUID(),
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
        height: "100vh",
        maxWidth: 600,
        margin: "0 auto",
        border: "1px solid #ddd",
      }}
    >
      <MessageList messages={messages} />
      <ChatInput onSend={handleSend} />
    </div>
  );
}