"use client";

import { useEffect, useRef } from "react";

import type { CueCard } from "@/state/examStore";

// Renders the server-pushed cue_card message (Spec 02 §3, Spec 04 §2 Phase
// 8) — the card is a deterministic record the backend selected, never
// invented client-side (CLAUDE.md rule 1). Semantic heading + list rather
// than styled <div>s so screen readers get real structure, and the panel
// moves focus to itself + is announced via aria-live on arrival (WCAG
// 4.1.3) since it appears mid-session without any user-initiated action.
interface CueCardPanelProps {
  cueCard: CueCard;
}

export function CueCardPanel({ cueCard }: CueCardPanelProps) {
  const panelRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    panelRef.current?.focus();
  }, [cueCard.cueCardId]);

  return (
    <section
      ref={panelRef}
      tabIndex={-1}
      aria-live="polite"
      aria-label="Part 2 cue card"
      style={{
        border: "2px solid #2563eb",
        borderRadius: "0.5rem",
        padding: "1.25rem",
        outline: "none",
      }}
    >
      <h2 style={{ marginTop: 0 }}>{cueCard.topic}</h2>
      <ul>
        {cueCard.bullets.map((bullet, index) => (
          <li key={index}>{bullet}</li>
        ))}
      </ul>
    </section>
  );
}
