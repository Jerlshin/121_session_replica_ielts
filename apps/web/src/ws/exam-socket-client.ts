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
  | { type: "server_going_away"; time_left_ms: number | null };

export interface ExamSocketClientOptions {
  sessionId: string;
  token: string;
  wsBaseUrl: string; // e.g. ws://localhost:8000
  onServerMessage?: (message: ServerMessage) => void;
  onAudioFrame?: (frame: ArrayBuffer) => void;
  onClose?: () => void;
}

export class ExamSocketClient {
  private socket: WebSocket | null = null;

  constructor(private readonly options: ExamSocketClientOptions) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = `${this.options.wsBaseUrl}/ws/exam/${this.options.sessionId}?token=${encodeURIComponent(
        this.options.token
      )}`;
      const socket = new WebSocket(url);
      socket.binaryType = "arraybuffer";

      socket.onopen = () => resolve();
      socket.onerror = (event) => reject(event);
      socket.onclose = () => this.options.onClose?.();
      socket.onmessage = (event) => {
        if (typeof event.data === "string") {
          this.options.onServerMessage?.(JSON.parse(event.data) as ServerMessage);
        } else {
          this.options.onAudioFrame?.(event.data as ArrayBuffer);
        }
      };

      this.socket = socket;
    });
  }

  activityStart(): void {
    this.send({ type: "activity_start" });
  }

  activityEnd(): void {
    this.send({ type: "activity_end" });
  }

  sendAudioFrame(frame: ArrayBuffer): void {
    this.socket?.send(frame);
  }

  close(): void {
    this.socket?.close();
    this.socket = null;
  }

  private send(payload: unknown): void {
    this.socket?.send(JSON.stringify(payload));
  }
}
