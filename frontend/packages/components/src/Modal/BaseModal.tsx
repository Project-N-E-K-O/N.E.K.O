import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import type { ReactNode } from "react";
import "./Modal.css";

export interface BaseModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: string;
  children: ReactNode;
  closeOnClickOutside?: boolean;
  closeOnEscape?: boolean;
}

/**
 * Render a modal dialog into document.body with an optional title, overlay click/Escape-to-close handling, and automatic focus of the first input or button when opened.
 *
 * @param closeOnClickOutside - Whether clicking the overlay closes the modal (defaults to `true`).
 * @param closeOnEscape - Whether pressing the Escape key closes the modal (defaults to `true`).
 * @returns The modal element mounted to `document.body` when `isOpen` is `true`, or `null` when closed.
 */
export function BaseModal({
  isOpen,
  onClose,
  title,
  children,
  closeOnClickOutside = true,
  closeOnEscape = true,
}: BaseModalProps) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const escHandlerRef = useRef<((e: KeyboardEvent) => void) | null>(null);

  // 处理 ESC 键关闭
  useEffect(() => {
    if (!isOpen || !closeOnEscape) return;

    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };

    escHandlerRef.current = handleEsc;
    document.addEventListener("keydown", handleEsc);

    return () => {
      if (escHandlerRef.current) {
        document.removeEventListener("keydown", escHandlerRef.current);
        escHandlerRef.current = null;
      }
    };
  }, [isOpen, closeOnEscape, onClose]);

  // 处理点击遮罩层关闭
  const handleOverlayClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (closeOnClickOutside && e.target === overlayRef.current) {
      onClose();
    }
  };

  // 自动聚焦
  useEffect(() => {
    if (isOpen && dialogRef.current) {
      // 延迟聚焦，确保动画完成
      const timer = setTimeout(() => {
        const firstInput = dialogRef.current?.querySelector("input");
        const firstButton = dialogRef.current?.querySelector("button");

        if (firstInput) {
          firstInput.focus();
          if (firstInput instanceof HTMLInputElement) {
            firstInput.select();
          }
        } else if (firstButton) {
          (firstButton as HTMLButtonElement).focus();
        }
      }, 100);

      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  // 使用 Portal 将 Modal 渲染到 body，避免受父容器样式影响
  return createPortal(
    <div
      ref={overlayRef}
      className="modal-overlay"
      onClick={handleOverlayClick}
      role="dialog"
      aria-modal="true"
      aria-labelledby={title ? "modal-title" : undefined}
    >
      <div ref={dialogRef} className="modal-dialog">
        {title && (
          <div className="modal-header">
            <h3 id="modal-title" className="modal-title">
              {title}
            </h3>
          </div>
        )}
        {children}
      </div>
    </div>,
    document.body
  );
}


