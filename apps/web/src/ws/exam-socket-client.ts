import { TurnBuffer } from "@/persistence/turn-buffer";

// Wire protocol against apps/api-gateway/app/routers/ws_exam.py: JSON text
// frames for control/caption/status messages, raw binary frames for PCM16
// audio in both directions. Since Phase 2 (Spec 04 §2), binary frames
// received from the server are Gemini's relayed audio deltas (24kHz, Spec
// 01 §4.1), not an echo of what the client sent.
export type ServerMessage =
  | { type: "connected"; session_id: string }
  | { type: "resumed"; session_id: string }
  | { type: "activity_start_ack"; turn_id: string }
  | { type: "turn_complete"; turn_id: string; byte_size: number; checksum: string }
  | { type: "transcript_delta"; text: string }
  | { type: "gemini_turn_complete" }
  | { type: "interrupted" }
  | { type: "server_going_away"; time_left_ms: number | null }
  | { type: "cue_card"; cue_card_id: string; topic: string; bullets: string[] }
  | { type: "scripted_audio"; asset: string }
  | { type: "timer_deadline"; name: string; deadline_epoch_ms: number }
  | { type: "pong"; client_ts: number; server_ts: number };

const PING_INTERVAL_MS = 10_000;
const RECONNECT_BASE_DELAY_MS = 500;
const RECONNECT_MAX_DELAY_MS = 8_000;

export interface ExamSocketClientOptions {
  sessionId: string;
  token: string;
  wsBaseUrl: string; // e.g. ws://localhost:8000
  onServerMessage?: (message: ServerMessage) => void;
  onAudioFrame?: (frame: ArrayBuffer) => void;
  /** A deliberate close() call — connection is gone for good. */
  onClose?: () => void;
  /** An unexpected drop that's about to be retried with backoff (Spec 01
   * §4.4/§5.3-adjacent UX, Spec 04 §2 Phase 8) — the UI's connection banner
   * should show "reconnecting", not "disconnected", for this. */
  onReconnecting?: () => void;
}

export class ExamSocketClient {
  private socket: WebSocket | null = null;
  private readonly turnBuffer = new TurnBuffer();
  private currentTurnId: string | null = null;
  private deliberateClose = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly options: ExamSocketClientOptions) {}

  connect(): Promise<void> {
    this.deliberateClose = false;
    return this.openSocket();
  }

  private openSocket(): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = `${this.options.wsBaseUrl}/ws/exam/${this.options.sessionId}?token=${encodeURIComponent(
        this.options.token
      )}`;
      const socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";

      socket.onopen = () => {
        this.reconnectAttempt = 0;
        this.startPing();
        void this.replayIncompleteTurn();
        resolve();
      };
      socket.onerror = (event) => reject(event);
      socket.onclose = () => {
        this.stopPing();
        if (this.deliberateClose) {
          this.options.onClose?.();
        } else {
          this.scheduleReconnect();
        }
      };
      socket.onmessage = (event) => {
        if (typeof event.data === "string") {
          this.handleServerMessage(JSON.parse(event.data) as ServerMessage);
        } else {
          this.options.onAudioFrame?.(event.data as ArrayBuffer);
        }
      };

      this.socket = socket;
    });
  }

  private handleServerMessage(message: ServerMessage): void {
    if (message.type === "pong") {
      this.send({ type: "rtt_report", rtt_ms: Date.now() - message.client_ts });
    } else if (message.type === "turn_complete" && this.currentTurnId) {
      void this.turnBuffer.completeTurn(this.currentTurnId);
      this.currentTurnId = null;
    }
    this.options.onServerMessage?.(message);
  }

  private scheduleReconnect(): void {
    this.options.onReconnecting?.();
    const delayMs = Math.min(
      RECONNECT_BASE_DELAY_MS * 2 ** this.reconnectAttempt,
      RECONNECT_MAX_DELAY_MS
    );
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.openSocket().catch(() => {
        // onclose fires for the failed attempt too and reschedules again.
      });
    }, delayMs);
  }

  private startPing(): void {
    this.pingTimer = setInterval(() => {
      this.send({ type: "client_ping", client_ts: Date.now() });
    }, PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer !== null) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }

  private async replayIncompleteTurn(): Promise<void> {
    const incomplete = await this.turnBuffer.takeIncompleteTurn();
    if (!incomplete || incomplete.frames.length === 0) return;
    // See turn-buffer.ts's module docstring: no backend partial-turn-resume
    // protocol exists, so this is replayed as a brand new turn rather than
    // spliced back in mid-stream. Goes through the normal public methods
    // (not a special-cased path), so if *this* replay itself gets
    // interrupted by another drop, it's durably re-buffered and will be
    // replayed again on the next reconnect.
    this.activityStart();
    for (const frame of incomplete.frames) {
      this.sendAudioFrame(frame);
    }
    this.activityEnd();
  }

  activityStart(): void {
    this.currentTurnId = crypto.randomUUID();
    this.turnBuffer.beginTurn(this.currentTurnId);
    this.send({ type: "activity_start" });
  }

  activityEnd(): void {
    this.send({ type: "activity_end" });
  }

  sendAudioFrame(frame: ArrayBuffer): void {
    void this.turnBuffer.appendFrame(frame);
    this.socket?.send(frame);
  }

  close(): void {
    this.deliberateClose = true;
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer);
    this.stopPing();
    this.socket?.close();
    this.socket = null;
  }

  private send(payload: unknown): void {
    this.socket?.send(JSON.stringify(payload));
  }
}
