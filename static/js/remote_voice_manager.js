/* Explicit remote voice import. Provider behavior and credentials belong to the server. */
(function (root) {
    'use strict';

    class RemoteVoiceOperation {
        constructor(fetcher, timeout = 35000) {
            this.fetcher = fetcher;
            this.timeout = timeout;
            this.current = null;
        }
        begin(provider) {
            this.cancel();
            const operation = { provider, controller: new AbortController() };
            this.current = operation;
            return operation;
        }
        owns(operation) { return this.current === operation && !operation.controller.signal.aborted; }
        cancel() {
            if (this.current) this.current.controller.abort();
            this.current = null;
        }
        async request(operation, url, options = {}) {
            if (!this.owns(operation)) throw new DOMException('Cancelled', 'AbortError');
            const timer = setTimeout(() => operation.controller.abort(), this.timeout);
            try {
                const response = await this.fetcher(url, { ...options, signal: operation.controller.signal });
                const data = await response.json();
                if (!this.owns(operation)) throw new DOMException('Cancelled', 'AbortError');
                if (!response.ok || data.success === false) {
                    const error = new Error(data.code || data.error || 'request_failed');
                    error.code = data.code || data.error;
                    error.status = response.status;
                    error.details = data.details;
                    throw error;
                }
                return data;
            } finally { clearTimeout(timer); }
        }
    }

    if (typeof module !== 'undefined' && module.exports) module.exports = { RemoteVoiceOperation };
    if (!root.document) return;

    const t = (key, options) => root.t ? root.t('voice.remote.' + key, options) : key;
    const node = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    };
    const button = (label, handler, className = 'btn sm') => {
        const element = node('button', className, t(label));
        element.type = 'button';
        element.addEventListener('click', handler);
        return element;
    };
    const operations = new RemoteVoiceOperation((...args) => root.fetch(...args));
    let active = null;
    let buttonGeneration = 0;

    function close() {
        operations.cancel();
        const state = active;
        active = null;
        if (!state) return;
        clearTimeout(state.searchTimer);
        document.removeEventListener('keydown', state.keyboard, true);
        state.overlay.remove();
        state.background.inert = state.wasInert;
        document.body.style.overflow = state.previousOverflow;
        if (state.returnFocus && state.returnFocus.isConnected) state.returnFocus.focus();
    }

    function showError(state, error, uncertain = false) {
        if (active !== state) return;
        const code = error.code || '';
        const errorKeys = {
            CONFIG_MISSING: 'configureFirst', MANAGEMENT_CONFIG_MISSING: 'configureFirst',
            AUTH_FAILED: 'authFailed', PERMISSION_DENIED: 'permissionDenied', RATE_LIMITED: 'rateLimited',
            CONTEXT_CHANGED: 'contextChanged', VOICE_NOT_FOUND: 'voiceNotFound', VOICE_NOT_READY: 'voiceNotReady',
            VOICE_STATE_CHANGED: 'voiceStateChanged',
            INVALID_VOICE_ID: 'invalidId', INVALID_DISPLAY_NAME: 'invalidMetadata', INVALID_METADATA: 'invalidMetadata',
            LIST_UNSUPPORTED: 'listUnavailable', DETAILS_UNSUPPORTED: 'listUnavailable', IMPORT_UNSUPPORTED: 'failed',
            OVERWRITE_UNSUPPORTED: 'overwriteUnsupported', OPERATION_IN_PROGRESS: 'operationInProgress',
            LOCAL_SAVE_FAILED_AFTER_UPDATE: 'updateLocalFailed', UPDATE_OUTCOME_UNKNOWN: 'uncertain',
            LOCAL_OPERATION_FAILED: 'saveFailed', INVALID_JSON: 'invalidMetadata', INVALID_AUDIO: 'invalidAudio', AUDIO_TOO_LARGE: 'audioTooLarge',
            UPSTREAM_TIMEOUT: 'requestFailed', UPSTREAM_UNAVAILABLE: 'requestFailed', UPSTREAM_REJECTED: 'failed', UPSTREAM_INVALID_RESPONSE: 'requestFailed',
            UPLOAD_FAILED: 'uploadFailed', INVALID_CURSOR: 'requestFailed', STORAGE_ERROR: 'saveFailed'
        };
        state.status.textContent = uncertain && code !== 'LOCAL_SAVE_FAILED_AFTER_UPDATE' ? t('uncertain') : (
            t(errorKeys[code] || (error.status === 409 ? 'contextChanged' : 'requestFailed'))
        );
        state.status.classList.add('remote-voice-error');
    }

    function dialog(provider, title) {
        close();
        const state = {
            provider, context: null, voices: [], selection: null, mode: 'list', busy: false,
            returnFocus: document.activeElement, background: document.querySelector('.container'),
            previousOverflow: document.body.style.overflow
        };
        state.wasInert = state.background.inert;
        state.background.inert = true;
        document.body.style.overflow = 'hidden';
        state.overlay = node('div', 'remote-voice-overlay');
        state.panel = node('section', 'remote-voice-dialog');
        state.panel.setAttribute('role', 'dialog');
        state.panel.setAttribute('aria-modal', 'true');
        state.panel.setAttribute('aria-labelledby', 'remoteVoiceTitle');
        state.panel.tabIndex = -1;
        const header = node('div', 'remote-voice-header');
        const heading = node('h3', '', t(title));
        heading.id = 'remoteVoiceTitle';
        const closer = button('close', close, 'remote-voice-close');
        closer.setAttribute('aria-label', t('close'));
        header.append(heading, closer);
        state.panel.append(header);
        const select = document.getElementById('voiceProvider');
        const option = Array.from(select.options).find(item => item.value === provider);
        state.panel.append(node('p', 'remote-voice-provider', t('currentProvider') + ': ' + (option ? option.textContent : provider)));
        state.body = node('div', 'remote-voice-body');
        state.status = node('p', 'remote-voice-status');
        state.status.setAttribute('role', 'status');
        state.status.setAttribute('aria-live', 'polite');
        state.footer = node('div', 'remote-voice-footer');
        state.footer.append(button('cancel', close));
        state.panel.append(state.body, state.status, state.footer);
        state.overlay.append(state.panel);
        document.body.append(state.overlay);
        active = state;
        state.keyboard = event => {
            if (active !== state || event.isComposing || event.keyCode === 229) return;
            if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
            if (event.key === 'Tab') {
                const elements = Array.from(state.panel.querySelectorAll('button, input, select, a[href], [tabindex="0"]'))
                    .filter(element => !element.disabled && !element.hidden && element.getClientRects().length);
                const first = elements[0], last = elements[elements.length - 1];
                if (!first) { event.preventDefault(); state.panel.focus(); }
                else if (event.shiftKey && (document.activeElement === first || document.activeElement === state.panel)) {
                    event.preventDefault(); last.focus();
                } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
            }
        };
        document.addEventListener('keydown', state.keyboard, true);
        state.panel.focus();
        return state;
    }

    function busy(state, value) {
        state.busy = value;
        state.panel.setAttribute('aria-busy', String(value));
        if (state.submit) state.submit.disabled = value || (state.mode === 'list' && !state.selection) || (state.mode === 'manual' && !state.id.value.trim());
        if (state.mode === 'overwrite' && state.submit) state.submit.disabled = value || !state.overwriteAllowed || !state.audio.files.length;
        if (state.more) state.more.disabled = value;
        if (state.refreshStatus) state.refreshStatus.disabled = value;
        if (state.recoverPrepared) state.recoverPrepared.disabled = value || !state.recoverySnapshot;
        if (state.reopenOverwrite) state.reopenOverwrite.disabled = value || !state.overwriteAllowed;
        for (const control of state.importControls || []) control.disabled = !!state.importSubmitting;
        if (state.mode === 'list' && state.empty) state.empty.hidden = value || state.rows.children.length > 0;
    }

    async function context(state, operation) {
        const params = new URLSearchParams({ provider: state.provider });
        if (state.localRef) params.set('local_ref', state.localRef);
        const result = await operations.request(operation, '/api/characters/remote_voices/context?' + params);
        if (active !== state || !operations.owns(operation)) return null;
        state.context = result;
        return result;
    }

    function manual(state) {
        if (state.importSubmitting) return;
        clearTimeout(state.searchTimer);
        operations.cancel();
        state.mode = 'manual';
        state.selection = null;
        state.body.replaceChildren();
        const back = button('backToList', () => {
            if (state.importSubmitting) return;
            state.mode = 'list'; listView(state); refresh(state);
        }, 'remote-voice-link');
        state.body.append(back);
        const fields = node('div', 'remote-voice-manual');
        function input(label, key, required = false, value = '') {
            const wrapper = node('label', 'remote-voice-field', t(label));
            const field = node('input');
            field.type = 'text'; field.required = required; field.value = value;
            field.maxLength = key === 'remote_voice_id' ? 512 : 256;
            field.name = key;
            field.autocomplete = 'off';
            wrapper.append(field); fields.append(wrapper); return field;
        }
        state.id = input('voiceId', 'remote_voice_id', true);
        state.name = input('displayName', 'display_name');
        state.metadata = {};
        for (const specification of state.context ? state.context.required_fields || [] : []) {
            const spec = typeof specification === 'string' ? { key: specification, required: true } : specification;
            const field = input(spec.key, spec.key, spec.required, spec.default_value || '');
            field.readOnly = spec.readonly === true;
            const translated = spec.label_key && root.t ? root.t(spec.label_key) : '';
            const label = translated && translated !== spec.label_key ? translated : t(spec.key);
            field.parentElement.firstChild.textContent = label;
            state.metadata[spec.key] = field;
        }
        state.body.append(fields, node('p', 'remote-voice-hint', t('manualHint')));
        state.importControls = [back, state.id, state.name, ...Object.values(state.metadata)];
        state.submit.textContent = t('import');
        state.submit.disabled = true;
        state.id.addEventListener('input', () => busy(state, state.busy));
        state.id.focus();
        busy(state, false);
        if (!state.context) {
            const operation = operations.begin(state.provider);
            busy(state, true);
            context(state, operation).then(ctx => {
                if (!ctx || active !== state || !operations.owns(operation)) return;
                const id = state.id.value, name = state.name.value;
                manual(state);
                state.id.value = id; state.name.value = name;
                busy(state, false);
            }).catch(error => {
                if (active === state && operations.current === operation) { showError(state, error); busy(state, false); }
            });
        }
    }

    function renderRows(state) {
        const query = state.search.value.toLocaleLowerCase().trim();
        state.rows.replaceChildren();
        let visible = 0;
        for (const voice of state.voices) {
            if (query && !(String(voice.name || '') + ' ' + voice.voice_id).toLocaleLowerCase().includes(query)) continue;
            visible++;
            const row = node('tr');
            const cell = node('td');
            const radio = node('input');
            radio.type = 'radio'; radio.name = 'remoteVoiceSelection';
            radio.disabled = !!voice.imported;
            radio.checked = state.selection === voice;
            radio.setAttribute('aria-label', voice.name || voice.voice_id);
            radio.addEventListener('change', () => { state.selection = voice; busy(state, state.busy); });
            cell.append(radio); row.append(cell);
            row.append(node('td', '', voice.name || voice.voice_id), node('td', 'remote-voice-id', voice.voice_id));
            const date = voice.created_at ? new Date(voice.created_at) : null;
            row.append(node('td', '', date && !Number.isNaN(date.getTime()) ? date.toLocaleString() : t('unknown')));
            const statusKey = voice.imported ? 'alreadyImported' : voice.status === 'ready' ? 'ready' : voice.status === 'processing' ? 'processing' : voice.status === 'failed' ? 'failed' : voice.status === 'unavailable' ? 'unavailable' : 'unverified';
            row.append(node('td', '', t(statusKey)));
            state.rows.append(row);
        }
        state.empty.hidden = !!visible;
        state.more.hidden = !state.nextCursor;
    }

    function listView(state) {
        state.body.replaceChildren();
        const toolbar = node('div', 'remote-voice-toolbar');
        state.search = node('input'); state.search.type = 'search'; state.search.placeholder = t('search');
        state.search.setAttribute('aria-label', t('search'));
        state.search.addEventListener('input', () => {
            if (state.importSubmitting) return;
            clearTimeout(state.searchTimer);
            operations.cancel();
            state.selection = null;
            state.voices = []; state.nextCursor = null;
            renderRows(state); busy(state, true);
            state.status.textContent = t('loading');
            state.searchTimer = setTimeout(() => refresh(state), 250);
        });
        const refreshButton = button('refresh', () => refresh(state));
        toolbar.append(state.search, refreshButton);
        const scroller = node('div', 'remote-voice-table-scroll');
        const table = node('table', 'remote-voice-table');
        const head = node('thead'), row = node('tr');
        for (const key of ['selection', 'name', 'voiceId', 'createdAt', 'status']) row.append(node('th', '', t(key)));
        head.append(row); state.rows = node('tbody'); table.append(head, state.rows); scroller.append(table);
        state.empty = node('p', 'remote-voice-hint', t('empty'));
        state.more = button('loadMore', () => refresh(state, true)); state.more.hidden = true;
        const manualButton = button('manualEntry', () => manual(state), 'remote-voice-link');
        const settings = button('apiSettings', () => { if (typeof root.openApiSettings === 'function') root.openApiSettings(); }, 'remote-voice-link');
        state.body.append(toolbar, scroller, state.empty, state.more, manualButton, settings);
        state.importControls = [state.search, refreshButton, manualButton];
        state.submit.textContent = t('importSelected');
        renderRows(state);
    }

    async function refresh(state, append = false) {
        if (active !== state || state.importSubmitting || state.mode !== 'list' || (append && state.busy)) return;
        clearTimeout(state.searchTimer);
        const operation = operations.begin(state.provider);
        const query = state.search.value.trim();
        const searchDeadline = query ? setTimeout(() => operation.controller.abort(), operations.timeout) : null;
        state.selection = null; state.status.textContent = t('loading'); state.status.classList.remove('remote-voice-error');
        if (!append) { state.voices = []; state.nextCursor = null; renderRows(state); }
        busy(state, true);
        try {
            const ctx = append && state.context ? state.context : await context(state, operation);
            if (!ctx || active !== state || !operations.owns(operation)) return;
            if (!ctx.capabilities.list) {
                state.status.textContent = t(ctx.configured ? 'listUnavailable' : 'configureFirst');
                return;
            }
            const params = new URLSearchParams({ provider: state.provider, context_token: ctx.context_token });
            if (query) params.set('query', query);
            if (append && state.nextCursor) params.set('cursor', state.nextCursor);
            let result;
            const cursors = new Set();
            if (params.has('cursor')) cursors.add(params.get('cursor'));
            // Providers often filter each page locally. Advance empty search
            // pages until a match or exhaustion; never claim no matches early.
            do {
                result = await operations.request(operation, '/api/characters/remote_voices?' + params);
                if (active !== state || !operations.owns(operation)) return;
                if (!query || (result.voices || []).length || !result.next_cursor) break;
                if (cursors.has(result.next_cursor)) {
                    const error = new Error('INVALID_CURSOR'); error.code = 'INVALID_CURSOR'; throw error;
                }
                cursors.add(result.next_cursor);
                params.set('cursor', result.next_cursor);
            } while (operations.owns(operation));
            if (active !== state || !operations.owns(operation)) return;
            const ids = new Set(state.voices.map(voice => voice.voice_id));
            for (const voice of result.voices || []) if (!ids.has(voice.voice_id)) { state.voices.push(voice); ids.add(voice.voice_id); }
            state.nextCursor = result.next_cursor || null;
            state.status.textContent = '';
            renderRows(state);
        } catch (error) {
            if (active === state && operations.current === operation) showError(state, error);
        } finally {
            clearTimeout(searchDeadline);
            if (active === state && operations.current === operation) busy(state, false);
        }
    }

    async function submitImport(state) {
        if (state.busy || active !== state) return;
        const manualMode = state.mode === 'manual';
        const id = manualMode ? state.id.value.trim() : state.selection && state.selection.voice_id;
        if (!id) return;
        if (manualMode && Object.values(state.metadata).some(field => !field.reportValidity())) return;
        state.importSubmitting = true;
        const operation = operations.begin(state.provider);
        busy(state, true); state.status.textContent = t('importing');
        try {
            const ctx = state.context || await context(state, operation);
            if (!ctx || active !== state || !operations.owns(operation)) return;
            const metadata = {};
            if (manualMode) for (const [key, field] of Object.entries(state.metadata)) metadata[key] = field.value.trim();
            const result = await operations.request(operation, '/api/characters/voices/import', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ provider: state.provider, remote_voice_id: id, display_name: manualMode ? state.name.value.trim() : '', context_token: ctx.context_token, metadata })
            });
            if (active !== state || !operations.owns(operation)) return;
            state.status.textContent = t(result.verification === 'verified' ? 'imported' : 'importedUnverified');
            state.submit.hidden = true;
            state.body.replaceChildren(node('p', 'remote-voice-hint', t('bindHint')));
            if (typeof root.loadVoices === 'function') await root.loadVoices();
        } catch (error) {
            if (active === state && operations.current === operation) showError(state, error);
        } finally {
            state.importSubmitting = false;
            if (active === state && operations.current === operation) busy(state, false);
        }
    }

    function openImport() {
        const provider = document.getElementById('voiceProvider').value;
        const state = dialog(provider, 'title');
        state.submit = button('importSelected', () => submitImport(state)); state.submit.disabled = true;
        state.footer.append(state.submit);
        listView(state); refresh(state);
    }

    function updateOverwriteView(state, result, error = null, sent = true) {
        if (active !== state) return;
        const snapshot = (error ? error.details : result && result.details)?.voice_state;
        const valid = snapshot && snapshot.local_ref === state.localRef && Number.isInteger(snapshot.record_revision) && snapshot.record_revision >= 0;
        if (valid && Number.isInteger(state.recordRevision) && snapshot.record_revision < state.recordRevision) return;
        if (valid) state.recordRevision = snapshot.record_revision;
        const status = valid ? snapshot.overwrite_status : result && result.status;
        state.currentOverwriteStatus = status;
        const pending = status === 'processing' || status === 'unknown';
        // Only a current server snapshot may reopen submission. Missing fields
        // (including an older server) leave an explicit query exit after sending.
        const actions = valid && Array.isArray(snapshot.actions) ? snapshot.actions :
            pending || error ? ['refresh'] : [];
        state.overwriteAllowed = ['completed', 'failed'].includes(status) && actions.includes('overwrite') && !!state.context?.capabilities.overwrite;
        if (state.submit) state.submit.hidden = !state.overwriteAllowed;
        if (state.refreshStatus) state.refreshStatus.hidden = !actions.includes('refresh');
        state.recoverySnapshot = valid && pending && actions.includes('recover') &&
            typeof snapshot.operation_id === 'string' && snapshot.operation_id
            ? { operation_id: snapshot.operation_id, record_revision: snapshot.record_revision } : null;
        if (state.recoverPrepared) state.recoverPrepared.hidden = !state.recoverySnapshot;
        if (state.recoveryHint) state.recoveryHint.hidden = !state.recoverySnapshot;
        if (state.reopenOverwrite) state.reopenOverwrite.hidden = !state.overwriteAllowed;
        if (error) {
            const uncertain = sent && (!valid || pending) && error.code !== 'OPERATION_IN_PROGRESS';
            showError(state, error, uncertain);
        } else {
            state.status.classList.remove('remote-voice-error');
            state.status.textContent = t(status === 'completed' ? 'completed' : status === 'failed' ? 'failed' :
                status === 'processing' ? 'processing' : 'uncertain');
        }
        busy(state, state.busy);
    }

    function recoveryControls(state) {
        state.recoveryHint = node('p', 'remote-voice-hint', t('recoverPreparedHint'));
        state.recoveryHint.hidden = true;
        state.body.append(state.recoveryHint);
        state.recoverPrepared = button('recoverPrepared', () => recoverPreparedOverwrite(state));
        state.recoverPrepared.hidden = true;
        state.recoverPrepared.disabled = true;
        state.footer.append(state.recoverPrepared);
    }

    async function recoverPreparedOverwrite(state) {
        if (active !== state || state.busy || !state.recoverySnapshot) return;
        const expected = state.recoverySnapshot;
        const operation = operations.begin(state.provider);
        let recovered = false;
        busy(state, true); state.status.textContent = t('recoveringPrepared');
        try {
            // Refresh account/project context before using the advisory action.
            const ctx = await context(state, operation);
            if (!ctx || active !== state || !operations.owns(operation) || state.recoverySnapshot !== expected) return;
            const result = await operations.request(operation,
                '/api/characters/voices/' + encodeURIComponent(state.localRef) + '/recover_overwrite', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ context_token: ctx.context_token, ...expected })
                });
            if (active !== state || !operations.owns(operation)) return;
            updateOverwriteView(state, result);
            const snapshot = result.details?.voice_state;
            // A successful local transition can outlive the subsequent record
            // read. Keep a query exit without inventing an unlocked snapshot.
            if (!snapshot && state.refreshStatus) state.refreshStatus.hidden = false;
            recovered = result.recovered === true && snapshot?.local_ref === state.localRef &&
                snapshot.operation_id === expected.operation_id && snapshot.record_revision > expected.record_revision &&
                snapshot.record_revision === state.recordRevision && snapshot.overwrite_status === 'failed';
            if (recovered) state.status.textContent = t('preparedRecovered');
            if (typeof root.loadVoices === 'function') await root.loadVoices();
        } catch (error) {
            if (active === state && operations.current === operation) {
                updateOverwriteView(state, null, error, false);
                if (!error.details?.voice_state) state.status.textContent = t('recoveryUncertain');
            }
        } finally {
            if (active === state && operations.current === operation) {
                busy(state, false);
                if (recovered) {
                    const next = state.reopenOverwrite && !state.reopenOverwrite.hidden ? state.reopenOverwrite : state.audio;
                    if (next) next.focus();
                }
            }
        }
    }

    function openOverwrite(localRef, voice) {
        const state = dialog(voice.provider, 'overwrite');
        state.localRef = localRef;
        state.mode = 'overwrite';
        state.body.append(node('p', 'remote-voice-warning', t('overwriteWarning')));
        state.body.append(node('p', 'remote-voice-id', voice.remote_voice_id || localRef));
        const label = node('label', 'remote-voice-field', t('audio'));
        const audio = node('input'); audio.type = 'file'; audio.accept = 'audio/*'; label.append(audio);
        state.audio = audio;
        state.overwriteAllowed = !['processing', 'unknown'].includes(voice.overwrite_status);
        state.body.append(label);
        state.submit = button('overwrite', async () => {
            if (state.busy || !state.overwriteAllowed || !audio.files.length) return;
            const operation = operations.begin(state.provider);
            busy(state, true); state.status.textContent = t('submitting');
            let sent = false;
            try {
                const ctx = await context(state, operation);
                if (!ctx || active !== state || !operations.owns(operation)) return;
                if (!ctx.capabilities.overwrite) { state.status.textContent = t('overwriteUnsupported'); return; }
                const body = new FormData(); body.append('audio', audio.files[0]); body.append('context_token', ctx.context_token);
                sent = true;
                const result = await operations.request(operation, '/api/characters/voices/' + encodeURIComponent(localRef) + '/overwrite', { method: 'POST', body });
                if (active !== state || !operations.owns(operation)) return;
                updateOverwriteView(state, result);
                if (typeof root.loadVoices === 'function') await root.loadVoices();
            } catch (error) {
                if (active === state && operations.current === operation) {
                    updateOverwriteView(state, null, error, sent);
                    if (sent && typeof root.loadVoices === 'function') await root.loadVoices();
                }
            } finally { if (active === state && operations.current === operation) busy(state, false); }
        });
        state.submit.disabled = true;
        audio.addEventListener('change', () => busy(state, state.busy));
        state.footer.append(state.submit);
        const refreshStatus = button('refreshStatus', () => fetchOverwriteStatus(state, localRef));
        refreshStatus.hidden = state.overwriteAllowed;
        state.footer.append(refreshStatus);
        state.refreshStatus = refreshStatus;
        state.submit.hidden = !state.overwriteAllowed;
        recoveryControls(state);
    }

    async function fetchOverwriteStatus(state, localRef) {
        if (active !== state || state.busy) return;
        const operation = operations.begin(state.provider);
        busy(state, true);
        if (state.refreshStatus) state.refreshStatus.disabled = true;
        try {
            const ctx = state.context || await context(state, operation);
            if (!ctx || active !== state || !operations.owns(operation)) return;
            const params = new URLSearchParams({ context_token: ctx.context_token });
            const result = await operations.request(operation, '/api/characters/voices/' + encodeURIComponent(localRef) + '/overwrite_status?' + params);
            if (active !== state || !operations.owns(operation)) return;
            updateOverwriteView(state, result);
            if (typeof root.loadVoices === 'function') await root.loadVoices();
        } catch (error) { if (active === state && operations.current === operation) updateOverwriteView(state, null, error); }
        finally {
            if (active === state && operations.current === operation) {
                busy(state, false);
                if (state.refreshStatus) state.refreshStatus.disabled = false;
            }
        }
    }

    function openStatus(localRef, voice) {
        const state = dialog(voice.provider, 'refreshStatus');
        state.localRef = localRef;
        state.mode = 'status';
        state.body.append(node('p', 'remote-voice-id', voice.remote_voice_id || localRef));
        state.refreshStatus = button('refreshStatus', () => fetchOverwriteStatus(state, localRef));
        state.footer.append(state.refreshStatus);
        state.reopenOverwrite = button('overwriteAgain', () => {
            if (active !== state || state.busy || !state.overwriteAllowed) return;
            openOverwrite(localRef, { ...voice, overwrite_status: state.currentOverwriteStatus });
        });
        state.reopenOverwrite.hidden = true;
        state.footer.append(state.reopenOverwrite);
        recoveryControls(state);
        fetchOverwriteStatus(state, localRef);
    }

    async function updateEntry() {
        const generation = ++buttonGeneration;
        const entry = document.getElementById('importExistingVoice');
        if (!entry) return;
        const provider = document.getElementById('voiceProvider').value;
        entry.hidden = true;
        entry.disabled = typeof root.t !== 'function';
        try {
            const metadata = typeof loadVoiceCloneProviderRestrictionState === 'function' ? await loadVoiceCloneProviderRestrictionState() : null;
            if (generation !== buttonGeneration) return;
            const info = metadata && metadata.ttsProviders[provider === 'cosyvoice_intl' ? 'cosyvoice' : provider];
            entry.hidden = !(info && info.voice_management && info.voice_management.manual_import);
        } catch (_) { /* A failed registry load must not invent provider capabilities. */ }
    }
    root.RemoteVoiceManager = { openImport, openOverwrite, openStatus, close, updateEntry };
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('importExistingVoice').addEventListener('click', openImport);
        document.getElementById('voiceProvider').addEventListener('change', () => { close(); updateEntry(); });
        updateEntry();
    });
    root.addEventListener('pagehide', close);
    root.addEventListener('localechange', updateEntry);
})(typeof window === 'undefined' ? globalThis : window);
