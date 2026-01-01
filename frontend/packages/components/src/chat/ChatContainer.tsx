import React, { useState, useRef } from "react";
import type { ChatMessage, PendingScreenshot } from "./types";
import MessageList from "./MessageList";
import ChatInput from "./ChatInput";
import { useT, tOrDefault } from "../i18n";

/** 生成跨环境安全的 id */
function generateId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
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

  const [pendingScreenshots, setPendingScreenshots] =
    useState<PendingScreenshot[]>([]);

  // 聊天区域 ref（保留，不再用于截图）
  const messageAreaRef = useRef<HTMLDivElement>(null);

  function handleSendText(text: string) {
    if (!text.trim() && pendingScreenshots.length === 0) return;

    const newMessages: ChatMessage[] = [];

    // 先发送 pending 图片
    pendingScreenshots.forEach((p) => {
      newMessages.push({
        id: generateId(),
        role: "user",
        image: p.base64,
        createdAt: Date.now(),
      });
    });

    // 再发送文本
    if (text.trim()) {
      newMessages.push({
        id: generateId(),
        role: "user",
        content: text,
        createdAt: Date.now(),
      });
    }

    setMessages((prev) => [...prev, ...newMessages]);
    setPendingScreenshots([]);
  }

  // 📸 Take Photo → Chrome 屏幕分享 → 进入 pending
  async function handleScreenshot() {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false,
    });

    const video = document.createElement("video");
    video.srcObject = stream;
    await video.play();

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;

    const ctx = canvas.getContext("2d")!;
    ctx.drawImage(video, 0, 0);

    const base64 = canvas.toDataURL("image/png");

    stream.getTracks().forEach((t) => t.stop());

    setPendingScreenshots((prev) => [
      ...prev,
      { id: generateId(), base64 },
    ]);
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        maxWidth: 400,
        height: 500,
        margin: "0 auto",
        background: "rgba(255, 255, 255, 0.65)",
        backdropFilter: "saturate(180%) blur(20px)",
        WebkitBackdropFilter: "saturate(180%) blur(20px)",
        borderRadius: 8,
        border: "1px solid rgba(255, 255, 255, 0.18)",
        boxShadow:
          "0 2px 4px rgba(0, 0, 0, 0.04), 0 8px 16px rgba(0, 0, 0, 0.08), 0 16px 32px rgba(0, 0, 0, 0.04)",
        overflow: "hidden",
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
        }}
      >
        <span style={{ fontSize: "0.875rem", fontWeight: 600 }}>
          {tOrDefault(t, "chat.title", "💬 Chat")}
        </span>
      </div>

      {/* 聊天区 */}
      <div ref={messageAreaRef} style={{ flex: 1, overflowY: "auto" }}>
        <MessageList messages={messages} />
      </div>

      <ChatInput
        onSend={handleSendText}
        onTakePhoto={handleScreenshot}
        pendingScreenshots={pendingScreenshots}
        setPendingScreenshots={setPendingScreenshots}
      />
    </div>
  );
}
