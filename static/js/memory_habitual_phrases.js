/*
 * Habitual-phrase section of the memory browser's insights panel.
 *
 * Shows, for the character the insights panel targets, which phrases the next
 * session start will ask her to say another way, why each one is (or is not)
 * suppressed, the rules behind the list, and lets the user allow a phrase,
 * force one the review kept, undo either, or recompute the list now.
 *
 * memory_browser.js owns the target and the storage / import locks; it
 * announces them through `neko:habitual-phrases-target`. Everything rendered
 * here that comes from her replies is set as text, never parsed as markup.
 */
(function () {
    'use strict';

    const VERDICT_KEYS = {
        normal: ['memory.habitualVerdictNormal', 'an ordinary expression'],
        topic: ['memory.habitualVerdictTopic', 'a topic'],
        name: ['memory.habitualVerdictName', 'a name or title'],
        card: ['memory.habitualVerdictCard', 'part of her character card']
    };

    let target = '';
    let ready = false;
    let busy = false;
    let panel = null;
    let requestId = 0;
    let feedback = null;

    function interpolate(text, options) {
        const values = options && typeof options === 'object' ? options : {};
        return String(text || '').replace(/\{\{\s*([\w.-]+)\s*\}\}/g, function (match, name) {
            if (!Object.prototype.hasOwnProperty.call(values, name)) return match;
            const value = values[name];
            return value === undefined || value === null ? '' : String(value);
        });
    }

    function translate(key, fallback, options) {
        let text = fallback;
        if (window.t) {
            const translated = window.t(key, options || {});
            if (typeof translated === 'string' && translated && translated !== key) {
                text = translated;
            }
        }
        return interpolate(text, options);
    }

    function locale() {
        return (window.i18n && window.i18n.language) || undefined;
    }

    // What the chat client falls back to for a character with no stored
    // conversation language; the server uses it to predict the next session.
    function uiLanguage() {
        try {
            if (window.i18next && window.i18next.language) return window.i18next.language;
            if (window.i18n && window.i18n.language) return window.i18n.language;
        } catch (_) { /* fall through */ }
        return '';
    }

    function formatTime(seconds) {
        if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return '—';
        const date = new Date(seconds * 1000);
        if (Number.isNaN(date.getTime())) return '—';
        try {
            return date.toLocaleString(locale(), {
                year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit'
            });
        } catch (_) {
            return date.toISOString().slice(0, 16).replace('T', ' ');
        }
    }

    function formatDate(seconds) {
        if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds <= 0) return '—';
        const date = new Date(seconds * 1000);
        if (Number.isNaN(date.getTime())) return '—';
        try {
            return date.toLocaleDateString(locale(), { year: 'numeric', month: 'numeric', day: 'numeric' });
        } catch (_) {
            return date.toISOString().slice(0, 10);
        }
    }

    function element(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function setStatusLines(lines) {
        const status = byId('memory-habitual-status');
        if (!status) return;
        status.textContent = '';
        lines.forEach(function (line) {
            const row = element('p', line.tone ? 'is-' + line.tone : '', line.text);
            status.appendChild(row);
        });
    }

    function clearContent() {
        const list = byId('memory-habitual-list');
        const allowed = byId('memory-habitual-allowed');
        const allowedList = byId('memory-habitual-allowed-list');
        const logic = byId('memory-habitual-logic-list');
        if (list) list.textContent = '';
        if (allowedList) allowedList.textContent = '';
        if (allowed) allowed.hidden = true;
        if (logic) logic.textContent = '';
    }

    function syncButtons() {
        const recompute = byId('memory-habitual-recompute');
        const locked = busy || !ready || !target;
        if (recompute) recompute.disabled = locked;
        document.querySelectorAll('#memory-habitual [data-habitual-action]').forEach(function (button) {
            button.disabled = locked;
        });
    }

    function renderLogic(rules) {
        const logic = byId('memory-habitual-logic-list');
        if (!logic) return;
        logic.textContent = '';
        const values = {
            sessions: rules.min_sessions,
            gap: rules.session_gap_minutes,
            max: rules.max_items,
            tokens: rules.prompt_max_tokens,
            hours: rules.refresh_interval_hours,
            days: rules.max_age_days
        };
        [
            ['memory.habitualLogicCandidates', 'A phrase becomes a candidate only after it appears in at least {{sessions}} separate conversations; replies more than {{gap}} minutes apart count as a new conversation.'],
            ['memory.habitualLogicExclusions', 'Never candidates: wording from her character card, either side\'s names and nicknames, anything she puts in quotes herself, and phrases made only of everyday function words.'],
            ['memory.habitualLogicReview', 'The model judges whether each candidate really is a habit, and only those it calls habits are suppressed; candidates it has not judged yet are suppressed by the rules.'],
            ['memory.habitualLogicLimits', 'At most {{max}} phrases are suppressed at once, and the whole reminder stays within {{tokens}} tokens. The list is recomputed about every {{hours}} hours while idle, phrases she stops using are released automatically, and a list older than {{days}} days is not injected.'],
            ['memory.habitualLogicOverrides', 'Your allows and suppressions take priority over the model and apply from the next chat. Allowing a phrase also allows any phrase that contains it.']
        ].forEach(function (pair) {
            logic.appendChild(element('li', '', translate(pair[0], pair[1], values)));
        });
    }

    function statusLines(data) {
        const rules = data.rules || {};
        const switches = data.switches || {};
        const review = data.review || {};
        const source = data.source || {};
        const lines = [];
        if (typeof data.computed_at !== 'number') {
            lines.push({ text: translate('memory.habitualNotComputed', 'Not computed yet. It runs on its own while memory is idle, or use "Recompute now".') });
        } else {
            lines.push({
                text: translate('memory.habitualComputedAt', 'Last computed: {{time}} · Next expected: {{next}}', {
                    time: formatTime(data.computed_at),
                    next: formatTime(data.next_refresh_at)
                })
            });
            lines.push({
                text: translate('memory.habitualWindow', 'Based on the latest {{replies}} replies over {{sessions}} conversations ({{from}} to {{to}})', {
                    replies: source.analyzed_reply_count || 0,
                    sessions: source.session_count || 0,
                    from: formatDate(source.window_start_at),
                    to: formatDate(source.window_end_at)
                })
            });
        }
        if (!switches.natural_expression) {
            lines.push({ tone: 'warning', text: translate('memory.habitualStatusSwitchOff', '"Natural Expression" is off, so nothing is injected.') });
        } else if (data.stale) {
            lines.push({ tone: 'warning', text: translate('memory.habitualStatusStale', 'The list has not been updated in over {{days}} days, so it is not injected.', { days: rules.max_age_days }) });
        } else if (typeof data.computed_at === 'number' && !data.language_matches) {
            lines.push({ tone: 'warning', text: translate('memory.habitualStatusLanguage', 'The list was mined in another language than the current one, so it is not injected.') });
        } else if (Array.isArray(data.injected) && data.injected.length) {
            lines.push({ tone: 'active', text: translate('memory.habitualStatusActive', 'The next chat will open by asking her to rephrase {{count}} phrase(s).', { count: data.injected.length }) });
        } else if (typeof data.computed_at === 'number') {
            lines.push({ text: translate('memory.habitualStatusNone', 'The next chat will open without a habitual-phrase reminder.') });
        }
        if (!switches.review) {
            lines.push({ text: translate('memory.habitualStatusReviewOff', '"Powerful Memory Mode" is off: new candidates are not reviewed by the model and are suppressed by the rules alone.') });
        } else if (review.status === 'failed') {
            lines.push({ tone: 'warning', text: translate('memory.habitualStatusReviewFailed', 'The last model review did not succeed; unjudged candidates are suppressed by the rules for now.') });
        }
        return lines;
    }

    function stateLabel(entry, injected, usable, rules) {
        const state = entry.state;
        if (state === 'allowed') return translate('memory.habitualStateAllowed', 'Allowed');
        if (state === 'kept') {
            const verdict = VERDICT_KEYS[(entry.review && entry.review.verdict) || ''] || VERDICT_KEYS.normal;
            return translate('memory.habitualStateKept', 'Not suppressed · judged {{verdict}}', {
                verdict: translate(verdict[0], verdict[1])
            });
        }
        if (!entry.suppressed) {
            return translate('memory.habitualStateOverCap', 'Not suppressed · over the {{max}}-phrase cap', { max: rules.max_items });
        }
        if (usable && !injected.has(entry.phrase)) {
            return translate('memory.habitualStateOverBudget', 'Suppressed · past the length budget, left out of the next chat');
        }
        if (state === 'forced') return translate('memory.habitualStateForced', 'Suppressed · by you');
        if (state === 'habit') return translate('memory.habitualStateHabit', 'Suppressed · judged a habit');
        return translate('memory.habitualStateUnreviewed', 'Suppressed · not reviewed yet');
    }

    function actionButton(action, phrase, key, fallback) {
        const button = element('button', 'memory-habitual-action', translate(key, fallback));
        button.type = 'button';
        button.dataset.habitualAction = action;
        button.dataset.habitualPhrase = phrase;
        return button;
    }

    function entryActions(entry, allowedKeys) {
        const actions = [];
        if (entry.state === 'allowed') {
            if (allowedKeys.has(entry.normalized_phrase)) {
                actions.push(actionButton('clear', entry.phrase, 'memory.habitualRestore', 'Stop allowing'));
            }
        } else if (entry.state === 'forced') {
            actions.push(actionButton('clear', entry.phrase, 'memory.habitualClear', 'Undo my choice'));
            actions.push(actionButton('allow', entry.phrase, 'memory.habitualAllow', 'Allow'));
        } else if (entry.state === 'kept') {
            actions.push(actionButton('suppress', entry.phrase, 'memory.habitualSuppress', 'Suppress'));
        } else {
            actions.push(actionButton('allow', entry.phrase, 'memory.habitualAllow', 'Allow'));
        }
        return actions;
    }

    function renderEntries(data) {
        const list = byId('memory-habitual-list');
        if (!list) return;
        list.textContent = '';
        const rules = data.rules || {};
        const entries = Array.isArray(data.entries) ? data.entries : [];
        const injected = new Set(Array.isArray(data.injected) ? data.injected : []);
        const usable = typeof data.computed_at === 'number' && !data.stale && !!data.language_matches;
        const overrides = data.overrides || {};
        const allowedKeys = new Set((overrides.allowed || []).map(function (item) {
            return item.normalized_phrase;
        }));
        if (typeof data.computed_at === 'number' && !entries.length) {
            list.appendChild(element('li', 'memory-habitual-empty', translate('memory.habitualEmpty', 'No habitual phrases found for now.')));
            return;
        }
        entries.forEach(function (entry) {
            const item = element('li', 'memory-habitual-entry');
            const active = !!entry.suppressed;
            item.classList.add(active ? 'is-suppressed' : 'is-released');
            item.dataset.state = String(entry.state || '');

            const header = element('div', 'memory-habitual-entry-header');
            header.appendChild(element('strong', 'memory-habitual-phrase', entry.phrase));
            header.appendChild(element('span', 'memory-habitual-badge', stateLabel(entry, injected, usable, rules)));
            item.appendChild(header);

            item.appendChild(element('p', 'memory-habitual-meta', translate(
                'memory.habitualStats',
                '{{sessions}} conversations · {{days}} days · {{count}} times · last {{last}}',
                {
                    sessions: entry.session_count || 0,
                    days: entry.day_count || 0,
                    count: entry.occurrence_count || 0,
                    last: formatDate(entry.last_seen_at)
                }
            )));

            const reason = entry.review && typeof entry.review.reason === 'string' ? entry.review.reason.trim() : '';
            if (reason) {
                item.appendChild(element('p', 'memory-habitual-reason', translate(
                    'memory.habitualReason', "Model's reason: {{reason}}", { reason: reason }
                )));
            }

            const excerpts = Array.isArray(entry.excerpts) ? entry.excerpts : [];
            if (excerpts.length) {
                const quotes = element('div', 'memory-habitual-excerpts');
                excerpts.forEach(function (excerpt) {
                    quotes.appendChild(element('q', 'memory-habitual-excerpt', String(excerpt)));
                });
                item.appendChild(quotes);
            }

            const actions = entryActions(entry, allowedKeys);
            if (actions.length) {
                const row = element('div', 'memory-habitual-actions');
                actions.forEach(function (button) { row.appendChild(button); });
                item.appendChild(row);
            }
            list.appendChild(item);
        });
    }

    function renderAllowed(data) {
        const wrapper = byId('memory-habitual-allowed');
        const list = byId('memory-habitual-allowed-list');
        if (!wrapper || !list) return;
        list.textContent = '';
        const allowed = ((data.overrides || {}).allowed || []).slice().reverse();
        wrapper.hidden = !allowed.length;
        allowed.forEach(function (item) {
            const row = element('li', 'memory-habitual-allowed-item');
            row.appendChild(element('span', 'memory-habitual-phrase', item.phrase));
            row.appendChild(actionButton('clear', item.phrase, 'memory.habitualRestore', 'Stop allowing'));
            list.appendChild(row);
        });
    }

    function render() {
        if (!panel) {
            clearContent();
            return;
        }
        renderLogic(panel.rules || {});
        const lines = statusLines(panel);
        if (feedback) lines.push(feedback);
        setStatusLines(lines);
        renderEntries(panel);
        renderAllowed(panel);
        syncButtons();
    }

    function showMessage(text, tone) {
        panel = null;
        clearContent();
        setStatusLines(text ? [{ text: text, tone: tone || '' }] : []);
        syncButtons();
    }

    async function readPanel(response) {
        let data = null;
        try {
            data = await response.json();
        } catch (_) {
            data = null;
        }
        if (!response.ok || !data || data.success !== true) {
            const error = new Error((data && data.error) || ('HTTP ' + response.status));
            error.status = response.status;
            throw error;
        }
        return data;
    }

    async function load() {
        const id = ++requestId;
        feedback = null;
        if (!target) {
            showMessage(translate('memory.habitualNoCharacter', 'Choose a character first.'));
            return;
        }
        if (!ready) {
            showMessage('');
            return;
        }
        showMessage(translate('memory.habitualLoading', 'Loading habitual phrases…'));
        try {
            const response = await fetch('/api/memory/habitual_phrases?character_name='
                + encodeURIComponent(target)
                + '&ui_language=' + encodeURIComponent(uiLanguage()));
            const data = await readPanel(response);
            if (id !== requestId) return;
            panel = data;
            render();
        } catch (error) {
            if (id !== requestId) return;
            console.error('Failed to load habitual phrases:', error);
            showMessage(translate('memory.habitualLoadFailed', 'Could not load habitual phrases. Try again shortly.'), 'error');
        }
    }

    async function post(url, body, failureKey, failureFallback) {
        if (busy || !ready || !target) return;
        const id = ++requestId;
        const name = target;
        busy = true;
        syncButtons();
        try {
            const response = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(Object.assign(
                    { character_name: name, ui_language: uiLanguage() || null }, body
                ))
            });
            const data = await readPanel(response);
            if (id !== requestId || name !== target) return;
            panel = data;
            feedback = null;
        } catch (error) {
            if (id !== requestId || name !== target) return;
            console.error('Habitual phrase request failed:', error);
            feedback = error && error.status === 409
                ? { tone: 'warning', text: translate('memory.habitualRecomputeBusy', 'Already recomputing; please wait.') }
                : { tone: 'error', text: translate(failureKey, failureFallback) };
        } finally {
            busy = false;
            if (id === requestId && name === target) {
                if (panel) render();
                else {
                    setStatusLines(feedback ? [feedback] : []);
                    syncButtons();
                }
            }
        }
    }

    function recompute() {
        if (busy || !ready || !target) return;
        feedback = { text: translate('memory.habitualRecomputing', 'Recomputing; this can take a minute or two…') };
        if (panel) render();
        else setStatusLines([feedback]);
        post(
            '/api/memory/habitual_phrases/recompute', {},
            'memory.habitualRecomputeFailed', 'Could not recompute. Try again shortly.'
        );
    }

    function override(action, phrase) {
        post(
            '/api/memory/habitual_phrases/override', { phrase: phrase, action: action },
            'memory.habitualSaveFailed', 'Could not save. Try again shortly.'
        );
    }

    function panelVisible() {
        const host = document.getElementById('memory-insights-panel');
        return !!host && !host.hidden;
    }

    document.addEventListener('neko:habitual-phrases-target', function (event) {
        const detail = (event && event.detail) || {};
        const name = String(detail.name || '');
        const isReady = !!detail.ready;
        const changed = name !== target || isReady !== ready;
        target = name;
        ready = isReady;
        if (!changed && !detail.reload) {
            syncButtons();
            return;
        }
        if (!panelVisible()) {
            // Fetched on the next open, which always asks for a reload.
            requestId += 1;
            busy = false;
            panel = null;
            clearContent();
            setStatusLines([]);
            syncButtons();
            return;
        }
        busy = false;
        load();
    });

    document.addEventListener('click', function (event) {
        const button = event.target && event.target.closest
            ? event.target.closest('#memory-habitual [data-habitual-action], #memory-habitual-recompute')
            : null;
        if (!button || button.disabled) return;
        if (button.id === 'memory-habitual-recompute') {
            recompute();
            return;
        }
        override(button.dataset.habitualAction, button.dataset.habitualPhrase || '');
    });

    window.addEventListener('localechange', function () {
        if (panel) render();
    });
})();
