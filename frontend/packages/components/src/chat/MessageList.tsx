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
  background: "rgba(249, 249, 249, 0.7)", // Fluent Design Content Background
  display: "flex",
  flexDirection: "column" as const,
  gap: 12,
};

const messageWrapperStyle = (isUser: boolean): CSSProperties => ({
  // marginBottom: 12, // handled by gap
  display: "flex",
  justifyContent: isUser ? "flex-end" : "flex-start",
});

const userBubbleStyle = {
  padding: "10px 14px",
  borderRadius: 12,
  borderBottomRightRadius: 4,
  background: "#44b7fe",
  color: "#fff",
  maxWidth: "80%",
  wordWrap: "break-word" as const,
  lineHeight: 1.5,
  fontSize: "0.95rem",
  whiteSpace: "pre-wrap" as const,
  boxShadow: "0 1px 2px rgba(0,0,0,0.1)", // Slight shadow for depth
};

const assistantBubbleStyle = {
  padding: "10px 14px",
  borderRadius: 12,
  borderBottomLeftRadius: 4,
  background: "rgba(68, 183, 254, 0.12)",
  color: "#333",
  maxWidth: "80%",
  wordWrap: "break-word" as const,
  lineHeight: 1.5,
  fontSize: "0.95rem",
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
