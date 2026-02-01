import React, { useState } from "react";
import { useT, tOrDefault } from "../i18n";
import type { PendingScreenshot } from "./types";

/** 检测是否为移动端 */
function isMobile(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
    navigator.userAgent
  );
}

interface Props {
  onSend: (text: string) => void;
  onTakePhoto?: () => Promise<void>;
  pendingScreenshots?: PendingScreenshot[];
  setPendingScreenshots?: React.Dispatch<
    React.SetStateAction<PendingScreenshot[]>
  >;
  /** Whether to disable the input */
  disabled?: boolean;
}

const MAX_SCREENSHOTS = 5;

export default function ChatInput({
  onSend,
  onTakePhoto,
  pendingScreenshots,
  setPendingScreenshots,
  disabled = false,
}: Props) {
  const t = useT();
  const [value, setValue] = useState("");

  async function handleSend() {
    if (!value.trim() && (!pendingScreenshots || pendingScreenshots.length === 0))
      return;
    onSend(value);
    setValue("");
  }

  async function handleTakePhoto() {
    if (pendingScreenshots && pendingScreenshots.length >= MAX_SCREENSHOTS) {
      // TODO: replace with toast / notification
      console.warn(
        tOrDefault(
          t,
          "chat.screenshot.maxReached",
          `最多只能添加 ${MAX_SCREENSHOTS} 张截图`
        )
      );
      return;
    }

    await onTakePhoto?.();
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
            <span>
              {tOrDefault(
                t,
                "chat.screenshot.pending",
                `📸 待发送截图 (${pendingScreenshots.length})`
              )}
            </span>

            <button
              onClick={() => setPendingScreenshots?.([])}
              aria-label={tOrDefault(t, "chat.screenshot.clearAll", "清除全部截图")}
              style={{
                background: "#ff4d4f",
                color: "#fff",
                border: "none",
                borderRadius: 4,
                padding: "2px 6px",
                cursor: "pointer",
              }}
            >
              {tOrDefault(t, "chat.screenshot.clearAll", "清除全部")}
            </button>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            {pendingScreenshots.map((p) => (
              <div key={p.id} style={{ position: "relative" }}>
                <img
                  src={p.base64}
                  alt={tOrDefault(t, "chat.screenshot.preview", "截图预览")}
                  style={{
                    width: 60,
                    height: 60,
                    objectFit: "cover",
                    borderRadius: 6,
                  }}
                />
                <button
                  onClick={() =>
                    setPendingScreenshots?.((prev) =>
                      prev.filter((x) => x.id !== p.id)
                    )
                  }
                  aria-label={tOrDefault(t, "chat.screenshot.remove", "删除截图")}
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
                    lineHeight: "16px",
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
          alignItems: "stretch", // ⭐关键：左右同高
          gap: 8,
        }}
      >
        {/* Textarea */}
        <textarea
          value={value}
          onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setValue(e.target.value)}
          disabled={disabled}
          aria-label={tOrDefault(t, "chat.input.label", "聊天输入框")}
          placeholder={tOrDefault(
            t,
            "chat.input.placeholder",
            "Text chat mode...Press Enter to send, Shift+Enter for new line"
          )}
          style={{
            flex: 1,
            resize: "none",
            border: "1px solid rgba(0,0,0,0.1)",
            borderRadius: 6,
            padding: "10px 12px",
            background: disabled ? "rgba(240,240,240,0.8)" : "rgba(255,255,255,0.8)",
            fontFamily: "inherit",
            fontSize: "0.9rem",
            lineHeight: "1.4",
            height: "100%",          // ⭐关键
            boxSizing: "border-box", // ⭐关键
            opacity: disabled ? 0.6 : 1,
            cursor: disabled ? "not-allowed" : "text",
          }}
          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === "Enter" && !e.shiftKey && !disabled) {
              e.preventDefault();
              handleSend();
            }
          }}
        />

        {/* Buttons */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 8,
            minHeight: "4.5rem", // 更响应式
          }}
        >
          <button
            onClick={handleSend}
            disabled={disabled}
            style={{
              flex: 1, // ⭐均分高度
              background: disabled ? "#a0d4f7" : "#44b7fe",
              color: "white",
              border: "none",
              borderRadius: 6,
              cursor: disabled ? "not-allowed" : "pointer",
              fontSize: "0.9rem",
              opacity: disabled ? 0.6 : 1,
            }}
          >
            {tOrDefault(t, "chat.send", "发送")}
          </button>

          {onTakePhoto && (
            <button
              onClick={handleTakePhoto}
              disabled={disabled}
              style={{
                flex: 1, // ⭐均分高度
                background: disabled ? "rgba(240,240,240,0.8)" : "rgba(255,255,255,0.8)",
                border: "1px solid #44b7fe",
                color: "#44b7fe",
                borderRadius: 6,
                cursor: disabled ? "not-allowed" : "pointer",
                fontSize: "0.8rem",
                opacity: disabled ? 0.6 : 1,
              }}
            >
              {isMobile()
                ? tOrDefault(t, "chat.screenshot.buttonMobile", "拍照")
                : tOrDefault(t, "chat.screenshot.button", "截图")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
