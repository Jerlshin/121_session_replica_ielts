"use client";

import { Pause, Play } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { cn } from "@/lib/utils";
import { CRITERION_LABELS, type CriterionKey, type TranscriptTurn } from "@/types/report";

const CRITERION_UNDERLINE: Record<CriterionKey, string> = {
  fluency_coherence: "var(--accent-blue)",
  lexical_resource: "var(--accent-aqua)",
  grammatical_range_accuracy: "var(--accent-yellow)",
  pronunciation: "var(--accent-violet)",
};

export interface JumpToken {
  criterion: CriterionKey;
  nonce: number;
}

interface TranscriptAudioMapProps {
  turns: TranscriptTurn[];
  jumpTo?: JumpToken | null;
}

function formatMs(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

// There is no presigned audio URL wired to the client yet (Spec 01 §7's
// object storage playback is a later phase) — this simulates a real-time
// transport against the word timestamps so the seek/playback interaction
// itself is fully demonstrable now, and is a drop-in swap for a real
// <audio> element's timeupdate/currentTime once that endpoint exists.
export function TranscriptAudioMap({ turns, jumpTo }: TranscriptAudioMapProps) {
  const totalMs = useMemo(() => {
    let max = 0;
    for (const turn of turns) {
      for (const word of turn.words) max = Math.max(max, word.endMs);
    }
    return max;
  }, [turns]);

  const [currentMs, setCurrentMs] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [pulseKey, setPulseKey] = useState<string | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastFrameRef = useRef<number | null>(null);
  const wordRefs = useRef(new Map<string, HTMLElement>());
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!isPlaying) return;
    lastFrameRef.current = null;

    const tick = (now: number) => {
      if (lastFrameRef.current === null) lastFrameRef.current = now;
      const delta = now - lastFrameRef.current;
      lastFrameRef.current = now;

      setCurrentMs((prev) => {
        const next = prev + delta;
        if (next >= totalMs) {
          setIsPlaying(false);
          return totalMs;
        }
        return next;
      });
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [isPlaying, totalMs]);

  const seekTo = (ms: number, autoPlay: boolean) => {
    setCurrentMs(Math.min(totalMs, Math.max(0, ms)));
    setIsPlaying(autoPlay);
  };

  const handleTogglePlay = () => {
    if (currentMs >= totalMs) {
      seekTo(0, true);
    } else {
      setIsPlaying((prev) => !prev);
    }
  };

  useEffect(() => {
    if (!jumpTo) return;
    for (const turn of turns) {
      const word = turn.words.find((w) => w.flag?.criterion === jumpTo.criterion);
      if (word) {
        seekTo(word.startMs, false);
        const key = `${turn.turnId}-${word.startMs}`;
        setPulseKey(key);
        wordRefs.current.get(key)?.scrollIntoView({ behavior: "smooth", block: "center" });
        const timeout = setTimeout(() => setPulseKey(null), 1500);
        return () => clearTimeout(timeout);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jumpTo]);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Transcript &amp; evidence map</CardTitle>
          <p className="mt-1 text-xs text-ink-muted">
            Click any word or flagged moment to seek. Colored underlines link back to a scored criterion.
          </p>
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-3 rounded-xl border border-border bg-page px-3 py-2">
          <button
            type="button"
            onClick={handleTogglePlay}
            aria-label={isPlaying ? "Pause playback" : "Play from current position"}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-blue text-white transition-transform active:scale-95"
          >
            {isPlaying ? <Pause size={14} fill="currentColor" /> : <Play size={14} fill="currentColor" />}
          </button>
          <span className="w-10 shrink-0 text-xs tabular-nums text-ink-muted">{formatMs(currentMs)}</span>
          <input
            type="range"
            min={0}
            max={totalMs}
            value={currentMs}
            onChange={(event) => seekTo(Number(event.target.value), isPlaying)}
            aria-label="Seek transcript playback position"
            className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-gridline accent-accent-blue"
          />
          <span className="w-10 shrink-0 text-right text-xs tabular-nums text-ink-muted">
            {formatMs(totalMs)}
          </span>
        </div>

        <div ref={containerRef} className="mt-4 max-h-80 space-y-4 overflow-y-auto pr-1">
          {turns.map((turn) => (
            <div key={turn.turnId}>
              <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                {turn.speaker === "examiner" ? "Examiner" : "Candidate"}
              </p>
              <p className="text-sm leading-relaxed text-ink">
                {turn.words.map((word) => {
                  const key = `${turn.turnId}-${word.startMs}`;
                  const isActive = currentMs >= word.startMs && currentMs < word.endMs;
                  const isPulsing = pulseKey === key;
                  return (
                    <button
                      key={key}
                      type="button"
                      ref={(el) => {
                        if (el) wordRefs.current.set(key, el);
                        else wordRefs.current.delete(key);
                      }}
                      onClick={() => seekTo(word.startMs, true)}
                      title={word.flag ? `${CRITERION_LABELS[word.flag.criterion]}: ${word.flag.note}` : undefined}
                      className={cn(
                        "mr-1 rounded px-0.5 py-0.5 outline-offset-2 transition-colors",
                        isActive ? "bg-accent-blue/15 text-ink" : "text-ink hover:bg-page",
                        isPulsing && "motion-safe:animate-flash-highlight"
                      )}
                      style={
                        word.flag
                          ? {
                              boxShadow: `inset 0 -2px 0 0 ${CRITERION_UNDERLINE[word.flag.criterion]}`,
                            }
                          : undefined
                      }
                    >
                      {word.word}
                    </button>
                  );
                })}
              </p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
