/**
 * What a NekoPower commission asks the user, answered with buttons and a line for words.
 *
 * Two places of the main page use this: the task HUD draws the question of a task that
 * waits for the user inside its card (`render`), and the chat window shows each question
 * as a card among the messages (`receive`, a `nekopower_ask` block the React chat draws).
 * Every answer goes to `POST /api/agent/commissions/{id}/answer`, against the id of the
 * question that was shown: one put meanwhile does not get it (409).
 *
 * A question is `{id, kind, ...}` as the agent process hands it over:
 *   - `permission`: `options` (always / session / once / deny), `line` (what is to be
 *     allowed, in words), `line_cut` (the command or path in it is not whole), `detail`;
 *   - `confirmation`: `question`; answered yes or no, or in words;
 *   - `questions`: `questions` (each `{question, options?, multi_select?}`);
 *   - anything else (`report`): `question`, `details`; answered in words.
 */
(function () {
    'use strict';

    var SETTLE_MS = 900;
    // An answer whose fate is not known is looked into again this often; after this many times
    // less often, for as long as its card is there (the server may be away for a while, and the
    // card must not stay shut once it is back).
    var RECHECK_MS = 3000;
    var RECHECKS = 20;
    var RECHECK_SLOW_MS = 15000;
    var WIDE_CHOICES = ['always', 'session'];
    // A command or path longer than this is not read at a glance: it is shown whole before an
    // approval that holds for more than this once is offered.
    var GLANCE_CHARS = 80;

    function tr(key, fallback, vars) {
        var text = '';
        try {
            if (typeof window.safeT === 'function') text = window.safeT(key, fallback);
            else if (typeof window.t === 'function') text = window.t(key);
        } catch (_) { text = ''; }
        if (!text || text === key) text = fallback;
        Object.keys(vars || {}).forEach(function (name) {
            text = text.split('{{' + name + '}}').join(String(vars[name])).split('{' + name + '}').join(String(vars[name]));
        });
        return text;
    }

    var WORDS = {
        title: ['agent.ask.title', '喵能力 · 等你回话'],
        always: ['agent.ask.always', '始终允许'],
        session: ['agent.ask.session', '这件事里都允许'],
        once: ['agent.ask.once', '只这一次'],
        deny: ['agent.ask.deny', '拒绝'],
        yes: ['agent.ask.yes', '是，照做'],
        no: ['agent.ask.no', '不要'],
        send: ['agent.ask.send', '发送'],
        reply: ['agent.ask.replyPlaceholder', '打字回答…'],
        add: ['agent.ask.addPlaceholder', '打字回答，或补一句再点选项'],
        note: ['agent.ask.notePlaceholder', '补一句（可选），再点按钮'],
        showWhole: ['agent.ask.showWhole', '看全部'],
        hideWhole: ['agent.ask.hideWhole', '收起'],
        wideHint: ['agent.ask.wideHint', '看过全部内容，才会出现「始终允许」这类选项'],
        answered: ['agent.ask.answered', '已回答：{answer}'],
        answeredPlain: ['agent.ask.answeredPlain', '已回答'],
        closed: ['agent.ask.closed', '这个问题已经结束了'],
        stale: ['agent.ask.stale', '没送到：这个问题已经答过了，或者它换了个新问题。'],
        noteLost: ['agent.ask.noteLost', '回答送到了，补的那句没送到。'],
        failed: ['agent.ask.failed', '没送到，再试一次吧。'],
        unsure: ['agent.ask.unsure', '还不确定送到没有，过一会儿再看。'],
        goneUnsure: ['agent.ask.goneUnsure', '这个问题已经结束了；这次的回答有没有算上，不确定。'],
        wideBlocked: ['agent.ask.wideBlocked', '这里看不到完整内容，「始终允许」这类选项请到喵能力界面里选']
    };
    function word(name, vars) { return tr(WORDS[name][0], WORDS[name][1], vars); }

    function text(value) { return typeof value === 'string' ? value.trim() : ''; }

    /** The questions of an `ask_user_question` call, each named by where it stands among them. */
    function questionsOf(question) {
        var list = Array.isArray(question && question.questions) ? question.questions : [];
        var made = [];
        list.forEach(function (item, index) {
            if (!item || typeof item !== 'object') return;
            var options = (Array.isArray(item.options) ? item.options : []).map(function (option) {
                return option && typeof option === 'object'
                    ? { label: text(option.label), description: text(option.description) }
                    : { label: text(String(option == null ? '' : option)), description: '' };
            }).filter(function (option) { return option.label; });
            made.push({ id: String(index), text: text(item.question), options: options, multi: item.multi_select === true });
        });
        return made;
    }

    /** What a question is shown and answered by. */
    function view(question) {
        var asked = question && typeof question === 'object' ? question : {};
        var detail = asked.detail && typeof asked.detail === 'object' ? asked.detail : {};
        var options = Array.isArray(asked.options) ? asked.options.filter(function (option) { return WORDS[option]; }) : [];
        var questions = questionsOf(asked);
        var whole = text(detail.command) || text(detail.path);
        // The line that says it left part of something out: of the command or path, or (where it
        // names neither) of whatever else it is said by, and of whom something is delivered to.
        var cut = asked.line_cut === true;
        if (cut) {
            whole = [whole || text(asked.key) || text(detail.title) || text(detail.tool), text(detail.content_to)]
                .filter(Boolean).join('\n');
        }
        var shape = options.length ? 'permission'
            : asked.kind === 'confirmation' ? 'confirmation'
                : questions.some(function (item) { return item.options.length; }) ? 'questions' : 'words';
        var line = shape === 'permission' ? (text(asked.line) || whole || text(detail.title) || text(detail.tool) || text(asked.category))
            : [text(asked.question), text(asked.details)].filter(Boolean).join(' ');
        if (!line && questions.length) line = questions.map(function (item) { return item.text; }).filter(Boolean).join(' ');
        return {
            id: text(asked.id),
            shape: shape,
            line: line,
            whole: whole,
            // Whether all of what it would do has to be opened before a wider approval is offered.
            needsWhole: shape === 'permission'
                && (cut || (Boolean(whole) && (whole.length > GLANCE_CHARS || whole.indexOf('\n') !== -1))),
            options: options,
            questions: questions,
            quick: questions.length === 1 && !questions[0].multi
        };
    }

    /** What an answer reads as once it is given: the button that was pressed, the words. */
    function shownAnswer(given) {
        var parts = [];
        if (given.choice && WORDS[given.choice]) parts.push(word(given.choice));
        Object.keys(given.selected || {}).forEach(function (key) { parts = parts.concat(given.selected[key]); });
        if (text(given.text)) parts.push(text(given.text));
        if (text(given.note)) parts.push(text(given.note));
        return parts.join(' · ');
    }

    async function mutationHeaders() {
        var headers = { 'Content-Type': 'application/json' };
        try {
            var security = window.nekoLocalMutationSecurity;
            if (security && typeof security.getMutationHeaders === 'function') {
                Object.assign(headers, await security.getMutationHeaders());
            }
        } catch (_) { /* Sent without it: the server says so. */ }
        return headers;
    }

    /**
     * Send an answer. Resolves to `{ok, stale, noteLost}`: `stale` when the question was
     * answered, taken back or replaced meanwhile; `noteLost` when the answer is in and the
     * sentence added to it was not taken.
     */
    async function answer(taskId, questionId, given) {
        var body = { question_id: questionId || undefined };
        if (given.choice) body.choice = given.choice;
        if (text(given.text)) body.text = text(given.text);
        if (text(given.note)) body.note = text(given.note);
        var selected = {};
        Object.keys(given.selected || {}).forEach(function (key) {
            if (given.selected[key] && given.selected[key].length) selected[key] = given.selected[key];
        });
        if (Object.keys(selected).length) body.selected = selected;
        try {
            var response = await fetch('/api/agent/commissions/' + encodeURIComponent(taskId) + '/answer', {
                method: 'POST', headers: await mutationHeaders(), body: JSON.stringify(body)
            });
            var result = null;
            try { result = await response.json(); } catch (_) { result = null; }
            if (response.ok) return { ok: true, stale: false, noteLost: Boolean(result && result.note_taken === false) };
            if (response.status !== 504) return { ok: false, stale: response.status === 409, noteLost: false };
        } catch (_) {
            // The connection broke after the answer was sent: it may have been taken all the same.
            // Looked into as below, and not said to have failed.
        }
        // The server gave up waiting after it had passed the answer on: it may be in. Where the
        // same question still waits it was not, and can be sent again. Where it waits no more it is
        // over, by this answer or by something else (answered elsewhere first, stopped): which is
        // not known, and it is not said to be answered. Nor where what waits cannot be read.
        var waits = await whetherWaits(taskId, questionId);
        if (waits === null) return { ok: false, stale: false, noteLost: false, unsure: true };
        return waits ? { ok: false, stale: false, noteLost: false } : { ok: false, stale: false, noteLost: false, gone: true };
    }

    /** Whether a task still waits with this very question: true, false, or null where that cannot be read. */
    async function whetherWaits(taskId, questionId) {
        var rows = await waiting();
        if (!rows) return null;
        return rows.some(function (row) {
            return row && String(row.task_id) === String(taskId) && view(row.question).id === (questionId || '');
        });
    }

    /**
     * Look into an answer whose fate is not known until it is: `settled(true)` once the same
     * question is seen to wait still (the answer was not taken, and can be given again),
     * `settled(false)` once it waits no more. Not given up while `wanted` says somebody still
     * wants to know (the card is there), only looked into less often after a while.
     */
    function lookInto(taskId, questionId, wanted, settled, tried) {
        var looks = tried || 0;
        setTimeout(function () {
            if (!wanted()) return;
            whetherWaits(taskId, questionId).then(function (waits) {
                if (!wanted()) return;
                if (waits === null) lookInto(taskId, questionId, wanted, settled, looks + 1);
                else settled(waits);
            });
        }, looks < RECHECKS ? RECHECK_MS : RECHECK_SLOW_MS);
    }

    /** What commissions ask right now, read from the agent process: `[{task_id, title, question, route}]`. */
    async function waiting() {
        try {
            var response = await fetch('/api/agent/questions', { cache: 'no-store' });
            if (!response.ok) return null;
            var result = await response.json();
            return Array.isArray(result && result.questions) ? result.questions : [];
        } catch (_) {
            return null;
        }
    }

    // ------------------------------------------------------------------------------------
    // The question inside a card of the task HUD: plain DOM, styled as the HUD styles itself.
    // ------------------------------------------------------------------------------------

    function el(tag, style, content) {
        var node = document.createElement(tag);
        if (style) Object.assign(node.style, style);
        if (content != null) node.textContent = content;
        return node;
    }

    var BUTTON = {
        font: 'inherit', fontSize: '11px', lineHeight: '1.5', padding: '2px 8px', borderRadius: '6px', cursor: 'pointer',
        border: '1px solid var(--neko-popup-accent, #2a7bc4)', color: 'var(--neko-popup-accent, #2a7bc4)',
        background: 'var(--neko-popup-bg, rgba(255, 255, 255, 0.9))', maxWidth: '100%', overflow: 'hidden',
        textOverflow: 'ellipsis', whiteSpace: 'nowrap'
    };
    var DENY = { borderColor: 'var(--neko-popup-error, #dc2626)', color: 'var(--neko-popup-error, #dc2626)' };
    var PLAIN = { borderColor: 'var(--neko-popup-border-color, rgba(0, 0, 0, 0.18))', color: 'var(--neko-popup-text-sub, #666)' };
    var ROW = { display: 'flex', flexWrap: 'wrap', gap: '4px' };

    /**
     * Draw a question with the ways to answer it. `options`: `taskId`, `question`,
     * `noNote` (nothing can take a sentence added to a choice: a plugin does the work),
     * `settle` (it took the place of another question a moment ago: its buttons wait).
     */
    function render(options) {
        var shown = view(options.question);
        var box = el('div', { display: 'flex', flexDirection: 'column', gap: '5px', marginTop: '6px', fontSize: '12px', lineHeight: '1.45' });
        box.className = 'task-ask';
        box.dataset.questionId = shown.id;
        box.dataset.shape = shown.shape;
        // Pressing in it neither drags the HUD nor does what a press on the card does.
        ['mousedown', 'touchstart', 'pointerdown', 'click'].forEach(function (name) {
            box.addEventListener(name, function (event) { event.stopPropagation(); });
        });

        var busy = false;
        var done = false;
        // Whether an answer was taken is not known yet: nothing more is sent until it is.
        var unknown = false;
        var settling = Boolean(options.settle);
        var wholeOpen = false;
        var picked = {};
        var buttons = [];
        var wide = [];

        function refresh() {
            buttons.forEach(function (button) {
                button.disabled = busy || done || settling || unknown;
                button.style.opacity = button.disabled ? '0.55' : '1';
                button.style.cursor = button.disabled ? 'default' : 'pointer';
            });
            wide.forEach(function (button) { button.style.display = shown.needsWhole && !wholeOpen ? 'none' : ''; });
            if (hint) {
                hint.style.display = shown.needsWhole && !wholeOpen && wide.length ? '' : 'none';
                // Cut, and nothing here to show it whole by: the wider approvals are given elsewhere.
                hint.textContent = word(shown.whole ? 'wideHint' : 'wideBlocked');
            }
            if (input) input.disabled = busy || done || unknown;
        }
        function button(label, style, press) {
            var node = el('button', Object.assign({}, BUTTON, style || {}), label);
            node.type = 'button';
            node.title = label;
            node.addEventListener('click', function () { if (!node.disabled) press(); });
            buttons.push(node);
            return node;
        }
        async function give(given) {
            if (busy || done || settling || unknown) return;
            busy = true;
            status.textContent = '';
            refresh();
            var result = await answer(options.taskId, shown.id, given);
            busy = false;
            if (result.ok) {
                done = true;
                var said = shownAnswer(given);
                status.textContent = said ? word('answered', { answer: said }) : word('answeredPlain');
                // Said by whoever shows this, outside it: the question is taken away the moment the
                // task goes on, which may be before this is known.
                if (result.noteLost) {
                    if (typeof options.onNoteLost === 'function') options.onNoteLost(word('noteLost'));
                    else status.textContent += ' ' + word('noteLost');
                }
                status.style.color = 'var(--neko-popup-success, #16a34a)';
            } else {
                status.textContent = word(result.stale ? 'stale' : result.gone ? 'goneUnsure' : result.unsure ? 'unsure' : 'failed');
                status.style.color = 'var(--neko-popup-error, #dc2626)';
                // Too late, or over: the question is closed, and what waits now is read anew.
                if (result.stale || result.gone) { done = true; sync(); }
                if (result.unsure) {
                    // Sent again now it could be taken twice over; looked into until it is known.
                    unknown = true;
                    lookInto(options.taskId, shown.id, function () { return unknown && box.isConnected; }, function (waits) {
                        unknown = false;
                        done = !waits;
                        status.textContent = word(waits ? 'failed' : 'goneUnsure');
                        if (!waits) sync();
                        refresh();
                    });
                }
            }
            refresh();
        }

        // What it asks: its questions each with what they offer, or one line.
        var input = null;
        var hint = null;
        if (shown.shape === 'questions') {
            shown.questions.forEach(function (item, index) {
                box.appendChild(el('div', { overflowWrap: 'anywhere' }, shown.questions.length > 1 ? (index + 1) + '. ' + item.text : item.text));
                if (!item.options.length) return;
                var row = el('div', ROW);
                item.options.forEach(function (option) {
                    var node = button(option.label, null, function () {
                        if (shown.quick) {
                            var selected = {};
                            selected[item.id] = [option.label];
                            give({ selected: selected, text: input ? input.value : '' });
                            return;
                        }
                        var had = picked[item.id] || [];
                        picked[item.id] = had.indexOf(option.label) !== -1
                            ? had.filter(function (each) { return each !== option.label; })
                            : item.multi ? had.concat([option.label]) : [option.label];
                        Array.prototype.forEach.call(row.children, function (each) {
                            var on = (picked[item.id] || []).indexOf(each.dataset.option) !== -1;
                            each.setAttribute('aria-pressed', on ? 'true' : 'false');
                            each.style.background = on ? 'var(--neko-popup-accent-bg, rgba(42, 123, 196, 0.16))' : BUTTON.background;
                            each.style.fontWeight = on ? '600' : '';
                        });
                    });
                    node.dataset.option = option.label;
                    if (option.description) node.title = option.label + ' — ' + option.description;
                    row.appendChild(node);
                });
                box.appendChild(row);
            });
        } else {
            var line = el('div', {
                overflow: 'hidden', display: '-webkit-box', webkitBoxOrient: 'vertical', webkitLineClamp: '3', overflowWrap: 'anywhere'
            }, shown.line);
            line.className = 'task-ask-line';
            box.appendChild(line);
        }

        if (shown.shape === 'permission') {
            if (shown.needsWhole && shown.whole) {
                var whole = el('pre', {
                    display: 'none', margin: '0', padding: '6px 8px', maxHeight: '140px', overflow: 'auto', whiteSpace: 'pre-wrap',
                    wordBreak: 'break-all', font: '11px/1.5 Consolas, Menlo, monospace', borderRadius: '6px',
                    background: 'var(--neko-hud-subtle-bg, rgba(0, 0, 0, 0.06))', userSelect: 'text'
                }, shown.whole);
                whole.className = 'task-ask-whole';
                var toggle = button(word('showWhole'), PLAIN, function () {
                    wholeOpen = !wholeOpen;
                    whole.style.display = wholeOpen ? 'block' : 'none';
                    toggle.textContent = word(wholeOpen ? 'hideWhole' : 'showWhole');
                    refresh();
                });
                toggle.dataset.askWhole = 'toggle';
                box.appendChild(whole);
                box.appendChild(el('div', ROW)).appendChild(toggle);
            }
            var choices = el('div', ROW);
            shown.options.forEach(function (option) {
                var node = button(word(option), option === 'deny' ? DENY : null, function () {
                    give({ choice: option, note: input ? input.value : '' });
                });
                node.dataset.choice = option;
                if (WIDE_CHOICES.indexOf(option) !== -1) wide.push(node);
                choices.appendChild(node);
            });
            box.appendChild(choices);
            hint = el('div', { fontSize: '10px', color: 'var(--neko-popup-text-sub, #888)' }, word('wideHint'));
            box.appendChild(hint);
        } else if (shown.shape === 'confirmation') {
            var yesNo = el('div', ROW);
            [['yes', null], ['no', DENY]].forEach(function (pair) {
                var node = button(word(pair[0]), pair[1], function () { give({ choice: pair[0], text: input ? input.value : '' }); });
                node.dataset.confirm = pair[0];
                yesNo.appendChild(node);
            });
            box.appendChild(yesNo);
        }

        // A line for words: the answer itself, or a sentence that goes with the button.
        if (!(shown.shape === 'permission' && options.noNote)) {
            var say = el('div', { display: 'flex', gap: '4px' });
            input = el('input', {
                flex: '1', minWidth: '0', font: 'inherit', fontSize: '11px', padding: '2px 6px', borderRadius: '6px',
                border: '1px solid var(--neko-popup-border-color, rgba(0, 0, 0, 0.18))', color: 'inherit',
                background: 'var(--neko-popup-bg, rgba(255, 255, 255, 0.9))'
            });
            input.type = 'text';
            input.className = 'task-ask-input';
            input.placeholder = word(shown.shape === 'permission' ? 'note' : shown.shape === 'words' ? 'reply' : 'add');
            input.setAttribute('aria-label', input.placeholder);
            say.appendChild(input);
            if (shown.shape !== 'permission') {
                // Words alone answer anything but a request for approval, which reads them as a refusal.
                var sendWords = function () {
                    var somePicked = Object.keys(picked).some(function (key) { return picked[key].length; });
                    if (!text(input.value) && !somePicked) return;
                    give(somePicked ? { selected: picked, text: input.value } : { text: input.value });
                };
                var send = button(word('send'), null, sendWords);
                send.dataset.askSend = 'true';
                say.appendChild(send);
                input.addEventListener('keydown', function (event) {
                    // Not while a word is being composed: that Enter picks the word.
                    if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); sendWords(); }
                    event.stopPropagation();
                });
            } else {
                input.addEventListener('keydown', function (event) { event.stopPropagation(); });
            }
            box.appendChild(say);
        }

        var status = el('div', { fontSize: '11px' });
        status.className = 'task-ask-status';
        status.setAttribute('role', 'status');
        box.appendChild(status);

        if (settling) setTimeout(function () { settling = false; refresh(); }, SETTLE_MS);
        refresh();
        return box;
    }

    // ------------------------------------------------------------------------------------
    // The chat window: each question a card among the messages (`nekopower_ask` block).
    // ------------------------------------------------------------------------------------

    var PREFIX = 'nekopower-ask-';
    // By task: the card of what it asks now (`{message, questionId}`); the cards are the chat's to keep.
    var open = new Map();
    var queued = [];
    // The ids of questions whose cards were closed or answered here (the latest few hundred): a
    // report of one of them that arrives late (it was on its way as the answer came) shows no card.
    var over = [];
    function isOver(questionId) { return Boolean(questionId) && over.indexOf(questionId) !== -1; }
    function setOver(questionId) {
        if (!questionId || isOver(questionId)) return;
        over.push(questionId);
        if (over.length > 300) over.shift();
    }
    // When each task was last told of by the page's connection (a count, not a time): what a read
    // that began before that says of the task is older, and is not gone by.
    var told = new Map();
    var tellings = 0;

    function character() {
        return (window.lanlan_config && window.lanlan_config.lanlan_name) || '';
    }

    function host() {
        var api = window.reactChatWindowHost;
        return api && typeof api.appendMessage === 'function' && typeof api.updateMessage === 'function' ? api : null;
    }
    /** Whether the chat still has a message; true where that cannot be told. */
    function inChat(api, messageId) {
        try {
            var state = typeof api.getState === 'function' ? api.getState() : null;
            if (!state || !Array.isArray(state.messages)) return true;
            return state.messages.some(function (message) { return message && message.id === messageId; });
        } catch (_) {
            return true;
        }
    }
    function patch(card, changes) {
        var api = host();
        card.message = Object.assign({}, card.message, {
            blocks: [Object.assign({}, card.message.blocks[0], changes)]
        });
        if (api) api.updateMessage(card.message.id, { blocks: card.message.blocks });
    }
    /** Close the card of a task's question: all of that task's, or the one that asked `questionId`. */
    function close(taskId, questionId) {
        queued = queued.filter(function (args) { return args[0] !== taskId || (questionId && view(args[2]).id !== questionId); });
        if (questionId) setOver(questionId);
        var card = open.get(taskId);
        if (!card || (questionId && card.questionId !== questionId)) return;
        open.delete(taskId);
        setOver(card.questionId);
        var block = card.message.blocks[0];
        if (block.state === 'pending' || block.state === 'sending') patch(card, { state: 'closed' });
    }
    function show(taskId, title, question, route) {
        var shown = view(question);
        // Before anything is let go of for it: a late report of a question that is over must not
        // take the place of the next one, which waits for the window.
        if (isOver(shown.id)) return;
        var api = host();
        if (!api) {
            // Only the latest of a task: an earlier one is over by the time the window is there.
            queued = queued.filter(function (args) { return args[0] !== taskId; });
            queued.push([taskId, title, question, route]);
            tries = 0;
            // One look at a time, for all that waits: each question starting its own would use up
            // the looks between them before the window is there.
            if (!draining) draining = setTimeout(drain, 500);
            return;
        }
        // Shown from here on: what waited for the window of this task is older, and is let go of.
        queued = queued.filter(function (args) { return args[0] !== taskId; });
        var card = open.get(taskId);
        if (card && card.questionId === shown.id) {
            // The chat keeps only its latest messages: a card that was pushed out while its
            // question still waits is put back, as it stands.
            if (!inChat(api, card.message.id)) api.appendMessage(card.message);
            return;
        }
        // Another question of the same task: the last one is over.
        if (card) close(taskId);
        var now = new Date();
        var message = {
            id: PREFIX + taskId + '-' + (shown.id || now.getTime()),
            role: 'system',
            author: word('title'),
            time: now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
            createdAt: now.getTime(),
            blocks: [{
                type: 'nekopower_ask', taskId: taskId, questionId: shown.id, title: text(title), question: question,
                state: 'pending', noNote: route === 'plugin'
            }]
        };
        open.set(taskId, { message: message, questionId: shown.id });
        api.appendMessage(message);
    }

    /** A `nekopower_question` frame of the main server: a question to show, or (none) one that is over. */
    function receive(frame) {
        if (!frame || !frame.task_id) return;
        told.set(String(frame.task_id), ++tellings);
        if (!frame.question || typeof frame.question !== 'object') {
            close(String(frame.task_id), text(frame.question_id));
            return;
        }
        // Of another character than the one this page is with (sent to every page, its own not
        // being open): not shown here, as what is read on connecting is not (`sync`).
        var mine = character();
        if (mine && frame.lanlan_name && frame.lanlan_name !== mine) return;
        show(String(frame.task_id), frame.title, frame.question, frame.route);
    }

    var syncing = null;
    var again = false;
    /**
     * Read what waits now: questions put while the page was away are shown, cards of ones that are
     * over closed. What the page's connection said of a task while the read was on its way is
     * newer than the read, and stands; so does the character the page is with by then.
     */
    function sync() {
        if (syncing) {
            // Asked for while one is on its way: that one may have been answered before what
            // prompted this, so another follows it.
            again = true;
            return syncing;
        }
        var since = tellings;
        var mine = character();
        syncing = waiting().then(function (rows) {
            syncing = null;
            var rerun = again || mine !== character();
            again = false;
            if (rows && mine === character()) {
                var asked = new Map();
                rows.forEach(function (row) {
                    if (row && row.task_id && row.question) asked.set(String(row.task_id), row);
                });
                var fresh = function (taskId) { return (told.get(taskId) || 0) > since; };
                Array.from(open.keys()).forEach(function (taskId) {
                    var row = asked.get(taskId);
                    if (fresh(taskId)) return;
                    if (!row || view(row.question).id !== open.get(taskId).questionId) close(taskId);
                });
                // What waits for the chat window to be there, likewise: one that is over is not shown then.
                queued = queued.filter(function (args) {
                    var row = asked.get(args[0]);
                    return fresh(args[0]) || Boolean(row && view(row.question).id === view(args[2]).id);
                });
                asked.forEach(function (row, taskId) {
                    // Of another character than the one this page is with: its page shows it, as
                    // the connection would have sent it there.
                    if (fresh(taskId) || (mine && row.lanlan_name && row.lanlan_name !== mine)) return;
                    show(taskId, row.title, row.question, row.route);
                });
            }
            if (rerun) return sync();
        });
        return syncing;
    }

    // The chat was emptied (the character was switched): its cards are gone with it. What the
    // character the page is with now is asked is read anew.
    window.addEventListener('react-chat-window:messages-cleared', function () {
        open.clear();
        queued = [];
        sync();
    });

    window.addEventListener('react-chat-window:action', function (event) {
        var detail = event.detail || {};
        var action = detail.action || {};
        if (action.action !== 'nekopower_ask_answer') return;
        var payload = action.payload || {};
        var card = open.get(String(payload.taskId || ''));
        if (!card || card.questionId !== text(payload.questionId)) return;
        var block = card.message.blocks[0];
        if (block.state !== 'pending') return;
        var given = { choice: payload.choice, text: payload.text, note: payload.note, selected: payload.selected };
        patch(card, { state: 'sending', notice: '' });
        answer(block.taskId, block.questionId, given).then(function (result) {
            // The task may ask its next question before this is back: that card is the one kept.
            var mine = open.get(block.taskId) === card;
            var forget = function () { if (mine) open.delete(block.taskId); };
            if (!mine) {
                // Closed while the answer was on its way (it was answered elsewhere, or is over):
                // the card says so already, and is not opened again whatever came of this.
                var api = host();
                // (Not of a chat that was emptied meanwhile: its cards are gone.)
                if (!api || !inChat(api, card.message.id)) return;
                if (result.ok) patch(card, { state: 'answered', answer: shownAnswer(given), notice: result.noteLost ? word('noteLost') : '' });
                // It stays closed; what is not known of this answer is said on it. (Of one that
                // was plainly refused nothing is: it did not count, and the card says it is over.)
                else if (result.stale) patch(card, { notice: word('stale') });
                else if (result.gone || result.unsure) patch(card, { notice: word('goneUnsure') });
                return;
            }
            if (result.ok) {
                forget();
                setOver(card.questionId);
                patch(card, { state: 'answered', answer: shownAnswer(given), notice: result.noteLost ? word('noteLost') : '' });
            } else if (result.stale || result.gone) {
                forget();
                setOver(card.questionId);
                patch(card, { state: 'closed', notice: word(result.stale ? 'stale' : 'goneUnsure') });
                sync();
            } else if (result.unsure) {
                // Not to be sent again until it is known whether this was taken: the card stays as
                // one whose answer is on its way, and is looked into until it is known.
                patch(card, { state: 'sending', notice: word('unsure') });
                lookInto(block.taskId, block.questionId, function () { return open.get(block.taskId) === card; }, function (waits) {
                    if (waits) {
                        patch(card, { state: 'pending', notice: word('failed') });
                        return;
                    }
                    open.delete(block.taskId);
                    setOver(card.questionId);
                    patch(card, { state: 'closed', notice: word('goneUnsure') });
                    sync();
                });
            } else {
                patch(card, { state: 'pending', notice: word('failed') });
            }
        });
    });

    // Once the chat window is there: what waited for it is shown. The page's connection reads
    // what was asked before it opened (`sync`, from `app-websocket.js` as it connects).
    var tries = 0;
    var draining = null;
    function drain() {
        draining = null;
        if (!queued.length) return;
        if (!host()) {
            if (tries++ < 120) draining = setTimeout(drain, 500);
            return;
        }
        var waitingToShow = queued.splice(0, queued.length);
        waitingToShow.forEach(function (args) { show.apply(null, args); });
    }

    window.NekoPowerAsk = {
        view: view, answer: answer, waiting: waiting, render: render, receive: receive, sync: sync, shownAnswer: shownAnswer,
        word: word, SETTLE_MS: SETTLE_MS, RECHECK_MS: RECHECK_MS, RECHECKS: RECHECKS, RECHECK_SLOW_MS: RECHECK_SLOW_MS
    };
})();
