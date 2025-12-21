export type Unsubscribe = () => void;

export class TinyEmitter<EventMap extends Record<string, any>> {
  private listeners: {
    [K in keyof EventMap]?: Array<(payload: EventMap[K]) => void>;
  } = {};

  on<K extends keyof EventMap>(event: K, handler: (payload: EventMap[K]) => void): Unsubscribe {
    const arr = (this.listeners[event] ||= []);
    arr.push(handler);
    return () => {
      const list = this.listeners[event];
      if (!list) return;
      const idx = list.indexOf(handler);
      if (idx >= 0) list.splice(idx, 1);
    };
  }

  emit<K extends keyof EventMap>(event: K, payload: EventMap[K]): void {
    const list = this.listeners[event];
    if (!list || list.length === 0) return;
    // 避免 handler 内部 on/off 导致迭代异常
    const snapshot = list.slice();
    snapshot.forEach((fn) => {
      try {
        fn(payload);
      } catch (_e) {
        // swallow：realtime client 不应因订阅者异常而崩溃
      }
    });
  }

  clear(): void {
    this.listeners = {};
  }
}


