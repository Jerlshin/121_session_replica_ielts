import { create } from "zustand";

// Mirrors server-pushed state only (CLAUDE.md rule 1) — the client never
// decides its own connection/turn state, it reflects what the gateway told
// it. Phase 3 adds the actual FSM phase once packages/exam-fsm exists.
export type ConnectionStatus = "idle" | "connecting" | "connected" | "disconnected";

interface ExamState {
  connectionStatus: ConnectionStatus;
  isPttActive: boolean;
  lastTurnId: string | null;
  setConnectionStatus: (status: ConnectionStatus) => void;
  setPttActive: (active: boolean) => void;
  setLastTurnId: (turnId: string | null) => void;
}

export const useExamStore = create<ExamState>((set) => ({
  connectionStatus: "idle",
  isPttActive: false,
  lastTurnId: null,
  setConnectionStatus: (status) => set({ connectionStatus: status }),
  setPttActive: (active) => set({ isPttActive: active }),
  setLastTurnId: (turnId) => set({ lastTurnId: turnId }),
}));
