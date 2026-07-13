// Independent of the live PCM tap (CLAUDE.md rule 3): captures video+audio
// as WebM/Opus via MediaRecorder and hands back a single Blob for direct
// presigned upload to object storage. Never decoded, never sent over the
// exam WS, never touches the live inference loop.
export class ProctoringRecorder {
  private mediaRecorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];

  constructor(private readonly stream: MediaStream) {}

  start(): void {
    this.chunks = [];
    this.mediaRecorder = new MediaRecorder(this.stream, {
      mimeType: "video/webm;codecs=vp8,opus",
    });
    this.mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.chunks.push(event.data);
    };
    this.mediaRecorder.start();
  }

  stop(): Promise<Blob> {
    return new Promise((resolve) => {
      if (!this.mediaRecorder) {
        resolve(new Blob([], { type: "video/webm" }));
        return;
      }
      this.mediaRecorder.onstop = () => resolve(new Blob(this.chunks, { type: "video/webm" }));
      this.mediaRecorder.stop();
    });
  }
}
