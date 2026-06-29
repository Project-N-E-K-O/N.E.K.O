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
            window.__NEKO_TUTORIAL_ASSISTANT_NAME_OVERRIDE__
            || (window.lanlan_config && window.lanlan_config.lanlan_name)
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

    function getAssistantAvatarLabel() {
        var name = getCurrentAssistantName();
        var first = Array.from(name || '')[0] || '';
        return first ? first.toUpperCase() : undefined;
    }

    function nextReactMessageId(prefix) {
        _reactMessageSeq += 1;
        return (prefix || 'msg') + '-' + Date.now() + '-' + _reactMessageSeq;
    }

    function normalizeGeminiText(s) {
        return (s || '').replace(/\r\n/g, '\n');
    }

    function getCurrentAssistantTurnId() {
        return window._nekoAssistantTurnId ? String(window._nekoAssistantTurnId) : '';
    }

    function beginCompactCaptionSegment() {
        var turnId = getCurrentAssistantTurnId();
        if (window._nekoCompactCaptionSegmentTurnId !== turnId) {
            window._nekoCompactCaptionSegmentTurnId = turnId;
            window._nekoCompactCaptionSegmentSeq = 0;
        }
        window._nekoCompactCaptionSegmentSeq = (window._nekoCompactCaptionSegmentSeq || 0) + 1;
        window._nekoCompactCaptionSegmentId = (turnId || 'no-turn') + ':segment:' + window._nekoCompactCaptionSegmentSeq;
        return window._nekoCompactCaptionSegmentId;
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
            turnId: role === 'assistant' && window._nekoAssistantTurnId ? String(window._nekoAssistantTurnId) : undefined,
            avatarLabel: resolvedAuthor ? String(resolvedAuthor).trim().slice(0, 1).toUpperCase() : undefined,
            avatarUrl: avatarUrl || undefined,
            blocks: [{ type: 'text', text: cleanText }],
            status: status
        };
    }

    function emitCompactCaptionUpdate(text) {
        var cleanText = String(text || '')
            .replace(/\[play_music:[^\]]*(\]|$)/g, '')
            .replace(/\s+/g, ' ')
            .trim();
        var turnId = window._nekoAssistantTurnId ? String(window._nekoAssistantTurnId) : '';
        if (!turnId || !cleanText) return;
        window.dispatchEvent(new CustomEvent('neko-compact-caption-update', {
            detail: {
                turnId: turnId,
                segmentId: window._nekoCompactCaptionSegmentId || '',
                text: cleanText
            }
        }));
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

    function isFullWidthJoinPunctuation(ch) {
        return ch === '\u3001' || ch === '\u3002' || ch === '\uFF0C' || ch === '\uFF01' ||
            ch === '\uFF1F' || ch === '\uFF1B' || ch === '\uFF1A' || ch === '\u2026' ||
            ch === '\u201D' || ch === '\u2019' || ch === ')' || ch === ']' || ch === '}' ||
            ch === '\uFF09' || ch === '\uFF3D' || ch === '\uFF5D' ||
            ch === '\u3009' || ch === '\u300B' || ch === '\u300D' || ch === '\u300F' ||
            ch === '\u3011' || ch === '\u3015' || ch === '\u3017' || ch === '\u3019' ||
            ch === '\u301B' || ch === '\u301E' || ch === '\u301F';
    }

    function getRealisticQueueItemText(item) {
        if (item && typeof item === 'object' && typeof item.text !== 'undefined') {
            return normalizeGeminiText(item.text);
        }
        return normalizeGeminiText(item);
    }

    function getRealisticQueueItemTurnId(item) {
        if (item && typeof item === 'object' && item.turnId) {
            return String(item.turnId);
        }
        return getCurrentAssistantTurnId();
    }

    function createRealisticQueueItem(text, turnId) {
        return {
            text: normalizeGeminiText(text),
            turnId: turnId ? String(turnId) : getCurrentAssistantTurnId()
        };
    }

    function getCommonRealisticQueueTurnId(queue) {
        var commonTurnId = '';
        for (var i = 0; i < queue.length; i++) {
            var turnId = getRealisticQueueItemTurnId(queue[i]);
            if (!turnId) return '';
            if (!commonTurnId) {
                commonTurnId = turnId;
            } else if (commonTurnId !== turnId) {
                return '';
            }
        }
        return commonTurnId;
    }

    function joinRealisticPendingPieces(pieces) {
        var joined = '';
        for (var i = 0; i < pieces.length; i++) {
            var piece = getRealisticQueueItemText(pieces[i]).replace(/^\s+/, '').replace(/\s+$/, '');
            if (!piece) continue;
            if (!joined) {
                joined = piece;
                continue;
            }
            var prev = joined.replace(/\s+$/, '');
            var prevLast = prev.charAt(prev.length - 1);
            var nextFirst = piece.replace(/^\s+/, '').charAt(0);
            var glue = (isFullWidthJoinPunctuation(prevLast) || isFullWidthJoinPunctuation(nextFirst)) ? '' : ' ';
            joined = prev + glue + piece;
        }
        return joined;
    }

    function isMiddleSplitBoundary(text, index) {
        var prev = text.charAt(index - 1);
        var next = text.charAt(index);
        return /\s/.test(prev) || /\s/.test(next) ||
            isFullWidthJoinPunctuation(prev) ||
            /[.!?;,]/.test(prev);
    }

    function splitRealisticTextNearMiddle(text) {
        var normalized = normalizeGeminiText(text).replace(/^\s+/, '').replace(/\s+$/, '');
        if (!normalized) return [];
        if (normalized.length < 2) return [normalized];

        var midpoint = Math.floor(normalized.length / 2);
        var splitAt = midpoint;
        for (var offset = 0; offset < normalized.length; offset++) {
            var left = midpoint - offset;
            var right = midpoint + offset;
            if (left > 0 && left < normalized.length && isMiddleSplitBoundary(normalized, left)) {
                splitAt = left;
                break;
            }
            if (right > 0 && right < normalized.length && isMiddleSplitBoundary(normalized, right)) {
                splitAt = right;
                break;
            }
        }

        var first = normalized.slice(0, splitAt).replace(/^\s+/, '').replace(/\s+$/, '');
        var second = normalized.slice(splitAt).replace(/^\s+/, '').replace(/\s+$/, '');
        if (!first || !second) return [normalized];
        return [first, second];
    }

    function rebalanceRealisticQueueIfNeeded() {
        var queue = window._realisticGeminiQueue;
        if (!Array.isArray(queue) || queue.length <= 2) return;

        var queueTurnId = getCommonRealisticQueueTurnId(queue);
        if (!queueTurnId) return;
        var queueText = joinRealisticPendingPieces(queue);
        var splitQueue = splitRealisticTextNearMiddle(queueText);
        if (splitQueue.length > 0 && splitQueue.length <= queue.length) {
            window._realisticGeminiQueue = splitQueue.map(function (text) {
                return createRealisticQueueItem(text, queueTurnId);
            });
        }
    }

    // ======================== 合并模式检测 ========================

    function isMergeMessagesEnabled() {
        if (typeof window.mergeMessagesEnabled !== 'undefined') return window.mergeMessagesEnabled;
        return S && S.mergeMessagesEnabled;
    }

    // ======================== 结构化文本检测（委托给共享 util） ========================

    function looksLikeStructuredRichText(text) {
        // 统一复用 app-chat-text-utils.js 里的唯一实现，避免与 DOM 路径分叉。
        return window.appChatTextUtils.looksLikeStructuredRichText(text);
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
            if (appendHostMessageSafely(host, batch[i], 'flush_pending_host_message')) {
                markAssistantVisibleResponseForAchievement();
            }
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

    function _resetReactChatSwitchState() {
        _pendingHostMessages = [];
        if (_pendingFlushTimer) {
            clearInterval(_pendingFlushTimer);
            _pendingFlushTimer = null;
        }
    }

    function markAssistantVisibleResponseForAchievement() {
        if (window.appChat && typeof window.appChat.markAssistantVisibleResponse === 'function') {
            window.appChat.markAssistantVisibleResponse();
        }
    }

    function appendHostMessageSafely(host, message, context) {
        if (!message || !host || typeof host.appendMessage !== 'function') return false;
        try {
            host.appendMessage(message);
            return true;
        } catch (error) {
            console.warn('[ChatAdapter] host.appendMessage failed', context || '', error);
            return false;
        }
    }

    function createGeminiBubble(sentence, options) {
        options = options || {};
        var host = getHost();
        var cleanSentence = (sentence || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
        var explicitTurnId = options.turnId ? String(options.turnId) : '';
        if (!explicitTurnId && typeof window.ensureAssistantTurnStarted === 'function') {
            window.ensureAssistantTurnStarted('create_gemini_bubble');
        }
        var msgId = nextReactMessageId('assistant');
        var timeStr = getCurrentTimeString();
        var previousTurnId = window._nekoAssistantTurnId;
        var msg = null;
        if (explicitTurnId) {
            window._nekoAssistantTurnId = explicitTurnId;
        }
        try {
            msg = buildMessage(msgId, 'assistant', getCurrentAssistantName(), timeStr, cleanSentence, 'streaming');
        } finally {
            if (explicitTurnId) {
                window._nekoAssistantTurnId = previousTurnId || null;
            }
        }

        var appendedToHost = false;
        if (msg && host && typeof host.appendMessage === 'function') {
            // host 就绪，先重放待发队列，再追加新消息
            _tryFlushPendingHostMessages();
            appendedToHost = appendHostMessageSafely(host, msg, 'create_gemini_bubble');
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

        if (appendedToHost) {
            markAssistantVisibleResponseForAchievement();
        }

        return ref;
    }

    // ======================== processRealisticQueue（覆盖） ========================

    function createRealisticQueueOwnerToken() {
        return 'realistic-queue-' + Date.now() + '-' + Math.random().toString(36).slice(2);
    }

    async function processRealisticQueue(queueVersion) {
        queueVersion = queueVersion || (window._realisticGeminiVersion || 0);
        if (window._realisticProcessingOwner) return;
        var processingOwner = createRealisticQueueOwnerToken();
        window._realisticProcessingOwner = processingOwner;
        window._isProcessingRealisticQueue = true;

        try {
            while (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                if (window._realisticProcessingOwner !== processingOwner) {
                    break;
                }
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
                if (window._realisticProcessingOwner !== processingOwner) {
                    break;
                }

                var item = window._realisticGeminiQueue.shift();
                var itemText = getRealisticQueueItemText(item);
                if (itemText && (window._realisticGeminiVersion || 0) === queueVersion) {
                    createGeminiBubble(itemText, {
                        turnId: getRealisticQueueItemTurnId(item)
                    });
                    window._lastBubbleTime = Date.now();
                }
            }
        } finally {
            if (window._realisticProcessingOwner === processingOwner) {
                window._realisticProcessingOwner = null;
                if (window._isProcessingRealisticQueue) {
                    window._isProcessingRealisticQueue = false;
                    if (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                        processRealisticQueue(window._realisticGeminiVersion || 0);
                    }
                }
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
            var appendedToHost = appendHostMessageSafely(host, msg, 'render_structured_gemini_message');
            if (!appendedToHost) return;

            var ref = createVirtualBubbleRef(msgId);
            ref._stableTime = timeStr;
            window.currentGeminiMessage = ref;
            window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
            window.currentTurnGeminiBubbles.push(ref);
            markAssistantVisibleResponseForAchievement();
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
        // 无论队列是否为空，都要先释放处理锁，避免 stale owner 阻塞下一轮。
        window._isProcessingRealisticQueue = false;
        window._realisticProcessingOwner = null;
        var queue = window._realisticGeminiQueue;
        if (!Array.isArray(queue) || queue.length === 0) return;
        // 同步创建所有待排队的 bubble
        for (var i = 0; i < queue.length; i++) {
            try {
                createGeminiBubble(getRealisticQueueItemText(queue[i]), {
                    turnId: getRealisticQueueItemTurnId(queue[i])
                });
            } catch (_) {}
        }
        window._realisticGeminiQueue = [];
    }

    // ======================== appendMessage（覆盖核心） ========================

    function appendMessage(text, sender, isNewMessage, options) {
        if (typeof isNewMessage === 'undefined') isNewMessage = true;
        options = options || {};

        var host = getHost();
        var bubbleCountBefore = window.currentTurnGeminiBubbles ? window.currentTurnGeminiBubbles.length : 0;
        var createdVisibleBubble = false;

        // 维护"本段 AI 回复"的完整文本（emotion analysis / subtitle 需要）
        if (sender === 'gemini') {
            // ── Segment boundary detection ──────────────────────────────────
            // 一个 dialog turn 内可能出现多段独立的 AI 回复，每段都是一个
            // 完整的 utterance（独立音频、独立 turn_end、独立 emotion）：
            //
            //   Path A (multi-response-item)：realtime LLM 同一对话轮里连发
            //     N 次 response.created，服务端 omni_realtime_client 每次都
            //     把 _is_first_text_chunk 复位为 True，所以 seg2 首 chunk
            //     也以 isNewMessage=true 抵达。turn_end_1 一定在 seg2 chunks
            //     之前发出（handle_response_complete 同步 await）。
            //
            //   Path B (late continuation)：Gemini Live 在 response.done 之后
            //     的 _ai_recent_activity_window 内还能来 chunk（典型 case：
            //     tool 调用前后两段台词）。服务端不重置 _is_first_text_chunk，
            //     前端拿到的是 isNewMessage=false。但 turn_end 已经发过且
            //     adapter 已把上一段气泡置 'sent' —— React StreamingText
            //     会在 status sent→streaming 切换时 re-mount + settledLen
            //     复位，导致 seg2 文字视觉上闪没。
            //
            // turn_end handler (app-websocket.js) 在两条路径之间都设
            // window._geminiTurnEndSealed = true。本段第一个 chunk 看到
            // sealed && !isNewMessage 即为 Path B 的虚拟"段起点"，与
            // Path A 的 isNewMessage=true 统一走同一套 per-segment reset。
            var startNewSegment = isNewMessage || !!window._geminiTurnEndSealed;
            if (startNewSegment) {
                // 同步渲染旧队列中所有待处理句子，防止 processRealisticQueue
                // 的 async 循环因 version 变更而静默丢弃队列中剩余的句子。
                _flushPendingRealisticQueue();
                window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
                window._geminiTurnFullText = '';
                window._pendingMusicCommand = '';
                window._structuredGeminiStreaming = false;
                window._turnIsStructured = false;
                window._geminiTurnEndSealed = false;
                beginCompactCaptionSegment();
                // Per-segment scoping for cleanup. response_discarded /
                // user-activity-cancel 都只针对当前 in-flight response item，
                // 不应跨段抹掉已经完成的上一段气泡 —— per-segment 才对。
                // currentGeminiMessage 也显式置 null，防止
                // renderStructuredGeminiMessage 把上一段气泡误当成"当前段
                // 已建气泡"走 collapse + upgrade 把它覆盖掉。
                window.currentTurnGeminiBubbles = [];
                window.currentTurnGeminiAttachments = [];
                window.currentGeminiMessage = null;
                // 字幕闸门 reset：上一段 turn_end 触发的 translateAndShowSubtitle
                // 会把 isCurrentTurnFinalized 置 true；不重置就会把本段首个
                // chunk 的 updateSubtitleStreamingText 静默丢弃。
                if (typeof window.beginSubtitleTurn === 'function') {
                    window.beginSubtitleTurn();
                }
            }
            var prevFull = typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '';
            window._geminiTurnFullText = prevFull + normalizeGeminiText(text);

            // 常驻字幕流式写入（adapter 是生产常驻路径；PR #777 漏了这段，导致 React
            // 聊天窗口下字幕只能等 turn_end 才首次出现，视觉上像"一口气显示"）。
            // 结构化命中时改走 [markdown] 占位，turn_end 跳过翻译。
            var streamingText = window._geminiTurnFullText.replace(/\[play_music:[^\]]*(\]|$)/g, '');
            if (!window._turnIsStructured && looksLikeStructuredRichText(streamingText)) {
                window._turnIsStructured = true;
            }
            if (window._turnIsStructured) {
                if (typeof window.markSubtitleStructured === 'function') {
                    window.markSubtitleStructured();
                }
            } else if (typeof window.updateSubtitleStreamingText === 'function') {
                window.updateSubtitleStreamingText(streamingText);
            }
            emitCompactCaptionUpdate(streamingText);
        }

        // ---------- gemini + realistic 模式 ----------
        if (sender === 'gemini' && !isMergeMessagesEnabled()) {
            if (startNewSegment) {
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
                var queueTurnId = getCurrentAssistantTurnId();
                window._realisticGeminiQueue.push.apply(window._realisticGeminiQueue, splitResult.sentences.map(function (sentence) {
                    return createRealisticQueueItem(sentence, queueTurnId);
                }));
                rebalanceRealisticQueueIfNeeded();
                processRealisticQueue(window._realisticGeminiVersion || 0);
                createdVisibleBubble = (window.currentTurnGeminiBubbles ? window.currentTurnGeminiBubbles.length : 0) > bubbleCountBefore;
            }

        // ---------- gemini + merge 模式 + 新段（含 isNewMessage 与 post-seal） ----------
        } else if (sender === 'gemini' && isMergeMessagesEnabled() && startNewSegment) {
            window._realisticGeminiBuffer = '';
            window._realisticGeminiQueue = [];
            window._lastBubbleTime = 0;

            var cleanNewText = cleanMusicFromChunk(text);

            if (cleanNewText.trim() && host) {
                var msgId = nextReactMessageId('assistant');
                var timeStr = getCurrentTimeString();
                var msg = buildMessage(msgId, 'assistant', getCurrentAssistantName(), timeStr, cleanNewText, 'streaming');
                var appendedToHost = appendHostMessageSafely(host, msg, 'merge_new_message');
                if (!appendedToHost) return createdVisibleBubble;

                var ref = createVirtualBubbleRef(msgId);
                ref._stableTime = timeStr;
                window.currentGeminiMessage = ref;
                window.currentTurnGeminiBubbles.push(ref);
                createdVisibleBubble = true;
                markAssistantVisibleResponseForAchievement();
            } else {
                window.currentGeminiMessage = null;
            }

        // ---------- gemini + merge 模式 + 段内续写 ----------
        } else if (sender === 'gemini' && isMergeMessagesEnabled()) {
            var cleanText = cleanMusicFromChunk(text);

            // 场景 A: 本段尚无气泡（每个 startNewSegment 都已把
            // currentTurnGeminiBubbles 清空 + currentGeminiMessage 置 null，
            // 所以两个判断等价；保留 length===0 作为主条件与历史代码对齐）。
            if (!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0) {
                if (cleanText.trim() && host) {
                    var newId = nextReactMessageId('assistant');
                    var newTime = getCurrentTimeString();
                    var newMsg = buildMessage(newId, 'assistant', getCurrentAssistantName(), newTime, cleanText, 'streaming');
                    var appendedToHost = appendHostMessageSafely(host, newMsg, 'merge_continuation_first_visible');
                    if (!appendedToHost) return createdVisibleBubble;

                    var newRef = createVirtualBubbleRef(newId);
                    newRef._stableTime = newTime;
                    window.currentGeminiMessage = newRef;
                    window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
                    window.currentTurnGeminiBubbles.push(newRef);
                    createdVisibleBubble = true;
                    markAssistantVisibleResponseForAchievement();
                } else {
                    window.currentGeminiMessage = null;
                }
            }
            // 场景 B: 气泡已存在，追加更新（_geminiTurnFullText 是 per-segment
            // 累积，已在 startNewSegment 处重置过，不需要再 slice）。
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
                markAssistantVisibleResponseForAchievement();
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
        if (!host || typeof host.appendMessage !== 'function') return null;

        payload = payload || {};
        var text = String(payload.text || '').trim();
        var imageUrls = Array.isArray(payload.imageUrls) ? payload.imageUrls.filter(Boolean) : [];
        if (!text && imageUrls.length === 0) return null;

        var author = getCurrentUserName();
        var blocks = [];

        if (text) {
            blocks.push({ type: 'text', text: text });
        }

        imageUrls.forEach(function (url, index) {
            var translatedAlt = window.t ? window.t('chat.pendingImageAlt', { index: index + 1 }) : '';
            blocks.push({
                type: 'image',
                url: String(url),
                alt: (typeof translatedAlt === 'string' && translatedAlt ? translatedAlt : '\u56FE\u7247 ' + (index + 1))
            });
        });

        return host.appendMessage({
            id: payload.id ? String(payload.id) : nextReactMessageId('user'),
            role: 'user',
            author: author,
            time: payload.time ? String(payload.time) : getCurrentTimeString(),
            createdAt: Date.now(),
            avatarLabel: String(author).trim().slice(0, 1).toUpperCase(),
            blocks: blocks,
            status: payload.status ? String(payload.status) : 'sent'
        });
    }

    // ======================== appendReactTopicHint（深话题预告气泡） ========================

    // Frontend-only teaser shown right before a proactive deep-topic opener.
    // Backend sends a `topic_hint` WS frame that never touches the sync queue /
    // chat memory, so this bubble is display-only and never re-enters LLM
    // context. Rendered by react-neko-chat's dedicated TopicHintBubble.
    function topicHintMessageId(turnId) {
        // Deterministic id keyed on the turn so a later cancel_topic_hint frame
        // can remove exactly this teaser. Falls back to an auto id when no turn
        // is supplied (then it just can't be cancelled, which is fine).
        var tid = String(turnId || '').trim();
        return tid ? 'topic-hint-' + tid : nextReactMessageId('topic-hint');
    }

    function appendReactTopicHint(author, turnId) {
        var host = getHost();
        if (!host || typeof host.appendMessage !== 'function') return null;

        // Trim BEFORE falling back: a whitespace-only author must yield the
        // assistant name, not an empty bubble.
        var name = String(author || '').trim() || String(getCurrentAssistantName() || '').trim();
        if (!name) return null;

        return host.appendMessage({
            id: topicHintMessageId(turnId),
            role: 'system',
            author: name,
            time: getCurrentTimeString(),
            createdAt: Date.now(),
            turnId: String(turnId || '').trim() || undefined,
            blocks: [{ type: 'topic-hint', author: name }]
        });
    }

    // Retract a previously shown teaser when its opener failed before producing
    // any output (backend send_cancel_topic_hint). No-op if it was never shown.
    function removeReactTopicHint(turnId) {
        var host = getHost();
        if (!host || typeof host.removeMessage !== 'function') return;
        var tid = String(turnId || '').trim();
        if (!tid) return;
        host.removeMessage('topic-hint-' + tid);
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
            host.updateMessage(message.id, {
                author: getCurrentAssistantName(),
                avatarLabel: getAssistantAvatarLabel(),
                avatarUrl: avatarUrl || undefined
            });
        });
    }

    // ======================== 覆盖全局函数 ========================

    window.appendMessage = appendMessage;
    window.createGeminiBubble = createGeminiBubble;
    window.processRealisticQueue = processRealisticQueue;
    window.setReactMessageStatus = setReactMessageStatus;
    window._tryFlushPendingHostMessages = _tryFlushPendingHostMessages;
    window._clearPendingHostMessagesByIds = _clearPendingHostMessagesByIds;
    window._resetReactChatSwitchState = _resetReactChatSwitchState;
    window.appendReactTopicHint = appendReactTopicHint;
    window.removeReactTopicHint = removeReactTopicHint;

    // 覆盖 appChat 上的方法
    if (window.appChat) {
        window.appChat.appendMessage = appendMessage;
        window.appChat.createGeminiBubble = createGeminiBubble;
        window.appChat.processRealisticQueue = processRealisticQueue;
        window.appChat.appendReactUserMessage = appendReactUserMessage;
        window.appChat.appendReactTopicHint = appendReactTopicHint;
        window.appChat.removeReactTopicHint = removeReactTopicHint;
        window.appChat.setReactMessageStatus = setReactMessageStatus;
    }

    // 头像更新事件
    window.addEventListener('chat-avatar-preview-updated', refreshReactAssistantAvatars);
    window.addEventListener('chat-avatar-preview-cleared', refreshReactAssistantAvatars);
    window.addEventListener('neko:tutorial-chat-identity-changed', refreshReactAssistantAvatars);

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

    async function waitForStartupBarrier() {
        if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
            try {
                await window.waitForStorageLocationStartupBarrier();
            } catch (_) {}
        } else if (window.__nekoStorageLocationStartupBarrier
            && typeof window.__nekoStorageLocationStartupBarrier.then === 'function') {
            try {
                await window.__nekoStorageLocationStartupBarrier;
            } catch (_) {}
        }
    }

    async function autoOpenReactChat() {
        await waitForStartupBarrier();
        hideOldChat();
        if (window.__NEKO_MULTI_WINDOW__ && !/^\/chat(?:_full)?(?:\/|$)/.test(window.location.pathname || '')) {
            return;
        }
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
