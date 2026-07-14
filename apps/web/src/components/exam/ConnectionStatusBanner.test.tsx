import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { ConnectionStatusBanner } from "./ConnectionStatusBanner";

describe("ConnectionStatusBanner", () => {
  it("has no axe violations when a banner is shown", async () => {
    render(<ConnectionStatusBanner status="reconnecting" />);
    expect(await axe(document.body)).toHaveNoViolations();
  });

  it.each([
    ["connecting", /connecting/i],
    ["reconnecting", /reconnecting automatically/i],
    ["disconnected", /disconnected/i],
  ] as const)("shows a status=%s message", (status, expected) => {
    render(<ConnectionStatusBanner status={status} />);
    expect(screen.getByRole("status")).toHaveTextContent(expected);
  });

  it.each(["idle", "connected"] as const)(
    "renders nothing for the steady/unremarkable status=%s",
    (status) => {
      render(<ConnectionStatusBanner status={status} />);
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    }
  );

  it("uses an assertive live region so drops interrupt screen reader output promptly", () => {
    render(<ConnectionStatusBanner status="disconnected" />);
    expect(screen.getByRole("status")).toHaveAttribute("aria-live", "assertive");
  });
});
