import React from "react";
import { BaseModal } from "./BaseModal";
import type { BaseModalProps } from "./BaseModal";

export interface ConfirmDialogProps extends Omit<BaseModalProps, "children"> {
  message: string;
  okText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel?: () => void;
}

export function ConfirmDialog({
  isOpen,
  onClose,
  title,
  message,
  okText,
  cancelText,
  danger = false,
  onConfirm,
  onCancel,
  closeOnClickOutside = true,
  closeOnEscape = true,
}: ConfirmDialogProps) {
  const handleConfirm = () => {
    onConfirm();
    // 不在这里调用 onClose，让父组件处理关闭逻辑
  };

  const handleCancel = () => {
    if (onCancel) {
      onCancel();
    }
    // 不在这里调用 onClose，让父组件处理关闭逻辑
  };

  // 获取按钮文本（支持国际化）
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

  const getCancelText = () => {
    if (cancelText) return cancelText;
    try {
      return window.t && typeof window.t === "function"
        ? window.t("common.cancel")
        : "取消";
    } catch (e) {
      return "取消";
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
          className="modal-btn modal-btn-secondary"
          onClick={handleCancel}
        >
          {getCancelText()}
        </button>
        <button
          className={danger ? "modal-btn modal-btn-danger" : "modal-btn modal-btn-primary"}
          onClick={handleConfirm}
          autoFocus
        >
          {getOkText()}
        </button>
      </div>
    </BaseModal>
  );
}


