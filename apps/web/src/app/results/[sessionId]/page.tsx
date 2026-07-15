"use client";

import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { BandScoreHero } from "@/components/results/BandScoreHero";
import { CriteriaRadarChart } from "@/components/results/CriteriaRadarChart";
import { CriterionCard } from "@/components/results/CriterionCard";
import { HistoryTimeline } from "@/components/results/HistoryTimeline";
import { TranscriptAudioMap, type JumpToken } from "@/components/results/TranscriptAudioMap";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { getDemoHistory, getDemoSessionReport, getDemoTranscript } from "@/lib/demoReport";
import type { CriterionKey } from "@/types/report";

export default function ResultsPage() {
  const params = useParams<{ sessionId: string }>();
  const sessionId = params.sessionId;

  const report = useMemo(() => getDemoSessionReport(sessionId), [sessionId]);
  const history = useMemo(() => getDemoHistory(sessionId), [sessionId]);
  const transcript = useMemo(() => getDemoTranscript(), []);

  const [jumpTo, setJumpTo] = useState<JumpToken | null>(null);
  const handleJumpToEvidence = (criterion: CriterionKey) =>
    setJumpTo((prev) => ({ criterion, nonce: (prev?.nonce ?? 0) + 1 }));

  return (
    <main className="min-h-screen bg-page pb-16">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Results</p>
          <p className="text-sm font-semibold text-ink">Session {sessionId.slice(0, 8)}</p>
        </div>
        <ThemeToggle />
      </header>

      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-6 py-8">
        <BandScoreHero report={report} />

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <CriteriaRadarChart criterionScores={report.criterionScores} targetBand={report.targetBand} />
          <HistoryTimeline history={history} targetBand={report.targetBand} />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {report.criterionScores.map((score, index) => (
            <CriterionCard
              key={score.criterion}
              score={score}
              index={index}
              onJumpToEvidence={handleJumpToEvidence}
            />
          ))}
        </div>

        <TranscriptAudioMap turns={transcript} jumpTo={jumpTo} />
      </div>
    </main>
  );
}
