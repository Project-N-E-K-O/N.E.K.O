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
    const t = (key, fallback) => {
        const translated = window.t ? window.t(`api.chatgpt.${key}`) : '';
        return translated && translated !== `api.chatgpt.${key}` ? translated : fallback;
    };
    const api = async (path, body) => {
        const response = await fetch(`/api/config/chatgpt/${path}`, {
            method: body === undefined ? 'GET' : 'POST', cache: 'no-store',
            headers: { 'Content-Type': 'application/json', 'X-Neko-ChatGPT': '1' },
            ...(body === undefined ? {} : { body: JSON.stringify(body) })
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? t(data.detail, data.detail) : t('failed', 'ChatGPT connection failed.'));
        return data;
    };
    const invalidate = () => {
        if (typeof invalidateModelLists === 'function') invalidateModelLists();
    };
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
        } else if (subscription.enabled && !pending && !data.error && data.login_status !== 'expired') {
            status.textContent = t('enabled', 'ChatGPT subscription is enabled and saved. Model: {{model}}').replace('{{model}}', subscription.model);
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
    function report(error) { status.textContent = error.message; }
    async function run(action) {
        if (busy) return;
        busy = true;
        login.disabled = add.disabled = logout.disabled = account.disabled = enabled.disabled = true;
        try { await action(); }
        catch (error) { report(error); }
        finally {
            busy = false;
            login.disabled = add.disabled = account.disabled = pending;
            logout.disabled = pending || !account.value;
            enabled.disabled = pending || (!connected && !subscription.enabled);
        }
    }
    const reloadSettings = async () => {
        if (typeof loadCurrentApiKey === 'function') await loadCurrentApiKey();
    };
    enabled.addEventListener('change', () => {
        const requested = enabled.checked;
        enabled.checked = subscription.enabled;
        return run(async () => {
            try {
                const current = await api('subscription');
                const slots = current.overwritten.map(slot => {
                    const key = slot === 'agent' ? 'api.agentApiConfigTitle' : `api.${slot}ModelConfig`;
                    const label = window.t ? window.t(key) : '';
                    return label && label !== key ? label : slot;
                }).join(', ');
                const message = requested
                    ? t('confirmEnable', 'Enable ChatGPT subscription and save? Existing slots to replace: {{slots}}. Unsaved edits on this page will be discarded.').replace('{{slots}}', slots || t('none', 'None'))
                    : t('confirmDisable', 'Restore the configuration saved before enabling the subscription? Unsaved edits on this page will be discarded.');
                if (!window.confirm(message)) return;
                const result = await api('subscription', { enabled: requested, revision: current.revision });
                await reloadSettings();
                if (!result.success) throw new Error(result.error || t('failed', 'ChatGPT connection failed.'));
            } finally {
                // Reconcile after a timeout too: persistence may already have completed.
                await refresh();
            }
        });
    });
    const signIn = accountId => run(async () => {
        if (pending) return;
        ++refreshGeneration;
        clearTimeout(timer);
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
            if (pending && !suspended) timer = setTimeout(refresh, 1500);
            throw error;
        }
    });
    login.addEventListener('click', () => signIn(account.value));
    add.addEventListener('click', () => signIn(null));
    account.addEventListener('change', () => run(async () => {
        await api('select', { account_id: account.value });
        invalidate();
        await refresh();
    }));
    logout.addEventListener('click', () => run(async () => {
        const result = await api('logout', { account_id: account.value });
        invalidate();
        if (result.subscription_restored) await reloadSettings();
        await refresh();
        if (!result.revocation_confirmed) status.textContent = t('revocationUnconfirmed', 'Signed out locally. Disconnect the app in ChatGPT Settings to confirm remote revocation.');
    }));
    window.addEventListener('localechange', () => refresh().catch(report));
    window.addEventListener('pagehide', () => {
        suspended = true;
        ++refreshGeneration;
        clearTimeout(timer);
    });
    window.addEventListener('pageshow', () => {
        suspended = false;
        refresh().catch(report);
    });
    refresh().catch(report);
})();
