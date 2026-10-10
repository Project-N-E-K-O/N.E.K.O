/* Original-reply display ordering. Ordinary plugin messages bypass this module. */
(function () {
    'use strict';
    var requests = new Map();
    var captions = new Map();
    var pending = new Map();
    var seen = new Set();
    var compact = false;
    var currentCaptionTurn = '';
    var scheduled = false;
    var MAX_ENTRIES = 128;

    function trim(map) {
        while (map.size > MAX_ENTRIES) map.delete(map.keys().next().value);
    }

    function changed() {
        if (scheduled || pending.size === 0) return;
        scheduled = true;
        window.requestAnimationFrame(function () {
            scheduled = false;
            drain();
        });
    }

    function remember(detail, ended) {
        if (!detail || !detail.requestId || !detail.turnId) return;
        var key = String(detail.requestId);
        var state = requests.get(key) || { turns: new Set(), ended: false };
        state.turns.add(String(detail.turnId));
        state.ended = ended;
        requests.set(key, state);
        trim(requests);
        changed();
    }

    function rendered(messageId, caption) {
        var nodes = document.querySelectorAll(caption
            ? '[data-reply-tail-caption-turn-id]'
            : '[data-message-id], [data-compact-export-history-message-id]');
        for (var i = 0; i < nodes.length; i++) {
            var nodeId = caption ? nodes[i].getAttribute('data-reply-tail-caption-turn-id')
                : nodes[i].getAttribute('data-message-id')
                    || nodes[i].getAttribute('data-compact-export-history-message-id');
            if (nodeId !== messageId) continue;
            if (nodes[i].getClientRects().length === 0) continue;
            var animations = typeof nodes[i].getAnimations === 'function'
                ? nodes[i].getAnimations({ subtree: true }) : [];
            var active = animations.filter(function (animation) {
                return animation.playState === 'running'
                    && animation.effect && animation.effect.getComputedTiming().iterations !== Infinity;
            });
            if (active.length) {
                Promise.allSettled(active.map(function (animation) { return animation.finished; })).then(changed);
                return false;
            }
            if (typeof nodes[i].checkVisibility === 'function'
                    && !nodes[i].checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
            return true;
        }
        return false;
    }

    function drain() {
        var host = window.reactChatWindowHost;
        if (!host || typeof host.getState !== 'function') return;
        var messages = host.getState().messages || [];
        pending.forEach(function (entry, key) {
            var tail = entry.payload.reply_tail;
            if (Date.now() - entry.created > 120000) {
                pending.delete(key);
                return;
            }
            var state = requests.get(String(tail.request_id));
            if (!state || !state.ended) return;
            var queue = window._realisticGeminiQueue || [];
            if (queue.some(function (item) { return item && state.turns.has(String(item.turnId)); })) return;
            var anchors = messages.filter(function (message) {
                return message.role === 'assistant' && state.turns.has(String(message.turnId));
            });
            if (!anchors.length) {
                // An evicted original must never attach to the newest reply.
                if (messages.length >= 50) pending.delete(key);
                return;
            }
            var ownsCaption = compact && state.turns.has(currentCaptionTurn);
            var captionReady = ownsCaption && captions.get(currentCaptionTurn)
                && rendered(currentCaptionTurn, true);
            if (ownsCaption && !captionReady) return;
            // A visible original caption is sufficient even with history closed.
            // A superseded caption needs the original history anchor instead.
            if (!captionReady && !anchors.every(function (message) { return rendered(message.id, false); })) return;
            var anchor = anchors[anchors.length - 1];
            if (!Number.isFinite(anchor.sortKey)) return;
            var floor = Math.max(anchor.sortKey, state.lastTailSortKey || anchor.sortKey);
            var next = messages.find(function (message) { return message.sortKey > floor; });
            // Reserve the bounded queue's space once. Repeated bisection
            // exhausts floating-point precision at timestamp-sized keys.
            if (!state.tailStep) {
                state.tailStep = (next ? next.sortKey - floor : 1) / (MAX_ENTRIES + 1);
            }
            var sortKey = floor + state.tailStep;
            if (!Number.isFinite(sortKey) || sortKey <= floor
                    || (next && sortKey >= next.sortKey)) return;
            var accepted = window.appendReactChatBlocks(entry.payload, {
                id: 'reply-tail:' + key,
                sortKey: sortKey,
                createdAt: anchor.createdAt,
            });
            if (accepted) {
                state.lastTailSortKey = sortKey;
                pending.delete(key);
                seen.add(key);
                trim(seen);
                messages = host.getState().messages || [];
            }
        });
    }

    window.nekoReplyTail = {
        version: 1,
        changed: changed,
        enqueue: function (payload) {
            var tail = payload && payload.reply_tail;
            if (!tail || tail.version !== 1 || !tail.reply_id || !tail.request_id || !tail.registration_id) return false;
            var key = JSON.stringify([tail.reply_id, tail.call_id || '', tail.registration_id]);
            if (seen.has(key) || pending.has(key)) return true;
            if (pending.size >= MAX_ENTRIES) return false;
            pending.set(key, { payload: payload, created: Date.now() });
            changed();
            return true;
        },
        reset: function () {
            requests.clear();
            captions.clear();
            pending.clear();
            seen.clear();
            currentCaptionTurn = '';
        },
    };
    window.addEventListener('neko-assistant-turn-start', function (event) { remember(event.detail, false); });
    window.addEventListener('neko-assistant-turn-end', function (event) { remember(event.detail, true); });
    window.addEventListener('neko-reply-tail-presentation', function (event) {
        var detail = event.detail || {};
        compact = detail.compact === true;
        currentCaptionTurn = String(detail.turnId || '');
        if (currentCaptionTurn) {
            captions.set(currentCaptionTurn, detail.complete === true);
            trim(captions);
        }
        changed();
    });
})();
