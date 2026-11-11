/* Static profile consent and retained conflicts; OAuth remains in the backend. */
(function () {
    'use strict';
    var dialog = null, promptDialog = null, timer = null, polling = false, reviewed = new Set();
    var promptQueue = Promise.resolve();
    function text(key, values) { return window.t ? window.t('chatSync.' + key, values || {}) : key; }
    async function api(path, body) {
        // The browser can choose an observed hash/revision, never an owner or token.
        var response = await fetch('/api/chat-sync' + path, {method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
            headers: body === undefined ? {} : {'Content-Type': 'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body)});
        var value;
        try { value = await response.json(); }
        catch (_) { throw new Error('profile_unavailable'); }
        if (!response.ok) throw new Error(value && typeof value.detail === 'string' ? value.detail : 'profile_unavailable');
        return value;
    }
    function preview(parent, profile) {
        // Show the actual persona in readable sections, never protocol IDs,
        // hashes or JSON envelopes. Text nodes keep user-authored text inert.
        var lines = [text('profileName') + ': ' + profile.name,
            text('profileNickname') + ': ' + profile.nickname,
            text('profileSetting') + ':\n' + profile.systemPrompt];
        profile.fields.forEach(function (field) { lines.push(field.key + ': ' + field.value); });
        if (profile.persona) {
            lines.push(text('profileStyle') + ':\n' + profile.persona.promptGuidance);
            profile.persona.fields.forEach(function (field) { lines.push(field.key + ': ' + field.value); });
        }
        var node = document.createElement('pre'); node.textContent = lines.join('\n\n');
        node.className = 'chat-sync-profile-preview';
        parent.appendChild(node);
    }
    function createPanel(titleKey, id) {
        var panel = document.createElement('dialog'); panel.id = id;
        panel.setAttribute('aria-labelledby', id + '-title');
        var header = document.createElement('div'); header.className = 'chat-sync-dialog-header';
        var icon = document.createElement('img'); icon.src = '/static/icons/set_on.png'; icon.alt = '';
        var title = document.createElement('h2'); title.id = id + '-title'; title.textContent = text(titleKey);
        var close = document.createElement('button'); close.type = 'button'; close.className = 'chat-sync-close';
        close.setAttribute('aria-label', text('close'));
        var closeIcon = document.createElement('img'); closeIcon.src = '/static/icons/close_button.png'; closeIcon.alt = '';
        close.appendChild(closeIcon); close.addEventListener('click', function () { panel.close(); });
        header.appendChild(icon); header.appendChild(title); header.appendChild(close); panel.appendChild(header);
        return panel;
    }
    function promptProfile(message, confirmation) {
        // Every save and notice keeps its own consent result while another prompt is open.
        var pending = promptQueue.then(function () { return showProfilePrompt(message, confirmation); });
        promptQueue = pending.catch(function () {});
        return pending;
    }
    function showProfilePrompt(message, confirmation) {
        // Native top-layer dialogs also keep rename confirmation above its profile preview.
        var panel = createPanel('title', 'character-profile-sync-prompt'), accepted = false;
        var body = document.createElement('p'); body.className = 'chat-sync-status'; body.textContent = message;
        panel.appendChild(body);
        var actions = document.createElement('div'); actions.className = 'chat-sync-actions'; panel.appendChild(actions);
        var cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'chat-sync-button chat-sync-secondary';
        cancel.textContent = confirmation ? (window.t ? window.t('common.cancel') : 'cancel') : text('close');
        cancel.addEventListener('click', function () { panel.close(); }); actions.appendChild(cancel);
        if (confirmation) {
            var confirm = document.createElement('button'); confirm.type = 'button'; confirm.className = 'chat-sync-button';
            confirm.textContent = window.t ? window.t('common.confirm') : 'confirm';
            confirm.addEventListener('click', function () { accepted = true; panel.close(); }); actions.appendChild(confirm);
        }
        return new Promise(function (resolve) {
            panel.addEventListener('close', function () {
                panel.remove(); if (promptDialog === panel) promptDialog = null; resolve(accepted);
            });
            promptDialog = panel;
            try { document.body.appendChild(panel); panel.showModal(); }
            catch (error) { panel.remove(); if (promptDialog === panel) promptDialog = null; throw error; }
        });
    }
    function openDialog(titleKey) {
        // One profile dialog owns its listeners and is removed on normal close.
        dialog = createPanel(titleKey, 'character-profile-sync-dialog'); var owned = dialog;
        // A dedicated selector opts this modal into the transparent chat page's pointer input.
        owned.addEventListener('close', function () { owned.remove(); if (dialog === owned) dialog = null; });
        document.body.appendChild(dialog); dialog.showModal();
        return dialog;
    }
    async function review(uid, state) {
        // Raw versions are text nodes. Neither candidate is overwritten by opening
        // or dismissing this dialog, including a pending remote profile rename.
        if (dialog || promptDialog || !state.head) return false;
        var panel = openDialog(state.renameRequired ? 'profileRenamePending' : 'profileConflict'), resolving = false;
        var versions = document.createElement('div'); versions.className = 'chat-sync-versions'; panel.appendChild(versions);
        [['profileLocal', state.local], ['profileRemote', state.head]].forEach(function (candidate) {
            var section = document.createElement('section'); section.className = 'chat-sync-version';
            var label = document.createElement('h3');
            label.textContent = text(candidate[0]);
            section.appendChild(label); preview(section, candidate[1]); versions.appendChild(section);
        });
        var status = document.createElement('p'); status.className = 'chat-sync-status'; status.setAttribute('role', 'status'); panel.appendChild(status);
        var actions = document.createElement('div'); actions.className = 'chat-sync-actions'; panel.appendChild(actions);
        ['local', 'remote', 'close'].forEach(function (choice) {
            var button = document.createElement('button'); button.type = 'button'; button.dataset.profileChoice = choice;
            button.className = 'chat-sync-button' + (choice === 'remote' ? '' : ' chat-sync-secondary');
            button.textContent = text(choice === 'local' ? 'profileUseLocal' : choice === 'remote' ? 'profileUseRemote' : 'close');
            button.addEventListener('click', async function () {
                if (choice === 'close') { panel.close(); return; }
                if (resolving) return;
                resolving = true;
                try {
                    if (choice === 'remote' && state.renameRequired && !await promptProfile(text('profileRenameConfirm', {
                        oldName: state.local.name, newName: state.head.name
                    }), true)) return;
                    if (dialog !== panel) return;
                    button.disabled = true;
                    await api('/characters/' + uid + '/profile/resolve', {choice: choice, localHash: state.local_hash,
                        revision: state.head_revision, confirmRename: choice === 'remote' && state.renameRequired});
                    panel.close();
                    // Refresh saved card names through the existing list loader;
                    // never reload the page or replace an open editor's draft.
                    if (choice === 'remote' && typeof window.loadCharacterCards === 'function') await window.loadCharacterCards();
                } catch (_) {
                    // A newer third revision must be reviewed, never force-replaced.
                    status.textContent = text('profileChanged'); reviewed.clear();
                } finally { button.disabled = false; resolving = false; }
            });
            actions.appendChild(button);
        });
        return true;
    }
    async function cloudImport() {
        // Import is an explicit user action. Listing or previewing alone never
        // binds, enables or creates a local character.
        if (dialog || promptDialog) return;
        var panel = openDialog('cloudImport'), state = document.createElement('p');
        state.className = 'chat-sync-status'; state.textContent = text('working');
        state.setAttribute('role', 'status'); panel.appendChild(state);
        var rolesList = document.createElement('div'); rolesList.className = 'chat-sync-role-list'; panel.appendChild(rolesList);
        var actions = document.createElement('div'); actions.className = 'chat-sync-actions'; panel.appendChild(actions);
        var close = document.createElement('button'); close.textContent = text('close'); close.type = 'button';
        close.className = 'chat-sync-button chat-sync-secondary';
        close.addEventListener('click', function () { panel.close(); }); actions.appendChild(close);
        try {
            var roles = await api('/cloud/characters');
            state.textContent = roles.characters.length ? '' : text('cloudEmpty');
            roles.characters.forEach(function (role) {
                var choose = document.createElement('button'); choose.type = 'button'; choose.textContent = role.displayName;
                choose.className = 'chat-sync-button chat-sync-secondary';
                choose.addEventListener('click', async function () {
                    choose.disabled = true;
                    try {
                        var head = await api('/cloud/characters/' + role.characterSyncId + '/profile');
                        // A closed dialog cannot issue a late import request.
                        if (dialog !== panel) return;
                        var old = panel.querySelector('[data-profile-preview]'); if (old) old.remove();
                        var section = document.createElement('section'); section.dataset.profilePreview = 'true'; panel.insertBefore(section, actions);
                        if (!head.profile) { section.textContent = text('cloudProfileEmpty'); return; }
                        preview(section, head.profile);
                        var create = document.createElement('button'); create.type = 'button'; create.textContent = text('cloudCreate');
                        create.className = 'chat-sync-button';
                        create.addEventListener('click', async function () {
                            create.disabled = true;
                            try {
                                var result = await api('/cloud/characters/' + role.characterSyncId + '/import', {
                                    revision: head.revision, profileHash: head.profileHash});
                                state.textContent = text(result.existing ? 'cloudExisting' : 'cloudCreated', {name: result.displayName});
                                // Refresh the existing local card list without
                                // selecting a role or invoking a conversation.
                                if (typeof window.loadCharacterCards === 'function') await window.loadCharacterCards();
                            } catch (error) {
                                state.textContent = text(error.message === 'character_profile_name_conflict' ? 'cloudNameConflict' : 'profileChanged');
                                create.disabled = false;
                            }
                        });
                        section.appendChild(create);
                    } catch (_) { state.textContent = text('profileSaveFailed'); }
                    finally { choose.disabled = false; }
                });
                rolesList.appendChild(choose);
            });
        } catch (_) { state.textContent = text('profileSaveFailed'); }
    }
    async function getSyncChoice(name) {
        const status = await api('/status');
        const role = status.characters.find(item => item.displayName === name);
        if (!role) throw new Error('chat_sync_character_not_found');
        // The backend includes a durable pending resume in the effective saved choice.
        return role.syncEnabled === true;
    }

    async function afterCardSave(name, enabled) {
        // A status read still in flight supplies no consent snapshot. Keep the
        // previous setting without reporting an optional sync failure.
        if (typeof enabled !== 'boolean') return;
        try {
            const status = await api('/status');
            const role = status.characters.find(item => item.displayName === name);
            if (!role) throw new Error('chat_sync_character_not_found');
            // The labeled card switch is the sole affirmative action for saved settings and trusted history.
            await api('/characters/' + role.characterSyncId + '/sync-choice', {enabled, includeHistory: true});
            if (enabled && (status.writerRecoveryRequired || (role.binding && role.binding.writerRecoveryRequired))) {
                window.showMessage(window.nekoChatSync ? window.nekoChatSync.describeError('chat_sync_writer_recovery_required')
                    : text('writerRecoveryRequired'), 'warning');
            } else {
                window.showMessage(text('syncChoiceSaved'), 'success');
            }
        } catch (error) {
            // The card is already saved; an unreadable switch must preserve its previous consent.
            window.showMessage(window.nekoChatSync ? window.nekoChatSync.describeError(error) : text('newSyncFailed'), 'warning');
        }
    }

    async function afterSave(name) {
        // Local saving already succeeded. A refusal still records divergence,
        // protecting this exact saved version from automatic remote replacement.
        try {
            var state = await api('/status');
            var role = state.characters.find(function (item) { return item.displayName === name; });
            if (!role || !role.binding) {
                // Saving an unbound role still asks about upload, but consent
                // cannot silently enable sync or invent an account/role binding.
                if (await promptProfile(text('profileUploadConfirm', {name: name}), true)) {
                    await promptProfile(text('profileNeedsBinding'), false);
                }
                return;
            }
            var candidate = await api('/characters/' + role.characterSyncId + '/profile/local', {});
            if (candidate.conflict || (candidate.renameRequired && candidate.apply_pending)) {
                await review(role.characterSyncId, candidate); return;
            }
            if (!candidate.dirty || !await promptProfile(text('profileUploadConfirm', {name: name}), true)) return;
            await api('/characters/' + role.characterSyncId + '/profile/approve', {
                localHash: candidate.local_hash, revision: candidate.head_revision});
        } catch (error) {
            // Optional sync failures cannot turn a completed local save into failure.
            await promptProfile(text(error.message === 'character_profile_unsupported_value' ? 'profileUnsupported' : 'profileSaveFailed'), false);
        }
    }
    async function poll() {
        // Cheap local observations surface conflicts for all opted-in roles;
        // the backend owns authenticated cloud polling at the normal cadence.
        if (polling || dialog || promptDialog || document.visibilityState === 'hidden') return;
        polling = true;
        try {
            var state = await api('/status');
            for (var role of state.characters) {
                if (role.syncSetupError) {
                    var setupKey = role.characterSyncId + ':setup:' + role.syncSetupError;
                    if (!reviewed.has(setupKey)) {
                        reviewed.add(setupKey);
                        var setupMessage = text('setupNeedsSave', {name: role.displayName});
                        var detail = window.nekoChatSync && window.nekoChatSync.describeError(role.syncSetupError);
                        if (detail && detail !== text('errorUnknown')) setupMessage += '\n' + detail;
                        window.showMessage(setupMessage, 'warning');
                    }
                }
                if (role.syncSetupDiagnostic) {
                    var diagnosticKey = role.characterSyncId + ':diagnostic:' + role.syncSetupDiagnostic;
                    if (!reviewed.has(diagnosticKey)) {
                        reviewed.add(diagnosticKey);
                        var diagnostic = window.nekoChatSync && window.nekoChatSync.describeError(role.syncSetupDiagnostic);
                        if (diagnostic) window.showMessage(diagnostic, 'warning');
                    }
                }
                var profile = role.binding && role.binding.profile;
                if (!profile) continue;
                var key = role.characterSyncId + ':' + profile.local_hash + ':' + profile.head_revision;
                if ((profile.conflict || (profile.renameRequired && profile.apply_pending)) && !reviewed.has(key)) {
                    var opened = true;
                    try { opened = await review(role.characterSyncId, await api('/characters/' + role.characterSyncId + '/profile/local', {})); }
                    catch (_) { /* Skip unreadable candidates until their hash/revision changes. */ }
                    if (opened) reviewed.add(key);
                    break;
                }
            }
        } finally { polling = false; }
    }
    function start() {
        // BFCache restores reuse one local timer and never start cloud requests.
        if (timer === null) timer = window.setInterval(function () { return poll().catch(function () {}); }, 5000);
    }
    window.nekoCharacterProfileSync = {afterCardSave: afterCardSave, getSyncChoice: getSyncChoice,
        afterSave: afterSave, review: review, poll: poll, cloudImport: cloudImport};
    document.addEventListener('DOMContentLoaded', start);
    window.addEventListener('pageshow', start);
    window.addEventListener('pagehide', function () { if (timer !== null) window.clearInterval(timer); timer = null; });
})();
