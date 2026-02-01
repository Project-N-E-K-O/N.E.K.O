import React from "react";
import { BaseModal } from "./BaseModal";
import type { BaseModalProps } from "./BaseModal";
import { tOrDefault, useT } from "../i18n";

export interface AlertDialogProps extends Omit<BaseModalProps, "children"> {
  message: string;
  okText?: string;
  onConfirm: () => void;
}

/**
 * Renders a confirmation modal with a message and a primary "OK" button.
 *
 * The primary button invokes `onConfirm` when clicked; the dialog does not close automatically
 * — the parent should handle closing (e.g., by calling `onClose`). The button label uses
 * `okText` if provided, otherwise it attempts to use i18n (`common.ok`) and falls back to `"确定"`.
 *
 * @param message - The message text displayed in the modal body
 * @param okText - Optional text for the primary button; when omitted the component attempts localization
 * @param onConfirm - Callback invoked when the primary button is clicked
 * @param closeOnClickOutside - Whether clicking outside the modal closes it (defaults to `true`)
 * @param closeOnEscape - Whether pressing Escape closes the modal (defaults to `true`)
 * @returns The AlertDialog JSX element
 */
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
  const t = useT();

  const handleConfirm = () => {
    onConfirm();
    // 不在这里调用 onClose，让父组件处理关闭逻辑
  };

  // 获取确定按钮文本（支持国际化）
  const getOkText = () => {
    if (okText) return okText;
    return tOrDefault(t, "common.ok", "确定");
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


