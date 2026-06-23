import '@testing-library/jest-dom/vitest';

if (
  typeof window.localStorage === 'undefined'
  || typeof window.localStorage.getItem !== 'function'
  || typeof window.localStorage.setItem !== 'function'
  || typeof window.localStorage.removeItem !== 'function'
) {
  const store = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
      setItem: (key: string, value: string) => {
        store.set(key, String(value));
      },
      removeItem: (key: string) => {
        store.delete(key);
      },
      clear: () => {
        store.clear();
      },
    },
  });
}

if (!HTMLElement.prototype.scrollTo) {
  HTMLElement.prototype.scrollTo = function scrollTo(options?: ScrollToOptions | number, _y?: number) {
    if (typeof options === 'number') {
      this.scrollLeft = options;
      if (typeof _y === 'number') {
        this.scrollTop = _y;
      }
      return;
    }
    if (options && typeof options === 'object' && typeof options.top === 'number') {
      this.scrollTop = options.top;
    }
    if (options && typeof options === 'object' && typeof options.left === 'number') {
      this.scrollLeft = options.left;
    }
  };
}

if (typeof window.ResizeObserver === 'undefined') {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  window.ResizeObserver = ResizeObserverStub as typeof ResizeObserver;
  globalThis.ResizeObserver = ResizeObserverStub as typeof ResizeObserver;
}
