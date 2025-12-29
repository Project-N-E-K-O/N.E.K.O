import React, { useState } from "react";
import { useT, tOrDefault } from "../i18n";

interface Props {
  onSend: (text: string) => void;
  onTakePhoto?: () => void;
}

export default function ChatInput({ onSend, onTakePhoto }: Props) {
  const t = useT();
  const [value, setValue] = useState("");

  function handleSend() {
    const text = value.trim();
    if (!text) return;
    onSend(text);
    setValue("");
  }

  return (
    <div
      style={{
        padding: 12,
        background: "rgba(255, 255, 255, 0.5)",
        borderTop: "1px solid rgba(0, 0, 0, 0.06)",
        display: "flex",
        alignItems: "center", // Align vertically
        gap: 8,
      }}
    >
      <textarea
        value={value}
        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setValue(e.target.value)}
        placeholder={tOrDefault(t, "chat.input.placeholder", "Text chat mode...Press Enter to send, Shift+Enter for new line")}
        rows={2}
        style={{
          flex: 1,
          resize: "none",
          border: "1px solid rgba(0,0,0,0.1)",
          borderRadius: 6,
          padding: "8px 12px",
          outline: "none",
          background: "rgba(255,255,255,0.8)",
          fontFamily: "inherit",
          fontSize: "0.9rem",
          overflow: "hidden",
        }}
        onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
      />
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <button
          onClick={handleSend}
          style={{
            background: "#44b7fe",
            color: "white",
            border: "none",
            borderRadius: 6,
            padding: "6px 16px",
            cursor: "pointer",
            fontSize: "0.9rem",
            fontWeight: 500,
            whiteSpace: "nowrap",
          }}
        >
          {tOrDefault(t, "chat.send", "Send")}
        </button>
        <button
          onClick={onTakePhoto}
          style={{
            background: "rgba(255,255,255,0.8)",
            border: "1px solid #44b7fe",
            color: "#44b7fe",
            borderRadius: 6,
            padding: "4px 8px",
            cursor: "pointer",
            fontSize: "0.8rem",
            fontWeight: 500,
            whiteSpace: "nowrap",
          }}
        >
          {tOrDefault(t, "chat.take_photo", "Take Photo")}
        </button>
      </div>
    </div>
  );
}