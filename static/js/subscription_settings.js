/* Copyright 2025-2026 Project N.E.K.O. Team; SPDX-License-Identifier: Apache-2.0 */
// Subscriptions & plans panel. Each subscription gets a card: sign-in or key, the
// features it takes over (one model each) and an on/off switch. Everything is
// stored apart from the settings form (POST /api/config/subscriptions/...), one
// change per click, so this panel never rewrites the per-feature settings below:
// turning a subscription off brings those settings back as they were.
// It also marks, in Per-Feature Models and under General Model Provider, which
// features currently go through a subscription.
(() => {
    'use strict';
    const section = document.getElementById('subscriptions-settings');
    if (!section) return;
    const toggle = document.getElementById('subscriptions-toggle-btn');
    const panel = document.getElementById('subscriptions-options');
    const cards = document.getElementById('subscription-cards');
    const loadError = document.getElementById('subscriptions-load-error');
    const summary = document.getElementById('subscription-summary');

    // Feature tiers: API name -> [Per-Feature Models block key, label key, label fallback].
    const TIERS = {
        conversation: ['conversation', 'api.conversationModelConfig', 'Chat model'],
        memory: ['memory', 'api.summaryModelConfig', 'Memory upkeep (large model)'],
        recognition: ['recognition', 'api.emotionModelConfig', 'Recognition (small model)'],
        agent: ['agent', 'api.agentApiConfigTitle', 'Familiar model · General'],
        agent_small: ['agent-small', 'api.agentSmallApiConfigTitle', 'Familiar model · Small'],
        agent_advanced: ['agent-advanced', 'api.agentAdvancedApiConfigTitle', 'Familiar model · Advanced'],
        game_main: ['game-main', 'api.gameMainModelConfig', 'Minigame chat model'],
        game_summary: ['game-summary', 'api.gameSummaryModelConfig', 'Minigame memory model'],
        game_recognition: ['game-recognition', 'api.gameRecognitionModelConfig', 'Minigame recognition model'],
    };
    const fill = (text, vars) => String(text).replace(/\{\{(\w+)\}\}/g, (_, name) => (vars && name in vars ? vars[name] : ''));
    const translate = (key, fallback, vars) => {
        const value = window.t ? window.t(key, vars || undefined) : '';
        return fill(value && value !== key ? value : fallback, vars);
    };
    const t = (key, fallback, vars) => translate(`api.subscriptions.${key}`, fallback, vars);
    const tierLabel = tier => translate(TIERS[tier][1], TIERS[tier][2]);

    let state = null;
    let busy = false;
    const accountModels = {};
    const messages = {};
    // Keys typed but not saved yet survive re-rendering the cards.
    const keyDrafts = {};
    // A reply is applied only while it is still the newest: a status load that a
    // change overtook, or a model list of an account switched away from, is dropped.
    let stateVersion = 0;
    let loadTicket = 0;
    let accountGeneration = 0;

    toggle.addEventListener('click', () => {
        panel.hidden = !panel.hidden;
        toggle.setAttribute('aria-expanded', String(!panel.hidden));
        toggle.classList.toggle('rotated', !panel.hidden);
        if (!panel.hidden) load();
    });

    const API_TIMEOUT_MS = 60000;
    async function api(path, body) {
        const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
            ? AbortSignal.timeout(API_TIMEOUT_MS) : undefined;
        let response;
        let data;
        try {
            response = await fetch(`/api/config/subscriptions${path}`, {
                method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
                headers: { 'Content-Type': 'application/json' }, signal,
                ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            });
        } catch (error) {
            throw new Error(t('error.network', 'Could not reach the subscription settings. Please retry.'));
        }
        try {
            data = await response.json();
        } catch (error) {
            // The server answered, just not with the settings' own reply (an unexpected error page).
            throw new Error(t('error.server', 'The subscription settings ran into an error (status {{status}}). Please retry.', { status: response.status }));
        }
        if (!response.ok) {
            const detail = data && data.detail;
            // Request validation answers with a list of problems, not one message.
            if (Array.isArray(detail)) throw new Error(t('error.invalid', 'The subscription settings could not take this change. Reload the page and retry.'));
            const code = detail && typeof detail === 'object' ? detail.code : '';
            const message = detail && typeof detail === 'object' ? detail.message : String(detail || '');
            throw new Error(code ? t(`error.${code}`, message) : message || t('error.network', 'Request failed.'));
        }
        return data;
    }

    const planName = id => {
        const plan = (state && state.plans.find(item => item.id === id)) || { name: id };
        return translate(`api.subscriptions.plans.${id}`, plan.name);
    };
    const entryOf = id => (state && state.state.plans[id]) || { enabled: false, tiers: {}, has_key: false, region: '' };

    function say(planId, text, kind) {
        messages[planId] = { text, kind: kind || '' };
        const node = cards.querySelector(`.subscription-card[data-plan="${planId}"] .subscription-message`);
        if (node) {
            node.textContent = text;
            node.dataset.kind = kind || '';
        }
    }

    function setBusy(value) {
        busy = value;
        cards.classList.toggle('is-busy', value);
        // Only the controls this script draws; the ChatGPT account controls belong to chatgpt_settings.js.
        for (const control of cards.querySelectorAll('[data-role] input, [data-role] select, [data-role] button')) {
            control.disabled = value;
        }
        // Drawn outside the cards (under Per-Feature Models), changing the same state.
        for (const control of document.querySelectorAll('[data-subscription-control]')) {
            control.disabled = value;
        }
    }

    // The answer the panel draws from; anything else (an older server, a stand-in
    // answering every address alike) is a failed load, never drawn.
    const isStatus = data => Boolean(data) && Array.isArray(data.plans)
        && Boolean(data.state && data.state.plans) && typeof data.state.plans === 'object' && Boolean(data.tiers);

    function applyState(data) {
        state = data;
        stateVersion += 1;
    }

    // One change at a time: controls are disabled meanwhile, and the cards are drawn
    // again afterwards from the server's state, so an edit never looks saved when it was not.
    async function act(planId, action) {
        if (busy) return;
        setBusy(true);
        let failed = false;
        try {
            const data = await action();
            if (isStatus(data)) applyState(data);
            return data;
        } catch (error) {
            failed = true;
            say(planId, error.message, 'error');
        } finally {
            setBusy(false);
            render();
            // A lost answer does not mean nothing was saved: read what the server has now.
            if (failed) load();
        }
    }

    async function load() {
        const ticket = ++loadTicket;
        const version = stateVersion;
        try {
            const data = await api('');
            if (ticket !== loadTicket || version !== stateVersion) return;
            if (!isStatus(data)) throw new Error(t('error.network', 'Could not reach the subscription settings. Please retry.'));
            applyState(data);
            loadError.hidden = true;
            render();
        } catch (error) {
            // A change that succeeded meanwhile already shows the current state.
            if (ticket !== loadTicket || version !== stateVersion) return;
            // Nothing retries on its own: the server is this machine, so the page cannot
            // tell when it is back. One click reads the state again.
            loadError.replaceChildren(error.message, ' ', el('button', {
                type: 'button', className: 'gsv-tutorial-link', text: translate('common.retry', 'Retry'),
                onclick: () => load(),
            }));
            loadError.dataset.kind = 'error';
            loadError.hidden = false;
        }
    }

    function el(tag, attrs, ...children) {
        const node = document.createElement(tag);
        for (const [name, value] of Object.entries(attrs || {})) {
            if (value === undefined || value === null || value === false) continue;
            if (name === 'text') node.textContent = value;
            else if (name === 'className') node.className = value;
            else if (name.startsWith('on')) node.addEventListener(name.slice(2), value);
            else node.setAttribute(name, value === true ? '' : value);
        }
        for (const child of children) if (child) node.appendChild(child);
        return node;
    }

    function modelChoices(plan) {
        const fixed = plan.models === 'account' ? [] : plan.models;
        const listed = accountModels[plan.id] || [];
        return [...listed, ...fixed.filter(model => !listed.includes(model))];
    }

    async function fetchAccountModels(plan) {
        const generation = accountGeneration;
        const data = await api(`/${plan.id}/models`, {});
        if (!data.success) throw new Error(data.error || t('error.models', 'Could not list models.'));
        const models = data.models.map(model => (typeof model === 'string' ? model : model.id)).filter(Boolean);
        // The ChatGPT account changed while listing: these models belong to the old one.
        if (plan.auth === 'chatgpt_oauth' && generation !== accountGeneration) return [];
        accountModels[plan.id] = models;
        return models;
    }

    async function chooseTier(plan, tier, model) {
        const info = state.tiers[tier] || {};
        // Ticked under another plan, on or off: moving it takes it away from there.
        const owner = info.assigned_to || info.claimed_by;
        if (model && owner && owner !== plan.id) {
            const vars = { feature: tierLabel(tier), from: planName(owner), to: planName(plan.id) };
            const question = entryOf(owner).enabled
                ? t('moveConfirm', '{{feature}} is taken over by {{from}} now. Let {{to}} take it over instead?', vars)
                : t('moveConfirmOff', '{{feature}} is ticked under {{from}} (that subscription is off). Let {{to}} take it over instead?', vars);
            const confirmed = typeof window.showConfirm === 'function'
                ? await window.showConfirm(question, t('title', 'Subscriptions & plans'))
                : window.confirm(question);
            if (!confirmed) {
                render();
                return;
            }
        }
        await act(plan.id, () => api(`/${plan.id}/tier`, { tier, model: model || null }));
    }

    function tierRows(plan, entry) {
        const list = el('div', { className: 'subscription-tiers' },
            el('p', { className: 'subscription-tiers-title', text: t('features', 'Features this subscription takes over') }));
        const choices = modelChoices(plan);
        for (const tier of plan.tiers) {
            const chosen = entry.tiers[tier] || '';
            const info = state.tiers[tier] || {};
            const owner = info.claimed_by;
            const checkbox = el('input', { type: 'checkbox', checked: Boolean(chosen) });
            const select = el('select', { className: 'subscription-model', 'aria-label': t('modelFor', 'Model for {{feature}}', { feature: tierLabel(tier) }) });
            const options = [...choices];
            if (chosen && !options.includes(chosen)) options.unshift(chosen);
            const preferred = chosen || plan.default_models[tier] || options[0] || '';
            for (const model of options) select.appendChild(el('option', { value: model, text: model, selected: model === preferred }));
            if (!options.length) select.appendChild(el('option', { value: '', text: t('noModels', 'Load the model list first') }));
            select.disabled = !options.length;
            checkbox.addEventListener('change', async () => {
                if (!checkbox.checked) return chooseTier(plan, tier, null);
                let model = select.value;
                if (!model && plan.fetch_models) {
                    // Listing the models is part of this change: nothing else is
                    // clickable meanwhile, so the box cannot be unticked under it.
                    if (busy) return;
                    setBusy(true);
                    try {
                        model = (await fetchAccountModels(plan))[0] || '';
                    } catch (error) {
                        say(plan.id, error.message, 'error');
                    } finally {
                        setBusy(false);
                    }
                }
                if (!model) {
                    say(plan.id, t('error.no_model', 'Choose a model first.'), 'error');
                    // Drawn again from the last confirmed state: the box unticked, an empty
                    // model list disabled again.
                    render();
                    return;
                }
                return chooseTier(plan, tier, model);
            });
            select.addEventListener('change', () => { if (checkbox.checked) chooseTier(plan, tier, select.value); });
            let note = '';
            if (owner && owner !== plan.id) note = t('takenBy', 'Now taken over by {{plan}}', { plan: planName(owner) });
            else if (owner === plan.id && info.own_setting) note = t('ownSettingWins', 'Set up under Per-Feature Models; that setting is used instead');
            list.appendChild(el('div', { className: 'subscription-tier-row' },
                el('label', { className: 'subscription-tier-label' }, checkbox, el('span', { text: tierLabel(tier) })),
                select,
                note ? el('span', { className: 'subscription-tier-note', text: note }) : null));
        }
        if (plan.fetch_models) {
            list.appendChild(el('button', {
                type: 'button', className: 'gsv-tutorial-link', text: t('loadModels', 'Load models from this account'),
                onclick: () => act(plan.id, async () => {
                    const models = await fetchAccountModels(plan);
                    say(plan.id, t('modelsLoaded', '{{count}} models available.', { count: models.length }));
                }),
            }));
        }
        return list;
    }

    function keyRows(plan, entry) {
        const rows = el('div', { className: 'subscription-key' });
        const input = el('input', {
            type: 'password', autocomplete: 'off', className: 'subscription-key-input', value: keyDrafts[plan.id] || '',
            'aria-label': t('keyLabel', '{{plan}} key', { plan: planName(plan.id) }),
            placeholder: entry.has_key ? t('keySaved', 'Key saved (enter a new one to replace it)')
                : plan.uses_general_provider_key ? t('keyFromGeneral', 'Using the matching key already entered on this page')
                    : t('keyPlaceholder', 'Paste the subscription key{{prefix}}', { prefix: plan.key_prefix ? ` (${plan.key_prefix}…)` : '' }),
        });
        input.addEventListener('input', () => { keyDrafts[plan.id] = input.value; });
        let region = null;
        if (plan.regions.length > 1) {
            region = el('select', { className: 'subscription-region', 'aria-label': t('region', 'Region') });
            for (const key of plan.regions) {
                region.appendChild(el('option', { value: key, text: t(`regions.${key}`, key), selected: key === (entry.region || plan.default_region) }));
            }
            region.addEventListener('change', () => act(plan.id, () => api(`/${plan.id}/region`, { region: region.value })));
        }
        rows.appendChild(el('div', { className: 'field-row subscription-key-row' },
            input, region,
            el('button', {
                type: 'button', className: 'gsv-tutorial-link', text: t('saveKey', 'Save key'),
                onclick: () => {
                    const key = input.value.trim();
                    if (!key) return say(plan.id, t('error.missing_key', 'Enter the key first.'), 'error');
                    return act(plan.id, async () => {
                        const data = await api(`/${plan.id}/key`, { api_key: key, region: region ? region.value : null });
                        delete keyDrafts[plan.id];
                        say(plan.id, t('keySavedNotice', 'Key saved.'));
                        return data;
                    });
                },
            }),
            entry.has_key ? el('button', {
                type: 'button', className: 'gsv-tutorial-link', text: t('removeKey', 'Remove key'),
                onclick: async () => {
                    const question = t('removeKeyConfirm', 'Remove the {{plan}} key? The subscription turns off and its features use your own settings again.', { plan: planName(plan.id) });
                    const confirmed = typeof window.showConfirm === 'function'
                        ? await window.showConfirm(question, t('title', 'Subscriptions & plans'), { danger: true })
                        : window.confirm(question);
                    if (confirmed) {
                        delete keyDrafts[plan.id];
                        await act(plan.id, () => api(`/${plan.id}/key`, { api_key: '' }));
                    }
                },
            }) : null));
        return rows;
    }

    function header(plan, entry) {
        const switchInput = el('input', { type: 'checkbox', checked: Boolean(entry.enabled) });
        switchInput.addEventListener('change', () => act(plan.id, () => api(`/${plan.id}/enable`, { enabled: switchInput.checked })));
        return [
            el('span', { className: 'subscription-name', text: planName(plan.id) }),
            plan.verified ? null : el('span', { className: 'subscription-badge', text: t('unverified', 'Not yet tested'), title: t('unverifiedHint', 'Not yet tested with a real subscription; report problems if it misbehaves.') }),
            el('label', { className: 'subscription-switch' }, switchInput, el('span', { text: t('useIt', 'Use this subscription') })),
        ];
    }

    function footer(plan, entry) {
        const test = el('button', {
            type: 'button', className: 'gsv-tutorial-link', text: t('test', 'Test connection'),
            onclick: () => act(plan.id, async () => {
                say(plan.id, t('testing', 'Testing…'));
                const result = await api(`/${plan.id}/test`, {});
                say(plan.id, result.success ? t('testOk', 'Connected.') : (result.error || t('testFailed', 'Connection failed.')), result.success ? 'ok' : 'error');
            }),
        });
        const stateText = plan.auth === 'chatgpt_oauth'
            ? (plan.ready ? '' : t('needsSignIn', 'Sign in to ChatGPT above to use this subscription.'))
            : (plan.ready ? (entry.has_key ? t('hasKey', 'Key saved.') : t('usesGeneralKey', 'Uses the matching key already entered elsewhere on this page (General Model Provider or API Key Book).'))
                : (entry.enabled
                    ? t('keyMissingWhileOn', 'No key available: the features this subscription took will fail instead of using another key. Enter the key or turn the subscription off.')
                    : t('needsKey', 'Enter the key to use this subscription.')));
        const message = el('p', { className: 'subscription-message', role: 'status', 'aria-live': 'polite' });
        const kept = messages[plan.id];
        if (kept) {
            message.textContent = kept.text;
            message.dataset.kind = kept.kind;
        }
        return [
            stateText ? el('p', {
                className: 'subscription-state', text: stateText,
                ...(entry.enabled && !plan.ready ? { 'data-kind': 'error' } : {}),
            }) : null,
            el('div', { className: 'subscription-actions' }, test,
                plan.info_url && plan.auth !== 'chatgpt_oauth'
                    ? el('a', { className: 'gsv-tutorial-link', href: plan.info_url, target: '_blank', rel: 'noopener noreferrer', text: t('planInfo', 'Plan details and usage') })
                    : null),
            message,
        ];
    }

    function render() {
        if (!isStatus(state)) return;
        for (const plan of state.plans) {
            const entry = entryOf(plan.id);
            let card = cards.querySelector(`.subscription-card[data-plan="${plan.id}"]`);
            if (!card) {
                card = el('div', { className: 'subscription-card', 'data-plan': plan.id },
                    el('div', { className: 'subscription-card-header', 'data-role': 'header' }),
                    el('div', { className: 'subscription-card-body', 'data-role': 'body' }));
                cards.appendChild(card);
            }
            card.classList.toggle('is-on', Boolean(entry.enabled));
            card.querySelector('[data-role="header"]').replaceChildren(...header(plan, entry).filter(Boolean));
            const body = card.querySelector('[data-role="body"]');
            body.replaceChildren(...[
                plan.auth === 'api_key' ? keyRows(plan, entry) : null,
                tierRows(plan, entry),
                ...footer(plan, entry),
            ].filter(Boolean));
        }
        markFeatures();
        // Drawn again mid-request (a language switch, say): the new controls wait too.
        if (busy) setBusy(true);
    }

    // Per-Feature Models and General Model Provider: which features go through a subscription.
    function markFeatures() {
        const grouped = {};
        for (const [tier, [blockKey]] of Object.entries(TIERS)) {
            const info = state.tiers[tier] || {};
            const content = document.getElementById(`${blockKey}-model-content`);
            const container = content && content.closest('.nested-model-config-container, .model-config-container');
            const headerNode = document.querySelector(`[onclick="toggleModelConfig('${blockKey}')"] .model-title`);
            if (headerNode) {
                const old = headerNode.querySelector('.subscription-tag');
                if (old) old.remove();
            }
            if (content) {
                const oldNote = content.querySelector(':scope > .subscription-feature-note');
                if (oldNote) oldNote.remove();
            }
            if (container) container.classList.toggle('subscription-taken', Boolean(info.effective));
            if (!info.effective) continue;
            (grouped[info.effective] = grouped[info.effective] || []).push(tierLabel(tier));
            const name = planName(info.effective);
            if (headerNode) headerNode.appendChild(el('span', { className: 'subscription-tag', text: t('tag', 'Subscription · {{plan}}', { plan: name }) }));
            if (content) {
                const direct = info.claimed_by === info.effective;
                const note = el('div', { className: 'field-row subscription-feature-note' },
                    el('em', { text: direct
                        ? t('featureNote', 'This feature uses the {{plan}} subscription now. The settings below are kept and come back when the subscription is off; their connectivity light does not apply meanwhile.', { plan: name })
                        : t('featureNoteFollow', 'This feature follows another one that uses the {{plan}} subscription.', { plan: name }) }),
                    direct ? el('button', {
                        type: 'button', className: 'btn secondary', text: t('giveBack', 'Use my own setting for this'),
                        'data-subscription-control': '', disabled: busy,
                        onclick: () => act(info.effective, () => api(`/${info.effective}/tier`, { tier, model: null })),
                    }) : null);
                content.prepend(note);
            }
        }
        const parts = Object.entries(grouped).map(([plan, labels]) =>
            t('summaryPart', '{{features}} use the {{plan}} subscription', { features: labels.join(t('listSeparator', ', ')), plan: planName(plan) }));
        if (parts.length) {
            summary.textContent = `${parts.join(t('sentenceSeparator', '; '))}${t('summaryTail', ' (manage under Subscriptions & plans).')}`;
            summary.hidden = false;
        } else {
            summary.textContent = '';
            summary.hidden = true;
        }
    }

    window.addEventListener('neko:chatgpt-account-changed', () => {
        accountGeneration += 1;
        delete accountModels.chatgpt;
        load();
    });
    window.addEventListener('neko:core-config-saved', () => load());
    window.addEventListener('localechange', () => render());
    window.addEventListener('pageshow', () => load());
    load();
})();
