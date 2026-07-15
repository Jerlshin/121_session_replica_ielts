"use client";

import { Mic, Square } from "lucide-react";
import { useRef } from "react";
import type { KeyboardEvent } from "react";

import { cn } from "@/lib/utils";

// The sole turn-boundary authority (CLAUDE.md rule 2): press/hold/release
// maps directly to activity_start / (frames stream) / activity_end. There
// is no VAD anywhere in this loop to second-guess it. Keyboard support
// (Space/Enter press-and-hold, Spec 04 §2 Phase 8 accessibility pass) is
// added alongside pointer events, not instead of them — both must drive
// the exact same onPress/onRelease pair so there is still only one turn-
// boundary authority, just two input paths into it.
//
// The exam page also calls onPress programmatically to auto-arm the mic
// once the examiner finishes speaking (see ExamRoomPage) — this component
// stays unaware of *why* a press happened, it only ever renders the
// active/inactive state it's given and reports presses/releases upward.
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
      aria-label={active ? "Recording — release or tap to stop speaking" : "Hold to speak"}
      onPointerDown={onPress}
      onPointerUp={onRelease}
      onPointerLeave={() => active && onRelease()}
      onKeyDown={handleKeyDown}
      onKeyUp={handleKeyUp}
      className={cn(
        "group relative inline-flex h-16 w-16 items-center justify-center rounded-full outline-offset-4",
        "transition-[transform,background-color,box-shadow] duration-200 ease-out active:scale-95",
        "disabled:cursor-not-allowed disabled:opacity-40",
        active
          ? "bg-accent-red text-white shadow-lg shadow-accent-red/30"
          : "bg-accent-blue text-white shadow-lg shadow-accent-blue/30 hover:brightness-110"
      )}
    >
      {active && (
        <>
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-full bg-accent-red/60 motion-safe:animate-pulse-ring"
          />
          <span
            aria-hidden="true"
            className="absolute inset-0 rounded-full bg-accent-red/60 motion-safe:animate-pulse-ring [animation-delay:0.5s]"
          />
        </>
      )}
      <span className="relative z-10">
        {active ? <Square size={22} fill="currentColor" /> : <Mic size={24} />}
      </span>
    </button>
  );
}
