import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import "@testing-library/jest-dom";
import { Button } from "../src/Button";

describe("Button", () => {
  describe("Rendering", () => {
    it("renders with default props", () => {
      render(<Button>Click me</Button>);
      const button = screen.getByRole("button", { name: /click me/i });
      expect(button).toBeInTheDocument();
      expect(button).toHaveClass("btn", "btn-primary", "btn-md");
    });

    it("renders with label prop when no children provided", () => {
      render(<Button label="Submit" />);
      expect(screen.getByRole("button", { name: /submit/i })).toBeInTheDocument();
    });

    it("prioritizes children over label prop", () => {
      render(<Button label="Label">Children</Button>);
      expect(screen.getByRole("button", { name: /children/i })).toBeInTheDocument();
      expect(screen.queryByText("Label")).not.toBeInTheDocument();
    });

    it("renders with custom className", () => {
      render(<Button className="custom-class">Button</Button>);
      expect(screen.getByRole("button")).toHaveClass("custom-class");
    });
  });

  describe("Variants", () => {
    it("renders primary variant", () => {
      render(<Button variant="primary">Primary</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-primary");
    });

    it("renders secondary variant", () => {
      render(<Button variant="secondary">Secondary</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-secondary");
    });

    it("renders danger variant", () => {
      render(<Button variant="danger">Danger</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-danger");
    });

    it("renders success variant", () => {
      render(<Button variant="success">Success</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-success");
    });
  });

  describe("Sizes", () => {
    it("renders small size", () => {
      render(<Button size="sm">Small</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-sm");
    });

    it("renders medium size (default)", () => {
      render(<Button size="md">Medium</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-md");
    });

    it("renders large size", () => {
      render(<Button size="lg">Large</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-lg");
    });
  });

  describe("Loading state", () => {
    it("shows spinner when loading", () => {
      render(<Button loading>Loading</Button>);
      const button = screen.getByRole("button");
      expect(button).toHaveClass("btn-loading");
      expect(button.querySelector(".btn-spinner")).toBeInTheDocument();
    });

    it("disables button when loading", () => {
      render(<Button loading>Loading</Button>);
      expect(screen.getByRole("button")).toBeDisabled();
    });

    it("hides icons when loading", () => {
      render(
        <Button loading icon={<span data-testid="left-icon">←</span>}>
          Loading
        </Button>
      );
      expect(screen.queryByTestId("left-icon")).not.toBeInTheDocument();
    });
  });

  describe("Icons", () => {
    it("renders left icon", () => {
      render(
        <Button icon={<span data-testid="left-icon">←</span>}>
          With Icon
        </Button>
      );
      expect(screen.getByTestId("left-icon")).toBeInTheDocument();
    });

    it("renders right icon", () => {
      render(
        <Button iconRight={<span data-testid="right-icon">→</span>}>
          With Icon
        </Button>
      );
      expect(screen.getByTestId("right-icon")).toBeInTheDocument();
    });

    it("renders both left and right icons", () => {
      render(
        <Button
          icon={<span data-testid="left-icon">←</span>}
          iconRight={<span data-testid="right-icon">→</span>}
        >
          Both Icons
        </Button>
      );
      expect(screen.getByTestId("left-icon")).toBeInTheDocument();
      expect(screen.getByTestId("right-icon")).toBeInTheDocument();
    });
  });

  describe("Full width", () => {
    it("applies full width class", () => {
      render(<Button fullWidth>Full Width</Button>);
      expect(screen.getByRole("button")).toHaveClass("btn-full-width");
    });

    it("does not apply full width class by default", () => {
      render(<Button>Normal Width</Button>);
      expect(screen.getByRole("button")).not.toHaveClass("btn-full-width");
    });
  });

  describe("Disabled state", () => {
    it("disables button when disabled prop is true", () => {
      render(<Button disabled>Disabled</Button>);
      expect(screen.getByRole("button")).toBeDisabled();
    });

    it("disables button when loading", () => {
      render(<Button loading>Loading</Button>);
      expect(screen.getByRole("button")).toBeDisabled();
    });
  });

  describe("Events", () => {
    it("calls onClick handler when clicked", async () => {
      const user = userEvent.setup();
      const handleClick = vi.fn();
      render(<Button onClick={handleClick}>Click</Button>);

      await user.click(screen.getByRole("button"));
      expect(handleClick).toHaveBeenCalledTimes(1);
    });

    it("does not call onClick when disabled", async () => {
      const user = userEvent.setup();
      const handleClick = vi.fn();
      render(
        <Button onClick={handleClick} disabled>
          Click
        </Button>
      );

      await user.click(screen.getByRole("button"));
      expect(handleClick).not.toHaveBeenCalled();
    });

    it("does not call onClick when loading", async () => {
      const user = userEvent.setup();
      const handleClick = vi.fn();
      render(
        <Button onClick={handleClick} loading>
          Click
        </Button>
      );

      await user.click(screen.getByRole("button"));
      expect(handleClick).not.toHaveBeenCalled();
    });
  });

  describe("HTML attributes", () => {
    it("forwards HTML button attributes", () => {
      render(
        <Button type="submit" form="myForm">
          Submit
        </Button>
      );
      const button = screen.getByRole("button");
      expect(button).toHaveAttribute("type", "submit");
      expect(button).toHaveAttribute("form", "myForm");
    });

    it("supports aria attributes", () => {
      render(<Button aria-label="Close dialog">×</Button>);
      expect(screen.getByRole("button")).toHaveAttribute("aria-label", "Close dialog");
    });
  });

  describe("Edge cases", () => {
    it("handles empty children gracefully", () => {
      render(<Button>{""}</Button>);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });

    it("handles multiple class combinations", () => {
      render(
        <Button
          variant="danger"
          size="lg"
          fullWidth
          loading
          className="custom"
        >
          Button
        </Button>
      );
      const button = screen.getByRole("button");
      expect(button).toHaveClass(
        "btn",
        "btn-danger",
        "btn-lg",
        "btn-full-width",
        "btn-loading",
        "custom"
      );
    });
  });
});
