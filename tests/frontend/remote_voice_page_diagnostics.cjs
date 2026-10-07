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
    return {
        directory,
        log(level, message) { logs.push({ level, message: String(message) }); },
        error(error) { errors.push(String(error.stack || error)); },
        assertClean() { assert.deepEqual(errors, [], 'Unexpected renderer exceptions or unhandled rejections'); },
        finish(result, error) {
            fs.writeFileSync(path.join(directory, 'console.json'), JSON.stringify(logs, null, 2));
            fs.writeFileSync(path.join(directory, 'summary.json'), JSON.stringify({ runtime, passed: !error && errors.length === 0,
                ...result, errors, ...(error ? { failure: error.stack || String(error) } : {}) }, null, 2));
        }
    };
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

module.exports = { createPageDiagnostics, closeTestServer, removeTestProfile };
