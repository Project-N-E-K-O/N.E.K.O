import type { OggOpusStreamDecoder } from "../types";

const DEFAULT_SAMPLE_RATE = 48000;

type GlobalDecoderModule = {
  OggOpusDecoder: new () => {
    ready: Promise<void>;
    decode: (chunk: Uint8Array) => Promise<{
      channelData?: Float32Array[];
      samplesDecoded?: number;
      sampleRate?: number;
    }>;
    free: () => void;
  };
};

/**
 * 兼容旧版 `/static/libs/ogg-opus-decoder.min.js` 的全局加载方式：
 * - 全局变量名：`window["ogg-opus-decoder"]`
 *
 * React WebApp 若未加载该脚本，会在收到 OGG/OPUS 时抛错（上层可注入自定义 decoder）。
 */
export function createGlobalOggOpusDecoder(): OggOpusStreamDecoder {
  let decoder: any = null;
  let readyPromise: Promise<any> | null = null;

  const ensure = async () => {
    if (decoder) return decoder;
    if (readyPromise) return readyPromise;

    readyPromise = (async () => {
      const w: any = typeof window !== "undefined" ? (window as any) : undefined;
      const mod: GlobalDecoderModule | undefined = w?.["ogg-opus-decoder"];
      if (!mod || !mod.OggOpusDecoder) {
        throw new Error('Global OGG/OPUS decoder not found: window["ogg-opus-decoder"]');
      }
      const d = new mod.OggOpusDecoder();
      await d.ready;
      decoder = d;
      return d;
    })();

    return readyPromise;
  };

  return {
    decode: async (chunk) => {
      const d = await ensure();
      const { channelData, sampleRate } = await d.decode(chunk);
      if (channelData && channelData.length > 0 && channelData[0] && channelData[0].length > 0) {
        return { float32Data: channelData[0], sampleRate: sampleRate || DEFAULT_SAMPLE_RATE };
      }
      return null;
    },
    reset: () => {
      try {
        decoder?.free?.();
      } catch (_e) {
        // ignore
      }
      decoder = null;
      readyPromise = null;
    },
  };
}

