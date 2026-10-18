/* Opt-in chat-original controls. Restoring originals never submits chat or models. */
(function () {
    'use strict';
    var ID = /^[0-9a-f]{32}(?![\s\S])/;
    var liveIds = new Map();
    var userRequests = new Map();
    var assistantTurns = new Map();
    var completedTurns = new Set();
    var knownIds = new Map();
    var deletedLocalIds = new Map();
    var knownCursors = new Map();
    var historyPollRunning = false;
    var pendingHistoryHint = null;
    var historyPollTimer = null;
    var activeUid = null;
    var activeName = null;
    var historyRequestGeneration = 0;
    var dialog = null;
    var settingsCharacterName = null;
    var statusNode = null;
    var DEFAULT_SERVICE_URL = 'https://sync.nekotech.cn';
    var configuredServiceUrl = null;
    var activeBindingKey = null;
    var activeBinding = null;
    var operationRunning = false;
    var writerRecoveryRequired = false;
    var historyPreview = null;
    var historyPreviewNode = null;
    var historyConsentGeneration = 0;
    var foregroundWakeRunning = false;

    function text(key, values) {
        // All visible labels use the same eight-locale namespace.
        return window.t ? window.t('chatSync.' + key, values || {}) : key;
    }

    function characterName() {
        // Resolve the current local profile, then use its persisted UID from the API.
        return settingsCharacterName || window.lanlan_config && window.lanlan_config.lanlan_name || window._currentCatgirl || '';
    }

    function traceHistoryMerge(records, uid, localOnly) {
        // A collector may install this opt-in observer; it never owns chat state.
        if (typeof window.__nekoChatSyncTrace !== 'function') return;
        records.forEach(function (record) {
            try {
                window.__nekoChatSyncTrace('ui_merge_requested', {
                    character_uid: uid, message_id: record.messageId,
                    local_message_id: (localOnly ? 'local-reply-' : 'sync-original-') + record.messageId
                });
            } catch (_) { /* Diagnostics must never prevent passive restoration. */ }
        });
    }

    async function api(path, method, body) {
        // Only the local backend handles OAuth; the browser never receives its bearer.
        var response = await fetch('/api/chat-sync' + path, {
            method: method || 'GET', cache: 'no-store',
            headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
            body: body === undefined ? undefined : JSON.stringify(body)
        });
        var result;
        try { result = await response.json(); }
        catch (_) { throw new Error('chat_sync_unavailable'); }
        if (!response.ok) throw new Error(result && typeof result.detail === 'string' ? result.detail : 'chat_sync_unavailable');
        return result;
    }

    function originalMessage(record, name, localOnly) {
        // Unknown source times stay explicitly unknown instead of becoming import time.
        var numeric = typeof record.originalTime === 'string' && /^\d+$/.test(record.originalTime)
            ? Number(record.originalTime) : NaN;
        var timestamp = Number.isSafeInteger(numeric) ? numeric : 0;
        var date = new Date(timestamp);
        var time = record.originalTime === null ? text('unknownTime')
            : timestamp && !Number.isNaN(date.getTime()) ? date.toLocaleString() : record.originalTime;
        var message = {
            id: (localOnly ? 'local-reply-' : 'sync-original-') + record.messageId,
            role: record.role,
            author: record.role === 'assistant' ? name : text('userLabel'),
            time: time, createdAt: timestamp, status: 'sent',
            blocks: [{ type: 'text', text: record.text }]
        };
        return message;
    }

    function mergeHistory(current, originals, previousKnown, mappings, name, turnMappings, localReplies) {
        // Identity is the only dedupe key; equal text/time never joins distinct messages.
        localReplies = localReplies || [];
        var received = new Set(originals.concat(localReplies).map(function (record) { return record.messageId; }));
        var live = new Map();
        current.forEach(function (message) {
            if (message.id.indexOf('sync-original-') === 0 || message.id.indexOf('local-reply-') === 0) return;
            var identity = mappings.get(message.id)
                || (turnMappings && message.role === 'assistant' && turnMappings.get(message.turnId));
            if (!identity) return;
            if (!live.has(identity)) live.set(identity, []);
            live.get(identity).push(message);
        });
        function restore(record, localOnly) {
            var canonical = originalMessage(record, name, localOnly);
            var displayed = live.get(record.messageId);
            if (!displayed) return [canonical];
            // Wire identity is independent of React keys. Keep the visible split
            // bubbles and blocks so passive sync cannot replay their entry animation.
            // Local-only/tool records stay local; this is presentation, not eligibility.
            return displayed.map(function (message) {
                // Retire cached transitional actions, preserving unrelated ones.
                var actions = (message.actions || []).filter(function (action) {
                    return action.action !== 'chat_sync_delete_original';
                });
                var next = Object.assign({}, message, { status: 'sent', actions: actions.length ? actions : undefined });
                if (typeof next.createdAt !== 'number' && canonical.createdAt) next.createdAt = canonical.createdAt;
                return next;
            });
        }
        var restored = originals.slice().sort(function (a, b) {
            var at = /^\d+$/.test(a.originalTime || '') ? Number(a.originalTime) : 0;
            var bt = /^\d+$/.test(b.originalTime || '') ? Number(b.originalTime) : 0;
            return at - bt || a.originStreamId.localeCompare(b.originStreamId) || a.originSeq - b.originSeq;
        }).flatMap(function (record) { return restore(record, false); });
        restored = restored.concat(localReplies.flatMap(function (record) { return restore(record, true); }));
        var retained = current.filter(function (message) {
            if (message.id.indexOf('sync-original-') === 0 || message.id.indexOf('local-reply-') === 0) return false;
            var originalId = mappings.get(message.id)
                || (turnMappings && message.role === 'assistant' && turnMappings.get(message.turnId));
            // Preserve live/uncommitted records until their exact source was stored.
            return !originalId || (!received.has(originalId) && !previousKnown.has(originalId));
        });
        // Local-only replies keep their actual place between restored originals.
        // Missing live timestamps remain at the end; equal times retain their order.
        return restored.concat(retained).sort(function (a, b) {
            var at = typeof a.createdAt === 'number' && Number.isFinite(a.createdAt) ? a.createdAt : Infinity;
            var bt = typeof b.createdAt === 'number' && Number.isFinite(b.createdAt) ? b.createdAt : Infinity;
            return at - bt;
        }).map(function (message, index) {
            return Object.assign({}, message, { sortKey: index });
        });
    }

    async function loadHistory(state, idleOnly) {
        // Latest-request wins also fences same-role pages overtaken by a delete.
        var generation = ++historyRequestGeneration;
        var name = characterName();
        if (!name) return;
        if (idleOnly && (document.visibilityState === 'hidden' || unfinishedChat())) return;
        state = state || await api('/status');
        var role = state.characters.find(function (item) { return item.displayName === name; });
        if (!role || characterName() !== name || generation !== historyRequestGeneration) return;
        activeUid = role.characterSyncId;
        activeName = name;
        var result = await api('/characters/' + activeUid + '/history');
        if (characterName() !== name || activeUid !== role.characterSyncId || generation !== historyRequestGeneration) return;
        // A live turn may start while local history I/O waits. Automatic refresh
        // defers without consuming its cursor so the next idle check can retry.
        if (idleOnly && (document.visibilityState === 'hidden' || unfinishedChat())) return;
        var host = window.reactChatWindowHost;
        if (!host || typeof host.getState !== 'function' || typeof host.setMessages !== 'function') return;
        var previous = new Set(knownIds.get(activeUid) || []);
        (deletedLocalIds.get(activeUid) || new Set()).forEach(function (id) { previous.add(id); });
        var localDeleted = deletedLocalIds.get(activeUid) || new Set();
        var localReplies = (result.localReplies || []).filter(function (record) { return !localDeleted.has(record.messageId); });
        if (!result.originals.length && !localReplies.length && !previous.size) {
            // An empty read is still a successful restoration of this exact cursor.
            if (role.binding) knownCursors.set(activeUid, role.binding.cursor);
            return true;
        }
        var merged = mergeHistory(host.getState().messages || [], result.originals, previous, liveIds, name, assistantTurns, localReplies);
        // setMessages is passive: appendMessage would enqueue message reactions.
        traceHistoryMerge(result.originals, activeUid);
        traceHistoryMerge(localReplies, activeUid, true);
        host.setMessages(merged);
        knownIds.set(activeUid, new Set(result.originals.map(function (record) { return record.messageId; })));
        if (role.binding) knownCursors.set(activeUid, role.binding.cursor);
        return true;
    }

    async function refreshDownloadedHistory() {
        // This local-only check does no cloud/auth work. Reuse passive restoration
        // only when a committed download cursor changes, retaining live bubbles.
        if (historyPollRunning || document.visibilityState === 'hidden' || unfinishedChat()) return;
        historyPollRunning = true;
        var hint = null;
        try {
            do {
                hint = pendingHistoryHint;
                pendingHistoryHint = null;
                var name = characterName();
                if (!name || (hint && hint.name !== name)) return;
                var state = await api('/status');
                if (name !== characterName()) return;
                var role = state.characters.find(function (item) { return item.displayName === name; });
                if (hint && (!role || hint.uid !== role.characterSyncId)) return;
                if (document.visibilityState === 'hidden' || unfinishedChat()) {
                    pendingHistoryHint = pendingHistoryHint || hint;
                    return;
                }
                if (role && role.binding && (!knownCursors.has(role.characterSyncId)
                        || knownCursors.get(role.characterSyncId) !== role.binding.cursor)) {
                    if (!await loadHistory(state, true)) {
                        // Do not consume a hint if a live turn began during I/O.
                        if (name === characterName()) pendingHistoryHint = pendingHistoryHint || hint;
                        return;
                    }
                }
                hint = null;
            } while (pendingHistoryHint && document.visibilityState !== 'hidden' && !unfinishedChat());
        } catch (error) {
            pendingHistoryHint = pendingHistoryHint || hint;
            throw error;
        } finally {
            historyPollRunning = false;
        }
    }

    function notifyHistoryCommitted(payload) {
        // This socket hint contains no progress or content. Read the local
        // authoritative cursor and history under the existing role/I/O fences.
        if (!payload || payload.type !== 'chat_sync_history_changed' || typeof payload.characterSyncId !== 'string'
                || !ID.test(payload.characterSyncId)
                || Object.keys(payload).length !== 2) return Promise.resolve();
        if (activeName === characterName() && activeUid !== payload.characterSyncId) return Promise.resolve();
        pendingHistoryHint = { uid: payload.characterSyncId, name: characterName() };
        return refreshDownloadedHistory();
    }

    function retryPendingHistory() {
        // Lifecycle events can fire before their producer clears pending state.
        // Retry after that synchronous cleanup; real rendering/streaming still wins.
        return Promise.resolve().then(function () {
            if (pendingHistoryHint) return refreshDownloadedHistory();
        }).catch(function () { /* The local poll retries after offline/window failures. */ });
    }

    function startHistoryPoll() {
        // BFCache restoration reuses this page instead of firing DOMContentLoaded.
        if (historyPollTimer !== null || !document.getElementById('react-chat-window-root')) return;
        historyPollTimer = window.setInterval(function () {
            return refreshDownloadedHistory().catch(function () { /* Local chat remains available offline. */ });
        }, 5000);
    }

    function unfinishedChat() {
        // Reuse actual live host/socket states rather than creating a sync busy flag.
        var host = window.reactChatWindowHost;
        var state = window.appState || {};
        // A cleared current lifecycle cannot borrow a previous role's fallback ID.
        var turnId = Object.prototype.hasOwnProperty.call(state, 'assistantTurnId')
            ? state.assistantTurnId : window._nekoAssistantTurnId;
        var messages = host && typeof host.getState === 'function' ? host.getState().messages || [] : [];
        // Completion may precede this script's listener. Only exact lifecycle
        // identities release delayed tail bubbles, never unknown streaming work.
        if (state.assistantTurnCompletedId) completedTurns.add(state.assistantTurnCompletedId);
        if (state.assistantTurnSettledId) completedTurns.add(state.assistantTurnSettledId);
        // Completed text and settled voice retain their turn ID until the next
        // user input. Pending submissions and real rendering still block merge.
        return !!(turnId && turnId !== state.assistantTurnCompletedId && turnId !== state.assistantTurnSettledId)
            || !!state.assistantTurnAwaitingBubble || !!window._lastSubmittedRequestId
            || !!(state.pendingTextTurnSubmitAt && Date.now() - state.pendingTextTurnSubmitAt < 15000)
            || !!window._isProcessingRealisticQueue || messages.some(function (message) {
            return message.status === 'sending' || (message.status === 'streaming'
                && !(message.role === 'assistant' && message.turnId && completedTurns.has(message.turnId)));
        });
    }

    function rememberDeletion(uid, receipt) {
        // A just-saved live bubble may not have appeared in a previous history page.
        if (uid === activeUid && activeName === characterName()) historyRequestGeneration++;
        var known = new Set(knownIds.get(uid) || []);
        receipt.messageIds.forEach(function (id) { if (ID.test(id)) known.add(id); });
        knownIds.set(uid, known);
        var localDeleted = new Set(deletedLocalIds.get(uid) || []);
        (receipt.localMessageIds || []).forEach(function (id) { if (ID.test(id)) localDeleted.add(id); });
        deletedLocalIds.set(uid, localDeleted);
        // Exact local hiding must survive a temporarily unavailable witness;
        // fetching history can fail while the ordinary current chat stays visible.
        var host = window.reactChatWindowHost;
        if (uid === activeUid && host && typeof host.getState === 'function' && typeof host.setMessages === 'function') {
            var deleted = new Set(receipt.messageIds);
            localDeleted.forEach(function (id) { deleted.add(id); });
            host.setMessages((host.getState().messages || []).filter(function (message) {
                var identity = message.id.indexOf('sync-original-') === 0 ? message.id.slice('sync-original-'.length)
                    : message.id.indexOf('local-reply-') === 0 ? message.id.slice('local-reply-'.length)
                    : liveIds.get(message.id) || (message.role === 'assistant' && assistantTurns.get(message.turnId));
                return !deleted.has(identity);
            }));
        }
    }

    function clearHistoryPreview() {
        // Dismissed consent never survives a role switch or a later local response.
        historyConsentGeneration++;
        historyPreview = null;
        if (historyPreviewNode) historyPreviewNode.replaceChildren();
    }

    function updateControls() {
        dialog.querySelectorAll('[data-sync-action]').forEach(function (button) { button.disabled = operationRunning; });
        dialog.querySelectorAll('[data-history-action]').forEach(function (button) { button.disabled = operationRunning; });
        ['sync', 'pause', 'history'].forEach(function (action) {
            dialog.querySelector('[data-sync-action="' + action + '"]').disabled = operationRunning || !activeBinding || !activeBinding.enabled;
        });
        dialog.querySelector('[data-sync-action="clear"]').disabled = operationRunning || !activeUid || unfinishedChat();
        dialog.querySelector('[data-sync-action="enable"]').disabled = operationRunning || writerRecoveryRequired || !activeUid;
        dialog.querySelector('[data-sync-action="recover"]').disabled = operationRunning || !writerRecoveryRequired || !activeUid;
        dialog.querySelector('[data-sync-action="enable"]').hidden = !!(activeBinding && activeBinding.enabled) || writerRecoveryRequired;
        dialog.querySelector('[data-sync-action="sync"]').hidden = !activeBinding || !activeBinding.enabled || writerRecoveryRequired;
        dialog.querySelector('[data-sync-action="pause"]').hidden = !activeBinding || !activeBinding.enabled || writerRecoveryRequired;
        dialog.querySelector('[data-sync-action="recover"]').hidden = !writerRecoveryRequired;
        if (writerRecoveryRequired) {
            ['sync', 'pause', 'history', 'profile', 'cloud'].forEach(function (action) {
                dialog.querySelector('[data-sync-action="' + action + '"]').disabled = true;
            });
        }
    }

    function showHistoryPreview(snapshot, name, bindingKey) {
        historyPreview = { snapshot: snapshot, name: name, bindingKey: bindingKey, generation: historyConsentGeneration };
        historyPreviewNode.replaceChildren();
        [['historyPreview'], ['historyLocalRole', { name: snapshot.localName }],
            ['historyCloudRole', { name: snapshot.cloudName }], ['historyCount', { count: snapshot.count }],
            [snapshot.unverifiedCount === null ? 'historyUnverifiedUnknown' : 'historyUnverifiedCount', { count: snapshot.unverifiedCount }],
            ['historyHint']].forEach(function (item) {
            var line = document.createElement('p');
            line.textContent = text(item[0], item[1]);
            historyPreviewNode.appendChild(line);
        });
        if (!snapshot.count) {
            var empty = document.createElement('p');
            empty.textContent = text('historyEmpty');
            historyPreviewNode.appendChild(empty);
        }
        (snapshot.count ? [['approve', 'historyApprove'], ['cancel', 'historyCancel']] : [['cancel', 'historyCancel']]).forEach(function (item) {
            var button = document.createElement('button');
            button.type = 'button';
            button.dataset.historyAction = item[0];
            button.textContent = text(item[1]);
            button.addEventListener('click', function () {
                if (item[0] === 'cancel') clearHistoryPreview();
                else return operateHistory('approve');
            });
            historyPreviewNode.appendChild(button);
        });
    }

    async function operateHistory(action) {
        if (operationRunning || !dialog.open) return;
        var name = characterName(), uid = activeUid, reviewed = historyPreview;
        if (action === 'approve' && (!reviewed || reviewed.name !== name)) {
            clearHistoryPreview();
            statusNode.textContent = text('historyChanged');
            return;
        }
        operationRunning = true;
        updateControls();
        try {
            if (!await refreshStatus() || characterName() !== name || activeUid !== uid || !activeBinding || !activeBinding.enabled
                    || !dialog.open || (reviewed && (reviewed.bindingKey !== activeBindingKey
                        || reviewed.generation !== historyConsentGeneration))) throw new Error('chat_sync_history_changed');
            statusNode.textContent = text('working');
            if (action === 'approve') {
                // Approve this exact local task; background scheduling owns registration and uploads.
                await api('/characters/' + uid + '/history/approve', 'POST', { taskId: reviewed.snapshot.taskId });
                if (!dialog.open || name !== characterName() || reviewed.generation !== historyConsentGeneration) return;
                clearHistoryPreview();
                await refreshStatus();
                if (dialog.open && name === characterName()) statusNode.textContent += ' ' + text('historyQueued');
            } else {
                clearHistoryPreview();
                var generation = historyConsentGeneration, bindingKey = activeBindingKey;
                var snapshot = await api('/characters/' + uid + '/history/preview', 'POST');
                if (!dialog.open || generation !== historyConsentGeneration || name !== characterName()
                        || uid !== activeUid || bindingKey !== activeBindingKey) return;
                if (snapshot.characterSyncId !== uid || snapshot.localName !== name) throw new Error('chat_sync_history_changed');
                showHistoryPreview(snapshot, name, bindingKey);
                statusNode.textContent = '';
            }
        } catch (error) {
            clearHistoryPreview();
            if (dialog.open && name === characterName()) statusNode.textContent = text(error.message === 'chat_sync_login_required'
                ? 'loginRequired' : ['chat_sync_history_changed', 'chat_sync_session_changed', 'chat_sync_binding_changed'].includes(error.message)
                    ? 'historyChanged' : 'failed');
        } finally {
            operationRunning = false;
            updateControls();
        }
    }

    async function refreshStatus() {
        // Show remaining local work instead of announcing complete two-device delivery.
        var name = characterName();
        var state = await api('/status');
        if (characterName() !== name) return false;
        var role = state.characters.find(function (item) { return item.displayName === name; });
        activeUid = role ? role.characterSyncId : null;
        configuredServiceUrl = state.serviceUrl;
        var binding = role && role.binding;
        activeBinding = binding;
        writerRecoveryRequired = !!(state.writerRecoveryRequired || (binding && binding.writerRecoveryRequired));
        activeBindingKey = binding ? JSON.stringify([activeUid, state.serviceUrl, binding.ownerId, binding.bindingId, binding.generation, binding.enabled]) : null;
        if (historyPreview && (historyPreview.name !== name || historyPreview.bindingKey !== activeBindingKey)) clearHistoryPreview();
        statusNode.textContent = !binding ? text('enableHint', { name: name })
            : text(binding.enabled ? 'enabled' : 'paused') + ' ' + text('pending', {
                originals: binding.pendingOriginals, deletions: binding.pendingDeletions
            }) + (binding.localOnlyOriginals ? ' ' + text('oversizedLocalOnly', { count: binding.localOnlyOriginals }) : '');
        if (writerRecoveryRequired) statusNode.textContent = text('writerRecoveryRequired');
        else if (role && role.syncSetupError) statusNode.textContent = text('setupNeedsSave', {name: name});
        else if (role && role.syncPending) statusNode.textContent = text('newSyncPending');
        // Automatic failures use existing localized progress labels; remote
        // bodies and internal retry metadata never become visible text.
        if (binding && binding.lastSyncError) statusNode.textContent += ' ' + text(
            binding.lastSyncError === 'chat_sync_login_required' ? 'loginRequired' : 'failed');
        var backfill = binding && binding.historyBackfill;
        if (backfill && backfill.awaitingRegistration > 0) {
            // Aggregate approved tasks independently: a newer preview must not hide earlier pending members.
            statusNode.textContent += ' ' + text('historyAwaitingRegistration', { count: backfill.awaitingRegistration });
        }
        updateControls();
        return true;
    }

    async function operate(action) {
        // UI operations change only this feature's state; normal chat remains independent.
        if (action === 'history') return operateHistory('preview');
        if (operationRunning) return;
        operationRunning = true;
        updateControls();
        var requestedName = characterName();
        var requestedUid = activeUid;
        var requestedBindingKey = activeBindingKey;
        var requestedServiceUrl = configuredServiceUrl;
        var enabledHere = false;
        try {
            if (!await refreshStatus() || characterName() !== requestedName
                    || (requestedUid && activeUid !== requestedUid)) return;
            requestedUid = activeUid;
            // Missing configuration already means the built-in project service.
            if (action === 'enable' && (requestedBindingKey !== activeBindingKey
                    || (requestedServiceUrl || DEFAULT_SERVICE_URL) !== (configuredServiceUrl || DEFAULT_SERVICE_URL))) return;
            statusNode.textContent = text('working');
            if (['enable', 'pause', 'clear'].includes(action)) clearHistoryPreview();
            var consentGeneration = historyConsentGeneration;
            if (action === 'cloud' && window.nekoCharacterProfileSync) {
                // Cloud import is independent of whichever local role is active.
                await window.nekoCharacterProfileSync.cloudImport();
                return;
            } else if (requestedUid) {
                var path = '/characters/' + requestedUid;
                if (action === 'recover') {
                    clearHistoryPreview();
                    var recovered = await api(path + '/recovery/preview', 'POST', { serviceUrl: (configuredServiceUrl || DEFAULT_SERVICE_URL) });
                    if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
                    await refreshStatus();
                    // Deletion consent is separate from the existing reviewed
                    // history task, and cancellation keeps these exact IDs hidden.
                    if (recovered.deletions.count && window.confirm(text('recoveryDeleteConfirm', {
                            count: recovered.deletions.count, localName: recovered.history.localName,
                            cloudName: recovered.history.cloudName
                        }))) {
                        await api(path + '/recovery/deletions/approve', 'POST', { taskId: recovered.deletions.taskId });
                    }
                    if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
                    showHistoryPreview(recovered.history, requestedName, activeBindingKey);
                    statusNode.textContent = text('recoveryChecked');
                    await loadHistory();
                    return;
                }
                if (action === 'enable') {
                    if (!configuredServiceUrl) await api('/configuration', 'PUT', { serviceUrl: (configuredServiceUrl || DEFAULT_SERVICE_URL) });
                    if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid
                            || consentGeneration !== historyConsentGeneration) return;
                    // One labeled enable/resume action durably approves the saved persona and trusted history.
                    // The backend owns retries; opening this dialog never enlarges an older grant.
                    await api(path + '/sync-choice', 'POST', {enabled: true, includeHistory: true});
                    enabledHere = true;
                    // This explicit action may target an edited, inactive role. Run its saved intent
                    // once without changing the selected chat; offline card saves still only queue.
                    await api(path + '/sync-choice/run', 'POST');
                }
                if (action === 'pause') await api(path + '/pause', 'POST');
                if (action === 'clear') {
                    if (unfinishedChat()) { statusNode.textContent = text('unfinishedChat'); return; }
                    if (!window.confirm(text('clearConfirm'))) return;
                    rememberDeletion(requestedUid, await api(path + '/clear', 'POST'));
                }
                if (action === 'sync') {
                    // Internal manual-pass budget only; not a production/compressed cap.
                    var receipt = await api(path + '/sync', 'POST', { byteBudget: 256 * 1024, maxPullPages: 10 });
                    await refreshStatus();
                    statusNode.textContent += ' ' + text(receipt.hasMore ? 'more' : 'passFinished');
                    return;
                }
                if (action === 'profile' && window.nekoCharacterProfileSync) {
                    // A dismissed conflict remains reviewable without another edit.
                    var candidate = await api(path + '/profile/local', 'POST', {});
                    if (candidate.head) await window.nekoCharacterProfileSync.review(requestedUid, candidate);
                    else await window.nekoCharacterProfileSync.afterSave(requestedName, true);
                    return;
                }
            }
            await refreshStatus();
            await loadHistory();
        } catch (error) {
            statusNode.textContent = text(error.message === 'chat_sync_login_required' ? 'loginRequired'
                : enabledHere ? 'initialHistoryFailed' : 'failed');
        } finally {
            if (action === 'sync') {
                // Earlier pages can commit even when a later network page fails.
                try { await loadHistory(); } catch (_) { /* Keep the visible sync error. */ }
            }
            operationRunning = false;
            updateControls();
        }
    }

    function buildDialog() {
        // Build DOM nodes without interpolating profile names or service URLs as HTML.
        dialog = document.createElement('dialog');
        dialog.id = 'chat-sync-dialog';
        dialog.setAttribute('aria-labelledby', 'chat-sync-title');
        var title = document.createElement('h2');
        title.id = 'chat-sync-title';
        title.textContent = text('title');
        dialog.appendChild(title);
        var advanced = document.createElement('details');
        var summary = document.createElement('summary');
        summary.textContent = text('moreOptions');
        advanced.appendChild(summary);
        statusNode = document.createElement('p');
        statusNode.setAttribute('role', 'status');
        dialog.appendChild(statusNode);
        [['recover', 'recoverWriter'], ['enable', 'enable'], ['pause', 'pause'], ['sync', 'syncNow'], ['history', 'previewHistory'], ['profile', 'profileReview'], ['cloud', 'cloudImport'], ['clear', 'clearKnown']].forEach(function (item) {
            var button = document.createElement('button');
            button.type = 'button';
            button.dataset.syncAction = item[0];
            button.textContent = text(item[1]);
            button.addEventListener('click', function () { return operate(item[0]); });
            (['enable', 'pause', 'sync', 'recover'].includes(item[0]) ? dialog : advanced).appendChild(button);
        });
        dialog.appendChild(advanced);
        historyPreviewNode = document.createElement('div');
        historyPreviewNode.setAttribute('role', 'group');
        historyPreviewNode.setAttribute('aria-label', text('historyPreview'));
        dialog.appendChild(historyPreviewNode);
        var close = document.createElement('button');
        close.type = 'button';
        close.textContent = text('close');
        close.addEventListener('click', function () { clearHistoryPreview(); dialog.close(); });
        dialog.addEventListener('cancel', clearHistoryPreview);
        dialog.addEventListener('close', function () { clearHistoryPreview(); settingsCharacterName = null; });
        dialog.appendChild(close);
        document.body.appendChild(dialog);
    }

    window.nekoChatSync = {
        openSettings: async function (name) {
            settingsCharacterName = name || null;
            if (!dialog) buildDialog();
            dialog.showModal();
            try { await refreshStatus(); } catch (_) { statusNode.textContent = text('failed'); }
        },
        // Pure identity merge is exposed for direct browser-independent regression tests.
        mergeHistory: mergeHistory,
        loadHistory: loadHistory,
        notifyHistoryCommitted: notifyHistoryCommitted,
        markUserRequest: function (requestId, localBubbleId) {
            // This mapping is display-only and does not authorize backend identities.
            if (requestId && localBubbleId) userRequests.set(requestId, localBubbleId);
        },
        markLiveOriginal: function (messageId, localBubbleId) {
            // Only backend-generated fixed IDs are accepted from the current socket.
            if (typeof messageId === 'string' && ID.test(messageId) && localBubbleId) liveIds.set(localBubbleId, messageId);
        },
        markAcceptedUser: function (payload) {
            // Optimistic text bubbles are joined only through a matching request ID.
            var localId = userRequests.get(payload.request_id);
            if (localId) this.markLiveOriginal(payload.message_id, localId);
        },
        markAssistantOriginal: function (messageId, turnId) {
            // Realistic rendering can build bubbles later; its explicit turn ID stays stable.
            if (typeof messageId === 'string' && ID.test(messageId) && turnId) assistantTurns.set(turnId, messageId);
        }
    };

    function memoryBrowserDeletion(data) {
        var receipt = data && data.receipt;
        if (!data || data.action !== 'chat_sync_deleted' || !ID.test(data.characterSyncId || '') || !receipt
                || !Array.isArray(receipt.messageIds) || !receipt.messageIds.every(function (id) { return ID.test(id); })
                || !Array.isArray(receipt.localMessageIds) || !receipt.localMessageIds.every(function (id) { return ID.test(id); })) return;
        rememberDeletion(data.characterSyncId, receipt);
        loadHistory().catch(function () { /* Durable tombstones already hide exact IDs. */ });
    }
    if (typeof window.BroadcastChannel === 'function') {
        var memoryDeletionChannel = new window.BroadcastChannel('neko_page_channel');
        memoryDeletionChannel.addEventListener('message', function (event) { memoryBrowserDeletion(event.data); });
    }
    window.addEventListener('message', function (event) {
        if (event.origin === window.location.origin) memoryBrowserDeletion(event.data);
    });

    window.addEventListener('neko-assistant-turn-end', function (event) {
        // The realistic queue can create streaming tail bubbles after turn_end.
        // Keep earlier completions when the next turn replaces appState's ID.
        var turnId = event.detail && event.detail.turnId;
        if (typeof turnId === 'string' && turnId) completedTurns.add(turnId);
        retryPendingHistory();
    });
    window.addEventListener('neko-assistant-speech-end', retryPendingHistory);
    window.addEventListener('neko-realistic-queue-idle', retryPendingHistory);

    window.addEventListener('voice-input-socket-open', function () {
        // Initial connect and role switches restore local originals without cloud I/O.
        clearHistoryPreview();
        loadHistory().catch(function () { /* Optional sync never blocks local chat. */ });
    });

    document.addEventListener('DOMContentLoaded', function () {
        // Notifications wake backend downloads. This cheap local cursor check
        // belongs to the chat host, independently of optional settings controls.
        startHistoryPoll();
        var button = document.getElementById('chatSyncButton');
        if (!button) return;
        button.addEventListener('click', async function () {
            await window.nekoChatSync.openSettings();
        });
    });

    window.addEventListener('pagehide', function () {
        // A closed chat page owns no timer; the backend scheduler continues.
        if (historyPollTimer !== null) window.clearInterval(historyPollTimer);
        historyPollTimer = null;
    });
    async function foregroundCatchup() {
        // The local bridge owns credentials and all enabled roles. A foreground
        // wake does not perform manual sync or bypass durable retry fences.
        if (foregroundWakeRunning || document.visibilityState === 'hidden' || !document.getElementById('react-chat-window-root')) return;
        foregroundWakeRunning = true;
        try { await api('/wake', 'POST'); } catch (_) { /* Local chat stays usable. */ }
        finally { foregroundWakeRunning = false; }
        await refreshDownloadedHistory().catch(function () { /* Retry on the next local tick. */ });
    }
    document.addEventListener('visibilitychange', foregroundCatchup);
    window.addEventListener('pageshow', function () { startHistoryPoll(); return foregroundCatchup(); });
})();
