"use client";

import { motion } from "framer-motion";
import { ScrollText } from "lucide-react";

import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { CRITERION_LABELS, type CriterionKey, type CriterionScore } from "@/types/report";

interface CriterionCardProps {
  score: CriterionScore;
  index: number;
  onJumpToEvidence?: (criterion: CriterionKey) => void;
}

export function CriterionCard({ score, index, onJumpToEvidence }: CriterionCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: index * 0.06, ease: [0.16, 1, 0.3, 1] }}
    >
      <Card className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">
              {CRITERION_LABELS[score.criterion]}
            </p>
            <p className="mt-1 text-2xl font-semibold text-ink">{score.band.toFixed(1)}</p>
          </div>
          <Badge variant="accent">{Math.round(score.confidence * 100)}% confidence</Badge>
        </div>

        <p className="mt-3 text-sm leading-relaxed text-ink-secondary">{score.justification}</p>

        <div className="mt-4 flex flex-wrap gap-1.5">
          {score.evidenceFeatures.map((feature) => (
            <Badge key={feature} variant="neutral" className="font-mono text-[11px]">
              {feature}
            </Badge>
          ))}
        </div>

        {onJumpToEvidence && (
          <button
            type="button"
            onClick={() => onJumpToEvidence(score.criterion)}
            className="mt-4 inline-flex items-center gap-1.5 text-xs font-medium text-accent-blue hover:underline"
          >
            <ScrollText size={13} aria-hidden="true" />
            View flagged moments in transcript
          </button>
        )}
      </Card>
    </motion.div>
  );
}
