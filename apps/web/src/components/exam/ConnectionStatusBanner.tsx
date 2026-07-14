"use client";

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

const BANNER_COLOR: Partial<Record<ConnectionStatus, string>> = {
  connecting: "#374151",
  reconnecting: "#92400e",
  disconnected: "#991b1b",
};

interface ConnectionStatusBannerProps {
  status: ConnectionStatus;
}

export function ConnectionStatusBanner({ status }: ConnectionStatusBannerProps) {
  const message = BANNER_COPY[status];
  if (!message) return null;

  return (
    <div
      role="status"
      aria-live="assertive"
      style={{
        padding: "0.75rem 1rem",
        borderRadius: "0.375rem",
        backgroundColor: "#fef3c7",
        color: BANNER_COLOR[status],
        fontWeight: 600,
      }}
    >
      {message}
    </div>
  );
}
