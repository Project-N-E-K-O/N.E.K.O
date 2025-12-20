import { describe, it, expect, vi } from "vitest";

describe("nativeStorage 独立测试", () => {
  it("动态导入成功时可读写", async () => {
    vi.resetModules();

    const asyncStorageMock = {
      getItem: vi.fn(async (key: string) => `value-${key}`),
      setItem: vi.fn(async (_key: string, _value: string) => {}),
      removeItem: vi.fn(async (_key: string) => {})
    };

    vi.doMock(
      "@react-native-async-storage/async-storage",
      () => ({ default: asyncStorageMock })
    );

    const nativeStorage = (await import("../src/storage/nativeStorage")).default;

    expect(await nativeStorage.getItem("k1")).toBe("value-k1");
    await nativeStorage.setItem("k2", "v2");
    await nativeStorage.removeItem("k3");

    expect(asyncStorageMock.setItem).toHaveBeenCalledWith("k2", "v2");
    expect(asyncStorageMock.removeItem).toHaveBeenCalledWith("k3");
  });

  it("动态导入失败时抛出明确错误", async () => {
    vi.resetModules();

    vi.doMock(
      "@react-native-async-storage/async-storage",
      () => {
        throw new Error("module missing");
      }
    );

    const nativeStorage = (await import("../src/storage/nativeStorage")).default;

    await expect(nativeStorage.getItem("k")).rejects.toThrow(
      "@react-native-async-storage/async-storage is not available"
    );
  });
});
