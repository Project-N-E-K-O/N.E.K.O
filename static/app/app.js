/**
 * app.js — 应用编排器 (Orchestrator)
 *
 * 仅负责：
 *   1. 全局安全函数 (window.t / safeT / closeAllSettingsWindows)
 *   2. DOM 元素初始化 → appState.dom
 *   3. 各模块 init() 调用
 *   4. WebSocket 连接 & 设置加载
 *   5. 页面生命周期（beforeunload / load / DOMContentLoaded）
 *
 * 业务逻辑已拆分到 app-*.js 模块中。
 */

// ======================== 全局安全函数 ========================

// 【防崩溃兜底】确保 window.t 始终是一个可调用的函数
if (typeof window.t !== 'function') {
    window.t = function (key, fallback) {
        if (typeof fallback === 'string') return fallback;
        if (fallback && typeof fallback === 'object' && fallback.defaultValue) {
            return fallback.defaultValue;
        }
        return key;
    };
}

// 全局安全的翻译函数
window.safeT = function (key, fallback) {
    if (window.t && typeof window.t === 'function') {
        const translated = window.t(key, fallback);
        if (typeof translated === 'string') return translated;
    }
    return typeof fallback === 'string' ? fallback : key;
};

// 音乐搜索纪元管理
let currentMusicSearchEpoch = 0;
window.invalidatePendingMusicSearch = function () {
    currentMusicSearchEpoch++;
    window._pendingMusicCommand = '';
    console.log(`[Music] 搜索纪元更新至: ${currentMusicSearchEpoch}, 已失效所有在途请求`);
};

// 上次用户输入时间
let lastUserInputTime = 0;
window.lastUserInputTime = lastUserInputTime;
Object.defineProperty(window, 'lastUserInputTime', {
    get: function () { return lastUserInputTime; },
    set: function (v) { lastUserInputTime = v; },
    configurable: true,
    enumerable: true,
});

// 关闭所有已打开的设置窗口
window.closeAllSettingsWindows = function () {
    if (window._openSettingsWindows) {
        Object.keys(window._openSettingsWindows).forEach(url => {
            try {
                const winRef = window._openSettingsWindows[url];
                if (winRef && !winRef.closed) winRef.close();
            } catch (_) { }
            delete window._openSettingsWindows[url];
        });
    }
    if (window.live2dManager && window.live2dManager._openSettingsWindows) {
        Object.keys(window.live2dManager._openSettingsWindows).forEach(url => {
            try {
                const winRef = window.live2dManager._openSettingsWindows[url];
                if (winRef && !winRef.closed) winRef.close();
            } catch (_) { }
            delete window.live2dManager._openSettingsWindows[url];
        });
    }
};

// ======================== 主初始化 ========================

function init_app() {
    const S = window.appState;

    // --- 缓存 DOM 引用 ---
    S.dom.micButton = document.getElementById('micButton');
    S.dom.muteButton = document.getElementById('muteButton');
    S.dom.screenButton = document.getElementById('screenButton');
    S.dom.stopButton = document.getElementById('stopButton');
    S.dom.resetSessionButton = document.getElementById('resetSessionButton');
    S.dom.returnSessionButton = document.getElementById('returnSessionButton');
    S.dom.statusElement = document.getElementById('status');
    S.dom.statusToast = document.getElementById('status-toast');
    S.dom.chatContainer = document.getElementById('chatContainer');
    S.dom.textInputBox = document.getElementById('textInputBox');
    S.dom.textInputArea = document.getElementById('text-input-area');
    S.dom.textSendButton = document.getElementById('textSendButton');
    S.dom.screenshotButton = document.getElementById('screenshotButton');
    S.dom.avatarPreviewButton = document.getElementById('avatarPreviewButton');
    S.dom.screenshotThumbnailContainer = document.getElementById('screenshot-thumbnail-container');
    S.dom.screenshotsList = document.getElementById('screenshots-list');
    S.dom.screenshotCount = document.getElementById('screenshot-count');
    S.dom.clearAllScreenshots = document.getElementById('clear-all-screenshots');

    // --- 初始化音乐消息提示词模块 ---
    if (typeof window.MusicPrompt !== 'undefined') {
        try {
            window.MusicPrompt.initMusicPromptModule(S.dom.textInputBox, S.dom.textInputArea);
            console.log('[MusicPrompt] 模块已初始化');
        } catch (e) {
            console.error('[MusicPrompt] 初始化失败:', e);
        }
    }

    // --- 初始化点歌台模块 ---
    if (typeof window.Jukebox !== 'undefined') {
        try {
            window.Jukebox.init();
            console.log('[Jukebox] 模块已初始化');
        } catch (e) {
            console.error('[Jukebox] 初始化失败:', e);
        }
    }

    // --- 初始化各模块 ---

    // UI 模块
    if (window.appUi && window.appUi.initFloatingButtonListeners) {
        window.appUi.initFloatingButtonListeners();
    }

    // 按钮事件绑定
    if (window.appButtons && window.appButtons.init) {
        window.appButtons.init();
    }

    if (window.appAutostartPrompt && window.appAutostartPrompt.init) {
        window.appAutostartPrompt.init();
    }

    if (window.appChatAvatar && window.appChatAvatar.init) {
        window.appChatAvatar.init();
    }
    // WebSocket 连接
    if (window.appWebSocket && window.appWebSocket.connectWebSocket) {
        window.appWebSocket.connectWebSocket();
    }

    // 设置加载后续初始化（mic/speaker + 主动搭话调度器）
    if (window.appSettings && window.appSettings.initProactiveChatScheduler) {
        window.appSettings.initProactiveChatScheduler();
    }

    // Agent UI 初始化
    if (window.appAgent && window.appAgent.setupAgentCheckboxListeners) {
        // Agent checkbox listeners are set up via live2d-floating-buttons-ready event
        // (already registered inside app-agent.js)
    }

    // UI guards（隐藏元素 + MutationObserver）
    if (window.appUi) {
        if (window.appUi.ensureHiddenElements) window.appUi.ensureHiddenElements();
        if (window.appUi.initFinalUiGuards) window.appUi.initFinalUiGuards();
    }

    // 页面卸载前清理屏幕捕获流
    window.addEventListener('beforeunload', () => {
        try {
            if (S.screenCaptureStream && typeof S.screenCaptureStream.getTracks === 'function') {
                S.screenCaptureStream.getTracks().forEach(track => {
                    try { track.stop(); } catch (e) { }
                });
            }
        } catch (e) { }
    });

    console.log('[App] init_app() 完成');
}

// ======================== 启动序列 ========================

const ready = async () => {
    if (ready._called) return;
    if (ready._inProgress) return;
    ready._inProgress = true;

    if (window.appStorageLocation && typeof window.appStorageLocation.init === 'function') {
        try {
            // 先经过网页端“存储位置哨兵”闸门，只有确认允许继续当前会话后，
            // 才继续 pageConfig 和主业务初始化。
            var storageDecision = await window.appStorageLocation.init();
            if (storageDecision && storageDecision.canContinue === false) {
                ready._inProgress = false;
                return;
            }
        } catch (error) {
            console.warn('[Init] storage location overlay init failed', error);
            ready._inProgress = false;
            return;
        }
    }

    ready._called = true;
    ready._inProgress = false;

    // 存储位置闸门放行后，才允许 pageConfig 开始加载。
    if (typeof window.startPageConfigLoad === 'function') {
        window.startPageConfigLoad();
    }

    // pageConfig 真正开始后，再等待页面配置就绪（带超时）
    if (window.pageConfigReady && typeof window.pageConfigReady.then === 'function') {
        const TIMEOUT = Symbol('timeout');
        const TIMEOUT_MS = 3000;
        let timeoutId = null;
        try {
            const result = await Promise.race([
                window.pageConfigReady,
                new Promise(resolve => {
                    timeoutId = setTimeout(() => resolve(TIMEOUT), TIMEOUT_MS);
                })
            ]);
            if (result === TIMEOUT) {
                console.warn(`[Init] pageConfigReady pending over ${TIMEOUT_MS}ms, continue with fallback config`);
            }
        } catch (error) {
            console.warn('[Init] pageConfigReady rejected, continue with fallback config', error);
        } finally {
            if (timeoutId !== null) clearTimeout(timeoutId);
        }
    }

    init_app();
};

if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(ready, 1);
} else {
    document.addEventListener('DOMContentLoaded', ready);
    window.addEventListener('load', ready);
}

// ======================== 页面加载后的事件 ========================

async function waitForStorageLocationStartupBarrierInternal() {
    if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
        try {
            await window.waitForStorageLocationStartupBarrier();
        } catch (error) {
            console.warn('[Init] waitForStorageLocationStartupBarrier failed', error);
        }
    } else if (window.__nekoStorageLocationStartupBarrier
        && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
        try {
            await window.__nekoStorageLocationStartupBarrier;
        } catch (error) {
            console.warn('[Init] __nekoStorageLocationStartupBarrier failed', error);
        }
    }
}

// 启动提示（chat 独立窗口不弹）
window.addEventListener('load', async () => {
    await waitForStorageLocationStartupBarrierInternal();

    const _isChatPage = window.location.pathname === '/chat'
        || window.location.pathname === '/chat/'
        || window.location.pathname === '/chat_full'
        || window.location.pathname === '/chat_full/';

    setTimeout(() => {
        if (_isChatPage) return;
        if (typeof window.showStatusToast === 'function' &&
            typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(
                window.t ? window.t('app.started', { name: lanlan_config.lanlan_name })
                    : `${lanlan_config.lanlan_name}已启动`,
                3000
            );
        }
    }, 1000);

    // 拉取待弹重要通知 + 版本更新日志（chat 独立窗口跳过）
    // 同一个 modal 队列；按 changelog（背景）→ pending notices（行动召唤）顺序入队。
    // 启动 gate：先等存档迁移/位置选择 + tutorial/初始人设走完，最多 15s 兜底防 hang。
    (async () => {
        if (_isChatPage) return;
        if (typeof window.showProminentNotice !== 'function') return;

        const NOTICE_GATE_FALLBACK_MS = 15000;
        try {
            if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
                await window.waitForStorageLocationStartupBarrier();
            }
            const onboarding = window.CharacterPersonalityOnboarding;
            if (onboarding && typeof onboarding.whenSettled === 'function') {
                // 超时只兜底"bootstrap 内部卡死"：whenSettled 没 resolve 且 overlay 也没显示。
                // 一旦 overlay 显示出来（用户在主动选择），就继续无限等 whenSettled——用户慢慢看 preset 是正常 UX，不该被超时强行放行。
                const settled = await Promise.race([
                    onboarding.whenSettled().then(() => true),
                    new Promise((resolve) => setTimeout(() => resolve(false), NOTICE_GATE_FALLBACK_MS)),
                ]);
                if (!settled) {
                    const overlayActive = (onboarding.overlay && !onboarding.overlay.hidden)
                        || onboarding.pendingResumeAfterTutorial;
                    if (overlayActive) {
                        await onboarding.whenSettled();
                    }
                }
            }
        } catch (_) { }

        // 更新日志/问卷要把 UI 语言传给后端做本地化下发。坑：本启动流程可能早于 i18next
        // init 完成就跑到这（init 内部要先 await 一次 Steam 语言查询），此时 window.i18next
        // .language 还是空 → 传 lang='' → 后端按中文原文下发（更新日志早读易踩；问卷在用户
        // 点完 changelog 弹窗后才读所以躲过，造成"更新日志中文、问卷正常"）。所以先 await
        // i18next ready 再取语言，拿到解析后的权威值（含 getInitialLanguage 不落盘的手动
        // uiLanguage 覆盖）；只有 init 彻底失败/超时才回退 localStorage。
        // ready 信号：i18next.isInitialized（已就绪直接过）或 i18n-i18next.js 在**所有终态**都会
        // 派发的 localechange 事件（finalizeInit 成功 / 无 backend 手动加载 / 初始化失败
        // exportFallbackFunctions 都派发），所以信号一定会来。超时给 12s 是为覆盖 i18n-i18next.js
        // 自身 bootstrap 的完整窗口：依赖轮询最多 5s + getInitialLanguage 的 Steam 语言查询最多
        // 2s，外加它 10s 硬安全网强制 init。5s 太短会在 i18next 尚未就绪时提前超时回退（Codex P2），
        // 12s 覆盖整段窗口后超时只兜「i18n 模块自身彻底卡死」这种极端情况。
        const _ensureI18nReady = (timeoutMs = 12000) => new Promise((resolve) => {
            if (window.i18next && window.i18next.isInitialized) { resolve(); return; }
            let done = false;
            let timerId;
            const finish = () => {
                if (done) return;
                done = true;
                clearTimeout(timerId);
                window.removeEventListener('localechange', finish);
                resolve();
            };
            window.addEventListener('localechange', finish);
            timerId = setTimeout(finish, timeoutMs);
        });
        // i18next ready 后 language 即权威值；万一 init 失败/超时，localStorage 的 i18nextLng
        // （getInitialLanguage 在 init 前对 query/steam 值落过盘）作末位兜底，仍比空 lang 强。
        // 与 app-websocket / app-proactive 等处的取法保持一致。
        const _resolveUiLang = () => {
            const live = (window.i18next && typeof window.i18next.language === 'string')
                ? window.i18next.language : '';
            return live || localStorage.getItem('i18nextLng') || '';
        };
        await _ensureI18nReady();

        // 1) 版本更新日志（先讲背景）
        try {
            const lastVer = localStorage.getItem('neko_last_notified_version') || '';
            const lang = _resolveUiLang();
            const cr = await fetch(`/api/changelog?since=${encodeURIComponent(lastVer)}&lang=${encodeURIComponent(lang)}`);
            const cdata = await cr.json();
            let entries = cdata.entries || [];
            // 问卷资格：在 step 1 改写 neko_last_notified_version 之前，把"本次是从旧版
            // 升上来的老玩家"持久化成独立 marker。不能从 neko_last_notified_version 现推
            // ——它马上会被改成当前版，一旦 step 1.5 的 /api/survey 这次失败/暂时非 steam
            // 没弹出，下次启动就分不清是升级老玩家还是首装本版，资格永久丢失。
            // 排除：全新用户(lastVer 空)、首装本版第二次启动(lastVer === 当前版)。
            // marker 落了之后只在问卷被真正处理（提交/跳过成功）时清除。
            if (lastVer && cdata.current_version && lastVer !== cdata.current_version) {
                localStorage.setItem('neko_survey_eligible_for', cdata.current_version);
            }
            // 全新用户（无历史记录）跳过版本更新弹窗，直接记录当前版本
            if (!lastVer) {
                if (cdata.current_version) {
                    localStorage.setItem('neko_last_notified_version', cdata.current_version);
                }
                entries = [];
            }
            if (entries.length > 0) {
                const changelogPromises = entries.map(entry => {
                    const changelogTitleKey = 'notice.changelog.title';
                    const resolvedChangelogTitle = typeof window.t === 'function'
                        ? window.t(changelogTitleKey)
                        : '';
                    const changelogTitle = (typeof resolvedChangelogTitle === 'string'
                        && resolvedChangelogTitle
                        && resolvedChangelogTitle !== changelogTitleKey)
                        ? resolvedChangelogTitle
                        : '更新内容';
                    return window.showProminentNotice({
                        kind: 'changelog',
                        version: entry.version,
                        title: changelogTitle,
                        message: `**v${entry.version} ${changelogTitle}**\n\n${(entry.content || '').trim()}`,
                    });
                });
                await Promise.all(changelogPromises);
                if (cdata.current_version) {
                    localStorage.setItem('neko_last_notified_version', cdata.current_version);
                }
            }
        } catch (_) { }

        // 1.5) 版本问卷（changelog 确认后，对老玩家追加）
        // 仅老玩家、当前版本有问卷、且这一版还没填过/跳过过时弹出。后端 /api/survey
        // 还会再做一道 DNT 门禁（关了被动统计的用户拿到 has_survey:false）。
        try {
            const _surveyEligibleFor = localStorage.getItem('neko_survey_eligible_for') || '';
            if (_surveyEligibleFor && typeof window.showSurveyModal === 'function') {
                const surveyLang = _resolveUiLang();
                const sr = await fetch(`/api/survey?lang=${encodeURIComponent(surveyLang)}`);
                const sdata = await sr.json();
                if (sdata && sdata.has_survey && sdata.survey) {
                    const surveyVer = sdata.survey_version || sdata.survey.survey_version || '';
                    const doneVer = localStorage.getItem('neko_last_survey_version') || '';
                    // 资格走持久化 marker（step 1 落的）：本次升级会话就算 /api/survey 失败，
                    // marker 仍在，下次启动重试，不会因 last_notified 已推进而漏掉老玩家。
                    if (surveyVer && _surveyEligibleFor === surveyVer && doneVer !== surveyVer) {
                        const result = await window.showSurveyModal(sdata.survey);
                        const submitHeaders = { 'Content-Type': 'application/json' };
                        const sec = window.nekoLocalMutationSecurity;
                        if (sec && typeof sec.getMutationHeaders === 'function') {
                            try { Object.assign(submitHeaders, await sec.getMutationHeaders()); } catch (_) { }
                        }
                        // 只有后端成功受理（resp.ok）才记本地完成标记；本地 POST 失败
                        // （CSRF token 没就绪 / 后端错误）时不标记，下次启动重弹一次，
                        // 避免把用户填好的答卷直接丢掉。后端已是 best-effort（远程上报
                        // 失败也回 ok:true），所以 resp.ok = 后端已受理即可视为完成。
                        let submitted = false;
                        try {
                            const sresp = await fetch('/api/survey/submit', {
                                method: 'POST',
                                headers: submitHeaders,
                                body: JSON.stringify({
                                    survey_version: surveyVer,
                                    action: (result && result.action) || 'skip',
                                    answers: (result && result.answers) || {},
                                }),
                            });
                            submitted = !!(sresp && sresp.ok);
                            if (!submitted) {
                                console.warn('[survey/submit] backend rejected with HTTP '
                                    + (sresp && sresp.status) + '; will re-prompt next launch');
                            }
                        } catch (e) {
                            console.warn('[survey/submit] request failed:', e);
                        }
                        if (submitted) {
                            localStorage.setItem('neko_last_survey_version', surveyVer);
                            // 真正处理完才清资格 marker；失败则留着下次重试。
                            localStorage.removeItem('neko_survey_eligible_for');
                        }
                    }
                }
            }
        } catch (_) { }

        // 2) 常规 prominent notices（行动召唤）
        try {
            const r = await fetch('/api/pending-notices');
            const data = await r.json();
            const notices = Array.isArray(data) ? data : (data.notices || []);
            const cursor = (data && typeof data.cursor === 'number') ? data.cursor : 0;
            if (notices.length > 0) {
                // 先全部入队（不 await），让 UI 能感知队列长度以显示"下一个"按钮
                const promises = notices.filter(Boolean).map(n => window.showProminentNotice(n));
                await Promise.all(promises);
                const ackHeaders = { 'Content-Type': 'application/json' };
                const sec = window.nekoLocalMutationSecurity;
                if (sec && typeof sec.getMutationHeaders === 'function') {
                    try { Object.assign(ackHeaders, await sec.getMutationHeaders()); } catch (_) { }
                }
                // 不能静默 .catch —— CodeRabbit on PR #1530 指出：如果 ACK
                // 因为 token 还没注入返了 403，原来的 `.catch(() => {})` 会
                // 当成成功，但后端没真正 drain cursor，下次启动还会再弹同一批。
                // 显式检查 response.ok + 在失败时 console.warn，让"下次启动
                // GET /api/pending-notices 仍带这批 → 再 ACK 一次"自然成为
                // 重试路径，不在这里硬塞 retry/queue 复杂度。
                try {
                    const ackResp = await fetch('/api/pending-notices/ack', {
                        method: 'POST',
                        headers: ackHeaders,
                        body: JSON.stringify({ cursor }),
                    });
                    if (!ackResp.ok) {
                        console.warn(
                            '[pending-notices/ack] backend rejected ack '
                            + 'with HTTP ' + ackResp.status
                            + '; notices will be re-shown on next page load',
                        );
                    }
                } catch (e) {
                    console.warn('[pending-notices/ack] ack request failed:', e);
                }
            }
        } catch (_) { }
    })();
});

// 监听 voice_id 更新和 VRM 表情预览消息
window.addEventListener('message', function (event) {
    if (event.origin !== window.location.origin) return;
    if (!event || !event.data || typeof event.data.type === 'undefined') return;

    if (event.data.type === 'voice_id_updated') {
        console.log('[Voice Clone] 收到voice_id更新消息:', event.data.voice_id);
        if (typeof window.showStatusToast === 'function' &&
            typeof lanlan_config !== 'undefined' && lanlan_config.lanlan_name) {
            window.showStatusToast(
                window.t ? window.t('app.voiceUpdated', { name: lanlan_config.lanlan_name })
                    : `${lanlan_config.lanlan_name}的语音已更新`,
                3000
            );
        }
    }

    if (event.data.type === 'vrm-preview-expression') {
        if (typeof event.data.expression === 'undefined') return;
        console.log('[VRM] 收到表情预览请求:', event.data.expression);
        if (window.vrmManager && window.vrmManager.expression) {
            window.vrmManager.expression.setBaseExpression(event.data.expression);
        }
    }

    if (event.data.type === 'vrm-get-expressions') {
        console.log('[VRM] 收到表情列表请求');
        let expressions = [];
        if (window.vrmManager && window.vrmManager.expression) {
            expressions = window.vrmManager.expression.getExpressionList();
        }
        if (event.source) {
            event.source.postMessage({
                type: 'vrm-expressions-response',
                expressions: expressions
            }, window.location.origin);
        }
    }

    if (event.data.type === 'mmd-get-morphs') {
        console.log('[MMD] 收到 Morph 列表请求');
        let morphs = [];
        if (window.mmdManager && window.mmdManager.expression) {
            morphs = window.mmdManager.expression.getMorphNames();
        }
        if (event.source) {
            event.source.postMessage({
                type: 'mmd-morphs-response',
                morphs: morphs
            }, window.location.origin);
        }
    }

    if (event.data.type === 'mmd-preview-morph') {
        if (typeof event.data.morph === 'undefined') return;
        console.log('[MMD] 收到 Morph 预览请求:', event.data.morph);
        if (window.mmdManager && window.mmdManager.expression) {
            clearTimeout(window._mmdMorphPreviewTimer);
            window.mmdManager.expression._clearEmotionMorphs();
            window.mmdManager.expression.setMorphWeight(event.data.morph, 1.0);
            var morphToReset = event.data.morph;
            window._mmdMorphPreviewTimer = setTimeout(() => {
                if (window.mmdManager && window.mmdManager.expression) {
                    window.mmdManager.expression.setMorphWeight(morphToReset, 0);
                }
            }, 3000);
        }
    }
});
