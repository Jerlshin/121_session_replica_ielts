// Mirrors apps/worker's JudgeOutput/BandScoreReport schema (Spec 03 §5.4,
// §2.2 `band_score_reports`) so this UI is built against the shape the real
// results endpoint will eventually return, not an invented one. There is no
// `/sessions/{id}/report` REST route on the gateway yet (Spec 04 Phase 6-7)
// — src/lib/demoReport.ts fills that gap with fixture data until it exists.
export type CriterionKey =
  | "fluency_coherence"
  | "lexical_resource"
  | "grammatical_range_accuracy"
  | "pronunciation";

export const CRITERION_ORDER: CriterionKey[] = [
  "fluency_coherence",
  "lexical_resource",
  "grammatical_range_accuracy",
  "pronunciation",
];

export const CRITERION_LABELS: Record<CriterionKey, string> = {
  fluency_coherence: "Fluency & Coherence",
  lexical_resource: "Lexical Resource",
  grammatical_range_accuracy: "Grammatical Range & Accuracy",
  pronunciation: "Pronunciation",
};

export const CRITERION_SHORT_LABELS: Record<CriterionKey, string> = {
  fluency_coherence: "Fluency",
  lexical_resource: "Lexical",
  grammatical_range_accuracy: "Grammar",
  pronunciation: "Pronunciation",
};

export interface CriterionScore {
  criterion: CriterionKey;
  band: number; // 0.0–9.0, 0.5 increments
  justification: string; // must name specific feature(s) used, Spec 03 §5.4
  evidenceFeatures: string[]; // e.g. ["MLR=4.2", "filled_pause_rate=9.1/100w"]
  confidence: number; // 0.0–1.0
}

export interface SessionReport {
  sessionId: string;
  candidateDisplayName: string;
  examDate: string; // ISO date
  overallBand: number;
  targetBand: number;
  criterionScores: CriterionScore[];
  flagForHumanReview: boolean;
}

export interface HistoryPoint {
  sessionId: string;
  attemptLabel: string;
  date: string; // ISO date
  overallBand: number;
  criterionBands: Record<CriterionKey, number>;
}

export type Speaker = "candidate" | "examiner";

export interface TranscriptWordFlag {
  criterion: CriterionKey;
  note: string;
}

export interface TranscriptWord {
  word: string;
  startMs: number;
  endMs: number;
  flag?: TranscriptWordFlag;
}

export interface TranscriptTurn {
  turnId: string;
  phase: string;
  speaker: Speaker;
  words: TranscriptWord[];
}
