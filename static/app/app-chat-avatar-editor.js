/** Candidate editing is local; only the persistent state module may confirm an avatar. */
(function () {
    'use strict';
    const api = {};
    let initialized = false;
    let owner = 0;
    let candidate = null;
    let busy = false;
    let statusKey = '';
    let pendingBinding = null;
    let uncertainRestore = null;

    function state() { return window.appChatAvatarState; }
    function core() { return window.appChatAvatar; }
    function element(id) { return document.getElementById(id); }
    function label(key) {
        return window.safeT ? window.safeT('chatAvatar.custom.' + key, key) : key;
    }
    function current(generation, binding) {
        return owner === generation && (!binding || state().isCurrent(binding));
    }
    function describeError(error) {
        const code = error && error.code;
        if (code === 'chat_avatar_conflict') return 'conflict';
        if (code === 'CLOUDSAVE_WRITE_FENCE_ACTIVE') return 'maintenance';
        if (code === 'chat_avatar_storage_changed') return 'storageChanged';
        if (code === 'chat_avatar_unknown_outcome') return 'unknownOutcome';
        if (code === 'chat_avatar_too_large') return 'tooLarge';
        if (code === 'chat_avatar_too_many_pixels') return 'tooManyPixels';
        if (code === 'chat_avatar_invalid_image') return 'invalidImage';
        if (code === 'chat_avatar_character_not_found') return 'roleMissing';
        if (code === 'chat_avatar_unavailable') return 'unavailable';
        return 'saveFailed';
    }

    async function confirmUncertain(reason) {
        try {
            const confirmed = await state().refresh(reason);
            if (confirmed) return confirmed;
        } catch (_) { /* Keep the uncertainty until an authoritative read succeeds. */ }
        const error = new Error('chat_avatar_unknown_outcome');
        error.code = 'chat_avatar_unknown_outcome';
        throw error;
    }

    api.update = function () {
        const upload = element('chat-avatar-upload');
        const save = element('chat-avatar-save');
        const cancel = element('chat-avatar-cancel-edit');
        const restore = element('chat-avatar-restore');
        const status = element('chat-avatar-custom-status');
        const refresh = element('chatAvatarPreviewRefreshButton');
        const identity = state().getIdentity();
        const confirmed = state().getDataUrl();
        if (upload) upload.disabled = busy;
        if (save) {
            save.hidden = !candidate || !candidate.blob;
            save.disabled = busy || !!(candidate && candidate.conflict);
        }
        if (cancel) { cancel.hidden = !candidate; cancel.disabled = false; }
        if (restore) { restore.hidden = !confirmed; restore.disabled = busy || !identity || !!state().getError(); }
        if (refresh) refresh.hidden = !!confirmed || !!candidate;
        if (status) {
            let key = statusKey;
            if (!key && state().getError()) {
                const code = state().getError().code;
                key = code === 'chat_avatar_character_not_found' ? 'roleMissing'
                    : code === 'chat_avatar_unavailable' ? 'unavailable' : 'readFailed';
            }
            if (!key && !identity) key = 'loading';
            if (!key && confirmed) key = 'custom';
            if (!key) key = 'chooseHint';
            status.textContent = label(key);
        }
        if (core() && core().refreshDisplayedAvatar) core().refreshDisplayedAvatar();
    };

    api.getCandidateDataUrl = function () { return candidate && candidate.dataUrl || ''; };
    api.getState = function () {
        return { editing: !!candidate, ready: !!(candidate && candidate.blob), busy: busy, status: statusKey, operationId: candidate && candidate.binding.operationId };
    };
    api.cancel = function () {
        state().cancelEdit(pendingBinding);
        state().cancelEdit(candidate && candidate.binding);
        ++owner;
        candidate = null;
        busy = false;
        pendingBinding = null;
        statusKey = '';
        if (core() && core().closeUploadCropper) core().closeUploadCropper();
        api.update();
    };

    api.chooseFile = async function (file) {
        if (!file) return;
        api.cancel();
        uncertainRestore = null;
        const generation = owner;
        let source = null;
        // File selection belongs to the character visible at selection, even if its GET is pending.
        let binding = state().getIdentity();
        pendingBinding = binding;
        try {
            if (!binding) { const unavailable = new Error('chat_avatar_unavailable'); unavailable.code = 'chat_avatar_unavailable'; throw unavailable; }
            if (!state().getRecord() || state().getError()) await state().refresh('edit-start');
            if (!current(generation, binding)) return;
            binding = state().captureEdit();
            pendingBinding = binding;
            candidate = { binding: binding, blob: null, dataUrl: '' };
            statusKey = 'loading';
            core().cancelModelPreviewCapture();
            api.update();
            source = await window.appChatAvatarImage.decodeFile(file, state().getLimits());
            if (!current(generation, binding)) return;
            const crop = await core().openUploadCropper(source);
            if (!current(generation, binding)) return;
            if (!crop) { api.cancel(); return; }
            const dataUrl = await core().normalizeUploadCrop(source.url, crop.cropRect, state().getLimits().normalized_size);
            if (!current(generation, binding)) return;
            const blob = window.appChatAvatarImage.pngBlob(dataUrl, state().getLimits().normalized_max_bytes);
            candidate = { binding: binding, blob: blob, dataUrl: dataUrl };
            statusKey = 'ready';
            api.update();
            const save = element('chat-avatar-save');
            if (save) save.focus();
        } catch (error) {
            if (!current(generation, binding)) return;
            candidate = null;
            statusKey = describeError(error);
            api.update();
        } finally {
            // Closing a cropper cannot revoke a URL still used by image decoding/encoding.
            if (source) source.release();
            if (current(generation, binding)) pendingBinding = null;
        }
    };

    api.save = async function () {
        if (busy || !candidate || !candidate.blob || candidate.conflict) return;
        const editing = candidate;
        const generation = owner;
        busy = true;
        pendingBinding = editing.binding;
        statusKey = 'saving';
        api.update();
        try {
            if (editing.unknown) {
                const confirmed = await confirmUncertain('manual-retry-confirm');
                if (!current(generation, editing.binding)) return;
                if (confirmed && confirmed.last_operation_id === editing.binding.operationId) {
                    candidate = null;
                    statusKey = '';
                    return;
                }
                if (!confirmed || confirmed.revision !== editing.binding.baseRevision) {
                    const conflict = new Error('chat_avatar_conflict'); conflict.code = 'chat_avatar_conflict'; throw conflict;
                }
            }
            await state().save(editing.blob, editing.binding);
            if (!current(generation, editing.binding)) return;
            candidate = null;
            uncertainRestore = null;
            statusKey = '';
        } catch (error) {
            if (!current(generation, editing.binding)) return;
            statusKey = describeError(error);
            editing.unknown = error.code === 'chat_avatar_unknown_outcome';
            editing.conflict = statusKey === 'conflict' || statusKey === 'storageChanged';
        } finally {
            if (current(generation, editing.binding)) { busy = false; pendingBinding = null; api.update(); }
        }
    };

    api.restore = async function () {
        if (busy) return;
        api.cancel();
        const generation = owner;
        let binding;
        try {
            binding = uncertainRestore && state().isCurrent(uncertainRestore) ? uncertainRestore : state().captureEdit();
            pendingBinding = binding;
            busy = true;
            statusKey = 'saving';
            api.update();
            if (uncertainRestore === binding) {
                const confirmed = await confirmUncertain('restore-retry-confirm');
                if (!current(generation, binding)) return;
                if (confirmed && confirmed.last_operation_id === binding.operationId) {
                    uncertainRestore = null;
                    statusKey = '';
                    return;
                }
                if (!confirmed || confirmed.revision !== binding.baseRevision) {
                    const conflict = new Error('chat_avatar_conflict'); conflict.code = 'chat_avatar_conflict'; throw conflict;
                }
            }
            await state().restore(binding);
            if (!current(generation, binding)) return;
            uncertainRestore = null;
            statusKey = '';
        } catch (error) {
            if (current(generation, binding)) {
                statusKey = describeError(error);
                uncertainRestore = error.code === 'chat_avatar_unknown_outcome' ? binding : null;
            }
        } finally {
            if (current(generation, binding)) { busy = false; pendingBinding = null; api.update(); }
        }
    };

    api.initialize = function () {
        if (initialized) return;
        initialized = true;
        const upload = element('chat-avatar-upload');
        const input = element('chat-avatar-file-input');
        const save = element('chat-avatar-save');
        const cancel = element('chat-avatar-cancel-edit');
        const restore = element('chat-avatar-restore');
        if (upload && input) upload.addEventListener('click', function () { input.click(); });
        if (input) input.addEventListener('change', function () {
            const file = input.files && input.files[0];
            input.value = ''; // Selecting the same file must trigger a new edit.
            api.chooseFile(file);
        });
        if (save) save.addEventListener('click', api.save);
        if (cancel) cancel.addEventListener('click', api.cancel);
        if (restore) restore.addEventListener('click', api.restore);
        window.addEventListener('chat-avatar-display-updated', function (event) {
            if (['identity', 'switch-start', 'switch-rollback'].includes(event && event.detail && event.detail.reason)) {
                core().cancelModelPreviewCapture();
            }
            if ((pendingBinding && !state().isCurrent(pendingBinding))
                || (candidate && !state().isCurrent(candidate.binding))) api.cancel();
            else api.update();
        });
        window.addEventListener('chat-avatar-preview-updated', api.update);
        api.update();
    };
    window.appChatAvatarEditor = api;
})();
