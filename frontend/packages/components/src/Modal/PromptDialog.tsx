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

  // 处理 Enter 和 ESC 键
  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      handleConfirm();
    } else if (e.key === "Escape") {
      handleCancel();
    }
  };

  // 获取按钮文本（支持国际化）
  const getOkText = () => {
    if (okText) return okText;
    const canTranslate = typeof window !== "undefined" && typeof window.t === "function";
    return canTranslate ? window.t("common.ok") : "确定";
  };

  const getCancelText = () => {
    if (cancelText) return cancelText;
    const canTranslate = typeof window !== "undefined" && typeof window.t === "function";
    return canTranslate ? window.t("common.cancel") : "取消";
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
          onChange={(e) => setInputValue(e.target.value)}
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


