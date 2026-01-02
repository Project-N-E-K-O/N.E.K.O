export class TinyEmitter<T extends Record<string, any>> {
  private listeners = new Map<keyof T, Set<(payload: any) => void>>();
  public onError?: (error: unknown, handler: (payload: T[keyof T]) => void, payload: T[keyof T]) => void;

  constructor(opts?: {
    /**
     * 事件处理器抛错时的钩子：
     * - 若提供，则优先调用（由上层决定如何上报/提示/中断）
     * - 若不提供，则默认使用 console.error 打印
     */
    onError?: (error: unknown, handler: (payload: T[keyof T]) => void, payload: T[keyof T]) => void;
  }) {
    this.onError = opts?.onError;
  }

  on<K extends keyof T>(event: K, handler: (payload: T[K]) => void): () => void {
    const set = this.listeners.get(event) || new Set();
    set.add(handler as any);
    this.listeners.set(event, set);
    return () => {
      const curr = this.listeners.get(event);
      if (!curr) return;
      curr.delete(handler as any);
      if (curr.size === 0) this.listeners.delete(event);
    };
  }

  emit<K extends keyof T>(event: K, payload: T[K]) {
    const set = this.listeners.get(event);
    if (!set) return;
    for (const handler of set) {
      try {
        (handler as any)(payload);
      } catch (error) {
        const onError = this.onError;
        if (onError) {
          onError(error, handler as any, payload as any);
        } else {
          const handlerName =
            typeof handler === "function" && (handler as any).name ? String((handler as any).name) : "<anonymous>";
          console.error(`[TinyEmitter] 事件处理器抛错 (event="${String(event)}", handler="${handlerName}")`, {
            error,
            handler,
            payload,
          });
        }
      }
    }
  }
}

