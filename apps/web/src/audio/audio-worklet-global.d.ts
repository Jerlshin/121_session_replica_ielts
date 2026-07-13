// Ambient declarations for the AudioWorkletGlobalScope. TypeScript's
// default DOM lib does not include these — they only exist inside the
// worklet's isolated realm, not the window/document realm.
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  constructor(options?: AudioWorkletNodeOptions);
  process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>
  ): boolean;
}

declare function registerProcessor(
  name: string,
  processorCtor: new (options?: AudioWorkletNodeOptions) => AudioWorkletProcessor
): void;
