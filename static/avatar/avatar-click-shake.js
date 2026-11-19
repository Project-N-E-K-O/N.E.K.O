/* Click shake feedback shared by the Live2D, VRM and MMD interaction adapters. */
(function (global) {
    'use strict';

    // 与 Live2D 点击判定同一口径：移动不超过 10px、按住不超过 300ms 才算点击
    const TAP_MAX_DISTANCE = 10;
    const TAP_MAX_DURATION_MS = 300;
    const SHAKE_DURATION_MS = 280;
    // 横向衰减振荡（px）。走独立的 translate 属性而不是 transform，
    // 不会覆盖画布 CSS 里的 translateZ(0) 合成层和退场动画的 transform
    const SHAKE_OFFSETS = [0, 4, -4, 3, -2, 1, 0];
    // 生气时的上下摇（px，按 1 倍记），比横向多摆一次、也更久
    const ANGRY_SHAKE_OFFSETS = [0, -5, 5, -4, 4, -3, 2, -1, 0];
    const ANGRY_SHAKE_DURATION_MS = 420;

    // ---- 连点规则 ----
    // 两次点击间隔不超过 COMBO_WINDOW_MS 才算连点，停手超过它这一串就结束、下次从第 1 下算起。
    //   第 1 下：横向轻晃 1 倍，不出声，照常播点击动作/表情（touch set）
    //   第 2 下：横向晃 1.5 倍，切 happy，预约一句语音（intensity=normal）
    //   第 3 下：横向晃 2 倍（封顶），切 surprised，取消第 2 下还没发出去的语音，改约 intensity=rapid
    //   第 4 下：上下摇（2 倍）+ 切 angry，取消待发语音，立刻发 intensity=angry
    //   之后 ANGRY_COOLDOWN_MS 内：点击不晃、不出声、不切表情，也不播任何点击动作/表情
    // 从第 2 下起表情由连点写死，所以第 2 下起不再播 touch set，免得把写死的表情盖掉。
    // 预约的语音在停手 COMBO_WINDOW_MS 后才发，也就是"这一串结束时只说一句最高档的话"。
    // 语音走 LLM 互动回复，一旦发出就没法撤回，所以"后一下打断前一下"只能在发出之前做。
    const COMBO_WINDOW_MS = 800;
    const COMBO_ANGRY_COUNT = 4;
    const COMBO_MAX_SCALE = 2;
    const COMBO_SCALE_STEP = 0.5;
    const ANGRY_COOLDOWN_MS = 30000;
    const COMBO_EMOTIONS = Object.freeze({ 2: 'happy', 3: 'surprised', 4: 'angry' });
    const COMBO_VOICE_INTENSITY = Object.freeze({ 2: 'normal', 3: 'rapid', 4: 'angry' });

    const running = new WeakMap();
    let combo = null;
    let angryUntil = 0;
    let avatarToolActive = false;
    let tapSeq = 0;

    function isEnabled() {
        if (typeof global.modelClickShakeEnabled === 'boolean') return global.modelClickShakeEnabled;
        // 模型管理、小游戏等页面不加载 app-settings.js，直接读主界面存下的设置
        try {
            const saved = JSON.parse(global.localStorage.getItem('project_neko_settings') || 'null');
            const value = saved && saved.modelClickShakeEnabled;
            if (value === false || value === 'false') return false;
        } catch (_) {}
        return true;
    }

    function prefersReducedMotion() {
        try {
            return !!(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
        } catch (_) {
            return false;
        }
    }

    function tapStart(point) {
        if (!point || !Number.isFinite(point.clientX) || !Number.isFinite(point.clientY)) return null;
        return { x: point.clientX, y: point.clientY, time: Date.now() };
    }

    function isTap(start, point) {
        if (!start || !point || !Number.isFinite(point.clientX) || !Number.isFinite(point.clientY)) return false;
        if (Date.now() - start.time >= TAP_MAX_DURATION_MS) return false;
        return Math.hypot(point.clientX - start.x, point.clientY - start.y) <= TAP_MAX_DISTANCE;
    }

    function roundPx(value) {
        return Math.round(value * 100) / 100 || 0;
    }

    // 画布整体原地抖一下；只动 CSS 合成层，不改模型坐标，也就不会被位置保存/回弹读到。
    // options.axis: 'x'（默认，横向）或 'y'（上下）；options.scale: 振幅倍数
    function play(element, options) {
        if (!isEnabled() || prefersReducedMotion() || !element || typeof element.animate !== 'function') return null;
        const vertical = !!(options && options.axis === 'y');
        const rawScale = Number(options && options.scale);
        const scale = Number.isFinite(rawScale) && rawScale > 0 ? Math.min(rawScale, COMBO_MAX_SCALE) : 1;
        const offsets = vertical ? ANGRY_SHAKE_OFFSETS : SHAKE_OFFSETS;
        const previous = running.get(element);
        if (previous) previous.cancel();
        let animation;
        try {
            animation = element.animate(
                offsets.map((offset) => {
                    const px = roundPx(offset * scale);
                    return { translate: vertical ? `0 ${px}px` : `${px}px 0` };
                }),
                { duration: vertical ? ANGRY_SHAKE_DURATION_MS : SHAKE_DURATION_MS, easing: 'ease-out' }
            );
        } catch (_) {
            return null;
        }
        running.set(element, animation);
        const clear = () => {
            if (running.get(element) === animation) running.delete(element);
        };
        animation.onfinish = clear;
        animation.oncancel = clear;
        return animation;
    }

    function shakeScaleFor(count) {
        return Math.min(COMBO_MAX_SCALE, 1 + COMBO_SCALE_STEP * (Math.max(1, count) - 1));
    }

    function isTheaterBlockingChat() {
        const runtime = global.nekoTheaterRuntime;
        try {
            return !!(runtime && typeof runtime.blocksOrdinaryChat === 'function'
                && runtime.blocksOrdinaryChat() === true);
        } catch (_) {
            return false;
        }
    }

    // 教程有自己的点击脚本；手里拿着道具时这一下已经算道具互动，都不叠加连点反应
    function canEscalate() {
        return global.isInTutorial !== true && !avatarToolActive;
    }

    function cancelPendingVoice(state) {
        if (state && state.voiceTimer) {
            clearTimeout(state.voiceTimer);
            state.voiceTimer = 0;
        }
    }

    function sendVoice(intensity) {
        if (!canEscalate() || isTheaterBlockingChat()) return false;
        const buttons = global.appButtons;
        if (!buttons || typeof buttons.sendAvatarInteractionPayload !== 'function') return false;
        tapSeq += 1;
        const payload = {
            interaction_id: `tap-${Date.now().toString(36)}-${tapSeq.toString(36)}`,
            tool_id: 'tap',
            action_id: 'poke',
            intensity,
            target: 'avatar',
            timestamp: Date.now()
        };
        try {
            Promise.resolve(buttons.sendAvatarInteractionPayload(payload)).catch(() => {});
        } catch (_) {
            return false;
        }
        return true;
    }

    function scheduleVoice(state, intensity) {
        cancelPendingVoice(state);
        state.voiceTimer = setTimeout(() => {
            state.voiceTimer = 0;
            if (combo === state) sendVoice(intensity);
        }, COMBO_WINDOW_MS);
    }

    function applyComboEmotion(emotion) {
        if (!emotion || isTheaterBlockingChat()) return;
        try {
            if (typeof global.applyEmotion === 'function') global.applyEmotion(emotion);
        } catch (_) {}
    }

    function resetCombo() {
        cancelPendingVoice(combo);
        combo = null;
    }

    // 一次确认过的点击（不是拖拽）：按连点规则晃动、切表情、出声。
    // 返回 { count, angry, allowTouchSet }：allowTouchSet=false 时调用方不得再播点击动作/表情
    // （第 2 下起表情归连点管；生气后的冷却期里点击完全不给反馈）。
    function tap(element) {
        if (!element) return { count: 0, angry: false, allowTouchSet: true };
        if (!canEscalate()) {
            resetCombo();
            play(element);
            return { count: 1, angry: false, allowTouchSet: true };
        }
        const now = Date.now();
        if (now < angryUntil) {
            // 气还没消：这一下当作没点
            return { count: 0, angry: true, allowTouchSet: false };
        }
        if (!combo || combo.element !== element || now - combo.lastAt > COMBO_WINDOW_MS) {
            resetCombo();
            combo = { element, count: 0, lastAt: 0, voiceTimer: 0 };
        }
        const state = combo;
        state.count += 1;
        state.lastAt = now;
        const count = state.count;

        applyComboEmotion(COMBO_EMOTIONS[count]);

        if (count >= COMBO_ANGRY_COUNT) {
            play(element, { axis: 'y', scale: COMBO_MAX_SCALE });
            cancelPendingVoice(state);
            sendVoice(COMBO_VOICE_INTENSITY[COMBO_ANGRY_COUNT]);
            combo = null;
            angryUntil = now + ANGRY_COOLDOWN_MS;
            return { count, angry: true, allowTouchSet: false };
        }

        play(element, { scale: shakeScaleFor(count) });
        if (COMBO_VOICE_INTENSITY[count]) scheduleVoice(state, COMBO_VOICE_INTENSITY[count]);
        return { count, angry: false, allowTouchSet: count < 2 };
    }

    if (typeof global.addEventListener === 'function') {
        global.addEventListener('react-chat-window:avatar-tool-state', (event) => {
            avatarToolActive = !!(event && event.detail && event.detail.active);
            if (avatarToolActive) resetCombo();
        });
        global.addEventListener('neko:deactivate-avatar-tool', () => {
            avatarToolActive = false;
        });
    }

    global.NekoAvatarClickShake = Object.freeze({
        isEnabled, tapStart, isTap, play, tap,
        COMBO_WINDOW_MS, COMBO_ANGRY_COUNT, ANGRY_COOLDOWN_MS
    });
})(window);
