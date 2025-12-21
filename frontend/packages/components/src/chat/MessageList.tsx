import React, { useEffect, useRef } from "react";
import type { ChatMessage } from "./types";
import type { CSSProperties } from "react";

interface Props {
  messages: ChatMessage[];
}

const containerStyle = {
  padding: 16,
  overflowY: "auto" as const,
  flex: 1,
};

const messageWrapperStyle = (isUser: boolean): CSSProperties => ({
  marginBottom: 12,
  textAlign: isUser ? "right" : "left",
});

const userBubbleStyle = {
  display: "inline-block",
  padding: "8px 12px",
  borderRadius: 8,
  background: "#0052a3", // WCAG AA 对比度 OK
  color: "#fff",
  maxWidth: "70%",
  whiteSpace: "pre-wrap" as const,
};

const assistantBubbleStyle = {
  display: "inline-block",
  padding: "8px 12px",
  borderRadius: 8,
  background: "rgba(0,0,0,0.06)",
  color: "#000",
  maxWidth: "70%",
  whiteSpace: "pre-wrap" as const,
};

export default function MessageList({ messages }: Props) {
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
              {msg.content}
            </div>
          </div>
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
