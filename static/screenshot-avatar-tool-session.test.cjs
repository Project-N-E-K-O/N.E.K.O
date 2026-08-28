const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, 'app/app-buttons.js'), 'utf8');

function extractFunction(name) {
    const marker = `function ${name}(`;
    let start = source.indexOf(marker);
    assert.notEqual(start, -1, `missing ${name}`);
    if (source.slice(Math.max(0, start - 6), start) === 'async ') {
        start -= 6;
    }
    const bodyStart = source.indexOf('{', start);
    let depth = 0;
    for (let index = bodyStart; index < source.length; index += 1) {
        if (source[index] === '{') depth += 1;
        if (source[index] === '}') {
            depth -= 1;
            if (depth === 0) return source.slice(start, index + 1);
        }
    }
    throw new AssertionError(`unbalanced ${name}`);
}

function createScreenshotAuthHarness(cryptoApi) {
    const context = {
        Uint8Array,
        console: { warn() {} },
        window: {
            crypto: cryptoApi,
            navigator: { userActivation: { isActive: false } },
        },
    };
    vm.runInNewContext(`
        const SCREENSHOT_PROXY_GUARD_MARKER = '__nekoScreenshotProxyGuarded__';
        let trustedScreenshotCaptureToken = '';
        ${extractFunction('hasTrustedScreenshotActivation')}
        ${extractFunction('mintTrustedScreenshotCaptureToken')}
        ${extractFunction('hasTrustedScreenshotCaptureToken')}
        ${extractFunction('consumeTrustedScreenshotCaptureToken')}
        ${extractFunction('wrapScreenshotProxy')}
        globalThis.auth = {
            hasTrustedScreenshotActivation,
            mintTrustedScreenshotCaptureToken,
            consumeTrustedScreenshotCaptureToken,
            wrapScreenshotProxy,
        };
    `, context);
    return context.auth;
}

test('desktop screenshot lifecycle brackets capture and crop with an avatar-tool suspension session', () => {
    const start = source.indexOf('mod.captureScreenshotDataUrl = async function captureScreenshotDataUrl(token)');
    const end = source.indexOf('window.captureScreenshotDataUrl = mod.captureScreenshotDataUrl', start);
    assert.notEqual(start, -1);
    assert.notEqual(end, -1);
    const section = source.slice(start, end);

    assert.match(source, /new CustomEvent\('neko:screenshot-capture-session'/);
    assert.match(section, /SCREENSHOT_AUTH_REQUIRED/);
    assert.match(section, /if \(!U\.isMobile\(\)\) \{[\s\S]*?setScreenshotCaptureSessionActive\(true\);/);
    assert.match(section, /finally \{[\s\S]*?setScreenshotCaptureSessionActive\(false\);[\s\S]*?_captureScreenshotDataUrlBusy = false;/);
});

test('screenshot authorization requires a trusted activation and a Web Crypto token', () => {
    let fillCount = 0;
    const auth = createScreenshotAuthHarness({
        getRandomValues(bytes) {
            fillCount += 1;
            bytes.fill(0xab);
            return bytes;
        },
    });

    assert.equal(auth.mintTrustedScreenshotCaptureToken({ isTrusted: false }), '');
    const token = auth.mintTrustedScreenshotCaptureToken({ isTrusted: true });
    assert.equal(fillCount, 1);
    assert.match(token, /^[0-9a-f]{64}$/);
    assert.equal(auth.consumeTrustedScreenshotCaptureToken(token), true);
    assert.equal(auth.consumeTrustedScreenshotCaptureToken(token), false);
});

test('screenshot capture rejects forged tokens before touching capture dependencies', async () => {
    const context = {
        console: { warn() {} },
        window: { navigator: { userActivation: { isActive: true } } },
    };
    vm.runInNewContext(`
        let trustedScreenshotCaptureToken = 'valid-token';
        ${extractFunction('hasTrustedScreenshotCaptureToken')}
        ${extractFunction('consumeTrustedScreenshotCaptureToken')}
        ${extractFunction('captureScreenshotDataUrl')}
        globalThis.capture = captureScreenshotDataUrl;
    `, context);

    await assert.rejects(context.capture('forged-token'), /SCREENSHOT_AUTH_REQUIRED/);
});

test('screenshot authorization rejects missing crypto and untrusted proxy requests', () => {
    const unavailable = createScreenshotAuthHarness(undefined);
    assert.equal(unavailable.mintTrustedScreenshotCaptureToken({ isTrusted: true }), '');

    let forwarded = 0;
    const auth = createScreenshotAuthHarness({
        getRandomValues(bytes) {
            bytes.fill(0xcd);
            return bytes;
        },
    });
    const token = auth.mintTrustedScreenshotCaptureToken({ isTrusted: true });
    const proxy = auth.wrapScreenshotProxy({
        request(receivedToken) {
            forwarded += 1;
            assert.equal(receivedToken, token);
            return 'forwarded';
        },
    });

    assert.equal(proxy.request('forged-token'), false);
    assert.equal(proxy.request(token), 'forwarded');
    assert.equal(forwarded, 1);
});
