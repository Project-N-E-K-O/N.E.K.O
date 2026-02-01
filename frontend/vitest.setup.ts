import { expect, afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";
import * as matchers from "@testing-library/jest-dom/matchers";

// 将 jest-dom 断言扩展到 Vitest
expect.extend(matchers);

// 每个用例后清理挂载的节点
afterEach(() => {
  cleanup();
});

// Mock window.matchMedia，便于处理媒体查询
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn()
  }))
});

// 简单的 localStorage mock
const localStorageMock = (() => {
  let store: Record<string, string> = {};

  return {
    getItem: (key: string) => store[key] || null,
    setItem: (key: string, value: string) => {
      store[key] = value.toString();
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    }
  };
})();

Object.defineProperty(window, "localStorage", {
  value: localStorageMock,
  writable: true,
  configurable: true
});

// Mock window.t（i18n）
Object.defineProperty(window, "t", {
  writable: true,
  value: vi.fn((key: string, params?: any) => {
    const translations: Record<string, string> = {
      "common.ok": "OK",
      "common.cancel": "Cancel",
      "common.alert": "Alert",
      "common.confirm": "Confirm",
      "common.input": "Input",
      "app.started": `${params?.name || "App"} started`
    };
    return translations[key] || key;
  })
});
