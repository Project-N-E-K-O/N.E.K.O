import React, { useEffect, useRef } from "react";
import type { ChatMessage } from "./types";
import { useT, tOrDefault } from "../i18n";
import type { CSSProperties } from "react";

interface Props {
  messages: ChatMessage[];
}

const containerStyle = {
  padding: 16,
  overflowY: "auto" as const,
  flex: 1,
  background: "rgba(249, 249, 249, 0.7)",
  display: "flex",
  flexDirection: "column" as const,
  gap: 12,
};

const messageWrapperStyle = (isUser: boolean): CSSProperties => ({
  display: "flex",
  justifyContent: isUser ? "flex-end" : "flex-start",
});

const userBubbleStyle: CSSProperties = {
  padding: "10px 14px",
  borderRadius: 12,
  borderBottomRightRadius: 4,
  background: "#44b7fe",
  color: "#fff",
  maxWidth: "80%",
};

const assistantBubbleStyle: CSSProperties = {
  padding: "10px 14px",
  borderRadius: 12,
  borderBottomLeftRadius: 4,
  background: "rgba(68, 183, 254, 0.12)",
  color: "#333",
  maxWidth: "80%",
};

export default function MessageList({ messages }: Props) {
  const t = useT();
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div style={containerStyle}>
      {messages.map((msg) => {
        const isUser = msg.role === "user";

        return (
          <div key={msg.id} style={messageWrapperStyle(isUser)}>
            <div style={isUser ? userBubbleStyle : assistantBubbleStyle}>
              {msg.image ? (
                <div>
                  <img
                    src={msg.image}
                    alt={tOrDefault(t, "chat.message.screenshot", "截图")}
                    style={{
                      maxWidth: "100%",
                      borderRadius: 8,
                      display: "block",
                    }}
                    onError={(e) => {
                      e.currentTarget.style.display = "none";
                      e.currentTarget.parentElement!.innerHTML =
                        tOrDefault(t, "chat.message.imageError", "图片加载失败");
                    }}
                  />
                  {msg.content && (
                    <div style={{ marginTop: 8 }}>{msg.content}</div>
                  )}
                </div>
              ) : msg.content ? (
                msg.content
              ) : (
                <span style={{ opacity: 0.5 }}>
                  {tOrDefault(t, "chat.message.empty", "空消息")}
                </span>
              )}
            </div>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
