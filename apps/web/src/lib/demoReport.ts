// Presentation-layer fixture data — clearly named "demo" throughout, never
// imported outside apps/web/src/app/results. There is no
// `/sessions/{id}/report` endpoint on the gateway yet (grading writes to
// `band_score_reports` in apps/worker/models.py but nothing exposes it over
// REST, Spec 04 Phase 6-7), and no multi-session history endpoint at all.
// This stands in for both so the dashboard UI can be built and exercised
// now; swapping it for a real `getSessionReport()` fetch later is a
// one-file change, the components below only depend on the typed shapes in
// src/types/report.ts.
import type {
  CriterionScore,
  HistoryPoint,
  SessionReport,
  TranscriptTurn,
  TranscriptWord,
  TranscriptWordFlag,
} from "@/types/report";

export function getDemoSessionReport(sessionId: string): SessionReport {
  const criterionScores: CriterionScore[] = [
    {
      criterion: "fluency_coherence",
      band: 6.5,
      justification:
        "Speech is sustained with only occasional repetition, evidenced by a Mean Length of Run of 4.8 words and a filled-pause rate of 7.2 per 100 words. Some noticeable hesitation appears at clause boundaries rather than mid-clause, which keeps the cost to coherence limited.",
      evidenceFeatures: [
        "MLR=4.8 words/run",
        "filled_pause_rate=7.2/100w",
        "articulation_rate=3.6 syll/s",
        "phonation_time_ratio=0.81",
      ],
      confidence: 0.86,
    },
    {
      criterion: "lexical_resource",
      band: 7.0,
      justification:
        "Lexical diversity is strong (MTLD=68.4) with 18% of content words beyond the B2 band, including natural collocational use such as \"practiced consistently.\" Only one lexical appropriacy flag was raised across the session.",
      evidenceFeatures: ["MTLD=68.4", "beyond_B2_ratio=18%", "collocation_errors=1/120w"],
      confidence: 0.82,
    },
    {
      criterion: "grammatical_range_accuracy",
      band: 6.0,
      justification:
        "A mean T-unit length of 9.1 words and a dependent-clause ratio of 0.34 show a reasonable attempt at complex structures, but the error-free-clause ratio of 0.71 is held down by a recurring subject-verb agreement slip (\"I says\").",
      evidenceFeatures: [
        "mean_T-unit_length=9.1",
        "error_free_clause_ratio=0.71",
        "dependent_clause_ratio=0.34",
      ],
      confidence: 0.79,
    },
    {
      criterion: "pronunciation",
      band: 6.5,
      justification:
        "Azure pronunciation assessment returned an accuracy score of 74.2 and a prosody score of 68.0 — individual sounds are generally clear, with some flattening of sentence stress on longer utterances such as the closing clause on \"fluently.\"",
      evidenceFeatures: ["accuracy_score=74.2 (azure)", "prosody_score=68.0 (azure)", "completeness=91%"],
      confidence: 0.88,
    },
  ];

  return {
    sessionId,
    candidateDisplayName: "Alex Chen",
    examDate: new Date().toISOString(),
    overallBand: 6.5,
    targetBand: 7.0,
    criterionScores,
    flagForHumanReview: false,
  };
}

export function getDemoHistory(currentSessionId: string): HistoryPoint[] {
  const today = Date.now();
  const weekMs = 7 * 24 * 60 * 60 * 1000;
  return [
    {
      sessionId: "demo-attempt-1",
      attemptLabel: "Attempt 1",
      date: new Date(today - 8 * weekMs).toISOString(),
      overallBand: 5.5,
      criterionBands: {
        fluency_coherence: 5.0,
        lexical_resource: 6.0,
        grammatical_range_accuracy: 5.5,
        pronunciation: 6.0,
      },
    },
    {
      sessionId: "demo-attempt-2",
      attemptLabel: "Attempt 2",
      date: new Date(today - 5 * weekMs).toISOString(),
      overallBand: 6.0,
      criterionBands: {
        fluency_coherence: 5.5,
        lexical_resource: 6.5,
        grammatical_range_accuracy: 5.5,
        pronunciation: 6.5,
      },
    },
    {
      sessionId: "demo-attempt-3",
      attemptLabel: "Attempt 3",
      date: new Date(today - 2 * weekMs).toISOString(),
      overallBand: 6.0,
      criterionBands: {
        fluency_coherence: 6.0,
        lexical_resource: 6.5,
        grammatical_range_accuracy: 6.0,
        pronunciation: 6.0,
      },
    },
    {
      sessionId: currentSessionId,
      attemptLabel: "This exam",
      date: new Date(today).toISOString(),
      overallBand: 6.5,
      criterionBands: {
        fluency_coherence: 6.5,
        lexical_resource: 7.0,
        grammatical_range_accuracy: 6.0,
        pronunciation: 6.5,
      },
    },
  ];
}

interface WordSpec {
  text: string;
  flag?: TranscriptWordFlag;
  pauseBeforeMs?: number;
}

function layoutWords(startMs: number, specs: WordSpec[]): TranscriptWord[] {
  let cursor = startMs;
  return specs.map((spec) => {
    cursor += spec.pauseBeforeMs ?? 90;
    const letters = spec.text.replace(/[^a-zA-Z']/g, "").length;
    const durationMs = 140 + letters * 55;
    const word: TranscriptWord = {
      word: spec.text,
      startMs: cursor,
      endMs: cursor + durationMs,
      ...(spec.flag ? { flag: spec.flag } : {}),
    };
    cursor += durationMs;
    return word;
  });
}

export function getDemoTranscript(): TranscriptTurn[] {
  const examinerWords = layoutWords(0, [
    { text: "Now" },
    { text: "please" },
    { text: "describe" },
    { text: "a" },
    { text: "skill" },
    { text: "you" },
    { text: "learned" },
    { text: "that" },
    { text: "you" },
    { text: "found" },
    { text: "difficult" },
    { text: "at" },
    { text: "first." },
  ]);
  const examinerEnd = examinerWords[examinerWords.length - 1].endMs;

  const candidateWords = layoutWords(examinerEnd + 700, [
    { text: "So," },
    { text: "um,", flag: { criterion: "fluency_coherence", note: "Filled pause — counted toward filled_pause_rate=7.2/100w" } },
    { text: "the" },
    { text: "skill" },
    { text: "I" },
    { text: "chose" },
    { text: "to" },
    { text: "talk" },
    { text: "about" },
    { text: "is" },
    { text: "playing" },
    { text: "the" },
    { text: "violin," },
    { text: "which" },
    { text: "I" },
    { text: "started" },
    { text: "learning" },
    { text: "about" },
    { text: "three" },
    { text: "years" },
    { text: "ago" },
    { text: "when" },
    { text: "I" },
    { text: "was," },
    { text: "uh,", flag: { criterion: "fluency_coherence", note: "Filled pause, mid-clause — heavier fluency cost than a boundary pause" } },
    { text: "still" },
    { text: "in" },
    { text: "high" },
    { text: "school." },
    { text: "At", pauseBeforeMs: 420 },
    { text: "first" },
    { text: "it" },
    { text: "was" },
    { text: "really" },
    {
      text: "really",
      flag: { criterion: "fluency_coherence", note: "Immediate repetition — flagged by the self-repair detector" },
    },
    { text: "difficult" },
    { text: "because" },
    { text: "my" },
    { text: "fingers" },
    { text: "didn't" },
    { text: "know" },
    { text: "where" },
    { text: "to" },
    { text: "go" },
    { text: "on" },
    { text: "the" },
    { text: "fingerboard" },
    { text: "at" },
    { text: "all." },
    { text: "I", pauseBeforeMs: 380 },
    { text: "practiced" },
    {
      text: "consistently",
      flag: { criterion: "lexical_resource", note: "Beyond-B2 collocation — \"practiced consistently\" contributes to beyond_B2_ratio=18%" },
    },
    { text: "almost" },
    { text: "every" },
    { text: "single" },
    { text: "day," },
    { text: "and" },
    { text: "gradually" },
    { text: "the" },
    { text: "notes" },
    { text: "started" },
    { text: "to" },
    { text: "sound" },
    { text: "less" },
    { text: "like" },
    { text: "a" },
    { text: "dying" },
    { text: "cat" },
    { text: "and" },
    { text: "more" },
    { text: "like" },
    { text: "actual" },
    { text: "music." },
    { text: "One", pauseBeforeMs: 420 },
    { text: "mistake" },
    { text: "I" },
    { text: "always" },
    { text: "made" },
    { text: "was" },
    {
      text: "I says",
      flag: { criterion: "grammatical_range_accuracy", note: "Subject–verb agreement error — drags down error_free_clause_ratio=0.71" },
    },
    { text: "the" },
    { text: "wrong" },
    { text: "finger" },
    { text: "position." },
    { text: "But", pauseBeforeMs: 400 },
    { text: "now," },
    { text: "after" },
    { text: "years" },
    { text: "of" },
    { text: "practice," },
    { text: "I" },
    { text: "can" },
    { text: "play" },
    { text: "reasonably" },
    {
      text: "fluently",
      flag: { criterion: "pronunciation", note: "Flattened sentence stress here — reflected in prosody_score=68.0" },
    },
    { text: "and" },
    { text: "I" },
    { text: "even" },
    { text: "performed" },
    { text: "in" },
    { text: "a" },
    { text: "small" },
    { text: "recital" },
    { text: "last" },
    { text: "spring." },
  ]);

  return [
    { turnId: "turn-examiner-intro", phase: "PART2_CUECARD_PRESENT", speaker: "examiner", words: examinerWords },
    { turnId: "turn-candidate-long", phase: "PART2_LONG_TURN", speaker: "candidate", words: candidateWords },
  ];
}
