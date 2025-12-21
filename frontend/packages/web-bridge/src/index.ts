import type { ModalHandle, StatusToastHandle } from "@project_neko/components";
import type { RequestClientConfig, TokenStorage } from "@project_neko/request";
import { createRequestClient } from "@project_neko/request";
import { WebTokenStorage } from "@project_neko/request";
import { createRealtimeClient } from "@project_neko/realtime";
import type { RealtimeClientOptions } from "@project_neko/realtime";
import type { AxiosInstance } from "axios";
import "./global";

type Cleanup = () => void;

const isAbsoluteUrl = (url: string): boolean =>
  /^(?:https?:|wss?:)?\/\//.test(url);

const trimTrailingSlash = (url?: string): string =>
  url ? url.replace(/\/+$/, "") : "";

const ensureLeadingSlash = (path: string): string =>
  path.startsWith("/") ? path : `/${path}`;

const readEnv = (key: string): string | undefined => {
  try {
    // 兼容 Vite/ESM 环境
    return (import.meta as any)?.env?.[key];
  } catch (_e) {
    return undefined;
  }
};

const defaultApiBase = (): string =>
  window.API_BASE_URL ||
  readEnv("VITE_API_BASE_URL") ||
  "http://localhost:48911";

const defaultStaticBase = (apiBase: string): string =>
  window.STATIC_SERVER_URL || readEnv("VITE_STATIC_SERVER_URL") || apiBase;

const defaultWebSocketBase = (apiBase: string): string =>
  window.WEBSOCKET_URL || readEnv("VITE_WEBSOCKET_URL") || apiBase;

const resolveApiBaseUrl = (
  options: Partial<RequestWindowOptions> & Partial<RequestClientConfig> = {}
): string => options.apiBaseUrl || options.baseURL || defaultApiBase();

const buildHttpUrl = (base: string, path: string): string => {
  if (isAbsoluteUrl(path)) return path;
  const cleanBase = trimTrailingSlash(base);
  const cleanPath = ensureLeadingSlash(path);
  return `${cleanBase}${cleanPath}`;
};

const toWebSocketUrl = (url: string): string =>
  url
    .replace(/^http:\/\//i, "ws://")
    .replace(/^https:\/\//i, "wss://");

export interface RequestWindowOptions {
  apiBaseUrl?: string;
  staticServerUrl?: string;
  websocketUrl?: string;
}

export interface RealtimeWindowOptions {
  /**
   * 默认情况下会使用 window.buildWebSocketUrl（若已由 bindRequestToWindow 注入）
   * 或回退到 location 推导。
   */
  path?: string;
  url?: string;
  protocols?: string | string[];
}

/**
 * Extract the lanlan_name identifier from the当前页面 URL。
 *
 * 优先读取查询参数 `lanlan_name`，若不存在则尝试从路径首段解析（排除保留段），并进行 URL 解码。
 *
 * @returns 解析到的 lanlan_name，若无法获取则返回空字符串
 */
export function resolveLanlanNameFromLocation(): string {
  // 优先 URL 参数
  const urlParams = new URLSearchParams(window.location.search);
  let lanlanNameFromUrl = urlParams.get("lanlan_name") || "";

  // 再从路径提取 /{lanlan_name}
  if (!lanlanNameFromUrl) {
    const pathParts = window.location.pathname.split("/").filter(Boolean);
    if (pathParts.length > 0 && !["focus", "api", "static", "templates"].includes(pathParts[0])) {
      lanlanNameFromUrl = decodeURIComponent(pathParts[0]);
    }
  }

  return lanlanNameFromUrl;
}

/**
 * 将 StatusToast 句柄绑定到 window，提供队列处理与就绪事件。
 *
 * - 暴露 `window.showStatusToast`，在 React 未就绪时会排队，待就绪后逐条显示。
 * - 尝试展示启动提示（来自 `window.lanlan_config`）或挂起的消息。
 * - 派发 `statusToastReady` 事件，返回清理函数。
 *
 * @param handle - 用于展示 toast 的 StatusToastHandle
 * @returns 清理函数，移除监听并清理定时器
 */
export function bindStatusToastToWindow(handle: StatusToastHandle): Cleanup {
  if (typeof window === "undefined") {
    return () => {};
  }

  let reactReadyListenerAttached = false;

  const pendingMessages =
    window.__statusToastQueue && window.__statusToastQueue.length > 0
      ? [...window.__statusToastQueue]
      : [];

  const wrappedShowToast = (message: string, duration: number = 3000) => {
    if (!message || message.trim() === "") {
      return;
    }

    if (window.__REACT_READY) {
      handle.show(message, duration);
      return;
    }

    if (!window.__statusToastQueue) {
      window.__statusToastQueue = [];
    }
    window.__statusToastQueue.push({ message, duration });

    if (!reactReadyListenerAttached) {
      const handleReactReady = () => {
        const queue = window.__statusToastQueue || [];
        queue.forEach((item) => handle.show(item.message, item.duration));
        window.__statusToastQueue = [];
        reactReadyListenerAttached = false;
      };
      window.addEventListener("react-ready", handleReactReady, { once: true });
      reactReadyListenerAttached = true;
    }
  };

  Object.defineProperty(window, "showStatusToast", {
    value: wrappedShowToast,
    writable: true,
    configurable: true,
    enumerable: true,
  });

  if (pendingMessages.length > 0) {
    const lastMessage = pendingMessages[pendingMessages.length - 1];
    if (lastMessage) {
      setTimeout(() => {
        wrappedShowToast(lastMessage.message, lastMessage.duration);
      }, 300);
    }
    window.__statusToastQueue = [];
  }

  const handleLoad = () => {
    setTimeout(() => {
      const loadQueue = window.__statusToastQueue || [];
      if (loadQueue.length > 0) {
        const lastLoadMessage = loadQueue[loadQueue.length - 1];
        if (lastLoadMessage) {
          wrappedShowToast(lastLoadMessage.message, lastLoadMessage.duration);
          window.__statusToastQueue = [];
        }
      } else if (typeof window.lanlan_config !== "undefined" && window.lanlan_config?.lanlan_name) {
        const message =
          window.t?.("app.started", { name: window.lanlan_config.lanlan_name }) ??
          `${window.lanlan_config.lanlan_name}已启动`;
        wrappedShowToast(message, 3000);
      }
    }, 1500);
  };

  const loadAttached = document.readyState !== "complete";
  if (loadAttached) {
    window.addEventListener("load", handleLoad, { once: true });
  } else {
    handleLoad();
  }

  const readyTimer = setTimeout(() => {
    window.dispatchEvent(new CustomEvent("statusToastReady"));

    setTimeout(() => {
      const delayedQueue = window.__statusToastQueue || [];
      if (delayedQueue.length > 0) {
        const lastDelayedMessage = delayedQueue[delayedQueue.length - 1];
        if (lastDelayedMessage) {
          wrappedShowToast(lastDelayedMessage.message, lastDelayedMessage.duration);
          window.__statusToastQueue = [];
        }
      }
    }, 100);
  }, 50);

  return () => {
    clearTimeout(readyTimer);
    if (loadAttached) {
      window.removeEventListener("load", handleLoad);
    }
  };
}

/**
 * 将 Modal 相关方法绑定到 window 并派发就绪事件。
 *
 * 暴露 `window.showAlert` / `showConfirm` / `showPrompt`，使用传入的句柄执行实际弹窗逻辑，
 * 并在短暂延迟后派发 `modalReady` 事件、设置 `window.__modalReady`。
 *
 * @param handle - 提供 alert/confirm/prompt 的 ModalHandle
 * @returns 清理函数，用于取消就绪定时器
 */
export function bindModalToWindow(handle: ModalHandle): Cleanup {
  if (typeof window === "undefined") {
    return () => {};
  }

  const getDefaultTitle = (type: "alert" | "confirm" | "prompt"): string => {
    try {
      if (window.t && typeof window.t === "function") {
        switch (type) {
          case "alert":
            return window.t("common.alert");
          case "confirm":
            return window.t("common.confirm");
          case "prompt":
            return window.t("common.input");
          default:
            return "提示";
        }
      }
    } catch (_e) {
      // ignore i18n errors
    }
    switch (type) {
      case "alert":
        return "提示";
      case "confirm":
        return "确认";
      case "prompt":
        return "输入";
      default:
        return "提示";
    }
  };

  const showAlert = (message: string, title: string | null = null): Promise<boolean> => {
    return handle.alert(message, title !== null ? title : getDefaultTitle("alert"));
  };

  const showConfirm = (
    message: string,
    title: string | null = null,
    options: { okText?: string; cancelText?: string; danger?: boolean } = {}
  ): Promise<boolean> => {
    return handle.confirm(message, title !== null ? title : getDefaultTitle("confirm"), options);
  };

  const showPrompt = (
    message: string,
    defaultValue: string = "",
    title: string | null = null
  ): Promise<string | null> => {
    return handle.prompt(message, defaultValue, title !== null ? title : getDefaultTitle("prompt"));
  };

  Object.defineProperty(window, "showAlert", {
    value: showAlert,
    writable: true,
    configurable: true,
    enumerable: true,
  });

  Object.defineProperty(window, "showConfirm", {
    value: showConfirm,
    writable: true,
    configurable: true,
    enumerable: true,
  });

  Object.defineProperty(window, "showPrompt", {
    value: showPrompt,
    writable: true,
    configurable: true,
    enumerable: true,
  });

  const readyTimer = setTimeout(() => {
    window.dispatchEvent(new CustomEvent("modalReady"));
    window.__modalReady = true;
  }, 50);

  return () => {
    clearTimeout(readyTimer);
  };
}

/**
 * 将组件句柄批量绑定到 window。
 *
 * @param handles.toast - 可选的 toast 句柄
 * @param handles.modal - 可选的 modal 句柄
 * @returns 清理函数，逐一调用内部清理
 */
export function bindComponentsToWindow(handles: {
  toast?: StatusToastHandle | null;
  modal?: ModalHandle | null;
}): Cleanup {
  const cleanups: Cleanup[] = [];
  if (handles.toast) {
    cleanups.push(bindStatusToastToWindow(handles.toast));
  }
  if (handles.modal) {
    cleanups.push(bindModalToWindow(handles.modal));
  }

  return () => {
    cleanups.forEach((fn) => fn && fn());
  };
}

/**
 * 将 Axios 客户端与 URL 构建工具挂载到 window，并派发 `requestReady`。
 *
 * 暴露 `window.request`、`API_BASE_URL`、`STATIC_SERVER_URL`、`WEBSOCKET_URL` 以及
 * `buildApiUrl`/`buildStaticUrl`/`buildWebSocketUrl`/`fetchWithBaseUrl`。
 *
 * @param client - 要挂载的 Axios 实例
 * @param options - 可选的基础 URL 覆盖项
 * @returns 清理函数，取消就绪定时器
 */
export function bindRequestToWindow(client: AxiosInstance, options: RequestWindowOptions = {}): Cleanup {
  if (typeof window === "undefined") {
    return () => {};
  }

  const apiBase = trimTrailingSlash(options.apiBaseUrl || defaultApiBase());
  const staticBase = trimTrailingSlash(options.staticServerUrl || defaultStaticBase(apiBase));
  const websocketBase = trimTrailingSlash(options.websocketUrl || defaultWebSocketBase(apiBase));

  const buildApiUrl = (path: string) => buildHttpUrl(apiBase, path);
  const buildStaticUrl = (path: string) => buildHttpUrl(staticBase || apiBase, path);
  const buildWebSocketUrl = (path: string) => {
    if (isAbsoluteUrl(path)) {
      return toWebSocketUrl(path);
    }
    const httpUrl = buildHttpUrl(websocketBase || apiBase, path);
    return toWebSocketUrl(httpUrl);
  };

  const fetchWithBaseUrl = (path: string, init?: RequestInit) =>
    fetch(buildApiUrl(path), init);

  Object.defineProperty(window, "request", {
    value: client,
    writable: true,
    configurable: true,
    enumerable: true,
  });
  window.API_BASE_URL = apiBase;
  window.STATIC_SERVER_URL = staticBase;
  window.WEBSOCKET_URL = websocketBase;
  window.buildApiUrl = buildApiUrl;
  window.buildStaticUrl = buildStaticUrl;
  window.buildWebSocketUrl = buildWebSocketUrl;
  window.fetchWithBaseUrl = fetchWithBaseUrl;

  const readyTimer = setTimeout(() => {
    window.dispatchEvent(new CustomEvent("requestReady"));
  }, 0);

  return () => {
    clearTimeout(readyTimer);
  };
}

/**
 * 将 Realtime(WebSocket) 客户端构造器与默认实例绑定到 window，并派发 `websocketReady`。
 *
 * 暴露：
 * - `window.createRealtimeClient(options)`：创建并返回 client
 * - `window.realtime`：默认 client（若传入 options.url 或 options.path）
 *
 * 注意：
 * - 旧模板可直接使用 UMD `window.ProjectNekoRealtime`，此绑定是“更统一的 window API”。
 * - 默认实例不会自动 connect；由调用方决定何时 connect。
 */
export function bindRealtimeToWindow(options: RealtimeWindowOptions = {}): { client: any | null; cleanup: Cleanup } {
  if (typeof window === "undefined") {
    return { client: null, cleanup: () => {} };
  }

  const factory = (opts: RealtimeClientOptions) => createRealtimeClient(opts);

  Object.defineProperty(window, "createRealtimeClient", {
    value: factory,
    writable: true,
    configurable: true,
    enumerable: true,
  });

  let client: any | null = null;
  const urlOrPath = options.url || options.path;
  if (urlOrPath) {
    client = createRealtimeClient({
      url: options.url,
      path: options.path,
      protocols: options.protocols,
      buildUrl: typeof window.buildWebSocketUrl === "function" ? window.buildWebSocketUrl : undefined,
    });

    Object.defineProperty(window, "realtime", {
      value: client,
      writable: true,
      configurable: true,
      enumerable: true,
    });
  }

  const readyTimer = setTimeout(() => {
    window.dispatchEvent(new CustomEvent("websocketReady"));
  }, 0);

  const cleanup = () => {
    clearTimeout(readyTimer);
  };

  return { client, cleanup };
}

export interface CreateAndBindRequestOptions
  extends Partial<RequestClientConfig>,
    RequestWindowOptions {
  storage?: TokenStorage;
}

/**
 * 创建 Axios 客户端并绑定到 window，返回客户端与清理函数。
 *
 * @param options - 可选项：apiBaseUrl/baseURL、staticServerUrl/websocketUrl、storage、refreshApi 等
 * @returns 包含 `client` 与 `cleanup` 的对象
 */
export function createAndBindRequest(
  options: CreateAndBindRequestOptions = {}
): { client: AxiosInstance; cleanup: Cleanup } {
  const apiBaseUrl = resolveApiBaseUrl(options);
  const storage = options.storage || new WebTokenStorage();
  const refreshApi =
    options.refreshApi ||
    (async () => {
      throw new Error("refreshApi not implemented");
    });

  const client = createRequestClient({
    ...options,
    baseURL: apiBaseUrl,
    storage,
    refreshApi,
  });

  const cleanup = bindRequestToWindow(client, {
    apiBaseUrl,
    staticServerUrl: options.staticServerUrl,
    websocketUrl: options.websocketUrl,
  });

  return { client, cleanup };
}

/**
 * 创建默认的 Axios 客户端（不绑定 window）。
 *
 * @param options - 允许覆盖 baseURL/apiBaseUrl、storage、refreshApi 等
 * @returns 组装好的 Axios 实例
 */
export function createDefaultRequestClient(
  options: Partial<CreateAndBindRequestOptions> = {}
): AxiosInstance {
  const apiBaseUrl = resolveApiBaseUrl(options);
  const storage = options.storage || new WebTokenStorage();
  const refreshApi =
    options.refreshApi ||
    (async () => {
      throw new Error("refreshApi not implemented");
    });

  return createRequestClient({
    ...options,
    baseURL: apiBaseUrl,
    storage,
    refreshApi,
  });
}

/**
 * 创建默认的 Axios 客户端并绑定到 window。
 *
 * @param options - 用于构建客户端和覆盖 window URL 的配置
 * @returns `{ client, cleanup }`：已绑定的 Axios 实例与解绑函数
 */
export function bindDefaultRequestToWindow(
  options: CreateAndBindRequestOptions = {}
): { client: AxiosInstance; cleanup: Cleanup } {
  const apiBaseUrl = resolveApiBaseUrl(options);
  const client = createDefaultRequestClient({
    ...options,
    apiBaseUrl,
    baseURL: apiBaseUrl,
  });
  const cleanup = bindRequestToWindow(client, {
    apiBaseUrl,
    staticServerUrl: options.staticServerUrl,
    websocketUrl: options.websocketUrl,
  });
  return { client, cleanup };
}

/**
 * 确保默认 Axios 实例在浏览器环境下绑定到 window.request。
 *
 * 若已绑定则直接返回已存在实例；非浏览器环境返回 null。
 */
export function autoBindDefaultRequest(): AxiosInstance | null {
  if (typeof window === "undefined") return null;
  if (window.__nekoBridgeRequestBound && window.request) {
    return window.request;
  }
  const { client } = bindDefaultRequestToWindow();
  window.__nekoBridgeRequestBound = true;
  return client;
}

// 立即执行一次自动绑定，确保页面引入 web-bridge 后即可使用 window.request
if (typeof window !== "undefined") {
  autoBindDefaultRequest();
  // 仅提供构造器（window.createRealtimeClient）。默认不创建实例/不自动 connect，避免旧页面产生隐式连接。
  if (!window.__nekoBridgeRealtimeBound) {
    bindRealtimeToWindow();
    window.__nekoBridgeRealtimeBound = true;
  }
}

