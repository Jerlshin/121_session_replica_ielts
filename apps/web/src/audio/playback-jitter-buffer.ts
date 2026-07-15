// Schedules incoming PCM16 frames back-to-back for gapless playback rather
// than starting each one at `currentTime` (which would overlap/glitch on
// jitter). Phase 1 echoes at 16kHz since there's no Gemini in the loop yet;
// Phase 2 will feed this the real 24kHz Gemini output deltas (Spec 01 §4.1).
export class PlaybackJitterBuffer {
  private context: AudioContext;
  private sampleRateHz: number;
  private nextPlayTime = 0;
  // Lazily-created passthrough tap: every scheduled source routes through
  // this analyser on its way to the destination instead of connecting
  // directly, so a UI layer (the voice blob, Spec/CLAUDE.md UI upgrade) can
  // read live examiner-output amplitude without touching playback timing or
  // audio content at all — analysers are silent, read-only observers.
  private analyserNode: AnalyserNode | null = null;

  constructor(context: AudioContext, sampleRateHz: number) {
    this.context = context;
    this.sampleRateHz = sampleRateHz;
  }

  enqueue(pcm16: ArrayBuffer): void {
    const samples = new Int16Array(pcm16);
    const floatSamples = new Float32Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      floatSamples[i] = samples[i] / (samples[i] < 0 ? 0x8000 : 0x7fff);
    }

    const buffer = this.context.createBuffer(1, floatSamples.length, this.sampleRateHz);
    buffer.copyToChannel(floatSamples, 0);

    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.getAnalyser());

    const startAt = Math.max(this.context.currentTime, this.nextPlayTime);
    source.start(startAt);
    this.nextPlayTime = startAt + buffer.duration;
  }

  /** Read-only amplitude tap for the examiner's live playback (UI-only, never
   * part of the audio graph's decision-making — CLAUDE.md rule 1). */
  getAnalyser(): AnalyserNode {
    if (!this.analyserNode) {
      this.analyserNode = this.context.createAnalyser();
      this.analyserNode.fftSize = 256;
      this.analyserNode.smoothingTimeConstant = 0.75;
      this.analyserNode.connect(this.context.destination);
    }
    return this.analyserNode;
  }

  reset(): void {
    this.nextPlayTime = 0;
  }
}
