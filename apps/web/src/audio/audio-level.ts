// Pure read helper shared by any UI that wants a single 0..1 loudness
// number off a Web Audio AnalyserNode, without each caller re-deriving RMS
// math. Kept dependency-free and framework-agnostic (no React) so it stays
// usable from a plain rAF loop.
let scratch: Uint8Array<ArrayBuffer> | null = null;

export function readLevel(analyser: AnalyserNode): number {
  if (!scratch || scratch.length !== analyser.fftSize) {
    scratch = new Uint8Array(analyser.fftSize);
  }
  analyser.getByteTimeDomainData(scratch);

  let sumSquares = 0;
  for (let i = 0; i < scratch.length; i++) {
    const normalized = (scratch[i] - 128) / 128;
    sumSquares += normalized * normalized;
  }
  const rms = Math.sqrt(sumSquares / scratch.length);
  // RMS of ordinary speech rarely approaches 1.0; a small fixed gain keeps
  // the visualizer responsive without every caller hand-tuning this.
  return Math.min(1, rms * 3.2);
}

const bandScratch = new Map<AnalyserNode, Uint8Array<ArrayBuffer>>();

/** `count` normalized (0..1) frequency-band averages, low→high, for UI that
 * wants per-band geometry (e.g. mapping distinct blob control points to
 * distinct bands) rather than a single overall loudness number. */
export function readFrequencyBands(analyser: AnalyserNode, count: number): number[] {
  let buffer = bandScratch.get(analyser);
  if (!buffer || buffer.length !== analyser.frequencyBinCount) {
    buffer = new Uint8Array(analyser.frequencyBinCount);
    bandScratch.set(analyser, buffer);
  }
  analyser.getByteFrequencyData(buffer);

  // Voiced speech energy concentrates well below Nyquist; only sampling the
  // lower ~60% of bins avoids every band reading near-silent.
  const usableBins = Math.max(count, Math.floor(buffer.length * 0.6));
  const bandSize = Math.max(1, Math.floor(usableBins / count));
  const bands: number[] = [];
  for (let b = 0; b < count; b++) {
    let sum = 0;
    const start = b * bandSize;
    for (let i = start; i < start + bandSize; i++) sum += buffer[i] ?? 0;
    bands.push(Math.min(1, sum / bandSize / 255 / 0.6));
  }
  return bands;
}
