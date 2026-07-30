(function () {
    'use strict';

    var SOURCE = 'new_user_icebreaker';
    var ICEBREAKER_API_BASE = '/api/icebreaker';
    var STORAGE_KEY = 'neko.new_user_icebreaker.v1';
    var ICEBREAKER_BRIDGE_STORAGE_KEY = 'neko_new_user_icebreaker_bridge_event';
    var SCRIPT_URL = '/static/tutorial/icebreaker/icebreaker_scripts.json';
    var LOCALE_BASE_URL = '/static/tutorial/icebreaker/locales/';
    var TRIGGER_WINDOW_MS = 2 * 60 * 1000;
    var PERSISTED_END_WINDOW_MS = 15 * 60 * 1000;
    var TUTORIAL_IDLE_RETRY_MS = 500;
    var CHOICE_PROMPT_REVEAL_MIN_DELAY_MS = 700;
    var CHOICE_PROMPT_REVEAL_MAX_DELAY_MS = 1400;
    var CHOICE_PROMPT_REVEAL_SPEECH_RATIO = 0.18;
    var TTS_REQUEST_MAX_WAIT_MS = 12000;
    var assistantLoading = window.NekoIcebreakerAssistantLoading;
    var freeTextRuntime = window.NekoIcebreakerFreeTextRuntime;
    var FREE_TEXT_TOPIC_ON_TOPIC = freeTextRuntime && freeTextRuntime.TOPIC_ON_TOPIC || 'on_topic';
    var FREE_TEXT_TOPIC_SOFT_DERAIL = freeTextRuntime && freeTextRuntime.TOPIC_SOFT_DERAIL || 'soft_derail';
    var FREE_TEXT_TOPIC_HARD_EXIT = freeTextRuntime && freeTextRuntime.TOPIC_HARD_EXIT || 'hard_exit';
    var activeSession = null;
    var pendingStartDay = '';
    var pendingGuideEndStateDay = '';
    var pendingGuideEndState = null;
    var pendingGuideEndStartPromise = null;
    var scriptPromise = null;
    var localePromises = Object.create(null);
    var icebreakerSortKeySeq = 0;
    var icebreakerBridgeTimestampSeq = 0;
    var contextAppendPromise = Promise.resolve();
    var freeTextState = freeTextRuntime && typeof freeTextRuntime.createRuntimeStateStore === 'function'
        ? freeTextRuntime.createRuntimeStateStore()
        : null;

    function safeJsonParse(raw, fallback) {
        if (!raw) return fallback;
        try {
            return JSON.parse(raw);
        } catch (_) {
            return fallback;
        }
    }

    function readStore() {
        try {
            return safeJsonParse(window.localStorage.getItem(STORAGE_KEY), { version: 1, days: {} });
        } catch (_) {
            return { version: 1, days: {} };
        }
    }

    function writeStore(store) {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store || { version: 1, days: {} }));
        } catch (_) {}
    }

    function markDay(day, patch) {
        var key = String(day || '');
        if (!key) return;
        var store = readStore();
        if (!store || typeof store !== 'object') store = { version: 1, days: {} };
        if (!store.days || typeof store.days !== 'object') store.days = {};
        store.days[key] = Object.assign({}, store.days[key] || {}, patch || {});
        writeStore(store);
    }

    function isDayCompleted(day) {
        var store = readStore();
        var entry = store && store.days ? store.days[String(day || '')] : null;
        return !!(entry && entry.completed);
    }

    function isPeriodActive() {
        return !!(activeSession || pendingStartDay || pendingGuideEndStateDay);
    }

    function fetchJson(url) {
        return fetch(url, { credentials: 'same-origin', cache: 'no-store' }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        });
    }

    function getPageConfigUrl() {
        var lanlanName = resolveLanlanName();
        var suffix = lanlanName ? ('?lanlan_name=' + encodeURIComponent(lanlanName)) : '';
        return '/api/config/page_config' + suffix;
    }

    function getLocalMutationHeaders() {
        var headers = { 'Content-Type': 'application/json' };
        var security = window.nekoLocalMutationSecurity;
        if (security && typeof security.getMutationHeaders === 'function') {
            return Promise.resolve(security.getMutationHeaders()).then(function (mutationHeaders) {
                return Object.assign(headers, mutationHeaders || {});
            }).catch(function () {
                return headers;
            });
        }
        return fetch(getPageConfigUrl(), {
            credentials: 'same-origin',
            cache: 'no-store'
        }).then(function (response) {
            if (!response.ok) return headers;
            return response.json();
        }).then(function (config) {
            if (config && typeof config.autostart_csrf_token === 'string' && config.autostart_csrf_token) {
                headers['X-CSRF-Token'] = config.autostart_csrf_token;
            }
            return headers;
        }).catch(function () {
            return headers;
        });
    }

    function refreshLocalMutationHeaders() {
        var security = window.nekoLocalMutationSecurity;
        if (security && typeof security.refreshToken === 'function') {
            return Promise.resolve(security.refreshToken()).then(function () {
                return getLocalMutationHeaders();
            }).catch(function () {
                return getLocalMutationHeaders();
            });
        }
        return getLocalMutationHeaders();
    }

    function postIcebreakerRoute(path, session, extraBody) {
        if (!session || !session.sessionId) return Promise.resolve(false);
        var body = Object.assign({
            lanlan_name: resolveLanlanName(),
            session_id: String(session.sessionId || ''),
            i18n_language: currentLocale()
        }, extraBody || {});

        function parseRouteResponse(response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json().then(function (data) {
                return !!(data && data.ok);
            });
        }

        return getLocalMutationHeaders().then(function (headers) {
            return fetch(ICEBREAKER_API_BASE + path, {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify(body)
            }).then(parseRouteResponse);
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] route lifecycle request failed:', path, error);
            return false;
        });
    }

    function startIcebreakerRoute(session) {
        return postIcebreakerRoute('/route/start', session, {
            source: SOURCE
        });
    }

    function clearPendingStartDay(dayKey) {
        if (pendingStartDay === dayKey) {
            pendingStartDay = '';
        }
    }

    function clearPendingGuideEndStateDay(dayKey) {
        if (pendingGuideEndStateDay === dayKey) {
            pendingGuideEndStateDay = '';
        }
        if (pendingGuideEndState && String(pendingGuideEndState.day || '') === dayKey) {
            pendingGuideEndState = null;
        }
    }

    function markPendingStartFromEndState(endState) {
        var dayKey = String(endState && endState.day || '');
        if (dayKey) {
            pendingGuideEndStateDay = dayKey;
            pendingGuideEndState = endState;
        }
        return dayKey;
    }

    function dispatchIcebreakerEnded(reason) {
        try {
            window.dispatchEvent(new CustomEvent('neko:new-user-icebreaker-ended', {
                detail: { reason: reason || 'complete' }
            }));
        } catch (_) {}
    }

    function endIcebreakerRoute(session, reason) {
        if (!session || session.routeEnded) return Promise.resolve(false);
        session.routeEnded = true;
        clearFreeTextRuntimeStateForSession(session);
        return postIcebreakerRoute('/route/end', session, {
            reason: reason || 'icebreaker_complete',
            postgameProactive: { enabled: false }
        });
    }

    function endIcebreakerRouteOnPageExit(reason) {
        var session = activeSession;
        if (!session || session.routeEnded || !session.sessionId) return;
        clearChoicePrompt();
        session.routeEnded = true;
        clearFreeTextRuntimeStateForSession(session);
        var body = {
            lanlan_name: resolveLanlanName(),
            session_id: String(session.sessionId || ''),
            i18n_language: currentLocale(),
            reason: reason || 'icebreaker_page_exit',
            postgameProactive: { enabled: false }
        };
        try {
            var security = window.nekoLocalMutationSecurity;
            if (security && typeof security.peekCachedToken === 'function') {
                var token = security.peekCachedToken();
                if (token) {
                    body._csrf_token = token;
                }
            }
        } catch (_) {}
        var rawBody = JSON.stringify(body);
        try {
            if (navigator.sendBeacon && typeof Blob === 'function') {
                if (navigator.sendBeacon(
                    ICEBREAKER_API_BASE + '/route/end',
                    new Blob([rawBody], { type: 'application/json' })
                )) {
                    return;
                }
            }
        } catch (error) {
            console.warn('[NewUserIcebreaker] route lifecycle beacon failed:', error);
        }
        try {
            fetch(ICEBREAKER_API_BASE + '/route/end', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                keepalive: true,
                body: rawBody
            }).catch(function (error) {
                console.warn('[NewUserIcebreaker] route lifecycle keepalive failed:', error);
            });
        } catch (error) {
            console.warn('[NewUserIcebreaker] route lifecycle keepalive threw:', error);
        }
    }

    function loadScripts() {
        if (!scriptPromise) {
            scriptPromise = fetchJson(SCRIPT_URL);
        }
        return scriptPromise;
    }

    function normalizeLocale(locale) {
        var value = String(locale || '').trim();
        if (!value) return 'zh-CN';
        var lower = value.toLowerCase();
        if (lower === 'zh' || lower === 'zh-cn' || lower === 'zh-hans') return 'zh-CN';
        if (lower === 'zh-tw' || lower === 'zh-hk' || lower === 'zh-hant') return 'zh-TW';
        if (lower.indexOf('ja') === 0) return 'ja';
        if (lower.indexOf('ko') === 0) return 'ko';
        if (lower.indexOf('ru') === 0) return 'ru';
        if (lower.indexOf('es') === 0) return 'es';
        if (lower.indexOf('pt') === 0) return 'pt';
        if (lower.indexOf('en') === 0) return 'en';
        return value;
    }

    function currentLocale() {
        try {
            if (window.i18next && window.i18next.language) {
                return normalizeLocale(window.i18next.language);
            }
        } catch (_) {}
        try {
            return normalizeLocale(window.localStorage.getItem('i18nextLng'));
        } catch (_) {
            return 'zh-CN';
        }
    }

    function loadLocale(locale) {
        var normalized = normalizeLocale(locale);
        if (!localePromises[normalized]) {
            localePromises[normalized] = fetchJson(LOCALE_BASE_URL + encodeURIComponent(normalized) + '.json')
                .catch(function () {
                    if (normalized === 'zh-CN') return {};
                    return loadLocale('zh-CN');
                });
        }
        return localePromises[normalized];
    }

    function getText(localeData, key) {
        if (!key) return '';
        return String((localeData && localeData[key]) || '');
    }

    function estimateSpeechDurationMs(text) {
        var value = String(text || '');
        if (!value) return 0;
        return Math.min(9000, Math.max(1400, value.length * 120));
    }

    // 选项揭示延迟：让选项按钮在 assistant 台词上屏、开始播放之后再露出，避免选项与
    // 台词同时蹦出。注意这只是「视觉」延迟——choicePrompt 仍会立刻下发给 chat host 完成
    // 输入路由绑定（host 据此把间隙内的自由文本判为 icebreaker free-text 而非普通聊天），
    // 真正延后的只是按钮的可见性（host 按 revealDelayMs 计时露出）。延迟若改回「扣住
    // choicePrompt 不下发」会重新打开间隙内输入落到普通聊天的窗口。
    function computeChoicePromptRevealDelay(text) {
        var speechDuration = estimateSpeechDurationMs(text);
        return Math.min(
            CHOICE_PROMPT_REVEAL_MAX_DELAY_MS,
            Math.max(CHOICE_PROMPT_REVEAL_MIN_DELAY_MS, speechDuration * CHOICE_PROMPT_REVEAL_SPEECH_RATIO)
        );
    }

    function resolveLanlanName() {
        try {
            return (window.appState && window.appState.lanlan_name)
                || (window.lanlan_config && window.lanlan_config.lanlan_name)
                || window._currentCatgirl
                || window.currentCatgirl
                || '';
        } catch (_) {
            return '';
        }
    }

    function resolveAuthor() {
        return resolveLanlanName() || 'N.E.K.O';
    }

    function makeIcebreakerApiError(reason, payload) {
        var normalizedReason = String(reason || 'icebreaker_api_error');
        var error = new Error(normalizedReason);
        error.reason = normalizedReason;
        error.payload = payload || null;
        return error;
    }

    function isIcebreakerRouteInactiveError(error) {
        var reason = String((error && error.reason) || (error && error.message) || '');
        return reason === 'route_not_active'
            || reason === 'stale_session'
            || reason === 'session_id_mismatch'
            || reason === 'missing_session_id';
    }

    function resolveAssistantAvatarUrl() {
        try {
            if (window.appChatAvatar && typeof window.appChatAvatar.getCurrentAvatarDataUrl === 'function') {
                return window.appChatAvatar.getCurrentAvatarDataUrl() || '';
            }
        } catch (_) {}
        return '';
    }

    function waitForChatHost(timeoutMs) {
        var deadline = Date.now() + (Number.isFinite(timeoutMs) ? timeoutMs : 4000);
        return new Promise(function (resolve, reject) {
            function tick() {
                var host = window.reactChatWindowHost;
                if (host && typeof host.appendMessage === 'function') {
                    resolve(host);
                    return;
                }
                if (Date.now() >= deadline) {
                    reject(new Error('react_chat_host_unavailable'));
                    return;
                }
                window.setTimeout(tick, 80);
            }
            tick();
        });
    }

    function makeMessageId(prefix) {
        return prefix + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8);
    }

    function nextIcebreakerSortKey() {
        icebreakerSortKeySeq = Math.max(icebreakerSortKeySeq + 1, Date.now());
        return icebreakerSortKeySeq;
    }

    function nextIcebreakerBridgeTimestamp() {
        icebreakerBridgeTimestampSeq = Math.max(icebreakerBridgeTimestampSeq + 1, Date.now());
        return icebreakerBridgeTimestampSeq;
    }

    function getBroadcastChannel() {
        try {
            var interpage = window.NekoInterpage || window.nekoInterpage || window.appInterpage;
            var channel = interpage && interpage.nekoBroadcastChannel;
            return channel && typeof channel.postMessage === 'function' ? channel : null;
        } catch (_) {
            return null;
        }
    }

    function shouldRenderIcebreakerOnLocalChatHost() {
        try {
            var path = String((window.location && window.location.pathname) || '');
            if (window.__NEKO_MULTI_WINDOW__ === true && !/^\/chat(?:\/|$)/.test(path)) {
                return false;
            }
        } catch (_) {}
        return true;
    }

    function broadcastIcebreaker(action, payload) {
        var message = Object.assign({
            action: action,
            lanlan_name: resolveLanlanName(),
            timestamp: nextIcebreakerBridgeTimestamp()
        }, payload || {});
        var channel = getBroadcastChannel();
        if (channel) {
            try {
                channel.postMessage(message);
            } catch (error) {
                console.warn('[NewUserIcebreaker] broadcast failed:', action, error);
            }
        }
        try {
            window.localStorage.setItem(ICEBREAKER_BRIDGE_STORAGE_KEY, JSON.stringify(message));
            window.setTimeout(function () {
                try {
                    window.localStorage.removeItem(ICEBREAKER_BRIDGE_STORAGE_KEY);
                } catch (_) {}
            }, 0);
        } catch (error) {
            console.warn('[NewUserIcebreaker] storage bridge failed:', action, error);
        }
    }

    function broadcastIcebreakerAppendMessage(message) {
        broadcastIcebreaker(null, {
            action: 'icebreaker_append_chat_message',
            message: message
        });
    }

    function broadcastIcebreakerChoicePrompt(prompt) {
        broadcastIcebreaker(null, {
            action: 'icebreaker_set_choice_prompt',
            prompt: prompt
        });
    }

    function broadcastIcebreakerClearChoicePrompt(sessionId) {
        broadcastIcebreaker(null, {
            action: 'icebreaker_clear_choice_prompt',
            sessionId: sessionId
        });
    }

    function broadcastIcebreakerClearChoicePromptSource(source, reason) {
        broadcastIcebreaker(null, {
            action: 'icebreaker_clear_choice_prompt_source',
            source: source || SOURCE,
            reason: reason || 'icebreaker_source_reset'
        });
    }

    function appendLlmContext(role, text, meta) {
        var cleanRole = String(role || '').trim();
        var cleanText = String(text || '').trim();
        if ((cleanRole !== 'assistant' && cleanRole !== 'user') || !cleanText) return Promise.resolve(false);
        var currentSession = activeSession || {};
        var extra = meta && typeof meta === 'object' ? meta : {};
        var body = {
            lanlan_name: resolveLanlanName(),
            role: cleanRole,
            text: cleanText,
            session_id: String(currentSession.sessionId || ''),
            request_id: String(extra.requestId || ''),
            event: {
                kind: 'icebreaker-context',
                source: SOURCE,
                day: String(extra.day || currentSession.day || ''),
                node_id: String(extra.nodeId || currentSession.nodeId || ''),
                choice: String(extra.choice || ''),
                voice_key: String(extra.voiceKey || ''),
                handoff: extra.handoff === true,
                fallback: String(extra.fallback || ''),
                free_text: extra.freeText === true,
                request_id: String(extra.requestId || '')
            }
        };
        function parseContextResponse(response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json().then(function (data) {
                return !!(data && data.ok);
            });
        }

        function postContextWithHeaders(headers, allowRetry) {
            return fetch(ICEBREAKER_API_BASE + '/context', {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify(body)
            }).then(function (response) {
                if (allowRetry && response.status === 403) {
                    return response.clone().json().catch(function () {
                        return null;
                    }).then(function (errorBody) {
                        if (errorBody && errorBody.error_code === 'csrf_validation_failed') {
                            return refreshLocalMutationHeaders().then(function (nextHeaders) {
                                return postContextWithHeaders(nextHeaders, false);
                            });
                        }
                        return parseContextResponse(response);
                    });
                }
                return parseContextResponse(response);
            });
        }

        contextAppendPromise = contextAppendPromise.catch(function () {}).then(function () {
            return getLocalMutationHeaders().then(function (headers) {
                return postContextWithHeaders(headers, true);
            }).catch(function (error) {
                console.warn('[NewUserIcebreaker] append LLM context failed:', error);
                return false;
            });
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] append LLM context failed:', error);
            return false;
        });
        return contextAppendPromise;
    }

    // 把用户在破冰里点的「有效选项」追加进后端持久化选项池（/api/icebreaker/choice）。
    // 这条独立于 appendLlmContext：后者写的是临时会话上下文，这里写的是跨会话留存、
    // 当前不喂模型也不进记忆的独立信号，纯 fire-and-forget，失败只告警不阻断教程流。
    function recordChoiceToPool(meta) {
        var info = meta && typeof meta === 'object' ? meta : {};
        var nodeId = String(info.nodeId || '').trim();
        var choice = String(info.choice || '').trim();
        if (!nodeId || !choice) return Promise.resolve(false);
        // 用 session 起始钉死的角色快照，而非点击时现取 resolveLanlanName()：若中途换了
        // 当前角色，选项仍归属到 /route/start 激活的那个角色，避免把后半段路径写到错角色
        // 名下（正是这个 per-角色池要避免的串味）。同步取值，防 activeSession 在 await 中被清。
        var lanlanName = String((activeSession && activeSession.lanlanName) || resolveLanlanName() || '');
        var body = {
            lanlan_name: lanlanName,
            session_id: String(info.sessionId || (activeSession && activeSession.sessionId) || ''),
            day: String(info.day || (activeSession && activeSession.day) || ''),
            node_id: nodeId,
            choice: choice,
            label: String(info.label || ''),
            handoff: info.handoff === true,
            completed: info.completed === true,
            seq: Number(info.seq) || 0
        };

        function parseChoiceResponse(response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json().then(function (data) {
                return !!(data && data.ok);
            });
        }

        // 与 /context 同款：缓存的 local-mutation token 过期（如后端重启而页面常驻）时，
        // 403 csrf_validation_failed 不当普通失败丢弃，刷新 token 后重试一次，避免静默漏记。
        function postChoiceWithHeaders(headers, allowRetry) {
            return fetch(ICEBREAKER_API_BASE + '/choice', {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                // keepalive：让本请求挺过页面卸载（reload/close），与 endIcebreakerRouteOnPageExit
                // 用 beacon/keepalive 发 /route/end 对称，避免点完即关页时浏览器取消普通 fetch 致丢记。
                // body 仅 ~200 字节，远低于 keepalive 的 64KB 上限。
                keepalive: true,
                body: JSON.stringify(body)
            }).then(function (response) {
                if (allowRetry && response.status === 403) {
                    return response.clone().json().catch(function () {
                        return null;
                    }).then(function (errorBody) {
                        if (errorBody && errorBody.error_code === 'csrf_validation_failed') {
                            return refreshLocalMutationHeaders().then(function (nextHeaders) {
                                return postChoiceWithHeaders(nextHeaders, false);
                            });
                        }
                        return parseChoiceResponse(response);
                    });
                }
                return parseChoiceResponse(response);
            });
        }

        return getLocalMutationHeaders().then(function (headers) {
            return postChoiceWithHeaders(headers, true);
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] record choice to pool failed:', error);
            return false;
        });
    }

    // Also defined in app-interpage for the standalone chat bridge path.
    function getIcebreakerMessageText(message) {
        var blocks = message && Array.isArray(message.blocks) ? message.blocks : [];
        for (var i = 0; i < blocks.length; i++) {
            if (blocks[i] && blocks[i].type === 'text') {
                var text = String(blocks[i].text || '').trim();
                if (text) return text;
            }
        }
        return '';
    }

    function syncIcebreakerAssistantCompactCaption(role, message) {
        if (role !== 'assistant') return;
        var line = getIcebreakerMessageText(message);
        if (!line) return;
        var turnId = String((message && (message.turnId || message.id)) || makeMessageId('icebreaker-turn'));
        var detail = {
            turnId: turnId,
            segmentId: turnId + ':icebreaker',
            text: line,
            source: SOURCE
        };
        try {
            window.dispatchEvent(new CustomEvent('neko-assistant-turn-start', {
                detail: {
                    turnId: turnId,
                    source: SOURCE
                }
            }));
            window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
                detail: detail
            }));
        } catch (error) {
            console.warn('[NewUserIcebreaker] compact caption sync failed:', error);
        }
    }

    function finalizeIcebreakerAssistantSubtitleTranslation(role, message) {
        if (role !== 'assistant') return;
        var line = getIcebreakerMessageText(message);
        if (!line) return;
        try {
            var bridge = window.subtitleBridge;
            if (!bridge || typeof bridge.finalizeTurnWithTranslation !== 'function') {
                return;
            }
            if (typeof bridge.beginTurn === 'function') {
                bridge.beginTurn({ latch: false });
            }
            var result = bridge.finalizeTurnWithTranslation(line);
            if (result && typeof result.catch === 'function') {
                result.catch(function (error) {
                    console.warn('[NewUserIcebreaker] subtitle translation failed:', error);
                });
            }
        } catch (error) {
            console.warn('[NewUserIcebreaker] subtitle translation failed:', error);
        }
    }

    function waitForIcebreakerChatHostMounted(host) {
        return new Promise(function (resolve) {
            var attempts = 0;
            function checkMounted() {
                var isMounted = false;
                try {
                    isMounted = !!(host && typeof host.isMounted === 'function' && host.isMounted());
                } catch (_) {}
                if (isMounted || attempts >= 100) {
                    window.setTimeout(resolve, 0);
                    return;
                }
                attempts += 1;
                window.setTimeout(checkMounted, 50);
            }
            checkMounted();
        });
    }

    function showIcebreakerAssistantFakeLoading(session) {
        if (!assistantLoading || typeof assistantLoading.showAssistantFakeLoading !== 'function') {
            return Promise.resolve(false);
        }
        return assistantLoading.showAssistantFakeLoading({
            session: session,
            source: SOURCE,
            getActiveSession: function () { return activeSession; },
            shouldRender: shouldRenderIcebreakerOnLocalChatHost,
            waitForChatHost: waitForChatHost,
            waitForMounted: waitForIcebreakerChatHostMounted
        });
    }

    function appendAssistantChatMessage(text, meta, session) {
        var targetSession = session || activeSession;
        return showIcebreakerAssistantFakeLoading(targetSession).then(function () {
            if (targetSession && activeSession !== targetSession) return null;
            return appendChatMessage('assistant', text, meta);
        }).then(function (message) {
            if (targetSession && activeSession !== targetSession) return null;
            return message;
        });
    }

    function didAppendChatMessage(message) {
        return !!message;
    }

    function appendChatMessage(role, text, meta) {
        var messageText = String(text || '').trim();
        if (!messageText) return Promise.resolve(null);
        var message = {
            id: makeMessageId(role === 'user' ? 'icebreaker-user' : 'icebreaker-assistant'),
            role: role,
            author: role === 'user' ? '你' : resolveAuthor(),
            time: '',
            createdAt: Date.now(),
            blocks: [{ type: 'text', text: messageText }],
            status: 'sent',
            sortKey: nextIcebreakerSortKey(),
            avatarLabel: role === 'assistant' ? resolveAuthor() : undefined,
            avatarUrl: role === 'assistant' ? resolveAssistantAvatarUrl() : undefined,
            actions: undefined,
            icebreaker: Object.assign({ source: SOURCE }, meta || {})
        };
        broadcastIcebreakerAppendMessage(message);
        return appendLlmContext(role, messageText, meta || {}).then(function () {
            if (!shouldRenderIcebreakerOnLocalChatHost()) {
                // In desktop multi-window mode the standalone /chat page renders the
                // bubble/caption, but the pet page remains the subtitle translation
                // owner. Finalize here so the translation panel is populated.
                finalizeIcebreakerAssistantSubtitleTranslation(role, message);
                return message;
            }
            var chatHost = null;
            return waitForChatHost(30000).then(function (host) {
                chatHost = host;
                if (typeof host.openWindow === 'function') {
                    host.openWindow();
                }
                return host.appendMessage(message);
            }).then(function (result) {
                if (!result) return result;
                return waitForIcebreakerChatHostMounted(chatHost).then(function () {
                    syncIcebreakerAssistantCompactCaption(role, message);
                    finalizeIcebreakerAssistantSubtitleTranslation(role, message);
                    return result;
                });
            });
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] append message failed:', error);
            return null;
        });
    }

    function speakViaProjectTts(text, voiceKey, signal) {
        var line = String(text || '').trim();
        if (!line) return Promise.resolve(false);
        var sessionId = activeSession && activeSession.sessionId ? activeSession.sessionId : '';
        var body = {
            lanlan_name: resolveLanlanName(),
            line: line,
            request_id: makeMessageId('icebreaker-tts'),
            session_id: sessionId,
            mirror_text: false,
            emit_turn_end: true,
            interrupt_audio: true,
            event: {
                kind: 'icebreaker-line',
                source: SOURCE,
                voice_key: String(voiceKey || '')
            }
        };
        return getLocalMutationHeaders().then(function (headers) {
            var requestOptions = {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify(body)
            };
            if (signal) requestOptions.signal = signal;
            return fetch(ICEBREAKER_API_BASE + '/speak', requestOptions);
        }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        }).then(function (data) {
            return !!(data && data.ok);
        }).catch(function (error) {
            if (error && error.name === 'AbortError') return false;
            console.warn('[NewUserIcebreaker] project TTS failed:', error);
            return false;
        });
    }

    function waitForTtsRequest(text, voiceKey) {
        return new Promise(function (resolve) {
            var settled = false;
            var controller = typeof AbortController === 'function' ? new AbortController() : null;
            var timeoutId = window.setTimeout(function () {
                if (controller) controller.abort();
                finish();
            }, TTS_REQUEST_MAX_WAIT_MS);
            function finish() {
                if (settled) return;
                settled = true;
                window.clearTimeout(timeoutId);
                resolve();
            }
            try {
                Promise.resolve(
                    speakViaProjectTts(text, voiceKey, controller ? controller.signal : undefined)
                ).then(finish).catch(function () {
                    finish();
                });
            } catch (error) {
                console.warn('[NewUserIcebreaker] project TTS failed:', error);
                finish();
            }
        });
    }

    function speakLine(text, voiceKey) {
        var speechDurationPromise = new Promise(function (resolve) {
            window.setTimeout(resolve, estimateSpeechDurationMs(text));
        });
        var ttsRequestPromise = waitForTtsRequest(text, voiceKey);
        return Promise.all([speechDurationPromise, ttsRequestPromise]).then(function () {});
    }

    function applyAssistantTextEmotion(text) {
        var line = String(text || '').trim();
        if (!line) return;
        try {
            if (window.appWebSocket && typeof window.appWebSocket.applyAssistantTextEmotion === 'function') {
                window.appWebSocket.applyAssistantTextEmotion(line, { source: SOURCE });
            }
        } catch (error) {
            console.warn('[NewUserIcebreaker] assistant text emotion failed:', error);
        }
    }

    function buildPromptOptions(node, localeData) {
        return (Array.isArray(node && node.options) ? node.options : []).map(function (option) {
            return {
                choice: String(option.id || ''),
                label: getText(localeData, option.labelKey)
            };
        }).filter(function (option) {
            return option.choice && option.label;
        });
    }

    function findOptionByChoice(node, choice) {
        if (freeTextRuntime && typeof freeTextRuntime.findOptionByChoice === 'function') {
            return freeTextRuntime.findOptionByChoice(node, choice);
        }
        return null;
    }

    function normalizeFreeTextInterpretation(data) {
        if (freeTextRuntime && typeof freeTextRuntime.normalizeInterpretation === 'function') {
            return freeTextRuntime.normalizeInterpretation(data);
        }
        return { action: 'respond_and_keep_options', choice: '', reply: '', topicState: FREE_TEXT_TOPIC_ON_TOPIC };
    }

    function clearFreeTextRuntimeStateForSession(session) {
        if (freeTextState && typeof freeTextState.clearForSession === 'function') {
            freeTextState.clearForSession(session);
        }
    }

    function getRecentFreeTextTurns(session, nodeId) {
        return freeTextState && typeof freeTextState.getRecentTurns === 'function'
            ? freeTextState.getRecentTurns(session, nodeId)
            : [];
    }

    function recordFreeTextTurn(session, turn, nodeId) {
        if (freeTextState && typeof freeTextState.recordTurn === 'function') {
            freeTextState.recordTurn(session, turn, nodeId);
        }
    }

    function getFreeTextDerailStreak(session, nodeId) {
        return freeTextState && typeof freeTextState.getDerailStreak === 'function'
            ? freeTextState.getDerailStreak(session, nodeId)
            : 0;
    }

    function setFreeTextDerailStreak(session, nodeId, value) {
        if (freeTextState && typeof freeTextState.setDerailStreak === 'function') {
            freeTextState.setDerailStreak(session, nodeId, value);
        }
    }

    function postIcebreakerJson(path, body) {
        function parseJsonResponse(response) {
            return response.json().catch(function () {
                return null;
            }).then(function (data) {
                if (!response.ok) {
                    throw makeIcebreakerApiError(
                        (data && (data.reason || data.error_code)) || ('HTTP ' + response.status),
                        data
                    );
                }
                return data;
            });
        }

        function postWithHeaders(headers, allowRetry) {
            return fetch(ICEBREAKER_API_BASE + path, {
                method: 'POST',
                headers: headers,
                credentials: 'same-origin',
                body: JSON.stringify(body || {})
            }).then(function (response) {
                if (allowRetry && response.status === 403) {
                    return response.clone().json().catch(function () {
                        return null;
                    }).then(function (errorBody) {
                        if (errorBody && errorBody.error_code === 'csrf_validation_failed') {
                            return refreshLocalMutationHeaders().then(function (nextHeaders) {
                                return postWithHeaders(nextHeaders, false);
                            });
                        }
                        return parseJsonResponse(response);
                    });
                }
                return parseJsonResponse(response);
            });
        }

        return getLocalMutationHeaders().then(function (headers) {
            return postWithHeaders(headers, true);
        });
    }

    function interpretFreeTextWithLlm(session, text, snapshot) {
        var info = snapshot && typeof snapshot === 'object' ? snapshot : {};
        var bodyNodeId = String(info.nodeId || (session && session.nodeId) || '');
        var node = session && session.dayConfig && session.dayConfig.nodes
            ? session.dayConfig.nodes[bodyNodeId]
            : null;
        var localeData = info.localeData || (session && session.localeData) || {};
        var body = {
            lanlan_name: String((session && session.lanlanName) || resolveLanlanName() || ''),
            session_id: String(info.sessionId || (session && session.sessionId) || ''),
            day: String(info.day || (session && session.day) || ''),
            node_id: bodyNodeId,
            i18n_language: currentLocale(),
            assistant_line: getText(localeData, node && node.lineKey),
            options: buildPromptOptions(node, localeData),
            user_text: String(text || ''),
            free_text_derail_streak: getFreeTextDerailStreak(session, bodyNodeId),
            recent_free_text_turns: getRecentFreeTextTurns(session, bodyNodeId),
            request_id: String(info.requestId || '')
        };
        return postIcebreakerJson('/free-text/interpret', body).then(function (data) {
            if (data && data.skipped === 'stale_session') {
                throw makeIcebreakerApiError('stale_session', data);
            }
            if (!data || data.ok !== true) {
                throw makeIcebreakerApiError((data && data.reason) || 'free_text_interpreter_failed', data);
            }
            return normalizeFreeTextInterpretation(data);
        });
    }

    function fallbackFreeTextInterpretation(snapshot) {
        var fallback = (snapshot && snapshot.fallback) || {};
        var localeData = (snapshot && snapshot.localeData) || {};
        return {
            action: 'respond_and_keep_options',
            choice: '',
            reply: getText(localeData, fallback.redirectKey),
            topicState: FREE_TEXT_TOPIC_SOFT_DERAIL
        };
    }

    function setChoicePrompt(node, localeData, revealDelayMs) {
        var prompt = {
            sessionId: activeSession.sessionId,
            gameType: SOURCE,
            options: buildPromptOptions(node, localeData),
            revealDelayMs: revealDelayMs > 0 ? revealDelayMs : 0
        };
        broadcastIcebreakerChoicePrompt(prompt);
        if (!shouldRenderIcebreakerOnLocalChatHost()) {
            return Promise.resolve(false);
        }
        return waitForChatHost(30000).then(function (host) {
            if (!host || typeof host.setIcebreakerChoicePrompt !== 'function') return false;
            host.setIcebreakerChoicePrompt(prompt);
            return true;
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] set choice prompt failed:', error);
            return false;
        });
    }

    function clearChoicePrompt() {
        if (!activeSession || !activeSession.sessionId) return;
        var sessionId = activeSession.sessionId;
        broadcastIcebreakerClearChoicePrompt(sessionId);
        if (!shouldRenderIcebreakerOnLocalChatHost()) return;
        waitForChatHost(1200).then(function (host) {
            if (host && typeof host.clearIcebreakerChoicePrompt === 'function') {
                host.clearIcebreakerChoicePrompt(sessionId);
            }
        }).catch(function () {});
    }

    function isIcebreakerBlockerVisible(el) {
        if (!el || el.hidden) return false;
        var style = null;
        try {
            style = window.getComputedStyle ? window.getComputedStyle(el) : null;
        } catch (_) {}
        if (style && (
            style.display === 'none'
            || style.visibility === 'hidden'
            || style.opacity === '0'
        )) {
            return false;
        }
        var rect = null;
        try {
            rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        } catch (_) {}
        return !rect || rect.width > 0 || rect.height > 0;
    }

    function hasVisibleTutorialBlocker(selectors) {
        try {
            for (var i = 0; i < selectors.length; i += 1) {
                var nodes = document.querySelectorAll(selectors[i]);
                for (var j = 0; j < nodes.length; j += 1) {
                    if (isIcebreakerBlockerVisible(nodes[j])) return true;
                }
            }
        } catch (_) {}
        return false;
    }

    function isDay1SystrayIntroBlockingIcebreaker() {
        try {
            if (document.body && document.body.classList.contains('neko-day1-systray-intro-open')) {
                return true;
            }
        } catch (_) {}
        return hasVisibleTutorialBlocker([
            '#neko-day1-systray-intro-modal',
            '.neko-day1-systray-intro-modal'
        ]);
    }

    function isTutorialBlockingIcebreaker() {
        try {
            if (window.isInTutorial) return true;
        } catch (_) {}
        try {
            var manager = window.universalTutorialManager;
            if (manager && (
                manager.isTutorialRunning
                || manager._teardownPromise
                || manager.activeAvatarFloatingGuideRound
            )) {
                return true;
            }
        } catch (_) {}
        if (isDay1SystrayIntroBlockingIcebreaker()) return true;
        return hasVisibleTutorialBlocker([
            '#neko-tutorial-skip-btn',
            '#home-avatar-floating-guide-player',
            '.home-avatar-floating-guide-player',
            '.yui-guide-overlay',
            '.yui-guide-stage'
        ]);
    }

    function deliverNode(nodeId) {
        if (!activeSession) return Promise.resolve(false);
        var session = activeSession;
        var previousNodeId = session.nodeId;
        var localeData = session.localeData;
        var dayConfig = session.dayConfig;
        var node = dayConfig && dayConfig.nodes ? dayConfig.nodes[nodeId] : null;
        if (!node) return Promise.resolve(false);
        session.nodeId = nodeId;
        var text = getText(localeData, node.lineKey);
        return appendAssistantChatMessage(text, {
            day: session.day,
            nodeId: nodeId,
            voiceKey: node.voiceKey || ''
        }, session).then(function (message) {
            if (activeSession !== session || session.nodeId !== nodeId) return false;
            if (!didAppendChatMessage(message)) {
                if (activeSession === session && session.nodeId === nodeId) {
                    session.nodeId = previousNodeId;
                }
                return false;
            }
            markDay(session.day, {
                started: true,
                completed: false,
                sessionId: session.sessionId,
                nodeId: nodeId,
                updatedAt: Date.now()
            });
            applyAssistantTextEmotion(text);
            speakLine(text, node.voiceKey || '');
            // 立刻下发 choicePrompt 绑定输入路由；按钮可见性由 host 按 revealDelayMs 延后。
            return setChoicePrompt(node, localeData, computeChoicePromptRevealDelay(text)).then(function () {
                return true;
            });
        });
    }

    function completeWithHandoff(option) {
        var session = activeSession;
        if (!session) return Promise.resolve(false);
        var text = getText(session.localeData, option.handoffKey);
        var day = session.day;
        var nodeId = session.nodeId;
        var sessionId = session.sessionId;
        var handoffSpeechPromise = Promise.resolve(false);
        return appendAssistantChatMessage(text, {
            day: day,
            nodeId: nodeId,
            voiceKey: option.handoffVoiceKey || '',
            handoff: true
        }, session).then(function (message) {
            if (!didAppendChatMessage(message)) return false;
            clearChoicePrompt();
            applyAssistantTextEmotion(text);
            handoffSpeechPromise = speakLine(text, option.handoffVoiceKey || '');
            // 关 route 前 await 本 session 全部未决池写入（中间+收尾）：严格后端 route 一关就
            // 拒收，迟到的写入会丢。绝大多数早已 resolve，Promise.all 实际几乎立即完成。
            var pendingWrites = (session.pendingChoiceWrites || []).map(function (p) {
                return Promise.resolve(p).catch(function () {});
            });
            return Promise.all(pendingWrites).then(function () {
                return endIcebreakerRoute(session, 'icebreaker_handoff');
            });
        }).then(function (completed) {
            if (!completed) return false;
            return Promise.resolve(handoffSpeechPromise).catch(function () {}).then(function () {
                return true;
            });
        }).then(function (completed) {
            if (!completed) return false;
            markDay(day, {
                started: true,
                completed: true,
                completedAt: Date.now(),
                sessionId: sessionId,
                nodeId: nodeId
            });
            if (activeSession === session) {
                activeSession = null;
            }
            dispatchIcebreakerEnded('handoff');
            return true;
        });
    }

    function advanceWithChoice(session, option, choice, label, choiceNodeId) {
        if (!session || activeSession !== session || !option) return Promise.resolve(null);
        var isHandoffChoice = !!option.handoffKey;
        setFreeTextDerailStreak(session, choiceNodeId, 0);
        // seq 是 session 内自增步序，让消费侧按点击顺序还原路径，不受 fire-and-forget
        // 写入到达顺序被网络打乱的影响；收尾前 completeWithHandoff 会 await 这些写入。
        var choiceWritePromise = recordChoiceToPool({
            day: session.day,
            sessionId: session.sessionId,
            nodeId: choiceNodeId,
            choice: choice,
            label: label,
            handoff: isHandoffChoice,
            completed: isHandoffChoice,
            seq: (session.choiceSeq = (session.choiceSeq || 0) + 1)
        });
        (session.pendingChoiceWrites || (session.pendingChoiceWrites = [])).push(choiceWritePromise);
        if (option.next) {
            return deliverNode(option.next);
        }
        if (option.handoffKey) {
            return completeWithHandoff(option);
        }
        return Promise.resolve(false);
    }

    function handleChoice(detail) {
        if (!activeSession || !detail) return;
        if (detail.sessionId && detail.sessionId !== activeSession.sessionId) return;
        var session = activeSession;
        if (session.choiceInFlight || session.freeTextInFlight) return;
        var node = session.dayConfig.nodes[session.nodeId];
        if (!node || !Array.isArray(node.options)) return;
        var choice = String(detail.choice || '');
        var option = node.options.find(function (candidate) {
            return String(candidate.id || '') === choice;
        });
        if (!option) return;
        session.choiceInFlight = true;
        clearChoicePrompt();
        var label = (detail.option && detail.option.label) || getText(session.localeData, option.labelKey);
        // 叶子节点的选项带 handoffKey（无 next）即这天的收尾选择；中间节点带 next。
        // 本次选择所属节点：deliverNode 之后 session.nodeId 会被改写，先快照下来。
        var choiceNodeId = session.nodeId;
        appendChatMessage('user', label, {
            day: session.day,
            nodeId: session.nodeId,
            choice: choice
        }).then(function (message) {
            if (!message) {
                if (activeSession === session) {
                    session.choiceInFlight = false;
                    setChoicePrompt(node, session.localeData);
                }
                return null;
            }
            if (activeSession !== session) {
                return null;
            }
            // 仅在用户消息被接受后才写池：appendChatMessage 返回 null（host 渲染超时）会回滚到
            // 原节点，此刻不能留下一条用户可能改选的幻影选项（handoff 还会误标 completed）。
            return advanceWithChoice(session, option, choice, label, choiceNodeId);
        }).then(function (result) {
            if (activeSession === session) {
                session.choiceInFlight = false;
                if (result === false) {
                    setChoicePrompt(node, session.localeData);
                }
            }
            return result;
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] choice handling failed:', error);
            if (activeSession === session) {
                session.choiceInFlight = false;
                setChoicePrompt(node, session.localeData);
            }
        });
    }

    function applyFreeTextInterpretation(session, interpretation, snapshot) {
        if (!session || activeSession !== session) return Promise.resolve(null);
        var info = snapshot && typeof snapshot === 'object' ? snapshot : {};
        var day = String(info.day || session.day || '');
        var nodeId = String(info.nodeId || session.nodeId || '');
        var sessionId = String(info.sessionId || session.sessionId || '');
        var localeData = info.localeData || session.localeData || {};
        var fallback = info.fallback || (session.dayConfig && session.dayConfig.fallback) || {};
        var currentNode = session.dayConfig && session.dayConfig.nodes
            ? session.dayConfig.nodes[nodeId]
            : null;
        var decision = normalizeFreeTextInterpretation(interpretation);

        if (decision.action === 'choose' && currentNode) {
            var option = findOptionByChoice(currentNode, decision.choice);
            if (option) {
                setFreeTextDerailStreak(session, nodeId, 0);
                recordFreeTextTurn(session, {
                    userText: info.userText,
                    action: 'choose',
                    choice: decision.choice,
                    topicState: FREE_TEXT_TOPIC_ON_TOPIC
                }, nodeId);
                clearChoicePrompt();
                session.choiceInFlight = true;
                return advanceWithChoice(session, option, decision.choice, getText(localeData, option.labelKey), nodeId).then(function (result) {
                    if (activeSession === session) {
                        session.choiceInFlight = false;
                        if (result === false) {
                            setChoicePrompt(currentNode, localeData);
                        }
                    }
                    return result;
                }).catch(function (error) {
                    console.warn('[NewUserIcebreaker] interpreted choice failed:', error);
                    if (activeSession === session) {
                        session.choiceInFlight = false;
                        setChoicePrompt(currentNode, localeData);
                    }
                    return null;
                });
            }
            decision = {
                action: 'respond_and_keep_options',
                choice: '',
                reply: decision.reply,
                topicState: decision.topicState
            };
        }

        if (decision.action === 'respond_and_keep_options'
            && decision.topicState === FREE_TEXT_TOPIC_SOFT_DERAIL
            && getFreeTextDerailStreak(session, nodeId) >= 1) {
            decision = {
                action: 'release',
                choice: '',
                reply: '',
                topicState: FREE_TEXT_TOPIC_SOFT_DERAIL
            };
        }

        if (decision.action === 'release') {
            var releaseText = decision.reply || getText(localeData, fallback.releaseKey);
            var releaseVoiceKey = fallback.releaseVoiceKey || '';
            recordFreeTextTurn(session, {
                userText: info.userText,
                action: 'release',
                topicState: decision.topicState,
                reply: releaseText
            }, nodeId);
            var releaseAppend = releaseText ? appendAssistantChatMessage(releaseText, {
                day: day,
                nodeId: nodeId,
                voiceKey: releaseVoiceKey,
                fallback: 'release',
                freeText: true,
                requestId: info.requestId || ''
            }, session).then(function (message) {
                if (!didAppendChatMessage(message)) return false;
                return true;
            }) : Promise.resolve(activeSession === session);
            return releaseAppend.then(function (didAppendRelease) {
                if (!didAppendRelease || activeSession !== session) return false;
                session.releasedByFreeText = true;
                setFreeTextDerailStreak(session, nodeId, 0);
                clearChoicePrompt();
                clearFreeTextRuntimeStateForSession(session);
                return Promise.resolve().then(function () {
                    if (!releaseText) return null;
                    return speakLine(releaseText, releaseVoiceKey);
                }).catch(function () {}).then(function () {
                    if (activeSession !== session) return false;
                    return endIcebreakerRoute(session, 'icebreaker_free_text_release');
                });
            }).then(function (completed) {
                if (!completed) return false;
                markDay(day, {
                    started: true,
                    completed: true,
                    completedAt: Date.now(),
                    sessionId: sessionId,
                    nodeId: nodeId,
                    releasedByFreeText: true
                });
                if (activeSession === session) {
                    activeSession = null;
                }
                dispatchIcebreakerEnded('free_text_release');
                return true;
            });
        }

        var replyText = decision.reply || getText(localeData, fallback.redirectKey);
        if (!replyText) {
            return currentNode ? setChoicePrompt(currentNode, localeData) : Promise.resolve(null);
        }
        recordFreeTextTurn(session, {
            userText: info.userText,
            action: 'respond_and_keep_options',
            topicState: decision.topicState,
            reply: replyText
        }, nodeId);
        return appendAssistantChatMessage(replyText, {
            day: day,
            nodeId: nodeId,
            fallback: 'respond_and_keep_options',
            freeText: true,
            requestId: info.requestId || ''
        }, session).then(function (message) {
            if (!didAppendChatMessage(message)) return null;
            if (decision.topicState === FREE_TEXT_TOPIC_SOFT_DERAIL) {
                setFreeTextDerailStreak(session, nodeId, 1);
            } else {
                setFreeTextDerailStreak(session, nodeId, 0);
            }
            applyAssistantTextEmotion(replyText);
            speakLine(replyText, '');
            if (activeSession !== session) return null;
            currentNode = session.dayConfig && session.dayConfig.nodes
                ? session.dayConfig.nodes[nodeId]
                : null;
            if (!currentNode) return null;
            return setChoicePrompt(currentNode, localeData, computeChoicePromptRevealDelay(replyText));
        });
    }

    function handleFreeText(detail) {
        if (!activeSession || !detail) return;
        var session = activeSession;
        if (detail.sessionId && detail.sessionId !== session.sessionId) return;
        var text = String(detail.text || '').trim();
        if (!text) return;
        if (session.releasedByFreeText) return;
        if (session.freeTextInFlight || session.choiceInFlight) return;
        session.freeTextInFlight = true;
        var day = session.day;
        var nodeId = session.nodeId;
        var sessionId = session.sessionId;
        var localeData = session.localeData;
        var fallback = (session.dayConfig && session.dayConfig.fallback) || {};
        var requestId = detail.requestId || '';

        return appendChatMessage('user', text, {
            day: day,
            nodeId: nodeId,
            freeText: true,
            requestId: requestId
        }).then(function (message) {
            if (!message) {
                if (activeSession === session) {
                    var restoreNode = session.dayConfig && session.dayConfig.nodes
                        ? session.dayConfig.nodes[nodeId]
                        : null;
                    if (restoreNode) setChoicePrompt(restoreNode, localeData);
                }
                return null;
            }
            if (activeSession !== session) {
                return null;
            }
            return interpretFreeTextWithLlm(session, text, {
                day: day,
                nodeId: nodeId,
                sessionId: sessionId,
                localeData: localeData,
                fallback: fallback,
                userText: text,
                requestId: requestId
            }).catch(function (error) {
                if (isIcebreakerRouteInactiveError(error)) {
                    throw error;
                }
                console.warn('[NewUserIcebreaker] free-text interpreter failed:', error);
                return fallbackFreeTextInterpretation({
                    localeData: localeData,
                    fallback: fallback
                });
            }).then(function (interpretation) {
                return applyFreeTextInterpretation(session, interpretation, {
                    day: day,
                    nodeId: nodeId,
                    sessionId: sessionId,
                    localeData: localeData,
                    fallback: fallback,
                    userText: text,
                    requestId: requestId
                });
            });
        }).then(function (result) {
            if (activeSession === session) {
                session.freeTextInFlight = false;
            }
            return result;
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] free-text handling failed:', error);
            if (activeSession === session) {
                session.freeTextInFlight = false;
                if (isIcebreakerRouteInactiveError(error)) {
                    clearChoicePrompt();
                    clearFreeTextRuntimeStateForSession(session);
                    activeSession = null;
                    return null;
                }
                var currentNode = session.dayConfig && session.dayConfig.nodes
                    ? session.dayConfig.nodes[nodeId]
                    : null;
                if (currentNode) setChoicePrompt(currentNode, localeData);
            }
            return null;
        });
    }

    function canStartFromEndState(endState, scripts) {
        if (!endState || endState.ended !== true) return false;
        if (endState.isAngryExit) return false;
        var outcome = String(endState.outcome || endState.rawReason || '');
        if (outcome !== 'complete') return false;
        var endedAt = Number(endState.endedAt || 0);
        if (endedAt && Date.now() - endedAt > TRIGGER_WINDOW_MS) return false;
        var day = String(endState.day || '');
        if (!day || !scripts || !scripts.days || !scripts.days[day]) return false;
        if (isDayCompleted(day)) return false;
        return true;
    }

    function readPersistedAvatarGuideState() {
        var stateApi = window.NekoSevenDayTutorialState || null;
        return stateApi ? stateApi.loadState() : {};
    }

    function resolveRecentPersistedEndState() {
        var state = readPersistedAvatarGuideState();
        if (!state || typeof state !== 'object') return null;
        if (state.lastEndState && typeof state.lastEndState === 'object') {
            var persistedLastEndState = Object.assign({}, state.lastEndState);
            var lastEndedAt = Number(persistedLastEndState.endedAt || 0);
            if (lastEndedAt && Date.now() - lastEndedAt <= PERSISTED_END_WINDOW_MS) {
                return persistedLastEndState;
            }
        }
        var updatedAtMs = Date.parse(String(state.updatedAt || ''));
        if (!Number.isFinite(updatedAtMs) || Date.now() - updatedAtMs > PERSISTED_END_WINDOW_MS) {
            return null;
        }
        var skipped = Array.isArray(state.skippedRounds) ? state.skippedRounds.map(Number) : [];
        var completed = Array.isArray(state.completedRounds) ? state.completedRounds.map(Number) : [];
        var candidates = skipped.concat(completed).filter(function (round) {
            return Number.isInteger(round) && round >= 1 && round <= 7;
        });
        if (!candidates.length) return null;
        var day = Math.max.apply(Math, candidates);
        var skippedDay = skipped.indexOf(day) !== -1;
        return {
            day: day,
            ended: true,
            outcome: skippedDay ? 'skip' : 'complete',
            rawReason: skippedDay ? 'skip' : 'complete',
            isAngryExit: false,
            completed: !skippedDay,
            skipped: skippedDay,
            source: 'persisted_avatar_floating_guide_state',
            endedAt: updatedAtMs
        };
    }

    function startForDay(day, options) {
        var force = !!(options && options.force);
        var dayKey = String(day || '');
        if (!force && pendingStartDay === dayKey) return Promise.resolve(false);
        pendingStartDay = dayKey;
        return Promise.all([loadScripts(), loadLocale(currentLocale())]).then(function (results) {
            if (activeSession && !force) return false;
            var scripts = results[0];
            var localeData = results[1];
            var dayConfig = scripts && scripts.days ? scripts.days[dayKey] : null;
            if (!dayConfig || !dayConfig.root || !dayConfig.nodes) return false;
            if (!force && isDayCompleted(dayKey)) return false;
            var nextSession = {
                day: dayKey,
                dayConfig: dayConfig,
                localeData: localeData || {},
                nodeId: dayConfig.root,
                // 钉死本 session 的角色：后续选项写入用这个快照而非现取，避免中途换角色串味。
                lanlanName: resolveLanlanName(),
                sessionId: 'icebreaker-day' + dayKey + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8)
            };
            return startIcebreakerRoute(nextSession).then(function (started) {
                if (!started) return false;
                activeSession = nextSession;
                clearPendingGuideEndStateDay(dayKey);
                return deliverNode(dayConfig.root).then(function (delivered) {
                    if (delivered) return true;
                    if (activeSession === nextSession) {
                        activeSession = null;
                    }
                    return endIcebreakerRoute(nextSession, 'icebreaker_start_append_failed').then(function () {
                        return false;
                    });
                });
            });
        }).then(function (result) {
            clearPendingStartDay(dayKey);
            return result;
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] start failed:', error);
            clearPendingStartDay(dayKey);
            return false;
        });
    }

    function startFromEndState(endState) {
        return loadScripts().then(function (scripts) {
            if (!canStartFromEndState(endState, scripts)) {
                return false;
            }
            return startForDay(String(endState.day || ''), { force: false });
        });
    }

    function getEndStateTriggerDeadline(endState) {
        var endedAt = Number(endState && endState.endedAt || 0);
        return (endedAt || Date.now()) + TRIGGER_WINDOW_MS;
    }

    function startFromEndStateWhenTutorialIdle(endState) {
        if (!endState) return Promise.resolve(false);
        if (isTutorialBlockingIcebreaker()) {
            // The Day 1 systray intro is a user-controlled modal; keep the guide end state until it closes.
            if (!isDay1SystrayIntroBlockingIcebreaker() && Date.now() >= getEndStateTriggerDeadline(endState)) return Promise.resolve(false);
            return new Promise(function (resolve) {
                window.setTimeout(resolve, TUTORIAL_IDLE_RETRY_MS);
            }).then(function () {
                return startFromEndStateWhenTutorialIdle(endState);
            });
        }
        return Promise.resolve(startFromEndState(endState));
    }

    function attemptStartFromGuideEndState(endState, pendingDay) {
        if (!endState) return Promise.resolve(false);
        var dayKey = String(pendingDay || endState.day || '');
        if (activeSession) return Promise.resolve(true);
        if (!pendingGuideEndState) return Promise.resolve(true);
        if (dayKey && String(pendingGuideEndState.day || '') !== dayKey) return Promise.resolve(true);
        if (pendingGuideEndStartPromise) return pendingGuideEndStartPromise;
        pendingGuideEndStartPromise = startFromEndStateWhenTutorialIdle(endState).then(function (started) {
            if (!started) {
                clearPendingGuideEndStateDay(pendingDay);
                dispatchIcebreakerEnded('start_failed');
            }
            return started;
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] deferred start failed:', error);
            clearPendingGuideEndStateDay(pendingDay);
            dispatchIcebreakerEnded('start_failed');
            return false;
        }).then(function (started) {
            pendingGuideEndStartPromise = null;
            return started;
        });
        return pendingGuideEndStartPromise;
    }

    function synthesizeEndStateFromEvent(eventType, detail) {
        var normalizedDetail = detail && typeof detail === 'object' ? detail : {};
        var day = normalizedDetail.day;
        var outcome = '';
        if (eventType === 'neko:avatar-floating-guide-complete') {
            outcome = 'complete';
        } else if (eventType === 'neko:tutorial-completed' && normalizedDetail.page === 'home') {
            outcome = 'complete';
        }
        if (!day || !outcome) return null;
        var rawReason = String(normalizedDetail.rawReason || normalizedDetail.reason || outcome || '').trim().toLowerCase();
        if (!rawReason) rawReason = outcome;
        return {
            day: day,
            ended: true,
            outcome: outcome,
            rawReason: rawReason,
            isAngryExit: rawReason === 'angry_exit',
            completed: outcome === 'complete',
            skipped: outcome === 'skip',
            source: eventType || 'tutorial_event',
            endedAt: Date.now()
        };
    }

    function resolveLatestEndState(detail, eventType) {
        var normalizedDetail = detail && typeof detail === 'object' ? detail : {};
        return normalizedDetail.endState
            || synthesizeEndStateFromEvent(eventType, normalizedDetail)
            || window.avatarFloatingGuideEndState
            || normalizedDetail;
    }

    function handleGuideEndEvent(event) {
        var detail = event && event.detail ? event.detail : {};
        var eventType = event && event.type ? String(event.type) : '';
        var endState = resolveLatestEndState(detail, eventType);
        if (
            !endState
            || endState.isAngryExit === true
            || String(endState.outcome || endState.rawReason || '') !== 'complete'
        ) {
            return;
        }
        var pendingDay = markPendingStartFromEndState(endState);
        window.setTimeout(function () {
            attemptStartFromGuideEndState(endState, pendingDay);
        }, 500);
    }

    function bootstrapFromRecentEndState() {
        // Cold starts must wait for an explicit tutorial end event; persisted
        // guide history can otherwise steal the first tutorial before it opens.
        return false;
    }

    window.addEventListener('neko:avatar-floating-guide-complete', handleGuideEndEvent);
    window.addEventListener('neko:tutorial-completed', handleGuideEndEvent);
    window.addEventListener('neko:day1-systray-intro-closed', function () {
        if (!pendingGuideEndState) return;
        attemptStartFromGuideEndState(pendingGuideEndState, String(pendingGuideEndState.day || ''));
    });
    window.addEventListener('pagehide', function () {
        endIcebreakerRouteOnPageExit('icebreaker_pagehide');
    });
    window.addEventListener('beforeunload', function () {
        endIcebreakerRouteOnPageExit('icebreaker_beforeunload');
    });
    window.addEventListener('unload', function () {
        endIcebreakerRouteOnPageExit('icebreaker_unload');
    });
    window.addEventListener('neko:icebreaker-choice-selected', function (event) {
        handleChoice(event && event.detail);
    });
    window.addEventListener('neko:icebreaker-free-text-submitted', function (event) {
        handleFreeText(event && event.detail);
    });
    window.addEventListener('neko:new-user-icebreaker-reset', function () {
        broadcastIcebreakerClearChoicePromptSource(SOURCE, 'new-user-icebreaker-reset');
    });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootstrapFromRecentEndState, { once: true });
    } else {
        bootstrapFromRecentEndState();
    }

    window.newUserIcebreaker = {
        start: function (day) {
            return startForDay(day || 1, { force: true });
        },
        startFromEndState: startFromEndState,
        speak: speakViaProjectTts,
        getActiveSession: function () {
            return activeSession ? Object.assign({}, activeSession) : null;
        }
    };

    window.NekoNewUserIcebreakerState = {
        readStore: readStore,
        hasCompletedDay: isDayCompleted,
        isPeriodActive: isPeriodActive
    };
})();
