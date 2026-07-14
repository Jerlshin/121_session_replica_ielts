"use client";

import { useEffect, useRef, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { PlaybackJitterBuffer } from "@/audio/playback-jitter-buffer";
import { ProctoringRecorder } from "@/audio/proctoring-recorder";
import { ConnectionStatusBanner } from "@/components/exam/ConnectionStatusBanner";
import { CountdownPanel } from "@/components/exam/CountdownPanel";
import { CueCardPanel } from "@/components/exam/CueCardPanel";
import { PTTButton } from "@/components/exam/PTTButton";
import { confirmVideoUpload, getVideoUploadUrl, uploadVideoBlob, wsBaseUrl } from "@/lib/api";
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

  const [log, setLog] = useState<string[]>([]);
  const socketRef = useRef<ExamSocketClient | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const workletNodeRef = useRef<AudioWorkletNode | null>(null);
  const jitterBufferRef = useRef<PlaybackJitterBuffer | null>(null);
  const proctoringRef = useRef<ProctoringRecorder | null>(null);
  const pttActiveRef = useRef(false);

  const appendLog = (line: string) => setLog((prev) => [...prev.slice(-19), line]);

  useEffect(() => {
    pttActiveRef.current = isPttActive;
  }, [isPttActive]);

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

      jitterBufferRef.current = new PlaybackJitterBuffer(audioContext, GEMINI_OUTPUT_SAMPLE_RATE_HZ);

      const socket = new ExamSocketClient({
        sessionId,
        token,
        wsBaseUrl: wsBaseUrl(),
        onServerMessage: (message) => {
          if (message.type === "connected") {
            setConnectionStatus("connected");
            appendLog(`connected to session ${message.session_id}`);
          } else if (message.type === "resumed") {
            setConnectionStatus("connected");
            appendLog(`resumed session ${message.session_id} (Gemini context restored)`);
          } else if (message.type === "activity_start_ack") {
            setLastTurnId(message.turn_id);
            appendLog(`turn started: ${message.turn_id}`);
          } else if (message.type === "turn_complete") {
            appendLog(
              `turn complete: ${message.turn_id} (${message.byte_size} bytes, checksum ${message.checksum.slice(0, 12)}...)`
            );
          } else if (message.type === "transcript_delta") {
            appendLog(`examiner: ${message.text}`);
          } else if (message.type === "gemini_turn_complete") {
            appendLog("examiner turn complete");
          } else if (message.type === "interrupted") {
            appendLog("examiner turn interrupted");
          } else if (message.type === "server_going_away") {
            appendLog(`server going away in ${message.time_left_ms ?? "?"}ms — expect a reconnect`);
          } else if (message.type === "cue_card") {
            setCueCard({
              cueCardId: message.cue_card_id,
              topic: message.topic,
              bullets: message.bullets,
            });
            appendLog(`cue card presented: ${message.topic}`);
          } else if (message.type === "timer_deadline") {
            setTimerDeadline({ name: message.name, deadlineEpochMs: message.deadline_epoch_ms });
            appendLog(`timer started: ${message.name}`);
          } else if (message.type === "scripted_audio") {
            appendLog(`scripted audio cue: ${message.asset}`);
          }
        },
        onAudioFrame: (frame) => jitterBufferRef.current?.enqueue(frame),
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
      appendLog(`setup failed: ${String(error)}`);
    });

    return () => {
      cancelled = true;
      socketRef.current?.close();
      workletNodeRef.current?.port.close();
      void audioContextRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  const handlePress = () => {
    setPttActive(true);
    socketRef.current?.activityStart();
    proctoringRef.current?.start();
  };

  const handleRelease = async () => {
    setPttActive(false);
    socketRef.current?.activityEnd();

    const token = sessionStorage.getItem("ielts_access_token");
    const blob = await proctoringRef.current?.stop();
    if (token && blob && blob.size > 0) {
      const { uploadUrl } = await getVideoUploadUrl(token, sessionId);
      await uploadVideoBlob(uploadUrl, blob);
      await confirmVideoUpload(token, sessionId);
      appendLog(`proctoring clip uploaded (${blob.size} bytes)`);
    }
  };

  return (
    <main style={{ padding: "2rem", fontFamily: "sans-serif" }}>
      <h1>Exam Room — Session {sessionId}</h1>

      <ConnectionStatusBanner status={connectionStatus} />

      <p>Status: {connectionStatus}</p>
      {lastTurnId && <p>Last turn: {lastTurnId}</p>}

      {cueCard && (
        <div style={{ marginTop: "1rem" }}>
          <CueCardPanel cueCard={cueCard} />
        </div>
      )}

      {timerDeadline && (
        <div style={{ marginTop: "1rem" }}>
          <CountdownPanel timerDeadline={timerDeadline} />
        </div>
      )}

      <div style={{ marginTop: "1.5rem" }}>
        <PTTButton
          active={isPttActive}
          disabled={connectionStatus !== "connected"}
          onPress={handlePress}
          onRelease={() => void handleRelease()}
        />
      </div>

      <pre style={{ marginTop: "2rem", background: "#111", color: "#0f0", padding: "1rem" }}>
        {log.join("\n")}
      </pre>
    </main>
  );
}
