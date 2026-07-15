"use client";

import { motion } from "framer-motion";
import { NotebookPen } from "lucide-react";
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
    <motion.section
      ref={panelRef}
      tabIndex={-1}
      aria-live="polite"
      aria-label="Part 2 cue card"
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="rounded-2xl border border-accent-blue/25 bg-surface-raised p-6 shadow-lg shadow-accent-blue/5 outline-none"
    >
      <div className="mb-3 flex items-center gap-2 text-accent-blue">
        <NotebookPen size={18} aria-hidden="true" />
        <span className="text-xs font-semibold uppercase tracking-wide">Cue card</span>
      </div>
      <h2 className="text-lg font-semibold leading-snug text-ink">{cueCard.topic}</h2>
      <ul className="mt-4 space-y-2">
        {cueCard.bullets.map((bullet, index) => (
          <motion.li
            key={index}
            initial={{ opacity: 0, x: -6 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, delay: 0.1 + index * 0.06, ease: [0.16, 1, 0.3, 1] }}
            className="flex items-start gap-2.5 text-sm text-ink-secondary"
          >
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-blue/60" />
            {bullet}
          </motion.li>
        ))}
      </ul>
    </motion.section>
  );
}
