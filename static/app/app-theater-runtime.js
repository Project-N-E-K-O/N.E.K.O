/** Numeric v2 小剧场在 N.E.K.O 本体中的页面级编排器。 */
(function () {
    'use strict';

    var api = {
        session: '/api/theater-numeric/session',
        input: '/api/theater-numeric/session/input',
        end: '/api/theater-numeric/session/end',
        speakBlock: '/api/theater-numeric/session/speak-block'
    };
    var POINTER_KEY = 'neko.theater.numeric.v2.capsule-pointer.v1';
    // 本体运行时只消费共享传输协议；胶囊状态、回放和跨窗口目标仍由本模块负责。
    var transport = window.nekoTheaterTransport;
    if (!transport) throw new Error('numeric_theater_transport_unavailable');
    var MESSAGE_SCHEMA = transport.MESSAGE_SCHEMA;
    var createId = transport.createId;
    var requestJson = transport.requestJson;
    // 旧 Session 可能已保存过去的空桥段占位句；只在转场桥段中精确隐藏，不改写正式演绎记录。
    var LEGACY_EMPTY_TRANSITION_BRIDGE = '时间向前流转，现场随之转换。';
    var state = {
        active: false, phase: 'inactive', storyId: '', storyTitle: '', sessionId: '', revision: 0, lifecycleRevision: 0,
        playerName: '', catgirlName: '',
        sessionStatus: '', scene: null, history: [], currentBlock: null, suggestedInputs: [],
        queueToken: 0, pendingTurn: null, pendingEnd: null, channel: null, hostReadyTimer: 0,
        draftRestore: null, ordinaryDraftRestore: null, presentationSeq: 0, composerVisibilityRestore: null,
        chatSurfaceModeRestore: null,
        errorMessage: '', tokenUsage: null
    };
    var launchRequests = Object.create(null);
    var launchRequestOrder = [];
    var launchReplyTargets = Object.create(null);
    var desktopLaunchRelayTimers = Object.create(null);
    var launchEpoch = 0;
    var pendingLaunch = null;
    var endConfirmationPending = false;
    var committedSnapshot = null;

    function t(key, fallback) {
        if (typeof window.t === 'function') {
            var value = window.t(key);
            if (typeof value === 'string' && value && value !== key) return value;
        }
        return fallback;
    }
    function postMessage(message) {
        var payload = transport.createMessage('theater-runtime', message);
        if (state.channel) { try { state.channel.postMessage(payload); } catch (_) {} }
        return payload;
    }
    function postDirect(target, message) {
        if (!target || typeof target.postMessage !== 'function') return false;
        try { target.postMessage(message, window.location.origin); return true; } catch (_) { return false; }
    }
    function desktopRuntimeRole() {
        var body = document.body;
        // 桌面端本体页和独立聊天页都会加载本 Runtime；角色必须由现有宿主标记确定，
        // 不能按剧本或窗口名称硬编码，否则会再次出现正文写入不可见窗口的问题。
        if (window.__LANLAN_IS_ELECTRON_PET__ === true) return 'pet';
        if (!body || !body.classList.contains('neko-electron-runtime')) return '';
        if (!body.classList.contains('electron-chat-window')) return '';
        return String(body.getAttribute('data-chat-host-kind') || 'compact');
    }
    function stopDesktopLaunchRelay(launchId) {
        var key = String(launchId || '');
        var timer = desktopLaunchRelayTimers[key];
        if (timer) window.clearInterval(timer);
        delete desktopLaunchRelayTimers[key];
    }
    function relayLaunchToDesktopChat(message) {
        var launchId = String(message && message.launch_id || '');
        if (!launchId || !state.channel) return false;
        stopDesktopLaunchRelay(launchId);
        var attempts = 0;
        function relay() {
            attempts += 1;
            // Electron Pet 只负责把选剧页的启动交给真正可见的紧凑胶囊窗口；
            // 同一 launch_id 在目标 Runtime 内幂等，短时重发只用于覆盖聊天页尚在加载的竞态。
            postMessage(Object.assign({}, message, { runtime_host_kind: 'compact' }));
            if (attempts >= 40) stopDesktopLaunchRelay(launchId);
        }
        relay();
        desktopLaunchRelayTimers[launchId] = window.setInterval(relay, 200);
        return true;
    }
    function rememberPointer() {
        try {
            // 演绎指针只服务当前程序生命周期内的页面刷新；完整退出后必须回到普通模式。
            // Session 和 Ledger 仍由后端保存，玩家下次可从剧本页主动“继续演绎”。
            if (!state.active) { window.sessionStorage.removeItem(POINTER_KEY); return; }
            window.sessionStorage.setItem(POINTER_KEY, JSON.stringify({ story_id: state.storyId, session_id: state.sessionId }));
        } catch (_) {}
    }
    function readPointer() {
        try {
            var value = JSON.parse(window.sessionStorage.getItem(POINTER_KEY) || 'null');
            return value && value.story_id && value.session_id ? value : null;
        } catch (_) { return null; }
    }
    function host() { return window.reactChatWindowHost || null; }
    function captureOrdinaryDraft(chatHost) {
        if (state.active) return;
        state.ordinaryDraftRestore = null;
        var snapshot = chatHost && typeof chatHost.getState === 'function' ? chatHost.getState() : {};
        // 猫娘本地聊天有自己的草稿状态，不能误当作普通聊天草稿保存。
        if (snapshot.viewProps && snapshot.viewProps.catLocalTextOnly) return;
        var input = document.querySelector('#react-chat-window-root .composer-input');
        if (!input || typeof input.value !== 'string') return;
        // 主页面初始化模型时可能重挂 React；退出小剧场时用该快照恢复普通聊天草稿。
        state.ordinaryDraftRestore = { id: createId('theater_ordinary_draft_'), text: input.value };
    }
    function captureChatSurfaceMode(chatHost) {
        if (state.chatSurfaceModeRestore !== null || !chatHost || typeof chatHost.getChatSurfaceMode !== 'function') return;
        state.chatSurfaceModeRestore = String(chatHost.getChatSurfaceMode() || 'compact');
    }
    function restoreChatSurfaceMode(chatHost) {
        var mode = state.chatSurfaceModeRestore;
        state.chatSurfaceModeRestore = null;
        if (!mode || !chatHost || typeof chatHost.setChatSurfaceMode !== 'function') return;
        // 小剧场只临时占用胶囊界面，退出后恢复用户进入前选择的聊天形态。
        chatHost.setChatSurfaceMode(mode);
    }
    function claimComposerVisibility(chatHost) {
        if (!state.active || !chatHost) return;
        if (!state.composerVisibilityRestore) {
            var snapshot = typeof chatHost.getState === 'function' ? chatHost.getState() : {};
            state.composerVisibilityRestore = {
                composerHidden: !!snapshot.composerHiddenRequested,
                goodbyeComposerHidden: !!snapshot.goodbyeComposerHidden
            };
        }
        // 快照只采集一次，但剧场活跃期间每次渲染都要重新声明输入区可见。
        if (typeof chatHost.setComposerHidden === 'function') chatHost.setComposerHidden(false);
        if (typeof chatHost.setGoodbyeComposerHidden === 'function') chatHost.setGoodbyeComposerHidden(false);
    }
    function restoreComposerVisibility(chatHost) {
        var snapshot = state.composerVisibilityRestore;
        state.composerVisibilityRestore = null;
        if (!snapshot || !chatHost) return;
        if (typeof chatHost.setComposerHidden === 'function') chatHost.setComposerHidden(snapshot.composerHidden);
        if (typeof chatHost.setGoodbyeComposerHidden === 'function') chatHost.setGoodbyeComposerHidden(snapshot.goodbyeComposerHidden);
    }
    function claimAudioPlayback() {
        var audio = window.appAudioPlayback;
        if (audio && typeof audio.clearAudioQueueWithoutDecoderReset === 'function') {
            audio.clearAudioQueueWithoutDecoderReset();
        }
    }
    async function stopOrdinaryVoiceInput() {
        var sharedState = window.appState || {};
        var voiceStartWasPending = sharedState.voiceStartPending === true
            || window.isMicStarting === true;
        var voiceWasActive = sharedState.isRecording === true
            || sharedState.voiceChatActive === true
            || voiceStartWasPending;
        if (!voiceWasActive) return true;
        var capture = window.appAudioCapture || {};
        var stopCapture = typeof capture.stopMicCapture === 'function'
            ? capture.stopMicCapture
            : window.stopMicCapture;
        if (typeof stopCapture !== 'function') return false;
        var recordingWasActive = sharedState.isRecording === true;
        if (voiceStartWasPending) {
            // 停麦只能清理采集资源；必须先推进语音启动世代，阻止等待中的旧协程稍后重新开麦。
            if (typeof window.cancelPendingSessionStart !== 'function') return false;
            window.cancelPendingSessionStart('Voice start cancelled by theater');
        }
        try {
            await stopCapture();
        } catch (_) {
            return false;
        }
        if (!recordingWasActive) {
            // 语音 Session 可能已启动但麦克风仍在准备；此时停麦不会发送 pause，需要显式收口后端。
            var websocket = window.appWebSocket;
            if (!websocket || typeof websocket.send !== 'function') return false;
            websocket.send({ action: 'pause_session' });
        }
        return true;
    }
    var TYPEWRITER_INTERVAL_MS = 32;
    function historyEntry(id, type, text, author, displayKind, status) {
        return {
            id: id,
            type: type,
            text: String(text || '').trim(),
            author: author || undefined,
            displayKind: displayKind || undefined,
            status: status || undefined
        };
    }
    function narrationDisplayKind(phase) {
        // 普通互动和来源回应使用括号微动作；开场与换场桥保留独立场景旁白。
        return phase === 'ordinary' || phase === 'source_response' ? 'action' : 'scene';
    }
    function presentationBlock(type, text, phase) {
        var block = { type: type, text: text };
        if (type === 'narration') block.displayKind = narrationDisplayKind(phase);
        return block;
    }
    function mixedPerformanceBlocks(value, phase) {
        // 新合同只让模型输出一个混合字符串；这里按括号确定性拆分，供逐字展示和 TTS 复用。
        var source = String(value || '').trim();
        if (!source) return [];
        var pairs = { '（': '）', '(': ')' };
        var closers = { '）': true, ')': true };
        var blocks = [];
        var segmentStart = 0;
        var actionStart = -1;
        var expectedClose = '';
        function append(type, rawText, text) {
            if (!String(text || '').trim()) return;
            var block = presentationBlock(type, String(text).trim(), phase);
            // displayText 保留模型原始穿插形式；动作始终属于猫娘气泡，不继承 opening 的场景样式。
            block.displayText = rawText;
            block.preserveSpacing = true;
            if (type === 'narration') block.displayKind = 'action';
            blocks.push(block);
        }
        for (var index = 0; index < source.length; index += 1) {
            var char = source[index];
            if (expectedClose) {
                if (Object.prototype.hasOwnProperty.call(pairs, char)) return [];
                if (closers[char]) {
                    if (char !== expectedClose) return [];
                    append('narration', source.slice(actionStart, index + 1), source.slice(segmentStart, index));
                    expectedClose = '';
                    segmentStart = index + 1;
                }
                continue;
            }
            if (Object.prototype.hasOwnProperty.call(pairs, char)) {
                append('dialogue', source.slice(segmentStart, index), source.slice(segmentStart, index));
                actionStart = index;
                segmentStart = index + 1;
                expectedClose = pairs[char];
                continue;
            }
            if (closers[char]) return [];
        }
        if (expectedClose) return [];
        append('dialogue', source.slice(segmentStart), source.slice(segmentStart));
        return blocks;
    }
    function formatPresentationBlock(block) {
        if (block && Object.prototype.hasOwnProperty.call(block, 'displayText')) return String(block.displayText || '');
        var text = String(block && block.text || '').trim();
        if (!text || block.type !== 'narration' || block.displayKind !== 'action') return text;
        var wrapped = (text.startsWith('（') && text.endsWith('）'))
            || (text.startsWith('(') && text.endsWith(')'));
        return wrapped ? text : '（' + text + '）';
    }
    function contentBlocks(performance, fallbackPhase) {
        if (!performance || typeof performance !== 'object') return [];
        var containers = Array.isArray(performance.segments) ? performance.segments : [performance];
        var blocks = [];
        containers.forEach(function (container) {
            var phase = String(container && container.phase || fallbackPhase || '').trim();
            // 旧的换场记录没有 segments 时，宁可保留独立旁白，也不能把整段换场包装成微动作。
            if (!phase) phase = performance.transition_delivered ? 'transition_bridge' : 'ordinary';
            if (container && (Object.prototype.hasOwnProperty.call(container, 'scene_narration')
                || Object.prototype.hasOwnProperty.call(container, 'performance'))) {
                var sceneNarration = String(container.scene_narration || '').trim();
                if (phase === 'transition_bridge' && sceneNarration === LEGACY_EMPTY_TRANSITION_BRIDGE) {
                    sceneNarration = '';
                }
                if (sceneNarration) blocks.push(presentationBlock('narration', sceneNarration, 'scene'));
                // Fixed text is committed by the runtime, outside the actor/TTS body.
                var fixedNarrations = Array.isArray(container.fixed_narrations) ? container.fixed_narrations : [];
                fixedNarrations.filter(function (item) { return item.position === 'before'; }).forEach(function (item) {
                    blocks.push(presentationBlock('narration', item.text, 'scene'));
                });
                mixedPerformanceBlocks(container.performance, phase).forEach(function (block) { blocks.push(block); });
                fixedNarrations.filter(function (item) { return item.position === 'after'; }).forEach(function (item) {
                    blocks.push(presentationBlock('narration', item.text, 'scene'));
                });
                return;
            }
            var raw = Array.isArray(container && container.content) ? container.content : null;
            if (raw) {
                raw.forEach(function (block) {
                    var type = block && block.type;
                    var text = String(block && block.text || '').trim();
                    if (text && type === 'action') {
                        // Legacy ordered actions retain character-bubble
                        // formatting even in an opening or transition bridge.
                        blocks.push(presentationBlock('narration', text, 'ordinary'));
                        return;
                    }
                    if (text && (type === 'narration' || (type === 'dialogue' && block.speaker_id === 'active_catgirl'))) {
                        blocks.push(presentationBlock(type, text, phase));
                    }
                });
                return;
            }
            var narration = String(container && container.narration || '').trim();
            if (narration) blocks.push(presentationBlock('narration', narration, phase));
            (Array.isArray(container && container.dialogue) ? container.dialogue : []).forEach(function (line) {
                var text = String(line && line.text || '').trim();
                if (text && line.speaker_id === 'active_catgirl') blocks.push(presentationBlock('dialogue', text, phase));
            });
        });
        return blocks;
    }
    function performanceHistoryGroups(performance, fallbackPhase) {
        var groups = [];
        contentBlocks(performance, fallbackPhase).forEach(function (block, blockIndex) {
            // 开场和换场场景旁白沿用独立旁白气泡；场景内微动作才与对白合并。
            if (block.type === 'narration' && block.displayKind === 'scene') {
                groups.push({ type: 'narration', blocks: [{ block: block, blockIndex: blockIndex }] });
                return;
            }
            var current = groups[groups.length - 1];
            if (!current || current.type !== 'dialogue') {
                current = { type: 'dialogue', blocks: [] };
                groups.push(current);
            }
            current.blocks.push({ block: block, blockIndex: blockIndex });
            if (block.preserveSpacing) current.preserveSpacing = true;
        });
        return groups;
    }
    function historyGroupText(group) {
        return group.blocks.map(function (item) { return formatPresentationBlock(item.block); })
            .filter(Boolean)
            .join(group.preserveSpacing ? '' : '\n');
    }
    function buildCommittedHistory(snapshot) {
        var session = snapshot.session || {};
        var result = [];
        performanceHistoryGroups(session.opening_performance, 'opening').forEach(function (group, groupIndex) {
            var openingText = historyGroupText(group);
            if (!openingText) return;
            result.push(historyEntry(
                'opening-performance-' + groupIndex,
                group.type,
                openingText,
                group.type === 'dialogue' ? state.catgirlName : undefined,
                group.type === 'narration' ? 'scene' : undefined
            ));
        });
        (Array.isArray(session.performance_history) ? session.performance_history : []).forEach(function (record, recordIndex) {
            var revision = Number(record.revision || recordIndex + 1);
            var input = String(record.input_text || '').trim();
            if (input) result.push(historyEntry('player-' + revision, 'player_action', input, state.playerName));
            performanceHistoryGroups(record, 'ordinary').forEach(function (group, groupIndex) {
                var performanceText = historyGroupText(group);
                if (!performanceText) return;
                result.push(historyEntry(
                    'performance-' + revision + '-' + groupIndex,
                    group.type,
                    performanceText,
                    group.type === 'dialogue' ? state.catgirlName : undefined,
                    group.type === 'narration' ? 'scene' : undefined
                ));
            });
        });
        if (snapshot.scene && snapshot.scene.terminal && snapshot.scene.ending) {
            var ending = snapshot.scene.ending;
            result.push(historyEntry('ending-' + session.session_id, 'ending', [ending.title, ending.summary].filter(Boolean).join('：')));
        }
        return result;
    }
    // 用量只属于最近一次请求，不混入可导出的剧情历史；缺报时明确显示已知部分。
    function usagePresentation() {
        var usage = state.tokenUsage;
        if (!usage || !Array.isArray(usage.calls)) return null;
        function line(key, fallback, values) {
            return t(key, fallback).replace(/\{(\w+)\}/g, function (_, name) { return String(values[name]); });
        }
        var summary = line('theater.tokenUsageSummary', 'This request · input {input} · output {output} tokens · {calls} calls', {
            input: usage.input_tokens, output: usage.output_tokens, calls: usage.calls.length
        });
        if (!usage.complete) summary += ' · ' + t('theater.tokenUsagePartial', 'Partial usage; some calls were not reported');
        var detail = usage.calls.map(function (call, index) {
            // 按需查原文有独立费用，不能落入默认分支而被显示成演员调用。
            var stage = ['actor', 'suggestions', 'evaluator', 'review', 'dispute', 'history_lookup'].indexOf(call.stage) >= 0 ? call.stage : 'actor';
            return line('theater.tokenUsageCall', '{number}. {stage} · input {input} · output {output}', {
                number: index + 1, stage: t('theater.tokenStage_' + stage, stage),
                input: call.input_tokens == null ? '?' : call.input_tokens,
                output: call.output_tokens == null ? '?' : call.output_tokens
            });
        });
        detail.push(t('theater.tokenUsageHint', 'Provider usage includes cached input and reasoning output when reported. Extra calls are included; this is not a price estimate.'));
        return { summary: summary, detail: detail.join('\n') };
    }

    function presentation() {
        return {
            active: state.active,
            phase: state.phase,
            storyTitle: state.storyTitle,
            currentBlock: state.currentBlock,
            history: state.history.slice(),
            suggestedInputs: state.phase === 'awaiting_player' ? state.suggestedInputs.slice(0, 3) : [],
            busy: ['loading', 'evaluating', 'ending', 'returning_selector'].indexOf(state.phase) >= 0,
            sessionEnded: state.sessionStatus === 'ended',
            errorMessage: state.errorMessage,
            tokenUsage: usagePresentation(),
            draftRestore: state.draftRestore,
            ordinaryDraftRestore: state.ordinaryDraftRestore,
            presentationSeq: ++state.presentationSeq
        };
    }
    function render() {
        var chatHost = host();
        if (!chatHost || typeof chatHost.setViewProps !== 'function') return false;
        if (state.active) captureChatSurfaceMode(chatHost);
        claimComposerVisibility(chatHost);
        var compactState = state.active && state.phase === 'awaiting_player' ? 'input' : 'default';
        chatHost.setViewProps({
            theaterPresentation: presentation(),
            chatSurfaceMode: 'compact',
            compactChatState: compactState,
            composerDisabled: state.active && state.phase !== 'awaiting_player'
        });
        if (state.active && typeof chatHost.openWindow === 'function') chatHost.openWindow();
        return true;
    }
    function submitFromHost(text) {
        void submit(text, 'freeform').catch(function () {
            if (!state.active) return;
            state.phase = 'awaiting_player';
            state.errorMessage = t('theater.inputFailed', '暂时未能取得演绎回复，请重试。');
            render();
        });
    }
    function submitSuggestedFromHost(text) {
        void submit(text, 'suggestion').catch(function () {
            if (!state.active) return;
            state.phase = 'awaiting_player';
            state.errorMessage = t('theater.inputFailed', '暂时未能取得演绎回复，请重试。');
            render();
        });
    }
    function bindHostCallbacks() {
        var chatHost = host();
        if (!chatHost) return false;
        if (typeof chatHost.setOnTheaterSubmit === 'function') {
            // 玩家自由输入直接进入剧场 Runtime，不经过普通聊天或猫娘局部聊天路由。
            chatHost.setOnTheaterSubmit(submitFromHost);
        }
        if (typeof chatHost.setOnTheaterSuggestedInputSelect === 'function') {
            // 推荐输入直接进入 Runtime 提交流程，不借用输入框草稿或普通 Galgame 回填链路。
            chatHost.setOnTheaterSuggestedInputSelect(submitSuggestedFromHost);
        }
        if (typeof chatHost.setOnTheaterEnd === 'function') chatHost.setOnTheaterEnd(function () { runtime.requestEnd(); });
        render();
        return true;
    }
    function waitForHost() {
        if (bindHostCallbacks()) return Promise.resolve(true);
        return new Promise(function (resolve) {
            var attempts = 80;
            window.clearInterval(state.hostReadyTimer);
            state.hostReadyTimer = window.setInterval(function () {
                attempts -= 1;
                var ready = bindHostCallbacks();
                if (ready || attempts <= 0) {
                    window.clearInterval(state.hostReadyTimer); state.hostReadyTimer = 0; resolve(ready);
                }
            }, 100);
        });
    }
    function applySnapshot(snapshot) {
        var session = snapshot.session || {};
        var participants = snapshot.participants || {};
        state.storyId = String(session.story_package_id || state.storyId);
        state.sessionId = String(session.session_id || state.sessionId);
        state.revision = Number(session.revision || 0);
        state.lifecycleRevision = Number(session.lifecycle_revision || 0);
        state.sessionStatus = String(session.status || 'active');
        // 玩家和猫娘署名都由服务端当前绑定提供，恢复旧记录时也不回退成通用占位名。
        state.playerName = String(participants.player_name || t('theater.player', 'Player'));
        state.catgirlName = String(participants.catgirl_name || 'Neko');
        state.scene = snapshot.scene || null;
        state.storyTitle = String(snapshot.story_title || state.storyTitle || state.storyId);
        state.suggestedInputs = Array.isArray(snapshot.suggested_inputs) ? snapshot.suggested_inputs.map(String) : [];
        // 保留最近一次服务端已提交快照；表现播放被打断时可直接恢复完整历史和推荐输入。
        committedSnapshot = snapshot;
    }
    function readingDelay(text) { return Math.min(5000, Math.max(1100, Array.from(String(text || '')).length * 55)); }
    // 语音优先等播放完成事件；事件丢失时按完整对白保守估时，不能沿用读字的 5 秒上限。
    function speechTimeout(text) { return 5000 + Array.from(String(text || '')).length * 300; }
    function wait(ms, token) {
        return new Promise(function (resolve) {
            window.setTimeout(function () { resolve(token === state.queueToken); }, ms);
        });
    }
    function waitForSpeech(speechId, timeoutMs, token) {
        return new Promise(function (resolve) {
            var done = false;
            var timer;
            function finish() {
                if (done) return; done = true;
                window.clearTimeout(timer);
                window.removeEventListener('neko-assistant-speech-end', onEnd);
                window.removeEventListener('neko-assistant-speech-unavailable', onEnd);
                window.removeEventListener('neko-assistant-speech-cancel', onEnd);
                resolve(token === state.queueToken);
            }
            function onEnd(event) {
                var turnId = event && event.detail && event.detail.turnId;
                // Ordinary-session shutdown can emit an uncorrelated event;
                // only this theater utterance may release its playback wait.
                if (speechId && turnId && String(turnId) === String(speechId)) finish();
            }
            window.addEventListener('neko-assistant-speech-end', onEnd);
            window.addEventListener('neko-assistant-speech-unavailable', onEnd);
            window.addEventListener('neko-assistant-speech-cancel', onEnd);
            timer = window.setTimeout(finish, timeoutMs);
        });
    }
    async function typeBlock(historyId, block, token) {
        var entry = state.history.find(function (candidate) { return candidate.id === historyId; });
        if (!entry) return false;
        var text = formatPresentationBlock(block);
        var separator = entry.text && !block.preserveSpacing ? '\n' : '';
        var characters = Array.from(separator + text);
        for (var index = 0; index < characters.length; index += 1) {
            if (token !== state.queueToken) return false;
            entry.text += characters[index];
            render();
            if (!await wait(TYPEWRITER_INTERVAL_MS, token)) return false;
        }
        return token === state.queueToken;
    }
    async function playDialogue(group, block, blockIndex, revision, token) {
        var alive = true;
        if (block.type === 'dialogue') {
            var dialogueItems = group.blocks.filter(function (item) {
                return item.block.type === 'dialogue';
            });
            var dialogueBlockIndexes = dialogueItems.map(function (item) { return item.blockIndex; });
            var dialogueText = dialogueItems.map(function (item) { return item.block.text; }).join(' ');
            var result;
            try {
                result = await requestJson(api.speakBlock, { method: 'POST', body: {
                    story_id: state.storyId, session_id: state.sessionId, revision: revision, block_index: blockIndex,
                    lifecycle_revision: state.lifecycleRevision,
                    dialogue_block_indexes: dialogueBlockIndexes,
                    playback_request_id: 'theater_speech_' + state.sessionId + '_' + revision + '_' + state.lifecycleRevision + '_' + blockIndex
                }});
            } catch (_) {
                // TTS 是表现层旁路；请求失败时按阅读时长继续，不能中断正文播放或锁住输入。
                result = { ok: false };
            }
            if (result.ok && result.speech_id && (result.audio_queued || result.audio_sent)) alive = await waitForSpeech(result.speech_id, speechTimeout(dialogueText), token);
            else alive = await wait(readingDelay(dialogueText), token);
        }
        return alive && token === state.queueToken;
    }
    async function playPerformance(performance, revision, options) {
        var token = ++state.queueToken;
        var groups = performanceHistoryGroups(performance, options && options.displayPhase || 'ordinary');
        var nextSuggestedInputs = state.suggestedInputs.slice();
        state.phase = 'performing'; state.currentBlock = null; state.suggestedInputs = []; render();
        if (options && options.playerInput && !options.playerAlreadyShown) {
            state.history.push(historyEntry('player-' + revision, 'player_action', options.playerInput, state.playerName));
        }
        var historyBaseId = options && options.historyId || 'performance-' + revision;
        for (var groupIndex = 0; groupIndex < groups.length; groupIndex += 1) {
            var group = groups[groupIndex];
            var historyId = historyBaseId + '-' + groupIndex;
            state.history.push(historyEntry(
                historyId,
                group.type,
                '',
                group.type === 'dialogue' ? state.catgirlName : undefined,
                group.type === 'narration' ? 'scene' : undefined,
                'streaming'
            ));
            render();
            var speechPromise = null;
            for (var itemIndex = 0; itemIndex < group.blocks.length; itemIndex += 1) {
                var item = group.blocks[itemIndex];
                // 同一演绎段只在首个对白块发起一次合并 TTS；动作与后续对白仍按原顺序逐字显示。
                if (!speechPromise && item.block.type === 'dialogue') {
                    speechPromise = playDialogue(group, item.block, item.blockIndex, revision, token);
                }
                if (!await typeBlock(historyId, item.block, token)) return;
            }
            if (speechPromise && !await speechPromise) return;
            var completedEntry = state.history.find(function (entry) { return entry.id === historyId; });
            if (completedEntry) completedEntry.status = 'sent';
            render();
        }
        if (state.sessionStatus === 'ended') {
            if (state.scene && state.scene.ending) state.history.push(historyEntry('ending-' + state.sessionId, 'ending', [state.scene.ending.title, state.scene.ending.summary].filter(Boolean).join('：')));
            state.phase = 'ended';
        } else {
            state.phase = 'awaiting_player';
            state.suggestedInputs = nextSuggestedInputs;
        }
        render();
    }
    function isCurrentLaunch(launchToken, storyId, sessionId) {
        return launchToken === launchEpoch
            && state.active
            && state.storyId === storyId
            && state.sessionId === sessionId;
    }
    async function performLaunch(message, launchToken) {
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        var snapshot;
        try {
            snapshot = await requestJson(api.session + '/' + encodeURIComponent(nextSessionId) + '?story_id=' + encodeURIComponent(nextStoryId));
        } catch (_) {
            // 候选快照读取失败时还未接管全局状态，保留当前健康演绎并只结束本次启动。
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        // 多个选剧页可能交错启动；候选快照通过世代与 revision 校验后才有权接管当前运行态。
        if (launchToken !== launchEpoch) {
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        if (!snapshot.ok || !snapshot.session || Number(snapshot.session.revision) !== Number(message.revision)) {
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        // 小剧场只接管文本胶囊；必须先停掉普通语音 Session，避免 ASR 和普通回复穿插进演绎。
        if (!await stopOrdinaryVoiceInput() || launchToken !== launchEpoch) {
            delete launchReplyTargets[message.launch_id];
            return false;
        }
        var chatHost = host();
        captureOrdinaryDraft(chatHost);
        captureChatSurfaceMode(chatHost);
        if (state.storyId !== nextStoryId || state.sessionId !== nextSessionId) {
            // Reuse the capsule's draft projection for an accepted session change.
            // A failed launch or same-session replay must retain the current input.
            state.draftRestore = { id: createId('theater_draft_restore_'), text: '' };
        }
        if (state.active) {
            // 即使重新启动同一 Session，也必须先使旧正文和旧语音失效，避免两个播放协程交错写回。
            claimAudioPlayback();
            state.queueToken += 1;
            state.pendingTurn = null;
            state.currentBlock = null;
        }
        // 新快照已获准接管，旧提交的失败提示不再属于当前展示。
        state.errorMessage = '';
        state.tokenUsage = message.token_usage || null;
        // A validated replacement owns the selector handshake from this point.
        state.pendingEnd = null;
        state.active = true; state.phase = 'loading'; state.storyId = nextStoryId; state.sessionId = nextSessionId; render();
        applySnapshot(snapshot);
        state.history = buildCommittedHistory(snapshot);
        state.active = true;
        rememberPointer();
        var hostReady = await waitForHost();
        if (!isCurrentLaunch(launchToken, nextStoryId, nextSessionId)) return false;
        if (!hostReady) {
            // React 胶囊尚未挂载时不能谎报启动成功，否则选剧页关闭后演绎会停在不可见状态。
            delete launchReplyTargets[message.launch_id];
            clear('launch-host-unavailable');
            return false;
        }
        claimAudioPlayback();
        var readyMessage = postMessage({ action: 'theater:launch-ready', launch_id: message.launch_id, story_id: state.storyId, session_id: state.sessionId });
        postDirect(launchReplyTargets[message.launch_id], readyMessage);
        delete launchReplyTargets[message.launch_id];
        if (message.launch_action === 'start' || message.launch_action === 'restart') {
            state.history = [];
            await playPerformance(snapshot.session.opening_performance, 0, {
                displayPhase: 'opening',
                historyId: 'opening-performance'
            });
        } else {
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            state.currentBlock = null;
            render();
        }
        return true;
    }
    function launch(message) {
        var launchId = String(message.launch_id || '');
        if (launchRequests[launchId]) return launchRequests[launchId];
        var launchToken = ++launchEpoch;
        var nextStoryId = String(message.story_id);
        var nextSessionId = String(message.session_id);
        pendingLaunch = { token: launchToken, storyId: nextStoryId, sessionId: nextSessionId };
        var request = performLaunch(message, launchToken).catch(function () {
            if (isCurrentLaunch(launchToken, nextStoryId, nextSessionId)) clear('launch-request-failed');
            return false;
        }).finally(function () {
            if (pendingLaunch && pendingLaunch.token === launchToken) pendingLaunch = null;
        });
        launchRequests[launchId] = request;
        launchRequestOrder.push(launchId);
        if (launchRequestOrder.length > 64) delete launchRequests[launchRequestOrder.shift()];
        return request;
    }
    async function submit(text, inputSource) {
        var message = String(text || '').trim();
        var normalizedInputSource = inputSource === 'suggestion' ? 'suggestion' : 'freeform';
        if (!state.active || state.phase !== 'awaiting_player' || !message) return false;
        var signature = state.sessionId + '\u001f' + state.revision + '\u001f' + message;
        if (!state.pendingTurn || state.pendingTurn.signature !== signature) state.pendingTurn = { signature: signature, id: createId('theater_turn_') };
        // 请求期间可能从另一个选剧页切换剧本；响应只能写回发起它的 Session。
        var submittedStoryId = state.storyId;
        var submittedSessionId = state.sessionId;
        var submittedLaunchEpoch = launchEpoch;
        var submittedTurnId = state.pendingTurn.id;
        var submittedSuggestedInputs = state.suggestedInputs.slice();
        var optimisticHistoryId = 'player-pending-' + state.pendingTurn.id;
        // 玩家行动先进入历史区，让推荐输入和手动提交都立即得到可见反馈。
        if (!state.history.some(function (entry) { return entry.id === optimisticHistoryId; })) {
            state.history.push(historyEntry(optimisticHistoryId, 'player_action', message, state.playerName));
        }
        state.phase = 'evaluating'; state.suggestedInputs = []; state.draftRestore = null; state.errorMessage = ''; render();
        var result;
        try {
            result = await requestJson(api.input, { method: 'POST', body: {
                story_id: state.storyId, session_id: state.sessionId, client_turn_id: state.pendingTurn.id,
                base_revision: state.revision, message: message, input_source: normalizedInputSource
            }});
        } catch (_) {
            result = { ok: false, reason: 'numeric_input_request_failed' };
        }
        if (
            !state.active
            || state.storyId !== submittedStoryId
            || state.sessionId !== submittedSessionId
        ) {
            // 服务端可能已经提交旧 Session；这里只丢弃迟到显示，恢复时仍会读到权威历史。
            return false;
        }
        if (!state.pendingTurn || state.pendingTurn.id !== submittedTurnId) return false;
        // 先过滤迟到请求，再呈现本次成功或失败的真实用量；网络断线时不能显示上一轮。
        state.tokenUsage = result.token_usage || null;
        if (!result.ok) {
            // 退出流程已经接管时，迟到失败不能重新打开输入区。
            if (state.phase !== 'evaluating') return false;
            var refreshed = null;
            if (result.reason === 'numeric_base_revision_mismatch'
                || result.reason === 'numeric_suggested_input_not_current'
                || result.reason === 'numeric_duplicate_client_turn_id'
                || result.reason === 'session_already_ended') {
                try {
                    refreshed = await requestJson(api.session + '/' + encodeURIComponent(submittedSessionId) + '?story_id=' + encodeURIComponent(submittedStoryId));
                } catch (_) {
                    // 刷新失败统一交给下方提示，不恢复已知过期的按钮。
                }
            }
            // 刷新期间继续保持忙碌；返回后再次确认交互归属，避免污染新演绎或退出阶段。
            if (!state.active || state.storyId !== submittedStoryId || state.sessionId !== submittedSessionId
                || !state.pendingTurn || state.pendingTurn.id !== submittedTurnId || state.phase !== 'evaluating') return false;
            state.history = state.history.filter(function (entry) { return entry.id !== optimisticHistoryId; });
            state.draftRestore = { id: createId('theater_draft_restore_'), text: message };
            state.errorMessage = t('theater.inputFailed', '暂时未能取得演绎回复，请重试。');
            // 只有当前接口明确在提交前返回的模型失败才恢复旧按钮；断网仍保留原输入与幂等编号。
            if (['numeric_v2_actor_failed', 'numeric_v2_actor_unavailable',
                'numeric_v2_evaluator_failed', 'numeric_v2_evaluator_unavailable'].indexOf(result.reason) >= 0) {
                state.suggestedInputs = submittedSuggestedInputs;
                // 推荐点击前草稿为空；回填按钮文字会再次把推荐隐藏。
                if (normalizedInputSource === 'suggestion') state.draftRestore.text = '';
            }
            // 冲突快照仍只回写原提交世代；不能用提交前按钮覆盖新的进度。
            if (isCurrentLaunch(submittedLaunchEpoch, submittedStoryId, submittedSessionId)
                && state.pendingTurn.id === submittedTurnId && refreshed && refreshed.ok) {
                applySnapshot(refreshed);
                state.history = buildCommittedHistory(refreshed);
                if (refreshed.end_receipt_id) {
                    state.pendingEnd = {
                        story_id: state.storyId, session_id: state.sessionId, revision: state.revision,
                        end_receipt_id: refreshed.end_receipt_id, archive_request_id: refreshed.archive_request_id || ''
                    };
                }
                state.errorMessage = state.sessionStatus === 'ended'
                    ? t('theater.ended', '已结束')
                    : t('theater.numericSessionUpdated', '演出状态已更新，已保留你的输入，请确认后重试。');
            }
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            render();
            return false;
        }
        state.pendingTurn = null;
        // 成功回合已经推进权威 revision；此前发起的同 Session 启动快照不得再覆盖新历史。
        launchEpoch += 1;
        applySnapshot(result);
        if (result.end_receipt_id) state.pendingEnd = {
            story_id: state.storyId,
            session_id: state.sessionId,
            revision: state.revision,
            end_receipt_id: result.end_receipt_id,
            archive_request_id: result.archive_request_id || ''
        };
        if (result.idempotent_replay === true) {
            // 上一次请求可能已在服务端提交但响应丢失；幂等重放只返回权威快照，
            // 不会再次返回 performance。必须用快照重建历史，不能留下乐观玩家气泡或漏掉猫娘回复。
            state.history = buildCommittedHistory(result);
            state.currentBlock = null;
            state.draftRestore = null;
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            render();
            return true;
        }
        try {
            await playPerformance(result.performance, state.revision, {
                playerInput: message,
                playerAlreadyShown: true
            });
        } catch (_) {
            state.currentBlock = null;
            state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player';
            state.errorMessage = t('theater.performanceFailed', '演绎播放中断，请继续输入或重新打开小剧场。');
            render();
            return false;
        }
        return true;
    }
    function clear(reason) {
        if (state.active && state.phase !== 'loading') claimAudioPlayback();
        state.queueToken += 1;
        state.active = false; state.phase = 'inactive'; state.currentBlock = null; state.history = []; state.suggestedInputs = [];
        state.playerName = ''; state.catgirlName = '';
        state.pendingTurn = null; state.draftRestore = null;
        committedSnapshot = null;
        rememberPointer();
        var chatHost = host();
        restoreComposerVisibility(chatHost);
        restoreChatSurfaceMode(chatHost);
        if (chatHost && typeof chatHost.setViewProps === 'function') {
            // full/compact 恢复可能重挂 React；草稿恢复必须作为最后一次视图更新交付，
            // 否则前一步刚写回的普通聊天草稿会被后续重挂清空。
            chatHost.setViewProps({
                theaterPresentation: {
                    active: false,
                    phase: 'inactive',
                    history: [],
                    suggestedInputs: [],
                    ordinaryDraftRestore: state.ordinaryDraftRestore
                },
                composerDisabled: false
            });
        }
        window.dispatchEvent(new CustomEvent('neko:theater-cleared', { detail: { reason: reason || 'clear' } }));
    }
    function openSelector(receipt) {
        state.pendingEnd = receipt || state.pendingEnd;
        var url = '/theater?story_id=' + encodeURIComponent(state.storyId);
        try {
            if (typeof window.openOrFocusWindow === 'function') {
                return window.openOrFocusWindow(url, 'neko_theater', 'width=1100,height=760,menubar=no,toolbar=no,location=no,status=no', { navigateOnReuse: true });
            }
            return window.open(url, 'neko_theater');
        } catch (_) {
            return null;
        }
    }
    function restoreSelectorWindow(target) {
        if (!target || target.closed) return false;
        try {
            if (typeof window.requestOpenedWindowRestore === 'function') {
                window.requestOpenedWindowRestore(target);
            } else {
                postDirect(target, { type: 'neko:restore-window' });
            }
        } catch (_) {}
        try {
            if (typeof target.focus === 'function') target.focus();
        } catch (_) {}
        return true;
    }
    function returnToSelector(receipt, clearReason, preparedSelector) {
        state.pendingEnd = receipt || state.pendingEnd;
        state.sessionStatus = 'ended';
        state.phase = 'returning_selector';
        state.errorMessage = '';
        render();
        var selectorTarget = preparedSelector || openSelector(state.pendingEnd);
        if (!selectorTarget) {
            // 已退出 Session 只能从选剧页继续；本体保留只读历史和返回按钮作为恢复入口。
            state.phase = 'ended';
            state.errorMessage = t(
                'theater.selectorReturnFailed',
                '已退出演绎，但剧本页面打开失败。请点击“返回剧本页”重试。'
            );
            render();
            return false;
        }
        // 预先打开的选剧页可能早于结束接口返回完成加载，需要在拿到回执后再主动补发一次。
        sendPendingEnd(selectorTarget);
        // 确认弹窗关闭和结束请求都会把焦点留回本体；提交成功后必须再次恢复选剧页。
        restoreSelectorWindow(selectorTarget);
        clear(clearReason);
        return true;
    }
    function sendPendingEnd(target) {
        if (!state.pendingEnd) return;
        var content = Object.assign({ action: 'theater:post-end', message_id: createId('theater_post_end_') }, state.pendingEnd);
        // 已知选剧页时只直发；直发失败才广播，避免同一回执通过两个传输通道重复到达。
        if (target) {
            var directMessage = transport.createMessage('theater-runtime', content);
            if (postDirect(target, directMessage)) return;
        }
        postMessage(content);
    }
    async function confirmEnd(onConfirmed) {
        var message = t('theater.endConfirm', '确定结束当前演绎吗？');
        if (typeof window.showConfirm === 'function') {
            return window.showConfirm(
                message,
                t('theater.endPerformance', '结束演绎'),
                {
                    okText: t('common.confirm', '确认'),
                    cancelText: t('common.cancel', '取消'),
                    danger: true,
                    skin: 'theater',
                    onResolve: function (confirmed) {
                        if (confirmed && typeof onConfirmed === 'function') onConfirmed();
                    }
                }
            );
        }
        // 极早启动阶段统一弹窗尚未加载时保留原生确认，不能静默结束演绎。
        return window.confirm(message);
    }
    async function requestEnd() {
        if (!state.active || endConfirmationPending) return false;
        if (state.sessionStatus === 'ended' || state.phase === 'ended') {
            return returnToSelector(state.pendingEnd, 'natural-ending-return');
        }
        var requestedStoryId = state.storyId;
        var requestedSessionId = state.sessionId;
        var requestedRevision = state.revision;
        var requestedLifecycleRevision = state.lifecycleRevision;
        var requestedLaunchEpoch = launchEpoch;
        function isCurrentEndRequest() {
            return state.active
                && state.storyId === requestedStoryId
                && state.sessionId === requestedSessionId
                && state.revision === requestedRevision
                && state.lifecycleRevision === requestedLifecycleRevision
                && launchEpoch === requestedLaunchEpoch;
        }
        endConfirmationPending = true;
        var confirmed = false;
        var preparedSelector = null;
        try {
            confirmed = await confirmEnd(function () {
                // 必须在确认按钮的原始点击事件里取得窗口句柄；等待结束接口后再打开会被桌面窗口策略拦截。
                if (isCurrentEndRequest()) preparedSelector = openSelector();
            });
        } finally {
            endConfirmationPending = false;
        }
        // 取消只关闭确认框，Session、输入和演绎历史都保持原样。
        if (!confirmed || !isCurrentEndRequest()) return false;
        state.phase = 'ending'; state.errorMessage = ''; state.queueToken += 1; render();
        var result;
        var endRequestFailed = false;
        try {
            result = await requestJson(api.end, { method: 'POST', body: {
                story_id: requestedStoryId,
                session_id: requestedSessionId,
                base_revision: requestedRevision,
                base_lifecycle_revision: requestedLifecycleRevision
            } });
        } catch (_) {
            endRequestFailed = true;
            result = { ok: false };
        }
        // 结束接口返回前也可能切换 Session；旧响应不能改变新 Session 的阶段或回执。
        if (!isCurrentEndRequest()) return false;
        if (!result.ok) {
            var snapshot = committedSnapshot;
            var committedSession = snapshot && snapshot.session && typeof snapshot.session === 'object'
                ? snapshot.session
                : null;
            if (
                committedSession
                && String(committedSession.story_package_id || '') === requestedStoryId
                && String(committedSession.session_id || '') === requestedSessionId
                && Number(committedSession.revision || 0) === requestedRevision
            ) {
                // 结束动作已经取消逐字播放；失败时从已提交快照重建，不能留下截断正文和空推荐项。
                applySnapshot(snapshot);
                state.history = buildCommittedHistory(snapshot);
                state.currentBlock = null;
            }
            state.phase = 'awaiting_player';
            // 只有请求本身未取得响应时才提示本地服务连接；后端拒绝属于业务状态错误。
            state.errorMessage = endRequestFailed
                ? t('theater.endConnectionFailed', '无法连接 N.E.K.O 本地服务，请确认程序仍在运行后重试。')
                : t('theater.endStateFailed', '当前演绎状态无法结束，请返回剧本页后重试。');
            render();
            return false;
        }
        var receipt = {
            story_id: state.storyId,
            session_id: state.sessionId,
            revision: result.session.revision,
            end_receipt_id: result.end_receipt_id,
            archive_request_id: result.archive_request_id || ''
        };
        state.revision = result.session.revision;
        return returnToSelector(receipt, 'user-ended', preparedSelector);
    }
    async function restorePointer() {
        // 桌面端只有独立紧凑胶囊拥有演绎投影；Pet 与 full 窗口不能从各自会话存储
        // 恢复并重复驱动正文或 TTS。Web 单页没有该宿主角色，仍保留原刷新恢复语义。
        var role = desktopRuntimeRole();
        if (role && role !== 'compact') return;
        var pointer = readPointer();
        if (!pointer) return;
        // 启动恢复只属于读取指针时的 launch 世代；任何更新的选剧启动都有更高优先级。
        var restoreLaunchEpoch = launchEpoch;
        // Refresh restoration is a pending launch too: end/delete notifications
        // can arrive before its GET resolves, while the runtime is still inactive.
        pendingLaunch = { token: restoreLaunchEpoch, storyId: pointer.story_id, sessionId: pointer.session_id };
        try {
            var snapshot;
            try {
                snapshot = await requestJson(api.session + '/' + encodeURIComponent(pointer.session_id) + '?story_id=' + encodeURIComponent(pointer.story_id));
            } catch (_) {
                // 暂时性网络失败保留指针供下次恢复，但不让启动 Promise 产生未处理拒绝。
                return;
            }
            if (restoreLaunchEpoch !== launchEpoch) return;
            if (!snapshot.ok || !snapshot.session) { try { window.sessionStorage.removeItem(POINTER_KEY); } catch (_) {} return; }
            applySnapshot(snapshot);
            if (snapshot.end_receipt_id) state.pendingEnd = {
                story_id: state.storyId,
                session_id: state.sessionId,
                revision: state.revision,
                end_receipt_id: snapshot.end_receipt_id,
                archive_request_id: snapshot.archive_request_id || ''
            };
            state.active = true; state.phase = state.sessionStatus === 'ended' ? 'ended' : 'awaiting_player'; state.history = buildCommittedHistory(snapshot); state.currentBlock = null;
            var hostReady = await waitForHost();
            if (restoreLaunchEpoch !== launchEpoch) return;
            if (!hostReady) {
                // 指针恢复同样依赖 React 胶囊；宿主不可用时清除不可见运行态和失效指针。
                clear('pointer-host-unavailable');
                return;
            }
            render();
        } finally {
            if (pendingLaunch && pendingLaunch.token === restoreLaunchEpoch) pendingLaunch = null;
        }
    }
    function handleCrossWindowMessage(event) {
        if (event && event.origin && event.origin !== window.location.origin) return;
        var message = event && event.data;
        if (!message || typeof message !== 'object') return;
        if (String(message.action || '').indexOf('theater:') === 0 && message.schema !== MESSAGE_SCHEMA) return;
        // A candidate is not active yet while ordinary voice shuts down.
        // Matching lifecycle events must still revoke its right to take over.
        if (pendingLaunch && message.story_id === pendingLaunch.storyId && (
            message.action === 'theater:story-deleted'
            || (message.action === 'theater:external-end' && message.session_id === pendingLaunch.sessionId)
        )) {
            launchEpoch += 1;
            pendingLaunch = null;
            if (!state.active) rememberPointer();
        }
        if (message.action === 'theater:launch-ready' && message.launch_id) {
            stopDesktopLaunchRelay(message.launch_id);
        }
        else if (message.action === 'theater:launch-request' && message.launch_id && message.story_id && message.session_id && Number.isInteger(message.revision)) {
            var role = desktopRuntimeRole();
            if (role === 'pet') {
                if (message.runtime_host_kind) return;
                // 选剧页通常由 Pet 打开，window.opener 会把启动请求直送 Pet；必须显式转交
                // 给独立胶囊，不能在不可见的 Pet React 宿主里只播放 TTS。
                relayLaunchToDesktopChat(message);
                return;
            }
            // 桌面 full 与 compact 页面可能同时存活；小剧场固定进入本体紧凑胶囊，
            // 只允许 compact Runtime 接管，避免两个窗口重复请求正文和 TTS。
            if (role && role !== 'compact') return;
            if (message.runtime_host_kind && role && message.runtime_host_kind !== role) return;
            if (event.source && event.source !== window) launchReplyTargets[message.launch_id] = event.source;
            launch(message);
        }
        else if (message.action === 'theater:selector-ready') sendPendingEnd(event.source);
        else if (
            message.action === 'theater:external-end'
            && state.active
            && message.story_id === state.storyId
            && message.session_id === state.sessionId
        ) clear('selector-ended');
        else if (
            message.action === 'theater:story-deleted'
            && state.active
            && message.story_id === state.storyId
        ) clear('story-deleted');
        else if (message.action === 'catgirl_switched') {
            // 角色切换即使发生在启动指针恢复期间，也必须立即使旧角色的异步快照失效。
            launchEpoch += 1;
            if (state.active) clear('catgirl-switched');
        }
    }

    var runtime = {
        isActive: function () { return state.active; },
        handleComposerSubmit: function (text) {
            if (!state.active) return false;
            submitFromHost(text);
            return true;
        },
        requestEnd: requestEnd,
        clear: clear,
        getState: function () { return Object.assign({}, state, { history: state.history.slice() }); }
    };
    window.nekoTheaterRuntime = runtime;

    if (typeof BroadcastChannel !== 'undefined') {
        try { state.channel = new BroadcastChannel('neko_page_channel'); state.channel.addEventListener('message', handleCrossWindowMessage); } catch (_) { state.channel = null; }
    }
    window.addEventListener('message', handleCrossWindowMessage);
    window.addEventListener('localechange', function () {
        if (!state.active) return;
        // 语言切换会让聊天宿主重建基础 props；等宿主处理完成后恢复仍在进行的剧场投影。
        window.setTimeout(function () {
            if (state.active) render();
        }, 0);
    });
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', restorePointer);
    else restorePointer();
})();
