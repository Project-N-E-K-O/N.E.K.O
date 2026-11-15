/**
 * text-mouth-driver.js — 串门台词「按估时放出」时的文本口型（docs/design/
 * visit-infrastructure.md §4.5 visit_line_delta、§5 PR-13；OD-15 v3）。
 *
 * 自家猫娘的一行台词有两种放法：按已播音频（`paced:'audio'`，嘴型由播放器的
 * RMS 口型驱动）或按文本估时（`paced:'estimate'`：串门语音关，或本场已走 TTS
 * 兜底——此时 `visitVoiceEnabled` 仍为 true 但这行没有音频）。判据只看本机
 * `visit_line_delta{speaker.self:true}` 每片带的 `paced`，不看持久设置。
 *
 * 估时片到达时按 `estimateSpeechMs`（与 utils/visit_wire.py::estimate_speech_ms
 * 同一公式，tests/unit/test_visit_estimate_ms_parity.py 钉住两边逐值相等）延长
 * 一段 8~10 Hz 的开合（每个开合周期幅度 0.35~0.8 随机，结尾 250 ms 衰减到 0），
 * 经 `LanLan1.setMouth` 写嘴型。与 RMS 互斥：`appState.lipSyncActive === true`
 * 时不接管、已在跑的立即让出。排帧只用 `nekoFramePacing.requestPacedFrame`
 * （Pet 窗帧率总闸；本目录禁 rAF）。只驱动 Live2D；VRM / MMD / PNGTuber 估时
 * 口型是 follow-up。本脚本只在 index.html 加载。
 */
(function () {
    'use strict';

    if (window.nekoVisitTextMouth) return;

    // 与 config/visit_settings.py 的同名常量逐值一致（parity 测试读两边源码比对）
    var VISIT_CJK_MS_PER_CHAR = 180;
    var VISIT_LATIN_MS_PER_WORD = 250;
    var VISIT_PUNCT_END_MS = 250;
    var VISIT_PUNCT_COMMA_MS = 120;
    var VISIT_CLAUSE_MIN_MS = 400;
    var VISIT_CLAUSE_MAX_MS = 12000;
    var END_MARKS = '。！？；…!?;.';
    var COMMA_MARKS = '，,、';
    var WORD_JOINERS = '.,\'’';
    var ALNUM_RE = /^[\p{L}\p{N}]$/u;

    var MOUTH_MIN_HZ = 8;
    var MOUTH_MAX_HZ = 10;
    var MOUTH_MIN_AMP = 0.35;
    var MOUTH_MAX_AMP = 0.8;
    var MOUTH_DECAY_MS = 250;

    function isCjkChar(ch) {
        // utils/cjk.py::is_cjk_char：Han / Kana（含半角片假名）/ Hangul 音节
        var c = ch.codePointAt(0);
        return (c >= 0x4E00 && c <= 0x9FFF) ||
            (c >= 0x3040 && c <= 0x30FF) ||
            (c >= 0xFF66 && c <= 0xFF9F) ||
            (c >= 0xAC00 && c <= 0xD7AF);
    }

    function isAlnum(ch) {
        return ALNUM_RE.test(ch);
    }

    /** 估计朗读时长（ms），夹在 [VISIT_CLAUSE_MIN_MS, VISIT_CLAUSE_MAX_MS]。 */
    function estimateSpeechMs(text) {
        var chars = Array.from(String(text == null ? '' : text));
        var cjk = 0;
        var words = 0;
        var ends = 0;
        var commas = 0;
        var inWord = false;
        var prev = '';
        for (var idx = 0; idx < chars.length; idx++) {
            var ch = chars[idx];
            var next = idx + 1 < chars.length ? chars[idx + 1] : '';
            if (isCjkChar(ch)) {
                cjk += 1;
                inWord = false;
                prev = '';
            } else if (isAlnum(ch)) {
                if (!inWord) {
                    words += 1;
                    inWord = true;
                }
                prev = '';
            } else if (WORD_JOINERS.indexOf(ch) !== -1 && inWord && next &&
                isAlnum(next) && !isCjkChar(next)) {
                continue; // 词内连接符：3.14 / 1,000 / don't
            } else if (END_MARKS.indexOf(ch) !== -1) {
                inWord = false;
                if (prev !== 'end') ends += 1;
                prev = 'end';
            } else if (COMMA_MARKS.indexOf(ch) !== -1) {
                inWord = false;
                if (prev !== 'comma') commas += 1;
                prev = 'comma';
            } else {
                inWord = false;
                prev = '';
            }
        }
        var ms = VISIT_CJK_MS_PER_CHAR * cjk + VISIT_LATIN_MS_PER_WORD * words +
            VISIT_PUNCT_END_MS * ends + VISIT_PUNCT_COMMA_MS * commas;
        return Math.trunc(Math.min(VISIT_CLAUSE_MAX_MS, Math.max(VISIT_CLAUSE_MIN_MS, ms)));
    }

    var endAt = 0;
    var cancelFrame = null;
    var lineId = null;
    var cycleStart = 0;
    var cyclePeriod = 0;
    var cycleAmp = 0;

    function now() {
        return (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    }

    function rmsActive() {
        var S = window.appState;
        return !!(S && S.lipSyncActive === true);
    }

    function live2dTarget() {
        var player = window.appAudioPlayback;
        if (player && typeof player.getActiveAvatarModelType === 'function') {
            var type = player.getActiveAvatarModelType();
            if (type && type !== 'live2d') return null;
        }
        var lanlan = window.LanLan1;
        if (!lanlan || typeof lanlan.setMouth !== 'function' || !lanlan.live2dModel) return null;
        return lanlan;
    }

    function pacer() {
        var pacing = window.nekoFramePacing;
        return pacing && typeof pacing.requestPacedFrame === 'function' ? pacing : null;
    }

    function canDrive() {
        return !rmsActive() && !!live2dTarget() && !!pacer();
    }

    function stop(resetMouth) {
        if (cancelFrame) {
            try { cancelFrame(); } catch (_) { /* noop */ }
            cancelFrame = null;
        }
        endAt = 0;
        lineId = null;
        var target = live2dTarget();
        if (resetMouth && target && !rmsActive()) target.setMouth(0);
    }

    function newCycle(t) {
        var hz = MOUTH_MIN_HZ + Math.random() * (MOUTH_MAX_HZ - MOUTH_MIN_HZ);
        cycleStart = t;
        cyclePeriod = 1000 / hz;
        cycleAmp = MOUTH_MIN_AMP + Math.random() * (MOUTH_MAX_AMP - MOUTH_MIN_AMP);
    }

    function frame() {
        cancelFrame = null;
        if (rmsActive()) {
            // RMS 口型接管：让出，不写 0 免得跟它抢
            endAt = 0;
            lineId = null;
            return;
        }
        var target = live2dTarget();
        var pacing = pacer();
        var t = now();
        if (!target || !pacing || t >= endAt) {
            stop(true);
            return;
        }
        if (t >= cycleStart + cyclePeriod) newCycle(t);
        var remaining = endAt - t;
        var envelope = remaining < MOUTH_DECAY_MS ? remaining / MOUTH_DECAY_MS : 1;
        var phase = (t - cycleStart) / cyclePeriod;
        target.setMouth(Math.max(0, cycleAmp * Math.sin(Math.PI * phase) * envelope));
        cancelFrame = pacing.requestPacedFrame(frame);
    }

    function drive(text, line) {
        if (!canDrive()) return false;
        var t = now();
        endAt = Math.max(endAt, t) + estimateSpeechMs(text);
        lineId = line || lineId;
        if (!cancelFrame) {
            newCycle(t);
            cancelFrame = pacer().requestPacedFrame(frame);
        }
        return true;
    }

    function onFrame(msg) {
        if (!msg || typeof msg !== 'object') return;
        if (msg.type === 'visit_line_delta') {
            var speaker = msg.speaker || {};
            if (speaker.self !== true || msg.paced !== 'estimate') return;
            drive(msg.text, msg.line_id);
        } else if (msg.type === 'visit_line_abort') {
            if (lineId && msg.line_id === lineId) stop(true);
        } else if (msg.type === 'visit_state_change' && msg.action === 'ended') {
            if (cancelFrame) stop(true);
        }
    }

    window.addEventListener('neko-visit-frame', function (event) {
        onFrame(event && event.detail);
    });

    window.nekoVisitTextMouth = Object.freeze({
        estimateSpeechMs: estimateSpeechMs,
        isDriving: function () { return !!cancelFrame; }
    });
})();
