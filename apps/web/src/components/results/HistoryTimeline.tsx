"use client";

import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { CRITERION_ORDER, CRITERION_SHORT_LABELS, type CriterionKey, type HistoryPoint } from "@/types/report";

interface HistoryTimelineProps {
  history: HistoryPoint[];
  targetBand: number;
}

function HistoryTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-lg border border-border bg-surface-raised px-3 py-2 text-xs shadow-lg">
      <p className="mb-1 font-medium text-ink-secondary">{label}</p>
      {payload.map((entry) => (
        <p key={entry.dataKey as string} className="flex items-center gap-2">
          <span className="h-[2px] w-3 shrink-0" style={{ backgroundColor: entry.color }} aria-hidden="true" />
          <span className="font-semibold text-ink">{Number(entry.value).toFixed(1)}</span>
          <span className="text-ink-muted">{entry.name}</span>
        </p>
      ))}
    </div>
  );
}

export function HistoryTimeline({ history, targetBand }: HistoryTimelineProps) {
  const [selected, setSelected] = useState<CriterionKey | null>(null);

  const data = history.map((point) => ({
    attemptLabel: point.attemptLabel,
    overall: point.overallBand,
    criterion: selected ? point.criterionBands[selected] : undefined,
  }));

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Progress over time</CardTitle>
          <p className="mt-1 text-xs text-ink-muted">Overall band across your last {history.length} exams</p>
        </div>
      </CardHeader>
      <CardContent>
        <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="Overlay a criterion trend">
          <button
            type="button"
            onClick={() => setSelected(null)}
            className={cn(
              "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
              selected === null
                ? "border-accent-blue/40 bg-accent-blue/10 text-accent-blue"
                : "border-border text-ink-muted hover:text-ink"
            )}
          >
            Overall only
          </button>
          {CRITERION_ORDER.map((criterion) => (
            <button
              key={criterion}
              type="button"
              onClick={() => setSelected(criterion)}
              className={cn(
                "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                selected === criterion
                  ? "border-accent-aqua/40 bg-accent-aqua/10 text-accent-aqua"
                  : "border-border text-ink-muted hover:text-ink"
              )}
            >
              + {CRITERION_SHORT_LABELS[criterion]}
            </button>
          ))}
        </div>

        <div
          className="h-72 w-full"
          role="img"
          aria-label={`Line chart of overall band score across ${history.length} exams, from ${history[0]?.overallBand.toFixed(1)} to ${history[history.length - 1]?.overallBand.toFixed(1)}`}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 4 }}>
              <CartesianGrid stroke="var(--gridline)" vertical={false} />
              <XAxis
                dataKey="attemptLabel"
                interval={0}
                padding={{ left: 24, right: 24 }}
                tick={{ fill: "var(--ink-muted)", fontSize: 12 }}
                axisLine={{ stroke: "var(--baseline)" }}
                tickLine={false}
              />
              <YAxis
                domain={[0, 9]}
                ticks={[0, 3, 6, 9]}
                tick={{ fill: "var(--ink-muted)", fontSize: 12 }}
                axisLine={false}
                tickLine={false}
                width={28}
              />
              <ReferenceLine
                y={targetBand}
                stroke="var(--baseline)"
                strokeDasharray="4 3"
                label={{
                  value: `Target ${targetBand.toFixed(1)}`,
                  position: "insideTopLeft",
                  fill: "var(--ink-muted)",
                  fontSize: 11,
                }}
              />
              <Tooltip content={HistoryTooltip} cursor={{ stroke: "var(--baseline)", strokeWidth: 1 }} />
              <Line
                name="Overall band"
                type="monotone"
                dataKey="overall"
                stroke="var(--accent-blue)"
                strokeWidth={2}
                dot={{ r: 4, fill: "var(--accent-blue)", strokeWidth: 2, stroke: "var(--surface-raised)" }}
                activeDot={{ r: 5 }}
                isAnimationActive
                animationDuration={700}
              />
              {selected && (
                <Line
                  name={CRITERION_SHORT_LABELS[selected]}
                  type="monotone"
                  dataKey="criterion"
                  stroke="var(--accent-aqua)"
                  strokeWidth={2}
                  dot={{ r: 4, fill: "var(--accent-aqua)", strokeWidth: 2, stroke: "var(--surface-raised)" }}
                  activeDot={{ r: 5 }}
                  isAnimationActive
                  animationDuration={700}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>

        <div className="mt-2 flex items-center gap-4 text-xs text-ink-secondary">
          <span className="flex items-center gap-1.5">
            <span className="h-[2px] w-3 bg-accent-blue" aria-hidden="true" />
            Overall band
          </span>
          {selected && (
            <span className="flex items-center gap-1.5">
              <span className="h-[2px] w-3 bg-accent-aqua" aria-hidden="true" />
              {CRITERION_SHORT_LABELS[selected]}
            </span>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
