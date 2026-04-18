import '@testing-library/jest-dom/vitest';

if (!HTMLElement.prototype.scrollTo) {
  HTMLElement.prototype.scrollTo = function scrollTo(options?: number | ScrollToOptions, y?: number) {
    if (typeof options === 'number') {
      this.scrollLeft = options;
      this.scrollTop = typeof y === 'number' ? y : this.scrollTop;
      return;
    }

    if (options && typeof options.top === 'number') {
      this.scrollTop = options.top;
    }
    if (options && typeof options.left === 'number') {
      this.scrollLeft = options.left;
    }
  };
}

if (typeof window.ResizeObserver === 'undefined') {
  class ResizeObserverMock {
    observe() {}

    unobserve() {}

    disconnect() {}
  }

  window.ResizeObserver = ResizeObserverMock as typeof ResizeObserver;
  globalThis.ResizeObserver = ResizeObserverMock as typeof ResizeObserver;
}
