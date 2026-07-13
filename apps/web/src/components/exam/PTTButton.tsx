"use client";

// The sole turn-boundary authority (CLAUDE.md rule 2): press/hold/release
// maps directly to activity_start / (frames stream) / activity_end. There
// is no VAD anywhere in this loop to second-guess it.
interface PTTButtonProps {
  active: boolean;
  disabled?: boolean;
  onPress: () => void;
  onRelease: () => void;
}

export function PTTButton({ active, disabled, onPress, onRelease }: PTTButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      onPointerDown={onPress}
      onPointerUp={onRelease}
      onPointerLeave={() => active && onRelease()}
      style={{
        padding: "1rem 2rem",
        fontSize: "1.1rem",
        backgroundColor: active ? "#d64545" : "#2563eb",
        color: "white",
        border: "none",
        borderRadius: "999px",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
      }}
    >
      {active ? "Release to stop" : "Hold to speak"}
    </button>
  );
}
