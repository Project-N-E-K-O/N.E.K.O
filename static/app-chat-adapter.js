/**
 * app-chat-adapter.js — React-first chat adapter
 *
 * Loaded AFTER app-chat.js. Overrides window.appendMessage,
 * window.createGeminiBubble, window.processRealisticQueue, and
 * window.appChat.appendReactUserMessage so that all message
 * rendering goes directly to the React chat host API, bypassing
 * the old DOM bubble system.
 *
 * The original app-chat.js remains loaded as reference — its code
 * is simply shadowed by the overrides here.
 */
(function () {
    'use strict';

    // ======================== 标记 adapter 激活 ========================

    window._chatAdapterActive = true;

    // ======================== 依赖 ========================

    var S = window.appState;

    function getHost() {
        return window.reactChatWindowHost || null;
    }

    // ======================== 工具函数（从 app-chat.js 私有函数复刻） ========================

    var _reactMessageSeq = 0;

    function getCurrentTimeString() {
        return window.getCurrentTimeString
            ? window.getCurrentTimeString()
            : new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function sanitizeDisplayName(value) {
        if (value == null) return '';
        return String(value).trim();
    }

    function getCurrentAssistantName() {
        return sanitizeDisplayName(
            (window.lanlan_config && window.lanlan_config.lanlan_name)
            || window._currentCatgirl
            || window.currentCatgirl
        ) || 'Neko';
    }

    function getCurrentUserName() {
        var candidates = [
            window.master_display_name,
            window.lanlan_config && window.lanlan_config.master_display_name,
            window.master_nickname,
            window.lanlan_config && window.lanlan_config.master_nickname,
            window.master_name,
            window.lanlan_config && window.lanlan_config.master_name,
            window.currentUser && (window.currentUser.nickname || window.currentUser.display_name || window.currentUser.displayName || window.currentUser.username || window.currentUser.name),
            window.userProfile && (window.userProfile.nickname || window.userProfile.display_name || window.userProfile.displayName || window.userProfile.username || window.userProfile.name),
            window.appUser && (window.appUser.nickname || window.appUser.display_name || window.appUser.displayName || window.appUser.username || window.appUser.name),
            window.username, window.userName, window.displayName, window.nickname
        ];
        for (var i = 0; i < candidates.length; i++) {
            var r = sanitizeDisplayName(candidates[i]);
            if (r) return r;
        }
        try {
            var keys = ['nickname', 'displayName', 'userName', 'username'];
            for (var j = 0; j < keys.length; j++) {
                var s = sanitizeDisplayName(localStorage.getItem(keys[j]));
                if (s) return s;
            }
        } catch (_) {}
        return 'You';
    }

    function getRoleDisplayName(role, fallbackAuthor) {
        var r = sanitizeDisplayName(fallbackAuthor);
        if (r) return r;
        return role === 'user' ? getCurrentUserName() : getCurrentAssistantName();
    }

    function getAssistantAvatarUrl() {
        if (!window.appChatAvatar || typeof window.appChatAvatar.getCurrentAvatarDataUrl !== 'function') return '';
        return window.appChatAvatar.getCurrentAvatarDataUrl() || '';
    }

    function nextReactMessageId(prefix) {
        _reactMessageSeq += 1;
        return (prefix || 'msg') + '-' + Date.now() + '-' + _reactMessageSeq;
    }

    function normalizeGeminiText(s) {
        return (s || '').replace(/\r\n/g, '\n');
    }

    // ======================== 虚拟引用（兼容 response_discarded 清理逻辑） ========================

    function createVirtualBubbleRef(messageId) {
        return {
            dataset: { reactChatMessageId: messageId },
            parentNode: null,
            isConnected: true,
            textContent: '',
            nodeType: 1
        };
    }

    // ======================== React 消息构建 ========================

    function buildMessage(id, role, author, timeStr, text, status) {
        var cleanText = String(text || '')
            .replace(/^\[\d{2}:\d{2}:\d{2}\]\s+[\u{1F380}\u{1F4AC}]\s*/u, '')
            .replace(/\[play_music:[^\]]*(\]|$)/g, '')
            .trim();
        if (!cleanText) return null;

        var avatarUrl = role === 'assistant' ? getAssistantAvatarUrl() : '';
        var resolvedAuthor = getRoleDisplayName(role, author);

        return {
            id: id,
            role: role,
            author: resolvedAuthor,
            time: timeStr || getCurrentTimeString(),
            createdAt: Date.now(),
            avatarLabel: resolvedAuthor ? String(resolvedAuthor).trim().slice(0, 1).toUpperCase() : undefined,
            avatarUrl: avatarUrl || undefined,
            blocks: [{ type: 'text', text: cleanText }],
            status: status
        };
    }

    // ======================== 句子分割（从 app-chat.js 复刻） ========================

    function splitIntoSentences(buffer) {
        var sentences = [];
        var s = normalizeGeminiText(buffer);
        var start = 0;

        function isPunctForBoundary(ch) {
            return ch === '\u3002' || ch === '\uFF01' || ch === '\uFF1F' || ch === '!' || ch === '?' || ch === '.' || ch === '\u2026';
        }

        function isBoundary(ch, next) {
            if (ch === '\n') return true;
            if (isPunctForBoundary(ch) && next && isPunctForBoundary(next)) return false;
            if (isPunctForBoundary(ch) && !next) return false;
            if (ch === '\u3002' || ch === '\uFF01' || ch === '\uFF1F') return true;
            if (ch === '!' || ch === '?') return true;
            if (ch === '\u2026') return true;
            if (ch === '.') {
                if (!next) return true;
                return /\s|\n|["')\]]/.test(next);
            }
            return false;
        }

        for (var i = 0; i < s.length; i++) {
            var ch = s[i];
            var next = i + 1 < s.length ? s[i + 1] : '';
            if (isBoundary(ch, next)) {
                var piece = s.slice(start, i + 1).replace(/^\s+/, '').replace(/\s+$/, '');
                if (piece) sentences.push(piece);
                start = i + 1;
            }
        }

        var rest = s.slice(start);
        return { sentences: sentences, rest: rest };
    }

    // ======================== 合并模式检测 ========================

    function isMergeMessagesEnabled() {
        if (typeof window.mergeMessagesEnabled !== 'undefined') return window.mergeMessagesEnabled;
        return S && S.mergeMessagesEnabled;
    }

    // ======================== 结构化文本检测（从 app-chat.js 复刻） ========================

    function looksLikeStructuredRichText(text) {
        var s = normalizeGeminiText(text || '');
        return /```[\s\S]*```/.test(s)
            || /(?:^|\n)```/.test(s)
            || /\$\$[\s\S]*\$\$/.test(s)
            || /(?<!\$)\$(?!\$)[^$\n]+(?<!\$)\$(?!\$)/.test(s)
            || /(?:^|\n)\s{0,3}(?:#{1,6}\s|[-*+]\s|\d+\.\s|>\s)/.test(s)
            || /(?:^|\n)\|.+\|.+(?:\n|\r\n)\|(?:[-: ]+\|){1,}/.test(s);
    }

    // ======================== 音乐指令清洗（从 app-chat.js 复刻） ========================

    function cleanMusicFromChunk(rawText) {
        var s = normalizeGeminiText(rawText);
        if (window._pendingMusicCommand) {
            s = window._pendingMusicCommand + s;
            window._pendingMusicCommand = '';
        }
        var m = s.match(/\[[^\]]*$/);
        if (m) {
            var partial = m[0].toLowerCase();
            var target = '[play_music:';
            if (partial.startsWith(target) || target.startsWith(partial)) {
                window._pendingMusicCommand = m[0];
                s = s.slice(0, m.index);
            }
        }
        return s.replace(/\[play_music:[^\]]*(\]|$)/g, '');
    }

    // ======================== createGeminiBubble（覆盖） ========================

    // ---- host 未就绪时的待重发队列 ----
    var _pendingHostMessages = [];
    var _pendingFlushTimer = null;

    function _tryFlushPendingHostMessages() {
        var host = getHost();
        if (_pendingHostMessages.length === 0) {
            // 队列已空，停止重试
            if (_pendingFlushTimer) { clearInterval(_pendingFlushTimer); _pendingFlushTimer = null; }
            return;
        }
        if (!host || typeof host.appendMessage !== 'function') {
            // host 尚未就绪，启动轮询重试（200ms 间隔，最多重试 50 次 ≈ 10s）
            if (!_pendingFlushTimer) {
                var _retryCount = 0;
                _pendingFlushTimer = setInterval(function () {
                    _retryCount++;
                    if (_retryCount > 50 || _pendingHostMessages.length === 0) {
                        clearInterval(_pendingFlushTimer); _pendingFlushTimer = null;
                        return;
                    }
                    _tryFlushPendingHostMessages();
                }, 200);
            }
            return;
        }
        // host 就绪，flush 全部并停止重试
        if (_pendingFlushTimer) { clearInterval(_pendingFlushTimer); _pendingFlushTimer = null; }
        var batch = _pendingHostMessages.splice(0);
        for (var i = 0; i < batch.length; i++) {
            try { host.appendMessage(batch[i]); } catch (_) {}
        }
    }

    // 供 response_discarded 等外部逻辑按 msgId 清理待发队列
    function _clearPendingHostMessagesByIds(idsToRemove) {
        if (!idsToRemove || idsToRemove.length === 0 || _pendingHostMessages.length === 0) return;
        var idSet = {};
        for (var i = 0; i < idsToRemove.length; i++) { idSet[idsToRemove[i]] = true; }
        _pendingHostMessages = _pendingHostMessages.filter(function (m) {
            return !(m && m.id && idSet[m.id]);
        });
    }

    function createGeminiBubble(sentence) {
        var host = getHost();
        var cleanSentence = (sentence || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
        var msgId = nextReactMessageId('assistant');
        var timeStr = getCurrentTimeString();
        var msg = buildMessage(msgId, 'assistant', getCurrentAssistantName(), timeStr, cleanSentence, 'streaming');

        if (msg && host && typeof host.appendMessage === 'function') {
            // host 就绪，先重放待发队列，再追加新消息
            _tryFlushPendingHostMessages();
            host.appendMessage(msg);
        } else if (msg) {
            // host 尚未初始化，放入待重发队列而非静默丢弃
            console.warn('[ChatAdapter] host not ready, queuing message', msgId);
            _pendingHostMessages.push(msg);
        }

        var ref = createVirtualBubbleRef(msgId);
        ref.textContent = '[' + timeStr + '] \u{1F380} ' + cleanSentence;
        ref._stableTime = timeStr;

        window.currentGeminiMessage = ref;
        window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
        window.currentTurnGeminiBubbles.push(ref);

        if (typeof window.ensureAssistantTurnStarted === 'function') {
            window.ensureAssistantTurnStarted('create_gemini_bubble');
        }

        return ref;
    }

    // ======================== processRealisticQueue（覆盖） ========================

    async function processRealisticQueue(queueVersion) {
        queueVersion = queueVersion || (window._realisticGeminiVersion || 0);
        if (window._isProcessingRealisticQueue) return;
        window._isProcessingRealisticQueue = true;

        try {
            while (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                // 版本变更说明新一轮已开始（isNewMessage），旧队列
                // 已由 _flushPendingRealisticQueue 同步渲染完毕，此处
                // 仅需退出，不再处理任何剩余项。
                if ((window._realisticGeminiVersion || 0) !== queueVersion) {
                    console.warn('[RealisticQueue] version mismatch (got %d, current %d), exiting loop',
                        queueVersion, window._realisticGeminiVersion || 0);
                    break;
                }

                var now = Date.now();
                var timeSinceLastBubble = now - (window._lastBubbleTime || 0);
                if (window._lastBubbleTime > 0 && timeSinceLastBubble < 2000) {
                    await new Promise(function (resolve) { setTimeout(resolve, 2000 - timeSinceLastBubble); });
                }

                if ((window._realisticGeminiVersion || 0) !== queueVersion) {
                    console.warn('[RealisticQueue] version changed during sleep (got %d, current %d), exiting',
                        queueVersion, window._realisticGeminiVersion || 0);
                    break;
                }

                var s = window._realisticGeminiQueue.shift();
                if (s && (window._realisticGeminiVersion || 0) === queueVersion) {
                    createGeminiBubble(s);
                    window._lastBubbleTime = Date.now();
                }
            }
        } finally {
            window._isProcessingRealisticQueue = false;
            if (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                processRealisticQueue(window._realisticGeminiVersion || 0);
            }
        }
    }

    // ======================== renderStructuredGeminiMessage（React 版） ========================

    function renderStructuredGeminiMessage(fullText) {
        var host = getHost();
        if (!host) return;

        var cleanFullText = normalizeGeminiText(fullText).replace(/\[play_music:[^\]]*(\]|$)/g, '').trim();
        if (!cleanFullText) return;

        // 收拢本轮旧气泡（保留最后一个用于升级）
        if (window.currentTurnGeminiBubbles && window.currentTurnGeminiBubbles.length > 1) {
            var oldBubbles = window.currentTurnGeminiBubbles.slice(0, -1);
            for (var bi = 0; bi < oldBubbles.length; bi++) {
                var oldBubble = oldBubbles[bi];
                if (typeof host.removeMessage === 'function' && oldBubble.dataset && oldBubble.dataset.reactChatMessageId) {
                    host.removeMessage(oldBubble.dataset.reactChatMessageId);
                }
            }
            window.currentTurnGeminiBubbles = [window.currentTurnGeminiBubbles[window.currentTurnGeminiBubbles.length - 1]];
        }

        // 无现有气泡 → 创建新的
        if (!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0 ||
            !window.currentGeminiMessage) {
            var msgId = nextReactMessageId('assistant');
            var timeStr = getCurrentTimeString();
            var msg = buildMessage(msgId, 'assistant', getCurrentAssistantName(), timeStr, cleanFullText, 'streaming');
            if (msg) host.appendMessage(msg);

            var ref = createVirtualBubbleRef(msgId);
            ref._stableTime = timeStr;
            window.currentGeminiMessage = ref;
            window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
            window.currentTurnGeminiBubbles.push(ref);
            return;
        }

        // 升级现有气泡
        var existing = window.currentGeminiMessage;
        var msgIdExisting = existing.dataset.reactChatMessageId;
        var stableTime = existing._stableTime || getCurrentTimeString();
        var updatedMsg = buildMessage(msgIdExisting, 'assistant', getCurrentAssistantName(), stableTime, cleanFullText, 'streaming');
        if (updatedMsg) {
            host.updateMessage(msgIdExisting, {
                author: updatedMsg.author,
                time: updatedMsg.time,
                avatarUrl: updatedMsg.avatarUrl,
                blocks: updatedMsg.blocks,
                status: 'streaming'
            });
        }
    }

    // ======================== _flushPendingRealisticQueue（新增辅助函数） ========================
    // 同步渲染 realistic 队列中所有待处理句子，并使正在运行的
    // async processRealisticQueue 循环失效。
    // 用于 isNewMessage 开始新一轮或模式切换时，确保旧轮句子不被丢弃。
    function _flushPendingRealisticQueue() {
        var queue = window._realisticGeminiQueue;
        if (!Array.isArray(queue) || queue.length === 0) return;
        // 同步创建所有待排队的 bubble
        for (var i = 0; i < queue.length; i++) {
            try { createGeminiBubble(queue[i]); } catch (_) {}
        }
        window._realisticGeminiQueue = [];
        // 重置并发锁，让下次 processRealisticQueue 可以正常启动
        window._isProcessingRealisticQueue = false;
    }

    // ======================== appendMessage（覆盖核心） ========================

    function appendMessage(text, sender, isNewMessage, options) {
        if (typeof isNewMessage === 'undefined') isNewMessage = true;
        options = options || {};

        var host = getHost();
        var bubbleCountBefore = window.currentTurnGeminiBubbles ? window.currentTurnGeminiBubbles.length : 0;
        var createdVisibleBubble = false;

        // 维护"本轮 AI 回复"的完整文本（emotion analysis / subtitle 需要）
        if (sender === 'gemini') {
            if (isNewMessage) {
                // 修复：新一轮开始前，同步渲染旧队列中所有待处理句子，
                // 防止 processRealisticQueue 的 async 循环因 version 变更而
                // 静默丢弃队列中剩余的句子（语音打断时的高频触发场景）。
                _flushPendingRealisticQueue();
                window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
                window._geminiTurnFullText = '';
                window._pendingMusicCommand = '';
                window._structuredGeminiStreaming = false;
                window.currentTurnGeminiBubbles = [];
                window.currentTurnGeminiAttachments = [];
            }
            var prevFull = typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '';
            window._geminiTurnFullText = prevFull + normalizeGeminiText(text);
        }

        // ---------- gemini + realistic 模式 ----------
        if (sender === 'gemini' && !isMergeMessagesEnabled()) {
            if (isNewMessage) {
                window._realisticGeminiBuffer = '';
                window._realisticGeminiQueue = [];
                window._lastBubbleTime = 0;
                window._pendingMusicCommand = '';
            }

            var incoming = normalizeGeminiText(text);

            // 未闭合的音乐指令片段
            if (window._pendingMusicCommand) {
                incoming = window._pendingMusicCommand + incoming;
                window._pendingMusicCommand = '';
            }
            var openBracketMatch = incoming.match(/\[[^\]]*$/);
            if (openBracketMatch) {
                var partialText = openBracketMatch[0];
                var normalizedPartial = normalizeGeminiText(partialText).toLowerCase();
                var targetPrefix = '[play_music:';
                if (normalizedPartial.startsWith(targetPrefix) || targetPrefix.startsWith(normalizedPartial)) {
                    window._pendingMusicCommand = partialText;
                    incoming = incoming.slice(0, openBracketMatch.index);
                }
            }

            var prev = typeof window._realisticGeminiBuffer === 'string' ? window._realisticGeminiBuffer : '';
            var combined = prev + incoming;
            combined = combined.replace(/\[play_music:[^\]]*(\]|$)/g, '');

            var fullTurnText = (typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '')
                .replace(/\[play_music:[^\]]*(\]|$)/g, '');

            // structured text detection
            if (looksLikeStructuredRichText(fullTurnText) || looksLikeStructuredRichText(combined)) {
                window._structuredGeminiStreaming = true;
                window._realisticGeminiBuffer = combined;
                window._realisticGeminiQueue = [];
                renderStructuredGeminiMessage(fullTurnText || combined);
                return;
            }

            var splitResult = splitIntoSentences(combined);
            window._realisticGeminiBuffer = splitResult.rest;

            if (splitResult.sentences.length > 0) {
                window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                window._realisticGeminiQueue.push.apply(window._realisticGeminiQueue, splitResult.sentences);
                processRealisticQueue(window._realisticGeminiVersion || 0);
                createdVisibleBubble = (window.currentTurnGeminiBubbles ? window.currentTurnGeminiBubbles.length : 0) > bubbleCountBefore;
            }

        // ---------- gemini + merge 模式 + 新轮 ----------
        } else if (sender === 'gemini' && isMergeMessagesEnabled() && isNewMessage) {
            window._realisticGeminiBuffer = '';
            window._realisticGeminiQueue = [];
            window._lastBubbleTime = 0;

            var cleanNewText = cleanMusicFromChunk(text);

            if (cleanNewText.trim() && host) {
                var msgId = nextReactMessageId('assistant');
                var timeStr = getCurrentTimeString();
                var msg = buildMessage(msgId, 'assistant', getCurrentAssistantName(), timeStr, cleanNewText, 'streaming');
                if (msg) host.appendMessage(msg);

                var ref = createVirtualBubbleRef(msgId);
                ref._stableTime = timeStr;
                window.currentGeminiMessage = ref;
                window.currentTurnGeminiBubbles.push(ref);
                createdVisibleBubble = true;
            } else {
                window.currentGeminiMessage = null;
            }

        // ---------- gemini + merge 模式 + 续写 ----------
        } else if (sender === 'gemini' && isMergeMessagesEnabled()) {
            var cleanText = cleanMusicFromChunk(text);

            // 场景 A: 本轮尚无气泡
            if (!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0) {
                if (cleanText.trim() && host) {
                    var newId = nextReactMessageId('assistant');
                    var newTime = getCurrentTimeString();
                    var newMsg = buildMessage(newId, 'assistant', getCurrentAssistantName(), newTime, cleanText, 'streaming');
                    if (newMsg) host.appendMessage(newMsg);

                    var newRef = createVirtualBubbleRef(newId);
                    newRef._stableTime = newTime;
                    window.currentGeminiMessage = newRef;
                    window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
                    window.currentTurnGeminiBubbles.push(newRef);
                    createdVisibleBubble = true;
                } else {
                    window.currentGeminiMessage = null;
                }
            }
            // 场景 B: 气泡已存在，追加更新
            else if (window.currentGeminiMessage && host) {
                var fullMergeText = (window._geminiTurnFullText || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
                var existingId = window.currentGeminiMessage.dataset.reactChatMessageId;
                var stableTime = window.currentGeminiMessage._stableTime || getCurrentTimeString();
                var updatedMsg = buildMessage(existingId, 'assistant', getCurrentAssistantName(), stableTime, fullMergeText, 'streaming');
                if (updatedMsg) {
                    host.updateMessage(existingId, {
                        author: updatedMsg.author,
                        time: updatedMsg.time,
                        avatarUrl: updatedMsg.avatarUrl,
                        blocks: updatedMsg.blocks,
                        status: 'streaming'
                    });
                }
            }

        // ---------- user 消息 / 其他 ----------
        } else {
            if (!options.skipReactSync && host) {
                var role = sender === 'user' ? 'user' : 'assistant';
                var author = sender === 'user' ? getCurrentUserName() : getCurrentAssistantName();
                var userId = nextReactMessageId(role);
                var cleanedText = (text || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
                var userMsg = buildMessage(userId, role, author, getCurrentTimeString(), cleanedText, 'sent');
                if (userMsg) host.appendMessage(userMsg);
            }

            if (sender === 'gemini') {
                var gemRef = createVirtualBubbleRef(nextReactMessageId('assistant'));
                window.currentGeminiMessage = gemRef;
                window.currentTurnGeminiBubbles.push(gemRef);
                createdVisibleBubble = true;
            }
        }

        return createdVisibleBubble;
    }

    // ======================== setReactMessageStatus（覆盖） ========================

    function setReactMessageStatus(element, role, status) {
        var host = getHost();
        if (!host || typeof host.updateMessage !== 'function') return;
        var messageId = element && element.dataset && element.dataset.reactChatMessageId;
        if (!messageId) return;
        host.updateMessage(messageId, { status: status });
    }

    // ======================== appendReactUserMessage（覆盖） ========================

    function appendReactUserMessage(payload) {
        var host = getHost();
        if (!host || typeof host.appendMessage !== 'function') return;

        payload = payload || {};
        var text = String(payload.text || '').trim();
        var imageUrls = Array.isArray(payload.imageUrls) ? payload.imageUrls.filter(Boolean) : [];
        if (!text && imageUrls.length === 0) return;

        var author = getCurrentUserName();
        var blocks = [];

        if (text) {
            blocks.push({ type: 'text', text: text });
        }

        imageUrls.forEach(function (url, index) {
            blocks.push({
                type: 'image',
                url: String(url),
                alt: (window.t ? window.t('chat.pendingImageAlt', { index: index + 1 }) : '\u56FE\u7247 ' + (index + 1))
            });
        });

        host.appendMessage({
            id: nextReactMessageId('user'),
            role: 'user',
            author: author,
            time: getCurrentTimeString(),
            createdAt: Date.now(),
            avatarLabel: String(author).trim().slice(0, 1).toUpperCase(),
            blocks: blocks,
            status: 'sent'
        });
    }

    // ======================== refreshReactAssistantAvatars ========================

    function refreshReactAssistantAvatars() {
        var host = getHost();
        if (!host || typeof host.getState !== 'function' || typeof host.updateMessage !== 'function') return;
        var avatarUrl = getAssistantAvatarUrl();
        var snapshot = host.getState();
        if (!snapshot || !Array.isArray(snapshot.messages)) return;
        snapshot.messages.forEach(function (message) {
            if (!message || message.role !== 'assistant') return;
            host.updateMessage(message.id, { avatarUrl: avatarUrl || undefined });
        });
    }

    // ======================== 覆盖全局函数 ========================

    window.appendMessage = appendMessage;
    window.createGeminiBubble = createGeminiBubble;
    window.processRealisticQueue = processRealisticQueue;
    window.setReactMessageStatus = setReactMessageStatus;
    window._tryFlushPendingHostMessages = _tryFlushPendingHostMessages;
    window._clearPendingHostMessagesByIds = _clearPendingHostMessagesByIds;

    // 覆盖 appChat 上的方法
    if (window.appChat) {
        window.appChat.appendMessage = appendMessage;
        window.appChat.createGeminiBubble = createGeminiBubble;
        window.appChat.processRealisticQueue = processRealisticQueue;
        window.appChat.appendReactUserMessage = appendReactUserMessage;
        window.appChat.setReactMessageStatus = setReactMessageStatus;
    }

    // 头像更新事件
    window.addEventListener('chat-avatar-preview-updated', refreshReactAssistantAvatars);
    window.addEventListener('chat-avatar-preview-cleared', refreshReactAssistantAvatars);

    // init() 的 chat-avatar-preview-updated 事件可能在本脚本或 reactChatWindowHost 就绪前触发，
    // 延迟到所有同步脚本加载完成后主动刷新一次
    setTimeout(refreshReactAssistantAvatars, 0);
    // 同时尝试重放 host 未就绪期间缓存的消息
    setTimeout(_tryFlushPendingHostMessages, 0);

    // ======================== 隐藏旧 chat container ========================

    function hideOldChat() {
        // CSS 规则：body.react-chat-adapter-active #chat-container { display:none!important }
        document.body.classList.add('react-chat-adapter-active');
        // 双保险：inline style
        var el = document.getElementById('chat-container');
        if (el) el.style.cssText += 'display:none!important;visibility:hidden!important;';
    }

    // 立即执行 + DOMContentLoaded + load 三重兜底
    if (document.body) {
        hideOldChat();
    }
    document.addEventListener('DOMContentLoaded', hideOldChat);
    window.addEventListener('load', hideOldChat);

    // ======================== 自动开启 React chat ========================

    function autoOpenReactChat() {
        hideOldChat();
        var host = getHost();
        if (host && typeof host.openWindow === 'function') {
            host.openWindow();
        } else {
            setTimeout(autoOpenReactChat, 200);
        }
    }

    if (document.readyState === 'complete') {
        setTimeout(autoOpenReactChat, 100);
    } else {
        window.addEventListener('load', function () {
            setTimeout(autoOpenReactChat, 100);
        });
    }

    console.log('[ChatAdapter] React-first chat adapter loaded. Old DOM chat bypassed.');
})();
