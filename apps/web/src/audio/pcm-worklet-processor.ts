// Encodes captured mic audio to 16-bit PCM, little-endian, mono, framed at
// 20ms (320 samples) — the exact client -> Gemini contract from Spec 01
// §4.1, even though Phase 1 only echoes it back rather than bridging to
// Gemini. The AudioContext this runs under must be created with
// { sampleRate: 16000 } (see exam-socket-client usage) so no resampling
// happens here — this processor only frames and quantizes.
const FRAME_SAMPLES = 320;

class PCMWorkletProcessor extends AudioWorkletProcessor {
  private accumulator = new Int16Array(FRAME_SAMPLES);
  private writeIndex = 0;

  process(inputs: Float32Array[][]): boolean {
    const channelData = inputs[0]?.[0];
    if (!channelData) return true;

    for (let i = 0; i < channelData.length; i++) {
      const clamped = Math.max(-1, Math.min(1, channelData[i]));
      this.accumulator[this.writeIndex++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;

      if (this.writeIndex === FRAME_SAMPLES) {
        const frame = this.accumulator.slice().buffer;
        this.port.postMessage(frame, [frame]);
        this.writeIndex = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-worklet-processor", PCMWorkletProcessor);
