import { TinyEmitter } from "../emitter";
import { SpeechInterruptController } from "../protocol";
import type { AudioService, AudioServiceEvents, AudioServiceState, NekoWsIncomingJson, RealtimeClientLike } from "../types";
import { WebAudioChunkPlayer } from "./player";
import { WebMicStreamer } from "./mic";
import { createGlobalOggOpusDecoder } from "./oggOpusGlobalDecoder";

function nowMs() {
  return Date.now();
}

function withTimeout<T>(p: Promise<T>, ms: number, message: string): Promise<T> {
  if (!ms || ms <= 0) return p;
  let timer: any = null;
  const timeout = new Promise<T>((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  });
  return Promise.race([p, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

export function createWebAudioService(args: {
  client: RealtimeClientLike;
  /**
   * 是否启用 focus 模式：播放中自动不回传麦克风音频（避免“打断/回声”）
   */
  focusModeEnabled?: boolean;
  /**
   * 是否移动端（影响默认 targetSampleRate）
   */
  isMobile?: boolean;
  /**
   * OGG/OPUS 解码器：默认尝试使用旧版全局 `window["ogg-opus-decoder"]`
   */
  decoder?: "global" | null | { decode: any; reset: any };
}): AudioService & { on: TinyEmitter<AudioServiceEvents>["on"]; getState: () => AudioServiceState; setFocusMode: (enabled: boolean) => void } {
  const emitter = new TinyEmitter<AudioServiceEvents>();
  const interrupt = new SpeechInterruptController();

  let state: AudioServiceState = "idle";
  const setState = (next: AudioServiceState) => {
    if (state === next) return;
    state = next;
    emitter.emit("state", { state: next });
  };

  let focusModeEnabled = args.focusModeEnabled === true;

  const decoder =
    args.decoder === "global" || args.decoder === undefined ? createGlobalOggOpusDecoder() : (args.decoder as any) || null;

  const player = new WebAudioChunkPlayer(emitter, { decoder });
  const mic = new WebMicStreamer(emitter, args.client, {
    shouldSendFrame: () => {
      if (!focusModeEnabled) return true;
      return !player.getPlaying();
    },
  });

  let offs: (() => void)[] = [];
  let sessionResolver: ((mode?: string) => void) | null = null;
  let sessionPromiseCreatedAt = 0;

  const handleIncomingJson = (json: NekoWsIncomingJson) => {
    if (!json || typeof json !== "object") return;

    if ((json as any).type === "session_started") {
      if (sessionResolver) {
        const r = sessionResolver;
        sessionResolver = null;
        r((json as any).input_mode);
      }
      return;
    }

    if ((json as any).type === "user_activity") {
      // 精确打断控制：只清空播放队列，不重置解码器（避免丢头）
      interrupt.onUserActivity((json as any).interrupted_speech_id);
      player.stopPlayback({ resetDecoder: false });
      return;
    }

    if ((json as any).type === "audio_chunk") {
      const decisions = interrupt.onAudioChunk((json as any).speech_id);
      for (const d of decisions) {
        if (d.kind === "reset_decoder") {
          try {
            decoder?.reset?.();
          } catch (_e) {}
        }
      }
      return;
    }
  };

  const handleIncomingBinary = async (data: unknown) => {
    if (interrupt.getSkipNextBinary()) {
      // 与旧版一致：skip 标志由后续 audio_chunk 决定何时清除
      return;
    }

    // 仅处理 Blob/ArrayBuffer/TypedArray
    try {
      if (typeof Blob !== "undefined" && data instanceof Blob) {
        await player.enqueueBinary(data);
        return;
      }
      if (data instanceof ArrayBuffer) {
        await player.enqueueBinary(data);
        return;
      }
      if (data instanceof Uint8Array) {
        await player.enqueueBinary(data);
        return;
      }
      // RN/WebSocket polyfill 可能给到 { data: ArrayBuffer } 之类
      const anyData: any = data as any;
      if (anyData && anyData.buffer instanceof ArrayBuffer && typeof anyData.byteLength === "number") {
        await player.enqueueBinary(new Uint8Array(anyData.buffer, anyData.byteOffset || 0, anyData.byteLength));
      }
    } catch (_e) {
      // ignore（上层可通过 error 事件扩展）
    }
  };

  const attach = () => {
    if (offs.length) return;
    setState("ready");
    offs = [
      args.client.on("json", ({ json }) => handleIncomingJson(json as any)),
      args.client.on("binary", ({ data }) => void handleIncomingBinary(data)),
      args.client.on("close", () => {
        // 连接断开：复位等待中的 start_session
        if (sessionResolver) {
          const r = sessionResolver;
          sessionResolver = null;
          r("closed");
        }
      }),
    ];
  };

  const detach = () => {
    for (const off of offs) {
      try {
        off();
      } catch (_e) {}
    }
    offs = [];
    sessionResolver = null;
    interrupt.reset();
    player.stopPlayback({ resetDecoder: false });
    void mic.stop();
    setState("idle");
  };

  const waitSessionStarted = (timeoutMs: number) => {
    if (sessionResolver) {
      // 已经有一笔 pending，直接复用（避免并发 start）
      return withTimeout(
        new Promise<void>((resolve) => {
          const prev = sessionResolver!;
          sessionResolver = (mode) => {
            try {
              prev(mode);
            } finally {
              resolve();
            }
          };
        }),
        timeoutMs,
        "Session start timeout"
      );
    }

    sessionPromiseCreatedAt = nowMs();
    return withTimeout(
      new Promise<void>((resolve) => {
        sessionResolver = () => resolve();
      }),
      timeoutMs,
      `Session start timeout after ${timeoutMs}ms`
    ).finally(() => {
      // 防御：避免悬挂 resolver
      if (sessionResolver && nowMs() - sessionPromiseCreatedAt >= timeoutMs) {
        sessionResolver = null;
      }
    });
  };

  const startVoiceSession: AudioService["startVoiceSession"] = async (opts) => {
    attach();
    setState("starting");

    const timeoutMs = opts?.timeoutMs ?? 10_000;
    const targetSampleRate = opts?.targetSampleRate ?? (args.isMobile ? 16000 : 48000);

    // 并行：start_session + mic init（对齐旧版体验）
    const sessionP = waitSessionStarted(timeoutMs);
    try {
      args.client.sendJson({ action: "start_session", input_type: "audio" });
    } catch (_e) {
      // ignore
    }

    await Promise.all([
      sessionP,
      mic.start({
        microphoneDeviceId: opts?.microphoneDeviceId ?? null,
        targetSampleRate,
      }),
    ]);

    setState("recording");
  };

  const stopVoiceSession: AudioService["stopVoiceSession"] = async () => {
    setState("stopping");
    await mic.stop();
    try {
      args.client.sendJson({ action: "pause_session" });
    } catch (_e) {
      // ignore
    }
    setState("ready");
  };

  const stopPlayback: AudioService["stopPlayback"] = () => {
    interrupt.reset();
    player.stopPlayback({ resetDecoder: true });
  };

  return {
    attach,
    detach,
    startVoiceSession,
    stopVoiceSession,
    stopPlayback,
    on: emitter.on.bind(emitter),
    getState: () => state,
    setFocusMode: (enabled: boolean) => {
      focusModeEnabled = enabled;
    },
  };
}

