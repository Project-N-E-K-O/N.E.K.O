import React, { useRef, useEffect } from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom";
import { Modal } from "../index";
import type { ModalHandle } from "../index";

type WindowWithT = Window & { t?: (key: string, params?: Record<string, unknown>) => string };

function ModalWrapper({ onMount }: { onMount: (handle: ModalHandle) => void }) {
  const modalRef = useRef<ModalHandle>(null);

  useEffect(() => {
    if (modalRef.current) {
      onMount(modalRef.current);
    }
  }, [onMount]);

  return <Modal ref={modalRef} />;
}

describe("Modal", () => {
  beforeEach(() => {
    // Mock window.t for i18n
    const win = window as WindowWithT;
    win.t = vi.fn((key: string) => {
      const translations: Record<string, string> = {
        "common.ok": "OK",
        "common.cancel": "Cancel",
        "common.alert": "Alert",
        "common.confirm": "Confirm",
        "common.input": "Input"
      };
      return translations[key] || key;
    });
  });

  describe("Alert Dialog", () => {
    it.skip("shows alert dialog", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      // Debug handle availability
      // console.log("handle", handle);
      expect(handle).not.toBeNull();

      let promise: Promise<boolean>;
      await act(async () => {
        promise = handle!.alert("Test alert message");
      });

      await waitFor(() => {
        expect(screen.getByText("Test alert message")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /OK/i })).toBeInTheDocument();
      });

      await promise;
    });

    it("resolves to true when OK is clicked", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<boolean>;
      await act(async () => {
        promise = handle!.alert("Test message");
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /OK/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole("button", { name: /OK/i }));

      const result = await promise;
      expect(result).toBe(true);
    });

    it("uses custom title", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      await act(async () => {
        handle!.alert("Message", "Custom Title");
      });

      await waitFor(() => {
        expect(screen.getByText("Custom Title")).toBeInTheDocument();
      });
    });

    it("uses default i18n title when no title provided", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      await act(async () => {
        handle!.alert("Message");
      });

      await waitFor(() => {
        expect(screen.getByText("Alert")).toBeInTheDocument();
      });
    });
  });

  describe("Confirm Dialog", () => {
    it.skip("shows confirm dialog", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<boolean>;
      await act(async () => {
        promise = handle!.confirm("Confirm this action?");
      });

      await waitFor(() => {
        expect(screen.getByText("Confirm this action?")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /OK/i })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
      });

      await promise;
    });

    it("resolves to true when OK is clicked", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<boolean>;
      await act(async () => {
        promise = handle!.confirm("Confirm?");
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /OK/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole("button", { name: /OK/i }));

      const result = await promise;
      expect(result).toBe(true);
    });

    it("resolves to false when Cancel is clicked", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<boolean>;
      await act(async () => {
        promise = handle!.confirm("Confirm?");
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole("button", { name: /Cancel/i }));

      const result = await promise;
      expect(result).toBe(false);
    });

    it("supports custom button text", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      await act(async () => {
        handle!.confirm("Message", null, { okText: "Yes", cancelText: "No" });
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Yes/i })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /No/i })).toBeInTheDocument();
      });
    });

    it("supports danger variant", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      await act(async () => {
        handle!.confirm("Delete this item?", null, { danger: true });
      });

      await waitFor(() => {
        const okButton = screen.getByRole("button", { name: /OK/i });
        expect(okButton).toHaveClass("modal-btn-danger");
      });
    });
  });

  describe("Prompt Dialog", () => {
    it.skip("shows prompt dialog with input", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<string | null>;
      await act(async () => {
        promise = handle!.prompt("Enter your name:");
      });

      expect(screen.getByText("Enter your name:")).toBeInTheDocument();
      expect(screen.getByRole("textbox")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /OK/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();

      await promise;
    });

    it("returns entered value when OK is clicked", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<string | null>;
      await act(async () => {
        promise = handle!.prompt("Enter name:");
      });

      await waitFor(() => {
        expect(screen.getByRole("textbox")).toBeInTheDocument();
      });

      const input = screen.getByRole("textbox");
      await user.clear(input);
      await user.type(input, "John Doe");
      await user.click(screen.getByRole("button", { name: /OK/i }));

      const result = await promise;
      expect(result).toBe("John Doe");
    });

    it("returns null when Cancel is clicked", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<string | null>;
      await act(async () => {
        promise = handle!.prompt("Enter name:");
      });

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /Cancel/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole("button", { name: /Cancel/i }));

      const result = await promise;
      expect(result).toBeNull();
    });

    it("uses default value", async () => {
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      await act(async () => {
        handle!.prompt("Enter name:", "Default Name");
      });

      await waitFor(() => {
        const input = screen.getByRole("textbox") as HTMLInputElement;
        expect(input.value).toBe("Default Name");
      });
    });

    it("submits on Enter key", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let promise: Promise<string | null>;
      await act(async () => {
        promise = handle!.prompt("Enter name:");
      });

      await waitFor(() => {
        expect(screen.getByRole("textbox")).toBeInTheDocument();
      });

      const input = screen.getByRole("textbox");
      await user.type(input, "Test Name{Enter}");

      const result = await promise;
      expect(result).toBe("Test Name");
    });
  });

  describe("Dialog cleanup", () => {
    it.skip("resolves pending dialogs on unmount", async () => {
      let handle: ModalHandle | null = null;
      const { unmount } = render(<ModalWrapper onMount={(h) => { handle = h; }} />);
      await act(async () => {});
      expect(handle).not.toBeNull();

      let alertPromise: Promise<boolean>;
      let confirmPromise: Promise<boolean>;
      let promptPromise: Promise<string | null>;
      await act(async () => {
        alertPromise = handle!.alert("Test");
        confirmPromise = handle!.confirm("Test");
        promptPromise = handle!.prompt("Test");
      });

      unmount();

      const alertResult = await alertPromise;
      const confirmResult = await confirmPromise;
      const promptResult = await promptPromise;

      expect(alertResult).toBe(true);
      expect(confirmResult).toBe(false);
      expect(promptResult).toBeNull();
    });
  });

  describe("Multiple sequential dialogs", () => {
    it("handles multiple alerts sequentially", async () => {
      const user = userEvent.setup();
      let handle: ModalHandle | null = null;
      render(<ModalWrapper onMount={(h) => { handle = h; }} />);

      const promise1 = handle!.alert("First alert");

      await waitFor(() => {
        expect(screen.getByText("First alert")).toBeInTheDocument();
      });

      await user.click(screen.getByRole("button", { name: /OK/i }));
      await promise1;

      const promise2 = handle!.alert("Second alert");

      await waitFor(() => {
        expect(screen.getByText("Second alert")).toBeInTheDocument();
      });

      await user.click(screen.getByRole("button", { name: /OK/i }));
      await promise2;
    });
  });
});
