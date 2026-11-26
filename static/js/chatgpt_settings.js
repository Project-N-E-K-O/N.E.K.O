/* Copyright 2025-2026 Project N.E.K.O. Team; SPDX-License-Identifier: Apache-2.0 */
// ChatGPT account controls inside the Subscriptions & plans panel: sign in, add,
// switch and sign out accounts. Which features use ChatGPT is chosen on its card
// (static/js/subscription_settings.js), which this script tells about account changes.
(() => {
    'use strict';
    const account = document.getElementById('chatgpt-account');
    if (!account) return;
    const status = document.getElementById('chatgpt-status');
    const login = document.getElementById('chatgpt-login');
    const add = document.getElementById('chatgpt-add');
    const logout = document.getElementById('chatgpt-logout');
    const authorization = document.getElementById('chatgpt-authorization');
    let timer;
    let suspended = false;
    let refreshGeneration = 0;
    let busy = false;
    let pending = false;
    // A signed-out saved account the user picked: it cannot become active until it
    // signs in again, so it stays selected for "Continue with ChatGPT".
    let reauthorizeAccount = '';
    let knownAccounts = [];
    let modelAccountState;
    const t = (key, fallback) => {
        const translated = window.t ? window.t(`api.chatgpt.${key}`) : '';
        return translated && translated !== `api.chatgpt.${key}` ? translated : fallback;
    };
    // One deadline covers the request and its body, so a stalled response
    // cannot keep the panel's buttons disabled forever.
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
    // Another account means another model catalog and other connectivity results.
    const invalidate = () => {
        if (typeof invalidateModelLists === 'function') invalidateModelLists();
        if (typeof ConnectivityManager !== 'undefined' && typeof ConnectivityManager.invalidateChatGPTAccount === 'function') {
            ConnectivityManager.invalidateChatGPTAccount();
        }
        // The ChatGPT card lists this account's models and can only be switched on while signed in.
        if (typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
            window.dispatchEvent(new CustomEvent('neko:chatgpt-account-changed'));
        }
    };
    async function refresh() {
        clearTimeout(timer);
        if (suspended) return;
        const generation = ++refreshGeneration;
        let data;
        try {
            data = await api('status');
        } catch (error) {
            if (suspended || generation !== refreshGeneration) return;
            // A failed initial/post-login poll must recover too: the server may
            // already have accepted a login even before we observed pending.
            report(error);
            timer = setTimeout(refresh, 1500);
            return;
        }
        if (suspended || generation !== refreshGeneration) return;
        const connected = data.accounts.some(entry => entry.id === data.active && entry.connected !== false);
        const previous = account.value;
        knownAccounts = data.accounts;
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
        // A sign-in that just succeeded with another account active (e.g. "Add account")
        // ends the pending re-authorization. A failed or expired retry keeps the choice.
        if (pending && data.login_status === 'connected' && data.active && data.active !== reauthorizeAccount) {
            reauthorizeAccount = '';
        }
        const reauthorizable = data.accounts.some(a => a.id === reauthorizeAccount && a.connected === false);
        if (!reauthorizable) reauthorizeAccount = '';
        account.value = reauthorizeAccount || data.active
            || (data.accounts.some(a => a.id === previous) ? previous : account.options[0].value);
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
            connected ? t('connected', 'ChatGPT connected. Tick the features that should use it below, pick their models and switch the subscription on.')
                : t('signedOut', 'ChatGPT is not connected.'));
        if (pending) timer = setTimeout(() => refresh().catch(report), 1500);
        else {
            const nextModelAccountState = JSON.stringify([data.active, connected]);
            if (finishedLogin || nextModelAccountState !== modelAccountState) invalidate();
            modelAccountState = nextModelAccountState;
        }
        return data;
    }
    function report(error) { status.textContent = error.message; }
    async function run(action) {
        if (busy) return;
        busy = true;
        ++refreshGeneration;
        clearTimeout(timer);
        login.disabled = add.disabled = logout.disabled = account.disabled = true;
        try { await action(); }
        catch (error) { report(error); }
        finally {
            busy = false;
            login.disabled = add.disabled = account.disabled = pending;
            logout.disabled = pending || !account.value;
        }
    }
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
    add.addEventListener('click', () => {
        // Adding another account is not re-authorizing the signed-out one chosen.
        reauthorizeAccount = '';
        return signIn(null);
    });
    account.addEventListener('change', () => run(async () => {
        if (knownAccounts.some(entry => entry.id === account.value && entry.connected === false)) {
            // Selecting it would be refused; keep it chosen so Continue with ChatGPT signs it in again.
            reauthorizeAccount = account.value;
            await refresh();
            status.textContent = t('signInRequired', 'Sign in to ChatGPT first.');
            return;
        }
        reauthorizeAccount = '';
        invalidate();
        try { await api('select', { account_id: account.value }); }
        // Again after the switch: a test started before it must not land even if refresh fails.
        finally { invalidate(); await refresh(); }
    }));
    logout.addEventListener('click', () => run(async () => {
        invalidate();
        let result;
        try { result = await api('logout', { account_id: account.value }); }
        finally { await refresh(); }
        const notes = [];
        if (!result.revocation_confirmed) {
            notes.push(t('revocationUnconfirmed', 'Signed out locally. Disconnect the app in ChatGPT Settings to confirm remote revocation.'));
        }
        if (result.subscription_turned_off) {
            notes.push(t('subscriptionTurnedOff', 'No ChatGPT account is signed in, so the ChatGPT subscription is off and its features use your own settings again.'));
        }
        if (notes.length) status.textContent = notes.join(' ');
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
