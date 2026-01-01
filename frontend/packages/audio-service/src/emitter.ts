export class TinyEmitter<T extends Record<string, any>> {
  private listeners = new Map<keyof T, Set<(payload: any) => void>>();

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
      } catch (_e) {
        // ignore
      }
    }
  }
}

