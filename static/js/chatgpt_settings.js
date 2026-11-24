/* Copyright 2025-2026 Project N.E.K.O. Team; SPDX-License-Identifier: Apache-2.0 */
(() => {
    'use strict';
    const account = document.getElementById('chatgpt-account');
    if (!account) return;
    const status = document.getElementById('chatgpt-status');
    const login = document.getElementById('chatgpt-login');
    const add = document.getElementById('chatgpt-add');
    const logout = document.getElementById('chatgpt-logout');
    const authorization = document.getElementById('chatgpt-authorization');
    const enabled = document.getElementById('chatgpt-enabled');
    const disclosure = document.getElementById('chatgpt-toggle-btn');
    const panel = document.getElementById('chatgpt-options');
    const confirmModal = document.getElementById('chatgpt-confirm-modal');
    const confirmMessage = document.getElementById('chatgpt-confirm-message');
    const confirmCancel = document.getElementById('chatgpt-confirm-cancel');
    const confirmAccept = document.getElementById('chatgpt-confirm-accept');
    let subscription = { enabled: false };
    let connected = false;
    disclosure.addEventListener('click', () => {
        panel.hidden = !panel.hidden;
        disclosure.setAttribute('aria-expanded', String(!panel.hidden));
        disclosure.classList.toggle('rotated', !panel.hidden);
    });
    let timer;
    let suspended = false;
    let refreshGeneration = 0;
    let busy = false;
    let pending = false;
    let modelAccountState;
    let finishConfirmation;
    // The last reload after a subscription change failed: the form does not show
    // the saved configuration until a later reload succeeds or the page reopens.
    let formStale = false;
    let confirmationFocus;
    window.isChatGPTSettingsBusy = () => busy;
    const t = (key, fallback) => {
        const translated = window.t ? window.t(`api.chatgpt.${key}`) : '';
        return translated && translated !== `api.chatgpt.${key}` ? translated : fallback;
    };
    // One deadline covers the request and its body, so a stalled response
    // cannot keep the page's save lock (busy) forever. Enabling reads the
    // account catalog and reloads the app, hence the generous bound; callers
    // reconcile with a fresh status read instead of resending a write.
    const API_TIMEOUT_MS = 60000;
    const api = async (path, body) => {
        const signal = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
            ? AbortSignal.timeout(API_TIMEOUT_MS) : undefined;
        let response;
        let data;
        try {
            response = await fetch(`/api/config/chatgpt/${path}`, {
                method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
                headers: { 'Content-Type': 'application/json' }, signal,
                ...(body === undefined ? {} : { body: JSON.stringify(body) })
            });
            data = await response.json();
        } catch (error) {
            if (!response && error?.name !== 'TimeoutError') throw error;
            throw new Error(t('failed', 'ChatGPT connection failed.'));
        }
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? t(data.detail, data.detail) : t('failed', 'ChatGPT connection failed.'));
        return data;
    };
    const invalidate = () => {
        if (typeof invalidateModelLists === 'function') invalidateModelLists();
        if (typeof ConnectivityManager !== 'undefined' && typeof ConnectivityManager.invalidateChatGPTAccount === 'function') {
            ConnectivityManager.invalidateChatGPTAccount();
        }
    };
    const confirmChange = message => new Promise(resolve => {
        if (suspended) { resolve(false); return; }
        confirmationFocus = enabled;
        confirmMessage.textContent = message;
        confirmModal.style.display = 'flex';
        confirmModal.setAttribute('aria-hidden', 'false');
        finishConfirmation = accepted => {
            finishConfirmation = undefined;
            confirmModal.style.display = 'none';
            confirmModal.setAttribute('aria-hidden', 'true');
            resolve(accepted);
        };
        confirmCancel.focus();
    });
    confirmCancel.addEventListener('click', () => finishConfirmation?.(false));
    confirmAccept.addEventListener('click', () => finishConfirmation?.(true));
    confirmModal.addEventListener('click', event => {
        if (event.target === confirmModal) finishConfirmation?.(false);
    });
    confirmModal.addEventListener('keydown', event => {
        if (!finishConfirmation) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            finishConfirmation(false);
        } else if (event.key === 'Tab') {
            event.preventDefault();
            (document.activeElement === confirmCancel ? confirmAccept : confirmCancel).focus();
        }
    });
    async function refresh() {
        clearTimeout(timer);
        if (suspended) return;
        const generation = ++refreshGeneration;
        let data;
        try {
            const results = await Promise.all([api('status'), api('subscription?verify_authorization=true')]);
            data = results[0];
            if (suspended || generation !== refreshGeneration) return;
            subscription = results[1];
        } catch (error) {
            if (suspended || generation !== refreshGeneration) return;
            // A failed initial/post-login poll must recover too: the server may
            // already have accepted a login even before we observed pending.
            report(error);
            timer = setTimeout(refresh, 1500);
            return;
        }
        if (suspended || generation !== refreshGeneration) return;
        connected = data.accounts.some(entry => entry.id === data.active && entry.connected !== false);
        enabled.checked = subscription.enabled;
        enabled.disabled = busy || pending || (!connected && !subscription.enabled);
        const previous = account.value;
        account.replaceChildren();
        for (const entry of data.accounts) {
            const option = document.createElement('option');
            option.value = entry.id;
            option.textContent = `${entry.email || 'ChatGPT'} · ${entry.id.slice(-8)}${entry.id === data.active ? ' ✓' : ''}`;
            account.appendChild(option);
        }
        if (!data.accounts.length) {
            const option = document.createElement('option');
            option.value = '';
            option.textContent = t('noAccount', 'No saved account');
            account.appendChild(option);
        }
        account.value = data.active || (data.accounts.some(a => a.id === previous) ? previous : account.options[0].value);
        const finishedLogin = pending && data.login_status !== 'pending';
        pending = data.login_status === 'pending';
        if (!pending) {
            authorization.hidden = true;
            authorization.removeAttribute('href');
        }
        login.disabled = add.disabled = account.disabled = pending || busy;
        logout.disabled = pending || busy || !account.value;
        status.textContent = data.error || (pending ? t('pending', 'Complete sign-in in your browser.') :
            data.login_status === 'expired' ? t('expired', 'Sign-in timed out. Please try again.') :
            data.active ? t('connected', 'ChatGPT connected. Turn on the subscription switch to configure models automatically.') : t('signedOut', 'ChatGPT is not connected.'));
        enabled.disabled = busy || pending || (!connected && !subscription.enabled);
        if (subscription.authorization_required) {
            panel.hidden = false;
            disclosure.setAttribute('aria-expanded', 'true');
            disclosure.classList.toggle('rotated', true);
            status.textContent = t('authorizationRequired', 'ChatGPT authorization is unavailable. Sign in again or turn off the switch to restore your previous configuration.');
        }
        const showsEnabled = !subscription.authorization_required && subscription.enabled
            && !pending && !data.error && data.login_status !== 'expired';
        if (showsEnabled) {
            status.textContent = t('enabled', 'ChatGPT subscription is enabled and saved. Model: {{model}}').replace('{{model}}', subscription.model);
        }
        // An out-of-date form is always said: it replaces "enabled and saved" (which
        // it contradicts) and follows any other state (pending sign-in, authorization).
        if (formStale) {
            status.textContent = showsEnabled ? staleFormText() : `${status.textContent} ${staleFormText()}`;
        }
        if (pending) timer = setTimeout(() => refresh().catch(report), 1500);
        else {
            const nextModelAccountState = JSON.stringify([data.active, connected, !!subscription.authorization_required]);
            if (finishedLogin || nextModelAccountState !== modelAccountState) invalidate();
            modelAccountState = nextModelAccountState;
            if (subscription.enabled) timer = setTimeout(() => refresh().catch(report), 15000);
        }
        return data;
    }
    // A connection error must not hide that the form is still out of date.
    function report(error) { status.textContent = formStale ? `${error.message} ${staleFormText()}` : error.message; }
    async function run(action) {
        const saving = typeof isApiSettingsSaveBusy === 'function'
            ? isApiSettingsSaveBusy() : typeof _apiSaveInProgress !== 'undefined' && _apiSaveInProgress;
        if (busy || saving) return;
        busy = true;
        ++refreshGeneration;
        clearTimeout(timer);
        login.disabled = add.disabled = logout.disabled = account.disabled = enabled.disabled = true;
        try { await action(); }
        catch (error) { report(error); }
        finally {
            busy = false;
            login.disabled = add.disabled = account.disabled = pending;
            logout.disabled = pending || !account.value;
            enabled.disabled = pending || (!connected && !subscription.enabled);
            if (confirmationFocus && !suspended) {
                (confirmationFocus.disabled ? disclosure : confirmationFocus).focus();
            }
            confirmationFocus = undefined;
        }
    }
    // A stalled settings read must not hold busy (and with it ordinary saves) forever.
    // The read keeps going; if it finishes later it announces neko:api-settings-loaded.
    const RELOAD_TIMEOUT_MS = 30000;
    const withDeadline = promise => {
        if (typeof AbortSignal === 'undefined' || typeof AbortSignal.timeout !== 'function') return promise;
        const deadline = AbortSignal.timeout(RELOAD_TIMEOUT_MS);
        return Promise.race([promise, new Promise(resolve => {
            deadline.addEventListener('abort', () => {
                // The late response must not land on edits made after the controls unlock.
                if (typeof window.abandonApiSettingsLoad === 'function') window.abandonApiSettingsLoad();
                resolve(false);
            }, { once: true });
        })]);
    };
    // Returns whether the settings form now shows the saved configuration.
    const reloadSettings = async () => {
        if (typeof loadCurrentApiKey !== 'function') return true;
        const loaded = await withDeadline(loadCurrentApiKey());
        // Values were set without change events: point the lights at the new slot keys.
        if (typeof window.refreshCustomConnectivityLightBindings === 'function') {
            window.refreshCustomConnectivityLightBindings();
        }
        formStale = loaded === false;
        return !formStale;
    };
    function staleFormText() {
        return t('reloadFailed', 'The change was saved, but this page could not reload the settings. Reopen the page before saving anything else.');
    }
    enabled.addEventListener('change', () => {
        const requested = enabled.checked;
        enabled.checked = subscription.enabled;
        return run(async () => {
            let attempted = false;
            try {
                const current = await api('subscription');
                // Same slot names as the connectivity lights (api_key_settings.js).
                const slots = current.overwritten.map(slot => (
                    typeof getCustomModelDisplayLabel === 'function' ? getCustomModelDisplayLabel(slot) : slot
                )).join(', ');
                const message = requested
                    ? t('confirmEnable', 'Enable ChatGPT subscription and save? Existing slots to replace: {{slots}}. Unsaved edits on this page will be discarded.').replace('{{slots}}', slots || t('none', 'None'))
                    : t('confirmDisable', 'Restore the configuration saved before enabling the subscription? Unsaved edits on this page will be discarded.');
                if (!await confirmChange(message)) return;
                attempted = true;
                const result = await api('subscription', { enabled: requested, revision: current.revision });
                if (!result.success) throw new Error(result.error || t('failed', 'ChatGPT connection failed.'));
            } finally {
                // Reconcile after a timeout too: persistence may already have completed.
                // refresh() states a failed reload (formStale) alongside whatever else it shows.
                try { if (attempted) await reloadSettings(); }
                finally { await refresh(); }
            }
        });
    });
    const signIn = accountId => run(async () => {
        if (pending) return;
        // Open synchronously on the click so popup blockers do not reject OAuth.
        const external = window.electronShell && window.electronShell.openExternal;
        const popup = external ? null : window.open('about:blank', '_blank');
        if (popup) popup.opener = null;
        try {
            const data = await api('login', { account_id: accountId || null });
            const url = new URL(data.url);
            if (url.origin !== 'https://auth.openai.com' || url.pathname !== '/api/accounts/authorize') throw new Error(t('failed', 'ChatGPT connection failed.'));
            pending = true;
            // Keep a user-activated entry point even when automatic navigation
            // is blocked or an Electron external-browser launch fails.
            authorization.href = url.href;
            authorization.hidden = false;
            if (external) await window.electronShell.openExternal(url.href);
            else if (popup) popup.location.replace(url.href);
            await refresh();
        } catch (error) {
            if (popup) popup.close();
            if (!suspended) timer = setTimeout(refresh, 1500);
            throw error;
        }
    });
    login.addEventListener('click', () => signIn(account.value));
    add.addEventListener('click', () => signIn(null));
    account.addEventListener('change', () => run(async () => {
        invalidate();
        try { await api('select', { account_id: account.value }); }
        // Again after the switch: a test started before it must not land even if refresh fails.
        finally { invalidate(); await refresh(); }
    }));
    logout.addEventListener('click', () => run(async () => {
        invalidate();
        let result;
        let failure;
        try { result = await api('logout', { account_id: account.value }); }
        catch (error) { failure = error; }
        try {
            // A lost/error response may follow successful local restoration.
            if (failure || result.subscription_restored) await reloadSettings();
        } finally { await refresh(); }
        if (failure) throw failure;
        if (!result.revocation_confirmed) status.textContent = t('revocationUnconfirmed', 'Signed out locally. Disconnect the app in ChatGPT Settings to confirm remote revocation.');
    }));
    window.addEventListener('localechange', () => refresh().catch(report));
    // Any successful settings reload (an ordinary save included) brings the form back in sync.
    window.addEventListener('neko:api-settings-loaded', () => {
        if (!formStale) return;
        formStale = false;
        // Replace the shown warning now; with the subscription off no poll would.
        if (!busy) refresh().catch(report);
    });
    window.addEventListener('pagehide', () => {
        suspended = true;
        finishConfirmation?.(false);
        ++refreshGeneration;
        clearTimeout(timer);
    });
    window.addEventListener('pageshow', () => {
        suspended = false;
        refresh().catch(report);
    });
    refresh().catch(report);
})();
