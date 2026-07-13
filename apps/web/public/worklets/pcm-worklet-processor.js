// Hand-synced plain-JS build of src/audio/pcm-worklet-processor.ts.
// Next.js has no built-in AudioWorklet bundling, and `addModule()` needs a
// URL the browser can fetch directly — so this is what actually loads at
// runtime. Keep this in lockstep with the TS source; wiring an actual
// build step for it is a reasonable follow-up, not done here since Phase 1
// is scoped to proving the media plumbing works end to end.
const FRAME_SAMPLES = 320; // 20ms @ 16kHz mono (Spec 01 §4.1)

class PCMWorkletProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.accumulator = new Int16Array(FRAME_SAMPLES);
    this.writeIndex = 0;
  }

  process(inputs) {
    const channelData = inputs[0] && inputs[0][0];
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
