import type { HTMLAttributes } from "react";

import { cn } from "@/lib/utils";

const VARIANT_CLASSES = {
  neutral: "bg-page text-ink-secondary border-border",
  accent: "bg-accent-blue/10 text-accent-blue border-accent-blue/20",
  good: "bg-status-good/10 text-status-good border-status-good/25",
  warning: "bg-status-warning/15 text-[#8a5b00] dark:text-status-warning border-status-warning/30",
  critical: "bg-status-critical/10 text-status-critical border-status-critical/25",
} as const;

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: keyof typeof VARIANT_CLASSES;
}

export function Badge({ className, variant = "neutral", ...props }: BadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium",
        VARIANT_CLASSES[variant],
        className
      )}
      {...props}
    />
  );
}
