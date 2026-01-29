import { describe, it, expect, beforeEach, beforeAll } from "vitest";
import { WebTokenStorage } from "../tokenStorage";

describe("WebTokenStorage", () => {
  let storage: WebTokenStorage;

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
    storage = new WebTokenStorage();
  });

  describe("Access Token", () => {
    it("stores and retrieves access token", async () => {
      await storage.setAccessToken("test-access-token");
      const token = await storage.getAccessToken();
      expect(token).toBe("test-access-token");
    });

    it("returns null when no access token stored", async () => {
      const token = await storage.getAccessToken();
      expect(token).toBeNull();
    });

    it("overwrites existing access token", async () => {
      await storage.setAccessToken("old-token");
      await storage.setAccessToken("new-token");
      const token = await storage.getAccessToken();
      expect(token).toBe("new-token");
    });
  });

  describe("Refresh Token", () => {
    it("stores and retrieves refresh token", async () => {
      await storage.setRefreshToken("test-refresh-token");
      const token = await storage.getRefreshToken();
      expect(token).toBe("test-refresh-token");
    });

    it("returns null when no refresh token stored", async () => {
      const token = await storage.getRefreshToken();
      expect(token).toBeNull();
    });

    it("overwrites existing refresh token", async () => {
      await storage.setRefreshToken("old-token");
      await storage.setRefreshToken("new-token");
      const token = await storage.getRefreshToken();
      expect(token).toBe("new-token");
    });
  });

  describe("clearTokens", () => {
    it("removes both tokens", async () => {
      await storage.setAccessToken("access-token");
      await storage.setRefreshToken("refresh-token");

      await storage.clearTokens();

      const accessToken = await storage.getAccessToken();
      const refreshToken = await storage.getRefreshToken();

      expect(accessToken).toBeNull();
      expect(refreshToken).toBeNull();
    });

    it("works when no tokens are stored", async () => {
      await expect(storage.clearTokens()).resolves.toBeUndefined();
    });
  });

  describe("localStorage integration", () => {
    it("stores tokens in localStorage", async () => {
      await storage.setAccessToken("test-token");
      expect((globalThis as any).localStorage.getItem("access_token")).toBe("test-token");
    });

    it("retrieves tokens from localStorage", async () => {
      (globalThis as any).localStorage.setItem("access_token", "direct-token");
      const token = await storage.getAccessToken();
      expect(token).toBe("direct-token");
    });
  });
});
