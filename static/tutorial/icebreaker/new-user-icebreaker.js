(function () {
    'use strict';

    var SOURCE = 'new_user_icebreaker';
    var GAME_TYPE = 'new_user_icebreaker';
    var STORAGE_KEY = 'neko.new_user_icebreaker.v1';
    var AVATAR_FLOATING_GUIDE_STORAGE_KEY = 'neko_avatar_floating_guide_v1';
    var ICEBREAKER_BRIDGE_STORAGE_KEY = 'neko_new_user_icebreaker_bridge_event';
    var SCRIPT_URL = '/static/tutorial/icebreaker/icebreaker_scripts.json';
    var LOCALE_BASE_URL = '/static/tutorial/icebreaker/locales/';
    var TRIGGER_WINDOW_MS = 2 * 60 * 1000;
    var PERSISTED_END_WINDOW_MS = 15 * 60 * 1000;
    var TUTORIAL_IDLE_RETRY_MS = 500;
    var activeSession = null;
    var pendingStartDay = '';
    var scriptPromise = null;
    var localePromises = Object.create(null);
    var icebreakerSortKeySeq = 0;
    var icebreakerBridgeTimestampSeq = 0;
    var contextAppendPromise = Promise.resolve();

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
        return !!activeSession;
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
            return fetch('/api/game/' + encodeURIComponent(GAME_TYPE) + path, {
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

    function endIcebreakerRoute(session, reason) {
        if (!session || session.routeEnded) return Promise.resolve(false);
        session.routeEnded = true;
        return postIcebreakerRoute('/route/end', session, {
            reason: reason || 'icebreaker_complete',
            postgameProactive: { enabled: false }
        });
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
            return fetch('/api/game/' + encodeURIComponent(GAME_TYPE) + '/context', {
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
                return message;
            }
            return waitForChatHost(30000).then(function (host) {
                if (typeof host.openWindow === 'function') {
                    host.openWindow();
                }
                return host.appendMessage(message);
            });
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] append message failed:', error);
            return null;
        });
    }

    function speakViaProjectTts(text, voiceKey) {
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
        return fetch('/api/game/' + encodeURIComponent(GAME_TYPE) + '/speak', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify(body)
        }).then(function (response) {
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return response.json();
        }).then(function (data) {
            return !!(data && data.ok);
        }).catch(function (error) {
            console.warn('[NewUserIcebreaker] project TTS failed:', error);
            return false;
        });
    }

    function speakLine(text, voiceKey) {
        speakViaProjectTts(text, voiceKey).then(function (ok) {
            if (ok) return;
            return new Promise(function (resolve) {
                window.setTimeout(resolve, estimateSpeechDurationMs(text));
            });
        });
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

    function setChoicePrompt(node, localeData) {
        var prompt = {
            sessionId: activeSession.sessionId,
            gameType: GAME_TYPE,
            options: buildPromptOptions(node, localeData)
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
        return hasVisibleTutorialBlocker([
            '#neko-tutorial-skip-btn',
            '#home-avatar-floating-guide-player',
            '.home-avatar-floating-guide-player',
            '.yui-guide-overlay',
            '.yui-guide-stage'
        ]);
    }

    function deliverNode(nodeId) {
        if (!activeSession) return Promise.resolve();
        var dayConfig = activeSession.dayConfig;
        var node = dayConfig && dayConfig.nodes ? dayConfig.nodes[nodeId] : null;
        if (!node) return Promise.resolve();
        activeSession.nodeId = nodeId;
        markDay(activeSession.day, {
            started: true,
            completed: false,
            sessionId: activeSession.sessionId,
            nodeId: nodeId,
            updatedAt: Date.now()
        });
        var text = getText(activeSession.localeData, node.lineKey);
        applyAssistantTextEmotion(text);
        return appendChatMessage('assistant', text, {
            day: activeSession.day,
            nodeId: nodeId,
            voiceKey: node.voiceKey || ''
        }).then(function () {
            speakLine(text, node.voiceKey || '');
            return setChoicePrompt(node, activeSession.localeData);
        });
    }

    function completeWithHandoff(option) {
        var session = activeSession;
        if (!session) return Promise.resolve(false);
        var text = getText(session.localeData, option.handoffKey);
        var day = session.day;
        var nodeId = session.nodeId;
        var sessionId = session.sessionId;
        applyAssistantTextEmotion(text);
        clearChoicePrompt();
        return appendChatMessage('assistant', text, {
            day: day,
            nodeId: nodeId,
            voiceKey: option.handoffVoiceKey || '',
            handoff: true
        }).then(function () {
            speakLine(text, option.handoffVoiceKey || '');
            markDay(day, {
                started: true,
                completed: true,
                completedAt: Date.now(),
                sessionId: sessionId,
                nodeId: nodeId
            });
            return endIcebreakerRoute(session, 'icebreaker_handoff');
        }).then(function () {
            if (activeSession === session) {
                activeSession = null;
            }
            return true;
        });
    }

    function handleChoice(detail) {
        if (!activeSession || !detail) return;
        if (detail.sessionId && detail.sessionId !== activeSession.sessionId) return;
        var session = activeSession;
        if (session.choiceInFlight) return;
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
            if (option.next) {
                return deliverNode(option.next);
            }
            if (option.handoffKey) {
                return completeWithHandoff(option);
            }
            return null;
        }).then(function (result) {
            if (activeSession === session) {
                session.choiceInFlight = false;
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

    function handleFreeText(detail) {
        if (!activeSession || !detail) return;
        var session = activeSession;
        if (detail.sessionId && detail.sessionId !== session.sessionId) return;
        var text = String(detail.text || '').trim();
        if (!text) return;
        if (session.releasedByFreeText) return;
        var day = session.day;
        var nodeId = session.nodeId;
        var sessionId = session.sessionId;
        var localeData = session.localeData;
        var fallback = (session.dayConfig && session.dayConfig.fallback) || {};
        var isRelease = Number(session.offTopicCount || 0) >= 1;
        session.offTopicCount = Number(session.offTopicCount || 0) + 1;
        if (isRelease) {
            session.releasedByFreeText = true;
            clearChoicePrompt();
        }

        appendChatMessage('user', text, {
            day: day,
            nodeId: nodeId,
            freeText: true,
            requestId: detail.requestId || ''
        }).then(function () {
            var fallbackKey = isRelease ? fallback.releaseKey : fallback.redirectKey;
            var voiceKey = isRelease ? fallback.releaseVoiceKey : fallback.redirectVoiceKey;
            var fallbackText = getText(localeData, fallbackKey);
            if (!fallbackText) return null;
            applyAssistantTextEmotion(fallbackText);
            return appendChatMessage('assistant', fallbackText, {
                day: day,
                nodeId: nodeId,
                voiceKey: voiceKey || '',
                fallback: isRelease ? 'release' : 'redirect'
            }).then(function () {
                speakLine(fallbackText, voiceKey || '');
                if (isRelease) {
                    markDay(day, {
                        started: true,
                        completed: true,
                        completedAt: Date.now(),
                        sessionId: sessionId,
                        nodeId: nodeId,
                        releasedByFreeText: true
                    });
                    endIcebreakerRoute(session, 'icebreaker_free_text_release');
                    if (activeSession === session) {
                        activeSession = null;
                    }
                } else if (activeSession === session) {
                    var currentNode = session.dayConfig && session.dayConfig.nodes
                        ? session.dayConfig.nodes[nodeId]
                        : null;
                    if (currentNode) {
                        setChoicePrompt(currentNode, localeData);
                    }
                }
                return null;
            });
        });
    }

    function canStartFromEndState(endState, scripts) {
        if (!endState || endState.ended !== true) return false;
        if (endState.isAngryExit) return false;
        var outcome = String(endState.outcome || endState.rawReason || '');
        if (outcome === 'destroy') return false;
        if (outcome && outcome !== 'complete' && outcome !== 'skip') return false;
        var endedAt = Number(endState.endedAt || 0);
        if (endedAt && Date.now() - endedAt > TRIGGER_WINDOW_MS) return false;
        var day = String(endState.day || '');
        if (!day || !scripts || !scripts.days || !scripts.days[day]) return false;
        if (isDayCompleted(day)) return false;
        return true;
    }

    function readPersistedAvatarGuideState() {
        try {
            return safeJsonParse(window.localStorage.getItem(AVATAR_FLOATING_GUIDE_STORAGE_KEY), {});
        } catch (_) {
            return {};
        }
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
                offTopicCount: 0,
                sessionId: 'icebreaker-day' + dayKey + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8)
            };
            return startIcebreakerRoute(nextSession).then(function (started) {
                if (!started) return false;
                activeSession = nextSession;
                markDay(dayKey, {
                    started: true,
                    completed: false,
                    triggeredAt: Date.now(),
                    sessionId: activeSession.sessionId,
                    nodeId: dayConfig.root
                });
                return deliverNode(dayConfig.root).then(function () {
                    return true;
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
            if (Date.now() >= getEndStateTriggerDeadline(endState)) return Promise.resolve(false);
            return new Promise(function (resolve) {
                window.setTimeout(resolve, TUTORIAL_IDLE_RETRY_MS);
            }).then(function () {
                return startFromEndStateWhenTutorialIdle(endState);
            });
        }
        return Promise.resolve(startFromEndState(endState));
    }

    function synthesizeEndStateFromEvent(eventType, detail) {
        var normalizedDetail = detail && typeof detail === 'object' ? detail : {};
        var day = normalizedDetail.day;
        var outcome = '';
        if (eventType === 'neko:avatar-floating-guide-skip') {
            outcome = 'skip';
        } else if (eventType === 'neko:avatar-floating-guide-complete') {
            outcome = 'complete';
        } else if (eventType === 'neko:tutorial-skipped' && normalizedDetail.page === 'home') {
            outcome = 'skip';
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
        window.setTimeout(function () {
            startFromEndStateWhenTutorialIdle(resolveLatestEndState(detail, eventType));
        }, 500);
    }

    function bootstrapFromRecentEndState() {
        // Cold starts must wait for an explicit tutorial end event; persisted
        // guide history can otherwise steal the first tutorial before it opens.
        return false;
    }

    window.addEventListener('neko:avatar-floating-guide-complete', handleGuideEndEvent);
    window.addEventListener('neko:avatar-floating-guide-skip', handleGuideEndEvent);
    window.addEventListener('neko:tutorial-completed', handleGuideEndEvent);
    window.addEventListener('neko:tutorial-skipped', handleGuideEndEvent);
    window.addEventListener('neko:icebreaker-choice-selected', function (event) {
        handleChoice(event && event.detail);
    });
    window.addEventListener('neko:icebreaker-free-text-submitted', function (event) {
        handleFreeText(event && event.detail);
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
