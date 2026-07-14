import { act, render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CountdownPanel } from "./CountdownPanel";

describe("CountdownPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("has no axe violations", async () => {
    vi.useRealTimers();
    render(
      <CountdownPanel timerDeadline={{ name: "part2_prep", deadlineEpochMs: Date.now() + 60_000 }} />
    );
    // "region" (all page content must be in a landmark) is a page-level
    // concern the real exam room page already satisfies by wrapping
    // everything in <main> -- irrelevant noise when testing this component
    // in isolation, so it's disabled here rather than added to this
    // component itself.
    expect(await axe(document.body, { rules: { region: { enabled: false } } })).toHaveNoViolations();
    vi.useFakeTimers();
  });

  it("renders the initial remaining time as MM:SS", () => {
    const now = Date.now();
    vi.setSystemTime(now);
    render(
      <CountdownPanel timerDeadline={{ name: "part2_prep", deadlineEpochMs: now + 65_000 }} />
    );
    expect(screen.getByText("1:05")).toBeInTheDocument();
  });

  it("exposes a timer role labeled by the timer name", () => {
    const now = Date.now();
    vi.setSystemTime(now);
    render(
      <CountdownPanel timerDeadline={{ name: "part2_long_turn", deadlineEpochMs: now + 10_000 }} />
    );
    expect(screen.getByRole("timer", { name: /speaking time remaining/i })).toBeInTheDocument();
  });

  it("only announces at boundary crossings, not on every tick", () => {
    const now = Date.now();
    vi.setSystemTime(now);
    render(
      <CountdownPanel timerDeadline={{ name: "part2_prep", deadlineEpochMs: now + 61_000 }} />
    );

    // Advance from 61s remaining down to 60s -- a real boundary.
    act(() => {
      vi.setSystemTime(now + 1_000);
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText(/60 seconds remaining/i)).toBeInTheDocument();

    // Advance one more second (59s remaining) -- not a boundary, the
    // announcement text should not have changed to a 59s message.
    act(() => {
      vi.setSystemTime(now + 2_000);
      vi.advanceTimersByTime(250);
    });
    expect(screen.queryByText(/59 seconds remaining/i)).not.toBeInTheDocument();
  });

  it("announces time's up at zero", () => {
    const now = Date.now();
    vi.setSystemTime(now);
    render(<CountdownPanel timerDeadline={{ name: "part2_prep", deadlineEpochMs: now + 500 }} />);

    act(() => {
      vi.setSystemTime(now + 1_000);
      vi.advanceTimersByTime(250);
    });
    expect(screen.getByText(/time's up/i)).toBeInTheDocument();
    expect(screen.getByText("0:00")).toBeInTheDocument();
  });
});
