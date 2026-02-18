// OGG OPUS 流式解码器 (WASM)
// 使用 @wasm-audio-decoders/ogg-opus-decoder
// https://github.com/eshaz/wasm-audio-decoders/tree/main/src/ogg-opus-decoder
// 库已在 index.html 中预加载，全局变量为 window["ogg-opus-decoder"]
// 从 app.js 中抽离的音频解码模块

// [Performance] 全局调试开关
window.DEBUG_AUDIO = typeof window.DEBUG_AUDIO !== 'undefined' ? window.DEBUG_AUDIO : false;
window.DEBUG_LIPSYNC = typeof window.DEBUG_LIPSYNC !== 'undefined' ? window.DEBUG_LIPSYNC : false;

let oggOpusDecoder = null;
let oggOpusDecoderReady = null;

// 安全的翻译函数，如果 window.t 不可用或翻译缺失则返回回退文本
function safeT(key, fallback, params) {
    if (!window.t) {
        console.error(`[safeT] window.t is not available, using fallback for key: ${key}`);
        return fallback;
    }
    try {
        const result = params ? window.t(key, params) : window.t(key);
        // 如果翻译结果等于 key 本身，说明翻译缺失，使用回退文本
        if (result === key) {
            console.error(`[safeT] Translation missing for key: ${key}, using fallback`);
            return fallback;
        }
        return result;
    } catch (e) {
        console.error(`[safeT] Error translating key: ${key}`, e);
        return fallback;
    }
}

async function getOggOpusDecoder() {
    if (oggOpusDecoder) return oggOpusDecoder;
    if (oggOpusDecoderReady) {
        try {
            const result = await oggOpusDecoderReady;
            if (result !== null) return result;
        } catch (e) {
            console.warn(safeT('console.oggOpusInitFailed', 'Ogg Opus decoder initialization failed'), e);
        }
        oggOpusDecoderReady = null;
    }

    oggOpusDecoderReady = (async () => {
        const module = window["ogg-opus-decoder"];
        if (!module || !module.OggOpusDecoder) {
            console.error(safeT('console.oggOpusNotLoaded', 'Ogg Opus decoder not loaded'));
            return null;
        }

        try {
            const decoder = new module.OggOpusDecoder();
            await decoder.ready;
            console.log(safeT('console.oggOpusReady', 'Ogg Opus decoder ready'));
            oggOpusDecoder = decoder;
            return decoder;
        } catch (e) {
            console.error(safeT('console.oggOpusCreateFailed', 'Failed to create Ogg Opus decoder'), e);
            return null;
        }
    })();

    try {
        const result = await oggOpusDecoderReady;
        if (result === null) oggOpusDecoderReady = null;
        return result;
    } catch (e) {
        // Promise reject 会"毒化缓存"，需要清空缓存允许重试
        oggOpusDecoderReady = null;
        oggOpusDecoder = null;
        console.warn(safeT('console.oggOpusInitRejected', 'Ogg Opus decoder initialization rejected'), e);
        return null;
    }
}

// 重置解码器（在新的音频流开始时调用）
// 使用 reset() 而非 free()：reset() 是为新的音频流做状态重置，实例仍可复用
async function resetOggOpusDecoder() {
    if (oggOpusDecoder) {
        try {
            // reset() 是异步的，用于重置解码器状态以处理新的音频流
            await oggOpusDecoder.reset();
        } catch (e) {
            console.warn(safeT('console.oggOpusResetFailed', 'Failed to reset Ogg Opus decoder'), e);
            oggOpusDecoder = null;
            oggOpusDecoderReady = null;
        }
    }
}

async function decodeOggOpusChunk(uint8Array) {
    const decoder = await getOggOpusDecoder();
    if (!decoder) {
        throw new Error('OGG OPUS 解码器不可用');
    }

    // decode() 用于流式解码
    const { channelData, samplesDecoded, sampleRate } = await decoder.decode(uint8Array);
    if (channelData && channelData[0] && channelData[0].length > 0) {
        return { float32Data: channelData[0], sampleRate: sampleRate || 48000 };
    }
    return null; // 数据不足，等待更多
}
