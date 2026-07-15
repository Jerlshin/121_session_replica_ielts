"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

interface ThemeToggleProps {
  className?: string;
}

export function ThemeToggle({ className }: ThemeToggleProps) {
  const { resolvedTheme, setTheme } = useTheme();
  // Avoids a server/client mismatch: next-themes can't know the real theme
  // until it reads localStorage/media-query client-side, so the first
  // client render must match the server's guess before this can be trusted.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const isDark = mounted && resolvedTheme === "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className={cn(
        "relative flex h-9 w-9 items-center justify-center overflow-hidden rounded-full border border-border",
        "bg-surface-raised text-ink-secondary transition-colors hover:text-ink",
        "hover:border-accent-blue/40 active:scale-95",
        className
      )}
    >
      <AnimatePresence mode="wait" initial={false}>
        <motion.span
          key={isDark ? "moon" : "sun"}
          initial={{ opacity: 0, rotate: -90, scale: 0.6 }}
          animate={{ opacity: 1, rotate: 0, scale: 1 }}
          exit={{ opacity: 0, rotate: 90, scale: 0.6 }}
          transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
          className="flex items-center justify-center"
        >
          {isDark ? <Moon size={16} aria-hidden="true" /> : <Sun size={16} aria-hidden="true" />}
        </motion.span>
      </AnimatePresence>
    </button>
  );
}
