"use client";

import { AnimatePresence, motion } from "framer-motion";
import { Loader2, WifiOff } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ConnectionStatus } from "@/state/examStore";

// Visible + screen-reader-announced connection state (Spec 04 §2 Phase 8 —
// "graceful handling and visible connection-status UI banners for socket
// dropouts"). Only rendered when there's something worth telling the
// candidate; "connected" is the expected steady state and doesn't need a
// permanent banner competing for attention with the exam itself.
const BANNER_COPY: Partial<Record<ConnectionStatus, string>> = {
  connecting: "Connecting to the exam session…",
  reconnecting: "Connection lost — reconnecting automatically…",
  disconnected: "Disconnected from the exam session.",
};

const BANNER_STYLE: Partial<Record<ConnectionStatus, string>> = {
  connecting: "bg-accent-blue/10 text-accent-blue border-accent-blue/25",
  reconnecting: "bg-status-warning/15 text-[#8a5b00] dark:text-status-warning border-status-warning/30",
  disconnected: "bg-status-critical/10 text-status-critical border-status-critical/30",
};

interface ConnectionStatusBannerProps {
  status: ConnectionStatus;
}

export function ConnectionStatusBanner({ status }: ConnectionStatusBannerProps) {
  const message = BANNER_COPY[status];

  return (
    <AnimatePresence mode="wait">
      {message && (
        <motion.div
          key={status}
          role="status"
          aria-live="assertive"
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
          className={cn(
            "flex items-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium",
            BANNER_STYLE[status]
          )}
        >
          {status === "disconnected" ? (
            <WifiOff size={16} aria-hidden="true" className="shrink-0" />
          ) : (
            <Loader2 size={16} aria-hidden="true" className="shrink-0 motion-safe:animate-spin" />
          )}
          {message}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
