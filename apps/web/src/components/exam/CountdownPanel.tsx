"use client";

import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { TimerDeadline } from "@/state/examStore";

// Renders the server-authoritative Part 2 timers (Spec 02 §3.3, Spec 04 §2
// Phase 8) — `timerDeadline.deadlineEpochMs` comes from
// exam_orchestrator.py's `_push_timer_deadline`/`_repush_live_deadline`, so
// a reconnected client gets the *real* remaining time instead of guessing.
// This component only ticks a local display against that deadline; it
// never invents or extends the deadline itself (CLAUDE.md rule 1 — the
// hard cutoff is enforced server-side regardless of what this renders).
const ANNOUNCE_BOUNDARIES_S = [60, 30, 10, 5, 4, 3, 2, 1, 0];

const TIMER_LABELS: Record<string, string> = {
  part2_prep: "Preparation time remaining",
  part2_long_turn: "Speaking time remaining",
};

const RING_RADIUS = 30;
const RING_CIRCUMFERENCE = 2 * Math.PI * RING_RADIUS;

interface CountdownPanelProps {
  timerDeadline: TimerDeadline;
}

export function CountdownPanel({ timerDeadline }: CountdownPanelProps) {
  const [remainingMs, setRemainingMs] = useState(
    () => timerDeadline.deadlineEpochMs - Date.now()
  );
  const [announcement, setAnnouncement] = useState("");
  const lastAnnouncedRef = useRef<number | null>(null);
  // Decorative-only baseline for the progress ring: the first observed
  // remaining duration for this deadline. Purely visual — never influences
  // the authoritative countdown math above.
  const totalMsRef = useRef(Math.max(1, timerDeadline.deadlineEpochMs - Date.now()));

  useEffect(() => {
    lastAnnouncedRef.current = null;
    totalMsRef.current = Math.max(1, timerDeadline.deadlineEpochMs - Date.now());

    const tick = () => {
      const remaining = Math.max(0, timerDeadline.deadlineEpochMs - Date.now());
      setRemainingMs(remaining);

      const remainingSeconds = Math.ceil(remaining / 1000);
      // Continuous live-region updates are a known screen-reader
      // anti-pattern — only announce at meaningful boundaries, not every
      // tick (WCAG 4.1.3 status-messages guidance).
      if (
        ANNOUNCE_BOUNDARIES_S.includes(remainingSeconds) &&
        lastAnnouncedRef.current !== remainingSeconds
      ) {
        lastAnnouncedRef.current = remainingSeconds;
        setAnnouncement(
          remainingSeconds === 0 ? "Time's up" : `${remainingSeconds} seconds remaining`
        );
      }
    };

    tick();
    const interval = setInterval(tick, 250);
    return () => clearInterval(interval);
  }, [timerDeadline.deadlineEpochMs, timerDeadline.name]);

  const remainingSeconds = Math.ceil(remainingMs / 1000);
  const label = TIMER_LABELS[timerDeadline.name] ?? "Time remaining";
  const progress = Math.min(1, Math.max(0, remainingMs / totalMsRef.current));
  const urgent = remainingSeconds <= 10;

  return (
    <motion.div
      role="timer"
      aria-label={label}
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className="flex items-center gap-4 rounded-2xl border border-border bg-surface-raised p-5 shadow-lg shadow-black/5"
    >
      <svg width="72" height="72" viewBox="0 0 72 72" aria-hidden="true" className="shrink-0 -rotate-90">
        <circle cx="36" cy="36" r={RING_RADIUS} fill="none" stroke="var(--gridline)" strokeWidth="5" />
        <circle
          cx="36"
          cy="36"
          r={RING_RADIUS}
          fill="none"
          stroke={urgent ? "var(--status-critical)" : "var(--accent-blue)"}
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={RING_CIRCUMFERENCE}
          strokeDashoffset={RING_CIRCUMFERENCE * (1 - progress)}
          style={{ transition: "stroke-dashoffset 0.25s linear, stroke 0.3s ease" }}
        />
      </svg>
      <div>
        <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">{label}</p>
        <p
          aria-hidden="true"
          className={cn(
            "font-semibold tabular-nums transition-colors",
            urgent ? "text-accent-red" : "text-ink",
            "text-3xl"
          )}
        >
          {formatSeconds(remainingSeconds)}
        </p>
      </div>
      <span className="sr-only" aria-live="polite">
        {announcement}
      </span>
    </motion.div>
  );
}

function formatSeconds(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}
