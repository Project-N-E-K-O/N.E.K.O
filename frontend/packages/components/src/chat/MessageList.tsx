import React from "react";
import type { ChatMessage } from "./types";
import { useT, tOrDefault } from "../i18n";

/**
 * 图片组件：使用 state 处理加载失败
 * - 不直接操作 DOM
 * - 不使用 parentElement / innerHTML
 * - 完全符合 React idiomatic 写法
 */
function ImageWithFallback({
  src,
  alt,
  fallback,
}: {
  src: string;
  alt: string;
  fallback: string;
}) {
  const [hasError, setHasError] = React.useState(false);

  // 当 src 变化时重置错误状态
  React.useEffect(() => {
    setHasError(false);
  }, [src]);

  if (hasError) {
    return (
      <span style={{ opacity: 0.6 }}>
        {fallback}
      </span>
    );
  }

  return (
    <img
      src={src}
      alt={alt}
      style={{
        maxWidth: "100%",
        borderRadius: 8,
        display: "block",
      }}
      onError={() => setHasError(true)}
    />
  );
}

interface Props {
  messages: ChatMessage[];
}

export default function MessageList({ messages }: Props) {
  const t = useT();

  return (
    <div
      style={{
        padding: 12,
        display: "flex",
        flexDirection: "column",
        gap: 12,
      }}
    >
      {messages.map((msg) => (
        <div
          key={msg.id}
          style={{
            alignSelf: msg.role === "user" ? "flex-end" : "flex-start",
            maxWidth: "80%",
            background:
              msg.role === "user"
                ? "rgba(68, 183, 254, 0.15)"
                : "rgba(0, 0, 0, 0.05)",
            borderRadius: 8,
            padding: 8,
            wordBreak: "break-word",
          }}
        >
          {msg.image ? (
            <div>
              <ImageWithFallback
                src={msg.image}
                alt={tOrDefault(
                  t,
                  "chat.message.screenshot",
                  "截图"
                )}
                fallback={tOrDefault(
                  t,
                  "chat.message.imageError",
                  "图片加载失败"
                )}
              />

              {msg.content && (
                <div style={{ marginTop: 8 }}>
                  {msg.content}
                </div>
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
      ))}
    </div>
  );
}
