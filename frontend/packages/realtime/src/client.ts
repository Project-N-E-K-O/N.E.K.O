import { TinyEmitter } from "./emitter";
import type {
  RealtimeClientOptions,
  RealtimeConnectionState,
  RealtimeEventMap,
  WebSocketConstructorLike,
  WebSocketLike,
  WebSocketMessageEventLike,
} from "./types";
import { buildWebSocketUrlFromBase, defaultWebSocketBaseFromLocation } from "./url";

function nowMs(): number {
  return Date.now();
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function safeJsonParse(text: string): { ok: true; value: unknown } | { ok: false; error: unknown } {
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch (error) {
    return { ok: false, error };
  }
}

function resolveWebSocketCtor(explicit?: WebSocketConstructorLike): WebSocketConstructorLike {
  if (explicit) return explicit;
  const ctor = (globalThis as any).WebSocket as WebSocketConstructorLike | undefined;
  if (!ctor) {
    throw new Error("WebSocket is not available in this environment. Please provide options.webSocketCtor.");
  }
  return ctor;
}

function resolveUrl(options: RealtimeClientOptions): string {
  if (options.url) return options.url;
  const path = options.path || "";
  if (!path) {
    throw new Error("RealtimeClientOptions.url or RealtimeClientOptions.path is required.");
  }
  if (options.buildUrl) {
    return options.buildUrl(path);
  }
  const base = defaultWebSocketBaseFromLocation();
  if (!base) {
    throw new Error("Cannot infer WebSocket base from location. Please provide options.url or options.buildUrl.");
  }
  return buildWebSocketUrlFromBase(base, path);
}

export interface RealtimeClient {
  connect: () => void;
  disconnect: (args?: { code?: number; reason?: string }) => void;
  send: (data: any) => void;
  sendJson: (value: unknown) => void;
  getState: () => RealtimeConnectionState;
  getUrl: () => string;
  getSocket: () => WebSocketLike | null;
  on: <K extends keyof RealtimeEventMap>(
    event: K,
    handler: (payload: RealtimeEventMap[K]) => void
  ) => () => void;
}

export function createRealtimeClient(options: RealtimeClientOptions): RealtimeClient {
  const emitter = new TinyEmitter<RealtimeEventMap>();

  const parseJson = options.parseJson !== false;

  const heartbeatIntervalMs = clamp(options.heartbeat?.intervalMs ?? 30_000, 0, 60 * 60 * 1000);
  const heartbeatPayload = options.heartbeat?.payload ?? { action: "ping" };

  const reconnectEnabled = options.reconnect?.enabled !== false;
  const reconnectMinDelayMs = clamp(options.reconnect?.minDelayMs ?? 3_000, 0, 60 * 60 * 1000);
  const reconnectMaxDelayMs = clamp(options.reconnect?.maxDelayMs ?? 30_000, 0, 60 * 60 * 1000);
  const reconnectFactor = clamp(options.reconnect?.backoffFactor ?? 1.6, 1, 100);
  const reconnectJitter = clamp(options.reconnect?.jitterRatio ?? 0.2, 0, 1);
  const reconnectMaxAttempts = options.reconnect?.maxAttempts;
  const shouldReconnect =
    options.reconnect?.shouldReconnect || (() => true);

  let state: RealtimeConnectionState = "idle";
  let socket: WebSocketLike | null = null;
  let manualClose = false;

  let reconnectAttempts = 0;
  let reconnectTimer: any = null;
  let heartbeatTimer: any = null;
  let lastConnectAt = 0;

  const setState = (next: RealtimeConnectionState) => {
    if (state === next) return;
    state = next;
    emitter.emit("state", { state: next });
  };

  const clearTimers = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  };

  const stopHeartbeat = () => {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  };

  const startHeartbeat = () => {
    stopHeartbeat();
    if (!heartbeatIntervalMs || heartbeatIntervalMs <= 0) return;
    heartbeatTimer = setInterval(() => {
      if (!socket || socket.readyState !== 1 /* OPEN */) return;
      const payload = typeof heartbeatPayload === "function" ? heartbeatPayload() : heartbeatPayload;
      if (typeof payload === "string") {
        try {
          socket.send(payload);
        } catch (_e) {}
        return;
      }
      try {
        socket.send(JSON.stringify(payload));
      } catch (_e) {}
    }, heartbeatIntervalMs);
  };

  const detachSocketHandlers = (s: WebSocketLike) => {
    s.onopen = null;
    s.onmessage = null;
    s.onclose = null;
    s.onerror = null;
  };

  const scheduleReconnect = (event?: any) => {
    if (!reconnectEnabled) return;
    if (manualClose) return;
    if (reconnectMaxAttempts !== undefined && reconnectAttempts >= reconnectMaxAttempts) return;
    if (!shouldReconnect({ event, attempts: reconnectAttempts })) return;

    setState("reconnecting");
    reconnectAttempts += 1;

    const rawDelay = Math.min(
      reconnectMaxDelayMs,
      reconnectMinDelayMs * Math.pow(reconnectFactor, Math.max(0, reconnectAttempts - 1))
    );
    const jitter = rawDelay * reconnectJitter;
    const delay = clamp(rawDelay + (Math.random() * 2 - 1) * jitter, 0, reconnectMaxDelayMs);

    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connectInternal();
    }, delay);
  };

  const handleMessage = (rawEvent: WebSocketMessageEventLike) => {
    const data = rawEvent?.data;
    emitter.emit("message", { data, rawEvent });

    if (isString(data)) {
      emitter.emit("text", { text: data, rawEvent });
      if (parseJson) {
        const parsed = safeJsonParse(data);
        if (parsed.ok) {
          emitter.emit("json", { json: parsed.value, text: data, rawEvent });
        }
      }
      return;
    }

    // 二进制 / 非字符串消息：Blob/ArrayBuffer/TypedArray/RN 原生对象等
    emitter.emit("binary", { data, rawEvent });
  };

  const connectInternal = () => {
    if (socket && (socket.readyState === 0 /* CONNECTING */ || socket.readyState === 1 /* OPEN */)) {
      return;
    }

    // 仅当确实要发起新连接时再清理计时器/重置手动关闭标记；
    // 否则外部误调用 connect() 可能会打断已建立连接的心跳等逻辑。
    clearTimers();
    manualClose = false;

    const url = resolveUrl(options);
    const ctor = resolveWebSocketCtor(options.webSocketCtor);
    lastConnectAt = nowMs();
    setState("connecting");

    let s: WebSocketLike;
    try {
      s = new ctor(url, options.protocols);
    } catch (error) {
      setState("closed");
      emitter.emit("error", { event: error });
      scheduleReconnect(error);
      return;
    }

    socket = s;

    s.onopen = () => {
      reconnectAttempts = 0;
      setState("open");
      emitter.emit("open", undefined as any);
      startHeartbeat();
    };

    s.onmessage = (ev: WebSocketMessageEventLike) => {
      handleMessage(ev);
    };

    s.onclose = (ev?: any) => {
      stopHeartbeat();
      setState("closed");
      emitter.emit("close", { event: ev });
      detachSocketHandlers(s);
      if (socket === s) socket = null;
      scheduleReconnect(ev);
    };

    s.onerror = (ev?: any) => {
      // 某些平台 error 不一定会触发 close；这里先派发 error，重连交给 close 触发。
      emitter.emit("error", { event: ev });
    };
  };

  const connect = () => {
    // 上层可能会在 client 已经 open/connecting/reconnecting 时重复调用 connect()。
    // 仅在 idle/closed 时才允许显式 connect，避免重复连接/打断现有心跳。
    if (state !== "idle" && state !== "closed") return;

    // connectInternal 内部也会忽略重复连接尝试（CONNECTING/OPEN）。
    connectInternal();
  };

  const disconnect = (args?: { code?: number; reason?: string }) => {
    manualClose = true;
    clearTimers();
    setState("closing");

    if (!socket) {
      setState("closed");
      return;
    }

    const s = socket;
    socket = null;
    try {
      detachSocketHandlers(s);
      s.close(args?.code, args?.reason);
    } catch (_e) {
      // ignore
    } finally {
      setState("closed");
    }
  };

  const send = (data: any) => {
    if (!socket || socket.readyState !== 1 /* OPEN */) {
      throw new Error("WebSocket is not open");
    }
    socket.send(data);
  };

  const sendJson = (value: unknown) => {
    send(JSON.stringify(value));
  };

  return {
    connect,
    disconnect,
    send,
    sendJson,
    getState: () => state,
    getUrl: () => resolveUrl(options),
    getSocket: () => socket,
    on: (event, handler) => emitter.on(event as any, handler as any),
  };
}


