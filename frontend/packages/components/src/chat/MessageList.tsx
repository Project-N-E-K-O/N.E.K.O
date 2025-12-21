import React from "react";
import type { ChatMessage } from "./types";

interface Props {
  messages: ChatMessage[];
}

export default function MessageList({ messages }: Props) {
  return (
    <div style={{ padding: 16, overflowY: "auto", flex: 1 }}>
      {messages.map((msg) => (
        <div
          key={msg.id}
          style={{
            marginBottom: 12,
            textAlign: msg.role === "user" ? "right" : "left",
          }}
        >
          <div
            style={{
              display: "inline-block",
              padding: "8px 12px",
              borderRadius: 8,
              background:
                msg.role === "user" ? "#4da3ff" : "rgba(0,0,0,0.06)",
              color: msg.role === "user" ? "#fff" : "#000",
              maxWidth: "70%",
              whiteSpace: "pre-wrap",
            }}
          >
            {msg.content}
          </div>
        </div>
      ))}
    </div>
  );
}