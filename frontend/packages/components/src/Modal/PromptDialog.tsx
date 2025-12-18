import React from "react";
import { useState, useEffect, useRef } from "react";
import { BaseModal } from "./BaseModal";
import type { BaseModalProps } from "./BaseModal";

export interface PromptDialogProps extends Omit<BaseModalProps, "children"> {
  message: string;
  defaultValue?: string;
  placeholder?: string;
  okText?: string;
  cancelText?: string;
  onConfirm: (value: string) => void;
  onCancel?: () => void;
}

/**
 * Renders a modal prompt with a single-line text input and configurable OK/Cancel actions.
 *
 * @param message - The message displayed above the input.
 * @param defaultValue - Initial value populated into the input when the dialog opens.
 * @param placeholder - Placeholder text shown inside the input when empty.
 * @param okText - Explicit label for the confirmation button; if omitted, uses i18n or a fallback.
 * @param cancelText - Explicit label for the cancel button; if omitted, uses i18n or a fallback.
 * @param onConfirm - Callback invoked with the current input value when the user confirms.
 * @param onCancel - Optional callback invoked when the user cancels the dialog.
 * @returns A JSX element representing the prompt dialog.
 */
export function PromptDialog({
  isOpen,
  onClose,
  title,
  message,
  defaultValue = "",
  placeholder = "",
  okText,
  cancelText,
  onConfirm,
  onCancel,
  closeOnClickOutside = true,
  closeOnEscape = true,
}: PromptDialogProps) {
  const [inputValue, setInputValue] = useState(defaultValue);
  const inputRef = useRef<HTMLInputElement>(null);

  // 当对话框打开时，重置输入值
  useEffect(() => {
    if (isOpen) {
      setInputValue(defaultValue);
    }
  }, [isOpen, defaultValue]);

  // 自动聚焦输入框
  useEffect(() => {
    if (isOpen && inputRef.current) {
      const timer = setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 100);
      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  const handleConfirm = () => {
    onConfirm(inputValue);
    // 不在这里调用 onClose，让父组件处理关闭逻辑
  };

  const handleCancel = () => {
    if (onCancel) {
      onCancel();
    }
    // 不在这里调用 onClose，让父组件处理关闭逻辑
  };

  // 处理 Enter 键（Esc 交由 BaseModal 统一处理）
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleConfirm();
    }
  };

  // 获取按钮文本（支持国际化）
  const getOkText = () => {
    if (okText) return okText;
    const t = typeof window !== "undefined" ? (window as any).t : undefined;
    return typeof t === "function" ? String(t("common.ok")) : "确定";
  };

  const getCancelText = () => {
    if (cancelText) return cancelText;
    const t = typeof window !== "undefined" ? (window as any).t : undefined;
    return typeof t === "function" ? String(t("common.cancel")) : "取消";
  };

  return (
    <BaseModal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      closeOnClickOutside={closeOnClickOutside}
      closeOnEscape={closeOnEscape}
    >
      <div className="modal-body">
        {message}
        <input
          ref={inputRef}
          type="text"
          className="modal-input"
          value={inputValue}
          onChange={(e: React.ChangeEvent<HTMLInputElement>) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
        />
      </div>
      <div className="modal-footer">
        <button
          className="modal-btn modal-btn-secondary"
          onClick={handleCancel}
        >
          {getCancelText()}
        </button>
        <button
          className="modal-btn modal-btn-primary"
          onClick={handleConfirm}
        >
          {getOkText()}
        </button>
      </div>
    </BaseModal>
  );
}


