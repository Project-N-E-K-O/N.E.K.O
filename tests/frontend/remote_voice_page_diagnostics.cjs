'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function createPageDiagnostics(runtime) {
    const directory = process.env.NEKO_TEST_ARTIFACT_DIR
        ? path.resolve(process.env.NEKO_TEST_ARTIFACT_DIR, runtime)
        : fs.mkdtempSync(path.join(os.tmpdir(), 'neko-remote-voice-' + runtime + '-'));
    fs.mkdirSync(directory, { recursive: true });
    const logs = [];
    const errors = [];
    const expectedConsoleErrors = new Set();
    return {
        directory,
        log(level, message) { logs.push({ level, message: String(message) }); },
        error(error) { errors.push(String(error.stack || error)); },
        consoleError(args) {
            const expected = Array.from(expectedConsoleErrors).find(item => item.matches(args));
            if (expected) {
                expected.observed = true;
                expectedConsoleErrors.delete(expected);
                return;
            }
            const message = args.map(arg => arg.description ?? arg.value ?? arg.unserializableValue ?? arg.type).join(' ');
            errors.push('Unexpected console.error: ' + message);
        },
        expectConsoleError(matches) {
            const expected = { matches, observed: false };
            expectedConsoleErrors.add(expected);
            return () => {
                expectedConsoleErrors.delete(expected);
                assert.equal(expected.observed, true, 'Expected console.error was not observed');
            };
        },
        assertClean() {
            assert.deepEqual(errors, [], 'Unexpected renderer exceptions, unhandled rejections or console errors');
            assert.equal(expectedConsoleErrors.size, 0, 'An expected console.error is still pending');
        },
        finish(result, error) {
            fs.writeFileSync(path.join(directory, 'console.json'), JSON.stringify(logs, null, 2));
            fs.writeFileSync(path.join(directory, 'summary.json'), JSON.stringify({ runtime, passed: !error && errors.length === 0 && expectedConsoleErrors.size === 0,
                ...result, errors, ...(error ? { failure: error.stack || String(error) } : {}) }, null, 2));
        }
    };
}

async function observeBrowserConsoleErrors(page, diagnostics) {
    // Console API calls exclude Chromium's network diagnostics for the controlled
    // HTTP failures exercised by these tests. The browser owns this CDP session.
    const session = await page.context().newCDPSession(page);
    session.on('Runtime.consoleAPICalled', event => {
        if (event.type === 'error') diagnostics.consoleError(event.args);
    });
    await session.send('Runtime.enable');
}

async function closeTestServer(server) {
    // Destroy active reads before joining the listener, including a stalled response body.
    server.closeAllConnections();
    if (server.listening) await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
}

function removeTestProfile(directory) {
    const target = path.resolve(directory);
    const temporaryRoot = path.resolve(os.tmpdir());
    if (path.dirname(target) !== temporaryRoot || !path.basename(target).startsWith('neko-remote-voice-profile-')) {
        throw new Error('Refusing to remove an unexpected test profile: ' + target);
    }
    fs.rmSync(target, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
}

module.exports = { createPageDiagnostics, observeBrowserConsoleErrors, closeTestServer, removeTestProfile };
