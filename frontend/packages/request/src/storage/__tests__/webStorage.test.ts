import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import webStorage from "../webStorage";

describe("webStorage", () => {
  beforeAll(() => {
    if (!(globalThis as any).localStorage) {
      let store: Record<string, string> = {};
      Object.defineProperty(globalThis, "localStorage", {
        value: {
          getItem: (key: string) => store[key] ?? null,
          setItem: (key: string, value: string) => {
            store[key] = value;
          },
          removeItem: (key: string) => {
            delete store[key];
          },
          clear: () => {
            store = {};
          }
        },
        writable: true
      });
    }
  });

  beforeEach(() => {
    (globalThis as any).localStorage.clear();
  });

  describe("getItem", () => {
    it("retrieves existing item", async () => {
      (globalThis as any).localStorage.setItem("test-key", "test-value");
      const value = await webStorage.getItem("test-key");
      expect(value).toBe("test-value");
    });

    it("returns null for non-existent item", async () => {
      const value = await webStorage.getItem("non-existent");
      expect(value).toBeNull();
    });

    it("returns promise", () => {
      const result = webStorage.getItem("test");
      expect(result).toBeInstanceOf(Promise);
    });
  });

  describe("setItem", () => {
    it("stores item in localStorage", async () => {
      await webStorage.setItem("test-key", "test-value");
      expect((globalThis as any).localStorage.getItem("test-key")).toBe("test-value");
    });

    it("overwrites existing item", async () => {
      (globalThis as any).localStorage.setItem("test-key", "old-value");
      await webStorage.setItem("test-key", "new-value");
      expect((globalThis as any).localStorage.getItem("test-key")).toBe("new-value");
    });

    it("returns promise", () => {
      const result = webStorage.setItem("test", "value");
      expect(result).toBeInstanceOf(Promise);
    });
  });

  describe("removeItem", () => {
    it("removes existing item", async () => {
      (globalThis as any).localStorage.setItem("test-key", "test-value");
      await webStorage.removeItem("test-key");
      expect((globalThis as any).localStorage.getItem("test-key")).toBeNull();
    });

    it("handles non-existent item gracefully", async () => {
      await expect(webStorage.removeItem("non-existent")).resolves.toBeUndefined();
    });

    it("returns promise", () => {
      const result = webStorage.removeItem("test");
      expect(result).toBeInstanceOf(Promise);
    });
  });

  describe("Multiple operations", () => {
    it("handles multiple set operations", async () => {
      await webStorage.setItem("key1", "value1");
      await webStorage.setItem("key2", "value2");
      await webStorage.setItem("key3", "value3");

      expect(await webStorage.getItem("key1")).toBe("value1");
      expect(await webStorage.getItem("key2")).toBe("value2");
      expect(await webStorage.getItem("key3")).toBe("value3");
    });

    it("handles set and remove operations", async () => {
      await webStorage.setItem("test", "value");
      expect(await webStorage.getItem("test")).toBe("value");

      await webStorage.removeItem("test");
      expect(await webStorage.getItem("test")).toBeNull();
    });
  });
});
