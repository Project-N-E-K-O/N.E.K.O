/**
 * N.E.K.O 通用新手引导系统
 * 支持所有页面的引导配置
 */

// Home uses the Yui seven-day guide; non-home pages use the restored Driver.js page tutorial runtime.
const TUTORIAL_PAGES = Object.freeze([
    'home',
    'model_manager',
    'model_manager_live2d',
    'model_manager_vrm',
    'model_manager_mmd',
    'model_manager_common',
    'parameter_editor',
    'emotion_manager',
    'chara_manager',
    'settings',
    'voice_clone',
    'steam_workshop',
    'memory_browser',
]);
const TUTORIAL_STORAGE_KEY_PREFIX = 'neko_tutorial_';
const TUTORIAL_FLOW_PREFIX = '[TutorialFlow]';
const TUTORIAL_YUI_LIVE2D_MODEL_NAME = 'yui-origin';
const TUTORIAL_YUI_LIVE2D_MODEL_PATH = '/static/yui-origin/yui-origin.model3.json';
const TUTORIAL_AVATAR_OVERRIDE_TIMEOUT_MS = 8000;
const HOME_TUTORIAL_RESET_EVENT = 'neko:home-tutorial-reset';
const HOME_TUTORIAL_RESET_STORAGE_EVENT_KEY = 'neko_home_tutorial_reset_event';
const HOME_TUTORIAL_RESET_CHANNEL = 'neko_tutorial_events';
const AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY = 'neko_avatar_floating_guide_usage_v1';
const YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY = 'neko_yui_guide_chat_bridge_queue_v1';
const STARTUP_GREETING_RELEASE_EVENT = 'neko:startup-greeting-release';
// PC 端最长可能先等折叠动画结束再展开；这里额外留出跨窗口转发余量，但不让教程无限等待。
const YUI_GUIDE_COMPACT_CHAT_PREPARE_TIMEOUT_MS = 4500;

function getTutorialStorageKeyForPage(pageKey) {
    return TUTORIAL_STORAGE_KEY_PREFIX + pageKey;
}

function getTutorialManualIntentKeyForPage(pageKey) {
    return getTutorialStorageKeyForPage(pageKey) + '_manual_intent';
}

function getTutorialStorageKeysForPageFallback(pageKey) {
    if (pageKey === 'model_manager') {
        return [
            'model_manager',
            'model_manager_live2d',
            'model_manager_vrm',
            'model_manager_mmd',
            'model_manager_common',
        ].map(getTutorialStorageKeyForPage);
    }

    return [getTutorialStorageKeyForPage(pageKey)];
}

function logTutorialFlow(step, details = {}) {
    if (localStorage.getItem('neko_tutorial_flow_debug') !== '1') {
        return;
    }
    console.log(TUTORIAL_FLOW_PREFIX + ' ' + step, details);
}

function getSevenDayTutorialStateApi() {
    const stateApi = window.NekoSevenDayTutorialState || null;
    if (!stateApi) {
        throw new Error('seven_day_tutorial_state_unavailable');
    }
    return stateApi;
}

function normalizeAvatarFloatingGuideRound(day) {
    const round = getSevenDayTutorialStateApi().normalizeRound(day);
    if (!round) {
        throw new Error(`Invalid avatar floating guide round: ${day}`);
    }
    return round;
}

function normalizeOptionalAvatarFloatingGuideRound(value) {
    return getSevenDayTutorialStateApi().normalizeRound(value);
}

function loadAvatarFloatingGuideState() {
    return getSevenDayTutorialStateApi().loadState();
}

function saveAvatarFloatingGuideState(state) {
    getSevenDayTutorialStateApi().saveState(state);
}

function recordAvatarFloatingGuideEndState(day, outcome, rawReason, source) {
    const normalizedDay = normalizeOptionalAvatarFloatingGuideRound(day);
    const normalizedOutcome = outcome === 'complete'
        ? 'complete'
        : (outcome === 'skip' ? 'skip' : 'destroy');
    const normalizedRawReason = typeof rawReason === 'string' && rawReason.trim()
        ? rawReason.trim().toLowerCase()
        : normalizedOutcome;
    const endState = {
        day: normalizedDay,
        ended: true,
        outcome: normalizedOutcome,
        rawReason: normalizedRawReason,
        isAngryExit: normalizedRawReason === 'angry_exit',
        completed: normalizedOutcome === 'complete',
        skipped: normalizedOutcome === 'skip',
        source: typeof source === 'string' ? source : '',
        endedAt: Date.now(),
    };
    window.avatarFloatingGuideEndState = endState;
    return endState;
}

function recordAvatarFloatingGuideUsageRoundEnd(day) {
    const normalizedDay = normalizeOptionalAvatarFloatingGuideRound(day);
    if (!normalizedDay) {
        return;
    }
    let state = {};
    try {
        const raw = localStorage.getItem(AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY);
        state = raw ? JSON.parse(raw) : {};
    } catch (error) {
        console.warn('[Tutorial] 悬浮窗教程使用状态读取失败，使用空状态:', error);
        state = {};
    }
    if (!state || typeof state !== 'object' || Array.isArray(state)) {
        state = {};
    }
    state['day' + normalizedDay + 'EndedAt'] = Date.now();
    try {
        localStorage.setItem(AVATAR_FLOATING_GUIDE_USAGE_STORAGE_KEY, JSON.stringify(state));
    } catch (error) {
        console.warn('[Tutorial] 悬浮窗教程使用状态写入失败:', error);
    }
}

function dispatchHomeTutorialResetEvent(pageKey, source) {
    if (pageKey !== 'home' && pageKey !== 'all') {
        return;
    }
    const detail = {
        page: pageKey,
        source: source || 'manual_home_tutorial_reset',
        nonce: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    };

    if (typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
        window.dispatchEvent(new CustomEvent(HOME_TUTORIAL_RESET_EVENT, { detail }));
    }

    if (typeof BroadcastChannel === 'function') {
        try {
            const channel = new BroadcastChannel(HOME_TUTORIAL_RESET_CHANNEL);
            channel.postMessage({
                type: HOME_TUTORIAL_RESET_EVENT,
                detail,
            });
            channel.close();
        } catch (error) {
            console.warn('[Tutorial] 广播首页教程重置事件失败:', error);
        }
    }

    try {
        localStorage.setItem(HOME_TUTORIAL_RESET_STORAGE_EVENT_KEY, JSON.stringify(detail));
        localStorage.removeItem(HOME_TUTORIAL_RESET_STORAGE_EVENT_KEY);
    } catch (error) {
        console.warn('[Tutorial] 写入首页教程重置同步事件失败:', error);
    }
}

window.getTutorialStorageKeyForPage = getTutorialStorageKeyForPage;
window.getTutorialManualIntentKeyForPage = getTutorialManualIntentKeyForPage;
window.logTutorialFlow = logTutorialFlow;

const TutorialLifecycleStores = window.TutorialLifecycleStores || {};
function createFallbackTutorialLifecycleStateStoreClass() {
    function FallbackTutorialLifecycleStateStore() {
        this.resetEndReason();
    }

    FallbackTutorialLifecycleStateStore.prototype.normalizeRawReason = function (reason) {
        const normalized = typeof reason === 'string' ? reason.trim().toLowerCase() : '';
        return normalized || 'destroy';
    };

    FallbackTutorialLifecycleStateStore.prototype.normalizeReason = function (reason) {
        const normalized = this.normalizeRawReason(reason);
        if (normalized === 'complete') {
            return 'complete';
        }
        if (normalized === 'skip' || normalized === 'escape' || normalized === 'angry_exit') {
            return 'skip';
        }
        return 'destroy';
    };

    FallbackTutorialLifecycleStateStore.prototype.setEndReason = function (reason) {
        if (this.endRawReason) {
            return this.endReason || 'destroy';
        }
        const rawReason = this.normalizeRawReason(reason);
        this.endRawReason = rawReason;
        this.endReason = this.normalizeReason(rawReason);
        return this.endReason;
    };

    FallbackTutorialLifecycleStateStore.prototype.resolveEndMeta = function (options) {
        const normalizedOptions = options || {};
        const finalSteps = Array.isArray(normalizedOptions.finalSteps)
            ? normalizedOptions.finalSteps
            : [];
        const currentStep = Number.isFinite(normalizedOptions.currentStep)
            ? normalizedOptions.currentStep
            : -1;

        if (this.endReason || this.endRawReason) {
            return {
                reason: this.endReason || 'destroy',
                rawReason: this.endRawReason || this.endReason || 'destroy'
            };
        }
        if (finalSteps.length > 0 && currentStep >= finalSteps.length - 1) {
            return {
                reason: 'complete',
                rawReason: 'complete'
            };
        }
        return {
            reason: 'destroy',
            rawReason: 'destroy'
        };
    };

    FallbackTutorialLifecycleStateStore.prototype.createYuiGuideEndDetail = function (options) {
        const normalizedOptions = options || {};
        const rawReason = this.normalizeRawReason(normalizedOptions.reason);
        return {
            page: normalizedOptions.page || '',
            runtimePage: normalizedOptions.runtimePage || '',
            reason: this.normalizeReason(rawReason),
            rawReason: rawReason
        };
    };

    FallbackTutorialLifecycleStateStore.prototype.createTerminationRequest = function (options) {
        const normalizedOptions = options || {};
        const sourcePage = typeof normalizedOptions.sourcePage === 'string'
            ? normalizedOptions.sourcePage.trim()
            : '';
        if (!sourcePage || sourcePage === 'home') {
            return null;
        }
        const rawReason = this.normalizeRawReason(
            normalizedOptions.rawReason || normalizedOptions.reason || 'destroy'
        );
        return {
            action: 'yui_guide_request_termination',
            sourcePage: sourcePage,
            targetPage: 'home',
            reason: rawReason,
            tutorialReason: rawReason,
            timestamp: Number.isFinite(normalizedOptions.timestamp)
                ? normalizedOptions.timestamp
                : Date.now()
        };
    };

    FallbackTutorialLifecycleStateStore.prototype.resetEndReason = function () {
        this.endReason = null;
        this.endRawReason = null;
    };

    FallbackTutorialLifecycleStateStore.prototype.getEndRawReason = function () {
        return this.endRawReason;
    };

    FallbackTutorialLifecycleStateStore.prototype.getEndReason = function () {
        return this.endReason;
    };

    return FallbackTutorialLifecycleStateStore;
}
const TutorialLifecycleStateStore = TutorialLifecycleStores.TutorialLifecycleStateStore
    || createFallbackTutorialLifecycleStateStoreClass();
const TutorialRoundPrelude = window.TutorialRoundPrelude || {};
const TutorialRoundPreludeController = TutorialRoundPrelude.TutorialRoundPreludeController;

function createUniversalTutorialScopedResources() {
    if (
        window.YuiGuideCommon
        && typeof window.YuiGuideCommon.createScopedTutorialResources === 'function'
    ) {
        return window.YuiGuideCommon.createScopedTutorialResources({ window: window });
    }

    const listeners = [];
    const timers = [];
    const intervals = [];
    return {
        addEventListener(target, type, handler, listenerOptions) {
            if (!target || typeof target.addEventListener !== 'function') {
                return null;
            }
            target.addEventListener(type, handler, listenerOptions);
            listeners.push({ target, type, handler, options: listenerOptions });
            return handler;
        },
        setTimeout(callback, delayMs) {
            const timerId = window.setTimeout(callback, delayMs);
            timers.push(timerId);
            return timerId;
        },
        clearTimeout(timerId) {
            if (!timerId) {
                return;
            }
            window.clearTimeout(timerId);
            const index = timers.indexOf(timerId);
            if (index !== -1) {
                timers.splice(index, 1);
            }
        },
        setInterval(callback, delayMs) {
            const intervalId = window.setInterval(callback, delayMs);
            intervals.push(intervalId);
            return intervalId;
        },
        clearInterval(intervalId) {
            if (!intervalId) {
                return;
            }
            window.clearInterval(intervalId);
            const index = intervals.indexOf(intervalId);
            if (index !== -1) {
                intervals.splice(index, 1);
            }
        },
        destroy() {
            while (intervals.length) {
                window.clearInterval(intervals.pop());
            }
            while (timers.length) {
                window.clearTimeout(timers.pop());
            }
            while (listeners.length) {
                const listener = listeners.pop();
                listener.target.removeEventListener(listener.type, listener.handler, listener.options);
            }
        }
    };
}

class UniversalTutorialManager {
    constructor() {
        // 立即设置全局引用，以便在 getter 中使用
        window.universalTutorialManager = this;

        this.STORAGE_KEY_PREFIX = TUTORIAL_STORAGE_KEY_PREFIX;
        this.isInitialized = true;
        this.isTutorialRunning = false; // 防止重复启动
        this.currentPage = UniversalTutorialManager.detectPage();
        this.currentStep = 0;
        this._tutorialLive2dRenderActivationToken = 0;
        this._avatarFloatingModelLockSnapshot = null;
        // 记录教程强制展开前是否为毛球，所有结束路径都用它恢复原形态。
        this._avatarFloatingChatSurfaceSnapshot = null;
        this.cachedValidSteps = null;
        this._pendingI18nStart = false;
        this.pendingTutorialStartSource = null;
        this.currentTutorialStartSource = 'auto';
        this.yuiGuideDirector = null;
        this._yuiGuideHandoffToken = null;
        this._yuiGuideLastSceneId = null;
        this._yuiGuideLifecycleActive = false;
        this.activeAvatarFloatingGuideRound = null;
        this._tutorialEndHandled = false;
        const LifecycleStateStore = typeof TutorialLifecycleStateStore === 'function'
            ? TutorialLifecycleStateStore
            : createFallbackTutorialLifecycleStateStoreClass();
        this.lifecycleStateStore = new LifecycleStateStore();
        this._tutorialAvatarReloadController = null;
        this._tutorialRoundPreludeController = null;
        this._tutorialSkipController = null;
        this._teardownPromise = null;
        this.managerResources = createUniversalTutorialScopedResources();
        this._tutorialViewportPlacementResources = null;
        this._tutorialViewportPlacementResizeHandler = null;
        this._tutorialViewportPlacementResizeTimer = null;
        this._tutorialScrollBlockHandler = this.blockTutorialScrollEvent.bind(this);
        this._tutorialScrollBlockOptions = { capture: true, passive: false };
        this._tutorialScrollBlockResources = null;
        this._isTutorialScrollBlocked = false;
        this._isDestroyed = false;
        this._desktopYuiGuideSkipHandler = this.handleDesktopYuiGuideSkipRequest.bind(this);
        this.managerResources.addEventListener(
            window,
            'neko:yui-guide:desktop-skip-request',
            this._desktopYuiGuideSkipHandler
        );

        window.setTimeout(() => {
            this.checkAndStartTutorial().catch(error => {
                console.error('[Tutorial] checkAndStartTutorial failed:', error);
            });
        }, 0);
    }

    logTutorialFlow(step, details = {}) {
        logTutorialFlow(step, details);
    }

    dispatchStartupGreetingRelease(reason, detail = {}) {
        // 放行启动问候即代表新手教程这一程不会再占屏（夭折未启动，或已结束），结束 pending 窗口。
        this.setHomeTutorialPending(false);
        const releaseDetail = Object.assign({
            released: true,
            page: this.currentPage,
            reason: reason || 'startup-flow-complete',
            timestamp: Date.now()
        }, detail || {});
        try {
            window.__NEKO_STARTUP_GREETING_RELEASED__ = releaseDetail;
            window.dispatchEvent(new CustomEvent(STARTUP_GREETING_RELEASE_EVENT, {
                detail: releaseDetail
            }));
        } catch (error) {
            console.warn('[Tutorial] 启动问候放行事件派发失败:', error);
        }
        return releaseDetail;
    }

    clearStartupGreetingRelease(reason = 'tutorial-started') {
        // 教程已进入运行态（isTutorialRunning/isInTutorial 已置），由运行锁接管占屏，结束 pending 窗口。
        this.setHomeTutorialPending(false);
        try {
            const detail = window.__NEKO_STARTUP_GREETING_RELEASED__;
            if (detail && detail.released === true) {
                delete window.__NEKO_STARTUP_GREETING_RELEASED__;
            }
            window.dispatchEvent(new CustomEvent(STARTUP_GREETING_RELEASE_EVENT, {
                detail: {
                    released: false,
                    page: this.currentPage,
                    reason: reason || 'tutorial-started',
                    timestamp: Date.now()
                }
            }));
        } catch (error) {
            console.warn('[Tutorial] 启动问候放行状态清理失败:', error);
        }
    }

    setHomeTutorialPending(pending) {
        // 新手教程「即将运行但尚未上锁」窗口的标志。冷启动加载 Live2D 模型与首句演出期间，
        // isTutorialRunning / window.isInTutorial 都还没置上时，选人格门控
        // （character_personality_onboarding.js 的 isHomeTutorialInteractionLocked）靠这个旗子
        // 提前知道教程马上要占屏，避免「上锁前的长 await 链超过选人格 15s 超时 → 选人格与新手教程并发弹出」。
        // 仅由 dispatchStartupGreetingRelease（教程不启动/已结束）与 clearStartupGreetingRelease（教程已上锁接管）
        // 这对 choke point 收口清除，凡是不启动的出口都必经前者，天然 deadlock-safe。
        window.isNekoHomeTutorialPending = pending === true;
        const runtime = window.NekoHomeTutorialRuntime || null;
        if (runtime && typeof runtime.refreshInteractionLock === 'function') {
            runtime.refreshInteractionLock(pending ? 'tutorial-pending' : 'tutorial-pending-cleared');
        }
    }

    loadAvatarFloatingGuideState() {
        return loadAvatarFloatingGuideState();
    }

    saveAvatarFloatingGuideState(state) {
        saveAvatarFloatingGuideState(state);
    }

    resetAvatarFloatingGuideRoundState(day, options = {}) {
        const round = normalizeAvatarFloatingGuideRound(day);
        return getSevenDayTutorialStateApi().resetRound(round, {
            source: options.source || 'home_reset_button',
        });
    }

    setAvatarFloatingGuideCurrentRound(day) {
        const round = normalizeAvatarFloatingGuideRound(day);
        const state = loadAvatarFloatingGuideState();
        state.currentRound = round;
        state.pendingRound = round;
        state.updatedAt = new Date().toISOString();
        saveAvatarFloatingGuideState(state);
        return state;
    }

    markAvatarFloatingGuideRoundOutcome(day, outcome, rawReason = outcome) {
        const round = normalizeAvatarFloatingGuideRound(day);
        const normalizedOutcome = outcome === 'skip' ? 'skip' : (outcome === 'complete' ? 'complete' : 'destroy');
        const normalizedRawReason = typeof rawReason === 'string' && rawReason.trim()
            ? rawReason.trim().toLowerCase()
            : normalizedOutcome;
        const state = normalizedOutcome === 'destroy'
            ? loadAvatarFloatingGuideState()
            : getSevenDayTutorialStateApi().markRoundOutcome(round, normalizedOutcome);
        state.currentRound = null;
        const endedAt = Date.now();
        state.lastEndState = {
            day: round,
            ended: true,
            outcome: normalizedOutcome,
            rawReason: normalizedRawReason,
            isAngryExit: normalizedRawReason === 'angry_exit',
            completed: normalizedOutcome === 'complete',
            skipped: normalizedOutcome === 'skip',
            source: 'avatar_floating_guide_state',
            endedAt: endedAt
        };
        state.updatedAt = new Date(endedAt).toISOString();
        saveAvatarFloatingGuideState(state);
        window.dispatchEvent(new CustomEvent(`neko:avatar-floating-guide-${normalizedOutcome}`, {
            detail: { day: round, state, endState: state.lastEndState },
        }));
        return state;
    }

    markAvatarFloatingGuideRoundAutoShown(day, reservationId = '') {
        const round = normalizeAvatarFloatingGuideRound(day);
        return getSevenDayTutorialStateApi().markAutoStartReservation(round, { reservationId });
    }

    rollbackAvatarFloatingGuideRoundAutoShown(day, reservationId = '') {
        const round = normalizeAvatarFloatingGuideRound(day);
        return getSevenDayTutorialStateApi().rollbackAutoStartReservation(round, { reservationId });
    }

    isAvatarFloatingGuideRoundPendingAutoStart(day) {
        const round = normalizeAvatarFloatingGuideRound(day);
        const state = loadAvatarFloatingGuideState();
        if (getSevenDayTutorialStateApi().isRoundSettled(state, round)) {
            return false;
        }
        if (state.manualResetRound) {
            return state.manualResetRound === round;
        }
        return this.getNextAvatarFloatingGuideAutoRound() === round;
    }

    isAvatarFloatingGuideRoundRegistered(day) {
        const round = normalizeAvatarFloatingGuideRound(day);
        const registry = window.YuiGuideDailyGuides || {};
        const guideConfig = registry[round] || null;
        return !!(
            guideConfig
            && guideConfig.round
            && Array.isArray(guideConfig.round.scenes)
            && guideConfig.round.scenes.length > 0
        );
    }

    getNextAvatarFloatingGuideAutoRound() {
        const state = loadAvatarFloatingGuideState();
        const round = getSevenDayTutorialStateApi().getNextAutoRound(state);
        return round && this.isAvatarFloatingGuideRoundRegistered(round) ? round : null;
    }

    getHomeAvatarFloatingGuideStartRound(options = {}) {
        if (this.currentPage !== 'home' || !this.isYuiGuideEnabledForPage(this.currentPage)) {
            return null;
        }

        const state = loadAvatarFloatingGuideState();
        const candidates = [];
        if (options && options.includeActive === true) {
            candidates.push(this.activeAvatarFloatingGuideRound, state.currentRound);
        }
        candidates.push(state.pendingRound, state.manualResetRound, 1);

        for (let index = 0; index < candidates.length; index += 1) {
            const round = normalizeOptionalAvatarFloatingGuideRound(candidates[index]);
            if (round && this.isAvatarFloatingGuideRoundRegistered(round)) {
                return round;
            }
        }
        return null;
    }

    getAvatarFloatingBootHelper() {
        return window.NekoAvatarFloatingBoot || null;
    }

    isDirectAvatarFloatingTutorialBoot(round) {
        const helper = this.getAvatarFloatingBootHelper();
        if (!helper) {
            return false;
        }
        const normalizedRound = normalizeOptionalAvatarFloatingGuideRound(round);
        const predictedRound = typeof helper.getPredictedRound === 'function'
            ? normalizeOptionalAvatarFloatingGuideRound(helper.getPredictedRound())
            : null;
        const canBootDirect = typeof helper.shouldBootIntoTutorial === 'function'
            ? helper.shouldBootIntoTutorial()
            : !!predictedRound;
        const skippedUserModel = typeof helper.wasUserModelBootSkipped === 'function'
            && helper.wasUserModelBootSkipped();
        const skippedRound = typeof helper.getSkippedUserModelBootRound === 'function'
            ? normalizeOptionalAvatarFloatingGuideRound(helper.getSkippedUserModelBootRound())
            : null;
        return !!normalizedRound
            && (
                (canBootDirect && predictedRound === normalizedRound)
                || (skippedUserModel && predictedRound === normalizedRound && skippedRound === normalizedRound)
            );
    }

    getDirectAvatarFloatingTutorialBootRound() {
        const helper = this.getAvatarFloatingBootHelper();
        if (!helper || this.currentPage !== 'home') {
            return null;
        }
        const predictedRound = typeof helper.getPredictedRound === 'function'
            ? normalizeOptionalAvatarFloatingGuideRound(helper.getPredictedRound())
            : null;
        if (!predictedRound || !this.isAvatarFloatingGuideRoundRegistered(predictedRound)) {
            return null;
        }
        return this.isDirectAvatarFloatingTutorialBoot(predictedRound) ? predictedRound : null;
    }

    getHomeAvatarFloatingGuideLaunchRound(options = {}) {
        return this.getDirectAvatarFloatingTutorialBootRound()
            || this.getHomeAvatarFloatingGuideStartRound(options);
    }

    claimDirectAvatarFloatingTutorialBoot(round, source) {
        if (!window.NekoAvatarFloatingBoot || typeof window.NekoAvatarFloatingBoot.claimDirectTutorialBoot !== 'function') {
            return false;
        }
        return window.NekoAvatarFloatingBoot.claimDirectTutorialBoot(round, source || 'avatar-floating-guide-start');
    }

    releaseDirectAvatarFloatingTutorialBoot(reason, options) {
        if (window.NekoAvatarFloatingBoot && typeof window.NekoAvatarFloatingBoot.releaseDirectTutorialBoot === 'function') {
            window.NekoAvatarFloatingBoot.releaseDirectTutorialBoot(reason || 'avatar-floating-guide-release', options || {});
        }
    }

    dispatchAvatarFloatingTutorialInputRestored(reason = 'tutorial-avatar-restored') {
        const detail = {
            action: 'yui_guide_tutorial_input_restored',
            reason,
            page: this.getYuiGuidePageKey(),
            runtimePage: this.currentPage,
            timestamp: Date.now()
        };
        try {
            window.dispatchEvent(new CustomEvent('neko:yui-guide:tutorial-input-restored', { detail }));
        } catch (_) {}
        try {
            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToPet === 'function'
            ) {
                window.nekoTutorialOverlay.relayToPet(detail);
            }
        } catch (_) {}
    }

    async recoverUserModelAfterDirectTutorialBootFailure(reason) {
        if (window.NekoAvatarFloatingBoot && typeof window.NekoAvatarFloatingBoot.recoverUserModelBoot === 'function') {
            try {
                return await window.NekoAvatarFloatingBoot.recoverUserModelBoot(reason || 'direct-tutorial-boot-failed');
            } catch (error) {
                console.warn('[Tutorial] direct tutorial boot 恢复用户模型失败:', reason || 'unknown', error);
            }
        }
        if (typeof window.showCurrentModel === 'function') {
            try {
                await window.showCurrentModel();
                return true;
            } catch (error) {
                console.warn('[Tutorial] showCurrentModel 恢复用户模型失败:', reason || 'unknown', error);
            }
        }
        return false;
    }

    waitForTutorialModelHostReady(maxWaitTime = 12000) {
        const hasHost = () => !!(
            document.body
            && document.getElementById('live2d-container')
            && document.getElementById('live2d-canvas')
        );
        if (hasHost()) {
            return Promise.resolve(true);
        }
        return new Promise((resolve) => {
            let resolved = false;
            const done = (result) => {
                if (resolved) {
                    return;
                }
                resolved = true;
                clearTimeout(timer);
                clearInterval(poller);
                resolve(result);
            };
            const poller = setInterval(() => {
                if (hasHost()) {
                    done(true);
                }
            }, 50);
            const timer = setTimeout(() => {
                console.warn(`[Tutorial] 等待教程模型容器超时（${maxWaitTime / 1000}秒）`);
                done(false);
            }, maxWaitTime);
        });
    }

    async maybeStartAvatarFloatingGuideAutoRound(delayMs = 1200) {
        if (this.currentPage !== 'home' || this.isTutorialRunning || window.isInTutorial) {
            return false;
        }
        const round = this.getNextAvatarFloatingGuideAutoRound();
        if (!round) {
            return false;
        }
        this.managerResources.setTimeout(() => {
            if (this._isDestroyed || window.universalTutorialManager !== this) {
                return;
            }
            if (this.currentPage !== 'home' || this.isTutorialRunning || window.isInTutorial) {
                return;
            }
            if (!this.isAvatarFloatingGuideRoundPendingAutoStart(round)) {
                this.dispatchStartupGreetingRelease('avatar-floating-round-not-pending', { day: round });
                return;
            }
            if (!this.isAvatarFloatingGuideRoundRegistered(round)) {
                this.dispatchStartupGreetingRelease('avatar-floating-round-not-registered', { day: round });
                return;
            }
            this.startAvatarFloatingGuideRound(round, { source: 'auto' }).then((result) => {
                if (result === false) {
                    this.dispatchStartupGreetingRelease('avatar-floating-round-start-skipped', { day: round });
                }
            }).catch((error) => {
                console.warn('[Tutorial] 自动启动悬浮窗教程失败:', round, error);
                this.dispatchStartupGreetingRelease('avatar-floating-round-start-failed', { day: round });
            });
        }, Math.max(0, Number.isFinite(delayMs) ? delayMs : 1200));
        return true;
    }

    ensureTutorialSkipController() {
        if (!this._tutorialSkipController
            && window.TutorialSkipController
            && typeof window.TutorialSkipController.createController === 'function') {
            this._tutorialSkipController = window.TutorialSkipController.createController({
                document: document,
                buttonId: 'neko-tutorial-skip-btn'
            });
        }
        return this._tutorialSkipController;
    }

    ensureTutorialAvatarReloadController() {
        if (!this._tutorialAvatarReloadController
            && window.TutorialAvatarReloadController
            && typeof window.TutorialAvatarReloadController.createController === 'function') {
            this._tutorialAvatarReloadController = window.TutorialAvatarReloadController.createController({
                host: this,
                timeoutMs: TUTORIAL_AVATAR_OVERRIDE_TIMEOUT_MS,
                tutorialModelName: TUTORIAL_YUI_LIVE2D_MODEL_NAME,
                resolveCurrentName: () => this.resolveCurrentTutorialCatgirlName(),
                fetchCharacters: () => this.fetchTutorialCharacters(),
                buildSnapshotPayload: (currentConfig) => this.buildTutorialModelSavePayload(currentConfig),
                fadeOutCurrentModel: () => this.fadeOutCurrentTutorialSourceModel(),
                reloadModel: (currentName, payload, options) => this.reloadTutorialModel(currentName, payload, options),
                setPreparing: (preparing) => this.setTutorialLive2dPreparing(preparing),
                revealPrepared: () => this.revealTutorialLive2dPrepared(),
                applyIdentityOverride: (payload) => this.applyTutorialChatIdentityOverride(payload),
                clearViewportWatcher: () => this.clearTutorialLive2dViewportPlacementWatcher()
            });
        }
        return this._tutorialAvatarReloadController;
    }

    ensureTutorialRoundPreludeController() {
        if (!this._tutorialRoundPreludeController && TutorialRoundPreludeController) {
            this._tutorialRoundPreludeController = new TutorialRoundPreludeController({
                beginAvatarOverride: (overrideOptions) => this.beginTutorialAvatarOverride(overrideOptions),
                revealPrepared: () => this.revealTutorialLive2dPrepared(),
                ensureVisible: (sceneId, ensureOptions) => this.ensureTutorialYuiLive2dVisible(sceneId, ensureOptions),
                waitForAvatarReady: (sceneId, _options) => this.waitForTutorialYuiLive2dVisualReady(sceneId, 12000),
                sleep: (delayMs) => this.sleep(delayMs),
                beginTakingOver: (detail) => {
                    const director = detail && detail.director;
                    if (director && typeof director.setTutorialTakingOver === 'function') {
                        director.setTutorialTakingOver(true);
                    }
                },
                setLifecycleActive: (active) => {
                    this._yuiGuideLifecycleActive = active === true;
                },
                showSkipButton: () => this.showSkipButton(),
                dispatchStarted: (detail) => {
                    window.dispatchEvent(new CustomEvent('neko:avatar-floating-guide-started', {
                        detail: detail
                    }));
                },
                warn: (...args) => console.warn(...args)
            });
        }
        return this._tutorialRoundPreludeController;
    }

    /**
     * 获取翻译文本的辅助函数
     * @param {string} key - 翻译键，格式: tutorial.{page}.step{n}.{title|desc}
     * @param {string} fallback - 备用文本（如果翻译不存在）
     */
    t(key, fallback = '') {
        if (window.t && typeof window.t === 'function') {
            return window.t(key, fallback);
        }
        return fallback;
    }

    getYuiGuideRegistry() {
        try {
            if (typeof window.getYuiGuideStepsRegistry === 'function') {
                return window.getYuiGuideStepsRegistry() || null;
            }
        } catch (error) {
            console.error('[Tutorial] 获取 Yui Guide 注册表失败:', error);
        }

        return window.YuiGuideStepsRegistry || null;
    }

    isYuiGuideAvailable() {
        return !!this.getYuiGuideRegistry();
    }

    getYuiGuideHandoffApi() {
        return window.YuiGuidePageHandoff || null;
    }

    getYuiGuidePageKey(page = this.currentPage) {
        const path = window.location.pathname || '';
        const normalizedPage = typeof page === 'string' ? page : '';

        if (normalizedPage === 'settings' && path.includes('api_key')) {
            return 'api_key';
        }

        if (
            normalizedPage === 'plugin_dashboard' ||
            path.includes('/api/agent/user_plugin/dashboard') ||
            path === '/ui' ||
            path.startsWith('/ui/')
        ) {
            return 'plugin_dashboard';
        }

        return normalizedPage;
    }

    getYuiGuideHandoffExpectedPages() {
        const pageKey = this.getYuiGuidePageKey();

        if (pageKey === 'api_key') {
            return ['api_key', 'settings'];
        }

        if (pageKey === 'memory_browser') {
            return ['memory_browser'];
        }

        if (pageKey === 'plugin_dashboard') {
            return ['plugin_dashboard'];
        }

        return [];
    }

    async consumePendingYuiGuideHandoffToken() {
        if (this._yuiGuideHandoffToken) {
            return this._yuiGuideHandoffToken;
        }

        const handoffApi = this.getYuiGuideHandoffApi();
        if (!handoffApi || typeof handoffApi.consumeHandoffToken !== 'function') {
            return null;
        }

        const expectedPages = this.getYuiGuideHandoffExpectedPages();
        if (!Array.isArray(expectedPages) || expectedPages.length === 0) {
            return null;
        }

        for (const expectedPage of expectedPages) {
            try {
                const token = await handoffApi.consumeHandoffToken(expectedPage);
                if (token) {
                    this._yuiGuideHandoffToken = token;
                    return token;
                }
            } catch (error) {
                console.error('[Tutorial] 消费 Yui Guide handoff token 失败:', expectedPage, error);
            }
        }

        return null;
    }

    isYuiGuideEnabledForPage(page = this.currentPage) {
        const pageKey = this.getYuiGuidePageKey(page);
        return pageKey === 'home' && this.isAvatarFloatingGuideRoundRegistered(1);
    }

    ensureYuiGuideDirector() {
        if (
            this.yuiGuideDirector
            && (this.yuiGuideDirector.destroyed || this.yuiGuideDirector.terminationRequested)
        ) {
            try {
                if (typeof this.yuiGuideDirector.destroy === 'function') {
                    this.yuiGuideDirector.destroy();
                }
            } catch (error) {
                console.warn('[Tutorial] 清理已终止的 Yui Guide Director 失败:', error);
            }
            this.yuiGuideDirector = null;
            this._yuiGuideLastSceneId = null;
            this._yuiGuideLifecycleActive = false;
        }

        if (this.yuiGuideDirector) {
            return this.yuiGuideDirector;
        }

        if (!this.isYuiGuideEnabledForPage()) {
            return null;
        }

        if (typeof window.createYuiGuideDirector !== 'function') {
            return null;
        }

        try {
            let homeInteractionApi = null;
            if (typeof window.getYuiGuideHomeInteractionApi === 'function') {
                try {
                    homeInteractionApi = window.getYuiGuideHomeInteractionApi() || null;
                } catch (error) {
                    console.warn('[Tutorial] 获取首页交互 API 失败，改用兜底实现:', error);
                }
            }
            if (!homeInteractionApi) {
                homeInteractionApi = window.YuiGuideHomeInteractionApi || window.YuiGuidePageHandoff || null;
            }

            const director = window.createYuiGuideDirector({
                tutorialManager: this,
                page: this.getYuiGuidePageKey(),
                registry: this.getYuiGuideRegistry(),
                homeInteractionApi: homeInteractionApi
            });

            if (director && typeof director === 'object') {
                this.yuiGuideDirector = director;
                return director;
            }

            console.warn('[Tutorial] createYuiGuideDirector 返回了无效对象');
        } catch (error) {
            console.error('[Tutorial] 创建 Yui Guide Director 失败:', error);
        }

        return null;
    }

    dispatchYuiGuideEvent(name, detail = {}) {
        if (!this.isYuiGuideEnabledForPage()) {
            return;
        }

        if (typeof window.dispatchEvent !== 'function' || typeof CustomEvent === 'undefined') {
            return;
        }

        const payload = Object.assign({
            currentPage: this.currentPage,
            yuiGuidePage: this.getYuiGuidePageKey(),
            tutorialManager: this,
            timestamp: Date.now()
        }, detail);

        window.dispatchEvent(new CustomEvent(`neko:yui-guide:${name}`, {
            detail: payload
        }));
    }

    notifyYuiGuideTutorialEnd(reason = 'destroy') {
        const detail = this.lifecycleStateStore.createYuiGuideEndDetail({
            page: this.getYuiGuidePageKey(),
            runtimePage: this.currentPage,
            reason: reason
        });

        if (!this.isYuiGuideEnabledForPage()) {
            this.yuiGuideDirector = null;
            this._yuiGuideLastSceneId = null;
            this._yuiGuideLifecycleActive = false;
            return;
        }

        if (!this._yuiGuideLifecycleActive && !this._yuiGuideLastSceneId && !this.yuiGuideDirector) {
            return;
        }

        this.dispatchYuiGuideEvent('tutorial-end', detail);
        if (this.yuiGuideDirector && typeof this.yuiGuideDirector.destroy === 'function') {
            try {
                this.yuiGuideDirector.destroy();
            } catch (error) {
                console.warn('[Tutorial] 销毁 Yui Guide Director 失败:', error);
            }
        }
        this.yuiGuideDirector = null;
        this._yuiGuideLastSceneId = null;
        this._yuiGuideLifecycleActive = false;
        if (this.getYuiGuidePageKey() !== 'home') {
            this._yuiGuideHandoffToken = null;
        }
    }

    clearAllTutorialLifecycles(reason = 'destroy') {
        const rawReason = this.normalizeTutorialEndRawReason(reason);
        const director = this.yuiGuideDirector;
        this.syncPcSystemCursorHidden(false, rawReason);
        this.clearYuiGuideCompactChatFixedLayout(rawReason);
        // 恢复请求必须先于 lifecycle-ended 发出；聊天窗关闭本轮生命周期后会拒绝迟到的教程命令。
        this.restoreYuiGuideCompactChatSurface(rawReason);

        try {
            this.notifyYuiGuideTutorialEnd(rawReason);
        } catch (error) {
            console.warn('[Tutorial] 清理 Yui Guide 生命周期失败:', error);
        }

        try {
            if (director && typeof director.destroy === 'function') {
                director.destroy();
            }
        } catch (error) {
            console.warn('[Tutorial] 销毁 Yui Guide Director 失败:', error);
        }

        this.yuiGuideDirector = null;
        this._yuiGuideLastSceneId = null;
        this._yuiGuideLifecycleActive = false;
        if (this.getYuiGuidePageKey() !== 'home') {
            this._yuiGuideHandoffToken = null;
        }

        try {
            this.hideSkipButton();
        } catch (error) {
            console.warn('[Tutorial] 清理跳过按钮失败:', error);
        }
        try {
            this.restoreYuiGuideChatInputState(rawReason);
        } catch (error) {
            console.warn('[Tutorial] 恢复教程聊天输入状态失败:', error);
        }
        this.clearPcTutorialGlobalOverlay(rawReason);
        try {
            if (window.localStorage) {
                window.localStorage.removeItem(YUI_GUIDE_CHAT_BRIDGE_QUEUE_KEY);
            }
        } catch (error) {
            console.warn('[Tutorial] 清理教程聊天桥队列失败:', error);
        }
        try {
            window.dispatchEvent(new CustomEvent('neko:yui-guide:tutorial-lifecycle-ended', {
                detail: { reason: rawReason }
            }));
        } catch (error) {
            console.warn('[Tutorial] 广播教程生命周期结束失败:', error);
        }
    }

    normalizeTutorialEndRawReason(reason) {
        return this.lifecycleStateStore.normalizeRawReason(reason);
    }

    normalizeTutorialEndReason(reason) {
        return this.lifecycleStateStore.normalizeReason(reason);
    }

    setTutorialEndReason(reason) {
        return this.lifecycleStateStore.setEndReason(reason);
    }

    resolveTutorialEndMeta(finalSteps = this.cachedValidSteps || []) {
        return this.lifecycleStateStore.resolveEndMeta({
            finalSteps: finalSteps,
            currentStep: this.currentStep
        });
    }

    syncPcSystemCursorHidden(hidden, reason = 'tutorial') {
        if (
            window.YuiGuideCommon
            && typeof window.YuiGuideCommon.syncPcSystemCursorHidden === 'function'
        ) {
            window.YuiGuideCommon.syncPcSystemCursorHidden(hidden === true, reason);
        }
    }

    ensurePcTutorialGlobalOverlayStarted(reason = 'tutorial-started') {
        let tutorialRunId = '';
        try {
            tutorialRunId = window.localStorage
                ? (window.localStorage.getItem('yuiGuidePcOverlayRunId') || '')
                : '';
        } catch (_) {}
        if (!tutorialRunId) {
            tutorialRunId = 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
            try {
                if (window.localStorage) {
                    window.localStorage.setItem('yuiGuidePcOverlayRunId', tutorialRunId);
                }
            } catch (_) {}
        }

        const overlay = window.nekoTutorialOverlay;
        if (!overlay || typeof overlay.begin !== 'function') {
            // 纯 BroadcastChannel 页面同样需要稳定 runId，才能在上一轮 closed 后
            // 用 lifecycle-start 重新打开独立聊天页，并过滤迟到的旧教程消息。
            return tutorialRunId;
        }

        try {
            const beginResult = overlay.begin({
                tutorialRunId: tutorialRunId,
                reason: reason
            });
            Promise.resolve(beginResult).then(result => {
                if (result && result.stale === true) {
                    try {
                        overlay.clear({ reason: reason, tutorialRunId: tutorialRunId });
                    } catch (_) {}
                }
            }).catch(() => {});
        } catch (error) {
            console.warn('[Tutorial] Failed to begin PC tutorial global overlay:', error);
        }
        return tutorialRunId;
    }

    relayYuiGuideTutorialLifecycleStarted(page, source) {
        let tutorialRunId = this.ensurePcTutorialGlobalOverlayStarted('tutorial-lifecycle-started');
        try {
            tutorialRunId = tutorialRunId || (window.localStorage
                ? (window.localStorage.getItem('yuiGuidePcOverlayRunId') || '')
                : '');
        } catch (_) {}

        const startedMessage = {
            action: 'yui_guide_tutorial_lifecycle_started',
            page: page || this.currentPage,
            source: source || this.currentTutorialStartSource || 'auto',
            tutorialRunId: tutorialRunId,
            timestamp: Date.now()
        };

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                // 浏览器独立聊天页没有原生 overlay relay；必须通过同一 BroadcastChannel
                // 先打开本轮 lifecycle，随后发送的 prepare 才不会被 closed guard 丢弃。
                channel.postMessage(startedMessage);
            } catch (error) {
                console.warn('[Tutorial] 广播 Yui Guide 生命周期开始到独立聊天窗失败:', error);
            }
        }

        try {
            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToChat === 'function'
            ) {
                window.nekoTutorialOverlay.relayToChat(startedMessage);
            }
        } catch (error) {
            console.warn('[Tutorial] 原生转发 Yui Guide 生命周期开始到聊天窗失败:', error);
        }
        try {
            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToPet === 'function'
            ) {
                window.nekoTutorialOverlay.relayToPet(startedMessage);
            }
        } catch (error) {
            console.warn('[Tutorial] 原生转发 Yui Guide 生命周期开始到桌宠窗失败:', error);
        }
    }

    syncYuiGuideCompactChatFixedLayout(fixed, reason = 'tutorial') {
        const normalizedReason = typeof reason === 'string' && reason.trim()
            ? reason.trim()
            : 'tutorial';
        let tutorialRunId = '';
        try {
            tutorialRunId = window.localStorage
                ? (window.localStorage.getItem('yuiGuidePcOverlayRunId') || '')
                : '';
        } catch (_) {}

        const message = {
            action: 'yui_guide_set_compact_chat_fixed_layout',
            fixed: fixed === true,
            reason: normalizedReason,
            tutorialRunId: tutorialRunId,
            timestamp: Date.now()
        };

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage(message);
            } catch (error) {
                console.warn('[Tutorial] 同步胶囊聊天框固定布局失败:', error);
            }
        }

        if (
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.relayToChat === 'function'
        ) {
            try {
                window.nekoTutorialOverlay.relayToChat(message);
            } catch (error) {
                console.warn('[Tutorial] 原生转发胶囊聊天框固定布局失败:', error);
            }
        }
    }

    prepareYuiGuideCompactChatForTutorial() {
        const requestId = `tutorial-compact-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        let tutorialRunId = '';
        try {
            tutorialRunId = window.localStorage
                ? (window.localStorage.getItem('yuiGuidePcOverlayRunId') || '')
                : '';
            if (!tutorialRunId) {
                // 正常入口会复用 lifecycle-start 创建的 runId；独立调用或旧入口缺失时仍需
                // 在排队前补一个稳定 id，让迟到的旧 lifecycle-ended 只清理旧教程请求。
                tutorialRunId = 'yui-guide-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
                if (window.localStorage) {
                    window.localStorage.setItem('yuiGuidePcOverlayRunId', tutorialRunId);
                }
            }
        } catch (_) {}
        const message = {
            action: 'yui_guide_prepare_compact_chat',
            requestId: requestId,
            tutorialRunId: tutorialRunId,
            reason: 'avatar-floating-guide-start',
            timestamp: Date.now()
        };

        return new Promise((resolve) => {
            let settled = false;
            let posted = false;
            const finish = (ready, detail) => {
                if (settled) return;
                settled = true;
                window.removeEventListener('neko:yui-guide:compact-chat-ready', handleReady);
                window.clearTimeout(timeoutId);
                const response = detail && typeof detail === 'object' ? detail : {};
                // 只在握手完成后保存启动前形态；上一轮迟到回执不能覆盖本轮恢复目标。
                this._avatarFloatingChatSurfaceSnapshot = {
                    requestId: requestId,
                    wasCollapsed: response.wasCollapsed === true,
                    // 浏览器主页可能同时准备本地 host 并广播给独立 /chat；恢复时必须区分两者，
                    // 不能因 BroadcastChannel 已发送就跳过本地毛球形态的恢复。
                    localHostPrepared: response.localHostPrepared === true,
                    // 超时只说明主页没收到回执；聊天窗可能已保存真实形态并仍在异步展开。
                    // teardown 通过同一 requestId 请求它按自己的 prepare 快照恢复，避免主页猜测。
                    restoreFromPrepareSnapshot: response.restoreFromPrepareSnapshot === true
                };
                resolve(ready === true);
            };
            const handleReady = (event) => {
                const detail = event && event.detail && typeof event.detail === 'object'
                    ? event.detail
                    : {};
                if (detail.requestId !== requestId) return;
                finish(detail.ready !== false, detail);
            };
            const timeoutId = window.setTimeout(() => {
                console.warn('[Tutorial] 等待胶囊聊天框原生展开超时，取消本轮教程启动');
                finish(false, { restoreFromPrepareSnapshot: posted });
            }, YUI_GUIDE_COMPACT_CHAT_PREPARE_TIMEOUT_MS);
            window.addEventListener('neko:yui-guide:compact-chat-ready', handleReady);

            const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
            if (channel && typeof channel.postMessage === 'function') {
                try {
                    // BroadcastChannel 是否有接收端无法从主页判断；先发送给可能存在的独立 /chat，
                    // 再由下方本地 fallback 决定是否需要等待回执。
                    channel.postMessage(message);
                    posted = true;
                } catch (error) {
                    console.warn('[Tutorial] 广播胶囊聊天框准备请求失败:', error);
                }
            }

            const localHost = window.reactChatWindowHost || null;
            const hasNativeChatRelay = !!(
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToChat === 'function'
            );
            if (!hasNativeChatRelay && localHost && typeof localHost.openWindow === 'function') {
                // 单窗口浏览器也可能创建 BroadcastChannel，但没有独立 /chat 页面负责回执；
                // 此时直接准备当前页的 React chat host，避免等待一个永远不会出现的回执。
                const wasCollapsed = !!(
                    typeof localHost.getChatSurfaceMode === 'function'
                    && localHost.getChatSurfaceMode() === 'minimized'
                );
                localHost.openWindow();
                finish(true, {
                    wasCollapsed: wasCollapsed,
                    localHostPrepared: true,
                    // 若独立 /chat 实际存在，它已按同 requestId 保存自己的折叠快照；
                    // teardown 仍要广播恢复，但单窗口页面不等待一个不存在的回执。
                    restoreFromPrepareSnapshot: posted
                });
                return;
            }

            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToChat === 'function'
            ) {
                try {
                    window.nekoTutorialOverlay.relayToChat(message);
                    posted = true;
                } catch (error) {
                    console.warn('[Tutorial] 原生转发胶囊聊天框准备请求失败:', error);
                }
            }
            if (!posted) {
                // 没有独立聊天窗的浏览器环境无需等待原生 carrier；本地 React 窗口沿用原行为。
                const wasCollapsed = !!(
                    localHost
                    && typeof localHost.getChatSurfaceMode === 'function'
                    && localHost.getChatSurfaceMode() === 'minimized'
                );
                if (localHost && typeof localHost.openWindow === 'function') {
                    localHost.openWindow();
                }
                finish(true, { wasCollapsed: wasCollapsed });
            }
        });
    }

    restoreYuiGuideCompactChatSurface(reason = 'tutorial-ended') {
        const snapshot = this._avatarFloatingChatSurfaceSnapshot;
        this._avatarFloatingChatSurfaceSnapshot = null;
        if (!snapshot || (
            snapshot.wasCollapsed !== true
            && snapshot.restoreFromPrepareSnapshot !== true
        )) return false;

        const message = {
            action: 'yui_guide_restore_compact_chat',
            requestId: snapshot.requestId,
            wasCollapsed: snapshot.wasCollapsed === true,
            // prepare 超时后由聊天窗使用同 request 的本地快照决定是否需要折叠。
            restoreFromPrepareSnapshot: snapshot.restoreFromPrepareSnapshot === true,
            reason: reason,
            timestamp: Date.now()
        };
        let posted = false;
        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage(message);
                posted = true;
            } catch (error) {
                console.warn('[Tutorial] 广播胶囊聊天框形态恢复失败:', error);
            }
        }
        if (
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.relayToChat === 'function'
        ) {
            try {
                window.nekoTutorialOverlay.relayToChat(message);
                posted = true;
            } catch (error) {
                console.warn('[Tutorial] 原生转发胶囊聊天框形态恢复失败:', error);
            }
        }
        if (
            snapshot.wasCollapsed === true
            && (snapshot.localHostPrepared === true || !posted)
        ) {
            const host = window.reactChatWindowHost || null;
            if (host && typeof host.setChatSurfaceMode === 'function') {
                // 只恢复明确由主页准备过、或完全没有 carrier 的本地毛球；外部回执/超时
                // 仍由保存过 prepare 快照的独立聊天窗判断，主页不能猜测并强制折叠。
                host.setChatSurfaceMode('minimized');
                return true;
            }
        }
        return posted;
    }

    clearPcTutorialGlobalOverlay(reason = 'destroy') {
        const rawReason = this.normalizeTutorialEndRawReason(reason);
        let tutorialRunId = '';
        try {
            tutorialRunId = window.localStorage
                ? (window.localStorage.getItem('yuiGuidePcOverlayRunId') || '')
                : '';
        } catch (_) {}
        const lifecycleEndedMessage = {
            action: 'yui_guide_tutorial_lifecycle_ended',
            tutorialRunId: tutorialRunId,
            reason: rawReason,
            timestamp: Date.now()
        };
        try {
            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.clear === 'function'
            ) {
                const clearResult = window.nekoTutorialOverlay.clear({
                    reason: rawReason,
                    tutorialRunId: tutorialRunId
                });
                Promise.resolve(clearResult).then(result => {
                    if (result && (result.stale === true || result.ok === false)) {
                        window.nekoTutorialOverlay.clear({ reason: rawReason });
                    }
                }).catch(() => {
                    try {
                        window.nekoTutorialOverlay.clear({ reason: rawReason });
                    } catch (_) {}
                });
            }
        } catch (error) {
            console.warn('[Tutorial] Failed to clear PC tutorial global overlay:', error);
        }
        try {
            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToChat === 'function'
            ) {
                window.nekoTutorialOverlay.relayToChat(lifecycleEndedMessage);
            }
        } catch (_) {}
        try {
            if (
                window.nekoTutorialOverlay
                && typeof window.nekoTutorialOverlay.relayToPet === 'function'
            ) {
                window.nekoTutorialOverlay.relayToPet(lifecycleEndedMessage);
            }
        } catch (_) {}
        try {
            if (
                window.appInterpage
                && window.appInterpage.nekoBroadcastChannel
                && typeof window.appInterpage.nekoBroadcastChannel.postMessage === 'function'
            ) {
                window.appInterpage.nekoBroadcastChannel.postMessage(lifecycleEndedMessage);
            }
        } catch (_) {}
        try {
            if (
                window.localStorage
                && (!tutorialRunId || window.localStorage.getItem('yuiGuidePcOverlayRunId') === tutorialRunId)
            ) {
                window.localStorage.removeItem('yuiGuidePcOverlayRunId');
            }
        } catch (_) {}
    }

    requestTutorialEnd(reason = 'destroy') {
        this.setTutorialEndReason(reason);
        this.clearAllTutorialLifecycles(reason);
        return this.onTutorialEnd();
    }

    requestTutorialDestroy(reason = 'destroy') {
        return this.requestTutorialEnd(reason);
    }

    requestAvatarFloatingGuideCooperativeEnd(reason = 'skip') {
        const director = this.yuiGuideDirector;
        if (
            !this.activeAvatarFloatingGuideRound
            || !director
            || director.destroyed
            || this._tutorialEndHandled
        ) {
            return false;
        }

        return this.requestTutorialEnd(reason);
    }

    handleDesktopYuiGuideSkipRequest(event) {
        if (this._isDestroyed) {
            return;
        }

        const detail = event && event.detail && typeof event.detail === 'object'
            ? event.detail
            : {};
        const skipButtonVisible = !!document.getElementById('neko-tutorial-skip-btn');
        if (!this.isTutorialRunning && !window.isInTutorial && !skipButtonVisible) {
            return;
        }

        this.logTutorialFlow('desktop-yui-guide-skip-request', {
            page: this.currentPage,
            session_id: detail.sessionId || '',
            source: detail.source || 'desktop',
        });
        void this.handleTutorialSkipRequest();
    }

    async destroy(reason = 'destroy') {
        this.setTutorialEndReason(reason);
        this._isDestroyed = true;
        this.clearAllTutorialLifecycles(reason);

        if (this.managerResources) {
            this.managerResources.destroy();
            this.managerResources = null;
            this._desktopYuiGuideSkipHandler = null;
        }

        this.clearTutorialLive2dViewportPlacementWatcher();

        if (this._teardownTutorialUI) {
            await this._teardownTutorialUI();
        }
    }

    broadcastYuiGuideTerminationRequest(endMeta = {}) {
        const yuiGuidePageKey = this.isYuiGuideEnabledForPage()
            ? this.getYuiGuidePageKey()
            : '';
        const message = this.lifecycleStateStore.createTerminationRequest({
            sourcePage: yuiGuidePageKey,
            rawReason: endMeta.rawReason,
            reason: endMeta.reason
        });
        if (!message) {
            return;
        }
        let tutorialRunId = '';
        try {
            tutorialRunId = window.localStorage
                ? (window.localStorage.getItem('yuiGuidePcOverlayRunId') || '')
                : '';
        } catch (_) {}
        message.tutorialRunId = tutorialRunId;

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage(message);
            } catch (error) {
                console.warn('[Tutorial] 广播 Yui Guide 跨页终止请求失败:', error);
            }
        }
        if (
            window.nekoTutorialOverlay
            && typeof window.nekoTutorialOverlay.relayToPet === 'function'
        ) {
            try {
                window.nekoTutorialOverlay.relayToPet(message);
            } catch (error) {
                console.warn('[Tutorial] 原生转发 Yui Guide 跨页终止请求失败:', error);
            }
        }
    }

    /**
     * 检查 i18n 是否已准备好（window.t 可用且 i18next 已初始化）
     */
    isI18nReady() {
        const i18nInstance = window.i18n || (typeof i18next !== 'undefined' ? i18next : null);
        return typeof window.t === 'function' && !!(i18nInstance && i18nInstance.isInitialized);
    }

    /**
     * 等待 i18n 就绪后再启动引导，避免回退到硬编码文案
     */
    startTutorialWhenI18nReady(delayMs = 0) {
        if (this.isTutorialRunning || window.isInTutorial) {
            // 已在引导中：消耗掉本次启动意图，避免遗留到下次刷新
            this.consumeTutorialStartSource();
            return;
        }

        if (this._pendingI18nStart) {
            return;
        }

        const launchTutorial = () => {
            setTimeout(() => {
                this._pendingI18nStart = false;
                const homeRound = this.currentPage === 'home'
                    ? this.getHomeAvatarFloatingGuideLaunchRound()
                    : null;
                if (homeRound) {
                    const source = this.consumeTutorialStartSource();
                    this.startAvatarFloatingGuideRound(homeRound, { source }).then((result) => {
                        if (result === false) {
                            this.recoverUserModelAfterDirectTutorialBootFailure('avatar-floating-round-start-skipped');
                            this.dispatchStartupGreetingRelease('avatar-floating-round-start-skipped', { day: homeRound });
                        }
                    }).catch(error => {
                        console.error('[Tutorial] 首页 Day' + homeRound + ' 悬浮窗教程启动失败:', error);
                        this.dispatchStartupGreetingRelease('avatar-floating-round-start-failed', { day: homeRound });
                    });
                    return;
                }
                const started = this.startTutorial();
                if (!started) {
                    this.dispatchStartupGreetingRelease('tutorial-start-not-needed');
                }
            }, delayMs);
        };

        if (this.isI18nReady()) {
            launchTutorial();
            return;
        }

        this._pendingI18nStart = true;

        let pollTimer = null;
        let timeoutTimer = null;

        const cleanup = () => {
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
            if (timeoutTimer) {
                clearTimeout(timeoutTimer);
                timeoutTimer = null;
            }
            window.removeEventListener('localechange', onLocaleReady);
        };

        const onLocaleReady = () => {
            if (!this.isI18nReady()) {
                return;
            }
            cleanup();
            launchTutorial();
        };

        window.addEventListener('localechange', onLocaleReady);
        pollTimer = setInterval(onLocaleReady, 100);

        // 容错：如果语言系统异常，超时后仍允许教程启动
        timeoutTimer = setTimeout(() => {
            cleanup();
            launchTutorial();
        }, 5000);
    }

    shouldStartHomeAvatarFloatingGuideRound() {
        return !!this.getHomeAvatarFloatingGuideLaunchRound();
    }

    /**
     * HTML转义辅助函数 - 用于在HTML属性或内容中安全使用翻译文本
     * @param {string} text - 要转义的文本
     * @returns {string} 转义后的HTML安全文本
     */
    safeEscapeHtml(text) {
        if (typeof text !== 'string') {
            return String(text);
        }
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    shouldShowDay1SystrayIntro(endMeta, avatarFloatingEndState) {
        if (!endMeta || !avatarFloatingEndState) {
            return false;
        }
        if (avatarFloatingEndState.day !== 1) {
            return false;
        }
        return endMeta.reason === 'complete' || endMeta.reason === 'skip';
    }

    scheduleDay1SystrayIntroAfterTeardown(teardownPromise, endMeta, avatarFloatingEndState) {
        if (!this.shouldShowDay1SystrayIntro(endMeta, avatarFloatingEndState)) {
            return teardownPromise;
        }
        return Promise.resolve(teardownPromise).finally(() => {
            try {
                this.showDay1SystrayIntroModal(endMeta, avatarFloatingEndState);
            } catch (error) {
                console.warn('[Tutorial] 第一天系统托盘介绍弹窗显示失败:', error);
            }
        });
    }

    closeDay1SystrayIntroModal(options = {}) {
        const existing = document.getElementById('neko-day1-systray-intro-modal');
        const hadBodyClass = !!(document.body && document.body.classList.contains('neko-day1-systray-intro-open'));
        const shouldNotify = options.notify !== false;
        if (existing) {
            existing.remove();
        }
        if (document.body) {
            document.body.classList.remove('neko-day1-systray-intro-open');
        }
        if (shouldNotify && (existing || hadBodyClass)) {
            window.dispatchEvent(new CustomEvent('neko:day1-systray-intro-closed', {
                detail: {
                    source: 'day1_systray_intro',
                    reason: 'closed',
                    timestamp: Date.now()
                }
            }));
        }
    }

    showDay1SystrayIntroModal(endMeta, avatarFloatingEndState) {
        if (!this.shouldShowDay1SystrayIntro(endMeta, avatarFloatingEndState) || !document.body) {
            return;
        }

        this.closeDay1SystrayIntroModal({ notify: false });

        const t = (key, fallback) => this.t(key, fallback);
        const escape = (text) => this.safeEscapeHtml(t(text.key, text.fallback));
        const overlay = document.createElement('div');
        overlay.id = 'neko-day1-systray-intro-modal';
        overlay.className = 'neko-day1-systray-intro-modal';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-labelledby', 'neko-day1-systray-intro-title');

        // 修改原因：托盘示意图已迁移到教程静态资源目录，保持弹窗资源归属一致。
        overlay.innerHTML = `
            <div class="neko-day1-systray-card">
                <button class="neko-day1-systray-close" type="button" aria-label="${this.safeEscapeHtml(t('common.close', '关闭'))}">×</button>
                <div class="neko-day1-systray-layout">
                    <div class="neko-day1-systray-media">
                        <div class="neko-day1-systray-location-copy">
                            <h2 id="neko-day1-systray-intro-title">${escape({ key: 'tutorial.systray.location.title', fallback: '📍 托盘图标位置' })}</h2>
                            <p>${escape({ key: 'tutorial.systray.location.desc', fallback: 'N.E.K.O 的图标会出现在屏幕右下角的系统托盘里，点击一下就能找到它。' })}</p>
                            <p class="neko-day1-systray-note">${escape({ key: 'tutorial.systray.location.note', fallback: '如果看不到，可以先展开托盘的小箭头，查看全部图标。' })}</p>
                        </div>
                        <img
                            src="/static/assets/tutorial/systray/stray_intro.png"
                            alt="${escape({ key: 'tutorial.systray.location.alt', fallback: '系统托盘位置示意图' })}"
                        >
                    </div>
                    <div class="neko-day1-systray-content">
                        <section class="neko-day1-systray-menu" aria-labelledby="neko-day1-systray-menu-title">
                            <h3 id="neko-day1-systray-menu-title">${escape({ key: 'tutorial.systray.menu.title', fallback: '📋 托盘菜单' })}</h3>
                            <p>${escape({ key: 'tutorial.systray.menu.desc', fallback: '右键点击系统托盘（见上一步提示）中的 N.E.K.O 图标即可打开菜单。以下是一些常用功能：' })}</p>
                            <div class="neko-day1-systray-menu-panel">
                                <div class="neko-day1-systray-menu-item">
                                    <strong>${escape({ key: 'tutorial.systray.resetPosition', fallback: '重置角色位置' })}</strong>
                                    <span>${escape({ key: 'tutorial.systray.resetPositionDesc', fallback: '猫娘跑到屏幕外时，点此恢复默认位置~' })}</span>
                                </div>
                                <div class="neko-day1-systray-menu-item">
                                    <strong>${escape({ key: 'tutorial.systray.openChat', fallback: '打开对话框' })}</strong>
                                    <span>${escape({ key: 'tutorial.systray.openChatDesc', fallback: '打开独立的对话框进行文字对话~' })}</span>
                                </div>
                                <div class="neko-day1-systray-menu-item">
                                    <strong>${escape({ key: 'tutorial.systray.hotkey', fallback: '快捷键设置' })}</strong>
                                    <span>${escape({ key: 'tutorial.systray.hotkeyDesc', fallback: '设置全局快捷键，更高效地控制 N.E.K.O~' })}</span>
                                </div>
                                <div class="neko-day1-systray-menu-item neko-day1-systray-menu-item--danger">
                                    <strong>${escape({ key: 'tutorial.systray.exit', fallback: '退出' })}</strong>
                                    <span>${escape({ key: 'tutorial.systray.exitDesc', fallback: '关闭 N.E.K.O。托盘菜单是退出应用的主要方式~' })}</span>
                                </div>
                            </div>
                        </section>
                    </div>
                </div>
                <div class="neko-day1-systray-actions">
                    <button class="neko-day1-systray-primary" type="button">${this.safeEscapeHtml(t('common.confirm', '确认'))}</button>
                </div>
            </div>
        `;

        const close = () => this.closeDay1SystrayIntroModal();
        overlay.addEventListener('click', (event) => {
            if (event.target === overlay) {
                close();
            }
        });
        overlay.querySelectorAll('button').forEach((button) => {
            button.addEventListener('click', close);
        });
        document.body.appendChild(overlay);
        document.body.classList.add('neko-day1-systray-intro-open');

        const primary = overlay.querySelector('.neko-day1-systray-primary');
        if (primary && typeof primary.focus === 'function') {
            try {
                primary.focus({ preventScroll: true });
            } catch (_) {
                primary.focus();
            }
        }
    }

    /**
     * 检测当前激活的模型类型前缀（live2d / vrm / mmd）
     * 浮动按钮等 UI 元素的 ID 以此前缀命名，如 vrm-floating-buttons、mmd-btn-mic。
     */
    static detectModelPrefix() {
        // 1. 检查 DOM 中实际存在哪种浮动按钮容器
        if (document.getElementById('vrm-floating-buttons')) return 'vrm';
        if (document.getElementById('mmd-floating-buttons')) return 'mmd';
        if (document.getElementById('pngtuber-floating-buttons')) return 'pngtuber';
        if (document.getElementById('live2d-floating-buttons')) return 'live2d';

        // 2. 回退到配置
        const cfg = window.lanlan_config && window.lanlan_config.model_type;
        if (cfg === 'vrm') return 'vrm';
        if (cfg === 'mmd') return 'mmd';
        if (cfg === 'pngtuber') return 'pngtuber';
        if (cfg === 'live3d') {
            if (window.mmdManager && window.mmdManager.currentModel) return 'mmd';
            if (window.vrmManager && window.vrmManager.currentModel) return 'vrm';
        }

        return 'live2d';
    }

    tutorialNonEmptyString(value) {
        if (value === undefined || value === null) {
            return '';
        }
        const normalized = String(value).trim();
        const lowered = normalized.toLowerCase();
        if (!normalized || lowered === 'undefined' || lowered === 'null') {
            return '';
        }
        return normalized;
    }

    tutorialReservedAvatar(config) {
        return (config && config._reserved && config._reserved.avatar) || {};
    }

    tutorialAvatarValue(config, path, legacyKeys = []) {
        const avatar = this.tutorialReservedAvatar(config);
        let current = avatar;
        for (let index = 0; index < path.length; index += 1) {
            if (!current || typeof current !== 'object') {
                current = undefined;
                break;
            }
            current = current[path[index]];
        }
        if (current !== undefined && current !== null) {
            return current;
        }
        for (let index = 0; index < legacyKeys.length; index += 1) {
            const legacyValue = config && config[legacyKeys[index]];
            if (legacyValue !== undefined && legacyValue !== null) {
                return legacyValue;
            }
        }
        return undefined;
    }

    inferTutorialLive2dModelName(modelPath) {
        const value = this.tutorialNonEmptyString(modelPath);
        if (!value) {
            return '';
        }
        const normalized = value.split('?')[0].split('#')[0].replace(/\\/g, '/');
        const segments = normalized.split('/').filter(Boolean);
        const filename = segments[segments.length - 1] || '';
        if (/\.model3\.json$/i.test(filename)) {
            return segments.length >= 2
                ? decodeURIComponent(segments[segments.length - 2])
                : decodeURIComponent(filename.replace(/\.model3\.json$/i, ''));
        }
        return value;
    }

    buildTutorialModelSavePayload(config) {
        const rawModelType = this.tutorialNonEmptyString(
            this.tutorialAvatarValue(config, ['model_type'], ['model_type'])
        ) || 'live2d';
        const modelType = rawModelType === 'vrm' ? 'live3d' : rawModelType;
        const payload = {
            model_type: modelType
        };

        if (modelType === 'live3d') {
            const live3dSubType = this.tutorialNonEmptyString(
                this.tutorialAvatarValue(config, ['live3d_sub_type'], ['live3d_sub_type'])
            ).toLowerCase();
            const vrmPath = this.tutorialNonEmptyString(
                this.tutorialAvatarValue(config, ['vrm', 'model_path'], ['vrm'])
            );
            const mmdPath = this.tutorialNonEmptyString(
                this.tutorialAvatarValue(config, ['mmd', 'model_path'], ['mmd'])
            );
            const useMmd = live3dSubType === 'mmd' || (!!mmdPath && !vrmPath);

            if (useMmd) {
                payload.mmd = mmdPath;
                const mmdAnimation = this.tutorialAvatarValue(config, ['mmd', 'animation'], ['mmd_animation']);
                const mmdIdleAnimation = this.tutorialAvatarValue(config, ['mmd', 'idle_animation'], ['mmd_idle_animation', 'mmd_idle_animations']);
                if (mmdAnimation !== undefined) payload.mmd_animation = mmdAnimation || '';
                if (mmdIdleAnimation !== undefined) payload.mmd_idle_animation = mmdIdleAnimation || [];
            } else {
                payload.vrm = vrmPath;
                const vrmAnimation = this.tutorialAvatarValue(config, ['vrm', 'animation'], ['vrm_animation']);
                const vrmIdleAnimation = this.tutorialAvatarValue(config, ['vrm', 'idle_animation'], ['idleAnimation', 'idleAnimations']);
                if (vrmAnimation !== undefined) payload.vrm_animation = vrmAnimation || '';
                if (vrmIdleAnimation !== undefined) payload.idle_animation = vrmIdleAnimation || [];
            }
            const itemId = this.tutorialNonEmptyString(
                this.tutorialAvatarValue(config, ['asset_source_id'], ['item_id', 'live2d_item_id'])
            );
            if (itemId) {
                payload.item_id = itemId;
            }
            return payload;
        }

        const live2dPath = this.tutorialAvatarValue(config, ['live2d', 'model_path'], ['live2d']);
        payload.model_type = 'live2d';
        payload.live2d = this.inferTutorialLive2dModelName(live2dPath) || TUTORIAL_YUI_LIVE2D_MODEL_NAME;

        const itemId = this.tutorialNonEmptyString(
            this.tutorialAvatarValue(config, ['asset_source_id'], ['item_id', 'live2d_item_id'])
        );
        if (itemId) {
            payload.item_id = itemId;
            payload.live2d_item_id = itemId;
        }

        const live2dIdleAnimation = this.tutorialAvatarValue(
            config,
            ['live2d', 'idle_animation'],
            ['live2d_idle_animation']
        );
        if (live2dIdleAnimation !== undefined) {
            payload.live2d_idle_animation = live2dIdleAnimation || '';
        }

        return payload;
    }

    async fetchTutorialCharacters() {
        const response = await fetch('/api/characters', {
            credentials: 'same-origin',
            cache: 'no-store'
        });
        if (!response.ok) {
            throw new Error(`characters load failed: ${response.status}`);
        }
        return response.json();
    }

    async resolveCurrentTutorialCatgirlName() {
        const configuredName = this.tutorialNonEmptyString(
            window.lanlan_config && window.lanlan_config.lanlan_name
        );
        if (configuredName) {
            return configuredName;
        }

        const response = await fetch('/api/config/page_config', {
            credentials: 'same-origin',
            cache: 'no-store'
        });
        if (!response.ok) {
            return '';
        }
        const data = await response.json();
        return this.tutorialNonEmptyString(data && data.lanlan_name);
    }

    async saveTutorialModelPayload(lanlanName, payload) {
        const response = await fetch(`/api/characters/catgirl/l2d/${encodeURIComponent(lanlanName)}`, {
            method: 'PUT',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(payload)
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) {
            throw new Error((result && result.error) || `model save failed: ${response.status}`);
        }
        return result;
    }

    buildTutorialTemporaryModelConfig(payload) {
        const modelName = this.tutorialNonEmptyString(payload && payload.live2d) || TUTORIAL_YUI_LIVE2D_MODEL_NAME;
        const modelPath = modelName === TUTORIAL_YUI_LIVE2D_MODEL_NAME
            ? TUTORIAL_YUI_LIVE2D_MODEL_PATH
            : `/live2d-models/${encodeURIComponent(modelName)}/${encodeURIComponent(modelName)}.model3.json`;

        return {
            success: true,
            model_type: 'live2d',
            live3d_sub_type: '',
            model_path: modelPath,
            lighting: window.lanlan_config && window.lanlan_config.lighting
                ? Object.assign({}, window.lanlan_config.lighting)
                : null
        };
    }

    syncTutorialLanlanModelMode(payload) {
        if (!window.lanlan_config || !payload) {
            return;
        }
        window.lanlan_config.model_type = payload.model_type || 'live2d';
        if (payload.model_type === 'live3d') {
            window.lanlan_config.live3d_sub_type = payload.mmd ? 'mmd' : 'vrm';
        } else {
            window.lanlan_config.live3d_sub_type = '';
        }
    }

    async loadTemporaryTutorialLive2dModel(payload, options = {}) {
        const deferRevealPrepared = options && options.deferRevealPrepared === true;
        const tempConfig = this.buildTutorialTemporaryModelConfig(payload);
        const modelPath = tempConfig.model_path;
        this.syncTutorialLanlanModelMode({
            model_type: 'live2d'
        });

        if (!window.live2dManager && typeof window.Live2DManager === 'function') {
            window.live2dManager = new window.Live2DManager();
        }
        if (!window.live2dManager) {
            throw new Error('Live2DManager unavailable');
        }

        if (!window.live2dManager.pixi_app || !window.live2dManager.pixi_app.renderer) {
            await window.live2dManager.initPIXI('live2d-canvas', 'live2d-container');
        }

        const vrmContainer = document.getElementById('vrm-container');
        if (vrmContainer) {
            vrmContainer.style.display = 'none';
            vrmContainer.classList.add('hidden');
        }
        const mmdContainer = document.getElementById('mmd-container');
        if (mmdContainer) {
            mmdContainer.style.display = 'none';
            mmdContainer.classList.add('hidden');
        }
        if (window.vrmManager && typeof window.vrmManager.pauseRendering === 'function') {
            window.vrmManager.pauseRendering();
        }
        if (window.mmdManager && typeof window.mmdManager.pauseRendering === 'function') {
            window.mmdManager.pauseRendering();
        }

        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.classList.remove('hidden');
            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            if (!deferRevealPrepared) {
                live2dContainer.style.removeProperty('opacity');
            }
            live2dContainer.style.setProperty('pointer-events', 'none', 'important');
        }
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.display = 'block';
            live2dCanvas.style.visibility = 'visible';
            if (!deferRevealPrepared) {
                live2dCanvas.style.removeProperty('opacity');
            }
            live2dCanvas.style.pointerEvents = 'auto';
        }

        await window.live2dManager.loadModel(modelPath, {
            isMobile: window.innerWidth <= 768,
            suppressInitialIdle: true,
            suppressPersistentExpressions: true
        });
        const loadedModel = this.getTutorialLive2dCurrentModel(window.live2dManager);
        if (!loadedModel || !this.hasTutorialYuiLive2dRenderableModel(window.live2dManager)) {
            throw new Error('tutorial_yui_live2d_model_missing_after_load');
        }
        await this.applyTutorialLive2dViewportPlacement();
        if (window.LanLan1) {
            window.LanLan1.live2dModel = loadedModel;
            window.LanLan1.currentModel = loadedModel;
        }
        if (typeof window.showLive2d === 'function') {
            window.showLive2d();
        }
        if (window.live2dManager && typeof window.live2dManager.resumeRendering === 'function') {
            window.live2dManager.resumeRendering();
        }
        this.ensureTutorialLive2dRenderActive('load-temporary-tutorial-model', {
            deferRevealPrepared
        });
    }

    isTutorialYuiLive2dActive() {
        const manager = window.live2dManager || null;
        if (!manager) {
            return false;
        }
        const loadedPath = this.tutorialNonEmptyString(manager._lastLoadedModelPath);
        const rootPath = this.tutorialNonEmptyString(manager.modelRootPath);
        const modelName = this.tutorialNonEmptyString(manager.modelName);
        return loadedPath.indexOf('/static/yui-origin/yui-origin.model3.json') >= 0
            || rootPath === '/static/yui-origin'
            || modelName === TUTORIAL_YUI_LIVE2D_MODEL_NAME;
    }

    getTutorialLive2dCurrentModel(manager = window.live2dManager || null) {
        if (!manager) {
            return null;
        }
        if (typeof manager.getCurrentModel === 'function') {
            return manager.getCurrentModel();
        }
        return manager.currentModel || null;
    }

    isTutorialLive2dModelAttachedToStage(stage, model) {
        if (!stage || !model) {
            return false;
        }
        if (model.parent === stage) {
            return true;
        }
        return Array.isArray(stage.children) && stage.children.indexOf(model) >= 0;
    }

    isTutorialLive2dRendererViewReady(app, renderer) {
        if (!app || !renderer) {
            return false;
        }
        const rendererView = renderer.view || app.view || null;
        if (!rendererView) {
            return false;
        }
        if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
            return true;
        }
        const live2dCanvas = document.getElementById('live2d-canvas');
        return !!(live2dCanvas && rendererView === live2dCanvas);
    }

    hasTutorialYuiLive2dRenderableModel(manager = window.live2dManager || null) {
        const app = manager && manager.pixi_app;
        const stage = app && app.stage;
        const renderer = app && app.renderer;
        const model = this.getTutorialLive2dCurrentModel(manager);
        const internalModel = model && model.internalModel;
        return !!(
            manager
            && model
            && !model.destroyed
            && internalModel
            && internalModel.coreModel
            && app
            && !app.destroyed
            && stage
            && !stage.destroyed
            && renderer
            && !renderer.destroyed
            && this.isTutorialLive2dModelAttachedToStage(stage, model)
            && this.isTutorialLive2dRendererViewReady(app, renderer)
        );
    }

    async ensureTutorialYuiLive2dVisible(reason = '', options = {}) {
        const deferRevealPrepared = options && options.deferRevealPrepared === true;
        if (!deferRevealPrepared) {
            this.revealTutorialLive2dPrepared();
        }
        const activeByPath = this.isTutorialYuiLive2dActive();
        if (activeByPath && this.hasTutorialYuiLive2dRenderableModel()) {
            this.ensureTutorialLive2dRenderActive('ensure-visible-active-yui', {
                deferRevealPrepared
            });
            const placementReady = await this.applyTutorialLive2dViewportPlacement();
            if (placementReady) {
                return true;
            }
            console.warn('[Tutorial] YUI 临时模型路径已激活但布局不可用，重新加载:', reason || 'unknown');
        } else if (activeByPath) {
            console.warn('[Tutorial] YUI 临时模型路径已激活但视觉对象不可用，重新加载:', reason || 'unknown');
        }

        console.warn(
            activeByPath
                ? '[Tutorial] YUI 临时模型需要重新加载以恢复视觉对象:'
                : '[Tutorial] YUI 临时模型未处于激活状态，尝试直接加载:',
            reason || 'unknown'
        );
        await this.loadTemporaryTutorialLive2dModel({
            live2d: TUTORIAL_YUI_LIVE2D_MODEL_NAME
        }, {
            deferRevealPrepared
        });
        if (!deferRevealPrepared) {
            this.revealTutorialLive2dPrepared();
        }
        this.ensureTutorialLive2dRenderActive('ensure-visible-after-direct-load', {
            deferRevealPrepared
        });
        const placementReady = await this.applyTutorialLive2dViewportPlacement();
        return this.isTutorialYuiLive2dActive()
            && this.hasTutorialYuiLive2dRenderableModel()
            && placementReady === true;
    }

    isLive2dModelLoadBusy() {
        const manager = window.live2dManager || null;
        if (!manager) {
            return false;
        }
        if (manager._isLoadingModel === true) {
            return true;
        }
        return ['preparing', 'applying', 'settling'].includes(String(manager._modelLoadState || ''));
    }

    isTutorialYuiLive2dVisualReady() {
        const manager = window.live2dManager || null;
        if (!this.isTutorialYuiLive2dActive() || !this.hasTutorialYuiLive2dRenderableModel(manager)) {
            return false;
        }
        if (manager._isLoadingModel === true) {
            return false;
        }
        const state = String(manager._modelLoadState || '');
        if (state !== 'ready') {
            return false;
        }
        if (manager._isModelReadyForInteraction !== true) {
            return false;
        }
        return true;
    }

    waitForTutorialYuiLive2dVisualReady(reason = '', maxWaitTime = 12000) {
        if (this.isTutorialYuiLive2dVisualReady()) {
            return Promise.resolve(true);
        }

        return new Promise((resolve) => {
            let resolved = false;
            const timeoutMs = Math.max(0, Math.floor(Number(maxWaitTime) || 0));
            const done = (result) => {
                if (resolved) {
                    return;
                }
                resolved = true;
                clearTimeout(timer);
                clearInterval(poller);
                window.removeEventListener('neko-live2d-model-ready', onReady);
                window.removeEventListener('live2d-model-ready', onReady);
                if (!result) {
                    console.warn('[Tutorial] 等待 YUI Live2D 视觉就绪超时，继续启动教程:', reason || 'unknown');
                }
                resolve(result);
            };
            const checkReady = () => {
                if (this.isTutorialYuiLive2dVisualReady()) {
                    done(true);
                }
            };
            const onReady = () => checkReady();
            const poller = setInterval(checkReady, 100);
            const timer = setTimeout(() => done(false), timeoutMs);
            window.addEventListener('neko-live2d-model-ready', onReady);
            window.addEventListener('live2d-model-ready', onReady);
            checkReady();
        });
    }

    waitForLive2dModelLoadIdle(maxWaitTime = 30000) {
        if (!this.isLive2dModelLoadBusy()) {
            return Promise.resolve(true);
        }

        return new Promise((resolve) => {
            let resolved = false;
            const done = (result) => {
                if (resolved) {
                    return;
                }
                resolved = true;
                clearTimeout(timer);
                clearInterval(poller);
                window.removeEventListener('neko-live2d-model-ready', onReady);
                resolve(result);
            };
            const checkIdle = () => {
                if (!this.isLive2dModelLoadBusy()) {
                    done(true);
                }
            };
            const onReady = () => checkIdle();
            const poller = setInterval(checkIdle, 100);
            const timer = setTimeout(() => done(false), maxWaitTime);
            window.addEventListener('neko-live2d-model-ready', onReady);
            checkIdle();
        });
    }

    async waitForLive2dModelLoadIdleOrThrow(reason = '', maxWaitTime = 30000) {
        const idle = await this.waitForLive2dModelLoadIdle(maxWaitTime);
        if (!idle) {
            throw new Error(`live2d_model_load_busy:${reason || 'unknown'}`);
        }
        return true;
    }

    async reloadTutorialModel(lanlanName, payload, options = {}) {
        const useTemporaryConfig = options && options.temporary === true;
        const deferRevealPrepared = options && options.deferRevealPrepared === true;
        if (typeof window.handleModelReload === 'function') {
            const reloadOptions = {
                suppressToast: true
            };
            if (useTemporaryConfig) {
                reloadOptions.temporaryConfig = this.buildTutorialTemporaryModelConfig(payload);
                reloadOptions.skipIdleRestore = true;
                reloadOptions.skipPersistentExpressions = true;
                reloadOptions.throwOnError = true;
                reloadOptions.deferRevealPrepared = deferRevealPrepared;
            }
            try {
                await this.waitForLive2dModelLoadIdleOrThrow('before-handle-model-reload');
                await window.handleModelReload(lanlanName, reloadOptions);
            } catch (error) {
                if (!useTemporaryConfig) {
                    throw error;
                }
                console.warn('[Tutorial] 临时模型热切换失败，改用直接 Live2D 加载:', error);
                await this.waitForLive2dModelLoadIdleOrThrow('before-direct-tutorial-load');
                await this.loadTemporaryTutorialLive2dModel(payload, {
                    deferRevealPrepared
                });
            }
            if (useTemporaryConfig) {
                await this.applyTutorialLive2dViewportPlacement();
            }
            return;
        }
        if (useTemporaryConfig) {
            await this.waitForLive2dModelLoadIdleOrThrow('before-direct-tutorial-load');
            await this.loadTemporaryTutorialLive2dModel(payload, {
                deferRevealPrepared
            });
            return;
        }
        this.syncTutorialLanlanModelMode(payload);
        if (typeof window.showCurrentModel === 'function') {
            await window.showCurrentModel();
        }
    }

    setTutorialLive2dPreparing(preparing) {
        if (typeof document === 'undefined' || !document.body) {
            return;
        }
        document.body.classList.toggle('yui-guide-live2d-preparing', preparing === true);
        window.nekoYuiGuideLive2dPreparing = preparing === true;
        if (preparing === true) {
            this.hideTutorialLive2dPreparingControls();
        }
    }

    hideTutorialLive2dPreparingControls() {
        if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
            return;
        }
        [
            'live2d-floating-buttons',
            'live2d-lock-icon',
            'live2d-return-button-container'
        ].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || !element.style || typeof element.style.removeProperty !== 'function') {
                return;
            }
            element.style.setProperty('display', 'none', 'important');
            element.style.setProperty('visibility', 'hidden', 'important');
            element.style.setProperty('opacity', '0', 'important');
            element.style.setProperty('pointer-events', 'none', 'important');
        });
    }

    async fadeOutCurrentTutorialSourceModel() {
        if (typeof document === 'undefined' || !document.body || typeof document.getElementById !== 'function') {
            return false;
        }
        if (document.body.classList && document.body.classList.contains('yui-guide-live2d-preparing')) {
            return false;
        }
        this.hideTutorialLive2dPreparingControls();
        const fadeOutMs = 900;
        const targetIds = [
            'live2d-container',
            'live2d-canvas',
            'vrm-container',
            'vrm-canvas',
            'mmd-container',
            'mmd-canvas'
        ];
        const targets = targetIds
            .map((id) => document.getElementById(id))
            .filter((element) => {
                if (!element || !element.style) {
                    return false;
                }
                const computedStyle = typeof window !== 'undefined' && typeof window.getComputedStyle === 'function'
                    ? window.getComputedStyle(element)
                    : null;
                if (!computedStyle) {
                    return true;
                }
                return computedStyle.display !== 'none'
                    && computedStyle.visibility !== 'hidden'
                    && Number(computedStyle.opacity || 1) > 0;
            });
        if (targets.length === 0) {
            return false;
        }
        targets.forEach((element) => {
            element.style.setProperty('transition', 'opacity 900ms ease-in-out', 'important');
            element.style.setProperty('opacity', '1', 'important');
        });
        if (targets[0] && targets[0].offsetHeight !== undefined) {
            void targets[0].offsetHeight;
        }
        targets.forEach((element) => {
            element.style.setProperty('opacity', '0', 'important');
        });
        await this.sleep(fadeOutMs);
        return true;
    }

    clearTutorialLive2dPreparingStyles() {
        if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
            return;
        }
        if (document.body && document.body.classList) {
            document.body.classList.remove('yui-guide-return-petal-fade');
        }
        if (document.body && document.body.style && typeof document.body.style.removeProperty === 'function') {
            document.body.style.removeProperty('--yui-guide-return-avatar-opacity');
        }
        [
            'live2d-container',
            'live2d-canvas',
            'live2d-floating-buttons',
            'live2d-lock-icon',
            'live2d-return-button-container',
            'vrm-container',
            'vrm-canvas',
            'mmd-container',
            'mmd-canvas'
        ].forEach((id) => {
            const element = document.getElementById(id);
            if (!element || !element.style || typeof element.style.removeProperty !== 'function') {
                return;
            }
            if (
                id === 'live2d-floating-buttons'
                || id === 'live2d-lock-icon'
            ) {
                element.style.removeProperty('display');
            }
            element.style.removeProperty('opacity');
            element.style.removeProperty('transition');
            element.style.removeProperty('visibility');
            element.style.removeProperty('pointer-events');
        });
    }

    restoreTutorialLive2dDisplayState(reason = '', options = {}) {
        if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
            return;
        }
        const preservePreparingOpacity = options && options.preservePreparingOpacity === true;
        const preserveOpacity = options && options.preserveOpacity === true;
        if (document.body && document.body.classList) {
            if (!preservePreparingOpacity) {
                document.body.classList.remove('yui-guide-live2d-preparing');
            }
            document.body.classList.remove('yui-guide-return-petal-fade');
        }
        if (document.body && document.body.style && typeof document.body.style.removeProperty === 'function') {
            document.body.style.removeProperty('--yui-guide-return-avatar-opacity');
        }

        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.classList.remove('hidden');
            live2dContainer.classList.remove('minimized');
            live2dContainer.removeAttribute('data-neko-model-goodbye-exiting');
            live2dContainer.style.display = 'block';
            live2dContainer.style.visibility = 'visible';
            live2dContainer.style.removeProperty('transition');
            if (preserveOpacity) {
                // 当前由 Live2D 探身演出逐帧接管透明度，保活逻辑只恢复可见性。
            } else if (preservePreparingOpacity) {
                live2dContainer.style.removeProperty('opacity');
            } else {
                live2dContainer.style.setProperty('opacity', '1', 'important');
            }
            live2dContainer.style.setProperty('pointer-events', 'none', 'important');
        }

        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.classList.remove('minimized');
            live2dCanvas.style.display = 'block';
            live2dCanvas.style.visibility = 'visible';
            live2dCanvas.style.removeProperty('transition');
            if (preserveOpacity) {
                // 当前由 Live2D 探身演出逐帧接管透明度，保活逻辑只恢复可见性。
            } else if (preservePreparingOpacity) {
                live2dCanvas.style.removeProperty('opacity');
                live2dCanvas.style.removeProperty('pointer-events');
            } else {
                live2dCanvas.style.setProperty('opacity', '1', 'important');
                live2dCanvas.style.setProperty('pointer-events', 'auto', 'important');
            }
        }
    }

    revealTutorialLive2dPrepared() {
        this._tutorialLive2dRenderActivationToken += 1;
        this.setTutorialLive2dPreparing(false);
        this.clearTutorialLive2dPreparingStyles();
    }

    ensureTutorialLive2dRenderActive(reason = '', options = {}) {
        const scheduleDelayed = options.scheduleDelayed !== false;
        const deferRevealPrepared = options.deferRevealPrepared === true;
        const activationToken = scheduleDelayed
            ? ++this._tutorialLive2dRenderActivationToken
            : this._tutorialLive2dRenderActivationToken;
        const manager = window.live2dManager || null;
        const app = manager && manager.pixi_app;
        const ticker = app && app.ticker;
        const model = manager && (typeof manager.getCurrentModel === 'function'
            ? manager.getCurrentModel()
            : manager.currentModel);
        const preserveAvatarCornerPeekOpacity = window.nekoYuiGuideAvatarCornerPeekActive === true;

        try {
            this.restoreTutorialLive2dDisplayState(reason, {
                preservePreparingOpacity: deferRevealPrepared,
                preserveOpacity: preserveAvatarCornerPeekOpacity
            });
            if (model && !model.destroyed) {
                model.visible = true;
                if (!preserveAvatarCornerPeekOpacity) {
                    model.alpha = 1;
                }
                if (model.renderable !== undefined) {
                    model.renderable = true;
                }
            }
            if (app && app.stage && !app.stage.destroyed) {
                app.stage.visible = true;
                if (!preserveAvatarCornerPeekOpacity) {
                    app.stage.alpha = 1;
                }
                if (app.stage.renderable !== undefined) {
                    app.stage.renderable = true;
                }
            }
            if (ticker) {
                if (!ticker.started && typeof ticker.start === 'function') {
                    ticker.start();
                }
                if (typeof ticker.update === 'function') {
                    ticker.update();
                }
            }
            if (app && app.renderer && typeof app.renderer.render === 'function' && app.stage) {
                app.renderer.render(app.stage);
            }
        } catch (error) {
            console.warn('[Tutorial] YUI Live2D 渲染激活失败:', reason || 'unknown', error);
        }

        if (!scheduleDelayed || !this.managerResources || this._isDestroyed) {
            return;
        }

        [80, 300].forEach((delayMs) => {
            this.managerResources.setTimeout(() => {
                if (
                    this._isDestroyed
                    || activationToken !== this._tutorialLive2dRenderActivationToken
                    || window.universalTutorialManager !== this
                ) {
                    return;
                }
                this.ensureTutorialLive2dRenderActive(
                    `${reason || 'tutorial-live2d'}:delay-${delayMs}`,
                    {
                        scheduleDelayed: false,
                        deferRevealPrepared: deferRevealPrepared
                    }
                );
            }, delayMs);
        });
    }

    getTutorialLive2dScreenBounds(manager, model) {
        if (manager && typeof manager.getModelScreenBounds === 'function') {
            const bounds = manager.getModelScreenBounds();
            if (bounds) {
                return bounds;
            }
        }

        if (!model || typeof model.getBounds !== 'function') {
            return null;
        }

        let rawBounds = null;
        try {
            rawBounds = model.getBounds();
        } catch (error) {
            console.warn('[Tutorial] 获取 YUI 模型边界失败:', error);
            return null;
        }

        if (!rawBounds) {
            return null;
        }

        const left = Number(rawBounds.left);
        const right = Number(rawBounds.right);
        const top = Number(rawBounds.top);
        const bottom = Number(rawBounds.bottom);
        const width = right - left;
        const height = bottom - top;
        if (
            !Number.isFinite(left) || !Number.isFinite(right) ||
            !Number.isFinite(top) || !Number.isFinite(bottom) ||
            !Number.isFinite(width) || !Number.isFinite(height) ||
            width <= 0 || height <= 0
        ) {
            return null;
        }

        return {
            left,
            right,
            top,
            bottom,
            width,
            height,
            centerX: left + width / 2,
            centerY: top + height / 2
        };
    }

    async waitForTutorialLive2dLayoutFrame(manager) {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        if (manager && manager.pixi_app && manager.pixi_app.renderer && typeof manager.pixi_app.renderer.render === 'function') {
            try {
                manager.pixi_app.renderer.render(manager.pixi_app.stage);
            } catch (_) {}
        }
    }

    async applyTutorialLive2dViewportPlacement() {
        const manager = window.live2dManager || null;
        if (!this.hasTutorialYuiLive2dRenderableModel(manager)) {
            return false;
        }
        const model = manager && (typeof manager.getCurrentModel === 'function'
            ? manager.getCurrentModel()
            : manager.currentModel);
        const app = manager && manager.pixi_app;
        if (!manager || !model || !app || !app.renderer) {
            return false;
        }

        const screen = app.renderer.screen || {};
        const viewportWidth = Math.max(1, window.innerWidth || Number(screen.width) || 1);
        const viewportHeight = Math.max(1, window.innerHeight || Number(screen.height) || 1);
        const marginX = Math.max(20, Math.min(48, viewportWidth * 0.035));
        const marginTop = Math.max(18, Math.min(42, viewportHeight * 0.04));
        const marginBottom = Math.max(28, Math.min(72, viewportHeight * 0.07));
        const targetCenterXRatio = viewportWidth < 900 ? 0.56 : 0.63;
        const targetCenterX = viewportWidth * targetCenterXRatio;
        const targetCenterY = viewportHeight * (viewportHeight < 720 ? 0.5 : 0.52);
        const horizontalFitWidth = Math.max(
            1,
            2 * Math.min(
                targetCenterX - marginX,
                viewportWidth - marginX - targetCenterX
            )
        );
        const maxVisibleWidth = Math.min(viewportWidth - marginX * 2, horizontalFitWidth);
        const maxVisibleHeight = viewportHeight - marginTop - marginBottom;

        await this.waitForTutorialLive2dLayoutFrame(manager);
        let bounds = this.getTutorialLive2dScreenBounds(manager, model);
        if (!bounds) {
            return false;
        }

        const currentScaleX = Math.abs(Number(model.scale && model.scale.x) || 1);
        const currentScaleY = Math.abs(Number(model.scale && model.scale.y) || currentScaleX || 1);
        const currentScale = Math.max(0.0001, Math.max(currentScaleX, currentScaleY));
        const naturalWidth = bounds.width / currentScale;
        const naturalHeight = bounds.height / currentScale;
        if (
            Number.isFinite(naturalWidth) && Number.isFinite(naturalHeight) &&
            naturalWidth > 0 && naturalHeight > 0
        ) {
            const targetScale = Math.max(
                0.005,
                Math.min(
                    maxVisibleWidth / naturalWidth,
                    maxVisibleHeight / naturalHeight,
                    0.5
                )
            );
            model.scale.set(targetScale, targetScale);
            await this.waitForTutorialLive2dLayoutFrame(manager);
            bounds = this.getTutorialLive2dScreenBounds(manager, model) || bounds;
        }

        const resolveSafeCenter = (rect) => {
            const rectWidth = rect && Number.isFinite(rect.width) ? rect.width : 0;
            const rectHeight = rect && Number.isFinite(rect.height) ? rect.height : 0;
            const minCenterX = marginX + rectWidth / 2;
            const maxCenterX = viewportWidth - marginX - rectWidth / 2;
            const minCenterY = marginTop + rectHeight / 2;
            const maxCenterY = viewportHeight - marginBottom - rectHeight / 2;
            const safeCenterX = minCenterX <= maxCenterX
                ? Math.max(minCenterX, Math.min(targetCenterX, maxCenterX))
                : viewportWidth / 2;
            const safeCenterY = minCenterY <= maxCenterY
                ? Math.max(minCenterY, Math.min(targetCenterY, maxCenterY))
                : viewportHeight / 2;
            return {
                x: safeCenterX,
                y: safeCenterY
            };
        };

        let safeCenter = resolveSafeCenter(bounds);
        model.x += safeCenter.x - bounds.centerX;
        model.y += safeCenter.y - bounds.centerY;
        await this.waitForTutorialLive2dLayoutFrame(manager);
        bounds = this.getTutorialLive2dScreenBounds(manager, model) || bounds;

        const overflowX = Math.max(0, marginX - bounds.left, bounds.right - (viewportWidth - marginX));
        const overflowY = Math.max(0, marginTop - bounds.top, bounds.bottom - (viewportHeight - marginBottom));
        if ((overflowX > 0 || overflowY > 0) && bounds.width > 0 && bounds.height > 0) {
            const fitRatio = Math.max(
                0.005,
                Math.min(
                    1,
                    (maxVisibleWidth / bounds.width) * 0.98,
                    (maxVisibleHeight / bounds.height) * 0.98
                )
            );
            if (fitRatio < 0.999) {
                const nextScaleX = Math.max(0.005, Math.abs(model.scale.x) * fitRatio);
                const nextScaleY = Math.max(0.005, Math.abs(model.scale.y) * fitRatio);
                model.scale.set(nextScaleX, nextScaleY);
                await this.waitForTutorialLive2dLayoutFrame(manager);
                bounds = this.getTutorialLive2dScreenBounds(manager, model) || bounds;
                safeCenter = resolveSafeCenter(bounds);
                model.x += safeCenter.x - bounds.centerX;
                model.y += safeCenter.y - bounds.centerY;
            }
        }

        await this.waitForTutorialLive2dLayoutFrame(manager);
        bounds = this.getTutorialLive2dScreenBounds(manager, model) || bounds;
        safeCenter = resolveSafeCenter(bounds);
        model.x += safeCenter.x - bounds.centerX;
        model.y += safeCenter.y - bounds.centerY;

        this.ensureTutorialLive2dViewportPlacementWatcher();
        return true;
    }

    ensureTutorialLive2dViewportPlacementWatcher() {
        if (this._tutorialViewportPlacementResizeHandler) {
            return;
        }

        this._tutorialViewportPlacementResources = createUniversalTutorialScopedResources();
        this._tutorialViewportPlacementResizeHandler = () => {
            if (this._tutorialViewportPlacementResizeTimer) {
                this._tutorialViewportPlacementResources.clearTimeout(this._tutorialViewportPlacementResizeTimer);
            }
            this._tutorialViewportPlacementResizeTimer = this._tutorialViewportPlacementResources.setTimeout(() => {
                this._tutorialViewportPlacementResizeTimer = null;
                const controller = this.ensureTutorialAvatarReloadController();
                if (!controller || !controller.hasActiveOverride() || this._isDestroyed) {
                    return;
                }
                this.applyTutorialLive2dViewportPlacement().catch(error => {
                    console.warn('[Tutorial] resize 后重排 YUI 模型失败:', error);
                });
            }, 120);
        };
        this._tutorialViewportPlacementResources.addEventListener(window, 'resize', this._tutorialViewportPlacementResizeHandler);
        this._tutorialViewportPlacementResources.addEventListener(window, 'electron-display-changed', this._tutorialViewportPlacementResizeHandler);
    }

    clearTutorialLive2dViewportPlacementWatcher() {
        if (this._tutorialViewportPlacementResources) {
            this._tutorialViewportPlacementResources.destroy();
            this._tutorialViewportPlacementResources = null;
        }
        this._tutorialViewportPlacementResizeTimer = null;
        this._tutorialViewportPlacementResizeHandler = null;
    }

    beginTutorialAvatarOverride(options = {}) {
        const controller = this.ensureTutorialAvatarReloadController();
        if (!controller || typeof controller.beginOverride !== 'function') {
            return Promise.reject(new Error('tutorial avatar reload controller unavailable'));
        }
        return controller.beginOverride(options);
    }

    restoreTutorialAvatarOverride() {
        const controller = this.ensureTutorialAvatarReloadController();
        if (!controller || typeof controller.restoreOverride !== 'function') {
            return Promise.resolve();
        }
        return controller.restoreOverride();
    }

    isCurrentRuntimeModelLive2d() {
        const modelType = String(
            window.lanlan_config && window.lanlan_config.model_type
                ? window.lanlan_config.model_type
                : 'live2d'
        ).toLowerCase();
        return modelType === 'live2d';
    }

    clearTutorialLive2dManagerMetadata(manager, staleModel) {
        if (!manager) {
            return;
        }
        if (window.LanLan1) {
            if (window.LanLan1.live2dModel === staleModel) {
                window.LanLan1.live2dModel = null;
            }
            if (window.LanLan1.currentModel === staleModel) {
                window.LanLan1.currentModel = null;
            }
        }
        if (!manager.currentModel || manager.currentModel === staleModel) {
            manager.currentModel = null;
        }
        manager._lastLoadedModelPath = null;
        manager.modelRootPath = null;
        manager.modelName = null;
        manager._isModelReadyForInteraction = false;
        if (!manager._isLoadingModel) {
            manager._modelLoadState = 'idle';
        }
        if (typeof manager._resetDerivedModelMetadata === 'function') {
            try {
                manager._resetDerivedModelMetadata();
            } catch (_) {}
        }
    }

    hideTutorialLive2dRuntimeSurfaceAfterResidueClear() {
        if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
            return;
        }
        const live2dContainer = document.getElementById('live2d-container');
        if (live2dContainer) {
            live2dContainer.style.display = 'none';
            live2dContainer.classList.add('hidden');
            live2dContainer.style.removeProperty('opacity');
            live2dContainer.style.removeProperty('transition');
            live2dContainer.style.pointerEvents = 'none';
        }
        const live2dCanvas = document.getElementById('live2d-canvas');
        if (live2dCanvas) {
            live2dCanvas.style.visibility = 'hidden';
            live2dCanvas.style.pointerEvents = 'none';
            live2dCanvas.style.removeProperty('opacity');
            live2dCanvas.style.removeProperty('transition');
        }
        document.querySelectorAll('#live2d-floating-buttons, #live2d-lock-icon, #live2d-return-button-container')
            .forEach((element) => {
                if (
                    element
                    && element.id === 'live2d-floating-buttons'
                    && typeof window._removeNekoFloatingButtonsElement === 'function'
                ) {
                    window._removeNekoFloatingButtonsElement(element);
                    return;
                }
                if (element && typeof element.remove === 'function') {
                    element.remove();
                }
            });
    }

    async clearTutorialYuiLive2dRuntimeResidue(reason = '') {
        const manager = window.live2dManager || null;
        if (!manager || !this.isTutorialYuiLive2dActive() || this.isCurrentRuntimeModelLive2d()) {
            return false;
        }

        const staleModel = this.getTutorialLive2dCurrentModel(manager);
        try {
            if (staleModel && typeof manager.removeModel === 'function') {
                await manager.removeModel({ skipCloseWindows: true });
            } else {
                const stage = manager.pixi_app && manager.pixi_app.stage;
                if (stage && staleModel && typeof stage.removeChild === 'function') {
                    try {
                        stage.removeChild(staleModel);
                    } catch (_) {}
                }
                if (staleModel && typeof staleModel.destroy === 'function') {
                    try {
                        staleModel.destroy({ children: true });
                    } catch (_) {}
                }
            }
        } catch (error) {
            console.warn('[Tutorial] 清理教程 YUI Live2D 残留失败:', reason || 'unknown', error);
        } finally {
            this.clearTutorialLive2dManagerMetadata(manager, staleModel);
            if (typeof manager.pauseRendering === 'function') {
                try {
                    manager.pauseRendering();
                } catch (_) {}
            }
            if (manager.pixi_app && manager.pixi_app.renderer && typeof manager.pixi_app.renderer.clear === 'function') {
                try {
                    manager.pixi_app.renderer.clear();
                } catch (_) {}
            }
            this.hideTutorialLive2dRuntimeSurfaceAfterResidueClear();
        }
        return true;
    }

    snapshotAvatarFloatingModelInteractionState(reason = 'tutorial-started') {
        if (this._avatarFloatingModelLockSnapshot) {
            return;
        }
        let autoGoodbyeState = null;
        try {
            autoGoodbyeState = window.nekoAutoGoodbye && typeof window.nekoAutoGoodbye.getState === 'function'
                ? window.nekoAutoGoodbye.getState()
                : null;
        } catch (_) {}
        const readLocked = (manager, fallback) => {
            if (manager && typeof manager.isLocked !== 'undefined') {
                return !!manager.isLocked;
            }
            if (fallback && typeof fallback.isLocked !== 'undefined') {
                return !!fallback.isLocked;
            }
            return false;
        };
        const readPointerEvents = (elementId) => {
            const element = document.getElementById(elementId);
            return element ? element.style.pointerEvents : '';
        };
        const startupDefaultCatPending = !!(
            autoGoodbyeState && autoGoodbyeState.startupDefaultCatRequested === true
        );
        this._avatarFloatingModelLockSnapshot = {
            live2d: readLocked(window.live2dManager),
            vrm: readLocked(window.vrmManager, window.vrmManager && window.vrmManager.interaction),
            mmd: readLocked(window.mmdManager, window.mmdManager && window.mmdManager.interaction),
            pngtuber: readLocked(window.pngtuberManager),
            pointerEvents: {
                live2dCanvas: readPointerEvents('live2d-canvas'),
                live2dContainer: readPointerEvents('live2d-container'),
                vrmCanvas: readPointerEvents('vrm-canvas'),
                vrmContainer: readPointerEvents('vrm-container'),
                mmdCanvas: readPointerEvents('mmd-canvas'),
                mmdContainer: readPointerEvents('mmd-container'),
                pngtuberCanvas: readPointerEvents('pngtuber-canvas'),
                pngtuberContainer: readPointerEvents('pngtuber-container')
            },
            // 猫咪/告别态是业务形态，不能只靠 pointer-events 快照推断；模型重载会清掉 manager 标志。
            goodbyeActive: !!(
                (window.live2dManager && window.live2dManager._goodbyeClicked)
                || (window.vrmManager && window.vrmManager._goodbyeClicked)
                || (window.mmdManager && window.mmdManager._goodbyeClicked)
                // 冷启动直进教程时 manager 标志可能尚未建立，auto-goodbye 的视觉层级仍是可靠业务状态。
                || (autoGoodbyeState && autoGoodbyeState.visualTier && autoGoodbyeState.visualTier !== 'none')
                // PC 冷启动直进教程会跳过用户模型；此时“默认猫咪”只有待执行请求，还没有可见猫咪态。
                || startupDefaultCatPending
            ),
            goodbyeMeta: autoGoodbyeState && typeof autoGoodbyeState === 'object'
                ? {
                    autoGoodbye: autoGoodbyeState.autoGoodbyeTriggered === true,
                    // 待执行的启动默认形态是明确的产品状态，优先于尚未进入猫咪态时的 started 等临时原因。
                    reason: startupDefaultCatPending ? 'startup-default-cat' : (autoGoodbyeState.lastReason || ''),
                    visualTier: autoGoodbyeState.visualTier || ''
                }
                : null,
            goodbyeRect: window._savedGoodbyeRect
                ? {
                    left: Number(window._savedGoodbyeRect.left),
                    top: Number(window._savedGoodbyeRect.top),
                    width: Number(window._savedGoodbyeRect.width),
                    height: Number(window._savedGoodbyeRect.height)
                }
                : null,
            reason
        };
    }

    getActiveAvatarFloatingModelPrefix() {
        const modelType = String(window.lanlan_config && window.lanlan_config.model_type || '').toLowerCase();
        if (modelType === 'live3d') {
            const subType = String(window.lanlan_config && window.lanlan_config.live3d_sub_type || '').toLowerCase();
            if (['vrm', 'mmd', 'pngtuber'].includes(subType)) {
                return subType;
            }
        }
        if (['vrm', 'mmd', 'pngtuber'].includes(modelType)) {
            return modelType;
        }
        return this._tutorialModelPrefix || UniversalTutorialManager.detectModelPrefix() || 'live2d';
    }

    restoreAvatarFloatingModelInteractionState(reason = 'tutorial-ended') {
        const snapshot = this._avatarFloatingModelLockSnapshot;
        if (!snapshot) {
            return;
        }
        if (reason === 'teardown-early' && snapshot.goodbyeActive === true) {
            // 猫咪态的锁定属性属于隐藏模型；必须等用户模型重载完成并重新进入猫咪态，
            // 不能提前把 pointer-events:none 套到仍在退场的教程模型上。
            return;
        }
        try {
            if (window.live2dManager && typeof window.live2dManager.setLocked === 'function') {
                window.live2dManager.setLocked(!!snapshot.live2d, { updateFloatingButtons: false });
            }
        } catch (error) {
            console.warn('[Tutorial] 恢复 Live2D 模型交互锁失败:', error);
        }

        try {
            if (window.vrmManager && window.vrmManager.core && typeof window.vrmManager.core.setLocked === 'function') {
                window.vrmManager.core.setLocked(!!snapshot.vrm);
            } else if (window.vrmManager && window.vrmManager.interaction && typeof window.vrmManager.interaction.setLocked === 'function') {
                window.vrmManager.interaction.setLocked(!!snapshot.vrm);
            }
        } catch (error) {
            console.warn('[Tutorial] 恢复 VRM 模型交互锁失败:', error);
        }

        try {
            if (window.mmdManager && window.mmdManager.core && typeof window.mmdManager.core.setLocked === 'function') {
                window.mmdManager.core.setLocked(!!snapshot.mmd);
            } else if (window.mmdManager && window.mmdManager.interaction && typeof window.mmdManager.interaction.setLocked === 'function') {
                window.mmdManager.interaction.setLocked(!!snapshot.mmd);
            }
        } catch (error) {
            console.warn('[Tutorial] 恢复 MMD 模型交互锁失败:', error);
        }

        try {
            if (window.pngtuberManager && typeof window.pngtuberManager.setLocked === 'function') {
                window.pngtuberManager.setLocked(!!snapshot.pngtuber, { updateFloatingButtons: false });
            }
        } catch (error) {
            console.warn('[Tutorial] 恢复 PNGTuber 模型交互锁失败:', error);
        }

        const activePrefix = this.getActiveAvatarFloatingModelPrefix();
        const activeLocked = snapshot[activePrefix] === true;
        function restoreAvatarPointerEvents(element, elementId, snapshotPointerEvents, hasSnapshotPointerEvents) {
            const isActiveAvatarContainer = elementId === `${activePrefix}-container`;
            if (isActiveAvatarContainer && (activePrefix === 'live2d' || activePrefix === 'pngtuber')) {
                element.style.setProperty('pointer-events', 'none', 'important');
                return;
            }
            if (hasSnapshotPointerEvents && snapshotPointerEvents) {
                element.style.pointerEvents = snapshotPointerEvents;
                return;
            }
            if (activePrefix === 'live2d' || activePrefix === 'pngtuber') {
                element.style.removeProperty('pointer-events');
                element.style.pointerEvents = activeLocked ? 'none' : 'auto';
            }
        }
        [`${activePrefix}-canvas`, `${activePrefix}-container`].forEach(elementId => {
            const element = document.getElementById(elementId);
            if (!element) return;
            const pointerKey = elementId.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
            const hasSnapshotPointerEvents = snapshot.pointerEvents
                && Object.prototype.hasOwnProperty.call(snapshot.pointerEvents, pointerKey);
            const snapshotPointerEvents = hasSnapshotPointerEvents ? snapshot.pointerEvents[pointerKey] : null;
            restoreAvatarPointerEvents(element, elementId, snapshotPointerEvents, hasSnapshotPointerEvents);
        });
        if (reason === 'tutorial-avatar-restored') {
            this._avatarFloatingModelLockSnapshot = null;
        }
    }

    restoreAvatarFloatingModelStateAfterTutorial() {
        const snapshot = this._avatarFloatingModelLockSnapshot;
        if (!snapshot || snapshot.goodbyeActive !== true) {
            this.restoreAvatarFloatingModelInteractionState('tutorial-avatar-restored');
            return false;
        }

        const goodbyeMeta = snapshot.goodbyeMeta || {};
        const restoreDetail = {
            source: 'tutorial-state-restore',
            reason: goodbyeMeta.reason || 'tutorial-state-restore',
            // 重新进入猫咪态时保留教程前的返回按钮锚点，不使用教程临时模型站位。
            restoreSavedGoodbyeRect: snapshot.goodbyeRect || null
        };
        if (goodbyeMeta.autoGoodbye === true) {
            restoreDetail.autoGoodbye = true;
        } else if (goodbyeMeta.reason === 'startup-default-cat') {
            restoreDetail.startupDefaultForm = 'cat';
        }

        try {
            // 模型重载只恢复角色资源，不会恢复猫咪/告别表现；重新走既有 goodbye 入口，
            // 让可见形态、锁定状态、返回按钮和 PC 命中区由同一业务链路一致重建。
            window.dispatchEvent(new CustomEvent('live2d-goodbye-click', { detail: restoreDetail }));
            if (
                ['cat1', 'cat2', 'cat3'].includes(goodbyeMeta.visualTier)
                && window.nekoAutoGoodbye
                && typeof window.nekoAutoGoodbye.setVisualTier === 'function'
            ) {
                // startup-default-cat 的 pending 快照可能仍是 none；goodbye 事件已经建立 cat1，
                // 这里只回放真实保存过的猫咪层级，不能再用 none 把刚恢复的猫咪视觉清空。
                window.nekoAutoGoodbye.setVisualTier(goodbyeMeta.visualTier, {
                    source: 'tutorial-state-restore',
                    reason: restoreDetail.reason
                });
            }
            return true;
        } finally {
            // 业务形态已接管交互恢复，不能再把旧 DOM 的 pointer-events 快照重复应用到新模型。
            this._avatarFloatingModelLockSnapshot = null;
        }
    }

    consumePendingStartupDefaultCatRestoreRequest() {
        const snapshot = this._avatarFloatingModelLockSnapshot;
        if (!snapshot) {
            return false;
        }
        let startupDefaultCatPending = false;
        try {
            const autoGoodbye = window.nekoAutoGoodbye;
            const currentState = autoGoodbye && typeof autoGoodbye.getState === 'function'
                ? autoGoodbye.getState()
                : null;
            startupDefaultCatPending = !!(
                currentState && currentState.startupDefaultCatRequested === true
            );
            if (startupDefaultCatPending && snapshot.goodbyeActive !== true) {
                // 默认猫咪事件可能晚于首次快照；在解除教程锁前读取实时 pending 状态，
                // 仅当原快照不是猫咪态时升级业务形态，保留既有猫咪层级及教程前交互/位置。
                snapshot.goodbyeActive = true;
                snapshot.goodbyeMeta = {
                    autoGoodbye: currentState.autoGoodbyeTriggered === true,
                    reason: 'startup-default-cat',
                    visualTier: currentState.visualTier || ''
                };
            }
        } catch (error) {
            console.warn('[Tutorial] 刷新待恢复的启动默认猫咪状态失败:', error);
        }
        if (
            !startupDefaultCatPending
            && (
                snapshot.goodbyeActive !== true
                || !snapshot.goodbyeMeta
                || snapshot.goodbyeMeta.reason !== 'startup-default-cat'
            )
        ) {
            return false;
        }
        try {
            const autoGoodbye = window.nekoAutoGoodbye;
            if (autoGoodbye && typeof autoGoodbye.consumeStartupDefaultCatRequest === 'function') {
                return autoGoodbye.consumeStartupDefaultCatRequest() === true;
            }
        } catch (error) {
            console.warn('[Tutorial] 消费待恢复的启动默认猫咪请求失败:', error);
        }
        return false;
    }

    applyTutorialChatIdentityOverride(detail) {
        const payload = detail || {};
        if (window.appInterpage && typeof window.appInterpage.applyTutorialChatIdentityOverride === 'function') {
            window.appInterpage.applyTutorialChatIdentityOverride(payload);
        } else if (payload.active) {
            const overrideDetail = {
                active: true,
                displayName: payload.displayName || 'YUI',
                avatarDataUrl: payload.avatarDataUrl || '',
                modelType: payload.modelType || ''
            };
            window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__ = {
                active: true,
                displayName: overrideDetail.displayName,
                avatarDataUrl: overrideDetail.avatarDataUrl,
                modelType: overrideDetail.modelType
            };
            window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__ = overrideDetail.displayName;
            if (window.appChatAvatar && typeof window.appChatAvatar.setTutorialAvatarOverride === 'function') {
                window.appChatAvatar.setTutorialAvatarOverride(overrideDetail.avatarDataUrl, overrideDetail.modelType);
            } else {
                window.__nekoPendingTutorialChatIdentity = overrideDetail;
            }
            window.dispatchEvent(new CustomEvent('neko:tutorial-chat-identity-changed', {
                detail: overrideDetail
            }));
        } else {
            delete window.__NEKO_TUTORIAL_CHAT_IDENTITY_OVERRIDE__;
            delete window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__;
            if (window.appChatAvatar && typeof window.appChatAvatar.clearTutorialAvatarOverride === 'function') {
                window.appChatAvatar.clearTutorialAvatarOverride();
            } else {
                window.__nekoPendingTutorialChatIdentity = { active: false };
            }
            window.dispatchEvent(new CustomEvent('neko:tutorial-chat-identity-changed', {
                detail: { active: false }
            }));
        }

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage({
                    action: 'tutorial_chat_identity_override',
                    active: !!payload.active,
                    displayName: payload.displayName || '',
                    avatarDataUrl: payload.avatarDataUrl || '',
                    modelType: payload.modelType || '',
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('[Tutorial] 广播新手教程聊天身份覆盖失败:', error);
            }
        }
    }

    /**
     * 检测当前页面类型
     */
    static detectPage() {
        const path = window.location.pathname;
        const hash = window.location.hash;

        // 主页
        if (path === '/' || path === '/index.html') {
            return 'home';
        }

        if (path.includes('api_key') || path.includes('settings')) {
            return 'settings';
        }

        if (path.includes('memory_browser')) {
            return 'memory_browser';
        }

        if (
            path.includes('/api/agent/user_plugin/dashboard')
            || path === '/ui'
            || path.startsWith('/ui/')
        ) {
            return 'plugin_dashboard';
        }

        return 'unknown';
    }

    getStorageKey() {
        return getTutorialStorageKeyForPage(this.currentPage);
    }

    /**
     * 获取指定页面相关的所有存储键（用于重置/判断）
     */
    getStorageKeysForPage(page) {
        const targetPage = page || this.currentPage;
        return [getTutorialStorageKeyForPage(targetPage)];
    }

    getResetStorageKeysForPage(page) {
        return Array.from(new Set([
            ...this.getStorageKeysForPage(page),
            ...getTutorialStorageKeysForPageFallback(page),
        ]));
    }

    getManualStartIntentKey(page = null) {
        const targetPage = page || this.currentPage;
        return getTutorialManualIntentKeyForPage(targetPage);
    }

    markTutorialManualStartIntent(page = null) {
        const targetPage = page || this.currentPage;
        if (!targetPage || targetPage === 'unknown') {
            return;
        }
        if (targetPage === 'home') {
            return;
        }
        localStorage.setItem(this.getManualStartIntentKey(targetPage), 'true');
    }

    peekTutorialStartSource(page = null) {
        const targetPage = page || this.currentPage;
        if (this.pendingTutorialStartSource) {
            return this.pendingTutorialStartSource;
        }
        if (targetPage === 'home' && loadAvatarFloatingGuideState().manualResetRound) {
            return 'manual_reset';
        }

        const intentKey = this.getManualStartIntentKey(targetPage);
        if (localStorage.getItem(intentKey) === 'true') {
            return 'manual';
        }

        return null;
    }

    consumeTutorialStartSource(page = null) {
        const targetPage = page || this.currentPage;

        if (this.pendingTutorialStartSource) {
            const source = this.pendingTutorialStartSource;
            this.pendingTutorialStartSource = null;
            return source;
        }
        if (targetPage === 'home' && loadAvatarFloatingGuideState().manualResetRound) {
            return 'manual_reset';
        }

        const intentKey = this.getManualStartIntentKey(targetPage);
        if (localStorage.getItem(intentKey) === 'true') {
            localStorage.removeItem(intentKey);
            return 'manual';
        }

        return 'auto';
    }

    waitUntilInitialized() {
        this.isInitialized = true;
        return Promise.resolve(true);
    }

    async requestTutorialStart(source = 'manual', delayMs = 0) {
        const requestedSource = source || 'manual';
        this.pendingTutorialStartSource = requestedSource;
        this.logTutorialFlow('request-tutorial-start', {
            page: this.currentPage,
            source: requestedSource,
            delayMs: delayMs || 0,
        });

        try {
            const ready = await this.waitUntilInitialized();
            if (!ready) {
                this.pendingTutorialStartSource = null;
                throw new Error('tutorial_not_initialized');
            }

            if (this.isTutorialRunning) {
                this.pendingTutorialStartSource = null;
                return true;
            }

            if (this.currentPage === 'home') {
                const round = this.getHomeAvatarFloatingGuideLaunchRound();
                if (round && this.isDirectAvatarFloatingTutorialBoot(round)) {
                    await this.waitForTutorialModelHostReady();
                } else {
                    await this.waitForFloatingButtons();
                }
                this.startTutorialWhenI18nReady(delayMs);
                return true;
            }

            if (!this.isYuiGuideEnabledForPage(this.currentPage)) {
                this.pendingTutorialStartSource = null;
                console.warn('[Tutorial] 当前页面没有新版 Yui Guide，引导请求已忽略:', this.currentPage);
                return false;
            }

            this.startTutorialWhenI18nReady(delayMs);
            return true;
        } catch (error) {
            this.pendingTutorialStartSource = null;
            throw error;
        }
    }

    async startAvatarFloatingGuideRound(day, options = {}) {
        const round = normalizeAvatarFloatingGuideRound(day);
        const source = options.source || 'manual';
        if (!this.isTutorialRunning && !window.isInTutorial && this._teardownPromise) {
            await this.waitForTutorialTeardownSettled('avatar-floating-guide-start');
        }
        if (this.isTutorialRunning || window.isInTutorial) {
            console.warn('[Tutorial] 引导已在运行中，跳过悬浮窗教程启动:', round);
            return false;
        }

        const ready = await this.waitUntilInitialized();
        if (!ready) {
            throw new Error('tutorial_not_initialized');
        }
        if (this.currentPage !== 'home') {
            throw new Error('avatar_floating_guide_requires_home');
        }
        const directTutorialBoot = this.isDirectAvatarFloatingTutorialBoot(round);
        if (directTutorialBoot) {
            const hostReady = await this.waitForTutorialModelHostReady();
            if (!hostReady) {
                await this.recoverUserModelAfterDirectTutorialBootFailure('tutorial-model-host-not-ready');
                throw new Error('tutorial_model_host_not_ready');
            }
        } else {
            const buttonsReady = await this.waitForFloatingButtons();
            if (!buttonsReady) {
                throw new Error('floating_buttons_not_ready');
            }
        }

        this._isDestroyed = false;
        this._tutorialEndHandled = false;
        this.lifecycleStateStore.resetEndReason();
        this.currentTutorialStartSource = source;
        this.pendingTutorialStartSource = null;
        this.currentStep = 0;
        this.cachedValidSteps = [{
            element: 'body',
            skipInitialCheck: true,
            yuiGuideSceneId: 'avatar_floating_day' + round,
        }];
        this.activeAvatarFloatingGuideRound = round;
        let tutorialStarted = false;
        const autoReservationId = source === 'auto'
            ? `auto-${round}-${Date.now()}-${Math.random().toString(36).slice(2)}`
            : '';

        try {
            if (source === 'auto') {
                // Reserve the daily auto start before long narration so refreshes cannot replay it.
                this.markAvatarFloatingGuideRoundAutoShown(round, autoReservationId);
            }
            if (directTutorialBoot) {
                // 自动 round 预留会改变预测结果；必须立即建立 direct-boot claim，覆盖随后
                // 的服务端同步与胶囊 prepare 等待窗口，避免用户模型盖住教程模型。
                this.claimDirectAvatarFloatingTutorialBoot(round, source);
            }
            this.setAvatarFloatingGuideCurrentRound(round);
            this.snapshotAvatarFloatingModelInteractionState('avatar-floating-guide-start');
            this.isTutorialRunning = true;
            window.isInTutorial = true;
            this.lockBodyScroll();
            if (source === 'auto') {
                const stateApi = getSevenDayTutorialStateApi();
                if (typeof stateApi.flush === 'function') {
                    await stateApi.flush();
                }
            }
            // prepare 已绑定当前教程生命周期；重启教程时聊天桥仍处于上一轮 closed 状态，
            // 必须先只打开本轮 PC/chat lifecycle，否则 prepare 会被当作迟到消息丢弃。
            // 用户可见的 tutorial-started 事件仍等胶囊准备成功后再发，避免失败启动被记录为已开始。
            this.relayYuiGuideTutorialLifecycleStarted('home', source);
            // 必须先让 PC 原生窗口完成毛球→胶囊，再开启固定布局；反过来会让固定锁
            // 与展开请求竞争，表现为教程仍显示毛球或胶囊位置闪烁。
            const compactChatReady = await this.prepareYuiGuideCompactChatForTutorial();
            if (
                this._tutorialEndHandled
                || this._isDestroyed
                || !this.isTutorialRunning
                || window.isInTutorial !== true
            ) {
                // skip/远程结束可能发生在 prepare 等待期间；迟到回执刚写入的形态快照
                // 仍需补恢复，但不能再走 catch 重复 teardown，更不能继续固定布局并启动 director。
                this.restoreYuiGuideCompactChatSurface('tutorial-start-cancelled-after-prepare');
                if (directTutorialBoot) {
                    // teardown 已接管取消流程；这里只释放提前建立的 claim，并保留已跳过模型标记，
                    // 让调用方既有的 result=false recovery 在拆除后恢复用户模型。
                    this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-start-cancelled', {
                        keepUserModelBootSkipped: true,
                        suppressPrediction: true
                    });
                }
                if (source === 'auto') {
                    this.rollbackAvatarFloatingGuideRoundAutoShown(round, autoReservationId);
                    const stateApi = getSevenDayTutorialStateApi();
                    if (typeof stateApi.flush === 'function') {
                        await stateApi.flush();
                    }
                }
                return false;
            }
            if (!compactChatReady) {
                throw new Error('tutorial_compact_chat_not_ready');
            }
            if (document.body) {
                document.body.classList.add('yui-guide-compact-chat-fixed');
            }
            this.syncYuiGuideCompactChatFixedLayout(true, 'avatar-floating-guide-start');
            this._tutorialModelPrefix = 'live2d';
            this.emitTutorialStarted('home', source, {
                lifecycleAlreadyStarted: true,
                day: round,
            });
            tutorialStarted = true;

            const director = this.ensureYuiGuideDirector();
            if (!director || typeof director.playAvatarFloatingRound !== 'function') {
                throw new Error('avatar_floating_director_unavailable');
            }
            await this.playAvatarFloatingRoundPrelude(round, source, director, {
                skipSourceModelFade: directTutorialBoot
            });
            const completed = await director.playAvatarFloatingRound(round, {
                source,
                surfaceReady: true,
                revealPrepared: () => this.revealTutorialLive2dPrepared()
            });
            if (directTutorialBoot) {
                this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-before-teardown', {
                    suppressPrediction: true
                });
            }
            if (!this._tutorialEndHandled) {
                const endReason = completed
                    ? 'complete'
                    : (
                        this.lifecycleStateStore.getEndReason()
                        || this.lifecycleStateStore.getEndRawReason()
                        || 'destroy'
                    );
                await this.requestTutorialDestroy(endReason);
            }
            return completed;
        } catch (error) {
            console.error('[Tutorial] 悬浮窗教程启动失败:', error);
            if (source === 'auto' && !tutorialStarted) {
                this.rollbackAvatarFloatingGuideRoundAutoShown(round, autoReservationId);
                const stateApi = getSevenDayTutorialStateApi();
                if (typeof stateApi.flush === 'function') {
                    await stateApi.flush();
                }
            }
            if (directTutorialBoot) {
                this.releaseDirectAvatarFloatingTutorialBoot('avatar-floating-before-teardown', {
                    keepUserModelBootSkipped: true,
                    suppressPrediction: true
                });
            }
            if (!this._tutorialEndHandled) {
                await this.requestTutorialDestroy('destroy');
            }
            if (directTutorialBoot) {
                await this.recoverUserModelAfterDirectTutorialBootFailure('avatar-floating-start-failed');
            }
            throw error;
        }
    }

    async waitForTutorialTeardownSettled(reason = '') {
        const teardownPromise = this._teardownPromise;
        if (!teardownPromise) {
            return true;
        }
        try {
            await teardownPromise;
            return true;
        } catch (error) {
            console.warn('[Tutorial] 等待旧引导拆除失败，继续启动:', reason || 'unknown', error);
            return false;
        }
    }

    async playAvatarFloatingRoundPrelude(round, source, director, options = {}) {
        const controller = this.ensureTutorialRoundPreludeController();
        if (!controller || typeof controller.play !== 'function') {
            throw new Error('tutorial_round_prelude_controller_unavailable');
        }
        return controller.play(round, {
            source: source,
            director: director || null,
            deferRevealPrepared: true,
            skipSourceModelFade: options && options.skipSourceModelFade === true
        });
    }




    /**
     * 检查是否需要自动启动引导
     */
    async checkAndStartTutorial() {
        if (this.isTutorialRunning || window.isInTutorial) {
            return;
        }

        const handoffToken = await this.consumePendingYuiGuideHandoffToken();
        if (handoffToken) {
            this.startTutorialWhenI18nReady(500);
            return;
        }

        const hasSeen = this.hasSeenTutorial(this.currentPage);
        if (this.currentPage === 'home') {
            const directBootRound = this.getDirectAvatarFloatingTutorialBootRound();
            if (directBootRound && this.isDirectAvatarFloatingTutorialBoot(directBootRound)) {
                const directBootState = loadAvatarFloatingGuideState();
                if (directBootState.manualResetRound === directBootRound) {
                    this.pendingTutorialStartSource = 'manual_reset';
                }
                if (!hasSeen) {
                    this.setHomeTutorialPending(true);
                }
                this.startTutorialWhenI18nReady(1500);
                return;
            }
        }

        if (!hasSeen && this.currentPage === 'home') {
            // 新手教程即将启动：先置 pending，让选人格门控在「模型加载/首句演出尚未上锁」这段窗口里
            // 也把教程视作占屏，避免选人格抢在新手教程之前弹出（上锁前的长 await 链可能超过选人格 15s 超时）。
            this.setHomeTutorialPending(true);
            this.waitForFloatingButtons().then((found) => {
                if (!found) {
                    console.warn('[Tutorial] 浮动按钮始终未出现，跳过主页引导');
                    this.dispatchStartupGreetingRelease('floating-buttons-not-found');
                    return;
                }
                this.startTutorialWhenI18nReady(1500);
            });
        } else if (this.currentPage === 'home') {
            // 老用户每日教程不置 pending：这里多数天根本没 round（要到 maybeStartAvatarFloatingGuideAutoRound
            // 之后才知道），且老用户 onboarding 早已完成、选人格不会再 pending，无收益却会在无 round 日白挡门控。
            // 首启并发的 bug 只发生在上面的新用户 Day1 分支。
            this.waitForFloatingButtons().then((found) => {
                if (!found) {
                    this.dispatchStartupGreetingRelease('floating-buttons-not-found');
                    return;
                }
                this.maybeStartAvatarFloatingGuideAutoRound(1200).then((started) => {
                    if (!started) {
                        this.dispatchStartupGreetingRelease('no-avatar-floating-round');
                    }
                }).catch((error) => {
                    console.warn('[Tutorial] 自动启动每日教程检查失败，放行启动问候:', error);
                    this.dispatchStartupGreetingRelease('avatar-floating-auto-round-check-failed');
                });
            });
        } else {
            this.dispatchStartupGreetingRelease('non-home-page');
        }
    }



    lockBodyScroll() {
        if (this._isBodyLocked) return;
        this._originalBodyOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        this.blockTutorialScroll();
        this._isBodyLocked = true;
    }

    unlockBodyScroll() {
        this.unblockTutorialScroll();
        if (!this._isBodyLocked) return;
        document.body.style.overflow = this._originalBodyOverflow ?? '';
        this._originalBodyOverflow = undefined;
        this._isBodyLocked = false;
    }

    blockTutorialScrollEvent(event) {
        if (!this.isTutorialRunning && !window.isInTutorial) return;
        if (event && typeof event.preventDefault === 'function') {
            event.preventDefault();
        }
    }

    blockTutorialScroll() {
        if (this._isTutorialScrollBlocked) return;
        this._tutorialScrollBlockResources = createUniversalTutorialScopedResources();
        this._tutorialScrollBlockResources.addEventListener(window, 'wheel', this._tutorialScrollBlockHandler, this._tutorialScrollBlockOptions);
        this._tutorialScrollBlockResources.addEventListener(window, 'touchmove', this._tutorialScrollBlockHandler, this._tutorialScrollBlockOptions);
        this._isTutorialScrollBlocked = true;
    }

    unblockTutorialScroll() {
        if (!this._isTutorialScrollBlocked) return;
        if (this._tutorialScrollBlockResources) {
            this._tutorialScrollBlockResources.destroy();
            this._tutorialScrollBlockResources = null;
        }
        this._isTutorialScrollBlocked = false;
    }
















    startTutorial() {
        if (!this.isInitialized) {
            console.warn('[Tutorial] Yui Guide 管理器未初始化');
            return false;
        }

        if (this.isTutorialRunning || window.isInTutorial) {
            console.warn('[Tutorial] 引导已在运行中，跳过重复启动');
            this.consumeTutorialStartSource();
            return true;
        }

        this.currentTutorialStartSource = this.consumeTutorialStartSource();

        if (this.currentPage === 'home') {
            const round = this.getHomeAvatarFloatingGuideLaunchRound();
            if (!round) {
                console.warn('[Tutorial] 首页每日教程 round 未注册，跳过启动');
                return false;
            }
            this.snapshotAvatarFloatingModelInteractionState('tutorial-start');
            this.startAvatarFloatingGuideRound(round, {
                source: this.currentTutorialStartSource
            }).catch(error => {
                console.error('[Tutorial] 首页 Day' + round + ' 悬浮窗教程启动失败:', error);
                this.resetTutorialStartState();
            });
            return true;
        }

        console.warn('[Tutorial] 当前页面没有新版每日教程 round，跳过旧页面教程:', this.currentPage);
        return false;
    }

    resetTutorialStartState() {
        this.revealTutorialLive2dPrepared();
        this._teardownTutorialUI();
    }

    emitTutorialStarted(page = this.currentPage, source = this.currentTutorialStartSource, options = {}) {
        this.clearStartupGreetingRelease('tutorial-started');
        // 胶囊准备链可能已提前打开当前 lifecycle；避免向 chat/pet 重复发送开始消息。
        if (options.lifecycleAlreadyStarted !== true) {
            this.relayYuiGuideTutorialLifecycleStarted(page, source);
        }
        this.syncPcSystemCursorHidden(true, 'tutorial-started');
        window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
            detail: {
                page: page,
                source: source,
                day: normalizeOptionalAvatarFloatingGuideRound(options.day) || undefined,
            }
        }));
        this.logTutorialFlow('tutorial-started', {
            page: page,
            source: source,
        });
    }



    /**
     * 在右上角显示「跳过」按钮，点击后结束引导
     */
    showSkipButton() {
        const controller = this.ensureTutorialSkipController();
        if (!controller || typeof controller.show !== 'function') {
            return;
        }

        controller.show({
            label: this.t('tutorial.buttons.skip', '跳过'),
            onSkip: () => this.handleTutorialSkipRequest()
        });
    }

    handleTutorialSkipRequest() {
        return Promise.resolve(this.requestTutorialEnd('skip'));
    }

    /**
     * 移除「跳过」按钮
     */
    hideSkipButton() {
        const controller = this.ensureTutorialSkipController();
        if (controller && typeof controller.hide === 'function') {
            controller.hide();
        }
    }

    /**
     * 检查并等待浮动按钮创建（用于主页引导）
     * 优先监听 live2d-floating-buttons-ready 事件（Live2D / VRM / MMD 均会派发），
     * 辅以轮询兜底，解决模型加载慢导致教程跳过按钮步骤的问题。
     */
    async waitForFloatingButtons(maxWaitTime = 60000) {
        const startedAt = Date.now();
        const buttonsFound = await new Promise((resolve) => {
            // 检查任意模型类型的浮动按钮容器是否已存在
            const findExisting = () =>
                document.getElementById('live2d-floating-buttons') ||
                document.getElementById('vrm-floating-buttons') ||
                document.getElementById('mmd-floating-buttons') ||
                document.getElementById('pngtuber-floating-buttons');

            if (findExisting()) {
                resolve(true);
                return;
            }

            let resolved = false;
            const done = (result) => {
                if (resolved) return;
                resolved = true;
                clearTimeout(timer);
                clearInterval(poller);
                window.removeEventListener('live2d-floating-buttons-ready', onReady);
                resolve(result);
            };

            // 1. 事件监听（所有模型类型都派发 live2d-floating-buttons-ready）
            const onReady = () => {
                done(true);
            };
            window.addEventListener('live2d-floating-buttons-ready', onReady);

            // 2. 轮询兜底（防止事件在监听注册前已派发）
            const poller = setInterval(() => {
                if (findExisting()) {
                    done(true);
                }
            }, 500);

            // 3. 超时兜底
            const timer = setTimeout(() => {
                console.warn(`[Tutorial] 等待浮动按钮超时（${maxWaitTime / 1000}秒）`);
                done(false);
            }, maxWaitTime);
        });
        if (!buttonsFound) {
            return false;
        }
        const remainingMs = Math.max(0, maxWaitTime - (Date.now() - startedAt));
        const live2dIdle = await this.waitForLive2dModelLoadIdle(remainingMs);
        if (!live2dIdle) {
            console.warn(`[Tutorial] 等待 Live2D 模型加载完成超时（${remainingMs / 1000}秒），浮动按钮已存在，继续启动教程`);
        }
        return true;
    }















    /**
     * 引导结束时的回调
     */
    onTutorialEnd() {
        if (this._tutorialEndHandled) {
            return this._teardownPromise || Promise.resolve();
        }

        this._tutorialEndHandled = true;
        const finalSteps = this.cachedValidSteps || [];
        const endMeta = this.resolveTutorialEndMeta(finalSteps);
        const avatarFloatingRound = this.activeAvatarFloatingGuideRound;

        this.broadcastYuiGuideTerminationRequest(endMeta);
        this.clearAllTutorialLifecycles(endMeta.rawReason);
        const completedSource = this.currentTutorialStartSource;

        const teardownPromise = this._teardownTutorialUI();
        const startupGreetingReleaseReason = endMeta.reason === 'complete'
            ? 'tutorial-completed'
            : (endMeta.reason === 'skip' ? 'tutorial-skipped' : 'tutorial-' + endMeta.reason);
        const startupGreetingReleasePromise = Promise.resolve(teardownPromise).finally(() => {
            this.dispatchStartupGreetingRelease(startupGreetingReleaseReason, {
                rawReason: endMeta.rawReason,
                source: completedSource,
                day: avatarFloatingRound || undefined
            });
        });

        if (avatarFloatingRound) {
            this.markAvatarFloatingGuideRoundOutcome(avatarFloatingRound, endMeta.reason, endMeta.rawReason);
            this.activeAvatarFloatingGuideRound = null;
        } else if (this.currentPage === 'home' && (endMeta.reason === 'complete' || endMeta.reason === 'skip')) {
            this.markAvatarFloatingGuideRoundOutcome(1, endMeta.reason, endMeta.rawReason);
        }
        let avatarFloatingEndState = null;
        if (avatarFloatingRound || this.currentPage === 'home') {
            avatarFloatingEndState = recordAvatarFloatingGuideEndState(
                avatarFloatingRound || 1,
                endMeta.reason,
                endMeta.rawReason,
                completedSource
            );
        }
        if (avatarFloatingEndState && (endMeta.reason === 'complete' || endMeta.reason === 'skip')) {
            if (avatarFloatingEndState.day === 1) {
                recordAvatarFloatingGuideUsageRoundEnd(1);
            }
            const avatarFloatingEndEventName = endMeta.reason === 'skip'
                ? 'neko:avatar-floating-guide-skip'
                : 'neko:avatar-floating-guide-complete';
            window.dispatchEvent(new CustomEvent(avatarFloatingEndEventName, {
                detail: {
                    page: this.currentPage,
                    source: completedSource,
                    reason: endMeta.rawReason,
                    day: avatarFloatingEndState.day,
                    endState: avatarFloatingEndState
                }
            }));
        }
        const scheduledDay1SystrayIntroPromise = this.scheduleDay1SystrayIntroAfterTeardown(
            startupGreetingReleasePromise,
            endMeta,
            avatarFloatingEndState
        );
        const stateApi = getSevenDayTutorialStateApi();
        const stateFlushPromise = typeof stateApi.flush === 'function'
            ? stateApi.flush()
            : Promise.resolve();
        const day1SystrayIntroPromise = Promise.all([
            scheduledDay1SystrayIntroPromise,
            stateFlushPromise,
        ]).then(results => results[0]);

        if (endMeta.reason === 'destroy') {
            window.dispatchEvent(new CustomEvent('neko:tutorial-ended-without-completion', {
                detail: {
                    page: this.currentPage,
                    source: completedSource,
                    reason: endMeta.rawReason,
                    day: avatarFloatingEndState ? avatarFloatingEndState.day : undefined,
                }
            }));
            this.logTutorialFlow('tutorial-ended-without-completion', {
                page: this.currentPage,
                source: completedSource,
                reason: endMeta.reason,
                rawReason: endMeta.rawReason
            });
            return startupGreetingReleasePromise;
        }

        // 主页 Day 1–7 由统一七日状态持久化；旧主页键只保留为迁移输入。
        if (this.currentPage !== 'home') {
            localStorage.setItem(this.getStorageKey(), 'true');
        }

        if (endMeta.reason === 'skip') {
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: {
                    page: this.currentPage,
                    source: completedSource,
                    reason: endMeta.rawReason,
                    day: avatarFloatingEndState ? avatarFloatingEndState.day : undefined,
                    endState: avatarFloatingEndState
                }
            }));
            this.logTutorialFlow('tutorial-skipped', {
                page: this.currentPage,
                source: completedSource,
                reason: endMeta.reason,
                rawReason: endMeta.rawReason
            });
            return day1SystrayIntroPromise;
        }

        window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
            detail: {
                page: this.currentPage,
                source: completedSource,
                day: avatarFloatingEndState ? avatarFloatingEndState.day : undefined,
                endState: avatarFloatingEndState
            }
        }));
        this.logTutorialFlow('tutorial-completed', {
            page: this.currentPage,
            source: completedSource,
            reason: endMeta.reason,
            rawReason: endMeta.rawReason
        });
        return day1SystrayIntroPromise;
    }

    clearYuiGuideCompactChatFixedLayout(reason = 'tutorial-ended') {
        if (document.body) {
            document.body.classList.remove('yui-guide-compact-chat-fixed');
        }
        this.syncYuiGuideCompactChatFixedLayout(false, reason);
    }

    restoreYuiGuideChatInputState(reason = 'tutorial-ended') {
        const restoreReason = typeof reason === 'string' && reason.trim()
            ? reason.trim()
            : 'tutorial-ended';

        if (document.body) {
            document.body.classList.remove('yui-guide-chat-buttons-disabled');
        }
        this.clearYuiGuideCompactChatFixedLayout(restoreReason);

        const readonlyTargets = document.querySelectorAll(
            '#react-chat-window-shell textarea, '
            + '#react-chat-window-shell input, '
            + '#text-input-area textarea, '
            + '#text-input-area input'
        );
        readonlyTargets.forEach((element) => {
            if (!element || !('readOnly' in element)) {
                return;
            }

            const prevReadOnly = element.getAttribute('data-yui-guide-prev-readonly');
            if (prevReadOnly !== null) {
                element.readOnly = prevReadOnly === 'true';
                element.removeAttribute('data-yui-guide-prev-readonly');
            } else {
                element.readOnly = false;
            }
        });

        const contentEditableTargets = document.querySelectorAll(
            '#react-chat-window-shell [contenteditable="true"], '
            + '#react-chat-window-shell [contenteditable="plaintext-only"], '
            + '#react-chat-window-shell [data-yui-guide-prev-contenteditable]'
        );
        contentEditableTargets.forEach((element) => {
            if (!element || typeof element.getAttribute !== 'function') {
                return;
            }

            const prevContentEditable = element.getAttribute('data-yui-guide-prev-contenteditable');
            if (prevContentEditable !== null) {
                element.setAttribute('contenteditable', prevContentEditable);
                element.removeAttribute('data-yui-guide-prev-contenteditable');
            }
        });

        const host = window.reactChatWindowHost;
        if (host && typeof host.setHomeTutorialInteractionLocked === 'function') {
            try {
                host.setHomeTutorialInteractionLocked(false, restoreReason);
            } catch (error) {
                console.warn('[Tutorial] 恢复 React 聊天输入状态失败:', error);
            }
        }

        const channel = window.appInterpage && window.appInterpage.nekoBroadcastChannel;
        if (channel && typeof channel.postMessage === 'function') {
            try {
                channel.postMessage({
                    action: 'yui_guide_set_chat_buttons_disabled',
                    disabled: false,
                    reason: restoreReason,
                    timestamp: Date.now()
                });
            } catch (error) {
                console.warn('[Tutorial] 同步独立聊天窗输入恢复失败:', error);
            }
        }
    }

    /**
     * 拆除引导期间安装的 UI 状态（定时器、临时样式、监听器等）。
     * 不写入"已看过"存储，也不派发 tutorial-completed 事件，
     * 因此既能给正常结束（onTutorialEnd）复用，也能给启动失败的回退路径复用。
     */
    _teardownTutorialUI() {
        this.revealTutorialLive2dPrepared();
        this.clearYuiGuideCompactChatFixedLayout(
            this.lifecycleStateStore.getEndRawReason()
            || this.lifecycleStateStore.getEndReason()
            || 'tutorial-ended'
        );
        try {
            this.hideSkipButton();
        } catch (error) {
            console.warn('[Tutorial] hideSkipButton 失败:', error);
        }
        try {
            this.restoreAvatarFloatingModelInteractionState('teardown-early');
        } catch (error) {
            console.warn('[Tutorial] restoreAvatarFloatingModelInteractionState 失败:', error);
        }
        try {
            this.restoreYuiGuideChatInputState(
                this.lifecycleStateStore.getEndRawReason()
                || this.lifecycleStateStore.getEndReason()
                || 'tutorial-ended'
            );
        } catch (error) {
            console.warn('[Tutorial] restoreYuiGuideChatInputState 失败:', error);
        }

        if (this._teardownPromise) {
            return this._teardownPromise;
        }
        this._isDestroyed = true;
        // 重置运行标志
        this.isTutorialRunning = false;
        this.cachedValidSteps = null;
        this.lifecycleStateStore.resetEndReason();
        this.currentTutorialStartSource = 'auto';

        // 在解除教程全局锁前消费启动默认猫咪重试；后续模型恢复链会按快照只重放一次猫咪形态。
        this.consumePendingStartupDefaultCatRestoreRequest();

        // 清除全局引导标记
        window.isInTutorial = false;

        // 恢复页面滚动
        this.unlockBodyScroll();

        const teardownPromise = Promise.resolve()
            .then(() => this.restoreTutorialAvatarOverride())
            .then(() => this.clearTutorialYuiLive2dRuntimeResidue('tutorial-avatar-restored'))
            // 用户模型恢复后再按教程前的业务形态恢复；猫咪态不能只回放旧 DOM 的点击穿透属性。
            .then(() => this.restoreAvatarFloatingModelStateAfterTutorial())
            .catch(error => {
                console.warn('[Tutorial] 拆除引导时恢复头像失败:', error);
            })
            .finally(() => {
                this.dispatchAvatarFloatingTutorialInputRestored('tutorial-avatar-restored');
            })
            .finally(() => {
                this._tutorialModelPrefix = null;
                if (this._teardownPromise === teardownPromise) {
                    this._teardownPromise = null;
                }
            });
        this._teardownPromise = teardownPromise;
        return teardownPromise;
    }



    /**
     * 获取引导状态
     */
    hasSeenTutorial(page = null) {
        const targetPage = page || this.currentPage;
        if (targetPage === 'home') {
            const state = loadAvatarFloatingGuideState();
            return getSevenDayTutorialStateApi().isRoundSettled(state, 1);
        }
        if (!page) {
            return localStorage.getItem(this.getStorageKey()) === 'true';
        }

        const storageKeys = this.getStorageKeysForPage(page);
        return storageKeys.some(key => localStorage.getItem(key) === 'true');
    }


    /**
     * 等待指定时间
     * @param {number} ms - 毫秒数
     */
    sleep(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }



    /** 
     * 重置所有页面的引导状态 
     */ 
    async resetAllTutorials() {
        const stateApi = getSevenDayTutorialStateApi();
        stateApi.resetAll({ source: 'manual_all_tutorial_reset' });
        if (typeof stateApi.flush === 'function') {
            await stateApi.flush();
        }
        TUTORIAL_PAGES.forEach(page => {
            this.getResetStorageKeysForPage(page).forEach(key => localStorage.removeItem(key));
        });
        dispatchHomeTutorialResetEvent('all', 'manual_all_tutorial_reset');
    } 

    /**
     * 重置指定页面的引导状态
     */
    async resetPageTutorial(pageKey) {
        if (pageKey === 'all') {
            await this.resetAllTutorials();
            return;
        }

        if (pageKey === 'home') {
            this.resetAvatarFloatingGuideRoundState(1, {
                source: 'manual_home_tutorial_reset',
            });
            const stateApi = getSevenDayTutorialStateApi();
            if (typeof stateApi.flush === 'function') {
                await stateApi.flush();
            }
        }

        this.getResetStorageKeysForPage(pageKey).forEach((storageKey) => {
            localStorage.removeItem(storageKey);
        });

        if (pageKey === 'home') {
            dispatchHomeTutorialResetEvent('home', 'manual_home_tutorial_reset');
        }

    }

    /**
     * 重新启动当前页面的引导
     */
    async restartCurrentTutorial() {
        const restartRound = this.getHomeAvatarFloatingGuideStartRound({ includeActive: true });
        await this.requestTutorialEnd('restart');
        if (this._teardownPromise) {
            try {
                await this._teardownPromise;
            } catch (error) {
                console.warn('[Tutorial] 等待旧引导拆除失败，继续重启:', error);
            }
        }

        const storageKeys = this.getStorageKeysForPage(this.currentPage);
        storageKeys.forEach(storageKey => localStorage.removeItem(storageKey));
        this.pendingTutorialStartSource = 'manual';

        this.isTutorialRunning = false;
        if (this.currentPage === 'home' && restartRound) {
            this.resetAvatarFloatingGuideRoundState(restartRound, {
                source: 'restart_current_tutorial',
            });
            await this.startAvatarFloatingGuideRound(restartRound, { source: 'manual' });
            return;
        }

        await this.requestTutorialStart('manual');
    }
}

// 创建全局实例
window.universalTutorialManager = null;
window.__universalTutorialManagerResizeRetryBound = false;

function dispatchStartupGreetingReleaseWithoutManager(reason, detail = {}) {
    // 无管理器路径（如移动端禁用教程）同样代表新手教程不会占屏，清除 pending 兜底，避免选人格被永久挡住。
    window.isNekoHomeTutorialPending = false;
    const releaseDetail = Object.assign({
        released: true,
        page: 'unknown',
        reason: reason || 'tutorial-manager-unavailable',
        timestamp: Date.now()
    }, detail || {});
    try {
        window.__NEKO_STARTUP_GREETING_RELEASED__ = releaseDetail;
        window.dispatchEvent(new CustomEvent(STARTUP_GREETING_RELEASE_EVENT, {
            detail: releaseDetail
        }));
    } catch (error) {
        console.warn('[Tutorial] 无管理器启动问候放行事件派发失败:', error);
    }
    return releaseDetail;
}

async function destroyUniversalTutorialManagerInstance(reason = 'destroy') {
    const manager = window.universalTutorialManager;
    if (!manager) return;

    if (typeof manager.destroy === 'function') {
        await manager.destroy(reason);
    } else {
        if (manager.isTutorialRunning && typeof manager.onTutorialEnd === 'function') {
            manager.onTutorialEnd();
        } else if (typeof manager._teardownTutorialUI === 'function') {
            await manager._teardownTutorialUI();
        }
    }
    window.universalTutorialManager = null;
}

function bindUniversalTutorialManagerResizeRetry() {
    if (window.__universalTutorialManagerResizeRetryBound) return;
    window.__universalTutorialManagerResizeRetryBound = true;

    window.addEventListener('resize', function retryUniversalTutorialManagerInit() {
        if (window.innerWidth <= 768) return;
        window.removeEventListener('resize', retryUniversalTutorialManagerInit);
        window.__universalTutorialManagerResizeRetryBound = false;
        if (window.__universalTutorialManagerInitialized) return;
        initUniversalTutorialManager().then(function (initialized) {
            if (initialized !== false) {
                window.__universalTutorialManagerInitialized = true;
            }
        }).catch(function (error) {
            console.error('[App] 通用引导管理器延迟初始化失败:', error);
        });
    });
}

/**
 * 初始化通用教程管理器
 * 应在 DOM 加载完成后调用
 */
async function initUniversalTutorialManager() {
    const sevenDayStateApi = getSevenDayTutorialStateApi();
    if (typeof sevenDayStateApi.ready === 'function') {
        await sevenDayStateApi.ready();
    }

    // 手机端不启用教程，避免引导遮罩、接管拖拽和移动端布局互相干扰。
    if (window.innerWidth <= 768) {
        bindUniversalTutorialManagerResizeRetry();
        await destroyUniversalTutorialManagerInstance('mobile-disabled');
        dispatchStartupGreetingReleaseWithoutManager('mobile-tutorial-disabled', {
            page: UniversalTutorialManager.detectPage(),
            viewportWidth: window.innerWidth
        });
        return false;
    }

    // 检测当前页面类型
    const currentPageType = UniversalTutorialManager.detectPage();

    // 如果全局实例存在，检查页面是否改变
    if (window.universalTutorialManager) {
        if (window.universalTutorialManager.currentPage !== currentPageType) {
            try {
                await destroyUniversalTutorialManagerInstance('page-changed');
            } catch (error) {
                console.warn('[Tutorial] 等待旧教程实例拆除失败，继续创建新实例:', error);
            }
            // 创建新实例
            window.universalTutorialManager = new UniversalTutorialManager();
        }
    } else {
        // 创建新实例
        window.universalTutorialManager = new UniversalTutorialManager();
    }
    return true;
}

/**
 * 全局函数：重置所有引导
 * 供 HTML 按钮调用
 */
async function showTutorialResetMessage(message, options = {}) {
    const title = options.title || (window.t ? window.t('memory.tutorialReset', 'Tutorial') : 'Tutorial');
    if (typeof window.showTutorialResetNotice === 'function') {
        try {
            await window.showTutorialResetNotice(message, Object.assign({}, options, { title }));
            return;
        } catch (error) {
            console.warn('[TutorialReset] custom notice failed:', error);
        }
    }
    if (typeof window.showAlert === 'function') {
        try {
            await window.showAlert(message, title);
            return;
        } catch (error) {
            console.warn('[TutorialReset] common alert failed:', error);
        }
    }
    if (typeof window.alert === 'function') {
        window.alert(message);
        return;
    }
    console.log('[TutorialReset]', message);
}

async function resetAllTutorials() {
    if (window.universalTutorialManager) {
        await window.universalTutorialManager.resetAllTutorials();
    } else {
        // 如果管理器未初始化，直接清除 localStorage
        const stateApi = getSevenDayTutorialStateApi();
        stateApi.resetAll({ source: 'manual_all_tutorial_reset' });
        if (typeof stateApi.flush === 'function') {
            await stateApi.flush();
        }
        TUTORIAL_PAGES.forEach(page => {
            getTutorialStorageKeysForPageFallback(page).forEach(key => localStorage.removeItem(key));
        });
        dispatchHomeTutorialResetEvent('all', 'manual_all_tutorial_reset');
    }
    const resetMessage = window.t
        ? window.t('memory.tutorialResetSuccess', '已重置所有引导，下次进入各页面时将重新显示引导。')
        : '已重置所有引导，下次进入各页面时将重新显示引导。';
    await showTutorialResetMessage(resetMessage);
}

/**
 * 全局函数：重置指定页面的引导
 * 供下拉菜单调用
 */
async function resetTutorialForPage(pageKey) {
    if (!pageKey) return;

    if (pageKey === 'all') {
        await resetAllTutorials();
        return;
    }

    if (pageKey === 'current_personality') {
        fetch('/api/characters/persona-reselect-current', {
            method: 'POST',
        }).then(async (response) => {
            let payload = null;
            try {
                payload = await response.json();
            } catch (error) {
                payload = null;
            }
            if (!response.ok || !payload || payload.success !== true) {
                const fallbackError = window.t
                    ? window.t('memory.currentPersonalityResetFailed', '触发当前角色性格重选失败，请稍后再试。')
                    : '触发当前角色性格重选失败，请稍后再试。';
                void showTutorialResetMessage(payload && payload.error ? payload.error : fallbackError, { variant: 'error' });
                return;
            }

            const successMessage = window.t
                ? window.t('memory.currentPersonalityResetSuccess', '已记录当前角色的性格重选请求，请回到主页刷新后继续。')
                : '已记录当前角色的性格重选请求，请回到主页刷新后继续。';
            void showTutorialResetMessage(successMessage);
        }).catch(() => {
            const fallbackError = window.t
                ? window.t('memory.currentPersonalityResetFailed', '触发当前角色性格重选失败，请稍后再试。')
                : '触发当前角色性格重选失败，请稍后再试。';
            void showTutorialResetMessage(fallbackError, { variant: 'error' });
        });
        return;
    }

    if (window.universalTutorialManager) {
        await window.universalTutorialManager.resetPageTutorial(pageKey);
    } else {
        if (pageKey === 'home') {
            getSevenDayTutorialStateApi().resetRound(1, {
                source: 'manual_home_tutorial_reset',
            });
            const stateApi = getSevenDayTutorialStateApi();
            if (typeof stateApi.flush === 'function') {
                await stateApi.flush();
            }
        }
        getTutorialStorageKeysForPageFallback(pageKey).forEach(key => localStorage.removeItem(key));
        if (pageKey === 'home') {
            dispatchHomeTutorialResetEvent('home', 'manual_home_tutorial_reset');
        }
    }

    const pageNames = {
        'home': window.t ? window.t('memory.tutorialPageHome', '主页') : '主页',
        'model_manager': window.t ? window.t('memory.tutorialPageModelManager', '模型设置') : '模型设置',
        'parameter_editor': window.t ? window.t('memory.tutorialPageParameterEditor', '捏脸系统') : '捏脸系统',
        'emotion_manager': window.t ? window.t('memory.tutorialPageEmotionManager', '情感管理') : '情感管理',
        'chara_manager': window.t ? window.t('memory.tutorialPageCharaManager', '角色管理') : '角色管理',
        'settings': window.t ? window.t('memory.tutorialPageSettings', 'API设置') : 'API设置',
        'voice_clone': window.t ? window.t('memory.tutorialPageVoiceClone', '语音克隆') : '语音克隆',
        'steam_workshop': window.t ? window.t('steam.workshop', 'Steam创意工坊') : 'Steam创意工坊',
        'memory_browser': window.t ? window.t('memory.tutorialPageMemoryBrowser', '记忆浏览') : '记忆浏览',
        'current_personality': window.t ? window.t('memory.tutorialPageCurrentPersonality', '当前角色性格') : '当前角色性格'
    };
    const pageName = pageNames[pageKey] || pageKey;
    // 使用带参数的 i18n 键，格式：已重置「{{pageName}}」的引导
    const message = window.t
        ? window.t('memory.tutorialPageResetSuccessWithName', { pageName: pageName, defaultValue: `已重置「${pageName}」的引导，下次进入该页面时将重新显示引导。` })
        : `已重置「${pageName}」的引导，下次进入该页面时将重新显示引导。`;
    await showTutorialResetMessage(message);
}

/**
 * 全局函数：重新启动当前页面引导
 * 供帮助按钮调用
 */
function restartCurrentTutorial() {
    if (window.universalTutorialManager) {
        void window.universalTutorialManager.restartCurrentTutorial();
    }
}

// 导出供其他模块使用
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { UniversalTutorialManager, initUniversalTutorialManager };
}
