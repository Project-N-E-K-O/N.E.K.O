import { afterEach, describe, it, expect, vi } from "vitest";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("请求库入口与存储实现", () => {
  it("index.web 导出默认实例与工具", async () => {
    const { request, createRequestClient, WebTokenStorage } = await import("../index.web");

    expect(request).toBeDefined();
    expect(createRequestClient).toBeInstanceOf(Function);
    expect(new WebTokenStorage()).toBeInstanceOf(WebTokenStorage);
  });

  it("index.native 提供用于 RN 的工厂函数", async () => {
    const { createNativeRequestClient, NativeTokenStorage } = await import("../index.native");

    const client = createNativeRequestClient({
      baseURL: "/rn",
      refreshApi: vi.fn()
    });

    expect(client.defaults.baseURL).toBe("/rn");
    expect(new NativeTokenStorage()).toBeInstanceOf(NativeTokenStorage);
  });

  it("storage/index.native 在非 web 环境选择原生存储", async () => {
    vi.resetModules();
    vi.doMock("react-native", () => ({ Platform: { OS: "ios" } }));

    const mockedNative = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() };
    vi.doMock("../src/storage/nativeStorage", () => ({ default: mockedNative }));

    const storageModule = await import("../src/storage/index.native");
    expect(storageModule.default).toBe(mockedNative);
  });

  it("storage/index.native 在 web 环境回退到 webStorage", async () => {
    vi.resetModules();
    vi.doMock("react-native", () => ({ Platform: { OS: "web" } }));

    const mockedWeb = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() };
    vi.doMock("../src/storage/webStorage", () => ({ default: mockedWeb }));

    const storageModule = await import("../src/storage/index.native");
    expect(storageModule.default).toBe(mockedWeb);
  });

  it("storage/index.web 直接导出 webStorage", async () => {
    vi.resetModules();
    const storageModule = await import("../src/storage/index.web");
    expect(typeof storageModule.default.getItem).toBe("function");
    expect(typeof storageModule.default.setItem).toBe("function");
  });

  it("默认 request 实例在 401 时会调用 refreshApi 并重试", async () => {
    vi.resetModules();
    const captured: { refreshApi?: (token: string) => Promise<any> } = {};
    vi.doMock("../createClient", () => {
      return {
        createRequestClient: vi.fn((options) => {
          captured.refreshApi = options.refreshApi;
          return { defaults: {} };
        })
      };
    });

    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        access_token: "a-2",
        refresh_token: "r-2"
      })
    }));
    (globalThis as any).fetch = fetchMock;

    await import("../index.web");
    const tokens = await captured.refreshApi?.("r-1");

    expect(tokens).toEqual({
      accessToken: "a-2",
      refreshToken: "r-2"
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit | undefined])?.[1];
    const fetchBody = JSON.parse((init?.body as string) ?? "{}");
    expect(fetchBody.refreshToken).toBe("r-1");

    delete (globalThis as any).fetch;
  });

  it("refreshApi 在响应非 2xx 时抛出详细错误", async () => {
    vi.resetModules();
    const captured: { refreshApi?: (token: string) => Promise<any> } = {};
    vi.doMock("../createClient", () => ({
      createRequestClient: vi.fn((options) => {
        captured.refreshApi = options.refreshApi;
        return { defaults: {} };
      })
    }));

    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 500,
      statusText: "Server",
      json: async () => ({ message: "boom" })
    }));
    (globalThis as any).fetch = fetchMock;

    await import("../index.web");
    await expect(captured.refreshApi?.("r-1")).rejects.toThrow("boom");
    expect(fetchMock).toHaveBeenCalled();

    delete (globalThis as any).fetch;
  });

  it("refreshApi 在解析失败时抛出解析错误", async () => {
    vi.resetModules();
    const captured: { refreshApi?: (token: string) => Promise<any> } = {};
    vi.doMock("../createClient", () => ({
      createRequestClient: vi.fn((options) => {
        captured.refreshApi = options.refreshApi;
        return { defaults: {} };
      })
    }));

    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => {
        throw new Error("parse failed");
      }
    }));
    (globalThis as any).fetch = fetchMock;

    await import("../index.web");
    await expect(captured.refreshApi?.("r-x")).rejects.toThrow("parse failed");

    delete (globalThis as any).fetch;
  });

  it("refreshApi 缺少 token 字段时抛出提示错误", async () => {
    vi.resetModules();
    const captured: { refreshApi?: (token: string) => Promise<any> } = {};
    vi.doMock("../createClient", () => ({
      createRequestClient: vi.fn((options) => {
        captured.refreshApi = options.refreshApi;
        return { defaults: {} };
      })
    }));

    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({ access_token: null, refresh_token: null })
    }));
    (globalThis as any).fetch = fetchMock;

    await import("../index.web");
    await expect(captured.refreshApi?.("r-x")).rejects.toThrow(
      "Refresh token response is missing access_token or refresh_token"
    );

    delete (globalThis as any).fetch;
  });

});
