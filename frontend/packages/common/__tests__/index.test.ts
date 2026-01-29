import { describe, it, expect } from "vitest";
import { noop } from "../index";
import type { ApiResponse } from "../index";

describe("Common utilities", () => {
  describe("noop", () => {
    it("is a function", () => {
      expect(typeof noop).toBe("function");
    });

    it("returns undefined", () => {
      expect(noop()).toBeUndefined();
    });

    it("accepts any arguments without error", () => {
      expect(() => noop(1, 2, 3, "test", {}, [])).not.toThrow();
    });
  });

  describe("ApiResponse type", () => {
    it("allows valid response structure", () => {
      const response: ApiResponse<string> = {
        code: 200,
        message: "Success",
        data: "test data"
      };

      expect(response.code).toBe(200);
      expect(response.message).toBe("Success");
      expect(response.data).toBe("test data");
    });

    it("allows partial response structure", () => {
      const response: ApiResponse = {
        code: 200
      };

      expect(response.code).toBe(200);
      expect(response.message).toBeUndefined();
      expect(response.data).toBeUndefined();
    });

    it("allows empty response", () => {
      const response: ApiResponse = {};
      expect(response).toEqual({});
    });

    it("supports generic types", () => {
      interface User {
        id: number;
        name: string;
      }

      const response: ApiResponse<User> = {
        code: 200,
        message: "User retrieved",
        data: {
          id: 1,
          name: "John Doe"
        }
      };

      expect(response.data?.id).toBe(1);
      expect(response.data?.name).toBe("John Doe");
    });
  });
});
