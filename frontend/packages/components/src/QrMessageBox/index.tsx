import React, { useEffect, useRef, useState } from "react";
import { useT, tOrDefault } from "../i18n";
import { BaseModal } from "../Modal/BaseModal";
import { Button } from "../Button";
import "./QrMessageBox.css";

export interface QrMessageBoxProps {
  apiBase: string;
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  endpoint?: string;
}

export function QrMessageBox({
  apiBase,
  isOpen,
  onClose,
  title,
  endpoint = "/getipqrcode",
}: QrMessageBoxProps) {
  const t = useT();
  const [qrImageUrl, setQrImageUrl] = useState<string | null>(null);
  const [qrLoading, setQrLoading] = useState(false);
  const [qrError, setQrError] = useState<string | null>(null);
  const qrObjectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    if (!isOpen) {
      setQrLoading(false);
      setQrError(null);
      if (qrObjectUrlRef.current) {
        try {
          URL.revokeObjectURL(qrObjectUrlRef.current);
        } catch (_e) {
          // ignore
        }
        qrObjectUrlRef.current = null;
      }
      setQrImageUrl(null);
      return;
    }

    const abortController = new AbortController();
    let activeObjectUrl: string | null = null;

    const run = async () => {
      setQrLoading(true);
      setQrError(null);

      try {
        const res = await fetch(`${apiBase}${endpoint}`, {
          method: "POST",
          signal: abortController.signal,
          headers: {
            Accept: "image/*,application/json",
          },
        });

        if (!res.ok) {
          throw new Error(tOrDefault(t, "webapp.qrDrawer.fetchError", `獲取失敗: ${res.status}`));
        }

        const contentType = (res.headers.get("content-type") || "").toLowerCase();

        if (contentType.startsWith("image/")) {
          const blob = await res.blob();
          activeObjectUrl = URL.createObjectURL(blob);
          qrObjectUrlRef.current = activeObjectUrl;
          setQrImageUrl(activeObjectUrl);
          return;
        }

        const data: any = await res.json();
        const url = data?.imageUrl || data?.url || data?.dataUrl;
        if (typeof url === "string" && url) {
          setQrImageUrl(url);
          return;
        }
        const base64 = data?.base64;
        if (typeof base64 === "string" && base64) {
          setQrImageUrl(`data:image/png;base64,${base64}`);
          return;
        }

        throw new Error(tOrDefault(t, "webapp.qrDrawer.invalidPayload", "返回數據格式無效"));
      } catch (e: any) {
        if (abortController.signal.aborted) return;
        setQrError(e?.message || tOrDefault(t, "webapp.qrDrawer.unknownError", "未知錯誤"));
      } finally {
        if (!abortController.signal.aborted) setQrLoading(false);
      }
    };

    run();

    return () => {
      abortController.abort();
      if (activeObjectUrl) {
        try {
          URL.revokeObjectURL(activeObjectUrl);
        } catch (_e) {
          // ignore
        }
        if (qrObjectUrlRef.current === activeObjectUrl) {
          qrObjectUrlRef.current = null;
        }
      }
    };
  }, [apiBase, endpoint, isOpen, t]);

  const modalTitle = title || tOrDefault(t, "webapp.qrDrawer.title", "二维码");

  return (
    <BaseModal isOpen={isOpen} onClose={onClose} title={modalTitle}>
      <div className="modal-body">
        {qrLoading && tOrDefault(t, "webapp.qrDrawer.loading", "加载中…")}
        {!qrLoading && qrError && tOrDefault(t, "webapp.qrDrawer.error", "二维码加载失败")}
        {!qrLoading && !qrError && !qrImageUrl &&
          tOrDefault(t, "webapp.qrDrawer.placeholder", "二维码区域（待接入）")}
        {!qrLoading && !qrError && qrImageUrl && (
          <img
            style={{ display: "block", maxWidth: "100%", maxHeight: "60vh", objectFit: "contain" }}
            src={qrImageUrl}
            alt={modalTitle}
          />
        )}
      </div>
      <div className="modal-footer">
        <Button variant="secondary" onClick={onClose}>
          {tOrDefault(t, "common.close", "关闭")}
        </Button>
      </div>
    </BaseModal>
  );
}

export default QrMessageBox;
