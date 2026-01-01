import { TinyEmitter } from "../emitter";
import type { AudioServiceEvents, RealtimeClientLike } from "../types";
import { getAudioProcessorWorkletUrl } from "./workletModule";

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n));
}

export class WebMicStreamer {
  private audioContext: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private stream: MediaStream | null = null;
  private inputAnalyser: AnalyserNode | null = null;
  private inputRaf: number | null = null;
  private recording = false;

  constructor(
    private emitter: TinyEmitter<AudioServiceEvents>,
    private client: RealtimeClientLike,
    private opts: {
      /**
       * focus-mode：当播放中时是否跳过回传（由上层传入）
       */
      shouldSendFrame?: () => boolean;
    } = {}
  ) {}

  getRecording() {
    return this.recording;
  }

  private ensureContext(): AudioContext {
    if (!this.audioContext) {
      const Ctor = (globalThis as any).AudioContext || (globalThis as any).webkitAudioContext;
      this.audioContext = new Ctor({ sampleRate: 48000 });
    }
    return this.audioContext!;
  }

  private startInputAmplitudeLoop() {
    if (this.inputRaf !== null) return;
    const analyser = this.inputAnalyser;
    if (!analyser) return;
    const dataArray = new Uint8Array(analyser.fftSize);
    const loop = () => {
      try {
        analyser.getByteTimeDomainData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          const v = (dataArray[i] - 128) / 128;
          sum += v * v;
        }
        const rms = Math.sqrt(sum / dataArray.length);
        const amplitude = clamp01(rms * 8);
        this.emitter.emit("inputAmplitude", { amplitude });
      } catch (_e) {}
      this.inputRaf = requestAnimationFrame(loop);
    };
    this.inputRaf = requestAnimationFrame(loop);
  }

  private stopInputAmplitudeLoop() {
    if (this.inputRaf === null) return;
    cancelAnimationFrame(this.inputRaf);
    this.inputRaf = null;
    this.emitter.emit("inputAmplitude", { amplitude: 0 });
  }

  async start(args?: { microphoneDeviceId?: string | null; targetSampleRate?: number }) {
    // 清理旧上下文（避免多 worklet 并发导致 QPS 超限）
    await this.stop();

    const ctx = this.ensureContext();
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch (_e) {}
    }

    const baseAudioConstraints: MediaTrackConstraints = {
      noiseSuppression: false,
      echoCancellation: true,
      autoGainControl: true,
      channelCount: 1,
    };

    const constraints: MediaStreamConstraints = {
      audio: args?.microphoneDeviceId
        ? { ...baseAudioConstraints, deviceId: { exact: args.microphoneDeviceId } as any }
        : baseAudioConstraints,
    };

    this.stream = await navigator.mediaDevices.getUserMedia(constraints);
    const source = ctx.createMediaStreamSource(this.stream);

    // 输入振幅监测
    this.inputAnalyser = ctx.createAnalyser();
    this.inputAnalyser.fftSize = 2048;
    this.inputAnalyser.smoothingTimeConstant = 0.8;
    source.connect(this.inputAnalyser);
    this.startInputAmplitudeLoop();

    // worklet
    const workletUrl = getAudioProcessorWorkletUrl();
    await ctx.audioWorklet.addModule(workletUrl);

    const targetSampleRate = args?.targetSampleRate ?? 48000;
    this.workletNode = new AudioWorkletNode(ctx, "audio-processor", {
      processorOptions: {
        originalSampleRate: ctx.sampleRate,
        targetSampleRate,
      },
    });

    this.workletNode.port.onmessage = (event: MessageEvent) => {
      const audioData = event.data as Int16Array;
      if (!audioData) return;

      if (this.opts.shouldSendFrame && !this.opts.shouldSendFrame()) return;

      // 与旧版协议一致：stream_data + input_type=audio + data 为 number[]
      try {
        this.client.sendJson({
          action: "stream_data",
          data: Array.from(audioData as any),
          input_type: "audio",
        });
      } catch (_e) {
        // ignore
      }
    };

    source.connect(this.workletNode);

    this.recording = true;
    this.emitter.emit("state", { state: "recording" });
  }

  async stop() {
    this.recording = false;
    this.stopInputAmplitudeLoop();

    if (this.stream) {
      try {
        this.stream.getTracks().forEach((t: MediaStreamTrack) => t.stop());
      } catch (_e) {}
      this.stream = null;
    }

    if (this.workletNode) {
      try {
        this.workletNode.port.onmessage = null;
      } catch (_e) {}
      this.workletNode = null;
    }

    this.inputAnalyser = null;

    if (this.audioContext) {
      const ctx = this.audioContext;
      this.audioContext = null;
      if (ctx.state !== "closed") {
        try {
          await ctx.close();
        } catch (_e) {}
      }
    }

    this.emitter.emit("state", { state: "ready" });
  }
}

