"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";

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

// Visually hidden but screen-reader-accessible, per the standard "sr-only"
// pattern — no global stylesheet exists yet in this app to put a reusable
// class in, so this is inlined here.
const srOnlyStyle: CSSProperties = {
  position: "absolute",
  width: "1px",
  height: "1px",
  padding: 0,
  margin: "-1px",
  overflow: "hidden",
  clip: "rect(0, 0, 0, 0)",
  whiteSpace: "nowrap",
  border: 0,
};

interface CountdownPanelProps {
  timerDeadline: TimerDeadline;
}

export function CountdownPanel({ timerDeadline }: CountdownPanelProps) {
  const [remainingMs, setRemainingMs] = useState(
    () => timerDeadline.deadlineEpochMs - Date.now()
  );
  const [announcement, setAnnouncement] = useState("");
  const lastAnnouncedRef = useRef<number | null>(null);

  useEffect(() => {
    lastAnnouncedRef.current = null;

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

  return (
    <div role="timer" aria-label={label}>
      <p
        aria-hidden="true"
        style={{ fontSize: "1.5rem", fontVariantNumeric: "tabular-nums", margin: 0 }}
      >
        {formatSeconds(remainingSeconds)}
      </p>
      <span style={srOnlyStyle} aria-live="polite">
        {announcement}
      </span>
    </div>
  );
}

function formatSeconds(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}
