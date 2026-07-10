(function () {
    'use strict';

    var FREE_TEXT_HISTORY_LIMIT = 4;
    var FREE_TEXT_HISTORY_TEXT_LENGTH = 240;
    var TOPIC_ON_TOPIC = 'on_topic';
    var TOPIC_SOFT_DERAIL = 'soft_derail';
    var TOPIC_HARD_EXIT = 'hard_exit';

    function findOptionByChoice(node, choice) {
        var target = String(choice || '').trim().toUpperCase();
        if (!target || !node || !Array.isArray(node.options)) return null;
        return node.options.find(function (candidate) {
            return String(candidate.id || '').trim().toUpperCase() === target;
        }) || null;
    }

    function normalizeInterpretation(data) {
        var action = String(data && data.action || '').trim();
        var choice = String(data && data.choice || '').trim().toUpperCase();
        var reply = String(data && data.reply || '').trim();
        var topicState = String(data && (data.topic_state || data.topicState) || '').trim();
        if (action !== 'choose' && action !== 'respond_and_keep_options' && action !== 'release') {
            action = 'respond_and_keep_options';
        }
        if (action === 'choose' && choice !== 'A' && choice !== 'B') {
            action = 'respond_and_keep_options';
            choice = '';
        }
        if (action !== 'choose') {
            choice = '';
        }
        if (topicState !== TOPIC_ON_TOPIC
            && topicState !== TOPIC_SOFT_DERAIL
            && topicState !== TOPIC_HARD_EXIT) {
            topicState = action === 'release'
                ? TOPIC_HARD_EXIT
                : TOPIC_ON_TOPIC;
        }
        return { action: action, choice: choice, reply: reply, topicState: topicState };
    }

    function trimHistoryText(value) {
        return String(value || '').replace(/\s+/g, ' ').trim().slice(0, FREE_TEXT_HISTORY_TEXT_LENGTH);
    }

    function createRuntimeStateStore() {
        var freeTextRuntimeStateByKey = Object.create(null);

        function getStateKey(session, nodeId) {
            var sessionId = String(session && session.sessionId || '').trim();
            var normalizedNodeId = String(nodeId || (session && session.nodeId) || '').trim();
            if (!sessionId || !normalizedNodeId) return '';
            return sessionId + '::' + normalizedNodeId;
        }

        function getState(session, nodeId) {
            var key = getStateKey(session, nodeId);
            if (!key) return { freeTextTurns: [], derailStreak: 0 };
            var state = freeTextRuntimeStateByKey[key];
            if (!state || typeof state !== 'object') {
                state = { freeTextTurns: [], derailStreak: 0 };
                freeTextRuntimeStateByKey[key] = state;
            }
            if (!Array.isArray(state.freeTextTurns)) state.freeTextTurns = [];
            state.derailStreak = Number(state.derailStreak || 0) > 0 ? 1 : 0;
            return state;
        }

        function clearForSession(session) {
            var sessionId = String(session && session.sessionId || '').trim();
            if (!sessionId) return;
            var prefix = sessionId + '::';
            Object.keys(freeTextRuntimeStateByKey).forEach(function (key) {
                if (key.indexOf(prefix) === 0) delete freeTextRuntimeStateByKey[key];
            });
        }

        function getRecentTurns(session, nodeId) {
            var state = getState(session, nodeId);
            return state.freeTextTurns.slice(-FREE_TEXT_HISTORY_LIMIT).map(function (turn) {
                return Object.assign({}, turn);
            });
        }

        function recordTurn(session, turn, nodeId) {
            if (!session || !turn || typeof turn !== 'object') return;
            var state = getState(session, nodeId);
            var userText = trimHistoryText(turn.userText || turn.user_text);
            if (!userText) return;
            var action = String(turn.action || '').trim();
            if (action !== 'choose' && action !== 'respond_and_keep_options' && action !== 'release') {
                action = 'respond_and_keep_options';
            }
            var entry = {
                user_text: userText,
                action: action
            };
            var choice = String(turn.choice || '').trim().toUpperCase();
            if (action === 'choose' && (choice === 'A' || choice === 'B')) {
                entry.choice = choice;
            }
            var topicState = String(turn.topicState || turn.topic_state || '').trim();
            if (topicState === TOPIC_ON_TOPIC
                || topicState === TOPIC_SOFT_DERAIL
                || topicState === TOPIC_HARD_EXIT) {
                entry.topic_state = topicState;
            }
            var reply = trimHistoryText(turn.reply);
            if (reply) entry.reply = reply;
            state.freeTextTurns.push(entry);
            if (state.freeTextTurns.length > FREE_TEXT_HISTORY_LIMIT) {
                state.freeTextTurns = state.freeTextTurns.slice(-FREE_TEXT_HISTORY_LIMIT);
            }
        }

        function getDerailStreak(session, nodeId) {
            var value = Number(getState(session, nodeId).derailStreak || 0);
            return Number.isFinite(value) && value > 0 ? 1 : 0;
        }

        function setDerailStreak(session, nodeId, value) {
            getState(session, nodeId).derailStreak = value ? 1 : 0;
        }

        return {
            clearForSession: clearForSession,
            getRecentTurns: getRecentTurns,
            recordTurn: recordTurn,
            getDerailStreak: getDerailStreak,
            setDerailStreak: setDerailStreak
        };
    }

    window.NekoIcebreakerFreeTextRuntime = {
        FREE_TEXT_HISTORY_LIMIT: FREE_TEXT_HISTORY_LIMIT,
        TOPIC_ON_TOPIC: TOPIC_ON_TOPIC,
        TOPIC_SOFT_DERAIL: TOPIC_SOFT_DERAIL,
        TOPIC_HARD_EXIT: TOPIC_HARD_EXIT,
        createRuntimeStateStore: createRuntimeStateStore,
        findOptionByChoice: findOptionByChoice,
        normalizeInterpretation: normalizeInterpretation
    };
})();
