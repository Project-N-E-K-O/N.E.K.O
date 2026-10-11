/**
 * visit-pacer.js — 串门台词的已播音频进度回报（docs/design/visit-infrastructure.md
 * §4.5 visit_speech_progress、§5 PR-13；OD-15 v3 / OD-21 v3）。
 *
 * 后端按「这一行已经播了多少音频」放出字幕分片，按 `ended{final:true}` 收口一行。
 * 本脚本只在 index.html 加载（chat.html 不播串门语音），做三件事：
 *
 * 1. 监听播放器的 `neko-speech-playback-state`，按 speech_id 记下每个
 *    `chunk_scheduled` 块的真开播时刻与终点（同一 speech_id 的后续块只累加、
 *    不重置开播时刻），换算该 speech_id 已播的音频时长 `played_ms`；
 *    lookahead 提前排程的块在真开播之前不计入，所以 `played_ms` 不会超前。
 * 2. 播放期间约 4 Hz 回报 `{action:'visit_speech_progress', speech_id, visit_id,
 *    played_ms, ended:false}`；每次该 speech_id 的音频播到头（含被清掉）回报一条
 *    `ended:true`，并带 `final`。
 * 3. `final:true` 三者同时满足才给：收到该 speech_id 的结束标记
 *    （`neko-audio-stream-closed`，app-websocket.js 的 audio_done 分支派发）、
 *    已播过它的排程终点、播放器里它待解码 / 待排程的块数为 0
 *    （`appAudioPlayback.pendingAudioCountForSpeech`）。每个 speech_id 恰一次。
 *    不用只带 turnId 的 `neko-assistant-speech-end`：不同台词可能共用同一 turnId。
 *
 * 回报只在本页已 visit_bind（`neko-visit-bound`）、且串门进行中或刚结束的
 * 收尾窗口内（回家仪式句与 debrief 简述也要回报）发出；其余时间不跟踪任何语音。
 * 排帧只用定时器（本目录禁 rAF，§5 总则静态门）。
 */
(function () {
    'use strict';

    if (window.nekoVisitPacer) return;

    var PROGRESS_INTERVAL_MS = 250;
    var TICK_MS = 125;
    // 串门结束后仍回报的窗口：仪式句与简述各有一段 LLM 生成 + 播放，后端另有硬顶兜底
    var POST_END_ARM_MS = 180000;
    // 播到头、既没再来音频也没等到结束标记的记录，静置这么久就丢掉（被打断的行）
    var IDLE_FORGET_MS = 60000;
    var CLEAR_REASONS = {
        clear_audio_queue: true,
        clear_audio_queue_without_decoder_reset: true
    };

    var boundSocket = null;
    var visitId = null;
    // 收尾窗口属于哪一场：窗口期间新一场的开始前状态帧不会把回报记到新一场头上
    var armedVisitId = null;
    var visitActive = false;
    // 收到过 started 的那一场：只有它之后的状态才让 pacer 跟踪语音，开始之前就中止（邀请过期、
    // 被拒、入房途中重连）推来的 reconnecting / ending / ended 都不算
    var startedVisitId = null;
    var armedUntil = 0;
    // 上一拍的音频时钟：两拍之间没走（AudioContext 被挂起）就只暂停发送、不删记录，恢复后接着报
    var lastTickAudioTime = null;
    var lastScheduledEnd = 0;
    var speeches = new Map();
    // 已回报过 final 的 speech_id（有界）：之后再到的块 / 结束标记一律不再跟踪
    var finalized = new Set();
    var FINALIZED_CAP = 256;
    var tickTimer = 0;

    function appState() {
        return window.appState || null;
    }

    function wallNow() {
        return Date.now();
    }

    function audioNow(fallback) {
        var S = appState();
        var ctx = S && S.audioPlayerContext;
        if (ctx && Number.isFinite(ctx.currentTime)) return ctx.currentTime;
        return Number.isFinite(fallback) ? fallback : 0;
    }

    function reportVisitId() {
        if (visitActive) return startedVisitId || visitId;
        return armedVisitId || visitId;
    }

    function isArmed() {
        return visitActive || wallNow() < armedUntil;
    }

    function liveSocket() {
        var S = appState();
        var socket = S && S.socket;
        if (!socket || socket !== boundSocket) return null;
        if (typeof WebSocket !== 'undefined' && socket.readyState !== WebSocket.OPEN) return null;
        if (typeof WebSocket === 'undefined' && socket.readyState !== 1) return null;
        return socket;
    }

    function pendingCount(sid) {
        var player = window.appAudioPlayback;
        if (player && typeof player.pendingAudioCountForSpeech === 'function') {
            var n = Number(player.pendingAudioCountForSpeech(sid));
            return Number.isFinite(n) ? n : 0;
        }
        return 0;
    }

    function playedMs(rec, t) {
        var total = 0;
        for (var i = 0; i < rec.chunks.length; i++) {
            var c = rec.chunks[i];
            var upto = Math.min(t, c.end);
            if (upto > c.start) total += upto - c.start;
        }
        return Math.max(0, Math.round(total * 1000));
    }

    function getRecord(sid) {
        var rec = speeches.get(sid);
        if (!rec) {
            rec = {
                sid: sid,
                chunks: [],
                // 排过音频（哪怕之后整段在开播前被清掉）：之后按排程终点判排空，不再等结束标记
                hadAudio: false,
                scheduledEnd: 0,
                closed: false,
                drainReported: false,
                finalSent: false,
                lastSentAt: 0,
                lastActivity: wallNow(),
                // 记录建立时就定下属于哪一场：仪式句播到一半新一场开始，剩下的回报也不改场次
                visitId: reportVisitId()
            };
            speeches.set(sid, rec);
        }
        return rec;
    }

    function send(rec, played, ended, final) {
        var socket = liveSocket();
        if (!socket) return false;
        var msg = {
            action: 'visit_speech_progress',
            speech_id: rec.sid,
            visit_id: rec.visitId || reportVisitId(),
            played_ms: played,
            ended: ended
        };
        if (ended) msg.final = !!final;
        try {
            socket.send(JSON.stringify(msg));
        } catch (_) {
            return false;
        }
        rec.lastSentAt = wallNow();
        return true;
    }

    function markFinal(rec) {
        rec.finalSent = true;
        finalized.add(rec.sid);
        if (finalized.size > FINALIZED_CAP) finalized.delete(finalized.values().next().value);
    }

    function ensureTick() {
        if (tickTimer || speeches.size === 0) return;
        tickTimer = setTimeout(function () {
            tickTimer = 0;
            tick();
        }, TICK_MS);
    }

    function tick() {
        var t = audioNow();
        var wall = wallNow();
        var clockStalled = lastTickAudioTime !== null && t === lastTickAudioTime;
        lastTickAudioTime = t;
        speeches.forEach(function (rec, sid) {
            if (rec.finalSent) {
                speeches.delete(sid);
                return;
            }
            if (!rec.hadAudio) {
                // 一块音频都没排过：结束标记到了、也没有待解码的块，就什么都不报、直接丢掉，交给后端的
                // 起播超时转估时（任何一条回报都会让后端认作已开播，起播超时就走不到了）
                if (rec.closed && pendingCount(sid) === 0) speeches.delete(sid);
                else if (wall - rec.lastActivity > IDLE_FORGET_MS) speeches.delete(sid);
                return;
            }
            var played = playedMs(rec, t);
            var drained = t >= rec.scheduledEnd;
            if (!drained) {
                // 还没播完（在播或被挂起）都算活跃：静置计时从排空之后才开始
                rec.lastActivity = wall;
                // 第一块真开播之前不报：lookahead 下它可能排在前一行的尾巴后面好几秒，后端收到第一条
                // 回报就起 3 s 停滞计时，played_ms 一直为 0 会被误判停滞
                if (rec.chunks.length && t < rec.chunks[0].start) return;
                if (!clockStalled && wall - rec.lastSentAt >= PROGRESS_INTERVAL_MS) send(rec, played, false);
                return;
            }
            // 静置检查在发送之前：socket 换了还没重新 bind 时发送一直失败，记录也得能丢掉、定时器才停得下
            if (wall - rec.lastActivity > IDLE_FORGET_MS) {
                speeches.delete(sid);
                return;
            }
            var final = rec.closed && t >= rec.scheduledEnd && pendingCount(sid) === 0;
            if (!rec.drainReported || final) {
                if (send(rec, played, true, final)) {
                    rec.drainReported = true;
                    rec.lastActivity = wall;
                    if (final) markFinal(rec);
                }
                return;
            }
        });
        ensureTick();
    }

    function onChunkScheduled(state) {
        var sid = state.speechId ? String(state.speechId) : '';
        var end = Number(state.scheduledEndAudioTime);
        if (!sid || !Number.isFinite(end)) return;
        var start = Number(state.chunkStartAudioTime);
        if (!Number.isFinite(start)) {
            var duration = Number(state.chunkDurationSec);
            start = Number.isFinite(duration)
                ? end - duration
                // 复刻播放器的钳位：上一块的排程终点落后于当前时刻时，本块从当前时刻开播
                : Math.max(lastScheduledEnd, Number(state.audioContextTime) || 0);
        }
        lastScheduledEnd = end;
        if (finalized.has(sid)) return;
        var rec = getRecord(sid);
        rec.chunks.push({ start: Math.min(start, end), end: end });
        rec.hadAudio = true;
        rec.scheduledEnd = Math.max(rec.scheduledEnd, end);
        rec.drainReported = false;
        rec.lastActivity = wallNow();
        ensureTick();
    }

    function onQueueCleared(state) {
        // 已排程的块被停掉：截到此刻，下一拍按「排空（被清掉）」回报 ended
        var t = audioNow(Number(state.audioContextTime));
        lastScheduledEnd = 0;
        speeches.forEach(function (rec) {
            if (rec.scheduledEnd <= t) return;
            rec.chunks = rec.chunks
                .filter(function (c) { return c.start < t; })
                .map(function (c) { return { start: c.start, end: Math.min(c.end, t) }; });
            rec.scheduledEnd = t;
            rec.drainReported = false;
            rec.lastActivity = wallNow();
        });
        ensureTick();
    }

    window.addEventListener('neko-speech-playback-state', function (event) {
        var state = event && event.detail;
        if (!state || typeof state !== 'object') return;
        if (CLEAR_REASONS[state.reason] === true) {
            onQueueCleared(state);
            return;
        }
        if (state.reason !== 'chunk_scheduled' || !isArmed() || !boundSocket) return;
        onChunkScheduled(state);
    });

    window.addEventListener('neko-audio-stream-closed', function (event) {
        var detail = event && event.detail;
        var sid = detail && detail.speechId ? String(detail.speechId) : '';
        if (!sid || !isArmed() || !boundSocket || finalized.has(sid)) return;
        var rec = getRecord(sid);
        rec.closed = true;
        rec.lastActivity = wallNow();
        // 不在这里同步判 final：派发早于播放器把 audio_done 排进接收队列（尾段 flush），
        // 留到下一拍再查 pendingAudioCountForSpeech
        ensureTick();
    });

    window.addEventListener('neko-visit-bound', function (event) {
        var detail = (event && event.detail) || {};
        var S = appState();
        boundSocket = detail.socket || (S && S.socket) || null;
        if (typeof detail.visit_id === 'string' && detail.visit_id) visitId = detail.visit_id;
        if (detail.active === true) {
            // 页面在串门途中重载后重新 bind：这一场已经开始过，结束时照样开收尾窗口
            visitActive = true;
            startedVisitId = detail.visit_id || visitId;
        } else if (detail.active === false) {
            // 断线期间那一场已经结束、ended 帧没收到：按 bind 的快照收掉残留的进行中状态
            visitActive = false;
            startedVisitId = null;
        }
    });

    window.addEventListener('neko-visit-frame', function (event) {
        var frame = event && event.detail;
        if (!frame || frame.type !== 'visit_state_change') return;
        var frameVisit = typeof frame.visit_id === 'string' ? frame.visit_id : '';
        if (frameVisit) visitId = frameVisit;
        if (frame.action === 'started') {
            startedVisitId = frameVisit || visitId;
            visitActive = true;
            armedVisitId = null;
        } else if (frame.action === 'ended') {
            if (startedVisitId && startedVisitId === (frameVisit || visitId)) {
                armedUntil = wallNow() + POST_END_ARM_MS;
                armedVisitId = startedVisitId;
            }
            visitActive = false;
            startedVisitId = null;
        }
    });

    window.nekoVisitPacer = Object.freeze({
        isArmed: isArmed,
        isBound: function () { return !!boundSocket && liveSocket() !== null; },
        PROGRESS_INTERVAL_MS: PROGRESS_INTERVAL_MS
    });
})();
