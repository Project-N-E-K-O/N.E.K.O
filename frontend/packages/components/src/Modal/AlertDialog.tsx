import React from "react";
import { BaseModal } from "./BaseModal";
import type { BaseModalProps } from "./BaseModal";

export interface AlertDialogProps extends Omit<BaseModalProps, "children"> {
  message: string;
  okText?: string;
  onConfirm: () => void;
}

export function AlertDialog({
  isOpen,
  onClose,
  title,
  message,
  okText,
  onConfirm,
  closeOnClickOutside = true,
  closeOnEscape = true,
}: AlertDialogProps) {
  const handleConfirm = () => {
    onConfirm();
    // 不在这里调用 onClose，让父组件处理关闭逻辑
  };

  // 获取确定按钮文本（支持国际化）
  const getOkText = () => {
    if (okText) return okText;
    try {
      return window.t && typeof window.t === "function"
        ? window.t("common.ok")
        : "确定";
    } catch (e) {
      return "确定";
    }
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      closeOnClickOutside={closeOnClickOutside}
      closeOnEscape={closeOnEscape}
    >
      <div className="modal-body">{message}</div>
      <div className="modal-footer">
        <button
          className="modal-btn modal-btn-primary"
          onClick={handleConfirm}
          autoFocus
        >
          {getOkText()}
        </button>
      </div>
    </BaseModal>
  );
}


