import '@testing-library/jest-dom/vitest';

if (!HTMLElement.prototype.scrollTo) {
  HTMLElement.prototype.scrollTo = function scrollTo(options?: ScrollToOptions | number, _y?: number) {
    if (typeof options === 'number') {
      this.scrollLeft = options;
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
