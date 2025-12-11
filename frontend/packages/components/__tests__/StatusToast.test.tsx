import React, { useRef, useEffect } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, waitFor, act } from "@testing-library/react";
import "@testing-library/jest-dom";
import { StatusToast } from "../src/StatusToast";
import type { StatusToastHandle } from "../src/StatusToast";

function StatusToastWrapper({ onMount }: { onMount: (handle: StatusToastHandle) => void }) {
  const toastRef = useRef<StatusToastHandle>(null);

  useEffect(() => {
    if (toastRef.current) {
      onMount(toastRef.current);
    }
  }, [onMount]);

  return <StatusToast ref={toastRef} staticBaseUrl="http://localhost:8080" />;
}

describe("StatusToast", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    const existing = document.getElementById("status-toast-container");
    if (existing) {
      existing.remove();
    }
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  describe("Rendering", () => {
    it("renders without crashing", () => {
      const handleMount = vi.fn();
      render(<StatusToastWrapper onMount={handleMount} />);
      expect(handleMount).toHaveBeenCalled();
    });

    it("creates portal container on mount", () => {
      render(<StatusToastWrapper onMount={() => {}} />);
      const container = document.getElementById("status-toast-container");
      expect(container).toBeInTheDocument();
    });

    it("does not show toast initially", () => {
      render(<StatusToastWrapper onMount={() => {}} />);
      const toast = document.getElementById("status-toast");
      expect(toast).toBeInTheDocument();
      expect(toast).not.toHaveClass("show");
    });
  });

  describe("Show method", () => {
    it("shows toast with message", async () => {
      let handle: StatusToastHandle | null = null;
      render(<StatusToastWrapper onMount={(h) => { handle = h; }} />);

      act(() => {
        handle?.show("Test message");
      });

      const toast = document.getElementById("status-toast");
      expect(toast).toHaveTextContent("Test message");
      expect(toast).toHaveClass("show");
    });

    it("hides toast after duration", async () => {
      let handle: StatusToastHandle | null = null;
      render(<StatusToastWrapper onMount={(h) => { handle = h; }} />);

      act(() => {
        handle?.show("Test message", 1000);
      });

      const toast = document.getElementById("status-toast");
      expect(toast).toHaveClass("show");

      act(() => {
        vi.advanceTimersByTime(1300);
      });

      expect(toast).not.toHaveClass("show");
      expect(toast?.textContent).toBe("");
    });

    it("handles empty message", async () => {
      let handle: StatusToastHandle | null = null;
      render(<StatusToastWrapper onMount={(h) => { handle = h; }} />);

      act(() => {
        handle?.show("");
      });

      const toast = document.getElementById("status-toast");
      expect(toast).not.toHaveClass("show");
    });
  });
});
