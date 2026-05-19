import '@testing-library/jest-dom/vitest';

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
  // Match the real `ResizeObserver(callback)` signature so callers like
  // `new ResizeObserver(measure)` aren't falsely flagged as passing a
  // superfluous argument by signature-based static analysis (CodeQL).
  class ResizeObserverStub {
    constructor(_cb?: ResizeObserverCallback) {}
    observe() {}
    unobserve() {}
    disconnect() {}
  }

  window.ResizeObserver = ResizeObserverStub as typeof ResizeObserver;
  globalThis.ResizeObserver = ResizeObserverStub as typeof ResizeObserver;
}
