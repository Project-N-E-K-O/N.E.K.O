import React, { useState } from "react";

interface Props {
  onSend: (text: string) => void;
}

export default function ChatInput({ onSend }: Props) {
  const [value, setValue] = useState("");

  function handleSend() {
    const text = value.trim();
    if (!text) return;
    onSend(text);
    setValue("");
  }

  return (
    <div style={{ padding: 12, borderTop: "1px solid #eee" }}>
      <textarea
        value={value}
        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setValue(e.target.value)}
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows={3}
        style={{ width: "100%", resize: "none" }}
        onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
      />
      <div style={{ textAlign: "right", marginTop: 8 }}>
        <button onClick={handleSend}>发送</button>
      </div>
    </div>
  );
}