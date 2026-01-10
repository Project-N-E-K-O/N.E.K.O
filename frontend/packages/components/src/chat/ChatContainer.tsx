import React, { useState } from "react";
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

  /** 是否缩小 */
  const [collapsed, setCollapsed] = useState(false);

  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "sys-1",
      role: "system",
      content: tOrDefault(
        t,
        "chat.welcome",
        "欢迎来到 React 聊天系统（迁移 Demo）"
      ),
      createdAt: Date.now(),
    },
  ]);

  const [pendingScreenshots, setPendingScreenshots] =
    useState<PendingScreenshot[]>([]);

  function handleSendText(text: string) {
    if (!text.trim() && pendingScreenshots.length === 0) return;

    const newMessages: ChatMessage[] = [];
    let timestamp = Date.now();

    pendingScreenshots.forEach((p) => {
      newMessages.push({
        id: generateId(),
        role: "user",
        image: p.base64,
        createdAt: timestamp++,
      });
    });

    if (text.trim()) {
      newMessages.push({
        id: generateId(),
        role: "user",
        content: text,
        createdAt: timestamp,
      });
    }

    setMessages((prev) => [...prev, ...newMessages]);
    setPendingScreenshots([]);
  }

  async function handleScreenshot() {
    if (!navigator.mediaDevices?.getDisplayMedia) {
      alert(
        tOrDefault(
          t,
          "chat.screenshot.unsupported",
          "您的浏览器不支持截图"
        )
      );
      return;
    }

    let stream: MediaStream | null = null;
    const video = document.createElement("video");

    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
      });
      video.srcObject = stream;
      await video.play();

      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;

      const ctx = canvas.getContext("2d");
      if (!ctx) {
        alert(tOrDefault(t, "chat.screenshot.failed", "截图失败"));
        return;
      }

      ctx.drawImage(video, 0, 0);
      const base64 = canvas.toDataURL("image/png");

      setPendingScreenshots((prev) => [
        ...prev,
        { id: generateId(), base64 },
      ]);
    } finally {
      if (stream) stream.getTracks().forEach((track) => track.stop());
      video.srcObject = null;
    }
  }

  /** ================= 缩小态：左下角按钮（button，支持键盘） ================= */
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        aria-label={tOrDefault(t, "chat.expand", "打开聊天")}
        style={{
          position: "fixed",
          left: 16,
          bottom: 16,
          width: 56,
          height: 56,
          borderRadius: "50%",
          background: "#44b7fe",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          cursor: "pointer",
          boxShadow: "0 8px 24px rgba(68,183,254,0.5)",
          zIndex: 9999,
          border: "none",
          padding: 0,
        }}
      >
        <span style={{ color: "#fff", fontSize: 22 }}>💬</span>
      </button>
    );
  }

  /** ================= 展开态：聊天框 ================= */
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        width: "100%",
        maxWidth: 400,
        height: 520,
        margin: "0 auto",
        background: "rgba(255, 255, 255, 0.65)",
        backdropFilter: "saturate(180%) blur(20px)",
        WebkitBackdropFilter: "saturate(180%) blur(20px)",
        borderRadius: 12,
        border: "1px solid rgba(255, 255, 255, 0.18)",
        boxShadow:
          "0 4px 12px rgba(0,0,0,0.08), 0 16px 32px rgba(0,0,0,0.12)",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          height: 48,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 12px 0 16px",
          borderBottom: "1px solid rgba(0,0,0,0.06)",
          background: "rgba(255,255,255,0.5)",
          flexShrink: 0,
        }}
      >
        <span style={{ fontWeight: 600 }}>
          {tOrDefault(t, "chat.title", "💬 Chat")}
        </span>

        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label={tOrDefault(t, "chat.minimize", "最小化聊天")}
          style={{
            width: 28,
            height: 28,
            borderRadius: "50%",
            border: "none",
            background: "#e6f4ff",
            color: "#44b7fe",
            cursor: "pointer",
            fontSize: 16,
            lineHeight: "28px",
          }}
        >
          —
        </button>
      </div>

      <div style={{ flex: 1, overflowY: "auto" }}>
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
