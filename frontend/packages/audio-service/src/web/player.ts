import { TinyEmitter } from "@project_neko/common";
import type { AudioServiceEvents, OggOpusStreamDecoder } from "../types";

function isOggOpus(arrayBuffer: ArrayBuffer): boolean {
  if (arrayBuffer.byteLength < 4) return false;
  const header = new Uint8Array(arrayBuffer, 0, 4);
  // "OggS"
  return header[0] === 0x4f && header[1] === 0x67 && header[2] === 0x67 && header[3] === 0x53;
}

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n));
}

export class WebAudioChunkPlayer {
  private audioContext: AudioContext | null = null;
  private analyser: AnalyserNode | null = null;
  private queue: AudioBuffer[] = [];
  private scheduledSources: AudioBufferSourceNode[] = [];
  private isPlaying = false;
  private nextChunkTime = 0;
  private scheduleTimer: any = null;
  private amplitudeRaf: number | null = null;

  constructor(
    private emitter: TinyEmitter<AudioServiceEvents>,
    private opts: {
      decoder?: OggOpusStreamDecoder | null;
      scheduleAheadTimeSec?: number;
      scheduleIntervalMs?: number;
      analyserFftSize?: number;
      /**
       * focus mode：播放中是否对外暴露“playing”状态由上层控制；
       * 这里仅维护内部 isPlaying。
       */
    } = {}
  ) {}

  getPlaying() {
    return this.isPlaying;
  }

  private ensureContext(): AudioContext {
    if (!this.audioContext) {
      const Ctor = (globalThis as any).AudioContext || (globalThis as any).webkitAudioContext;
      this.audioContext = new Ctor();
    }
    return this.audioContext!;
  }

  private ensureAnalyser(): AnalyserNode {
    const ctx = this.ensureContext();
    if (!this.analyser) {
      const analyser = ctx.createAnalyser();
      analyser.fftSize = this.opts.analyserFftSize ?? 2048;
      analyser.connect(ctx.destination);
      this.analyser = analyser;
    }
    return this.analyser!;
  }

  private startAmplitudeLoop() {
    if (this.amplitudeRaf !== null) return;
    const analyser = this.ensureAnalyser();
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
        this.emitter.emit("outputAmplitude", { amplitude });
      } catch (_e) {
        // ignore
      }
      this.amplitudeRaf = requestAnimationFrame(loop);
    };

    this.amplitudeRaf = requestAnimationFrame(loop);
  }

  private stopAmplitudeLoop() {
    if (this.amplitudeRaf === null) return;
    cancelAnimationFrame(this.amplitudeRaf);
    this.amplitudeRaf = null;
    // 关闭嘴巴：输出 0
    this.emitter.emit("outputAmplitude", { amplitude: 0 });
  }

  private startScheduleLoop() {
    if (this.scheduleTimer) return;
    const intervalMs = this.opts.scheduleIntervalMs ?? 25;
    const loop = () => {
      this.scheduleTimer = setTimeout(loop, intervalMs);
      this.schedule();
    };
    loop();
  }

  private stopScheduleLoop() {
    if (!this.scheduleTimer) return;
    clearTimeout(this.scheduleTimer);
    this.scheduleTimer = null;
  }

  private schedule() {
    if (!this.audioContext) return;
    const ctx = this.audioContext;
    const scheduleAhead = this.opts.scheduleAheadTimeSec ?? 5;
    const analyser = this.ensureAnalyser();

    while (this.nextChunkTime < ctx.currentTime + scheduleAhead) {
      if (this.queue.length === 0) break;
      const buffer = this.queue.shift()!;
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(analyser);

      const startAt = this.nextChunkTime;
      source.start(startAt);

      source.onended = () => {
        const idx = this.scheduledSources.indexOf(source);
        if (idx >= 0) this.scheduledSources.splice(idx, 1);
        if (this.scheduledSources.length === 0 && this.queue.length === 0) {
          this.isPlaying = false;
          this.stopAmplitudeLoop();
          this.emitter.emit("state", { state: "ready" });
        }
      };

      this.scheduledSources.push(source);
      this.nextChunkTime += buffer.duration;

      if (!this.isPlaying) {
        this.isPlaying = true;
        this.startAmplitudeLoop();
        this.emitter.emit("state", { state: "playing" });
      }
    }
  }

  async enqueueBinary(blobOrBuffer: Blob | ArrayBuffer | Uint8Array) {
    const ctx = this.ensureContext();
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch (_e) {
        // ignore
      }
    }

    const toArrayBuffer = async (input: Blob | ArrayBuffer | Uint8Array): Promise<ArrayBuffer> => {
      if (typeof Blob !== "undefined" && input instanceof Blob) {
        return await input.arrayBuffer();
      }
      if (input instanceof Uint8Array) {
        const ab = new ArrayBuffer(input.byteLength);
        new Uint8Array(ab).set(input);
        return ab;
      }
      return input as ArrayBuffer;
    };

    const arrayBuffer = await toArrayBuffer(blobOrBuffer);
    if (!arrayBuffer || arrayBuffer.byteLength === 0) return;

    let float32Data: Float32Array | null = null;
    let sampleRate = 48000;

    if (isOggOpus(arrayBuffer)) {
      const decoder = this.opts.decoder;
      if (!decoder) {
        throw new Error("OGG/OPUS audio received but no decoder is configured.");
      }
      const result = await decoder.decode(new Uint8Array(arrayBuffer));
      if (!result) return; // 数据不足，等待更多 chunk
      float32Data = result.float32Data as unknown as Float32Array;
      sampleRate = result.sampleRate || 48000;
    } else {
      // PCM Int16 -> Float32
      const int16 = new Int16Array(arrayBuffer);
      if (int16.length === 0) return;
      const f32 = new Float32Array(int16.length);
      for (let i = 0; i < int16.length; i++) f32[i] = int16[i] / 32768;
      float32Data = f32;
      sampleRate = 48000;
    }

    if (!float32Data || float32Data.length === 0) return;

    const audioBuffer = ctx.createBuffer(1, float32Data.length, sampleRate);
    audioBuffer.copyToChannel(float32Data as unknown as Float32Array<ArrayBuffer>, 0);
    this.queue.push(audioBuffer);

    if (!this.isPlaying) {
      this.nextChunkTime = ctx.currentTime + 0.1;
      this.isPlaying = true;
      this.startScheduleLoop();
      this.startAmplitudeLoop();
      this.emitter.emit("state", { state: "playing" });
    } else {
      // 补调度，避免卡住
      this.schedule();
    }
  }

  stopPlayback(args?: { resetDecoder?: boolean }) {
    // 停止所有已调度 source
    for (const s of this.scheduledSources) {
      try {
        s.stop();
      } catch (_e) {}
    }
    this.scheduledSources = [];
    this.queue = [];
    this.isPlaying = false;
    this.nextChunkTime = 0;
    this.stopAmplitudeLoop();
    this.stopScheduleLoop();
    if (args?.resetDecoder) {
      try {
        this.opts.decoder?.reset();
      } catch (_e) {}
    }
    this.emitter.emit("state", { state: "ready" });
  }

  async close() {
    this.stopPlayback({ resetDecoder: false });
    const ctx = this.audioContext;
    this.audioContext = null;
    this.analyser = null;
    if (ctx && ctx.state !== "closed") {
      try {
        await ctx.close();
      } catch (_e) {}
    }
  }
}

