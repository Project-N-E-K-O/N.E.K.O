import React from "react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import "./Button.css";

export type ButtonVariant = "primary" | "secondary" | "danger" | "success";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /**
   * 直接传入按钮文本（若同时传 children，优先生效 children）
   */
  label?: ReactNode;
  /**
   * 按钮变体
   * @default "primary"
   */
  variant?: ButtonVariant;
  /**
   * 按钮尺寸
   * @default "md"
   */
  size?: ButtonSize;
  /**
   * 是否显示加载状态
   */
  loading?: boolean;
  /**
   * 左侧图标
   */
  icon?: ReactNode;
  /**
   * 右侧图标
   */
  iconRight?: ReactNode;
  /**
   * 是否全宽
   */
  fullWidth?: boolean;
  /**
   * 子元素
   */
  children?: ReactNode;
}

/**
 * Renders a configurable button with variants, sizes, optional icons, and a loading state.
 *
 * @param variant - Visual style of the button: "primary", "secondary", "danger", or "success".
 * @param size - Button size: "sm", "md", or "lg".
 * @param loading - When true, shows a spinner and disables the button.
 * @param icon - Left-side icon to display when not loading.
 * @param iconRight - Right-side icon to display when not loading.
 * @param fullWidth - When true, the button expands to fill its container's width.
 * @param disabled - If true the button is disabled; the button is also disabled while `loading` is true.
 * @param className - Additional CSS class names applied to the button element.
 * @param label - Fallback content used when `children` is not provided.
 * @param children - Button content; takes precedence over `label`.
 * @returns The rendered button element.
 */
export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  icon,
  iconRight,
  fullWidth = false,
  disabled,
  className = "",
  label,
  children,
  ...props
}: ButtonProps) {
  const isDisabled = disabled || loading;
  const content = children ?? label;

  // 构建类名
  const classes = [
    "btn",
    `btn-${variant}`,
    `btn-${size}`,
    fullWidth && "btn-full-width",
    loading && "btn-loading",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      className={classes}
      disabled={isDisabled}
      {...props}
    >
      {loading && (
        <span className="btn-spinner" aria-hidden="true">
          <svg
            className="btn-spinner-svg"
            viewBox="0 0 24 24"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <circle
              className="btn-spinner-circle"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
              strokeLinecap="round"
              strokeDasharray="32"
              strokeDashoffset="32"
            >
              <animate
                attributeName="stroke-dasharray"
                dur="2s"
                values="0 32;16 16;0 32;0 32"
                repeatCount="indefinite"
              />
              <animate
                attributeName="stroke-dashoffset"
                dur="2s"
                values="0;-16;-32;-32"
                repeatCount="indefinite"
              />
            </circle>
          </svg>
        </span>
      )}
      {icon && !loading && <span className="btn-icon-left">{icon}</span>}
      {content && <span className="btn-content">{content}</span>}
      {iconRight && !loading && <span className="btn-icon-right">{iconRight}</span>}
    </button>
  );
}


