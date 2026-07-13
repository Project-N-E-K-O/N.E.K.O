/**
 * app-context-prompt.js — 「情境弹窗」
 *
 * 后端活动 tracker 检测到用户「进入」游戏/娱乐 或「进入」专注工作时，经 WebSocket
 * 推 { type:'activity_context_prompt', context:'play'|'work' }；app-websocket.js 把它
 * 转给本模块的 handle()。
 *
 * 本模块只负责「要不要弹 + 弹什么 + 用户选了之后改哪个设置」：
 *   - 对所有用户生效（原 A/B 实验组 vision_chat_default_off 的机制已合并进 main）；
 *   - 每个 app 会话每类（play / work）最多弹一次（模块级标志，刷新页面即新会话）；
 *   - play（游戏/娱乐）→ 问要不要开启主动搭话里的「屏幕分享来源」；
 *   - work（专注工作）→ 问要不要关掉「屏幕分享来源」避嫌（不动搭话频率，尊重用户原设定）。
 *
 * 这里说的「屏幕分享」全部指 proactiveVisionChatEnabled（主动搭话的屏幕来源），不是
 * 隐私模式（proactiveVisionEnabled）。后端只在隐私模式关时才会推送，所以无需在此再判隐私。
 *
 * 时序：handle() 要读合并后的 proactiveVisionChatEnabled 才能判断「还有没有可改的」，
 * 故在 settings 合并完成前先把事件按类暂存，等 app-settings.js 广播
 * neko:telemetry-branch-resolved（settings 已合并就绪的信号）后再重放。
 *
 * 依赖: app-state.js (window.appState)、common_dialogs.js (window.showDecisionPrompt)、
 *       app-settings.js (window.saveNEKOSettings)、app-settings 广播的
 *       neko:telemetry-branch-resolved 事件。
 */
(function () {
    'use strict';

    const S = window.appState || {};

    // 每会话每类去重：弹过（无论用户选了什么、甚至直接关掉）就不再弹同类。
    let _shownPlay = false;
    let _shownWork = false;
    // 同一时刻只允许一个情境弹窗，避免两类信号叠出两个 modal。
    let _promptOpen = false;
    // settings 还没决议就绪时收到的事件先按类别暂存，等 branch 决议后逐类重放。
    // 后端这条信号是「进入态」一次性推送、不会自动重发；GET 慢时若直接丢，
    // 本会话就再也看不到这次弹窗了。按类别存（而非单槽）避免决议前先后收到
    // play + work 时只剩最后一个、另一类被吞——与「每会话每类一次」语义保持一致。
    const _pendingContexts = new Set();

    // 读 flag 当前值：优先 window 镜像，回退 appState。
    function _flag(name) {
        if (typeof window[name] !== 'undefined') return !!window[name];
        return !!(S && S[name]);
    }

    // 写 flag：window 镜像 + appState 双写，保持与 saveSettings/调度器一致。
    function _setFlag(name, value) {
        const v = !!value;
        window[name] = v;
        if (S) S[name] = v;
    }

    function tr(key, fallback) {
        try {
            if (typeof window.t === 'function') {
                const v = window.t(key);
                if (v && v !== key) return v;
            }
        } catch (_) { /* i18n 不可用就用兜底中文 */ }
        return fallback;
    }

    function _persistAndReschedule() {
        try {
            if (typeof window.saveNEKOSettings === 'function') {
                window.saveNEKOSettings();
            }
        } catch (e) {
            console.warn('[context-prompt] saveNEKOSettings 失败:', e);
        }
        try {
            const scheduler = (window.appProactive && typeof window.appProactive.scheduleProactiveChat === 'function')
                ? window.appProactive.scheduleProactiveChat
                : (typeof window.scheduleProactiveChat === 'function' ? window.scheduleProactiveChat : null);
            if (scheduler) scheduler();
        } catch (e) {
            console.warn('[context-prompt] 重新调度主动搭话失败:', e);
        }
    }

    // 已经没什么可改的就别弹：play 时屏幕分享已开且主动搭话已开 / work 时屏幕分享已关。
    function _isActionable(context) {
        const visionChatOn = _flag('proactiveVisionChatEnabled');
        if (context === 'play') {
            return !(_flag('proactiveChatEnabled') && visionChatOn);
        }
        // work
        return visionChatOn;
    }

    function _buildConfig(context) {
        if (context === 'play') {
            return {
                title: tr('contextPrompt.play.title', '要不要我陪你一起看屏幕？'),
                message: tr('contextPrompt.play.message',
                    '你在打游戏 / 看番呢，我可以开启主动搭话里的屏幕分享，跟着你眼前的画面一起聊、一起吐槽。要开吗？'),
                accept: tr('contextPrompt.play.accept', '好呀，开启'),
                decline: tr('contextPrompt.play.decline', '先不用'),
            };
        }
        return {
            title: tr('contextPrompt.work.title', '要不要我安静点、别看屏幕？'),
            message: tr('contextPrompt.work.message',
                '看你在专注工作，我可以关掉主动搭话里的屏幕分享来源，免得打扰你、也避避窥屏的嫌。要关吗？'),
            accept: tr('contextPrompt.work.accept', '好，关掉屏幕分享'),
            decline: tr('contextPrompt.work.decline', '不用，继续'),
        };
    }

    function _apply(context) {
        if (context === 'play') {
            // 开启主动搭话里的屏幕分享来源；若主动搭话总开关没开，一并打开。
            _setFlag('proactiveChatEnabled', true);
            _setFlag('proactiveVisionChatEnabled', true);
        } else {
            // 只关屏幕分享来源，避嫌；搭话频率 / 其它来源 / 隐私模式都不动。
            _setFlag('proactiveVisionChatEnabled', false);
        }
        _persistAndReschedule();
    }

    async function handle(context) {
        if (context !== 'play' && context !== 'work') return;
        // settings 还没合并就绪（branch 未决议，nekoTelemetryBranch 为 undefined）：暂存
        // 这次事件，等 neko:telemetry-branch-resolved 再重放。不能直接丢——后端一次性推送
        // 不会重发。GET 失败时 branch 永远 undefined、该事件也永不重放，等于 fail-closed
        // （settings 没就绪宁可漏弹，避免拿首启默认值误判 _isActionable）。
        if (typeof window.nekoTelemetryBranch === 'undefined') {
            _pendingContexts.add(context);
            return;
        }
        if (typeof window.showDecisionPrompt !== 'function') return;
        // 已有弹窗开着：暂存这次，等当前弹窗关掉（finally 里 drain）再重放，别直接丢
        // ——否则用户开着 play 弹窗时切到工作，work 信号会被吞且不再重试。同类重复入
        // Set 自动去重，重放时 _shownX 也会拦掉已弹过的。
        if (_promptOpen) {
            _pendingContexts.add(context);
            return;
        }
        if (context === 'play' && _shownPlay) return;
        if (context === 'work' && _shownWork) return;
        if (!_isActionable(context)) {
            // 没可改的也算这类「处理过」，本会话不再就同类打扰。
            if (context === 'play') _shownPlay = true; else _shownWork = true;
            return;
        }

        // 先置去重 + 开窗标志：即便用户直接关掉弹窗，本会话也不再弹同类。
        if (context === 'play') _shownPlay = true; else _shownWork = true;
        _promptOpen = true;

        const cfg = _buildConfig(context);
        try {
            const decision = await window.showDecisionPrompt({
                title: cfg.title,
                message: cfg.message,
                dismissValue: 'decline',
                closeOnClickOutside: true,
                closeOnEscape: true,
                buttons: [
                    { value: 'decline', text: cfg.decline, variant: 'secondary' },
                    { value: 'accept', text: cfg.accept, variant: 'primary' },
                ],
            });
            if (decision === 'accept') {
                _apply(context);
            }
        } catch (e) {
            console.warn('[context-prompt] 弹窗失败:', e);
        } finally {
            _promptOpen = false;
            // 弹窗开着期间攒下的另一类 context，关窗后接着重放。
            _drainPending();
        }
    }

    // 串行重放暂存的 context。两处会攒 pending：①branch 未决议时收到的事件；②已有
    // 弹窗开着时收到的事件。两种都靠这里重放。必须串行 await——handle() 开窗时置
    // _promptOpen=true 并在 modal 上挂起，若并发触发第二个 ctx 会撞 _promptOpen 早退被
    // 永久丢弃；逐个 await 让第二个弹窗等第一个关掉再弹。开头先清空 Set，重入安全
    // （重放途中新攒的事件由当前弹窗 finally 再触发一次 drain 接住）。
    function _drainPending() {
        if (_pendingContexts.size === 0) return;
        const pend = Array.from(_pendingContexts);
        _pendingContexts.clear();
        (async function () {
            for (const ctx of pend) {
                // 按单个 ctx 隔离异常：play/work 是一次性事件，前一个意外抛错不能连累
                // 后一个不被消费（否则那类本会话就丢了）。
                try {
                    await handle(ctx);
                } catch (e) {
                    console.warn('[context-prompt] 重放暂存事件失败:', ctx, e);
                }
            }
        })();
    }

    // settings 合并就绪后重放暂存的事件（app-settings.js 在拿到 telemetryBranch 后广播）。
    // 此时 window.nekoTelemetryBranch 已就绪、proactiveVisionChatEnabled 也已是合并后的值。
    window.addEventListener('neko:telemetry-branch-resolved', _drainPending);

    window.appContextPrompt = { handle };
})();
