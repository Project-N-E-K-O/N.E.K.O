import type { NekoWsIncomingJson } from "./types";

export type SpeechInterruptDecision =
  | { kind: "noop" }
  | { kind: "drop_next_binary" }
  | { kind: "allow_binary" }
  | { kind: "reset_decoder" };

/**
 * 复刻旧版 static/app.js 的 speech_id 精确打断控制：
 * - user_activity 会携带 interrupted_speech_id，表示“上一轮 speech 被用户打断”
 * - audio_chunk 会携带 speech_id，作为后续二进制 blob 的“头”
 * - 当确认进入新的 speech_id 时，重置流式解码器（避免丢失新头信息）
 */
export class SpeechInterruptController {
  private interruptedSpeechId: string | null = null;
  private currentPlayingSpeechId: string | null = null;
  private pendingDecoderReset = false;
  private skipNextBinary = false;

  getSkipNextBinary() {
    return this.skipNextBinary;
  }

  /**
   * user_activity: 清空播放队列但不重置解码器；等待新 speech_id 再重置。
   */
  onUserActivity(interruptedSpeechId?: string | null): SpeechInterruptDecision[] {
    this.interruptedSpeechId = interruptedSpeechId || null;
    this.pendingDecoderReset = true;
    this.skipNextBinary = false;
    return [{ kind: "noop" }];
  }

  /**
   * audio_chunk: 根据 speech_id 决定丢弃/允许二进制，以及是否重置解码器。
   */
  onAudioChunk(speechId?: string | null): SpeechInterruptDecision[] {
    const decisions: SpeechInterruptDecision[] = [];

    const sid = typeof speechId === "string" && speechId ? speechId : null;
    if (sid && this.interruptedSpeechId && sid === this.interruptedSpeechId) {
      // 被打断的旧音频：丢弃后续二进制
      this.skipNextBinary = true;
      decisions.push({ kind: "drop_next_binary" });
      return decisions;
    }

    // 新一轮 speech：此时重置解码器
    if (sid && sid !== this.currentPlayingSpeechId) {
      if (this.pendingDecoderReset) {
        decisions.push({ kind: "reset_decoder" });
        this.pendingDecoderReset = false;
      }
      this.currentPlayingSpeechId = sid;
      this.interruptedSpeechId = null;
    }

    this.skipNextBinary = false;
    decisions.push({ kind: "allow_binary" });
    return decisions;
  }

  reset() {
    this.interruptedSpeechId = null;
    this.currentPlayingSpeechId = null;
    this.pendingDecoderReset = false;
    this.skipNextBinary = false;
  }

  static fromIncomingJson(json: unknown): NekoWsIncomingJson {
    if (!json || typeof json !== "object") return {} as any;
    return json as any;
  }
}

