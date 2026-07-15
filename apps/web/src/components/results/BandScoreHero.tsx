"use client";

import { motion } from "framer-motion";
import { AlertTriangle, TrendingUp } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { SessionReport } from "@/types/report";

const RADIUS = 54;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const MAX_BAND = 9;

interface BandScoreHeroProps {
  report: SessionReport;
}

export function BandScoreHero({ report }: BandScoreHeroProps) {
  const { overallBand, targetBand, candidateDisplayName, examDate, flagForHumanReview } = report;
  const progress = overallBand / MAX_BAND;
  const onTarget = overallBand >= targetBand;
  const delta = Math.round((overallBand - targetBand) * 10) / 10;

  return (
    <section className="rounded-2xl border border-border bg-surface-raised p-6 sm:p-8">
      <div className="flex flex-col items-start justify-between gap-6 sm:flex-row sm:items-center">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
            Overall band score
          </p>
          <h1 className="mt-1 text-2xl font-semibold text-ink">{candidateDisplayName}</h1>
          <p className="text-sm text-ink-muted">
            {new Date(examDate).toLocaleDateString("en-US", {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </p>
          {flagForHumanReview && (
            <Badge variant="warning" className="mt-3 gap-1">
              <AlertTriangle size={12} aria-hidden="true" />
              Flagged for human review
            </Badge>
          )}
        </div>

        <div className="relative flex h-36 w-36 shrink-0 items-center justify-center">
          <svg width="144" height="144" viewBox="0 0 144 144" aria-hidden="true" className="-rotate-90">
            <circle cx="72" cy="72" r={RADIUS} fill="none" stroke="var(--gridline)" strokeWidth="10" />
            <motion.circle
              cx="72"
              cy="72"
              r={RADIUS}
              fill="none"
              stroke="var(--accent-blue)"
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray={CIRCUMFERENCE}
              initial={{ strokeDashoffset: CIRCUMFERENCE }}
              animate={{ strokeDashoffset: CIRCUMFERENCE * (1 - progress) }}
              transition={{ duration: 1, ease: [0.16, 1, 0.3, 1], delay: 0.15 }}
            />
          </svg>
          <div className="absolute flex flex-col items-center">
            <span className="text-4xl font-semibold text-ink">{overallBand.toFixed(1)}</span>
            <span className="text-[11px] text-ink-muted">of 9.0</span>
          </div>
        </div>
      </div>

      <div
        className={cn(
          "mt-6 inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-sm font-medium",
          onTarget
            ? "border-status-good/25 bg-status-good/10 text-status-good"
            : "border-accent-blue/25 bg-accent-blue/10 text-accent-blue"
        )}
      >
        <TrendingUp size={15} aria-hidden="true" />
        {onTarget
          ? `At or above your ${targetBand.toFixed(1)} target`
          : `${Math.abs(delta).toFixed(1)} band${Math.abs(delta) === 1 ? "" : "s"} below your ${targetBand.toFixed(1)} target`}
      </div>
    </section>
  );
}
