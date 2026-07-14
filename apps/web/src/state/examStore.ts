import { create } from "zustand";

// Mirrors server-pushed state only (CLAUDE.md rule 1) — the client never
// decides its own connection/turn state, it reflects what the gateway told
// it. Phase 3 adds the actual FSM phase once packages/exam-fsm exists.
export type ConnectionStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected";

export interface CueCard {
  cueCardId: string;
  topic: string;
  bullets: string[];
}

export interface TimerDeadline {
  name: string;
  deadlineEpochMs: number;
}

interface ExamState {
  connectionStatus: ConnectionStatus;
  isPttActive: boolean;
  lastTurnId: string | null;
  cueCard: CueCard | null;
  timerDeadline: TimerDeadline | null;
  setConnectionStatus: (status: ConnectionStatus) => void;
  setPttActive: (active: boolean) => void;
  setLastTurnId: (turnId: string | null) => void;
  setCueCard: (cueCard: CueCard | null) => void;
  setTimerDeadline: (timerDeadline: TimerDeadline | null) => void;
}

export const useExamStore = create<ExamState>((set) => ({
  connectionStatus: "idle",
  isPttActive: false,
  lastTurnId: null,
  cueCard: null,
  timerDeadline: null,
  setConnectionStatus: (status) => set({ connectionStatus: status }),
  setPttActive: (active) => set({ isPttActive: active }),
  setLastTurnId: (turnId) => set({ lastTurnId: turnId }),
  setCueCard: (cueCard) => set({ cueCard }),
  setTimerDeadline: (timerDeadline) => set({ timerDeadline }),
}));
