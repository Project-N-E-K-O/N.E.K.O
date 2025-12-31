import React, { useEffect, useState } from "react";
import { useT, tOrDefault } from "../i18n";
import "./QrMessageBox.css";

export interface QrMessageBoxProps {
  title?: string;
  description?: string;
  imageUrl: string;
  visible?: boolean;
  onClose?: () => void;
}

type LoadStatus = "idle" | "loading" | "loaded" | "error";

export function QrMessageBox({
  title,
  description,
  imageUrl,
  visible = true,
  onClose,
}: QrMessageBoxProps) {
  const t = useT();
  const [status, setStatus] = useState<LoadStatus>("idle");

  useEffect(() => {
    if (!visible) {
      setStatus("idle");
      return;
    }
    if (imageUrl) {
      setStatus("loading");
    } else {
      setStatus("error");
    }
  }, [imageUrl, visible]);

  if (!visible) return null;

  const handleImageLoad = () => {
    setStatus("loaded");
  };

  const handleImageError = () => {
    setStatus("error");
  };

  const loadingText = tOrDefault(t, "qr_message_box.loading", "加载中…");
  const errorText = tOrDefault(t, "qr_message_box.error", "图片加载失败");
  const closeLabel = tOrDefault(t, "common.close", "关闭");
  const altText = title || tOrDefault(t, "qr_message_box.alt", "二维码");

  return (
    <div className="qr-message-box">
      <div className="qr-message-box__header">
        {title && <h3 className="qr-message-box__title">{title}</h3>}
        {onClose && (
          <button
            type="button"
            className="qr-message-box__close"
            aria-label={closeLabel}
            onClick={onClose}
          >
            ×
          </button>
        )}
      </div>
      {description && <p className="qr-message-box__description">{description}</p>}
      <div className="qr-message-box__content">
        {status === "loading" && (
          <div className="qr-message-box__placeholder">{loadingText}</div>
        )}
        {status === "error" && (
          <div className="qr-message-box__placeholder qr-message-box__placeholder--error">
            {errorText}
          </div>
        )}
        {imageUrl && (
          <img
            className={
              "qr-message-box__image" +
              (status !== "loaded" ? " qr-message-box__image--hidden" : "")
            }
            src={imageUrl}
            alt={altText}
            onLoad={handleImageLoad}
            onError={handleImageError}
          />
        )}
      </div>
    </div>
  );
}

export default QrMessageBox;
