/** Numeric v2 剧本选择页：只管理剧本、Session 入口和结束后的记忆询问。 */
(function () {
    'use strict';

    // 选择页不参与演绎推进；所有状态变化都提交给 Numeric v2 Runtime。
    var api = {
        stories: '/api/theater-numeric/stories',
        importStory: '/api/theater-numeric/packages/import',
        packages: '/api/theater-numeric/packages',
        start: '/api/theater-numeric/session/start',
        resume: '/api/theater-numeric/session/resume',
        end: '/api/theater-numeric/session/end',
        active: '/api/theater-numeric/session/active',
        archive: '/api/theater-numeric/session/archive',
        skipArchive: '/api/theater-numeric/session/archive/skip',
        memoryStories: '/api/theater-numeric/memory/stories',
        memoryArchives: '/api/theater-numeric/memory/archives',
        memoryArchive: '/api/theater-numeric/memory/archive',
        pinMemoryArchive: '/api/theater-numeric/memory/archive/pin',
        forgetMemory: '/api/theater-numeric/memory/forget'
    };
    // 选择页只消费共享传输协议；跨窗口目标和业务状态仍由本页独立管理。
    var transport = window.nekoTheaterTransport;
    if (!transport) throw new Error('numeric_theater_transport_unavailable');
    var MESSAGE_SCHEMA = transport.MESSAGE_SCHEMA;
    var createId = transport.createId;
    var requestJson = transport.requestJson;
    // pendingEnd 跨窗口保存结束回执，确保返回选剧页后才询问是否写入记忆。
    var state = { stories: [], storyId: '', characterId: '', session: null, archives: [], busy: false, channel: null, pendingEnd: null, memoryPromptActive: false };
    // 同一剧本在切换猫娘后仍保持相同 story_id，单独世代号用于拦截旧角色的迟到响应。
    var characterEpoch = 0;
    var modalResolve = null;
    var modalPersistent = false;
    var modalReturnFocus = null;
    var modalQueue = [];

    function $(id) { return document.getElementById(id); }
    function t(key, fallback, options) {
        if (typeof window.t === 'function') {
            var value = window.t(key, options);
            if (typeof value === 'string' && value && value !== key) return value;
        }
        return fallback;
    }
    function setStatus(key, fallback) {
        var node = $('theater-selector-status');
        node.textContent = t(key, fallback);
        node.setAttribute('data-i18n', key);
    }
    function setFeedback(text, isError) {
        var node = $('theater-inline-feedback');
        node.hidden = !text;
        node.textContent = text || '';
        node.dataset.tone = isError ? 'error' : 'info';
    }
    function postMessage(message, preferOpener) {
        var payload = transport.createMessage('theater-selector', message);
        var sent = false;
        var opener = window.opener && !window.opener.closed ? window.opener : null;
        // 启动请求只投递给打开选剧页的本体；没有 opener 的独立页面才退回共享频道发现本体。
        if (preferOpener === true && opener) {
            try { opener.postMessage(payload, window.location.origin); return true; } catch (_) {}
        }
        if (state.channel) {
            try { state.channel.postMessage(payload); sent = true; } catch (_) {}
        }
        if (opener) {
            try { opener.postMessage(payload, window.location.origin); sent = true; } catch (_) {}
        }
        return sent;
    }
    var pendingEndSelection = null;
    function selectPendingEnd(receipt) {
        if (state.pendingEnd !== receipt) return;
        selectStory(String(receipt.story_id)).catch(function () {
            setStatus('theater.failed', '出错了');
            setFeedback(t('theater.sessionLoadFailed', '演绎进度读取失败，请重试。'), true);
        });
    }
    function setBusy(busy) {
        state.busy = busy;
        ['theater-import-btn', 'theater-empty-import-btn', 'theater-start-btn', 'theater-continue-btn', 'theater-end-btn', 'theater-delete-btn', 'theater-forget-memory-btn'].forEach(function (id) {
            var node = $(id);
            if (node) node.disabled = busy;
        });
        document.querySelectorAll('[data-theater-pin-session], [data-theater-view-session]').forEach(function (node) { node.disabled = busy; });
        renderActions();
        if (!busy && pendingEndSelection) {
            var receipt = pendingEndSelection;
            pendingEndSelection = null;
            selectPendingEnd(receipt);
        }
    }
    function selectedStory() {
        return state.stories.find(function (story) { return String(story.story_id || '') === state.storyId; }) || null;
    }
    function sessionKind() {
        if (!state.session) return 'new';
        if (state.session.status !== 'ended') return 'active';
        return state.session.ended_reason === 'user_exit' ? 'paused' : 'ended';
    }
    function selectedSessionMatches(storyId, sessionId) {
        return state.storyId === storyId
            && state.session
            && String(state.session.session_id || '') === sessionId;
    }
    // 玩家主动退出的记录允许继续或重新开始；剧情自然结局只能重新开始。
    function renderActions() {
        var kind = sessionKind();
        var memoryOnly = !!(selectedStory() || {}).memory_only;
        var startButton = $('theater-start-btn');
        var startKey = state.session ? 'theater.restartSession' : 'theater.start';
        startButton.textContent = state.session
            ? t(startKey, '重新开始')
            : t(startKey, '开始');
        startButton.setAttribute('data-i18n', startKey);
        startButton.disabled = memoryOnly || state.busy || !state.storyId || kind === 'active';
        $('theater-continue-btn').disabled = state.busy || (state.session && state.session.continuation_allowed === false) || (kind !== 'active' && kind !== 'paused');
        var endButton = $('theater-end-btn');
        endButton.hidden = kind !== 'active';
        endButton.disabled = state.busy || kind !== 'active';
        $('theater-delete-btn').disabled = memoryOnly || state.busy || !state.storyId;
        startButton.classList.toggle('is-current-primary', kind === 'new' || kind === 'ended');
        $('theater-continue-btn').classList.toggle('is-current-primary', kind === 'active' || kind === 'paused');
        $('theater-session-hint').textContent = memoryOnly
            ? t('theater.deletedStoryMemoryHint', '剧本已删除，可查看保留的记忆摘要或选择忘记。重新导入剧本后才能演绎。')
            : state.session && state.session.continuation_allowed === false
            ? t('theater.sessionHintPackageChanged', '剧本已更新，旧进度无法继续；结束旧演绎后可以重新开始。')
            : kind === 'active'
            ? t('theater.sessionHintActive', '演绎正在进行，点击“继续”返回演绎。')
            : kind === 'paused'
                ? t('theater.sessionHintPausedRestart', '上次演绎已退出，可以继续原进度或重新开始。')
            : kind === 'ended'
                ? t('theater.sessionHintEndedRestart', '本次演绎已结束，点击“重新开始”创建新的演绎。')
                : t('theater.sessionHintNew', '点击“开始”创建本次演绎。');
        var badge = $('theater-session-badge');
        badge.textContent = memoryOnly ? t('theater.deletedStoryMemory', '已删除 · 记忆管理') : kind === 'active'
            ? t('theater.running', '演出中')
            : kind === 'paused'
                ? t('theater.paused', '已退出')
            : kind === 'ended'
                ? t('theater.ended', '已结束')
                : t('theater.notStarted', '尚未开始');
    }
    function renderStories() {
        var list = $('theater-story-list');
        list.textContent = '';
        $('theater-empty-state').hidden = state.stories.length > 0;
        list.hidden = state.stories.length === 0;
        state.stories.forEach(function (story, storyIndex) {
            var button = document.createElement('button');
            button.type = 'button';
            button.className = 'theater-story-card';
            button.setAttribute('role', 'option');
            button.setAttribute('aria-selected', String(String(story.story_id) === state.storyId));
            button.dataset.storyId = String(story.story_id || '');
            var title = document.createElement('strong');
            title.textContent = String(story.title || story.story_id || '');
            var meta = document.createElement('span');
            meta.textContent = story.memory_only ? t('theater.deletedStoryMemory', '已删除 · 记忆管理') : [story.author, story.language].filter(Boolean).join(' · ');
            button.append(title, meta);
            button.addEventListener('click', function () {
                selectStory(button.dataset.storyId).catch(function () {
                    setFeedback(t('theater.sessionLoadFailed', '演绎进度读取失败，请重试。'), true);
                });
            });
            button.addEventListener('keydown', function (event) {
                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
                event.preventDefault();
                var offset = event.key === 'ArrowDown' ? 1 : -1;
                var next = list.children[(storyIndex + offset + state.stories.length) % state.stories.length];
                if (next) {
                    next.focus();
                    selectStory(next.dataset.storyId).catch(function () {
                        setFeedback(t('theater.sessionLoadFailed', '演绎进度读取失败，请重试。'), true);
                    });
                }
            });
            list.appendChild(button);
        });
    }
    function renderDetail() {
        var story = selectedStory();
        $('theater-detail-placeholder').hidden = !!story;
        $('theater-detail-content').hidden = !story;
        if (!story) return;
        var intro = story.display_intro || {};
        $('theater-detail-title').textContent = String(story.title || story.story_id || '');
        $('theater-detail-meta').textContent = story.memory_only ? t('theater.deletedStoryMemory', '已删除 · 记忆管理') : [story.author, story.language, 'rev.' + story.revision].filter(Boolean).join(' · ');
        document.querySelectorAll('.theater-intro-background, .theater-role-grid').forEach(function (node) { node.hidden = !!story.memory_only; });
        var memoryHeading = document.querySelector('.theater-memory-heading h2');
        var memoryHeadingKey = story.memory_only ? 'memory.summary' : 'theater.savedPerformances';
        memoryHeading.setAttribute('data-i18n', memoryHeadingKey);
        memoryHeading.textContent = t(memoryHeadingKey, story.memory_only ? '摘要' : '已保存的演绎');
        document.querySelector('.theater-memory-heading p').hidden = !!story.memory_only;
        $('theater-detail-background').textContent = String(intro.background || '');
        $('theater-detail-player').textContent = String(intro.player_identity || '');
        $('theater-detail-catgirl').textContent = String(intro.catgirl_identity || '');
        renderMemoryArchives();
        renderActions();
    }
    function renderMemoryArchives() {
        var list = $('theater-memory-list');
        var empty = $('theater-memory-empty');
        if (!list || !empty) return;
        list.textContent = '';
        var summaries = (selectedStory() || {}).memory_summaries || [];
        empty.hidden = state.archives.length > 0 || summaries.length > 0;
        summaries.forEach(function (summary) {
            var row = document.createElement('p');
            row.textContent = String(summary);
            list.appendChild(row);
        });
        state.archives.forEach(function (archive, index) {
            var row = document.createElement('div');
            row.className = 'theater-memory-row';
            var copy = document.createElement('div');
            var title = document.createElement('strong');
            title.textContent = String(archive.ending_title || (archive.episode_status === 'completed'
                ? t('theater.completedPerformance', '已完成的演绎')
                : t('theater.pausedPerformance', '暂停的演绎')));
            var meta = document.createElement('span');
            meta.textContent = t('theater.performanceRecordMeta', '记录 {{index}} · revision {{revision}}', {
                index: state.archives.length - index,
                revision: Number(archive.revision || 0)
            });
            copy.append(title, meta);
            var pin = document.createElement('button');
            pin.type = 'button';
            pin.className = 'theater-memory-pin';
            pin.dataset.theaterPinSession = String(archive.session_id || '');
            pin.textContent = archive.pinned
                ? t('theater.unpinPerformance', '取消收藏')
                : t('theater.pinPerformance', '收藏');
            pin.disabled = state.busy;
            pin.addEventListener('click', function () { toggleArchivePin(archive); });
            var view = document.createElement('button');
            view.type = 'button';
            view.className = 'theater-memory-view';
            view.dataset.theaterViewSession = String(archive.session_id || '');
            view.textContent = t('theater.viewPerformance', '查看');
            view.disabled = state.busy;
            view.addEventListener('click', function () { viewMemoryArchive(archive); });
            var actions = document.createElement('div');
            actions.className = 'theater-memory-actions';
            actions.append(view, pin);
            row.append(copy, actions);
            list.appendChild(row);
        });
    }
    function formatMemoryArchive(archive) {
        var lines = [];
        var opening = archive && archive.opening && typeof archive.opening === 'object' ? archive.opening : {};
        var playerName = String(archive.player_name || '你');
        var catgirlName = String(archive.catgirl_name || 'Neko');
        function formatStoredPerformance(entry, attributeLegacy) {
            var storedParts = Array.isArray(entry && entry.parts) ? entry.parts : [];
            var renderedParts = storedParts.map(function (part) {
                if (!part || typeof part !== 'object') return '';
                var text = String(part.text || '').trim();
                if (!text) return '';
                // 场景旁白保持无署名；猫娘动作和对白才归到角色名下。
                return part.kind === 'action' || part.kind === 'dialogue'
                    ? catgirlName + '：' + text
                    : text;
            }).filter(Boolean);
            if (renderedParts.length) return renderedParts;
            var legacyPerformance = String(entry && entry.performance || '').trim();
            if (!legacyPerformance) return [];
            return [attributeLegacy ? catgirlName + '：' + legacyPerformance : legacyPerformance];
        }
        var openingLines = formatStoredPerformance(opening, false);
        if (openingLines.length) {
            lines.push(t('theater.performanceArchiveOpening', '开场') + '\n' + openingLines.join('\n'));
        }
        var turns = Array.isArray(archive.turns) ? archive.turns : [];
        turns.forEach(function (turn, index) {
            var parts = [t('theater.performanceArchiveTurn', '第 {{index}} 回合', { index: index + 1 })];
            var playerInput = String(turn && turn.player_input || '').trim();
            if (playerInput) parts.push(playerName + '：' + playerInput);
            formatStoredPerformance(turn, true).forEach(function (line) { parts.push(line); });
            lines.push(parts.join('\n'));
        });
        var ending = archive && archive.ending && typeof archive.ending === 'object' ? archive.ending : {};
        var endingText = [ending.title, ending.summary].filter(Boolean).map(String).join('：');
        if (endingText) lines.push(t('theater.completedPerformance', '已完成的演绎') + '\n' + endingText);
        return lines.join('\n\n');
    }
    async function viewMemoryArchive(summary) {
        if (state.busy || !summary || !summary.session_id) return;
        var viewStoryId = state.storyId;
        var viewSessionId = String(summary.session_id);
        var viewCharacterEpoch = characterEpoch;
        setBusy(true); setFeedback('');
        try {
            var result = await requestJson(
                api.memoryArchive
                + '?story_id=' + encodeURIComponent(viewStoryId)
                + '&session_id=' + encodeURIComponent(viewSessionId)
            );
            if (state.storyId !== viewStoryId || viewCharacterEpoch !== characterEpoch) return;
            if (!result.ok || !result.archive) throw new Error('archive_load_failed');
            var archive = result.archive;
            await showModal({
                title: String(archive.story_title || summary.story_title || t('theater.performanceArchiveTitle', '演绎记录')),
                body: formatMemoryArchive(archive),
                cancelLabel: '',
                confirmLabel: t('common.close', '关闭'),
                singleAction: true,
                transcript: true
            });
        } catch (_) {
            if (state.storyId === viewStoryId && viewCharacterEpoch === characterEpoch) {
                setFeedback(t('theater.performanceArchiveLoadFailed', '演绎记录读取失败，请重试。'), true);
            }
        } finally { setBusy(false); }
    }
    async function loadMemoryArchives(storyId, expectedCharacterEpoch) {
        var loadCharacterEpoch = expectedCharacterEpoch === undefined
            ? characterEpoch
            : expectedCharacterEpoch;
        var result = await requestJson(api.memoryArchives + '?story_id=' + encodeURIComponent(storyId));
        if (state.storyId !== storyId || loadCharacterEpoch !== characterEpoch) return;
        // 后端读取失败不能伪装成空归档；交给 selectStory 的错误链路显示可重试反馈。
        if (!result.ok || !Array.isArray(result.archives)) throw new Error('archive_list_load_failed');
        state.archives = result.archives;
        renderMemoryArchives();
    }
    var storySelectionEpoch = 0;
    async function selectStory(storyId, forceWhileBusy) {
        if ((state.busy && forceWhileBusy !== true) || !storyId) return;
        storySelectionEpoch += 1;
        var selectionCharacterEpoch = characterEpoch;
        state.storyId = storyId;
        state.session = null;
        state.archives = [];
        setFeedback('');
        renderStories();
        renderDetail();
        setStatus('theater.loadingSession', '正在读取演绎进度...');
        if ((selectedStory() || {}).memory_only) {
            setStatus('theater.ready', '就绪');
            if (selectedStory().forget_pending) setFeedback(t('theater.storyMemoryForgetFailed', '剧本记忆删除失败，请重试。'), true);
            var memoryUrl = new URL(window.location.href);
            memoryUrl.searchParams.set('story_id', storyId);
            window.history.replaceState(null, '', memoryUrl.toString());
            return true;
        }
        var result;
        try {
            result = await requestJson(api.active + '?story_id=' + encodeURIComponent(storyId));
        } catch (_) {
            // 只允许当前选择发布断网状态；旧请求失败不能覆盖玩家后来切换的剧本。
            if (state.storyId !== storyId || selectionCharacterEpoch !== characterEpoch) return false;
            setStatus('theater.failed', '出错了');
            setFeedback(t('theater.sessionLoadFailed', '演绎进度读取失败，请重试。'), true);
            return false;
        }
        if (state.storyId !== storyId || selectionCharacterEpoch !== characterEpoch) return;
        if (result.ok && result.session) {
            state.session = result.session;
            if (result.session.status === 'ended' && result.end_receipt_id) {
                if (result.archive_status === 'pending' || result.archive_status === 'writing') {
                    state.pendingEnd = {
                        story_id: storyId,
                        session_id: result.session.session_id,
                        revision: result.session.revision,
                        end_receipt_id: result.end_receipt_id,
                        archive_request_id: result.archive_request_id || ''
                    };
                } else if (state.pendingEnd && state.pendingEnd.end_receipt_id === result.end_receipt_id) {
                    // 同一 revision 已记录或已跳过时不重复询问；继续产生新 revision 后会收到新回执。
                    state.pendingEnd = null;
                }
            }
        }
        else if (result._status !== 404) setFeedback(t('theater.sessionLoadFailed', '演绎进度读取失败，请重试。'), true);
        try {
            await loadMemoryArchives(storyId, selectionCharacterEpoch);
        } catch (_) {
            // 记忆列表同属详情快照；请求失败时结束 loading 状态并保留当前可重试选择。
            if (state.storyId !== storyId || selectionCharacterEpoch !== characterEpoch) return false;
            setStatus('theater.failed', '出错了');
            setFeedback(t('theater.sessionLoadFailed', '演绎进度读取失败，请重试。'), true);
            return false;
        }
        // 归档请求返回期间可能已经切换剧本；旧选择不能覆盖详情或地址栏。
        if (state.storyId !== storyId || selectionCharacterEpoch !== characterEpoch) return;
        renderDetail();
        setStatus('theater.ready', '就绪');
        var url = new URL(window.location.href);
        url.searchParams.set('story_id', storyId);
        window.history.replaceState(null, '', url.toString());
        if (state.pendingEnd && state.pendingEnd.story_id === storyId) await maybePromptMemory();
        return true;
    }
    // 页面只保留一个模态框实例；所有请求串行展示，避免异步回执覆盖尚未选择的确认框。
    function presentModal(options, resolve) {
        var modal = $('theater-modal');
        if (modal.hidden) {
            modalReturnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }
        $('theater-modal-title').textContent = options.title;
        $('theater-modal-body').textContent = options.body;
        // 完整演绎只扩展当前只读弹窗，其他确认框继续沿用紧凑尺寸。
        modal.querySelector('.theater-modal').classList.toggle('theater-transcript-modal', options.transcript === true);
        if (options.keepError !== true) $('theater-modal-error').hidden = true;
        var cancel = $('theater-modal-cancel');
        var confirm = $('theater-modal-confirm');
        cancel.hidden = options.singleAction === true;
        cancel.textContent = options.cancelLabel;
        confirm.textContent = options.confirmLabel;
        confirm.classList.toggle('theater-danger', options.danger === true);
        modalPersistent = options.persistent === true;
        modalResolve = resolve;
        modal.hidden = false;
        // 初始焦点放在弹窗容器，避免第一个按钮在弹出瞬间出现误导性的选中描边；Tab 后仍保留按钮焦点环。
        modal.querySelector('.theater-modal').focus();
    }
    function drainModalQueue() {
        if (modalResolve || modalPersistent || !modalQueue.length) return;
        var request = modalQueue.shift();
        presentModal(request.options, request.resolve);
    }
    function showModal(options) {
        return new Promise(function (resolve) {
            // 归档失败后的重试继续占用当前弹窗，不让排队中的普通确认插入其中。
            if (modalPersistent && !modalResolve && options.persistent === true) {
                presentModal(options, resolve);
                return;
            }
            modalQueue.push({ options: options, resolve: resolve });
            drainModalQueue();
        });
    }
    function restoreModalFocus() {
        var target = modalReturnFocus;
        modalReturnFocus = null;
        if (target && target.isConnected && typeof target.focus === 'function') target.focus();
    }
    function closeModal(confirmed) {
        if ($('theater-modal').hidden) return;
        if (!modalPersistent) {
            $('theater-modal').hidden = true;
            restoreModalFocus();
        }
        var resolve = modalResolve;
        modalResolve = null;
        if (resolve) resolve(confirmed);
        if (!modalPersistent) Promise.resolve().then(drainModalQueue);
    }
    function hideModal() {
        var resolve = modalResolve;
        modalResolve = null;
        $('theater-modal').hidden = true;
        modalPersistent = false;
        setModalBusy(false);
        restoreModalFocus();
        if (resolve) resolve(false);
        Promise.resolve().then(drainModalQueue);
    }
    function setModalBusy(busy) {
        $('theater-modal-cancel').disabled = busy;
        $('theater-modal-confirm').disabled = busy;
    }
    // 启动演绎前必须等本体回执 launch-ready，避免选剧页先关闭导致快照丢失。
    async function handoff(snapshot, action) {
        var launchId = createId('theater_launch_');
        var payload = {
            action: 'theater:launch-request', launch_id: launchId, launch_action: action,
            story_id: state.storyId, session_id: snapshot.session.session_id, revision: snapshot.session.revision,
            // 开场用量随启动回执传给演绎页，仅用于显示，不成为会话或剧情字段。
            token_usage: snapshot.token_usage || null
        };
        setStatus('theater.connectingNeko', '演出已准备好，正在连接 N.E.K.O 本体');
        return new Promise(function (resolve) {
            var settled = false;
            function ready(event) {
                var message = event && event.data;
                if (!message || message.action !== 'theater:launch-ready' || message.launch_id !== launchId) return;
                settled = true;
                cleanup();
                resolve(true);
            }
            function cleanup() {
                window.removeEventListener('message', ready);
                if (state.channel) state.channel.removeEventListener('message', ready);
            }
            window.addEventListener('message', ready);
            if (state.channel) state.channel.addEventListener('message', ready);
            postMessage(payload, true);
            // 本体先用最长 30 秒读取权威 Session，再最多等待约 8 秒挂载 React 胶囊；
            // 选剧页必须覆盖完整链路，不能在本体仍可能成功启动时提前允许第二次启动。
            window.setTimeout(function () { if (!settled) { cleanup(); resolve(false); } }, 40000);
        });
    }
    async function launchSnapshot(snapshot, action) {
        var ready = await handoff(snapshot, action);
        if (ready) { window.close(); return; }
        setFeedback(t('theater.connectNekoFailed', '无法连接 N.E.K.O 本体，请保持本体开启后重试。'), true);
        setStatus('theater.failed', '出错了');
    }
    async function startSession(replaceExisting) {
        if (state.busy || !state.storyId || !state.characterId) return;
        var startCharacterEpoch = characterEpoch;
        var startCharacterId = state.characterId;
        setBusy(true); setFeedback('');
        try {
            var result = await requestJson(api.start, { method: 'POST', body: {
                story_id: state.storyId,
                session_id: createId('numeric_capsule_session_'),
                character_id: startCharacterId,
                replace_existing: replaceExisting === true
            }});
            if (startCharacterEpoch !== characterEpoch) return;
            if (!result.ok) throw new Error(result.reason || 'start_failed');
            state.session = result.session;
            renderActions();
            await launchSnapshot(result, replaceExisting ? 'restart' : (result.resumed ? 'continue' : 'start'));
        } catch (_) {
            setFeedback(t('theater.startFailed', '启动演出失败，请重试。'), true);
            setStatus('theater.failed', '出错了');
        } finally { setBusy(false); }
    }
    async function continueSession() {
        var kind = sessionKind();
        if (!state.session || (kind !== 'active' && kind !== 'paused')) return;
        var continueCharacterEpoch = characterEpoch;
        setBusy(true);
        try {
            var result = kind === 'paused'
                ? await requestJson(api.resume, { method: 'POST', body: {
                    story_id: state.storyId,
                    session_id: state.session.session_id,
                    base_revision: state.session.revision,
                    base_lifecycle_revision: state.session.lifecycle_revision
                }})
                : await requestJson('/api/theater-numeric/session/' + encodeURIComponent(state.session.session_id) + '?story_id=' + encodeURIComponent(state.storyId));
            if (continueCharacterEpoch !== characterEpoch) return;
            if (!result.ok) throw new Error(result.reason || 'restore_failed');
            state.session = result.session;
            renderActions();
            await launchSnapshot(result, 'continue');
        } catch (_) { setFeedback(t('theater.continueFailed', '继续演出失败，请重试。'), true); }
        finally { setBusy(false); }
    }
    // 选剧页是胶囊结束按钮之外的独立兜底入口；成功后同步本体运行时解除锁定。
    async function endSession() {
        if (state.busy || sessionKind() !== 'active' || !state.session) return;
        var targetStoryId = state.storyId;
        var targetSessionId = String(state.session.session_id || '');
        var targetRevision = Number(state.session.revision || 0);
        var targetLifecycleRevision = Number(state.session.lifecycle_revision || 0);
        var targetCharacterEpoch = characterEpoch;
        var confirmed = await showModal({
            title: t('theater.endPerformance', '结束演绎'),
            body: t('theater.endConfirm', '确定结束当前演绎吗？'),
            cancelLabel: t('common.cancel', '取消'),
            confirmLabel: t('theater.endPerformance', '结束演绎'),
            danger: true
        });
        if (!confirmed || targetCharacterEpoch !== characterEpoch || !selectedSessionMatches(targetStoryId, targetSessionId) || sessionKind() !== 'active') return;
        setBusy(true); setFeedback('');
        var endResponseReceived = false;
        try {
            var result = await requestJson(api.end, { method: 'POST', body: {
                story_id: targetStoryId,
                session_id: targetSessionId,
                base_revision: targetRevision,
                base_lifecycle_revision: targetLifecycleRevision
            }});
            endResponseReceived = true;
            if (targetCharacterEpoch !== characterEpoch || !selectedSessionMatches(targetStoryId, targetSessionId)) return;
            if (!result.ok || !result.session) throw new Error(result.reason || 'end_failed');
            state.session = result.session;
            state.pendingEnd = result.end_receipt_id && result.archive_status !== 'skipped' && result.archive_status !== 'written' ? {
                story_id: targetStoryId,
                session_id: result.session.session_id,
                revision: result.session.revision,
                end_receipt_id: result.end_receipt_id || '',
                archive_request_id: result.archive_request_id || ''
            } : null;
            postMessage({ action: 'theater:external-end', story_id: targetStoryId, session_id: result.session.session_id });
            renderDetail();
            setStatus('theater.paused', '已退出');
            if (!result.end_receipt_id) {
                setFeedback(t('theater.storyMemoryForgetFailed', '剧本记忆删除失败，请重试。'), true);
            }
            await maybePromptMemory();
        } catch (_) {
            // HTTP 已返回时显示业务失败；只有 fetch 没有拿到响应才归为本地服务连接问题。
            setFeedback(
                endResponseReceived
                    ? t('theater.endStateFailed', '当前演绎状态无法结束，请返回剧本页后重试。')
                    : t('theater.endConnectionFailed', '无法连接 N.E.K.O 本地服务，请确认程序仍在运行后重试。'),
                true
            );
        } finally { setBusy(false); }
    }
    // “开始”同时承担首次创建和结束后再次开局；只有后者需要替换确认。
    async function beginSession() {
        if (sessionKind() === 'active') return;
        if (sessionKind() === 'new') {
            await startSession(false);
            return;
        }
        var targetStoryId = state.storyId;
        var targetSessionId = String(state.session && state.session.session_id || '');
        var targetSessionKind = sessionKind();
        var confirmed = await showModal({
            title: t('theater.startAgainConfirmTitle', '开始新的演绎？'),
            body: t('theater.startAgainConfirmBody', '这会用新的演绎替换当前角色的已结束记录。'),
            cancelLabel: t('common.cancel', '取消'), confirmLabel: t('theater.start', '开始'), danger: true
        });
        if (
            confirmed
            && selectedSessionMatches(targetStoryId, targetSessionId)
            && sessionKind() === targetSessionKind
        ) await startSession(true);
    }
    // 删除前由服务端汇总活跃角色，确认后再执行剧本包和 Session 的事务删除。
    async function deleteStory() {
        if (!state.storyId || state.busy) return;
        var deletedStoryId = state.storyId;
        setBusy(true);
        try {
            var preview = await requestJson(api.packages + '/' + encodeURIComponent(deletedStoryId) + '/delete-preview');
            if (!preview.ok) throw new Error('preview_failed');
            var names = Array.isArray(preview.active_catgirl_names) ? preview.active_catgirl_names.filter(Boolean) : [];
            var body = names.length
                ? t('theater.deleteStoryActiveConfirm', '跟' + names.join('、') + '的演绎还未结束，是否确认删除？', { names: names.join('、') })
                : t('theater.deleteStoryConfirm', '是否确认删除？');
            var confirmed = await showModal({ title: t('theater.deleteStory', '删除剧本'), body: body, cancelLabel: t('common.cancel', '取消'), confirmLabel: t('theater.deleteStory', '删除剧本'), danger: true });
            if (!confirmed) return;
            var result = await requestJson(api.packages + '/' + encodeURIComponent(deletedStoryId), { method: 'DELETE' });
            if (!result.ok) throw new Error('delete_failed');
            // 服务端已级联删除该剧本的 Session；通知所有本体释放仍在展示的胶囊运行态。
            postMessage({ action: 'theater:story-deleted', story_id: deletedStoryId });
            await loadStories(deletedStoryId);
            setStatus('theater.storyDeleted', '剧本已删除');
        } catch (_) { setFeedback(t('theater.storyDeleteFailed', '剧本删除失败，请重试。'), true); }
        finally { setBusy(false); }
    }
    async function toggleArchivePin(archive) {
        if (state.busy || !archive || !archive.session_id) return;
        var pinCharacterEpoch = characterEpoch;
        setBusy(true); setFeedback('');
        try {
            var result = await requestJson(api.pinMemoryArchive, { method: 'POST', body: {
                story_id: state.storyId,
                session_id: archive.session_id,
                pinned: !archive.pinned
            }});
            if (pinCharacterEpoch !== characterEpoch) return;
            if (!result.ok) throw new Error('pin_failed');
            await loadMemoryArchives(state.storyId);
        } catch (_) {
            setFeedback(t('theater.memoryPinFailed', '收藏状态更新失败，请重试。'), true);
        } finally { setBusy(false); }
    }
    async function forgetStoryMemory() {
        if (!state.storyId || !state.characterId || state.busy) return;
        var targetStoryId = state.storyId;
        var targetCharacterId = state.characterId;
        var targetCharacterEpoch = characterEpoch;
        var confirmed = await showModal({
            title: t('theater.forgetStoryMemoryTitle', '忘记该剧本？'),
            body: t('theater.forgetStoryMemoryBody', '这会删除当前猫娘对该剧本的摘要、时间索引和完整演绎档案，但不会删除剧本或当前进度。'),
            cancelLabel: t('common.cancel', '取消'),
            confirmLabel: t('theater.forgetStoryMemory', '忘记该剧本'),
            danger: true
        });
        if (!confirmed || targetCharacterEpoch !== characterEpoch || state.storyId !== targetStoryId) return;
        setBusy(true); setFeedback('');
        try {
            var result = await requestJson(api.forgetMemory, {
                method: 'POST', body: {
                    story_id: targetStoryId,
                    character_id: targetCharacterId
                }
            });
            if (targetCharacterEpoch !== characterEpoch || state.storyId !== targetStoryId) return;
            if (!result.ok) throw new Error('forget_failed');
            state.archives = [];
            state.pendingEnd = null;
            await loadStories(targetStoryId);
            setFeedback(t('theater.storyMemoryForgotten', '已忘记该剧本的演绎记忆。'));
        } catch (_) {
            setFeedback(t('theater.storyMemoryForgetFailed', '剧本记忆删除失败，请重试。'), true);
        } finally { setBusy(false); }
    }
    async function importStory(file) {
        if (!file || state.busy) return;
        setBusy(true);
        try {
            var payload = JSON.parse(await file.text());
            var result = await requestJson(api.importStory, { method: 'POST', body: payload });
            if (!result.ok) throw new Error(result.reason || 'import_failed');
            await loadStories(String(result.package.story_id || ''));
            setFeedback(t('theater.importStorySuccess', '剧本导入成功。'));
        } catch (_) { setFeedback(t('theater.importStoryInvalid', '剧本格式未通过 Numeric v2 校验。'), true); }
        finally { $('theater-import-input').value = ''; setBusy(false); }
    }
    // 记忆询问只消费服务端结束回执，不接收浏览器自行拼接的演绎正文。
    async function maybePromptMemory() {
        var receipt = state.pendingEnd;
        if (state.memoryPromptActive || !receipt || !state.session || state.session.status !== 'ended' || state.session.session_id !== receipt.session_id) return;
        // 同一次结束归档的重试必须复用稳定 ID；旧回执没有服务端 ID 时只在首次询问生成一次。
        if (!receipt.archive_request_id) receipt.archive_request_id = createId('theater_archive_');
        state.memoryPromptActive = true;
        var archiveFailed = false;
        try {
            while (state.pendingEnd === receipt) {
                var remember = await showModal({
                    title: t('theater.rememberPerformanceTitle', '是否让 N.E.K.O 记下本次演绎内容？'),
                    body: t('theater.rememberPerformanceRetentionBody', '猫娘的日常记忆只保留公开摘要，完整公开演绎保存在小剧场档案中；隐藏数值与路线条件不会写入。'),
                    cancelLabel: t('theater.skipMemory', '暂不记录'), confirmLabel: t('theater.saveMemory', '记下本次演绎'),
                    persistent: true, keepError: archiveFailed
                });
                if (state.pendingEnd !== receipt) {
                    // 角色切换等程序化关闭不等于玩家选择“暂不记录”，不得发送 skip 请求。
                    hideModal();
                    break;
                }
                var body = { story_id: receipt.story_id, session_id: receipt.session_id, revision: receipt.revision, end_receipt_id: receipt.end_receipt_id };
                setModalBusy(true);
                $('theater-modal-body').textContent = remember
                    ? t('theater.memorySaving', '正在记录本次演绎...')
                    : t('theater.memorySkipping', '正在保留本次选择...');
                var result;
                try {
                    result = await requestJson(remember ? api.archive : api.skipArchive, {
                        method: 'POST',
                        body: Object.assign(body, remember ? { archive_request_id: receipt.archive_request_id } : {})
                    });
                } catch (_) {
                    result = { ok: false };
                }
                setModalBusy(false);
                if (state.pendingEnd !== receipt) {
                    hideModal();
                    break;
                }
                if (!result.ok) {
                    archiveFailed = true;
                    $('theater-modal-error').hidden = false;
                    $('theater-modal-error').textContent = t('theater.memorySaveFailed', '演绎记录写入失败，请稍后重试。');
                    continue;
                }
                if (state.pendingEnd === receipt) state.pendingEnd = null;
                hideModal();
                if (remember) {
                    await loadMemoryArchives(receipt.story_id);
                    setFeedback(t('theater.memorySaved', '本次演绎已记下。'));
                }
                break;
            }
        } finally {
            state.memoryPromptActive = false;
            // 处理旧回执期间若收到更新回执，释放串行闸门后继续消费新事实。
            if (state.pendingEnd && state.pendingEnd !== receipt) {
                maybePromptMemory().catch(function () {
                    setFeedback(t('theater.memorySaveFailed', '演绎记录写入失败，请稍后重试。'), true);
                });
            }
        }
    }
    var memoryListEpoch = 0;
    async function loadMemoryStories(expectedCharacterEpoch, expectedListEpoch, preferredStoryId, expectedSelectionEpoch) {
        var result;
        try { result = await requestJson(api.memoryStories); } catch (_) { result = {ok: false}; }
        if (characterEpoch !== expectedCharacterEpoch || memoryListEpoch !== expectedListEpoch) return;
        if (!result.ok || result.character_id !== state.characterId) {
            setFeedback(t('theater.memoryStoryListFailed', '保留的剧本记忆暂时无法读取，请重新加载。'), true);
            return;
        }
        (result.stories || []).forEach(function (story) {
            if (!state.stories.some(function (existing) { return existing.story_id === story.story_id; })) state.stories.push(story);
        });
        renderStories();
        // An installed fallback must not discard a requested memory-only story.
        // Any later selection, including A -> B -> A, takes precedence.
        if (storySelectionEpoch === expectedSelectionEpoch) {
            var preferred = state.stories.find(function (story) { return String(story.story_id) === preferredStoryId; });
            if (preferred && state.storyId !== preferredStoryId) await selectStory(preferredStoryId, true);
            else if (!state.storyId && state.stories.length) await selectStory(String(state.stories[0].story_id), true);
        }
        if (result.memory_available === false) setFeedback(t('theater.memoryStoryListFailed', '保留的剧本记忆暂时无法读取，请重新加载。'), true);
    }
    async function loadStories(preferredStoryId, expectedCharacterEpoch) {
        var listEpoch = ++memoryListEpoch;
        var storiesCharacterEpoch = expectedCharacterEpoch === undefined
            ? characterEpoch
            : expectedCharacterEpoch;
        setStatus('theater.loading', '正在准备舞台...');
        var result = await requestJson(api.stories);
        if (!result.ok || !Array.isArray(result.stories)) throw new Error('stories_failed');
        if (storiesCharacterEpoch !== characterEpoch) return false;
        state.stories = result.stories;
        state.characterId = String(result.character_id || '');
        var queryStoryId = new URLSearchParams(window.location.search).get('story_id') || '';
        var requestedStoryId = preferredStoryId || queryStoryId;
        state.storyId = requestedStoryId || String((state.stories[0] || {}).story_id || '');
        if (!state.stories.some(function (story) { return String(story.story_id) === state.storyId; })) state.storyId = String((state.stories[0] || {}).story_id || '');
        renderStories(); renderDetail();
        // 导入/删除期间 loadStories 也会在 busy 状态内运行，允许这一次内部详情刷新。
        if (state.storyId) {
            if (!(await selectStory(state.storyId, true))) return false;
        } else setStatus('theater.ready', '就绪');
        // Memory-server reads do not delay starting an installed story.
        loadMemoryStories(storiesCharacterEpoch, listEpoch, requestedStoryId, storySelectionEpoch).catch(function () {
            if (memoryListEpoch === listEpoch) setFeedback(t('theater.memoryStoryListFailed', '保留的剧本记忆暂时无法读取，请重新加载。'), true);
        });
        return true;
    }
    // 本体结束演绎后只传定位回执；选择页重新读取服务端状态再显示询问。
    function handleCrossWindowMessage(event) {
        if (event && event.origin && event.origin !== window.location.origin) return;
        var message = event && event.data;
        if (!message || typeof message !== 'object') return;
        if (String(message.action || '').indexOf('theater:') === 0 && message.schema !== MESSAGE_SCHEMA) return;
        if (message.action === 'theater:post-end' && message.story_id && message.session_id && message.end_receipt_id) {
            // BroadcastChannel、opener 或重复 ready 都可能重送同一事实；按服务端稳定回执 ID 去重。
            if (state.pendingEnd && state.pendingEnd.end_receipt_id === message.end_receipt_id) return;
            state.pendingEnd = message;
            if (state.busy) pendingEndSelection = message;
            else selectPendingEnd(message);
        } else if (message.action === 'catgirl_switched') {
            // 清除旧角色详情和确认框，并让所有已经发出的同 story_id 请求失效后重新读取当前角色。
            characterEpoch += 1;
            state.characterId = '';
            state.pendingEnd = null;
            state.session = null;
            state.archives = [];
            modalQueue.splice(0).forEach(function (request) { request.resolve(false); });
            hideModal();
            setFeedback('');
            renderDetail();
            var selectedStoryId = state.storyId;
            var switchCharacterEpoch = characterEpoch;
            loadStories(selectedStoryId, switchCharacterEpoch).catch(function () {
                if (switchCharacterEpoch !== characterEpoch) return;
                setStatus('theater.failed', '出错了');
                setFeedback(t('theater.storyListFailed', '剧本列表加载失败，请重新加载。'), true);
            });
        }
    }
    function bindModalKeyboard() {
        $('theater-modal-cancel').addEventListener('click', function () { closeModal(false); });
        $('theater-modal-confirm').addEventListener('click', function () { closeModal(true); });
        document.addEventListener('keydown', function (event) {
            if ($('theater-modal').hidden) return;
            if (event.key === 'Escape') { event.preventDefault(); closeModal(false); return; }
            if (event.key !== 'Tab') return;
            var buttons = [$('theater-modal-cancel'), $('theater-modal-confirm')].filter(function (node) { return !node.disabled && !node.hidden; });
            if (!buttons.length) return;
            var index = buttons.indexOf(document.activeElement);
            if (index < 0) { event.preventDefault(); buttons[event.shiftKey ? buttons.length - 1 : 0].focus(); return; }
            if (event.shiftKey && (index <= 0)) { event.preventDefault(); buttons[buttons.length - 1].focus(); }
            else if (!event.shiftKey && index === buttons.length - 1) { event.preventDefault(); buttons[0].focus(); }
        });
    }
    document.addEventListener('DOMContentLoaded', function () {
        if (typeof BroadcastChannel !== 'undefined') {
            try { state.channel = new BroadcastChannel('neko_page_channel'); state.channel.addEventListener('message', handleCrossWindowMessage); } catch (_) { state.channel = null; }
        }
        window.addEventListener('message', handleCrossWindowMessage);
        bindModalKeyboard();
        $('theater-import-btn').addEventListener('click', function () { $('theater-import-input').click(); });
        $('theater-empty-import-btn').addEventListener('click', function () { $('theater-import-input').click(); });
        $('theater-import-input').addEventListener('change', function () { importStory(this.files && this.files[0]); });
        $('theater-start-btn').addEventListener('click', beginSession);
        $('theater-continue-btn').addEventListener('click', continueSession);
        $('theater-end-btn').addEventListener('click', endSession);
        $('theater-delete-btn').addEventListener('click', deleteStory);
        $('theater-forget-memory-btn').addEventListener('click', forgetStoryMemory);
        loadStories().catch(function () { setStatus('theater.failed', '出错了'); setFeedback(t('theater.storyListFailed', '剧本列表加载失败，请重新加载。'), true); });
        postMessage({ action: 'theater:selector-ready' });
    });
})();
