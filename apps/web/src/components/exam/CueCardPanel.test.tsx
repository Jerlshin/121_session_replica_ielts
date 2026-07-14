import { render, screen } from "@testing-library/react";
import { axe } from "jest-axe";
import { describe, expect, it } from "vitest";

import { CueCardPanel } from "./CueCardPanel";

const CUE_CARD = {
  cueCardId: "cc_0142",
  topic: "Describe a skill you learned that you found difficult at first",
  bullets: ["what it was", "why you chose it", "how you felt afterward"],
};

describe("CueCardPanel", () => {
  it("has no axe violations", async () => {
    render(<CueCardPanel cueCard={CUE_CARD} />);
    expect(await axe(document.body)).toHaveNoViolations();
  });

  it("renders the topic as a heading and bullets as a list", () => {
    render(<CueCardPanel cueCard={CUE_CARD} />);
    expect(screen.getByRole("heading", { name: CUE_CARD.topic })).toBeInTheDocument();
    for (const bullet of CUE_CARD.bullets) {
      expect(screen.getByText(bullet)).toBeInTheDocument();
    }
  });

  it("moves focus to itself on mount so screen readers announce it arriving", () => {
    render(<CueCardPanel cueCard={CUE_CARD} />);
    expect(screen.getByRole("region", { name: /part 2 cue card/i })).toHaveFocus();
  });

  it("is wrapped in an aria-live region", () => {
    render(<CueCardPanel cueCard={CUE_CARD} />);
    expect(screen.getByRole("region", { name: /part 2 cue card/i })).toHaveAttribute(
      "aria-live",
      "polite"
    );
  });
});
