/* Opt-in sync controls and foreground wake; chat history stays with the chat host. */
(function () {
    'use strict';
    var activeUid = null;
    var dialog = null;
    var settingsCharacterName = null;
    var statusNode = null;
    var DEFAULT_SERVICE_URL = 'https://sync.nekotech.cn';
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
        var error = role && (role.syncSetupError || (binding && binding.lastSyncError));
        statusNode.textContent = error ? describeError(error)
            : text(writerRecoveryRequired ? 'writerRecoveryRequired' : 'recoveryNotRequired');
        if (binding) {
            if (binding.pendingOriginals || binding.pendingDeletions) {
                statusNode.textContent += '\n' + text('pending', {
                    originals: binding.pendingOriginals || 0, deletions: binding.pendingDeletions || 0
                });
            }
            if (binding.localOnlyOriginals) statusNode.textContent += '\n' + text('oversizedLocalOnly', {count: binding.localOnlyOriginals});
            var awaiting = binding.historyBackfill && binding.historyBackfill.awaitingRegistration;
            if (awaiting) statusNode.textContent += '\n' + text('historyAwaitingRegistration', {count: awaiting});
        }
        updateControls();
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
            var recovered = await api(path + '/recovery/preview', 'POST', { serviceUrl: DEFAULT_SERVICE_URL });
            if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
            await refreshStatus();
            if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
            // Recovery never grants a copied deletion or a historical upload.
            // The existing card switch owns combined profile/history consent.
            var deletionTarget = recovered.deletions.localName ? recovered.deletions : recovered.history;
            if (recovered.deletions.count && window.confirm(text('recoveryDeleteConfirm', {
                    count: recovered.deletions.count, localName: deletionTarget.localName,
                    cloudName: deletionTarget.cloudName
                }))) {
                await api(path + '/recovery/deletions/approve', 'POST', { taskId: recovered.deletions.taskId });
            }
            if (!dialog.open || characterName() !== requestedName || activeUid !== requestedUid) return;
            statusNode.textContent = text('recoveryChecked');
            if (recovered.historyError) statusNode.textContent += '\n' + text('initialHistoryFailed');
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
        describeError: describeError
    };

    document.addEventListener('DOMContentLoaded', function () {
        var button = document.getElementById('chatSyncButton');
        if (!button) return;
        button.addEventListener('click', async function () {
            await window.nekoChatSync.openSettings();
        });
    });

    async function foregroundCatchup() {
        // The local bridge owns credentials and all enabled roles. A foreground
        // wake does not perform manual sync or bypass durable retry fences.
        if (foregroundWakeRunning || document.visibilityState === 'hidden' || !document.getElementById('react-chat-window-root')) return;
        foregroundWakeRunning = true;
        try { await api('/wake', 'POST'); } catch (_) { /* Local chat stays usable. */ }
        finally { foregroundWakeRunning = false; }
    }
    document.addEventListener('visibilitychange', foregroundCatchup);
    window.addEventListener('pageshow', foregroundCatchup);
})();
