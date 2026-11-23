'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '../..');
const SOURCE = fs.readFileSync(
    path.join(ROOT, 'static/js/character_card_manager/character-data-and-transfer.js'),
    'utf8'
);

function loadCharacterTransferModule({ fetchImpl, security, fastTimeoutMs = null } = {}) {
    const messages = [];
    const warnings = [];
    let refreshedListCount = 0;
    let forgottenRetryTarget = null;
    const openedMetaRefreshes = [];

    const windowObj = {
        _cardMetas: {},
        nekoLocalMutationSecurity: security,
        location: { origin: 'http://127.0.0.1:48911' },
        addEventListener() {},
        removeEventListener() {},
    };
    const context = {
        window: windowObj,
        document: {
            createElement() { return { style: {} }; },
            getElementById() { return null; },
        },
        AbortController,
        DOMException,
        fetch: fetchImpl,
        setTimeout: (fn, ms, ...args) => setTimeout(
            fn,
            fastTimeoutMs !== null && ms === 5000 ? fastTimeoutMs : ms,
            ...args
        ),
        clearTimeout,
        console: {
            log() {},
            warn(...args) { warnings.push(args.join(' ')); },
            error() {},
        },
        showMessage: (msg, kind) => {
            messages.push({ msg, kind });
            return { dismiss() {} };
        },
        refreshOpenCardMetaBlock: (name) => { openedMetaRefreshes.push(name); },
        isCharacterReservedFieldName: () => false,
    };
    vm.createContext(context);
    vm.runInContext(SOURCE, context);
    context.showMessage = (msg, kind) => {
        messages.push({ msg, kind });
        return { dismiss() {} };
    };
    context._moliliFileToBase64 = async () => 'ZmFrZQ==';
    context._showMoliliImportDialog = async () => ({ name: 'CatMolili', mode: 'create', masterOverwrite: false });
    context._moliliNameFitsMemory = () => true;
    context._moliliForgetRetryTarget = (preview) => { forgottenRetryTarget = preview; };
    context._moliliRefreshCardList = () => { refreshedListCount += 1; };
    context.refreshOpenCardMetaBlock = (name) => { openedMetaRefreshes.push(name); };
    return {
        context,
        windowObj,
        messages,
        warnings,
        getRefreshedListCount: () => refreshedListCount,
        getForgottenRetryTarget: () => forgottenRetryTarget,
        openedMetaRefreshes,
    };
}

test('PR3981 #56: hanging page_config during Molili origin update times out cleanly without blocking import', async () => {
    let abortObserved = false;
    const mod = loadCharacterTransferModule({
        fastTimeoutMs: 15,
        fetchImpl: async (url, init = {}) => {
            const pathName = String(url);
            if (pathName === '/api/memory/external_import/preview') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        candidate_count: 0,
                        character_card: { sections: [] },
                    }),
                };
            }
            if (pathName === '/api/characters/catgirl') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ success: true, character_name: 'CatMolili' }),
                };
            }
            if (pathName.endsWith('/card-meta') && (init.method || 'GET') === 'GET') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ meta: { origin: 'self' } }),
                };
            }
            if (pathName === '/api/config/page_config') {
                return await new Promise((_resolve, reject) => {
                    init.signal?.addEventListener('abort', () => {
                        abortObserved = true;
                        const err = new Error('aborted');
                        err.name = 'AbortError';
                        reject(err);
                    }, { once: true });
                });
            }
            throw new Error(`Unexpected fetch: ${pathName}`);
        },
    });

    await mod.context.handleMoliliArchiveImport({ size: 16 });

    assert.equal(abortObserved, true, 'page_config hang must be aborted when MOLILI_CARD_META_TIMEOUT_MS expires');
    assert.equal(mod.getRefreshedListCount(), 1, 'import completion must continue and refresh card list');
    assert.notEqual(mod.getForgottenRetryTarget(), null);
    assert.ok(mod.warnings.some((w) => w.includes('卡面来源标记失败')), 'warning should log non-blocking timeout');
    assert.equal(mod.windowObj._cardMetas.CatMolili, undefined);
});

test('PR3981 #56: timely Molili card-meta update updates _cardMetas badge and _charTransferMutationFetch ignores empty headers', async () => {
    let putCsrfHeader = null;
    const mod = loadCharacterTransferModule({
        security: {
            getMutationHeaders: async () => ({}),
        },
        fetchImpl: async (url, init = {}) => {
            const pathName = String(url);
            if (pathName === '/api/memory/external_import/preview') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({
                        success: true,
                        candidate_count: 0,
                        character_card: { sections: [] },
                    }),
                };
            }
            if (pathName === '/api/characters/catgirl') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ success: true, character_name: 'CatMolili' }),
                };
            }
            if (pathName.endsWith('/card-meta') && (init.method || 'GET') === 'GET') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ meta: { origin: 'self' } }),
                };
            }
            if (pathName === '/api/config/page_config') {
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ autostart_csrf_token: 'tok-molili' }),
                };
            }
            if (pathName.endsWith('/card-meta') && init.method === 'PUT') {
                putCsrfHeader = init.headers && init.headers['X-CSRF-Token'];
                return {
                    ok: true,
                    status: 200,
                    json: async () => ({ meta: { origin: 'molili' } }),
                };
            }
            throw new Error(`Unexpected fetch: ${pathName}`);
        },
    });

    await mod.context.handleMoliliArchiveImport({ size: 16 });

    assert.equal(putCsrfHeader, 'tok-molili', 'must fall back to /api/config/page_config when security headers lack X-CSRF-Token');
    assert.deepEqual(mod.windowObj._cardMetas.CatMolili, { origin: 'molili' });
    assert.deepEqual(mod.openedMetaRefreshes, ['CatMolili']);

    // Direct _charTransferMutationFetch timeoutMs also aborts even if security.getMutationHeaders hangs.
    const hangingSecurityMod = loadCharacterTransferModule({
        security: {
            getMutationHeaders: () => new Promise(() => {}),
        },
        fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({}) }),
    });
    await assert.rejects(
        () => hangingSecurityMod.context._charTransferMutationFetch('/api/characters/catgirl/X/card-meta', {
            method: 'PUT',
            timeoutMs: 15,
        }),
        (err) => err && err.name === 'AbortError'
    );
});

test('PR3981 #59: _charTransferMutationFetch keeps timeoutMs and external AbortSignal active across 403 retry', async () => {
    // 1) timeoutMs remains armed when 403 csrf_validation_failed triggers send(true) and refresh hangs.
    let pageConfigCount = 0;
    const retryTimeoutMod = loadCharacterTransferModule({
        fetchImpl: async (url) => {
            const pathName = String(url);
            if (pathName === '/api/config/page_config') {
                pageConfigCount += 1;
                if (pageConfigCount === 1) {
                    return { ok: true, status: 200, json: async () => ({ autostart_csrf_token: 'tok-1' }) };
                }
                return new Promise(() => {});
            }
            return {
                ok: false,
                status: 403,
                json: async () => ({ error_code: 'csrf_validation_failed' }),
                clone() { return { json: async () => ({ error_code: 'csrf_validation_failed' }) }; },
            };
        },
    });
    await assert.rejects(
        () => retryTimeoutMod.context._charTransferMutationFetch('/api/characters/catgirl/X/card-meta', {
            method: 'PUT',
            timeoutMs: 25,
        }),
        (err) => err && err.name === 'AbortError'
    );

    // 2) External AbortSignal listener stays attached across 403 csrf_validation_failed retry.
    let retryPageConfigStartedResolve = null;
    const retryPageConfigStarted = new Promise((resolve) => { retryPageConfigStartedResolve = resolve; });
    let extPageConfigCount = 0;
    const externalController = new AbortController();
    const externalAbortMod = loadCharacterTransferModule({
        fetchImpl: async (url) => {
            const pathName = String(url);
            if (pathName === '/api/config/page_config') {
                extPageConfigCount += 1;
                if (extPageConfigCount === 1) {
                    return { ok: true, status: 200, json: async () => ({ autostart_csrf_token: 'tok-ext-1' }) };
                }
                retryPageConfigStartedResolve();
                return new Promise(() => {});
            }
            return {
                ok: false,
                status: 403,
                json: async () => ({ error_code: 'csrf_validation_failed' }),
                clone() { return { json: async () => ({ error_code: 'csrf_validation_failed' }) }; },
            };
        },
    });
    const retryPromise = externalAbortMod.context._charTransferMutationFetch('/api/characters/catgirl/X/card-meta', {
        method: 'PUT',
        signal: externalController.signal,
        timeoutMs: 5000,
    });
    await retryPageConfigStarted;
    externalController.abort();
    await assert.rejects(
        () => retryPromise,
        (err) => err && err.name === 'AbortError'
    );
});
