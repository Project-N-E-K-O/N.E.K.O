import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  forwardRef,
  useImperativeHandle,
} from "react";
import { createPortal } from "react-dom";
import "./StatusToast.css";

export interface StatusToastProps {
  /**
   * 可选：静态资源根路径（提供 /static），优先于 window/env 内部解析
   */
  staticBaseUrl?: string;
}

const trimTrailingSlash = (url?: string) => (url ? url.replace(/\/+$/, "") : "");
const resolveStaticBase = (): string => {
  try {
    const env = (import.meta as any)?.env ?? {};
    const w = typeof window !== "undefined" ? (window as any) : {};
    return (
      trimTrailingSlash(
        w.STATIC_SERVER_URL ||
          w.API_BASE_URL ||
          env.VITE_STATIC_SERVER_URL ||
          env.VITE_API_BASE_URL ||
          ""
      ) || ""
    );
  } catch (_e) {
    return "";
  }
};

interface StatusToastState {
  message: string;
  duration: number;
  isVisible: boolean;
}

export interface StatusToastHandle {
  show: (message: string, duration?: number) => void;
}

const StatusToast = forwardRef<StatusToastHandle | null, StatusToastProps>(function StatusToastComponent(
  { staticBaseUrl },
  ref
) {
  const [toastState, setToastState] = useState<StatusToastState>({
    message: "",
    duration: 3000,
    isVisible: false,
  });

  // 根据 static_url 自动设置背景图，避免依赖外部页面手动注入
  useEffect(() => {
    if (typeof document === "undefined") return;
    const base = trimTrailingSlash(staticBaseUrl || resolveStaticBase());
    if (!base) return;
    const url = `${base}/static/icons/toast_background.png`;
    document.documentElement.style.setProperty("--toast-background-url", `url('${url}')`);
  }, [staticBaseUrl]);

  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const portalContainerRef = useRef<HTMLElement | null>(null);
  const createdPortalContainerRef = useRef(false);

  // 创建 Portal 容器（SSR 安全）
  useEffect(() => {
    if (typeof document === "undefined") return;
    let container = document.getElementById("status-toast-container");
    if (!container) {
      container = document.createElement("div");
      container.id = "status-toast-container";
      document.body.appendChild(container);
      createdPortalContainerRef.current = true;
    }
    portalContainerRef.current = container;

    return () => {
      if (createdPortalContainerRef.current && portalContainerRef.current?.parentNode) {
        portalContainerRef.current.parentNode.removeChild(portalContainerRef.current);
      }
      portalContainerRef.current = null;
      createdPortalContainerRef.current = false;
    };
  }, []);

  const showToast = useCallback((message: string, duration: number = 3000) => {
    if (hideTimer.current) {
      clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    if (clearTimer.current) {
      clearTimeout(clearTimer.current);
      clearTimer.current = null;
    }

    if (!message || message.trim() === "") {
      setToastState((prev) => ({ ...prev, isVisible: false }));
      clearTimer.current = setTimeout(() => {
        setToastState((prev) => ({ ...prev, message: "" }));
      }, 300);
      return;
    }

    // 立即更新内容并显示
    setToastState({
      message,
      duration,
      isVisible: true,
    });

    // 计时自动隐藏
    hideTimer.current = setTimeout(() => {
      setToastState((prev) => ({ ...prev, isVisible: false }));
      clearTimer.current = setTimeout(() => {
        setToastState((prev) => ({ ...prev, message: "" }));
      }, 300);
    }, duration);
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      show: showToast,
    }),
    [showToast]
  );

  // 卸载时清理定时器，避免潜在的状态更新警告
  useEffect(() => {
    return () => {
      if (hideTimer.current) {
        clearTimeout(hideTimer.current);
      }
      if (clearTimer.current) {
        clearTimeout(clearTimer.current);
      }
    };
  }, []);

  // 同时更新隐藏的 status 元素（保持兼容性，匹配 app.js）
  useEffect(() => {
    const statusElement = document.getElementById("status");
    if (statusElement) {
      statusElement.textContent = toastState.message || "";
    }
  }, [toastState.message]);

  const className = toastState.message
    ? toastState.isVisible
      ? "show"
      : "hide"
    : "";

  // 返回真实 DOM，Portal 到 body（SSR 时回退为就地渲染）
  const toastContent = (
    <div
      id="status-toast"
      className={className}
      aria-live="polite"
    >
      {toastState.message}
    </div>
  );

  return portalContainerRef.current ? createPortal(toastContent, portalContainerRef.current) : toastContent;
});

export { StatusToast };

// 导出默认函数，用于在 index.html 中直接挂载
export default StatusToast;


