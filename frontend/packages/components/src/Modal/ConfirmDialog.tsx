import React from "react";
import { BaseModal } from "./BaseModal";
import type { BaseModalProps } from "./BaseModal";
import { tOrDefault, useT } from "../i18n";

export interface ConfirmDialogProps extends Omit<BaseModalProps, "children"> {
  message: string;
  okText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel?: () => void;
}

/**
 * Renders a confirmation modal with a message and Cancel/Confirm actions.
 *
 * The Confirm button invokes `onConfirm` and the Cancel button invokes `onCancel` if provided.
 * Neither handler closes the modal; closing is delegated to the parent via `onClose`.
 *
 * @param isOpen - Whether the modal is visible
 * @param onClose - Callback invoked when the modal requests to close (e.g., backdrop click or Escape)
 * @param title - Optional modal title
 * @param message - Text displayed in the modal body
 * @param okText - Custom label for the confirm button
 * @param cancelText - Custom label for the cancel button
 * @param danger - If true, applies danger styling to the confirm button
 * @param onConfirm - Callback invoked when the confirm button is clicked
 * @param onCancel - Callback invoked when the cancel button is clicked (optional)
 * @param closeOnClickOutside - Whether clicking outside the modal closes it
 * @param closeOnEscape - Whether pressing Escape closes the modal
 * @returns The confirmation modal React element
 */
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
  const t = useT();

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
    return tOrDefault(t, "common.ok", "确定");
  };

  const getCancelText = () => {
    if (cancelText) return cancelText;
    return tOrDefault(t, "common.cancel", "取消");
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


