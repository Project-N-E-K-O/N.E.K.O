import React, { useState } from "react";
import { useT, tOrDefault } from "../i18n";
import type { PendingScreenshot } from "./types";

interface Props {
  onSend: (text: string) => void;
  onTakePhoto?: () => void;
  pendingScreenshots?: PendingScreenshot[];
  setPendingScreenshots?: React.Dispatch<
    React.SetStateAction<PendingScreenshot[]>
  >;
}

export default function ChatInput({
  onSend,
  onTakePhoto,
  pendingScreenshots,
  setPendingScreenshots,
}: Props) {
  const t = useT();
  const [value, setValue] = useState("");

  function handleSend() {
    onSend(value);
    setValue("");
  }

  return (
    <div
      style={{
        padding: 12,
        background: "rgba(255, 255, 255, 0.5)",
        borderTop: "1px solid rgba(0, 0, 0, 0.06)",
        display: "flex",
        flexDirection: "column",
        gap: 8,
      }}
    >
      {/* Pending Screenshots */}
      {pendingScreenshots && pendingScreenshots.length > 0 && (
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              fontSize: 12,
              color: "#44b7fe",
              marginBottom: 4,
            }}
          >
            <span>📸 Pending Screenshots ({pendingScreenshots.length})</span>
            <button
              onClick={() => setPendingScreenshots?.([])}
              style={{
                background: "#ff4d4f",
                color: "#fff",
                border: "none",
                borderRadius: 4,
                padding: "2px 6px",
                cursor: "pointer",
              }}
            >
              Clear All
            </button>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            {pendingScreenshots.map((p) => (
              <div key={p.id} style={{ position: "relative" }}>
                <img
                  src={p.base64}
                  style={{ width: 60, borderRadius: 6 }}
                />
                <button
                  onClick={() =>
                    setPendingScreenshots?.((prev) =>
                      prev.filter((x) => x.id !== p.id)
                    )
                  }
                  style={{
                    position: "absolute",
                    top: -6,
                    right: -6,
                    width: 16,
                    height: 16,
                    borderRadius: "50%",
                    border: "none",
                    background: "#ff4d4f",
                    color: "#fff",
                    cursor: "pointer",
                    fontSize: 10,
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Input Row */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={tOrDefault(
            t,
            "chat.input.placeholder",
            "Text chat mode...Press Enter to send, Shift+Enter for new line"
          )}
          rows={2}
          style={{
            flex: 1,
            resize: "none",
            border: "1px solid rgba(0,0,0,0.1)",
            borderRadius: 6,
            padding: "8px 12px",
            background: "rgba(255,255,255,0.8)",
            fontFamily: "inherit",
            fontSize: "0.9rem",
          }}
          onKeyDown={(e) => {
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
            }}
          >
            {tOrDefault(t, "chat.send", "Send")}
          </button>

          {onTakePhoto && (
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
              }}
            >
              Screenshot
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
