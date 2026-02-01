export type AudioServiceMode = "voice" | "text";

export type AudioServiceState =
  | "idle"
  | "starting"
  | "ready"
  | "recording"
  | "playing"
  | "stopping"
  | "error";

export type NekoWsIncomingJson =
  | { type: "session_preparing"; input_mode?: "audio" | "text" | string }
  | { type: "session_started"; input_mode?: "audio" | "text" | string }
  | { type: "user_activity"; interrupted_speech_id?: string | null }
  | { type: "audio_chunk"; speech_id?: string | null }
  | Record<string, unknown>;

export type NekoWsOutgoingJson =
  | { action: "ping" }
  | { action: "start_session"; input_type: "audio" | "text"; new_session?: boolean }
  | { action: "pause_session" }
  | { action: "end_session" }
  | { action: "stream_data"; input_type: "audio" | "text" | "screen" | "camera"; data: any }
  | Record<string, unknown>;

export type RealtimeUnsubscribe = () => void;

export interface RealtimeClientLike {
  send: (data: any) => void;
  sendJson: (value: unknown) => void;
  on(event: "json", handler: (payload: { json: unknown; text: string }) => void): RealtimeUnsubscribe;
  on(event: "binary", handler: (payload: { data: unknown }) => void): RealtimeUnsubscribe;
  on(event: "open", handler: () => void): RealtimeUnsubscribe;
  on(event: "close", handler: (payload: { event?: any }) => void): RealtimeUnsubscribe;
}

export interface OggOpusStreamDecoder {
  /**
   * 流式解码一段 OGG/OPUS 数据。
   * - 返回 null 表示数据不足（等待更多 chunk）
   */
  decode: (chunk: Uint8Array) => Promise<{ float32Data: Float32Array; sampleRate: number } | null>;
  reset: () => void | Promise<void>;
}

export interface AudioServiceEvents {
  state: { state: AudioServiceState };
  /**
   * 输出振幅（0~1）——用于口型同步
   */
  outputAmplitude: { amplitude: number };
  /**
   * 输入振幅（0~1）——用于“麦克风无声音”检测或 UI
   */
  inputAmplitude: { amplitude: number };
}

export interface AudioService {
  attach: () => void;
  detach: () => void;

  startVoiceSession: (args?: {
    /**
     * Web: 传给 getUserMedia 的 deviceId；RN: 可忽略（交由权限/系统选择）
     */
    microphoneDeviceId?: string | null;
    timeoutMs?: number;
    /**
     * Mobile 侧通常要求 16kHz，上层可覆盖
     */
    targetSampleRate?: number;
  }) => Promise<void>;
  stopVoiceSession: () => Promise<void>;

  /**
   * 清空播放队列并停止当前播放（用于打断/重置）。
   */
  stopPlayback: () => void;
}

