/**
 * Cat appearance local text chat.
 *
 * The processing page (the page that owns nekoCatMind) is authoritative for
 * the current temporary transcript. Standalone Chat pages only display the
 * snapshot delivered by the existing goodbye cross-window state channel.
 */
(function () {
    'use strict';

    window.reactChatWindowHost = window.reactChatWindowHost || {};
    var I = window.__appReactChatWindowParts || (window.__appReactChatWindowParts = {});
    var MAX_ITEMS = 40;
    var MAX_SEEN_REQUESTS = 128;
    var REPLY_DELAY_MIN_MS = 320;
    var REPLY_DELAY_SPAN_MS = 480;
    var CAT1_HISS_STRETCH_EASTER_EGG_RATE = 0.03;
    var state = {
        active: false,
        tier: 'none',
        enteredAt: 0,
        items: []
    };
    var replyQueue = [];
    var replyTimer = 0;
    var itemSequence = 0;
    var seenRequestIds = Object.create(null);
    var seenRequestOrder = [];
    var lastReplyText = '';
    var lastAppliedSnapshotAt = 0;

    function normalizeTier(value) {
        return value === 'cat1' || value === 'cat2' || value === 'cat3' ? value : 'cat1';
    }

    function isCanonicalPage() {
        var pathname = (window.location && window.location.pathname) || '';
        if (pathname === '/chat' || pathname === '/chat/'
            || pathname === '/chat_full' || pathname === '/chat_full/') {
            return false;
        }
        return !!(
            window.nekoCatMind
            && typeof window.nekoCatMind.getState === 'function'
        );
    }

    function readCatMindState() {
        if (!isCanonicalPage()) return null;
        try {
            var current = window.nekoCatMind.getState();
            return current && typeof current === 'object' ? current : null;
        } catch (_) {
            return null;
        }
    }

    function cloneItem(item) {
        if (!item || typeof item !== 'object') return null;
        var role = item.role === 'user' ? 'user' : (item.role === 'assistant' ? 'assistant' : '');
        var text = typeof item.text === 'string' ? item.text : '';
        var id = typeof item.id === 'string' ? item.id : '';
        if (!role || !text || !id) return null;
        return {
            id: id,
            role: role,
            text: text,
            createdAt: Number.isFinite(Number(item.createdAt)) ? Number(item.createdAt) : Date.now(),
            sequence: Number.isFinite(Number(item.sequence)) ? Number(item.sequence) : 0,
            requestId: typeof item.requestId === 'string' ? item.requestId : ''
        };
    }

    function cloneItems(items) {
        return (Array.isArray(items) ? items : []).map(cloneItem).filter(Boolean).slice(-MAX_ITEMS);
    }

    function clearReplyWork() {
        if (replyTimer) {
            window.clearTimeout(replyTimer);
            replyTimer = 0;
        }
        replyQueue = [];
        seenRequestIds = Object.create(null);
        seenRequestOrder = [];
        lastReplyText = '';
    }

    function clearCycle() {
        clearReplyWork();
        state.items = [];
        itemSequence = 0;
    }

    function renderHost() {
        if (typeof I.renderWindow === 'function') {
            I.renderWindow();
        }
    }

    function leaveAutomaticDock() {
        if (I.idleDockTriggeredMinimize || I.idleDockActive) {
            if (typeof I.exitIdleDock === 'function') {
                I.exitIdleDock({});
            }
        }
        if (I.electronIdleDockTriggeredCollapse || I.electronIdleDockActive || I.electronIdleDockDesired) {
            if (typeof I.exitElectronIdleDock === 'function') {
                I.exitElectronIdleDock({});
            }
        }
    }

    function keepCompactInputAvailable() {
        if (!state.active) return;
        leaveAutomaticDock();
        if (typeof I.getCurrentChatSurfaceMode === 'function'
            && I.getCurrentChatSurfaceMode() === 'compact'
            && typeof I.setCompactChatState === 'function') {
            I.setCompactChatState('input');
        }
    }

    function syncHostPresentation(previousActive, previousAttachmentsVisible) {
        if (previousActive === state.active) return;
        if (typeof I.applyGalgameBodyClass === 'function') {
            I.applyGalgameBodyClass();
        }
        if (typeof I.syncComposerAttachmentsVisibility === 'function') {
            I.syncComposerAttachmentsVisibility(previousAttachmentsVisible);
        }
        if (typeof I.dispatchHostEvent === 'function') {
            I.dispatchHostEvent('galgame-mode-change', {
                enabled: typeof I.getEffectiveGalgameEnabled === 'function'
                    ? I.getEffectiveGalgameEnabled()
                    : false
            });
        }
    }

    function applySnapshot(snapshot) {
        var next = snapshot && typeof snapshot === 'object' ? snapshot : {};
        var nextUpdatedAt = Number(next.updatedAt);
        if (Number.isFinite(nextUpdatedAt) && nextUpdatedAt > 0) {
            if (nextUpdatedAt < lastAppliedSnapshotAt) return false;
            lastAppliedSnapshotAt = nextUpdatedAt;
        }
        var nextActive = next.active === true;
        var nextEnteredAt = nextActive && Number.isFinite(Number(next.enteredAt))
            ? Number(next.enteredAt)
            : 0;
        var previousActive = state.active;
        var previousAttachmentsVisible = typeof I.getEffectiveComposerAttachmentsVisible === 'function'
            ? I.getEffectiveComposerAttachmentsVisible()
            : undefined;
        var cycleChanged = state.enteredAt !== nextEnteredAt || state.active !== nextActive;
        if (cycleChanged) {
            clearReplyWork();
            itemSequence = 0;
        }
        state.active = nextActive;
        state.tier = nextActive ? normalizeTier(next.tier) : 'none';
        state.enteredAt = nextEnteredAt;
        state.items = nextActive ? cloneItems(next.items) : [];
        if (!nextActive) {
            clearCycle();
        } else {
            state.items.forEach(function (item) {
                itemSequence = Math.max(itemSequence, Number(item.sequence) || 0);
            });
            keepCompactInputAvailable();
        }
        syncHostPresentation(previousActive, previousAttachmentsVisible);
        renderHost();
        return true;
    }

    function getSnapshot() {
        return {
            active: state.active,
            tier: state.tier,
            enteredAt: state.enteredAt,
            items: cloneItems(state.items)
        };
    }

    function syncFromCatMind() {
        var current = readCatMindState();
        if (!current) return getSnapshot();
        var nextActive = current.active === true;
        var nextEnteredAt = nextActive && Number.isFinite(Number(current.enteredAt))
            ? Number(current.enteredAt)
            : 0;
        var previousActive = state.active;
        var previousAttachmentsVisible = typeof I.getEffectiveComposerAttachmentsVisible === 'function'
            ? I.getEffectiveComposerAttachmentsVisible()
            : undefined;
        if (!nextActive || state.enteredAt !== nextEnteredAt) {
            clearCycle();
        }
        state.active = nextActive;
        state.tier = nextActive ? normalizeTier(current.tier) : 'none';
        state.enteredAt = nextEnteredAt;
        if (!nextActive) state.items = [];
        keepCompactInputAvailable();
        syncHostPresentation(previousActive, previousAttachmentsVisible);
        renderHost();
        return getSnapshot();
    }

    function publishSnapshot(reason) {
        if (!isCanonicalPage()) return false;
        syncFromCatMind();
        if (typeof window.postGoodbyeChatComposerHiddenState !== 'function') return false;
        window.postGoodbyeChatComposerHiddenState(undefined, reason || 'cat-local-chat');
        return true;
    }

    function pick(values, random) {
        if (!Array.isArray(values) || !values.length) return undefined;
        var index = Math.floor(random() * values.length);
        return values[Math.max(0, Math.min(values.length - 1, index))];
    }

    function composeReply(tier, randomFn) {
        var lexicon = window.nekoCatLocalChatLexicon;
        if (!lexicon) return '';
        var random = typeof randomFn === 'function' ? randomFn : Math.random;
        var normalizedTier = normalizeTier(tier);
        var shape = lexicon.tiers[normalizedTier] || lexicon.tiers.cat1;
        var meowPool = Array.isArray(shape.meows) ? shape.meows : lexicon.meows;
        var meowCount = Number(pick(shape.meowCounts, random)) || 1;
        var voiceParts = [];
        for (var index = 0; index < meowCount; index += 1) {
            var meow = pick(meowPool, random) || '';
            if (!meow) return '';
            voiceParts.push(meow);
            if (index < meowCount - 1 &&
                pick(shape.infixPunctuationSlots, random) === true) {
                var infixGroup = lexicon.punctuation[shape.infixPunctuationGroup];
                var infix = pick(infixGroup, random) || '';
                if (infix) voiceParts.push(infix);
            }
        }
        var punctuation = pick(lexicon.punctuation[shape.punctuationGroup], random) || '';
        var leadingPause = pick(shape.leadingPauseSlots, random) === true
            ? (pick(lexicon.punctuation.pause, random) || '')
            : '';
        var face = pick(shape.kaomojiSlots, random) === true
            ? (pick(lexicon.kaomoji[shape.kaomojiGroup], random) || '')
            : '';
        return leadingPause + voiceParts.join('') + punctuation + face;
    }

    function chooseReply(tier) {
        var candidate = composeReply(tier);
        for (var attempt = 0; attempt < 3 && candidate === lastReplyText; attempt += 1) {
            candidate = composeReply(tier);
        }
        lastReplyText = candidate;
        return candidate;
    }

    function composeHissStretchReply(randomFn) {
        var lexicon = window.nekoCatLocalChatLexicon;
        var hiss = lexicon && lexicon.easterEggs && lexicon.easterEggs.hissStretch;
        if (!hiss) return '';
        var random = typeof randomFn === 'function' ? randomFn : Math.random;
        var voice = pick(hiss.voices, random) || '';
        var punctuation = pick(hiss.punctuation, random) || '';
        var face = pick(hiss.kaomoji, random) || '';
        return voice && punctuation && face ? voice + punctuation + face : '';
    }

    function requestCat1StretchPresentation() {
        var presentation = window.NekoCatIdlePresentation;
        if (!presentation || typeof presentation.requestCat1Stretch !== 'function') return false;
        try {
            return presentation.requestCat1Stretch() === true;
        } catch (_) {
            return false;
        }
    }

    function chooseHissStretchReply(tier) {
        if (normalizeTier(tier) !== 'cat1' || Math.random() >= CAT1_HISS_STRETCH_EASTER_EGG_RATE) {
            return '';
        }
        var candidate = composeHissStretchReply();
        if (!candidate || !requestCat1StretchPresentation()) return '';
        lastReplyText = candidate;
        return candidate;
    }

    function appendItem(role, text, requestId) {
        itemSequence += 1;
        var now = Date.now();
        state.items.push({
            id: 'cat-local-' + state.enteredAt + '-' + itemSequence,
            role: role,
            text: text,
            createdAt: now,
            sequence: itemSequence,
            requestId: requestId
        });
        if (state.items.length > MAX_ITEMS) {
            state.items = state.items.slice(-MAX_ITEMS);
        }
    }

    function rememberRequestId(requestId) {
        if (seenRequestIds[requestId]) return false;
        seenRequestIds[requestId] = true;
        seenRequestOrder.push(requestId);
        if (seenRequestOrder.length > MAX_SEEN_REQUESTS) {
            delete seenRequestIds[seenRequestOrder.shift()];
        }
        return true;
    }

    function observeAcceptedLocalText(requestId) {
        var catMind = window.nekoCatMind;
        if (!catMind || typeof catMind.observe !== 'function') return false;
        var current = readCatMindState();
        if (!current || current.active !== true || Number(current.enteredAt) !== state.enteredAt) {
            return false;
        }
        try {
            return !!catMind.observe({
                type: 'cat_local_text_received',
                source: 'cat-local-chat',
                tier: normalizeTier(current.tier),
                timestamp: Date.now(),
                detail: {
                    requestId: requestId,
                    enteredAt: state.enteredAt
                }
            });
        } catch (_) {
            return false;
        }
    }

    function scheduleNextReply() {
        if (replyTimer || !replyQueue.length) return;
        var delay = REPLY_DELAY_MIN_MS + Math.floor(Math.random() * REPLY_DELAY_SPAN_MS);
        replyTimer = window.setTimeout(function () {
            replyTimer = 0;
            var pending = replyQueue.shift();
            var current = readCatMindState();
            if (!pending || !current || current.active !== true
                || Number(current.enteredAt) !== pending.enteredAt) {
                syncFromCatMind();
                publishSnapshot('cat-local-chat-reply-invalidated');
                scheduleNextReply();
                return;
            }
            state.tier = normalizeTier(current.tier);
            var reply = chooseHissStretchReply(state.tier) || chooseReply(state.tier);
            if (!reply) {
                publishSnapshot('cat-local-chat-reply-invalidated');
                scheduleNextReply();
                return;
            }
            appendItem('assistant', reply, pending.requestId);
            publishSnapshot('cat-local-chat-reply');
            scheduleNextReply();
        }, delay);
    }

    function submit(payload) {
        if (!isCanonicalPage()) return false;
        syncFromCatMind();
        var text = payload && typeof payload.text === 'string' ? payload.text.trim() : '';
        var requestId = payload && typeof payload.requestId === 'string' ? payload.requestId.trim() : '';
        var requestedEnteredAt = Number(payload && payload.enteredAt);
        if (!text || !requestId || !state.active) return false;
        if (Number.isFinite(requestedEnteredAt) && requestedEnteredAt > 0
            && requestedEnteredAt !== state.enteredAt) return false;
        if (!rememberRequestId(requestId)) return true;
        appendItem('user', text, requestId);
        observeAcceptedLocalText(requestId);
        replyQueue.push({ requestId: requestId, enteredAt: state.enteredAt });
        publishSnapshot('cat-local-chat-user');
        scheduleNextReply();
        return true;
    }

    I.isCatLocalChatActive = function isCatLocalChatActive() {
        if (state.active) return true;
        var current = readCatMindState();
        return !!(current && current.active === true);
    };

    I.getCatLocalChatDisplayMessages = function getCatLocalChatDisplayMessages() {
        if (!state.active || typeof I.normalizeMessage !== 'function') return [];
        return state.items.map(function (item, index) {
            return I.normalizeMessage({
                id: item.id,
                role: item.role,
                createdAt: item.createdAt,
                blocks: [{ type: 'text', text: item.text }],
                status: 'sent'
            }, I._sortKeySeq + index + 1);
        }).filter(Boolean);
    };

    I.submitCatLocalChatText = function submitCatLocalChatText(payload) {
        var text = payload && typeof payload.text === 'string' ? payload.text.trim() : '';
        if (!I.isCatLocalChatActive() || !text) return false;
        var requestId = payload && typeof payload.requestId === 'string' && payload.requestId
            ? payload.requestId
            : ('cat-local-' + Date.now() + '-' + Math.random().toString(36).slice(2, 8));
        if (typeof window.postCatLocalTextSubmit !== 'function') return false;
        return window.postCatLocalTextSubmit({
            text: text,
            requestId: requestId,
            enteredAt: state.enteredAt
        });
    };

    window.nekoCatLocalChatManager = Object.freeze({
        getSnapshot: getSnapshot,
        syncFromCatMind: syncFromCatMind,
        applySnapshot: applySnapshot,
        submit: submit,
        composeReply: composeReply,
        composeHissStretchReply: composeHissStretchReply
    });

    window.addEventListener('neko:cat-local-chat-state', function (event) {
        applySnapshot(event && event.detail);
    });
    window.addEventListener('neko:cat-local-chat-submit-request', function (event) {
        submit(event && event.detail);
    });
    window.addEventListener('neko:cat-local-active-change', function () {
        publishSnapshot('cat-local-active-change');
    });
    window.addEventListener('neko:goodbye-state-cleared', function () {
        publishSnapshot('goodbye-state-cleared');
    });
    window.addEventListener('neko:auto-goodbye:state-change', function (event) {
        var detail = event && event.detail;
        if (detail && detail.type === 'visual-tier') {
            publishSnapshot('cat-local-tier-change');
        }
    });
    window.addEventListener('neko:config-injected', function () {
        if (isCanonicalPage()) publishSnapshot('cat-local-config-injected');
    });

    var initialSnapshot = window.__nekoCatLocalChatState;
    if (initialSnapshot && typeof initialSnapshot === 'object') {
        applySnapshot(initialSnapshot);
    } else if (isCanonicalPage()) {
        syncFromCatMind();
    }
})();
