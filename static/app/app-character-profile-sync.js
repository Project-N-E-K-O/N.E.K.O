/* Static profile consent and retained conflicts; OAuth remains in the backend. */
(function () {
    'use strict';
    var dialog = null, timer = null, polling = false, reviewed = new Set();
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
        node.style.maxHeight = '30vh'; node.style.overflow = 'auto'; node.style.whiteSpace = 'pre-wrap';
        parent.appendChild(node);
    }
    function openDialog(titleKey) {
        // One profile dialog owns its listeners and is removed on normal close.
        dialog = document.createElement('dialog'); var owned = dialog; owned.setAttribute('aria-label', text(titleKey));
        // A dedicated selector opts this modal into the transparent chat page's pointer input.
        owned.id = 'character-profile-sync-dialog';
        var title = document.createElement('h2'); title.textContent = text(titleKey); dialog.appendChild(title);
        owned.addEventListener('close', function () { owned.remove(); if (dialog === owned) dialog = null; });
        document.body.appendChild(dialog); dialog.showModal();
        return dialog;
    }
    async function review(uid, state) {
        // Raw versions are text nodes. Neither candidate is overwritten by opening
        // or dismissing this dialog, including a pending remote profile rename.
        if (dialog || !state.head) return;
        var panel = openDialog(state.renameRequired ? 'profileRenamePending' : 'profileConflict'), resolving = false;
        [['profileLocal', state.local], ['profileRemote', state.head]].forEach(function (candidate) {
            var label = document.createElement('h3');
            label.textContent = text(candidate[0]);
            panel.appendChild(label); preview(panel, candidate[1]);
        });
        var status = document.createElement('p'); status.setAttribute('role', 'status'); panel.appendChild(status);
        ['local', 'remote', 'close'].forEach(function (choice) {
            var button = document.createElement('button'); button.type = 'button'; button.dataset.profileChoice = choice;
            button.textContent = text(choice === 'local' ? 'profileUseLocal' : choice === 'remote' ? 'profileUseRemote' : 'close');
            button.addEventListener('click', async function () {
                if (choice === 'close') { panel.close(); return; }
                if (resolving) return;
                if (choice === 'remote' && state.renameRequired && !window.confirm(text('profileRenameConfirm', {
                    oldName: state.local.name, newName: state.head.name
                }))) return;
                button.disabled = true; resolving = true;
                try {
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
            panel.appendChild(button);
        });
    }
    async function cloudImport() {
        // Import is an explicit user action. Listing or previewing alone never
        // binds, enables or creates a local character.
        if (dialog) return;
        var panel = openDialog('cloudImport'), state = document.createElement('p');
        state.setAttribute('role', 'status'); panel.appendChild(state);
        var close = document.createElement('button'); close.textContent = text('close'); close.type = 'button';
        close.addEventListener('click', function () { panel.close(); }); panel.appendChild(close);
        try {
            var roles = await api('/cloud/characters');
            if (!roles.characters.length) state.textContent = text('cloudEmpty');
            roles.characters.forEach(function (role) {
                var choose = document.createElement('button'); choose.type = 'button'; choose.textContent = role.displayName;
                choose.addEventListener('click', async function () {
                    choose.disabled = true;
                    try {
                        var head = await api('/cloud/characters/' + role.characterSyncId + '/profile');
                        // A closed dialog cannot issue a late import request.
                        if (dialog !== panel) return;
                        var old = panel.querySelector('[data-profile-preview]'); if (old) old.remove();
                        var section = document.createElement('section'); section.dataset.profilePreview = 'true'; panel.appendChild(section);
                        if (!head.profile) { section.textContent = text('cloudProfileEmpty'); return; }
                        preview(section, head.profile);
                        var create = document.createElement('button'); create.type = 'button'; create.textContent = text('cloudCreate');
                        create.addEventListener('click', async function () {
                            create.disabled = true;
                            try {
                                var result = await api('/cloud/characters/' + role.characterSyncId + '/import', {
                                    revision: head.revision, profileHash: head.profileHash});
                                state.textContent = text(result.existing ? 'cloudExisting' : 'cloudCreated', {name: result.displayName});
                                // Refresh the existing local card list without
                                // selecting a role or invoking a conversation.
                                if (typeof window.loadCharacterCards === 'function') await window.loadCharacterCards();
                                window.dispatchEvent(new CustomEvent('neko-character-profile-imported'));
                            } catch (error) {
                                state.textContent = text(error.message === 'character_profile_name_conflict' ? 'cloudNameConflict' : 'profileChanged');
                                create.disabled = false;
                            }
                        });
                        section.appendChild(create);
                    } catch (_) { state.textContent = text('profileSaveFailed'); }
                    finally { choose.disabled = false; }
                });
                panel.insertBefore(choose, close);
            });
        } catch (_) { state.textContent = text('profileSaveFailed'); }
    }
    async function getSyncChoice(name) {
        const status = await api('/status');
        const role = status.characters.find(item => item.displayName === name);
        if (!role) throw new Error('chat_sync_character_not_found');
        // The backend includes a durable pending resume in the effective saved choice.
        if (typeof role.syncEnabled === 'boolean') return role.syncEnabled;
        // A first grant still being issued is pending, not an explicit user pause.
        if (role.binding && (role.binding.bindingId !== null || role.binding.generation > 1)) return !!role.binding.enabled;
        return role.syncEnabled === true;
    }

    async function afterCardSave(name, enabled, isNew) {
        // A status read still in flight supplies no consent snapshot. Keep the
        // previous setting without reporting an optional sync failure.
        if (typeof enabled !== 'boolean') return;
        try {
            const status = await api('/status');
            const role = status.characters.find(item => item.displayName === name);
            if (!role) throw new Error('chat_sync_character_not_found');
            // The labeled card switch is the sole affirmative action for saved settings and trusted history.
            await api('/characters/' + role.characterSyncId + '/sync-choice', {enabled, includeHistory: true});
            window.showMessage(text('syncChoiceSaved'), 'success');
        } catch (_) {
            // The card is already saved; an unreadable switch must preserve its previous consent.
            window.showMessage(text('newSyncFailed'), 'warning');
        }
    }

    async function afterSave(name, initial) {
        // Local saving already succeeded. A refusal still records divergence,
        // protecting this exact saved version from automatic remote replacement.
        try {
            var state = await api('/status');
            var role = state.characters.find(function (item) { return item.displayName === name; });
            if (!role || !role.binding) {
                // Saving an unbound role still asks about upload, but consent
                // cannot silently enable sync or invent an account/role binding.
                if (window.confirm(text(initial ? 'profileInitialUpload' : 'profileUploadConfirm', {name: name}))) {
                    window.alert(text('profileNeedsBinding'));
                }
                return;
            }
            var candidate = await api('/characters/' + role.characterSyncId + '/profile/local', {});
            if (candidate.conflict || (candidate.renameRequired && candidate.apply_pending)) {
                await review(role.characterSyncId, candidate); return;
            }
            if (!candidate.dirty || !window.confirm(text(initial ? 'profileInitialUpload' : 'profileUploadConfirm', {name: name}))) return;
            await api('/characters/' + role.characterSyncId + '/profile/approve', {
                localHash: candidate.local_hash, revision: candidate.head_revision});
        } catch (error) {
            // Optional sync failures cannot turn a completed local save into failure.
            window.alert(text(error.message === 'character_profile_unsupported_value' ? 'profileUnsupported' : 'profileSaveFailed'));
        }
    }
    async function poll() {
        // Cheap local observations surface conflicts for all opted-in roles;
        // the backend owns authenticated cloud polling at the normal cadence.
        if (polling || dialog || document.visibilityState === 'hidden') return;
        polling = true;
        try {
            var state = await api('/status');
            for (var role of state.characters) {
                if (role.syncSetupError) {
                    var setupKey = role.characterSyncId + ':setup:' + role.syncSetupError;
                    if (!reviewed.has(setupKey)) {
                        reviewed.add(setupKey);
                        window.showMessage(text('setupNeedsSave', {name: role.displayName}), 'warning');
                    }
                }
                var profile = role.binding && role.binding.profile;
                if (!profile) continue;
                var key = role.characterSyncId + ':' + profile.local_hash + ':' + profile.head_revision;
                if ((profile.conflict || (profile.renameRequired && profile.apply_pending)) && !reviewed.has(key)) {
                    reviewed.add(key);
                    await review(role.characterSyncId, await api('/characters/' + role.characterSyncId + '/profile/local', {}));
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
