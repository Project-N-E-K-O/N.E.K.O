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

// State belongs to one decoder generation. Retired work may finish, but cannot
// publish into the cache or parser state used by a replacement generation.
const OGG_OPUS_OPERATION_TIMEOUT_MS = 5000;
function createOggDecoderGeneration() {
    return { decoder: null, ready: null, chain: Promise.resolve(), work: Promise.resolve(),
        context: null, pages: new Uint8Array(), started: false, retired: false,
        cancelWork: null };
}
let oggDecoderGeneration = createOggDecoderGeneration();

function currentOggEpoch() {
    return window.appState ? window.appState.incomingAudioEpoch : 0;
}
function clearOggStreamContext(generation) {
    generation.context = null;
    generation.pages = new Uint8Array();
    generation.started = false;
}
function retireOggDecoderGeneration(generation) {
    if (generation.retired) return;
    generation.retired = true;
    // Wake the logical receive operation without freeing its live WASM owner.
    if (generation.cancelWork) generation.cancelWork();
    if (generation === oggDecoderGeneration) {
        oggDecoderGeneration = createOggDecoderGeneration();
        oggOpusDecoder = null;
        oggOpusDecoderReady = null;
    }
    // Do not free a WASM instance while decode/flush still owns it. Cleanup is
    // detached from replacement work; even a hung old operation cannot lock it.
    Promise.allSettled([generation.ready, generation.work]).then(async () => {
        if (generation.decoder) await generation.decoder.free();
        generation.decoder = null;
        clearOggStreamContext(generation);
    }).catch(error => console.warn('Ogg Opus retired decoder cleanup failed:', error));
}
function invalidateOggOpusDecoder() {
    retireOggDecoderGeneration(oggDecoderGeneration);
}
async function getOggOpusDecoder(generation = oggDecoderGeneration) {
    if (generation.retired) return null;
    if (generation.decoder && oggOpusDecoder === generation.decoder) return generation.decoder;
    if (!generation.ready) {
        generation.ready = (async () => {
            const module = window['ogg-opus-decoder'];
            if (!module || !module.OggOpusDecoder) {
                console.error(safeT('console.oggOpusNotLoaded', 'Ogg Opus decoder not loaded'));
                return null;
            }
            try {
                const decoder = new module.OggOpusDecoder();
                generation.decoder = decoder;
                await decoder.ready;
                if (generation.retired || generation !== oggDecoderGeneration) return null;
                oggOpusDecoder = decoder;
                console.log(safeT('console.oggOpusReady', 'Ogg Opus decoder ready'));
                return decoder;
            } catch (error) {
                console.warn(safeT('console.oggOpusInitFailed', 'Ogg Opus decoder initialization failed'), error);
                return null;
            }
        })();
        if (generation === oggDecoderGeneration) oggOpusDecoderReady = generation.ready;
    }
    const result = await generation.ready;
    if (result === null) retireOggDecoderGeneration(generation);
    return result;
}
function joinOggParts(parts, Type = Float32Array) {
    const joined = new Type(parts.reduce((n, part) => n + part.length, 0));
    let offset = 0;
    for (const part of parts) { joined.set(part, offset); offset += part.length; }
    return joined;
}
function queueOggOperation(fn, context) {
    const generation = oggDecoderGeneration;
    const alive = () => !generation.retired && generation === oggDecoderGeneration
        && context.epoch === currentOggEpoch();
    const task = generation.chain.then(async () => {
        if (!alive()) return null;
        let timer;
        let releaseCancellation;
        const cancellation = new Promise(resolve => { releaseCancellation = resolve; });
        generation.cancelWork = releaseCancellation;
        generation.work = Promise.resolve().then(() => fn(generation, alive));
        const timeout = new Promise((_, reject) => {
            timer = window.setTimeout(() => {
                const error = new Error('Ogg Opus operation timed out');
                error.name = 'TimeoutError';
                reject(error);
            }, OGG_OPUS_OPERATION_TIMEOUT_MS);
        });
        try {
            const result = await Promise.race([generation.work, timeout, cancellation]);
            return alive() ? result : null;
        } catch (error) {
            // Parser state is uncertain after rejection/timeout. New speech gets
            // a fresh generation; a late old callback only holds its old state.
            retireOggDecoderGeneration(generation);
            throw error;
        } finally {
            if (generation.cancelWork === releaseCancellation) generation.cancelWork = null;
            window.clearTimeout(timer);
        }
    });
    generation.chain = task.catch(() => {});
    return task;
}
async function resetOggOpusDecoder() {
    // Logical invalidation is immediate. Physical disposal waits for the owner.
    invalidateOggOpusDecoder();
}
async function decodeOggOpusChunk(bytes, options = {}) {
    const context = { epoch: options.epoch ?? currentOggEpoch(),
        speechId: options.speechId || null, playbackGain: options.playbackGain };
    return queueOggOperation(async (generation, alive) => {
        const decoder = await getOggOpusDecoder(generation);
        if (!alive()) return null;
        if (!decoder) throw new Error('Ogg Opus decoder unavailable');
        if (generation.context && (generation.context.epoch !== context.epoch
            || generation.context.speechId !== context.speechId)) {
            // Ownership changed without a normal close: discard the old tail.
            await decoder.reset();
            if (!alive()) return null;
            clearOggStreamContext(generation);
        }
        generation.context = context;
        generation.pages = joinOggParts([generation.pages, bytes], Uint8Array);
        const output = [];
        let cursor = 0;
        while (generation.pages.length - cursor >= 27) {
            const pages = generation.pages;
            if (pages[cursor] !== 79 || pages[cursor + 1] !== 103
                || pages[cursor + 2] !== 103 || pages[cursor + 3] !== 83) {
                throw new Error('Invalid Ogg page boundary');
            }
            const segments = pages[cursor + 26];
            if (pages.length - cursor < 27 + segments) break;
            let size = 27 + segments;
            for (let n = 0; n < segments; n++) size += pages[cursor + 27 + n];
            if (pages.length - cursor < size) break;
            const page = pages.subarray(cursor, cursor + size);
            if (page[5] & 2) {
                if (generation.started) {
                    // A new independent stream inside the same speech owner.
                    const tail = await decoder.flush();
                    if (!alive()) return null;
                    if (tail.errors?.length) throw new Error('Ogg Opus decoder returned errors');
                    output.push(tail.channelData[0] || new Float32Array());
                }
                generation.started = true;
            }
            const result = await decoder.decode(page);
            if (!alive()) return null;
            if (result.errors?.length) throw new Error('Ogg Opus decoder returned errors');
            output.push(result.channelData[0] || new Float32Array());
            cursor += size;
        }
        generation.pages = generation.pages.slice(cursor);
        const float32Data = joinOggParts(output);
        return float32Data.length ? { float32Data, sampleRate: 48000 } : null;
    }, context);
}
async function flushOggOpusDecoder(options) {
    return queueOggOperation(async (generation, alive) => {
        const context = generation.context;
        if (!context || options.epoch !== context.epoch
            || options.speechId !== context.speechId || !generation.started) return null;
        if (generation.pages.length) throw new Error('Audio ended inside an Ogg page');
        const decoder = await getOggOpusDecoder(generation);
        if (!alive()) return null;
        const result = await decoder.flush();
        if (!alive()) return null;
        if (result.errors?.length) throw new Error('Ogg Opus decoder returned errors');
        clearOggStreamContext(generation);
        const float32Data = result.channelData[0];
        return float32Data?.length ? { float32Data, sampleRate: result.sampleRate || 48000,
            playbackGain: context.playbackGain } : null;
    }, options);
}
