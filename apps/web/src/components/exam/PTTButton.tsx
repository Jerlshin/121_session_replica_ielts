"use client";

import { useRef } from "react";
import type { KeyboardEvent } from "react";

// The sole turn-boundary authority (CLAUDE.md rule 2): press/hold/release
// maps directly to activity_start / (frames stream) / activity_end. There
// is no VAD anywhere in this loop to second-guess it. Keyboard support
// (Space/Enter press-and-hold, Spec 04 §2 Phase 8 accessibility pass) is
// added alongside pointer events, not instead of them — both must drive
// the exact same onPress/onRelease pair so there is still only one turn-
// boundary authority, just two input paths into it.
interface PTTButtonProps {
  active: boolean;
  disabled?: boolean;
  onPress: () => void;
  onRelease: () => void;
}

export function PTTButton({ active, disabled, onPress, onRelease }: PTTButtonProps) {
  // Guards against keyboard auto-repeat re-firing onPress on every repeated
  // keydown event while a key is held.
  const keyHeldRef = useRef(false);

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== " " && event.key !== "Enter") return;
    if (event.key === " ") event.preventDefault(); // stop the page from scrolling
    if (keyHeldRef.current) return;
    keyHeldRef.current = true;
    onPress();
  };

  const handleKeyUp = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== " " && event.key !== "Enter") return;
    keyHeldRef.current = false;
    onRelease();
  };

  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={active}
      aria-label={active ? "Recording — release to stop speaking" : "Hold to speak"}
      onPointerDown={onPress}
      onPointerUp={onRelease}
      onPointerLeave={() => active && onRelease()}
      onKeyDown={handleKeyDown}
      onKeyUp={handleKeyUp}
      style={{
        padding: "1rem 2rem",
        fontSize: "1.1rem",
        backgroundColor: active ? "#d64545" : "#2563eb",
        color: "white",
        border: "none",
        borderRadius: "999px",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        outlineOffset: "3px",
      }}
    >
      {active ? "Release to stop" : "Hold to speak"}
    </button>
  );
}
