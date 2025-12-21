export type WebSocketData = unknown;

export interface WebSocketMessageEventLike {
  data: WebSocketData;
}

export interface WebSocketLike {
  readyState: number;
  send(data: any): void;
  close(code?: number, reason?: string): void;
  onopen: ((ev?: any) => void) | null;
  onmessage: ((ev: WebSocketMessageEventLike) => void) | null;
  onclose: ((ev?: any) => void) | null;
  onerror: ((ev?: any) => void) | null;
}

export interface WebSocketConstructorLike {
  new (url: string, protocols?: string | string[]): WebSocketLike;
}

export type RealtimeConnectionState =
  | "idle"
  | "connecting"
  | "open"
  | "closing"
  | "closed"
  | "reconnecting";

export interface RealtimeHeartbeatOptions {
  /**
   * 心跳间隔（ms）。设为 0 或 undefined 表示关闭心跳。
   */
  intervalMs?: number;

  /**
   * 心跳 payload。默认使用 `{ action: "ping" }`（与旧版 static/app.js 协议保持一致）。
   * - 若为对象，会自动 JSON.stringify
   * - 若为函数，将在每次心跳时重新计算
   */
  payload?: string | Record<string, unknown> | (() => string | Record<string, unknown>);
}

export interface RealtimeReconnectOptions {
  enabled?: boolean;
  /**
   * 初始重连等待（ms）
   */
  minDelayMs?: number;
  /**
   * 最大重连等待（ms）
   */
  maxDelayMs?: number;
  /**
   * 退避倍数
   */
  backoffFactor?: number;
  /**
   * 抖动比例（0~1）。例如 0.2 表示在目标延迟基础上做 ±20% 抖动。
   */
  jitterRatio?: number;
  /**
   * 最大尝试次数（undefined 表示无限）
   */
  maxAttempts?: number;
  /**
   * 是否应当重连。手动 close() 会自动跳过重连。
   */
  shouldReconnect?: (info: { event?: any; attempts: number }) => boolean;
}

export interface RealtimeClientOptions {
  /**
   * 连接 URL。若提供 url，则忽略 buildUrl/path。
   */
  url?: string;

  /**
   * 连接 path（如 `/ws/xxx`），配合 buildUrl 使用。
   * 旧版页面通常使用 `/ws/{lanlan_name}`。
   */
  path?: string;

  /**
   * 将 path 转为 ws/wss URL 的构造函数。
   * Web 环境建议传入 `window.buildWebSocketUrl`（由 web-bridge 提供）。
   */
  buildUrl?: (path: string) => string;

  /**
   * 子协议
   */
  protocols?: string | string[];

  /**
   * 允许注入 WebSocket 构造器（便于测试/特殊环境）。
   * 默认使用 globalThis.WebSocket。
   */
  webSocketCtor?: WebSocketConstructorLike;

  /**
   * 是否自动尝试 JSON.parse 文本消息。默认 true。
   */
  parseJson?: boolean;

  /**
   * 心跳配置（默认 interval=30000，payload={action:"ping"}）
   */
  heartbeat?: RealtimeHeartbeatOptions;

  /**
   * 自动重连配置（默认 enabled=true，3s~30s 退避）
   */
  reconnect?: RealtimeReconnectOptions;
}

export type RealtimeEventMap = {
  state: { state: RealtimeConnectionState };
  open: undefined;
  close: { event?: any };
  error: { event?: any };
  message: { data: WebSocketData; rawEvent: WebSocketMessageEventLike };
  text: { text: string; rawEvent: WebSocketMessageEventLike };
  json: { json: unknown; text: string; rawEvent: WebSocketMessageEventLike };
  binary: { data: unknown; rawEvent: WebSocketMessageEventLike };
};


