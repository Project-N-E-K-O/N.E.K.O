/**
 * Shared voice display helpers for pages that render registered or native voices.
 *
 * Dependencies: optional window.t i18n function. Include this script before page
 * scripts that use VoiceDisplayUtils.
 */
// eslint-disable-next-line no-unused-vars
const VoiceDisplayUtils = (() => {
    const PROVIDER_SHORT = Object.freeze({
        cosyvoice: 'CosyVoice',
        cosyvoice_intl: 'CosyVoice Intl',
        minimax: 'MiniMax',
        minimax_intl: 'MiniMax Intl',
        elevenlabs: 'ElevenLabs',
        gptsovits: 'GPT-SoVITS',
        gemini: 'Gemini',
        step: 'StepFun',
        grok: 'Grok',
        mimo: 'MiMo',
        vllm_omni: 'vLLM-Omni',
    });

    function t(key, fallback) {
        if (window.t) {
            const translated = window.t(key);
            if (translated && translated !== key) return translated;
        }
        return fallback;
    }

    function normalizeProvider(provider) {
        return String(provider || '').trim();
    }

    function isKnownProvider(provider, options = {}) {
        const normalized = normalizeProvider(provider);
        if (!normalized) return false;
        if (normalized === 'local') return true;
        if (options.includeFree !== false && normalized === 'free') return true;
        return Object.prototype.hasOwnProperty.call(PROVIDER_SHORT, normalized);
    }

    function providerShortName(provider, options = {}) {
        const normalized = normalizeProvider(provider);
        if (!normalized) {
            return t(options.unknownKey || 'voice.providerUnknown', options.unknownFallback || 'Other');
        }
        if (normalized === 'local') {
            return t(options.localKey || 'voice.providerLocal', options.localFallback || 'Local CosyVoice');
        }
        if (normalized === 'free') {
            return t(options.freeKey || 'voice.providerFree', options.freeFallback || 'Free');
        }
        return PROVIDER_SHORT[normalized] || normalized;
    }

    function nativeVoiceDisplayName(voiceId, voiceData, options = {}) {
        const id = String(voiceId || '').trim();
        if (id) {
            const translated = t((options.nativeVoiceKeyPrefix || 'voice.nativeVoice.') + id, '');
            if (translated) return translated;
        }
        if (voiceData && voiceData.prefix) return voiceData.prefix;
        if (voiceData && voiceData.display_name) return voiceData.display_name;
        return id;
    }

    return {
        t,
        isKnownProvider,
        providerShortName,
        nativeVoiceDisplayName,
    };
})();
