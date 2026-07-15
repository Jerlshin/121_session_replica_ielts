"use client";

import { useEffect, useRef } from "react";

import { readFrequencyBands, readLevel } from "@/audio/audio-level";

// Purely decorative — the actual "who's speaking" state is announced
// elsewhere as text (CLAUDE.md rule 1 concern doesn't apply here: this
// never decides exam state, it only visualizes state the page already
// derived from server messages). Canvas rather than SVG/Three.js: cheapest
// path to a soft, organic, 60fps-capable blob with no extra render-engine
// dependency.
export type VoiceBlobState = "idle" | "examiner" | "user";

interface VoiceBlobProps {
  state: VoiceBlobState;
  examinerAnalyser?: AnalyserNode | null;
  userAnalyser?: AnalyserNode | null;
  size?: number;
  className?: string;
}

const POINTS = 14;

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "").trim();
  const bigint = parseInt(clean.length === 3 ? clean.replace(/./g, (c) => c + c) : clean, 16);
  return [(bigint >> 16) & 255, (bigint >> 8) & 255, bigint & 255];
}

function readCssColor(varName: string, fallback: string): [number, number, number] {
  const value = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  return hexToRgb(value || fallback);
}

interface ThemeColors {
  examiner: [number, number, number];
  examinerGlow: [number, number, number];
  user: [number, number, number];
  userGlow: [number, number, number];
  idle: [number, number, number];
}

export function VoiceBlob({
  state,
  examinerAnalyser,
  userAnalyser,
  size = 280,
  className,
}: VoiceBlobProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const stateRef = useRef(state);
  const analysersRef = useRef({ examinerAnalyser, userAnalyser });
  const colorsRef = useRef<ThemeColors>({
    examiner: [42, 120, 214],
    examinerGlow: [111, 168, 234],
    user: [74, 58, 167],
    userGlow: [185, 139, 224],
    idle: [137, 135, 129],
  });

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(() => {
    analysersRef.current = { examinerAnalyser, userAnalyser };
  }, [examinerAnalyser, userAnalyser]);

  // Re-read theme colors on mount and whenever the theme toggle flips the
  // `dark` class on <html> — far cheaper than getComputedStyle every frame.
  useEffect(() => {
    const refresh = () => {
      colorsRef.current = {
        examiner: readCssColor("--voice-examiner", "#2a78d6"),
        examinerGlow: readCssColor("--voice-examiner-glow", "#6fa8ea"),
        user: readCssColor("--voice-user", "#4a3aa7"),
        userGlow: readCssColor("--voice-user-glow", "#b98be0"),
        idle: readCssColor("--voice-idle", "#898781"),
      };
    };
    refresh();
    const observer = new MutationObserver(refresh);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    ctx.scale(dpr, dpr);

    const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

    const center = size / 2;
    const baseRadius = size * 0.28;
    const phases = Array.from({ length: POINTS }, () => Math.random() * Math.PI * 2);
    const freqs = Array.from({ length: POINTS }, () => 0.5 + Math.random() * 0.7);

    let raf = 0;
    let smoothLevel = 0;
    let t = 0;

    const draw = () => {
      t += reduceMotion ? 0.003 : 0.016;
      const current = stateRef.current;
      const { examinerAnalyser: examiner, userAnalyser: user } = analysersRef.current;

      let rawLevel = 0;
      let bands: number[] | null = null;
      if (current === "examiner" && examiner) {
        rawLevel = readLevel(examiner);
      } else if (current === "user" && user) {
        rawLevel = readLevel(user);
        bands = readFrequencyBands(user, POINTS);
      }
      smoothLevel += (rawLevel - smoothLevel) * 0.25;

      const profile =
        current === "examiner"
          ? {
              color: colorsRef.current.examiner,
              glow: colorsRef.current.examinerGlow,
              ampBase: 0.045,
              ampReactive: reduceMotion ? 0.04 : 0.3,
              speed: 1,
            }
          : current === "user"
            ? {
                color: colorsRef.current.user,
                glow: colorsRef.current.userGlow,
                ampBase: 0.05,
                ampReactive: reduceMotion ? 0.04 : 0.55,
                speed: 1.9,
              }
            : {
                color: colorsRef.current.idle,
                glow: colorsRef.current.idle,
                ampBase: reduceMotion ? 0.008 : 0.03,
                ampReactive: 0,
                speed: 0.45,
              };

      ctx.clearRect(0, 0, size, size);

      const haloRadius = baseRadius * (1.3 + smoothLevel * 0.55);
      const halo = ctx.createRadialGradient(center, center, baseRadius * 0.35, center, center, haloRadius);
      halo.addColorStop(
        0,
        `rgba(${profile.glow[0]},${profile.glow[1]},${profile.glow[2]},${(0.16 + smoothLevel * 0.24).toFixed(3)})`
      );
      halo.addColorStop(1, `rgba(${profile.glow[0]},${profile.glow[1]},${profile.glow[2]},0)`);
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(center, center, haloRadius, 0, Math.PI * 2);
      ctx.fill();

      const points: Array<{ x: number; y: number }> = [];
      for (let i = 0; i < POINTS; i++) {
        const angle = (i / POINTS) * Math.PI * 2;
        const bandTerm = bands ? bands[i] : smoothLevel;
        const wobble =
          Math.sin(t * profile.speed * freqs[i] + phases[i]) * profile.ampBase +
          bandTerm * profile.ampReactive * (0.6 + 0.4 * Math.sin(t * 2 + phases[i]));
        const radius = baseRadius * (1 + wobble);
        points.push({ x: center + Math.cos(angle) * radius, y: center + Math.sin(angle) * radius });
      }

      ctx.beginPath();
      const wrap = { x: (points[POINTS - 1].x + points[0].x) / 2, y: (points[POINTS - 1].y + points[0].y) / 2 };
      ctx.moveTo(wrap.x, wrap.y);
      for (let i = 0; i < POINTS; i++) {
        const next = points[(i + 1) % POINTS];
        const mid = { x: (points[i].x + next.x) / 2, y: (points[i].y + next.y) / 2 };
        ctx.quadraticCurveTo(points[i].x, points[i].y, mid.x, mid.y);
      }
      ctx.closePath();

      const bodyGradient = ctx.createRadialGradient(
        center - baseRadius * 0.25,
        center - baseRadius * 0.32,
        baseRadius * 0.08,
        center,
        center,
        baseRadius * 1.2
      );
      bodyGradient.addColorStop(0, `rgba(${profile.glow[0]},${profile.glow[1]},${profile.glow[2]},0.95)`);
      bodyGradient.addColorStop(1, `rgba(${profile.color[0]},${profile.color[1]},${profile.color[2]},0.92)`);
      ctx.fillStyle = bodyGradient;
      ctx.fill();

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [size]);

  return (
    <canvas
      ref={canvasRef}
      aria-hidden="true"
      className={className}
      style={{ width: size, height: size, display: "block" }}
    />
  );
}
