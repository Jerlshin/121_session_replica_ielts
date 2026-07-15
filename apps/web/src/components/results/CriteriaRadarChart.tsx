"use client";

import {
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  type TooltipContentProps,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { CRITERION_ORDER, CRITERION_SHORT_LABELS, type CriterionScore } from "@/types/report";

interface CriteriaRadarChartProps {
  criterionScores: CriterionScore[];
  targetBand: number;
}

function RadarTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const label = String((payload[0]?.payload as { label?: string } | undefined)?.label ?? "");
  return (
    <div className="rounded-lg border border-border bg-surface-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink-secondary">{label}</p>
      {payload.map((entry) => (
        <p key={entry.dataKey as string} className="flex items-center gap-2">
          <span
            className="h-[2px] w-3 shrink-0"
            style={{ backgroundColor: entry.color }}
            aria-hidden="true"
          />
          <span className="font-semibold text-ink">{Number(entry.value).toFixed(1)}</span>
          <span className="text-ink-muted">{entry.name}</span>
        </p>
      ))}
    </div>
  );
}

export function CriteriaRadarChart({ criterionScores, targetBand }: CriteriaRadarChartProps) {
  const byKey = new Map(criterionScores.map((s) => [s.criterion, s.band]));
  const data = CRITERION_ORDER.map((criterion) => ({
    criterion,
    label: CRITERION_SHORT_LABELS[criterion],
    candidate: byKey.get(criterion) ?? 0,
    target: targetBand,
  }));

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Criteria profile</CardTitle>
          <p className="mt-1 text-xs text-ink-muted">Your band per criterion vs. target</p>
        </div>
        <div className="flex flex-col items-end gap-1 text-xs">
          <span className="flex items-center gap-1.5 text-ink-secondary">
            <span className="h-2.5 w-2.5 rounded-sm bg-accent-blue" aria-hidden="true" />
            Your band
          </span>
          <span className="flex items-center gap-1.5 text-ink-secondary">
            <span className="h-0 w-3 border-t-2 border-dashed border-baseline" aria-hidden="true" />
            {`Target (${targetBand.toFixed(1)})`}
          </span>
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-72 w-full" role="img" aria-label={`Radar chart comparing band scores across ${CRITERION_ORDER.length} criteria against a target of ${targetBand.toFixed(1)}`}>
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={data} outerRadius="72%">
              <PolarGrid stroke="var(--gridline)" />
              <PolarAngleAxis
                dataKey="label"
                tick={{ fill: "var(--ink-secondary)", fontSize: 12 }}
              />
              <PolarRadiusAxis
                angle={90}
                domain={[0, 9]}
                tickCount={4}
                tick={{ fill: "var(--ink-muted)", fontSize: 10 }}
                axisLine={false}
              />
              <Radar
                name="Target"
                dataKey="target"
                stroke="var(--baseline)"
                strokeDasharray="4 3"
                strokeWidth={2}
                fill="none"
                legendType="line"
                isAnimationActive={false}
              />
              <Radar
                name="Your band"
                dataKey="candidate"
                stroke="var(--accent-blue)"
                fill="var(--accent-blue)"
                fillOpacity={0.18}
                strokeWidth={2}
                dot={{ r: 4, fill: "var(--accent-blue)", strokeWidth: 2, stroke: "var(--surface-raised)" }}
                legendType="rect"
                animationDuration={700}
              />
              <Tooltip content={RadarTooltip} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}
