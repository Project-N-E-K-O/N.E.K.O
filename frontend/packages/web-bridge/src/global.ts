export {};

declare global {
  interface StatusToastQueueItem {
    message: string;
    duration: number;
  }

  interface LanlanConfig {
    lanlan_name?: string;
  }

  interface Window {
    // React 组件相关
    __statusToastQueue?: StatusToastQueueItem[];
    __REACT_READY?: boolean;
    lanlan_config?: LanlanConfig;
    t?: (key: string, params?: Record<string, unknown>) => string;
    showStatusToast?: (message: string, duration?: number) => void;
    showAlert?: (message: string, title?: string | null) => Promise<boolean>;
    showConfirm?: (
      message: string,
      title?: string | null,
      options?: { okText?: string; cancelText?: string; danger?: boolean }
    ) => Promise<boolean>;
    showPrompt?: (message: string, defaultValue?: string, title?: string | null) => Promise<string | null>;
    __modalReady?: boolean;

    // Request 相关
    request?: any;
    RequestAPI?: Record<string, any>;
    buildApiUrl?: (path: string) => string;
    buildStaticUrl?: (path: string) => string;
    buildWebSocketUrl?: (path: string) => string;
    fetchWithBaseUrl?: (path: string, init?: RequestInit) => Promise<Response>;
    API_BASE_URL?: string;
    STATIC_SERVER_URL?: string;
    WEBSOCKET_URL?: string;

    // Realtime/WebSocket 相关
    createRealtimeClient?: (options: any) => any;
    realtime?: any;

    // 内部标记：web-bridge 是否已自动绑定默认 request
    __nekoBridgeRequestBound?: boolean;
    __nekoBridgeRealtimeBound?: boolean;
  }
}

