// 公共工具与类型的入口，可按需扩展

export type ApiResponse<T = unknown> = {
  code?: number;
  message?: string;
  data?: T;
};

/**
 * Performs no operation.
 *
 * This function intentionally does nothing and exists as a callable placeholder.
 */
export function noop(..._args: any[]): void {
  // intentionally empty
}

/**
 * 取消订阅函数类型
 */
export type Unsubscribe = () => void;

/**
 * 轻量级事件发射器
 * 
 * @template T - 事件映射类型，键为事件名，值为 payload 类型
 * 
 * @example
 * ```typescript
 * type Events = {
 *   'user:login': { userId: string };
 *   'user:logout': void;
 * };
 * 
 * const emitter = new TinyEmitter<Events>();
 * const unsubscribe = emitter.on('user:login', (payload) => {
 *   console.log('User logged in:', payload.userId);
 * });
 * 
 * emitter.emit('user:login', { userId: '123' });
 * unsubscribe();
 * ```
 */
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

  /**
   * 订阅事件
   * 
   * @param event - 事件名
   * @param handler - 事件处理器
   * @returns 取消订阅函数
   */
  on<K extends keyof T>(event: K, handler: (payload: T[K]) => void): Unsubscribe {
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

  /**
   * 发射事件
   * 
   * @param event - 事件名
   * @param payload - 事件 payload
   */
  emit<K extends keyof T>(event: K, payload: T[K]): void {
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

  /**
   * 清空所有事件监听器
   */
  clear(): void {
    this.listeners.clear();
  }
}
