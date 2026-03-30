/**
 * app-chat.js — 聊天/消息渲染模块
 * 从 app.js lines 2135-2634 提取：
 *   - getCurrentTimeString()
 *   - createGeminiBubble()
 *   - processRealisticQueue()
 *   - dispatchMusicPlay()
 *   - processMusicCommands()
 *   - appendMessage()
 *   - checkAndUnlockFirstDialogueAchievement()
 *   - 首次交互跟踪变量
 */
(function () {
    'use strict';

    const mod = {};
    const S = window.appState;
    // const C = window.appConst;  // unused in this module for now

    // ======================== 模块级变量 ========================
    let _musicDispatchId = 0;
    let _reactMessageSeq = 0;
    let _userDisplayNamePromise = null;

    // 首次交互跟踪
    let isFirstUserInput = true;   // 跟踪是否为用户第一次输入
    let isFirstAIResponse = true;  // 跟踪是否为AI第一次回复

    // ======================== 工具函数 ========================

    function getCurrentTimeString() {
        return new Date().toLocaleTimeString('en-US', {
            hour12: false,
            hour: '2-digit',
            minute: '2-digit',
            second: '2-digit'
        });
    }

    function getReactChatHost() {
        return window.reactChatWindowHost || null;
    }

    function sanitizeDisplayName(value) {
        if (value == null) return '';
        var text = String(value).trim();
        return text;
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
            window.username,
            window.userName,
            window.displayName,
            window.nickname
        ];

        for (var i = 0; i < candidates.length; i += 1) {
            var resolved = sanitizeDisplayName(candidates[i]);
            if (resolved) return resolved;
        }

        try {
            var storageKeys = ['nickname', 'displayName', 'userName', 'username'];
            for (var j = 0; j < storageKeys.length; j += 1) {
                var stored = sanitizeDisplayName(localStorage.getItem(storageKeys[j]));
                if (stored) return stored;
            }
        } catch (_) {}

        return 'You';
    }

    function getRoleDisplayName(role, fallbackAuthor) {
        var resolvedFallback = sanitizeDisplayName(fallbackAuthor);
        if (resolvedFallback) return resolvedFallback;
        return role === 'user' ? getCurrentUserName() : getCurrentAssistantName();
    }

    function resolveMasterDisplayNameFromProfile(masterProfile) {
        if (!masterProfile || typeof masterProfile !== 'object') return '';
        var nickname = sanitizeDisplayName(masterProfile['昵称']);
        if (nickname) {
            var firstNickname = nickname.split(',')[0].split('，')[0].trim();
            if (firstNickname) return firstNickname;
        }
        return sanitizeDisplayName(masterProfile['档案名']);
    }

    async function ensureUserDisplayName() {
        if (getCurrentUserName() !== 'You') {
            return getCurrentUserName();
        }

        if (_userDisplayNamePromise) {
            return _userDisplayNamePromise;
        }

        _userDisplayNamePromise = (async function () {
            try {
                var response = await fetch('/api/characters?language=zh-CN', {
                    credentials: 'same-origin'
                });
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                var data = await response.json();
                var masterProfile = data && data['主人'];
                var displayName = resolveMasterDisplayNameFromProfile(masterProfile);
                var profileName = masterProfile && sanitizeDisplayName(masterProfile['档案名']);
                var nickname = masterProfile && sanitizeDisplayName(masterProfile['昵称']);

                if (displayName) {
                    window.master_display_name = displayName;
                    window.master_name = profileName || displayName;
                    window.master_profile_name = profileName || '';
                    window.master_nickname = nickname || '';
                    window.lanlan_config = window.lanlan_config || {};
                    window.lanlan_config.master_display_name = displayName;
                    window.lanlan_config.master_name = profileName || displayName;
                    window.lanlan_config.master_profile_name = profileName || '';
                    window.lanlan_config.master_nickname = nickname || '';
                }
                return displayName || getCurrentUserName();
            } catch (error) {
                console.warn('[Chat] ensureUserDisplayName failed:', error);
                return getCurrentUserName();
            } finally {
                _userDisplayNamePromise = null;
            }
        })();

        return _userDisplayNamePromise;
    }

    function getAssistantAvatarUrl() {
        if (!window.appChatAvatar || typeof window.appChatAvatar.getCurrentAvatarDataUrl !== 'function') {
            return '';
        }
        return window.appChatAvatar.getCurrentAvatarDataUrl() || '';
    }

    function nextReactMessageId(prefix) {
        _reactMessageSeq += 1;
        return (prefix || 'msg') + '-' + Date.now() + '-' + _reactMessageSeq;
    }

    function getOrAssignReactMessageId(element, prefix) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) return nextReactMessageId(prefix);
        if (!element.dataset.reactChatMessageId) {
            element.dataset.reactChatMessageId = nextReactMessageId(prefix);
        }
        return element.dataset.reactChatMessageId;
    }

    function extractMessageText(text) {
        return String(text || '')
            .replace(/^\[\d{2}:\d{2}:\d{2}\]\s+[\u{1F380}\u{1F4AC}]\s*/u, '')
            .trim();
    }

    function buildReactTextMessage(messageId, role, author, timeText, text, status) {
        var cleanText = extractMessageText(text);
        if (!cleanText) return null;
        var avatarUrl = role === 'assistant' ? getAssistantAvatarUrl() : '';
        var resolvedAuthor = getRoleDisplayName(role, author);

        return {
            id: messageId,
            role: role,
            author: resolvedAuthor,
            time: timeText || getCurrentTimeString(),
            createdAt: Date.now(),
            avatarLabel: resolvedAuthor ? String(resolvedAuthor).trim().slice(0, 1).toUpperCase() : undefined,
            avatarUrl: avatarUrl || undefined,
            blocks: [
                {
                    type: 'text',
                    text: cleanText
                }
            ],
            status: status
        };
    }

    function appendReactTextMessage(element, role, author, text, status) {
        var host = getReactChatHost();
        if (!host || typeof host.appendMessage !== 'function') return;

        var messageId = getOrAssignReactMessageId(element, role);
        var message = buildReactTextMessage(messageId, role, author, getCurrentTimeString(), text, status);
        if (!message) return;
        host.appendMessage(message);
    }

    function appendReactUserMessage(payload) {
        var host = getReactChatHost();
        if (!host || typeof host.appendMessage !== 'function') return;

        payload = payload || {};
        var text = String(payload.text || '').trim();
        var imageUrls = Array.isArray(payload.imageUrls) ? payload.imageUrls.filter(Boolean) : [];
        if (!text && imageUrls.length === 0) return;

        var author = getCurrentUserName();
        var blocks = [];

        if (text) {
            blocks.push({
                type: 'text',
                text: text
            });
        }

        imageUrls.forEach(function (url, index) {
            blocks.push({
                type: 'image',
                url: String(url),
                alt: (window.t ? window.t('chat.pendingImageAlt', { index: index + 1 }) : '图片 ' + (index + 1))
            });
        });

        host.appendMessage({
            id: nextReactMessageId('user'),
            role: 'user',
            author: author,
            time: getCurrentTimeString(),
            createdAt: Date.now(),
            avatarLabel: author ? String(author).trim().slice(0, 1).toUpperCase() : undefined,
            blocks: blocks,
            status: 'sent'
        });
    }

    function updateReactTextMessage(element, role, author, text, status) {
        var host = getReactChatHost();
        if (!host || typeof host.updateMessage !== 'function' || !element) return;

        var messageId = getOrAssignReactMessageId(element, role);
        var message = buildReactTextMessage(messageId, role, author, getCurrentTimeString(), text, status);
        if (!message) return;

        host.updateMessage(messageId, {
            author: message.author,
            time: message.time,
            avatarUrl: message.avatarUrl,
            blocks: message.blocks,
            status: status
        });
    }

    function setReactMessageStatus(element, role, status) {
        var host = getReactChatHost();
        if (!host || typeof host.updateMessage !== 'function' || !element) return;

        var messageId = getOrAssignReactMessageId(element, role);
        host.updateMessage(messageId, {
            status: status
        });
    }

    function refreshReactAssistantAvatars() {
        var host = getReactChatHost();
        if (!host || typeof host.getState !== 'function' || typeof host.updateMessage !== 'function') return;

        var avatarUrl = getAssistantAvatarUrl();
        var snapshot = host.getState();
        if (!snapshot || !Array.isArray(snapshot.messages)) return;

        snapshot.messages.forEach(function (message) {
            if (!message || message.role !== 'assistant') return;
            host.updateMessage(message.id, {
                avatarUrl: avatarUrl || undefined
            });
        });
    }

    // ======================== 成就 ========================

    /**
     * 检查并解锁首次对话成就
     * 当用户和AI都完成首次交互后调用API
     */
    async function checkAndUnlockFirstDialogueAchievement() {
        if (!isFirstUserInput && !isFirstAIResponse) {
            if (!window.unlockAchievement) return;
            console.log(window.t('console.firstConversationUnlockAchievement'));
            try {
                await window.unlockAchievement('ACH_FIRST_DIALOGUE');
            } catch (error) {
                console.error(window.t('console.achievementUnlockError'), error);
            }
        }
    }

    // ======================== 气泡创建 ========================

    function createGeminiBubble(sentence) {
        const chatContainer = S.dom.chatContainer;
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', 'gemini');
        const cleanSentence = (sentence || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
        messageDiv.textContent = "[" + getCurrentTimeString() + "] \u{1F380} " + cleanSentence;
        chatContainer.appendChild(messageDiv);
        appendReactTextMessage(messageDiv, 'assistant', getCurrentAssistantName(), messageDiv.textContent);
        window.currentGeminiMessage = messageDiv;

        // ========== 追踪本轮气泡 ==========
        window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
        window.currentTurnGeminiBubbles.push(messageDiv);

        // 检测AI消息的语言，如果与用户语言不同，显示字幕提示框
        checkAndShowSubtitlePrompt(cleanSentence);

        // 如果是AI第一次回复，更新状态并检查成就
        if (isFirstAIResponse) {
            isFirstAIResponse = false;
            console.log(window.t('console.aiFirstReplyDetected'));
            checkAndUnlockFirstDialogueAchievement();
        }
    }

    // ======================== 拟真输出队列 ========================

    async function processRealisticQueue(queueVersion) {
        queueVersion = queueVersion || (window._realisticGeminiVersion || 0);
        if (window._isProcessingRealisticQueue) return;
        window._isProcessingRealisticQueue = true;

        const chatContainer = S.dom.chatContainer;

        try {
            while (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                if ((window._realisticGeminiVersion || 0) !== queueVersion) {
                    break;
                }
                // 基于时间戳的延迟：确保每句之间至少间隔2秒
                const now = Date.now();
                const timeSinceLastBubble = now - (window._lastBubbleTime || 0);
                if (window._lastBubbleTime > 0 && timeSinceLastBubble < 2000) {
                    await new Promise(function (resolve) { setTimeout(resolve, 2000 - timeSinceLastBubble); });
                }

                if ((window._realisticGeminiVersion || 0) !== queueVersion) {
                    break;
                }

                const s = window._realisticGeminiQueue.shift();
                if (s && (window._realisticGeminiVersion || 0) === queueVersion) {
                    createGeminiBubble(s);
                    // 仅在用户已处于底部附近时自动滚动
                    var _wrap = chatContainer.parentElement;
                    if (_wrap && _wrap.scrollHeight - _wrap.scrollTop - _wrap.clientHeight < 60) {
                        _wrap.scrollTop = _wrap.scrollHeight;
                    }
                    window._lastBubbleTime = Date.now();
                }
            }
        } finally {
            window._isProcessingRealisticQueue = false;
            // 兜底检查：如果在循环结束到重置标志位之间又有新消息进入队列，递归触发
            if (window._realisticGeminiQueue && window._realisticGeminiQueue.length > 0) {
                processRealisticQueue(window._realisticGeminiVersion || 0);
            }
        }
    }

    // ======================== 音乐播放调度 ========================

    /**
     * 向音频组件派发播放请求 [Async Ready]
     * @param {Object} trackInfo
     * @param {Object} options 
     */
    window.dispatchMusicPlay = async function (trackInfo, options) {
        options = options || {};

        // 拦截逻辑：如果是主动搭话触发的切歌，且当前正在放歌，则拦截
        if (options.source === 'proactive' && typeof window.isMusicPlaying === 'function' && window.isMusicPlaying()) {
            console.log('[MusicDispatch] 拦截来自主动搭话的切歌请求，保持当前播放');
            return false;
        }

        if (!trackInfo || !trackInfo.url) {
            console.warn('[MusicDispatch] 无效的音乐信息，跳过播放');
            return false;
        }

        var currentDispatchId = ++_musicDispatchId;

        if (window.sendMusicMessage) {
            var accepted = await window.sendMusicMessage(trackInfo);
            return accepted; // 返回布尔值表示是否成功派发
        } else {
            console.warn('[MusicDispatch] sendMusicMessage \u5C1A\u672A\u5C31\u7EEA\uFF0C\u542F\u52A8\u7B49\u5F85 (ID: ' + currentDispatchId + ')...');

            var retryPlay = function () {
                // 门闩校验：只允许最新的 dispatch 请求执行
                if (currentDispatchId !== _musicDispatchId) {
                    console.log('[MusicDispatch] \u653E\u5F03\u8FC7\u65F6\u7684\u64AD\u653E\u8BF7\u6C42 (ID: ' + currentDispatchId + ')');
                    cleanup();
                    return;
                }

                if (window.sendMusicMessage) {
                    console.log('[MusicDispatch] \u63A5\u53E3\u5DF2\u5C31\u7EEA\uFF0C\u8865\u53D1\u64AD\u653E\u8BF7\u6C42 (ID: ' + currentDispatchId + ')');
                    cleanup();
                    window.dispatchMusicPlay(trackInfo, options);
                }
            };

            var cleanup = function () {
                clearInterval(pollTimer);
                clearTimeout(timeoutTimer);
                window.removeEventListener('music-ui-ready', retryPlay);
            };

            var pollTimer = setInterval(retryPlay, 500);
            var timeoutTimer = setTimeout(cleanup, 5000);
            window.addEventListener('music-ui-ready', retryPlay, { once: true });

            return 'queued'; // 返回特殊状态表示排队中
        }
    };

    // ======================== 音乐指令解析 ========================

    /**
     * 解析并处理 AI 文本中的音乐播放指令
     *
     * 【当前状态 - 预留功能】
     * 此函数目前未被调用。当前主动搭话音乐功能走的是另一条路径：
     *   后端 proactive_chat_prompt_music → 返回搜索关键词 → 后端搜索 → source_links → 前端播放
     *
     * 【未来用途】
     * 当需要在普通对话中让 AI 主动触发音乐播放时，需要在角色系统提示词中添加指令说明，
     * 让 AI 输出 [play_music: {"name": "歌曲名", "artist": "歌手名"}] 格式的指令。
     * 届时在消息处理流程中调用此函数即可解析并播放音乐。
     *
     * 【指令格式】
     * [play_music: {"name": "歌曲名", "artist": "歌手名"}]
     * - name: 必填，歌曲名称
     * - artist: 可选，歌手名称
     *
     * @param {string} text - 可能包含音乐指令的文本
     */
    window.processMusicCommands = async function (text) {
        if (!text) return;
        var musicRegex = /\[play_music:\s*({[\s\S]*?})\]/g;
        var match;

        function levenshteinDistance(a, b) {
            if (!a || !b) return 999;
            a = a.toLowerCase();
            b = b.toLowerCase();
            if (a === b) return 0;
            var matrix = [];
            for (var i = 0; i <= b.length; i++) {
                matrix[i] = [i];
            }
            for (var j = 0; j <= a.length; j++) {
                matrix[0][j] = j;
            }
            for (var i = 1; i <= b.length; i++) {
                for (var j = 1; j <= a.length; j++) {
                    if (b.charAt(i - 1) === a.charAt(j - 1)) {
                        matrix[i][j] = matrix[i - 1][j - 1];
                    } else {
                        matrix[i][j] = Math.min(
                            matrix[i - 1][j - 1] + 1,
                            matrix[i][j - 1] + 1,
                            matrix[i - 1][j] + 1
                        );
                    }
                }
            }
            return matrix[b.length][a.length];
        }

        function calculateSimilarity(a, b) {
            if (!a || !b) return 0;
            a = a.toLowerCase().trim();
            b = b.toLowerCase().trim();
            if (a === b) return 100;
            var distance = levenshteinDistance(a, b);
            var maxLen = Math.max(a.length, b.length);
            return Math.max(0, Math.round((1 - distance / maxLen) * 100));
        }

        function findBestMatch(tracks, targetName, targetArtist) {
            if (!tracks || tracks.length === 0) return null;
            
            var scoredTracks = tracks.map(function(track) {
                var nameScore = calculateSimilarity(track.name, targetName);
                var artistScore = targetArtist ? calculateSimilarity(track.artist, targetArtist) : 50;
                var totalScore = nameScore * 0.6 + artistScore * 0.4;
                
                if (targetArtist && track.artist) {
                    var artistLower = track.artist.toLowerCase();
                    var targetArtistLower = targetArtist.toLowerCase();
                    if (artistLower.includes(targetArtistLower) || targetArtistLower.includes(artistLower)) {
                        totalScore += 20;
                    }
                }
                
                if (track.name && targetName) {
                    var nameLower = track.name.toLowerCase();
                    var targetNameLower = targetName.toLowerCase();
                    if (nameLower.includes(targetNameLower) || targetNameLower.includes(nameLower)) {
                        totalScore += 15;
                    }
                }
                
                return {
                    track: track,
                    score: Math.min(totalScore, 100),
                    nameScore: nameScore,
                    artistScore: artistScore
                };
            });
            
            scoredTracks.sort(function(a, b) { return b.score - a.score; });
            
            console.log('[Music] 匹配结果排序:');
            scoredTracks.slice(0, 3).forEach(function(item, idx) {
                console.log('  #' + (idx + 1) + ' ' + item.track.name + ' - ' + item.track.artist + ' (总分:' + item.score + ', 歌名:' + item.nameScore + ', 歌手:' + item.artistScore + ')');
            });
            
            return scoredTracks[0].track;
        }

        while ((match = musicRegex.exec(text)) !== null) {
            try {
                var aiTrackInfo = JSON.parse(match[1]);

                if (!aiTrackInfo.name) {
                    console.warn('[Music Parser] 缺少 name 字段，跳过:', match[1]);
                    continue;
                }

                var query = (aiTrackInfo.name + ' ' + (aiTrackInfo.artist || '')).trim();

                if (query) {
                    var myEpoch = ++window._musicSearchEpoch;

                    var response = await fetch('/api/music/search?query=' + encodeURIComponent(query));
                    var result = await response.json();

                    if (myEpoch !== window._musicSearchEpoch) {
                        console.log('[Music] 指令搜索结果过时，已丢弃: "' + query + '"');
                        continue;
                    }

                    if (!result.success) {
                        console.error('[Music] Search API failed:', result.error);
                        if (window.showStatusToast) {
                            var failMsg = window.safeT ? window.safeT('music.searchFailed', '音乐搜索失败') : '音乐搜索失败';
                            window.showStatusToast(result.message || result.error || failMsg, 3000);
                        }
                        continue;
                    }

                    if (result.data && result.data.length > 0) {
                        var realTrack = findBestMatch(result.data, aiTrackInfo.name, aiTrackInfo.artist);
                        if (!realTrack) {
                            console.warn('[Music] 智能匹配失败，使用第一条结果');
                            realTrack = result.data[0];
                        }
                        console.log('[Music] 指令搜歌最终选择:', realTrack.name, '-', realTrack.artist);

                        if (typeof window.dispatchMusicPlay === 'function') {
                            window.dispatchMusicPlay(realTrack);
                        } else {
                            console.warn('[Music] dispatchMusicPlay 不可用，尝试直接发送');
                            window.sendMusicMessage(realTrack);
                        }
                    } else {
                        if (window.showStatusToast) {
                            var defaultStr = '找不到歌曲: ' + aiTrackInfo.name;
                            var notFoundMsg = window.t ? window.t('music.notFound', {
                                query: aiTrackInfo.name,
                                defaultValue: defaultStr
                            }) : defaultStr;

                            if (typeof notFoundMsg !== 'string') notFoundMsg = defaultStr;
                            window.showStatusToast(notFoundMsg, 3000);
                        }
                    }
                }
            } catch (e) {
                console.error('[Music Parser] 音乐指令解析或请求失败:', e);
            }
        }
    };

    // ======================== appendMessage ========================

    /**
     * 添加消息到聊天界面
     */
    function appendMessage(text, sender, isNewMessage, options) {
        if (typeof isNewMessage === 'undefined') isNewMessage = true;
        options = options || {};

        var chatContainer = S.dom.chatContainer;

        function isMergeMessagesEnabled() {
            if (typeof window.mergeMessagesEnabled !== 'undefined') return window.mergeMessagesEnabled;
            return S.mergeMessagesEnabled;
        }

        function normalizeGeminiText(s) {
            return (s || '').replace(/\r\n/g, '\n');
        }

        function cleanMusicFromChunk(rawText) {
            var s = normalizeGeminiText(rawText);
            if (window._pendingMusicCommand) {
                s = window._pendingMusicCommand + s;
                window._pendingMusicCommand = '';
            }
            var m = s.match(/\[[^\]]*$/);
            if (m) {
                var partial = m[0].toLowerCase();
                var target = "[play_music:";
                if (partial.startsWith(target) || target.startsWith(partial)) {
                    window._pendingMusicCommand = m[0];
                    s = s.slice(0, m.index);
                }
            }
            return s.replace(/\[play_music:[^\]]*(\]|$)/g, '');
        }

        function splitIntoSentences(buffer) {
            // 逐字符扫描，尽量兼容中英文标点与流式输入
            var sentences = [];
            var s = normalizeGeminiText(buffer);
            var start = 0;

            var isPunctForBoundary = function (ch) {
                return ch === '\u3002' || ch === '\uFF01' || ch === '\uFF1F' || ch === '!' || ch === '?' || ch === '.' || ch === '\u2026';
            };

            var isBoundary = function (ch, next) {
                if (ch === '\n') return true;
                // 连续标点只在最后一个标点处分段，避免 "！？"、"..." 被拆开
                if (isPunctForBoundary(ch) && next && isPunctForBoundary(next)) return false;
                // 流式输入：缓冲区末尾的标点暂不分段，等下一个 chunk 确认是否为连续标点
                if (isPunctForBoundary(ch) && !next) return false;
                if (ch === '\u3002' || ch === '\uFF01' || ch === '\uFF1F') return true;
                if (ch === '!' || ch === '?') return true;
                if (ch === '\u2026') return true;
                if (ch === '.') {
                    // 英文句点：尽量避免把小数/缩写切断，要求后面是空白/换行/结束/常见结束符
                    if (!next) return true;
                    return /\s|\n|["')\]]/.test(next);
                }
                return false;
            };

            for (var i = 0; i < s.length; i++) {
                var ch = s[i];
                var next = i + 1 < s.length ? s[i + 1] : '';
                if (isBoundary(ch, next)) {
                    var piece = s.slice(start, i + 1);
                    var trimmed = piece.replace(/^\s+/, '').replace(/\s+$/, '');
                    if (trimmed) sentences.push(trimmed);
                    start = i + 1;
                }
            }

            var rest = s.slice(start);
            return { sentences: sentences, rest: rest };
        }

        function looksLikeStructuredRichText(text) {
            var s = normalizeGeminiText(text || '');
            return /```[\s\S]*```/.test(s)
                || /(?:^|\n)```/.test(s)
                || /\$\$[\s\S]*\$\$/.test(s)
                || /(?<!\$)\$(?!\$)[^$\n]+(?<!\$)\$(?!\$)/.test(s)
                || /(?:^|\n)\s{0,3}(?:#{1,6}\s|[-*+]\s|\d+\.\s|>\s)/.test(s)
                || /(?:^|\n)\|.+\|.+(?:\n|\r\n)\|(?:[-: ]+\|){1,}/.test(s);
        }

        function renderStructuredGeminiMessage(fullText) {
            var cleanFullText = normalizeGeminiText(fullText).replace(/\[play_music:[^\]]*(\]|$)/g, '').trim();
            if (!cleanFullText) return;

            // 进入 structured 模式前，收拢本轮旧的拟真气泡（保留最后一个用于升级）
            if (window.currentTurnGeminiBubbles && window.currentTurnGeminiBubbles.length > 1) {
                var host = getReactChatHost();
                var oldBubbles = window.currentTurnGeminiBubbles.slice(0, -1);
                for (var bi = 0; bi < oldBubbles.length; bi++) {
                    var oldBubble = oldBubbles[bi];
                    // 移除 React 镜像消息
                    if (host && typeof host.removeMessage === 'function' && oldBubble.dataset && oldBubble.dataset.reactChatMessageId) {
                        host.removeMessage(oldBubble.dataset.reactChatMessageId);
                    }
                    // 移除 DOM 节点
                    if (oldBubble.parentNode) {
                        oldBubble.parentNode.removeChild(oldBubble);
                    }
                }
                window.currentTurnGeminiBubbles = [window.currentTurnGeminiBubbles[window.currentTurnGeminiBubbles.length - 1]];
            }

            if (!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0 ||
                !window.currentGeminiMessage || !window.currentGeminiMessage.isConnected) {
                var msgDiv = document.createElement('div');
                msgDiv.classList.add('message', 'gemini');
                msgDiv.textContent = "[" + getCurrentTimeString() + "] \u{1F380} " + cleanFullText;
                chatContainer.appendChild(msgDiv);
                appendReactTextMessage(msgDiv, 'assistant', getCurrentAssistantName(), msgDiv.textContent, 'streaming');

                window.currentGeminiMessage = msgDiv;
                window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
                window.currentTurnGeminiBubbles.push(msgDiv);

                checkAndShowSubtitlePrompt(cleanFullText);

                if (isFirstAIResponse) {
                    isFirstAIResponse = false;
                    console.log(window.t('console.aiFirstReplyDetected'));
                    checkAndUnlockFirstDialogueAchievement();
                }
                return;
            }

            var timePrefix = window.currentGeminiMessage.textContent.match(/^\[\d{2}:\d{2}:\d{2}\] \u{1F380} /);
            if (!timePrefix) {
                timePrefix = "[" + getCurrentTimeString() + "] \u{1F380} ";
            } else {
                timePrefix = timePrefix[0];
            }
            window.currentGeminiMessage.textContent = timePrefix + cleanFullText;
            updateReactTextMessage(window.currentGeminiMessage, 'assistant', getCurrentAssistantName(), window.currentGeminiMessage.textContent, 'streaming');
        }

        // 维护"本轮 AI 回复"的完整文本（用于 turn end 时整段翻译/情感分析）
        if (sender === 'gemini') {
            if (isNewMessage) {
                window._realisticGeminiVersion = (window._realisticGeminiVersion || 0) + 1;
                window._geminiTurnFullText = '';
                window._pendingMusicCommand = '';
                window._structuredGeminiStreaming = false;
                // ========== 重置本轮气泡追踪 ==========
                window.currentTurnGeminiBubbles = [];
                window.currentTurnGeminiAttachments = [];
            }
            var prevFull = typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '';
            window._geminiTurnFullText = prevFull + normalizeGeminiText(text);
        }

        if (sender === 'gemini' && !isMergeMessagesEnabled()) {
            // 拟真输出（合并消息关闭）：流式内容先缓冲，按句号/问号/感叹号/换行等切分，每句一个气泡
            if (isNewMessage) {
                window._realisticGeminiBuffer = '';
                window._realisticGeminiQueue = []; // 新一轮开始时，清空队列
                window._lastBubbleTime = 0; // 重置时间戳，第一句立即显示
                window._pendingMusicCommand = ''; // 新一轮开始时，清空待闭合的音乐指令
            }

            var incoming = normalizeGeminiText(text);

            // 处理未闭合的音乐指令片段
            if (window._pendingMusicCommand) {
                incoming = window._pendingMusicCommand + incoming;
                window._pendingMusicCommand = '';
            }

            // 捕获字符串末尾尚未闭合的任意中括号块（防止 JSON 片段泄漏到聊天气泡）
            var openBracketMatch = incoming.match(/\[[^\]]*$/);
            if (openBracketMatch) {
                var partialText = openBracketMatch[0];
                var normalizedPartial = normalizeGeminiText(partialText).toLowerCase();

                // 这样即使只收到 "[" 或 "[pl"，或者已经包含了部分 JSON 体
                var targetPrefix = "[play_music:";
                var isPlayMusicPrefix =
                    normalizedPartial.startsWith(targetPrefix) ||
                    targetPrefix.startsWith(normalizedPartial);

                if (isPlayMusicPrefix) {
                    window._pendingMusicCommand = partialText;
                    incoming = incoming.slice(0, openBracketMatch.index);
                    console.log('[Music] 拦截到不完整指令片段: ' + partialText);
                }
            }

            var prev = typeof window._realisticGeminiBuffer === 'string' ? window._realisticGeminiBuffer : '';
            var combined = prev + incoming;
            combined = combined.replace(/\[play_music:[^\]]*(\]|$)/g, '');

            var fullTurnText = (typeof window._geminiTurnFullText === 'string' ? window._geminiTurnFullText : '')
                .replace(/\[play_music:[^\]]*(\]|$)/g, '');

            if (looksLikeStructuredRichText(fullTurnText) || looksLikeStructuredRichText(combined)) {
                window._structuredGeminiStreaming = true;
                window._realisticGeminiBuffer = combined;
                window._realisticGeminiQueue = [];
                renderStructuredGeminiMessage(fullTurnText || combined);
                var _wrapStructured = chatContainer.parentElement;
                if (_wrapStructured) {
                    _wrapStructured.scrollTop = _wrapStructured.scrollHeight;
                }
                return;
            }

            var splitResult = splitIntoSentences(combined);
            window._realisticGeminiBuffer = splitResult.rest;

            if (splitResult.sentences.length > 0) {
                window._realisticGeminiQueue = window._realisticGeminiQueue || [];
                window._realisticGeminiQueue.push.apply(window._realisticGeminiQueue, splitResult.sentences);
                processRealisticQueue(window._realisticGeminiVersion || 0);
            }
        } else if (sender === 'gemini' && isMergeMessagesEnabled() && isNewMessage) {
            // 合并消息开启：新一轮开始时，清空拟真缓冲，防止残留
            window._realisticGeminiBuffer = '';
            window._realisticGeminiQueue = [];
            window._lastBubbleTime = 0;

            // 1. 清洗文本（含未闭合指令片段的拦截）
            var cleanNewText = cleanMusicFromChunk(text);

            // 2. 只有当清洗后还有实质性文本时，才去创建气泡 DOM；否则清空指针以避免误追加
            if (cleanNewText.trim()) {
                var messageDiv = document.createElement('div');
                messageDiv.classList.add('message', 'gemini');
                messageDiv.textContent = "[" + getCurrentTimeString() + "] \u{1F380} " + cleanNewText;

                chatContainer.appendChild(messageDiv);
                appendReactTextMessage(messageDiv, 'assistant', getCurrentAssistantName(), messageDiv.textContent, 'streaming');
                window.currentGeminiMessage = messageDiv;

                // ========== 追踪本轮气泡 ==========
                window.currentTurnGeminiBubbles.push(messageDiv);
            } else {
                window.currentGeminiMessage = null;
            }

            // 3. 对干净的文本调用字幕检测
            checkAndShowSubtitlePrompt(cleanNewText);

            if (isFirstAIResponse) {
                isFirstAIResponse = false;
                console.log(window.t('console.aiFirstReplyDetected'));
                checkAndUnlockFirstDialogueAchievement();
            }
        } else if (sender === 'gemini' && isMergeMessagesEnabled()) {
            // 【核心重构】不再依赖 isNewMessage 标志，而是根据"本轮是否已有气泡"来决策。
            // 解决首个 chunk 被清洗为空（纯指令）时导致的渲染坠落 Bug
            var cleanText = cleanMusicFromChunk(text);

            // 场景 A: 本轮尚未创建气泡
            if (!window.currentTurnGeminiBubbles || window.currentTurnGeminiBubbles.length === 0) {
                if (cleanText.trim()) {
                    var msgDiv = document.createElement('div');
                    msgDiv.classList.add('message', 'gemini');
                    msgDiv.textContent = "[" + getCurrentTimeString() + "] \u{1F380} " + cleanText;
                    chatContainer.appendChild(msgDiv);
                    appendReactTextMessage(msgDiv, 'assistant', getCurrentAssistantName(), msgDiv.textContent, 'streaming');

                    window.currentGeminiMessage = msgDiv;
                    window.currentTurnGeminiBubbles = window.currentTurnGeminiBubbles || [];
                    window.currentTurnGeminiBubbles.push(msgDiv);

                    checkAndShowSubtitlePrompt(cleanText);
                } else {
                    // 仅有指令无文本，继续保持指针为空，直到出现有意义的文本块
                    window.currentGeminiMessage = null;
                }
            }
            // 场景 B: 气泡已存在，执行平滑追加
            else if (window.currentGeminiMessage && window.currentGeminiMessage.isConnected) {
                var fullText = window._geminiTurnFullText.replace(/\[play_music:[^\]]*(\]|$)/g, '');


                // var timePrefix = window.currentGeminiMessage.textContent.match(/^\[\d{2}:\d{2}:\d{2}\] \u{1F380} /) || [""];
                // window.currentGeminiMessage.textContent = timePrefix[0] + fullText;
                var timePrefix = window.currentGeminiMessage.textContent.match(/^\[\d{2}:\d{2}:\d{2}\] \u{1F380} /);
                if (!timePrefix) {
                    timePrefix = "[" + getCurrentTimeString() + "] \u{1F380} ";
                } else {
                    timePrefix = timePrefix[0];
                }
                window.currentGeminiMessage.textContent = timePrefix + fullText;
                updateReactTextMessage(window.currentGeminiMessage, 'assistant', getCurrentAssistantName(), window.currentGeminiMessage.textContent, 'streaming');

                
                // 触发字幕检测逻辑（防抖）
                if (S.subtitleCheckDebounceTimer) {
                    clearTimeout(S.subtitleCheckDebounceTimer);
                }

                S.subtitleCheckDebounceTimer = setTimeout(function () {
                    if (!window.currentGeminiMessage ||
                        window.currentGeminiMessage.nodeType !== Node.ELEMENT_NODE ||
                        !window.currentGeminiMessage.isConnected) {
                        S.subtitleCheckDebounceTimer = null;
                        return;
                    }

                    var currentFullText = window.currentGeminiMessage.textContent.replace(/^\[\d{2}:\d{2}:\d{2}\] \u{1F380} /, '');
                    if (currentFullText && currentFullText.trim()) {
                        if (typeof userLanguage !== 'undefined' && userLanguage === null) {
                            getUserLanguage().then(function () {
                                if (window.currentGeminiMessage && window.currentGeminiMessage.isConnected) {
                                    var detectedLang = detectLanguage(currentFullText);
                                    if (detectedLang !== 'unknown' && detectedLang !== userLanguage) {
                                        showSubtitlePrompt();
                                    }
                                }
                            }).catch(function (err) { console.warn('[i18n] Stream error:', err); });
                        } else {
                            var detectedLang = detectLanguage(currentFullText);
                            if (detectedLang !== 'unknown' && typeof userLanguage !== 'undefined' && detectedLang !== userLanguage) {
                                showSubtitlePrompt();
                            }
                        }
                    }
                    S.subtitleCheckDebounceTimer = null;
                }, 300);
            }
        } else {
            // 创建新消息 (user / 其他 sender)
            var newDiv = document.createElement('div');
            newDiv.classList.add('message', sender);

            // 根据sender设置不同的图标
            var icon = sender === 'user' ? '\u{1F4AC}' : '\u{1F380}';
            var cleanedText = (text || '').replace(/\[play_music:[^\]]*(\]|$)/g, '');
            newDiv.textContent = "[" + getCurrentTimeString() + "] " + icon + " " + cleanedText;
            chatContainer.appendChild(newDiv);
            if (!options.skipReactSync) {
                appendReactTextMessage(
                    newDiv,
                    sender === 'user' ? 'user' : 'assistant',
                    sender === 'user' ? getCurrentUserName() : getCurrentAssistantName(),
                    newDiv.textContent
                );
            }

            // 如果是Gemini消息，更新当前消息引用
            if (sender === 'gemini') {
                window.currentGeminiMessage = newDiv;
                // ========== 追踪本轮气泡 ==========
                window.currentTurnGeminiBubbles.push(newDiv);

                // 检测AI消息的语言，如果与用户语言不同，显示字幕提示框
                checkAndShowSubtitlePrompt(cleanedText);

                // 如果是AI第一次回复，更新状态并检查成就
                if (isFirstAIResponse) {
                    isFirstAIResponse = false;
                    console.log('\u68C0\u6D4B\u5230AI\u7B2C\u4E00\u6B21\u56DE\u590D');
                    checkAndUnlockFirstDialogueAchievement();
                }
            }
        }
        // 仅在用户已处于底部附近时自动滚动
        var _wrap = chatContainer.parentElement;
        if (_wrap && _wrap.scrollHeight - _wrap.scrollTop - _wrap.clientHeight < 60) {
            _wrap.scrollTop = _wrap.scrollHeight;
        }
    }

    // ======================== 导出 ========================

    mod.getCurrentTimeString = getCurrentTimeString;
    mod.createGeminiBubble = createGeminiBubble;
    mod.processRealisticQueue = processRealisticQueue;
    mod.appendMessage = appendMessage;
    mod.appendReactUserMessage = appendReactUserMessage;
    mod.checkAndUnlockFirstDialogueAchievement = checkAndUnlockFirstDialogueAchievement;
    mod.setReactMessageStatus = setReactMessageStatus;
    mod.ensureUserDisplayName = ensureUserDisplayName;

    /**
     * 标记用户已完成首次输入（供外部模块调用）
     */
    mod.markFirstUserInput = function () {
        if (isFirstUserInput) {
            isFirstUserInput = false;
        }
    };

    /**
     * 查询首次输入/回复状态（供外部模块调用）
     */
    mod.isFirstUserInput = function () { return isFirstUserInput; };
    mod.isFirstAIResponse = function () { return isFirstAIResponse; };

    // 向后兼容：旧代码中直接使用 window.appendMessage 等
    window.appendMessage = appendMessage;
    window.createGeminiBubble = createGeminiBubble;
    window.processRealisticQueue = processRealisticQueue;
    window.checkAndUnlockFirstDialogueAchievement = checkAndUnlockFirstDialogueAchievement;
    window.getCurrentTimeString = getCurrentTimeString;
    window.setReactMessageStatus = setReactMessageStatus;

    window.addEventListener('chat-avatar-preview-updated', refreshReactAssistantAvatars);
    window.addEventListener('chat-avatar-preview-cleared', refreshReactAssistantAvatars);

    // 音乐搜索纪元：向后兼容全局变量（原来定义在 app.js IIFE 外部的 currentMusicSearchEpoch）
    if (typeof window._musicSearchEpoch === 'undefined') {
        window._musicSearchEpoch = 0;
    }

    window.appChat = mod;
})();
