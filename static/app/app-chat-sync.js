/* Opt-in sync controls and foreground wake; chat history stays with the chat host. */
(function () {
    'use strict';
    var activeUid = null;
    var modelFlight = null;
    var dialog = null;
    var settingsCharacterName = null;
    var statusNode = null;
    var operationRunning = false;
    var writerRecoveryRequired = false;
    var foregroundWakeRunning = false;

    function text(key, values) {
        // All visible labels use the same eight-locale namespace.
        return window.t ? window.t('chatSync.' + key, values || {}) : key;
    }

    function describeError(error) {
        // Only fixed adapter codes reach diagnostics; arbitrary errors may contain credentials.
        var messages = {
            chat_sync_account_mismatch: 'errorAccountMismatch',
            chat_sync_writer_recovery_required: 'errorWriterRecovery',
            chat_sync_login_required: 'loginRequired',
            chat_sync_session_changed: 'errorSessionChanged',
            chat_sync_session_unavailable: 'errorSessionUnavailable',
            chat_sync_unavailable: 'errorNetwork',
            chat_sync_pull_timeout: 'errorNetwork',
            long_term_memory_capacity_exceeded: 'errorLongTermCapacity',
            long_term_memory_incoming_pending: 'errorLongTermPending',
            long_term_archive_subject_unavailable: 'errorArchiveSubject',
            chat_sync_retry_later: 'errorRetryLater',
            chat_sync_service_rejected: 'errorServiceRejected',
            chat_sync_binding_changed: 'errorBindingChanged',
            chat_sync_not_enabled: 'profileNeedsBinding',
            chat_sync_character_not_found: 'errorBindingChanged',
            character_profile_changed: 'errorProfileChanged',
            character_profile_name_conflict: 'cloudNameConflict',
            character_profile_unsupported_value: 'profileUnsupported',
            character_profile_conflict: 'profileConflict',
            profile_revision_conflict: 'profileConflict'
        };
        var code = typeof error === 'string' ? error : error && error.message;
        if (!Object.prototype.hasOwnProperty.call(messages, code)) return text('errorUnknown');
        return text('errorDetail', { message: text(messages[code]), code: code });
    }

    function characterName() {
        // Resolve the current local profile, then use its persisted UID from the API.
        return settingsCharacterName || window.lanlan_config && window.lanlan_config.lanlan_name || window._currentCatgirl || '';
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

    function updateControls() {
        var recover = dialog.querySelector('[data-sync-action="recover"]');
        recover.disabled = operationRunning || !writerRecoveryRequired || !activeUid;
        recover.setAttribute('aria-busy', String(operationRunning));
    }

    async function refreshStatus() {
        var name = characterName();
        var state = await api('/status');
        if (characterName() !== name) return false;
        var role = state.characters.find(function (item) { return item.displayName === name; });
        activeUid = role ? role.characterSyncId : null;
        var binding = role && role.binding;
        writerRecoveryRequired = !!(state.writerRecoveryRequired || (binding && binding.writerRecoveryRequired));
        var errors = [role && role.syncSetupError, binding && binding.lastSyncError]
            .concat(binding && binding.localMemoryDiagnostics || []).filter(Boolean);
        statusNode.textContent = errors.length ? Array.from(new Set(errors)).map(describeError).join('\n')
            : text(writerRecoveryRequired ? 'writerRecoveryRequired' : 'recoveryNotRequired');
        if (binding) {
            if (binding.pendingOriginals || binding.pendingDeletions) {
                statusNode.textContent += '\n' + text('pending', {
                    originals: binding.pendingOriginals || 0, deletions: binding.pendingDeletions || 0
                });
            }
        }
        updateControls();
        var modelPanel = dialog.querySelector('[data-sync-model]');
        if (modelPanel) {
            modelPanel.replaceChildren();
            if (activeUid && binding && binding.enabled) {
                var modelUid = activeUid, modelName = characterName();
                var modelStatus = await api('/characters/' + modelUid + '/model-sync').catch(function () { return {}; });
                if (!dialog.open || activeUid !== modelUid || characterName() !== modelName) return false;
                function modelButton(key, action) {
                    var button = document.createElement('button');
                    button.type = 'button';
                    button.textContent = window.t('cloudModel.' + key);
                    button.addEventListener('click', async function () {
                        if (!dialog.open || activeUid !== modelUid || characterName() !== modelName) return;
                        button.disabled = true;
                        try { await api('/characters/' + modelUid + '/model-sync-action', 'POST', { action: action }); await refreshStatus(); }
                        catch (error) {
                            if (dialog.open && activeUid === modelUid && characterName() === modelName) {
                                statusNode.textContent = describeError(error);
                            }
                        }
                        finally { button.disabled = false; }
                    });
                    modelPanel.appendChild(button);
                }
                if (modelStatus.conflict) {
                    var description = document.createElement('p'); description.textContent = window.t('cloudModel.conflict'); modelPanel.appendChild(description);
                    modelButton('useCloud', 'cloud'); modelButton('keepLocal', 'local');
                } else if (modelStatus.failed) {
                    var failure = document.createElement('p'); failure.textContent = window.t('cloudModel.failed'); modelPanel.appendChild(failure);
                    modelButton('retry', 'retry');
                }
            }
        }
        return true;
    }

    async function recoverSync() {
        if (operationRunning || !dialog.open || !writerRecoveryRequired || !activeUid) return;
        var requestedName = characterName(), requestedUid = activeUid;
        operationRunning = true;
        updateControls();
        try {
            if (!await refreshStatus() || !dialog.open || characterName() !== requestedName
                    || activeUid !== requestedUid || !writerRecoveryRequired) return;
            statusNode.textContent = text('working');
            var path = '/characters/' + requestedUid;
            var recovered = await api(path + '/recovery/preview', 'POST');
            if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
            await refreshStatus();
            if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
            statusNode.textContent = text('recoveryChecked');
            // Earlier local deletions that never reached the cloud stay local only.
            var kept = recovered.deletions.count;
            if (kept) statusNode.textContent += '\n' + text('recoveryCloudCopiesKept', { count: kept });
        } catch (error) {
            if (dialog.open && characterName() === requestedName && activeUid === requestedUid) {
                statusNode.textContent = describeError(error);
            }
        } finally {
            operationRunning = false;
            updateControls();
        }
    }

    function buildDialog() {
        dialog = document.createElement('dialog');
        dialog.id = 'chat-sync-dialog';
        dialog.setAttribute('aria-labelledby', 'chat-sync-title');
        var header = document.createElement('div');
        header.className = 'chat-sync-dialog-header';
        var icon = document.createElement('img');
        icon.src = '/static/icons/set_on.png';
        icon.alt = '';
        header.appendChild(icon);
        var title = document.createElement('h2');
        title.id = 'chat-sync-title';
        title.textContent = text('title');
        header.appendChild(title);
        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'chat-sync-close';
        close.setAttribute('aria-label', text('close'));
        var closeIcon = document.createElement('img');
        closeIcon.src = '/static/icons/close_button.png';
        closeIcon.alt = '';
        close.appendChild(closeIcon);
        close.addEventListener('click', function () { dialog.close(); });
        header.appendChild(close);
        dialog.appendChild(header);
        var row = document.createElement('div');
        row.className = 'chat-sync-recovery-row';
        var recover = document.createElement('button');
        recover.type = 'button';
        recover.className = 'chat-sync-recover';
        recover.dataset.syncAction = 'recover';
        recover.textContent = text('recoverWriter');
        recover.disabled = true;
        recover.setAttribute('aria-describedby', 'chat-sync-recovery-tooltip');
        recover.addEventListener('click', recoverSync);
        row.appendChild(recover);
        var help = document.createElement('span');
        help.className = 'chat-sync-help';
        var helpButton = document.createElement('button');
        helpButton.type = 'button';
        helpButton.className = 'chat-sync-help-button';
        helpButton.textContent = '?';
        helpButton.setAttribute('aria-label', text('recoverWriterHelp'));
        helpButton.setAttribute('aria-describedby', 'chat-sync-recovery-tooltip');
        help.appendChild(helpButton);
        var tooltip = document.createElement('span');
        tooltip.id = 'chat-sync-recovery-tooltip';
        tooltip.setAttribute('role', 'tooltip');
        tooltip.textContent = text('recoverWriterTooltip');
        help.appendChild(tooltip);
        row.appendChild(help);
        dialog.appendChild(row);
        statusNode = document.createElement('p');
        statusNode.className = 'chat-sync-status';
        statusNode.setAttribute('role', 'status');
        dialog.appendChild(statusNode);
        var modelPanel = document.createElement('div');
        modelPanel.dataset.syncModel = '';
        dialog.appendChild(modelPanel);
        dialog.addEventListener('close', function () { settingsCharacterName = null; });
        document.body.appendChild(dialog);
    }

    window.nekoChatSync = {
        openSettings: async function (name) {
            settingsCharacterName = name || null;
            if (!dialog) buildDialog();
            dialog.showModal();
            statusNode.textContent = text('working');
            dialog.querySelector('[data-sync-action="recover"]').disabled = true;
            try { await refreshStatus(); } catch (error) { statusNode.textContent = describeError(error); }
        },
        describeError: describeError,
        notifyMediaChanged: refreshReadyModel,
        requestMemoryRefresh: requestMemoryRefresh
    };

    async function applyReadyModel(uid, name) {
        if (modelFlight || window.appState?.isSwitchingCatgirl || !document.getElementById('live2d-container') || typeof window.handleModelReload !== 'function') return;
        var switchAttempt = window.appState?._switchAttemptCounter || 0;
        function isCurrentRole() {
            return characterName() === name && !window.appState?.isSwitchingCatgirl &&
                (window.appState?._switchAttemptCounter || 0) === switchAttempt;
        }
        modelFlight = true;
        var candidate;
        var failed = false;
        var loaded = false;
        try {
            var value;
            try { value = await api('/characters/' + uid + '/model-sync'); }
            catch (_) { return; }
            candidate = value && value.candidate;
            if (!candidate || !isCurrentRole() || candidate.name !== name) return;
            await window.handleModelReload(name, { temporaryConfig: {
                model_type: 'live2d', live3d_sub_type: '', model_path: candidate.path
            }, suppressToast: true, throwOnError: true, preservePreviousUntilLoaded: true });
            if (!isCurrentRole()) return;
            var manager = window.live2dManager;
            loaded = !!(manager && manager._lastLoadedModelPath === candidate.path && manager._modelLoadState === 'ready'
                && typeof manager.getCurrentModel === 'function' && manager.getCurrentModel());
            var receipt = await api('/characters/' + uid + '/model-sync/' + candidate.token, 'POST', { loaded: loaded });
            if (!receipt.applied && isCurrentRole()) {
                failed = true;
                await window.handleModelReload(name, { suppressToast: true });
            }
        } catch (_) {
            failed = true;
            if (isCurrentRole()) {
                if (!loaded) await api('/characters/' + uid + '/model-sync/' + candidate.token, 'POST', { loaded: false }).catch(function () {});
                if (isCurrentRole()) await window.handleModelReload(name, { suppressToast: true }).catch(function () {});
            }
        } finally {
            modelFlight = null;
            if (failed && isCurrentRole() && typeof window.showStatusToast === 'function') {
                window.showStatusToast(window.t ? window.t('app.modelSwitchFailed') : 'Model switch failed', 3000);
            }
        }
    }

    async function refreshReadyModel() {
        if (!window.live2dManager || typeof window.handleModelReload !== 'function') return;
        var name = characterName();
        var state = await api('/status');
        var role = state.characters.find(function (item) { return item.displayName === name; });
        if (role && characterName() === name) await applyReadyModel(role.characterSyncId, name);
    }

    var pendingMemoryName = null;
    var memoryRefreshFlight = false;
    var displayFlight = false;
    var displayPending = false;
    var displayPrefix = 'sync-recent-';
    var displayReceiptUid = null;
    var displayReceiptOrder = new Map();

    function liveCharacterName() {
        return window.lanlan_config && window.lanlan_config.lanlan_name || window._currentCatgirl || '';
    }

    function liveChatBusy(host) {
        var state = window.appState || {};
        var completed = new Set([state.assistantTurnCompletedId, state.assistantTurnSettledId]);
        return !!state.assistantTurnAwaitingBubble || !!window._isProcessingRealisticQueue
            || !!state.isPlaying || !!(window.NekoSpeechPlaybackState && window.NekoSpeechPlaybackState.active)
            || !!(state.assistantTurnId && !completed.has(state.assistantTurnId))
            || !!(state.isRecording && state.userRecentSpeechTime && Date.now() - state.userRecentSpeechTime < 700)
            || (state.pendingTextTurnSubmitAt && Date.now() - state.pendingTextTurnSubmitAt < 15000)
            || ((host && host.getState().messages) || []).some(function (message) {
                return message.status === 'sending' || (message.status === 'streaming' && !completed.has(message.turnId));
            });
    }

    function recentMessage(record, name, uid) {
        var body = record.role === 'assistant'
            ? record.text.replace(/^\[\d{8} [^\]\s]+ \d{2}:\d{2}\][ \t]*/, '') : record.text;
        var blocks = body ? [{ type: 'text', text: body }] : [];
        if (!blocks.length) return null;
        var timestamp = record.originalTime === null ? null : Number(record.originalTime);
        if (!Number.isSafeInteger(timestamp)) timestamp = null;
        return { id: displayPrefix + uid + '-' + record.messageId, role: record.role,
            author: record.role === 'assistant' ? name : text('userLabel'),
            time: timestamp === null ? text('unknownTime') : new Date(timestamp).toLocaleString(),
            createdAt: timestamp === null ? 0 : timestamp, status: 'sent', blocks: blocks };
    }

    function displaySnapshot(messages) {
        // Array order captures display ordering; append sort keys are internal bookkeeping.
        return JSON.stringify(messages.map(function (message) {
            return [message.id, message.role, message.author, message.time, message.createdAt,
                message.status, message.blocks];
        }));
    }

    async function refreshRecentDisplay() {
        var host = window.reactChatWindowHost;
        if (!host || typeof host.getState !== 'function' || typeof host.setMessages !== 'function'
                || window.appState?.isSwitchingCatgirl || document.visibilityState === 'hidden' || liveChatBusy(host)) return;
        if (displayFlight) { displayPending = true; return; }
        displayFlight = true;
        try {
            var name = liveCharacterName();
            var switchAttempt = window.appState?._switchAttemptCounter || 0;
            function isCurrentRole() {
                return liveCharacterName() === name && !window.appState?.isSwitchingCatgirl &&
                    (window.appState?._switchAttemptCounter || 0) === switchAttempt;
            }
            var state = await api('/status');
            var role = (state.characters || []).find(function (item) { return item.displayName === name; });
            if (!role || !role.binding || !role.binding.enabled || !isCurrentRole()) return;
            var uid = role.characterSyncId;
            var result = await api('/characters/' + uid + '/recent-display');
            if (!isCurrentRole() || window.reactChatWindowHost !== host || liveChatBusy(host)) return;
            var received = result.originals.map(function (record) { return recentMessage(record, name, uid); }).filter(Boolean);
            // The endpoint supplies only received recent messages. Local split bubbles stay owned by the chat host.
            var current = host.getState().messages || [];
            var local = current.filter(function (message) { return !message.id.startsWith(displayPrefix); });
            var unknown = new Set(result.originals.filter(function (record) {
                return record.originalTime === null || !Number.isSafeInteger(Number(record.originalTime));
            }).map(function (record) { return displayPrefix + uid + '-' + record.messageId; }));
            if (displayReceiptUid !== uid) { displayReceiptUid = uid; displayReceiptOrder.clear(); }
            var latestPosition = Math.max(0, ...local.concat(received).map(function (message) {
                return typeof message.createdAt === 'number' ? message.createdAt : 0;
            }), ...displayReceiptOrder.values());
            var receiptOrder = new Map();
            received.forEach(function (message) {
                if (unknown.has(message.id)) receiptOrder.set(message.id,
                    displayReceiptOrder.has(message.id) ? displayReceiptOrder.get(message.id) : ++latestPosition);
            });
            // Display positions are receipt order only; original times remain unknown.
            displayReceiptOrder = receiptOrder;
            function position(message) {
                return receiptOrder.has(message.id) ? receiptOrder.get(message.id)
                    : typeof message.createdAt === 'number' ? message.createdAt : Infinity;
            }
            var merged = local.concat(received).sort(function (a, b) {
                return position(a) - position(b);
            }).slice(-host.maxMessages).map(function (message, index) {
                // The host sorts by explicit keys, including keys retained by local split bubbles.
                return Object.assign({}, message, { sortKey: index });
            });
            if (displaySnapshot(current) !== displaySnapshot(merged)) host.setMessages(merged);
        } finally {
            displayFlight = false;
            if (displayPending) { displayPending = false; refreshRecentDisplay().catch(function () {}); }
        }
    }

    function requestMemoryRefresh(name) {
        if (typeof name !== 'string' || name !== liveCharacterName()) return;
        pendingMemoryName = name;
        flushMemoryRefresh();
    }

    function flushMemoryRefresh() {
        var state = window.appState || {};
        // Sync must not restart an open voice session or cancel microphone startup.
        if (state.isRecording || state.voiceChatActive || state.voiceStartPending || window.isMicStarting) return;
        if (!pendingMemoryName || memoryRefreshFlight || liveChatBusy(window.reactChatWindowHost)) return;
        var name = pendingMemoryName;
        if (name !== liveCharacterName()) { pendingMemoryName = null; return; }
        if (!window.appInterpage || typeof window.appInterpage.handleMemoryEdited !== 'function') return;
        pendingMemoryName = null;
        memoryRefreshFlight = true;
        Promise.resolve(window.appInterpage.handleMemoryEdited(name, { silent: true })).catch(function () {
            if (liveCharacterName() === name && !pendingMemoryName) pendingMemoryName = name;
        }).finally(function () { memoryRefreshFlight = false; });
    }

    function refreshReceived() {
        flushMemoryRefresh();
        refreshRecentDisplay().catch(function () { /* The next committed hint or local poll retries. */ });
    }
    window.addEventListener('voice-input-socket-open', refreshReceived);
    window.addEventListener('neko-assistant-turn-end', refreshReceived);
    window.addEventListener('neko-realistic-queue-idle', refreshReceived);
    window.addEventListener('neko-speech-playback-state', refreshReceived);
    window.addEventListener('voice-input-lifecycle-changed', refreshReceived);

    async function foregroundCatchup() {
        // The local bridge owns credentials and all enabled roles. A foreground
        // wake does not perform manual sync or bypass durable retry fences.
        if (foregroundWakeRunning || document.visibilityState === 'hidden' || !document.getElementById('react-chat-window-root')) return;
        foregroundWakeRunning = true;
        try { await api('/wake', 'POST'); await refreshReadyModel(); await refreshRecentDisplay(); } catch (_) { /* Local chat stays usable. */ }
        finally { foregroundWakeRunning = false; }
    }
    document.addEventListener('neko:chat-sync-history-changed', function () {
        refreshReceived();
        return refreshReadyModel().catch(function () { /* A later foreground wake retries. */ });
    });
    document.addEventListener('DOMContentLoaded', function () {
        refreshReceived();
        window.setInterval(refreshReceived, 30000);
        if (!window.live2dManager || typeof window.handleModelReload !== 'function') return;
        refreshReadyModel().catch(function () {});
        window.setInterval(function () {
            if (document.visibilityState !== 'hidden') refreshReadyModel().catch(function () {});
        }, 30000);
    });
    document.addEventListener('visibilitychange', foregroundCatchup);
    window.addEventListener('pageshow', foregroundCatchup);
})();
