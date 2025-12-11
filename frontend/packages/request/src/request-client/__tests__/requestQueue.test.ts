import { describe, it, expect, vi, beforeEach } from "vitest";
import { RequestQueue } from "../requestQueue";
import type { QueuedRequest } from "../types";

describe("RequestQueue", () => {
  let queue: RequestQueue;

  beforeEach(() => {
    queue = new RequestQueue();
  });

  describe("Initial state", () => {
    it("starts with empty queue", () => {
      expect(queue.getIsRefreshing()).toBe(false);
    });

    it("is not refreshing initially", () => {
      expect(queue.getIsRefreshing()).toBe(false);
    });
  });

  describe("enqueue", () => {
    it("adds request to queue", () => {
      const request: QueuedRequest = {
        resolve: vi.fn(),
        reject: vi.fn(),
        config: {} as any
      };

      queue.enqueue(request);
    });

    it("accepts multiple requests", () => {
      const requests = Array.from({ length: 5 }, () => ({
        resolve: vi.fn(),
        reject: vi.fn(),
        config: {} as any
      }));

      requests.forEach((req) => queue.enqueue(req));
    });
  });

  describe("startRefresh", () => {
    it("sets isRefreshing to true", () => {
      queue.startRefresh();
      expect(queue.getIsRefreshing()).toBe(true);
    });

    it("returns a promise", () => {
      const promise = queue.startRefresh();
      expect(promise).toBeInstanceOf(Promise);
    });

    it("returns same promise for concurrent calls", () => {
      const promise1 = queue.startRefresh();
      const promise2 = queue.startRefresh();
      expect(promise1).toBe(promise2);
    });
  });

  describe("finishRefresh", () => {
    it("sets isRefreshing to false", async () => {
      queue.startRefresh();
      await queue.finishRefresh();
      expect(queue.getIsRefreshing()).toBe(false);
    });

    it("resolves the refresh promise", async () => {
      const refreshPromise = queue.startRefresh();
      await queue.finishRefresh();
      await expect(refreshPromise).resolves.toBeUndefined();
    });

    it("processes queued requests successfully", async () => {
      const request: QueuedRequest = {
        resolve: vi.fn(),
        reject: vi.fn(),
        config: { url: "/test" } as any
      };

      queue.enqueue(request);
      queue.startRefresh();
      await queue.finishRefresh();

      expect(request.resolve).toHaveBeenCalledWith(request.config);
      expect(request.reject).not.toHaveBeenCalled();
    });
  });

  describe("finishRefreshWithError", () => {
    it("sets isRefreshing to false", async () => {
      queue.startRefresh();
      await queue.finishRefreshWithError(new Error("Test error"));
      expect(queue.getIsRefreshing()).toBe(false);
    });

    it("rejects the refresh promise", async () => {
      const refreshPromise = queue.startRefresh();
      const error = new Error("Test error");
      await queue.finishRefreshWithError(error);
      await expect(refreshPromise).rejects.toThrow("Test error");
    });

    it("rejects all queued requests", async () => {
      const requests = Array.from({ length: 3 }, () => ({
        resolve: vi.fn(),
        reject: vi.fn(),
        config: {} as any
      }));

      requests.forEach((req) => queue.enqueue(req));
      queue.startRefresh();

      const error = new Error("Refresh failed");
      await queue.finishRefreshWithError(error);

      requests.forEach((req) => {
        expect(req.reject).toHaveBeenCalledWith(error);
        expect(req.resolve).not.toHaveBeenCalled();
      });
    });
  });

  describe("clear", () => {
    it("clears the queue", () => {
      const request: QueuedRequest = {
        resolve: vi.fn(),
        reject: vi.fn(),
        config: {} as any
      };

      queue.enqueue(request);
      queue.clear();

      queue.processQueue();
      expect(request.resolve).not.toHaveBeenCalled();
    });

    it("resets isRefreshing flag", () => {
      queue.startRefresh();
      queue.clear();
      expect(queue.getIsRefreshing()).toBe(false);
    });

    it("rejects pending refresh promise", async () => {
      const refreshPromise = queue.startRefresh();
      queue.clear();
      await expect(refreshPromise).rejects.toThrow("Request queue cleared");
    });
  });

  describe("Edge cases", () => {
    it("handles empty queue processing", async () => {
      await expect(queue.processQueue()).resolves.toBeUndefined();
    });

    it("handles multiple finishRefresh calls safely", async () => {
      queue.startRefresh();
      await queue.finishRefresh();
      await expect(queue.finishRefresh()).resolves.toBeUndefined();
    });
  });
});
