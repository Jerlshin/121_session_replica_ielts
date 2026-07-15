"use client";

import { AnimatePresence, motion } from "framer-motion";
import { MessageSquareText, Radio } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { PlaybackJitterBuffer } from "@/audio/playback-jitter-buffer";
import { ProctoringRecorder } from "@/audio/proctoring-recorder";
import { ConnectionStatusBanner } from "@/components/exam/ConnectionStatusBanner";
import { CountdownPanel } from "@/components/exam/CountdownPanel";
import { CueCardPanel } from "@/components/exam/CueCardPanel";
import { PTTButton } from "@/components/exam/PTTButton";
import { VoiceBlob, type VoiceBlobState } from "@/components/exam/VoiceBlob";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { confirmVideoUpload, getVideoUploadUrl, uploadVideoBlob, wsBaseUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useExamStore } from "@/state/examStore";
import { ExamSocketClient } from "@/ws/exam-socket-client";

// Must match apps/api-gateway/app/services/media_tap.py's PCM contract
// (Spec 01 §4.1) and the AudioContext rate the worklet assumes.
const PCM_SAMPLE_RATE_HZ = 16000;
// Gemini's audio output rate (Spec 01 §4.1) — distinct from the 16kHz
// input rate above. The AudioContext itself still runs at 16kHz (forced
// for the capture worklet); createBuffer's declared sample rate is what
// the browser uses to resample this stream correctly on playback.
const GEMINI_OUTPUT_SAMPLE_RATE_HZ = 24000;

// Grace period before the mic auto-arms once the examiner finishes
// speaking — long enough to not feel like it cuts them off, short enough
// that the candidate isn't left wondering if anything happened.
const AUTO_ARM_TURN_END_DELAY_MS = 650;
const AUTO_ARM_LONG_TURN_START_DELAY_MS = 500;

interface LogEntry {
  id: string;
  kind: "examiner" | "system";
  text: string;
}

export default function ExamRoomPage() {
  const params = useParams<{ sessionId: string }>();
  const router = useRouter();
  const sessionId = params.sessionId;

  const connectionStatus = useExamStore((s) => s.connectionStatus);
  const setConnectionStatus = useExamStore((s) => s.setConnectionStatus);
  const isPttActive = useExamStore((s) => s.isPttActive);
  const setPttActive = useExamStore((s) => s.setPttActive);
  const lastTurnId = useExamStore((s) => s.lastTurnId);
  const setLastTurnId = useExamStore((s) => s.setLastTurnId);
  const cueCard = useExamStore((s) => s.cueCard);
  const setCueCard = useExamStore((s) => s.setCueCard);
  const timerDeadline = useExamStore((s) => s.timerDeadline);
  const setTimerDeadline = useExamStore((s) => s.setTimerDeadline);

  const [log, setLog] = useState<LogEntry[]>([]);
  const [examinerSpeaking, setExaminerSpeaking] = useState(false);
  const [autoArming, setAutoArming] = useState(false);
  const [examinerAnalyser, setExaminerAnalyser] = useState<AnalyserNode | null>(null);
  const [userAnalyser, setUserAnalyser] = useState<AnalyserNode | null>(null);
  const [transcriptOpen, setTranscriptOpen] = useState(false);

  const socketRef = useRef<ExamSocketClient | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const jitterBufferRef = useRef<PlaybackJitterBuffer | null>(null);
  const proctoringRef = useRef<ProctoringRecorder | null>(null);
  const pttActiveRef = useRef(false);
  const connectionStatusRef = useRef(connectionStatus);
  const timerDeadlineRef = useRef(timerDeadline);
  const autoArmTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const appendLog = (text: string, kind: LogEntry["kind"] = "system") =>
    setLog((prev) => [...prev.slice(-49), { id: crypto.randomUUID(), kind, text }]);

  useEffect(() => {
    pttActiveRef.current = isPttActive;
  }, [isPttActive]);
  useEffect(() => {
    connectionStatusRef.current = connectionStatus;
  }, [connectionStatus]);
  useEffect(() => {
    timerDeadlineRef.current = timerDeadline;
  }, [timerDeadline]);

  const clearAutoArm = () => {
    if (autoArmTimeoutRef.current) {
      clearTimeout(autoArmTimeoutRef.current);
      autoArmTimeoutRef.current = null;
    }
    setAutoArming(false);
  };

  const handlePress = () => {
    clearAutoArm();
    if (pttActiveRef.current) return; // already an open turn — a second press is a no-op, only release ends it
    setPttActive(true);
    socketRef.current?.activityStart();
    proctoringRef.current?.start();
  };

  const handleRelease = async () => {
    clearAutoArm();
    if (!pttActiveRef.current) return;
    setPttActive(false);
    socketRef.current?.activityEnd();

    const token = sessionStorage.getItem("ielts_access_token");
    const blob = await proctoringRef.current?.stop();
    if (token && blob && blob.size > 0) {
      const { uploadUrl } = await getVideoUploadUrl(token, sessionId);
      await uploadVideoBlob(uploadUrl, blob);
      await confirmVideoUpload(token, sessionId);
      appendLog(`Proctoring clip uploaded (${(blob.size / 1024).toFixed(0)} KB)`);
    }
  };

  // Innovation on top of strict PTT (CLAUDE.md rule 2 still holds — this
  // still drives the exact same activityStart/activityEnd pair as a manual
  // press, it just triggers that press automatically instead of waiting for
  // a tap): once the examiner visibly finishes talking, the mic opens on
  // its own so the candidate never has to scramble for a button mid-thought.
  // Suppressed during PART2_PREP, which is silent by design (Spec 02 §1) —
  // derived from the *current* server-pushed timer, never invented locally.
  const scheduleAutoArm = (delayMs: number) => {
    if (pttActiveRef.current) return;
    if (connectionStatusRef.current !== "connected") return;
    const activeTimer = timerDeadlineRef.current;
    const inSilentPrep =
      activeTimer?.name === "part2_prep" && activeTimer.deadlineEpochMs > Date.now();
    if (inSilentPrep) return;

    clearAutoArm();
    setAutoArming(true);
    autoArmTimeoutRef.current = setTimeout(() => {
      autoArmTimeoutRef.current = null;
      setAutoArming(false);
      if (!pttActiveRef.current) handlePress();
    }, delayMs);
  };

  useEffect(() => {
    const storedToken = sessionStorage.getItem("ielts_access_token");
    if (!storedToken) {
      router.replace("/");
      return;
    }
    const token: string = storedToken;

    let cancelled = false;

    async function setup() {
      setConnectionStatus("connecting");

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: true });
      if (cancelled) return;

      const audioContext = new AudioContext({ sampleRate: PCM_SAMPLE_RATE_HZ });
      audioContextRef.current = audioContext;
      await audioContext.audioWorklet.addModule("/worklets/pcm-worklet-processor.js");

      const source = audioContext.createMediaStreamSource(stream);
      const workletNode = new AudioWorkletNode(audioContext, "pcm-worklet-processor");
      workletNodeRef.current = workletNode;
      source.connect(workletNode);

      // UI-only amplitude tap for the candidate's own mic (VoiceBlob's
      // "user speaking" state) — routed through a silent gain node so it
      // stays in the actively-processed graph without ever being audible
      // to the candidate (would otherwise be mic self-monitoring, which
      // nobody wants).
      const micAnalyser = audioContext.createAnalyser();
      micAnalyser.fftSize = 256;
      micAnalyser.smoothingTimeConstant = 0.65;
      const silentGain = audioContext.createGain();
      silentGain.gain.value = 0;
      source.connect(micAnalyser);
      micAnalyser.connect(silentGain);
      silentGain.connect(audioContext.destination);
      setUserAnalyser(micAnalyser);

      const jitterBuffer = new PlaybackJitterBuffer(audioContext, GEMINI_OUTPUT_SAMPLE_RATE_HZ);
      jitterBufferRef.current = jitterBuffer;
      setExaminerAnalyser(jitterBuffer.getAnalyser());

      const socket = new ExamSocketClient({
        sessionId,
        token,
        wsBaseUrl: wsBaseUrl(),
        onServerMessage: (message) => {
          if (message.type === "connected") {
            setConnectionStatus("connected");
            appendLog(`Connected to session ${message.session_id}`);
          } else if (message.type === "resumed") {
            setConnectionStatus("connected");
            appendLog(`Resumed session ${message.session_id} — examiner context restored`);
          } else if (message.type === "activity_start_ack") {
            setLastTurnId(message.turn_id);
          } else if (message.type === "turn_complete") {
            appendLog(`Your answer was received (${(message.byte_size / 1024).toFixed(0)} KB)`);
          } else if (message.type === "transcript_delta") {
            appendLog(message.text, "examiner");
          } else if (message.type === "gemini_turn_complete") {
            setExaminerSpeaking(false);
            appendLog("Examiner finished speaking");
            scheduleAutoArm(AUTO_ARM_TURN_END_DELAY_MS);
          } else if (message.type === "interrupted") {
            setExaminerSpeaking(false);
            appendLog("Examiner turn interrupted");
          } else if (message.type === "server_going_away") {
            appendLog(`Server going away in ${message.time_left_ms ?? "?"}ms — expect a reconnect`);
          } else if (message.type === "cue_card") {
            setCueCard({
              cueCardId: message.cue_card_id,
              topic: message.topic,
              bullets: message.bullets,
            });
            appendLog(`Cue card presented: ${message.topic}`);
          } else if (message.type === "timer_deadline") {
            setTimerDeadline({ name: message.name, deadlineEpochMs: message.deadline_epoch_ms });
            appendLog(`Timer started: ${message.name.replace(/_/g, " ")}`);
            if (message.name === "part2_long_turn") {
              scheduleAutoArm(AUTO_ARM_LONG_TURN_START_DELAY_MS);
            }
          } else if (message.type === "scripted_audio") {
            appendLog(`Examiner cue: ${message.asset}`);
          }
        },
        onAudioFrame: (frame) => {
          jitterBufferRef.current?.enqueue(frame);
          setExaminerSpeaking(true);
        },
        onReconnecting: () => setConnectionStatus("reconnecting"),
        onClose: () => setConnectionStatus("disconnected"),
      });
      socketRef.current = socket;
      await socket.connect();

      // The worklet emits continuously; only forward frames to the
      // gateway while PTT is held — PTT is the sole turn boundary, not
      // the worklet itself (CLAUDE.md rule 2).
      workletNode.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (pttActiveRef.current) {
          socketRef.current?.sendAudioFrame(event.data);
        }
      };

      proctoringRef.current = new ProctoringRecorder(stream);
    }

    setup().catch((error) => {
      console.error(error);
      appendLog(`Setup failed: ${String(error)}`);
    });

    return () => {
      cancelled = true;
      clearAutoArm();
      socketRef.current?.close();
      workletNodeRef.current?.port.close();
      void audioContextRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const blobState: VoiceBlobState = isPttActive ? "user" : examinerSpeaking ? "examiner" : "idle";

  const statusLabel =
    connectionStatus === "connecting"
      ? "Preparing your exam room…"
      : connectionStatus === "reconnecting"
        ? "Reconnecting…"
        : connectionStatus === "disconnected"
          ? "Disconnected"
          : examinerSpeaking
            ? "Examiner is speaking"
            : isPttActive
              ? "You're live — the examiner is listening"
              : autoArming
                ? "Get ready — the mic is opening…"
                : "Ready when you are";

  return (
    <main className="flex min-h-screen flex-col bg-page">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <div>
          <p className="text-xs font-medium uppercase tracking-wide text-ink-muted">Exam room</p>
          <p className="text-sm font-semibold text-ink">Session {sessionId.slice(0, 8)}</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setTranscriptOpen((v) => !v)}
            aria-pressed={transcriptOpen}
            aria-label={transcriptOpen ? "Hide transcript" : "Show transcript"}
            className={cn(
              "flex h-9 w-9 items-center justify-center rounded-full border border-border transition-colors",
              "bg-surface-raised text-ink-secondary hover:text-ink hover:border-accent-blue/40 active:scale-95"
            )}
          >
            <MessageSquareText size={16} aria-hidden="true" />
          </button>
          <ThemeToggle />
        </div>
      </header>

      <div className="px-6 pt-4">
        <ConnectionStatusBanner status={connectionStatus} />
      </div>

      <div className="mx-auto flex w-full max-w-6xl flex-1 flex-col gap-6 px-6 py-8 lg:flex-row">
        <section className="flex flex-1 flex-col items-center justify-center gap-6 rounded-3xl border border-border bg-surface p-8">
          <div className="relative flex items-center justify-center">
            <VoiceBlob state={blobState} examinerAnalyser={examinerAnalyser} userAnalyser={userAnalyser} size={260} />
          </div>

          <p role="status" aria-live="polite" className="text-sm font-medium text-ink-secondary">
            {statusLabel}
          </p>

          <PTTButton
            active={isPttActive}
            disabled={connectionStatus !== "connected"}
            onPress={handlePress}
            onRelease={() => void handleRelease()}
          />

          <p className="max-w-sm text-center text-xs text-ink-muted">
            The mic opens automatically once the examiner finishes speaking. Hold or tap it any
            time to speak sooner, and tap it again when you&apos;re done.
          </p>
        </section>

        <div className="flex w-full flex-col gap-4 lg:w-80">
          <AnimatePresence mode="popLayout">
            {cueCard && <CueCardPanel key={cueCard.cueCardId} cueCard={cueCard} />}
            {timerDeadline && (
              <CountdownPanel key={`${timerDeadline.name}-${timerDeadline.deadlineEpochMs}`} timerDeadline={timerDeadline} />
            )}
          </AnimatePresence>

          {lastTurnId && (
            <div className="flex items-center gap-2 rounded-xl border border-border bg-surface-raised px-4 py-3 text-xs text-ink-muted">
              <Radio size={14} aria-hidden="true" className="text-accent-blue" />
              Last turn: <span className="font-mono">{lastTurnId.slice(0, 8)}</span>
            </div>
          )}
        </div>
      </div>

      <AnimatePresence>
        {transcriptOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden border-t border-border bg-surface"
          >
            <div className="mx-auto flex max-w-6xl flex-col gap-2 px-6 py-4">
              <p className="text-xs font-semibold uppercase tracking-wide text-ink-muted">
                Live transcript
              </p>
              <div className="flex max-h-56 flex-col gap-1.5 overflow-y-auto pr-1">
                {log.length === 0 && <p className="text-sm text-ink-muted">Nothing yet.</p>}
                {log.map((entry) => (
                  <motion.p
                    key={entry.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.2 }}
                    className={cn(
                      "text-sm",
                      entry.kind === "examiner" ? "font-medium text-accent-blue" : "text-ink-muted"
                    )}
                  >
                    {entry.text}
                  </motion.p>
                ))}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </main>
  );
}
