import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { createRequestClient } from "../createClient";
import type { TokenStorage } from "../src/request-client/types";
import { RequestQueue } from "../src/request-client/requestQueue";
import { getNativeStorage } from "../index.native";

type AdapterResult = Pick<AxiosResponse, "status" | "statusText" | "headers" | "data">;

const createResponse = (
  config: InternalAxiosRequestConfig,
  result: AdapterResult
): AxiosResponse => ({
  config,
  status: result.status,
  statusText: result.statusText,
  headers: result.headers,
  data: result.data
});

const createMemoryStorage = (tokens: { access?: string | null; refresh?: string | null } = {}): TokenStorage => {
  let accessToken = tokens.access ?? null;
  let refreshToken = tokens.refresh ?? null;

  return {
    async getAccessToken() {
      return accessToken;
    },
    async setAccessToken(token: string) {
      accessToken = token;
    },
    async getRefreshToken() {
      return refreshToken;
    },
    async setRefreshToken(token: string) {
      refreshToken = token;
    },
    async clearTokens() {
      accessToken = null;
      refreshToken = null;
    }
  };
};

const createDeferred = <T>() => {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: any) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
};

describe("createRequestClient", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("在请求前自动附加 access token", async () => {
    const storage = createMemoryStorage({ access: "access-token" });

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn()
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) =>
      createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: {
          auth: config.headers?.Authorization
        }
      })
    );

    client.defaults.adapter = adapter;

    const data: any = await client.get("/hello");

    expect(data.auth).toBe("Bearer access-token");
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it("请求队列在刷新完成后会依次处理等待中的请求", async () => {
    const queue = new RequestQueue();

    const order: string[] = [];
    const p1 = new Promise<void>((resolve, reject) => {
      queue.enqueue({
        resolve: async () => {
          order.push("first");
          resolve();
        },
        reject,
        config: {} as InternalAxiosRequestConfig
      });
    });

    const p2 = new Promise<void>((resolve, reject) => {
      queue.enqueue({
        resolve: async () => {
          order.push("second");
          resolve();
        },
        reject,
        config: {} as InternalAxiosRequestConfig
      });
    });

    const refreshPromise = queue.startRefresh();
    expect(queue.getIsRefreshing()).toBe(true);

    await queue.finishRefresh();
    await Promise.all([refreshPromise, p1, p2]);

    expect(queue.getIsRefreshing()).toBe(false);
    expect(order).toEqual(["first", "second"]);
  });

  it("刷新失败时会拒绝队列中的请求并重置状态", async () => {
    const queue = new RequestQueue();
    const err = new Error("refresh failed");

    const rejectSpy = vi.fn();

    queue.enqueue({
      resolve: vi.fn(),
      reject: rejectSpy,
      config: {} as InternalAxiosRequestConfig
    });

    const refreshPromise = queue.startRefresh();

    await expect(queue.finishRefreshWithError(err)).resolves.toBeUndefined();
    await expect(refreshPromise).rejects.toThrow(err);
    expect(queue.getIsRefreshing()).toBe(false);
    expect(rejectSpy).toHaveBeenCalledWith(err);
  });

  it("支持自定义请求/响应拦截器并可返回自定义结果", async () => {
    const storage = createMemoryStorage({ access: "token-123" });

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      returnDataOnly: false,
      requestInterceptor: async (config) => {
        config.headers = {
          ...(config.headers as any),
          "X-Test": "yes"
        } as any;
        return config;
      },
      responseInterceptor: {
        onFulfilled: (response) => {
          return { ok: true, status: response.status, echoed: response.data };
        }
      }
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) =>
      createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: { receivedHeader: config.headers?.["X-Test"] }
      })
    );

    client.defaults.adapter = adapter;

    const result = await client.post("/echo", { hello: "world" });

    expect(result).toEqual({
      ok: true,
      status: 200,
      echoed: { receivedHeader: "yes" }
    });
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it("错误时调用 errorHandler 并返回脱敏的错误结构", async () => {
    const storage = createMemoryStorage({ access: "token-err" });
    const errorHandler = vi.fn();

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      errorHandler
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = createResponse(config, {
        status: 500,
        statusText: "Server Error",
        headers: {},
        data: { message: "boom" }
      });
      return Promise.reject(new AxiosError("Server error", undefined, config, undefined, response));
    });

    client.defaults.adapter = adapter;

    const err = await client.get("/fail").catch((e) => e);

    expect(errorHandler).toHaveBeenCalledTimes(1);
    expect(err.status).toBe(500);
    expect(err.data).toEqual({ message: "boom" });
    expect(err.config).toMatchObject({
      url: "/fail",
      baseURL: "/api",
      method: "get"
    });
    expect(err.config.headers).toBeUndefined();
  });

  it("WebTokenStorage 支持读写与清空 token", async () => {
    const store = new Map<string, string>();
    (globalThis as any).localStorage = {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        store.delete(key);
      }
    };

    const { WebTokenStorage } = await import("../src/request-client/tokenStorage");
    const storage = new WebTokenStorage();

    await storage.setAccessToken("a1");
    await storage.setRefreshToken("r1");

    expect(await storage.getAccessToken()).toBe("a1");
    expect(await storage.getRefreshToken()).toBe("r1");

    await storage.clearTokens();

    expect(await storage.getAccessToken()).toBeNull();
    expect(await storage.getRefreshToken()).toBeNull();

    delete (globalThis as any).localStorage;
  });

  it("NativeTokenStorage 懒加载存储并读写 token", async () => {
    vi.resetModules();

    const nativeStore = {
      getItem: vi.fn(async (key: string) => key === "access_token" ? "ax" : "rx"),
      setItem: vi.fn(async (_key: string, _value: string) => {}),
      removeItem: vi.fn(async (_key: string) => {})
    };

    vi.doMock("../src/storage/nativeStorage", () => ({ default: nativeStore }));

    const { NativeTokenStorage } = await import("../src/request-client/tokenStorage");
    const storage = new NativeTokenStorage();

    expect(await storage.getAccessToken()).toBe("ax");
    expect(await storage.getRefreshToken()).toBe("rx");

    await storage.setAccessToken("a2");
    await storage.setRefreshToken("r2");

    expect(nativeStore.setItem).toHaveBeenCalledWith("access_token", "a2");
    expect(nativeStore.setItem).toHaveBeenCalledWith("refresh_token", "r2");

    await storage.clearTokens();
    expect(nativeStore.removeItem).toHaveBeenCalledWith("access_token");
    expect(nativeStore.removeItem).toHaveBeenCalledWith("refresh_token");
  });

  it("RequestQueue 清空后会拒绝挂起刷新并重置状态", async () => {
    const queue = new RequestQueue();

    const refreshPromise = queue.startRefresh();
    queue.clear();

    expect(queue.getIsRefreshing()).toBe(false);
    await expect(refreshPromise).rejects.toThrow("Request queue cleared");
  });

  it("getNativeStorage 返回动态导入的存储实现", async () => {
    vi.resetModules();

    const mockedStorage = { getItem: vi.fn(), setItem: vi.fn(), removeItem: vi.fn() };
    vi.doMock("../src/storage/nativeStorage", () => ({ default: mockedStorage }));

    const storage = await getNativeStorage();
    expect(storage).toBe(mockedStorage);
  });

  it("日志开启时会安全序列化请求/响应并记录错误", async () => {
    const storage = createMemoryStorage({ access: "log-access" });
    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      logEnabled: true
    });

    const troublesome = { nested: { value: "ok" } }; // 常规对象，关注日志不抛错

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      if (config.url === "/error") {
        const response = createResponse(config, {
          status: 500,
          statusText: "Server Error",
          headers: {},
          data: { detail: "boom" }
        });
        throw new AxiosError("server exploded", undefined, config, undefined, response);
      }
      return createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: { ok: true }
      });
    });

    client.defaults.adapter = adapter;

    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    await client.request({
      url: "/ok",
      method: "post",
      data: troublesome,
      transformRequest: [(data) => data]
    });
    const logArgs = logSpy.mock.calls.find((args) => String(args[0]).includes("[Request] POST /api/ok"));
    expect(logArgs?.[1]).toBeDefined();

    const err = await client.get("/error").catch((e) => e);
    expect(errorSpy).toHaveBeenCalled();
    expect(err.status).toBe(500);
    expect(err.data).toEqual({ detail: "boom" });

    logSpy.mockRestore();
    errorSpy.mockRestore();
  });

  it("响应错误时会调用自定义 onRejected", async () => {
    const storage = createMemoryStorage({ access: "reject" });
    const onRejected = vi.fn((error: AxiosError) => Promise.reject(error));

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      responseInterceptor: { onRejected }
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = createResponse(config, {
        status: 400,
        statusText: "Bad",
        headers: {},
        data: { message: "bad" }
      });
      throw new AxiosError("bad request", undefined, config, undefined, response);
    });

    client.defaults.adapter = adapter;

    const err = await client.get("/reject").catch((e) => e);
    expect(onRejected).toHaveBeenCalledTimes(1);
    expect(err).toBeInstanceOf(AxiosError);
    expect(err.response?.status).toBe(400);
  });

  it("刷新过程中新的请求会排队并在刷新后使用最新 token", async () => {
    const storage = createMemoryStorage({ access: "old-access", refresh: "refresh-1" });
    const refreshStarted = createDeferred<void>();
    const refreshGate = createDeferred<void>();

    const refreshApi = vi.fn(async (token: string) => {
      expect(token).toBe("refresh-1");
      refreshStarted.resolve();
      await refreshGate.promise;
      return { accessToken: "new-access", refreshToken: "refresh-2" };
    });

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi,
      returnDataOnly: false
    });

    let shouldFail = true;
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const auth = config.headers?.Authorization;
      if (config.url === "/need-refresh" && shouldFail) {
        shouldFail = false;
        const response = createResponse(config, {
          status: 401,
          statusText: "Unauthorized",
          headers: {},
          data: { message: "unauthorized" }
        });
        throw new AxiosError("unauthorized", undefined, config, undefined, response);
      }
      return createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: { auth, url: config.url }
      });
    });

    client.defaults.adapter = adapter;

    const first = client.get("/need-refresh");
    await refreshStarted.promise;

    const queued = client.get("/another");
    refreshGate.resolve();

    const [firstResult, queuedResult] = await Promise.all([first, queued]);
    expect(firstResult.data.auth).toBe("Bearer new-access");
    expect(queuedResult.data.auth).toBe("Bearer new-access");
    expect(adapter).toHaveBeenCalledTimes(3);
    expect(refreshApi).toHaveBeenCalledTimes(1);
  });

  it("刷新失败时会清空 token 并拒绝队列中的请求", async () => {
    const storage: TokenStorage = {
      async getAccessToken() {
        return "old";
      },
      async setAccessToken() {},
      async getRefreshToken() {
        return "refresh-bad";
      },
      async setRefreshToken() {},
      clearTokens: vi.fn(async () => {})
    };

    const refreshStarted = createDeferred<void>();
    const refreshApi = vi.fn(async () => {
      refreshStarted.resolve();
      throw new Error("refresh failed hard");
    });

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = createResponse(config, {
        status: 401,
        statusText: "Unauthorized",
        headers: {},
        data: {}
      });
      throw new AxiosError("unauthorized", undefined, config, undefined, response);
    });

    client.defaults.adapter = adapter;

    const first = client.get("/need-refresh");
    await refreshStarted.promise;
    const queued = client.get("/another");

    const firstErr = await first.catch((e) => e);
    const queuedErr = await queued.catch((e) => e);

    expect(storage.clearTokens).toHaveBeenCalledTimes(1);
    expect(refreshApi).toHaveBeenCalledTimes(1);
    expect(firstErr).toMatchObject({ message: expect.any(String) });
    expect(queuedErr).toMatchObject({ message: expect.any(String) });
  });

  it("startRefresh 在刷新中重复调用会返回同一个 promise", async () => {
    const queue = new RequestQueue();
    const first = queue.startRefresh();
    const second = queue.startRefresh();

    expect(second).toBe(first);

    await queue.finishRefresh();
    await expect(first).resolves.toBeUndefined();
    expect(queue.getIsRefreshing()).toBe(false);
  });

  it("日志模式下对不可序列化与超长参数进行安全截断", async () => {
    const storage = createMemoryStorage({ access: "ax" });
    const bad: any = {
      toJSON() {
        throw new Error("json fail");
      },
      toString() {
        throw new Error("string fail");
      }
    };
    const payload = bad;
    const longParams = bad;

    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      logEnabled: true
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) =>
      createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: { ok: true }
      })
    );
    client.defaults.adapter = adapter;

    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});

    await client.post("/log", payload, { params: longParams, transformRequest: [(d) => d] });

    const call = logSpy.mock.calls.find((args) => String(args[0]).includes("[Request] POST /api/log"));
    expect(call?.[1]).toMatchObject({
      params: "[Unserializable]",
      data: "[Unserializable]"
    });

    logSpy.mockRestore();
  });

  it("请求拦截器错误分支会记录错误日志并拒绝", async () => {
    const storage = createMemoryStorage({ access: "ax" });
    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      logEnabled: true
    });

    const handler = (client.interceptors.request as any).handlers[0];
    const err = new AxiosError("interceptor failed");
    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const result = handler.rejected(err).catch((e: any) => e);

    await expect(result).resolves.toBe(err);
    expect(consoleSpy).toHaveBeenCalled();
    consoleSpy.mockRestore();
  });

  it("刷新占用时仍会执行自定义 requestInterceptor 后入队", async () => {
    vi.resetModules();
    const queued: any[] = [];
    vi.doMock("../src/request-client/requestQueue", () => {
      class MockQueue {
        getIsRefreshing() {
          return true;
        }
        enqueue(item: any) {
          queued.push(item);
        }
        startRefresh() {
          return Promise.resolve();
        }
        finishRefresh() {
          return Promise.resolve();
        }
        finishRefreshWithError() {
          return Promise.resolve();
        }
      }
      return { RequestQueue: MockQueue };
    });

    const { createRequestClient: createWithQueue } = await import("../createClient");
    const storage = createMemoryStorage({ access: "ax" });
    const client = createWithQueue({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      requestInterceptor: async (cfg) => {
        cfg.headers = {
          ...(cfg.headers as any),
          "X-Queued": "yes"
        } as any;
        return cfg;
      },
      logEnabled: true
    });

    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) =>
      createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: { queued: config.headers?.["X-Queued"] }
      })
    );
    client.defaults.adapter = adapter;

    const pending = client.get("/queued");
    await Promise.resolve(); // 等待入队
    expect(queued).toHaveLength(1);
    await queued[0].resolve(queued[0].config);
    const result: any = await pending;
    expect(result.queued).toBe("yes");
    expect(adapter).toHaveBeenCalledTimes(1);

    logSpy.mockRestore();
  });

  it("请求拦截时刷新占用会将请求排队并带最新 token", async () => {
    vi.resetModules();
    const queued: any[] = [];
    vi.doMock("../src/request-client/requestQueue", () => {
      class MockQueue {
        constructor() {
          queued.length = 0;
        }
        getIsRefreshing() {
          return true;
        }
        enqueue(item: any) {
          queued.push(item);
        }
        startRefresh() {
          return Promise.resolve();
        }
        finishRefresh() {
          return Promise.resolve();
        }
        finishRefreshWithError() {
          return Promise.resolve();
        }
      }
      return { RequestQueue: MockQueue };
    });

    const storage = {
      getAccessToken: vi.fn(async () => "token-q"),
      setAccessToken: vi.fn(),
      getRefreshToken: vi.fn(async () => "r1"),
      setRefreshToken: vi.fn(),
      clearTokens: vi.fn()
    };

    const { createRequestClient: createWithQueue } = await import("../createClient");
    const client = createWithQueue({
      baseURL: "/api",
      storage: storage as any,
      refreshApi: vi.fn()
    });

    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) =>
      createResponse(config, {
        status: 200,
        statusText: "OK",
        headers: {},
        data: { auth: config.headers?.Authorization }
      })
    );
    client.defaults.adapter = adapter;

    const pending = client.get("/queue");
    await Promise.resolve(); // 等待拦截器入队
    expect(queued).toHaveLength(1);
    await queued[0].resolve(queued[0].config);
    const result: any = await pending;
    expect(result.auth).toBe("Bearer token-q");
    expect(adapter).toHaveBeenCalledTimes(1);
  });

  it("错误日志包含字符串响应体时也能脱敏返回", async () => {
    const storage = createMemoryStorage({ access: "err" });
    const client = createRequestClient({
      baseURL: "/api",
      storage,
      refreshApi: vi.fn(),
      logEnabled: true
    });

    const logSpy = vi.spyOn(console, "log").mockImplementation(() => {});
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => {
      const response = createResponse(config, {
        status: 500,
        statusText: "Server Error",
        headers: {},
        data: "plain-error"
      });
      throw new AxiosError("server err", undefined, config, undefined, response);
    });
    client.defaults.adapter = adapter;

    const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const err = await client.get("/plain").catch((e) => e);
    expect(consoleSpy).toHaveBeenCalled();
    expect(err.status).toBe(500);
    logSpy.mockRestore();
    consoleSpy.mockRestore();
  });

  describe("刷新拦截分支覆盖", () => {
    beforeEach(() => {
      vi.resetModules();
    });

    it("并发刷新时等待既有刷新并传播错误", async () => {
      const mockState: any = {
        isRefreshing: true,
        refreshPromise: (() => {
          const p = Promise.reject(new Error("refresh boom"));
          p.catch(() => {}); // 吃掉未处理警告
          return p;
        })(),
        finishRefresh: vi.fn(),
        finishRefreshWithError: vi.fn()
      };

      vi.doMock("../src/request-client/requestQueue", () => {
        class MockQueue {
          getIsRefreshing() {
            return mockState.isRefreshing;
          }
          startRefresh() {
            return mockState.refreshPromise;
          }
          finishRefresh() {
            mockState.finishRefresh();
            return Promise.resolve();
          }
          finishRefreshWithError(err: any) {
            mockState.finishRefreshWithError(err);
            return Promise.resolve();
          }
        }
        return { RequestQueue: MockQueue };
      });

      let capturedRefresh: any;
      vi.doMock("axios-auth-refresh", () => ({
        __esModule: true,
        default: (_instance: any, refreshFn: any) => {
          capturedRefresh = refreshFn;
        }
      }));

      const storage = {
        getAccessToken: vi.fn(),
        setAccessToken: vi.fn(),
        getRefreshToken: vi.fn(async () => "r1"),
        setRefreshToken: vi.fn(),
        clearTokens: vi.fn()
      };

      const { createRequestClient: mockedCreate } = await import("../createClient");
      mockedCreate({
        baseURL: "/api",
        storage: storage as any,
        refreshApi: vi.fn()
      });

      const failed = new AxiosError("unauth", undefined, { headers: {} } as any);
      await expect(capturedRefresh(failed)).rejects.toThrow("refresh boom");
      expect(mockState.finishRefresh).not.toHaveBeenCalled();
    });

    it("无 refresh token 时抛出错误并清空存储", async () => {
      const mockState: any = {
        isRefreshing: false,
        refreshPromise: Promise.resolve(),
        finishRefresh: vi.fn(),
        finishRefreshWithError: vi.fn()
      };

      vi.doMock("../src/request-client/requestQueue", () => {
        class MockQueue {
          getIsRefreshing() {
            return mockState.isRefreshing;
          }
          startRefresh() {
            mockState.isRefreshing = true;
            return mockState.refreshPromise;
          }
          finishRefresh() {
            mockState.finishRefresh();
            return Promise.resolve();
          }
          finishRefreshWithError(err: any) {
            mockState.finishRefreshWithError(err);
            return Promise.resolve();
          }
        }
        return { RequestQueue: MockQueue };
      });

      let capturedRefresh: any;
      vi.doMock("axios-auth-refresh", () => ({
        __esModule: true,
        default: (_instance: any, refreshFn: any) => {
          capturedRefresh = refreshFn;
        }
      }));

      const storage = {
        getAccessToken: vi.fn(),
        setAccessToken: vi.fn(),
        getRefreshToken: vi.fn(async () => null),
        setRefreshToken: vi.fn(),
        clearTokens: vi.fn()
      };

      const { createRequestClient: mockedCreate } = await import("../createClient");
      mockedCreate({
        baseURL: "/api",
        storage: storage as any,
        refreshApi: vi.fn()
      });

      const failed = new AxiosError("unauth", undefined, { headers: {} } as any);
      await expect(capturedRefresh(failed)).rejects.toThrow("No refresh token available");
      expect(storage.clearTokens).toHaveBeenCalledTimes(1);
      expect(mockState.finishRefreshWithError).toHaveBeenCalled();
    });

    it("已有刷新进行时复用 refreshPromise 并写入新 token", async () => {
      const mockState: any = {
        isRefreshing: true,
        refreshPromise: Promise.resolve(),
        finishRefresh: vi.fn(),
        finishRefreshWithError: vi.fn()
      };

      vi.doMock("../src/request-client/requestQueue", () => {
        class MockQueue {
          getIsRefreshing() {
            return mockState.isRefreshing;
          }
          startRefresh() {
            return mockState.refreshPromise;
          }
          enqueue() {
            throw new Error("should not enqueue");
          }
          finishRefresh() {
            mockState.finishRefresh();
            return Promise.resolve();
          }
          finishRefreshWithError(err: any) {
            mockState.finishRefreshWithError(err);
            return Promise.resolve();
          }
        }
        return { RequestQueue: MockQueue };
      });

      let capturedRefresh: any;
      vi.doMock("axios-auth-refresh", () => ({
        __esModule: true,
        default: (_instance: any, refreshFn: any) => {
          capturedRefresh = refreshFn;
        }
      }));

      const storage = {
        getAccessToken: vi.fn(async () => "new-token"),
        setAccessToken: vi.fn(),
        getRefreshToken: vi.fn(),
        setRefreshToken: vi.fn(),
        clearTokens: vi.fn()
      };

      const { createRequestClient: mockedCreate } = await import("../createClient");
      mockedCreate({
        baseURL: "/api",
        storage: storage as any,
        refreshApi: vi.fn()
      });

      const failed = new AxiosError("unauth", undefined, { headers: {} } as any);
      await expect(capturedRefresh(failed)).resolves.toBeUndefined();
      expect(failed.config?.headers?.Authorization).toBe("Bearer new-token");
      expect(storage.getAccessToken).toHaveBeenCalled();
      expect(mockState.finishRefresh).not.toHaveBeenCalled();
    });
  });
});
