import { fireEvent, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it, vi } from "vitest";

import { PTTButton } from "./PTTButton";

describe("PTTButton", () => {
  it("has no axe violations in either state", async () => {
    const { rerender } = render(
      <PTTButton active={false} onPress={vi.fn()} onRelease={vi.fn()} />
    );
    expect(await axe(document.body)).toHaveNoViolations();

    rerender(<PTTButton active={true} onPress={vi.fn()} onRelease={vi.fn()} />);
    expect(await axe(document.body)).toHaveNoViolations();
  });

  it("exposes aria-pressed and a descriptive aria-label reflecting active state", () => {
    const { rerender } = render(
      <PTTButton active={false} onPress={vi.fn()} onRelease={vi.fn()} />
    );
    const button = screen.getByRole("button");
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button.getAttribute("aria-label")).toMatch(/hold to speak/i);

    rerender(<PTTButton active={true} onPress={vi.fn()} onRelease={vi.fn()} />);
    expect(screen.getByRole("button")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button").getAttribute("aria-label")).toMatch(/release/i);
  });

  it("is keyboard-operable via Space press-and-hold, firing onPress once and onRelease on keyup", () => {
    const onPress = vi.fn();
    const onRelease = vi.fn();
    render(<PTTButton active={false} onPress={onPress} onRelease={onRelease} />);
    const button = screen.getByRole("button");

    fireEvent.keyDown(button, { key: " " });
    // Auto-repeat should not re-fire onPress while the key stays held.
    fireEvent.keyDown(button, { key: " " });
    expect(onPress).toHaveBeenCalledTimes(1);
    expect(onRelease).not.toHaveBeenCalled();

    fireEvent.keyUp(button, { key: " " });
    expect(onRelease).toHaveBeenCalledTimes(1);
  });

  it("is keyboard-operable via Enter as well", () => {
    const onPress = vi.fn();
    const onRelease = vi.fn();
    render(<PTTButton active={false} onPress={onPress} onRelease={onRelease} />);
    const button = screen.getByRole("button");

    fireEvent.keyDown(button, { key: "Enter" });
    expect(onPress).toHaveBeenCalledTimes(1);
    fireEvent.keyUp(button, { key: "Enter" });
    expect(onRelease).toHaveBeenCalledTimes(1);
  });

  it("ignores unrelated keys", () => {
    const onPress = vi.fn();
    render(<PTTButton active={false} onPress={onPress} onRelease={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "a" });
    expect(onPress).not.toHaveBeenCalled();
  });
});
