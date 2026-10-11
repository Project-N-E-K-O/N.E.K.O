'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const http = require('node:http');
const { createPageDiagnostics, closeTestServer, removeTestProfile } = require('./remote_voice_page_diagnostics.cjs');

const errorArgs = [{ type: 'string', value: 'controlled error' }];
const timeoutArgs = [{ type: 'string', value: 'Preview error:' },
    { type: 'object', className: 'DOMException', description: 'AbortError: controlled timeout' }];
const matchesTimeout = args => args.length === 2 && args[0].value === 'Preview error:' &&
    args[1].className === 'DOMException' && args[1].description?.startsWith('AbortError:');

function fixture(t) {
    const diagnostics = createPageDiagnostics('console-guard-' + process.pid + '-' + t.name.replace(/\W/g, '-'));
    t.after(() => {
        const target = path.resolve(diagnostics.directory);
        const root = path.resolve(process.env.NEKO_TEST_ARTIFACT_DIR || os.tmpdir());
        assert.equal(path.dirname(target), root);
        assert.match(path.basename(target), /^(?:neko-remote-voice-)?console-guard-/);
        fs.rmSync(target, { recursive: true, force: true });
    });
    return diagnostics;
}

test('console.error without a thrown exception fails the persisted summary', t => {
    const diagnostics = fixture(t);
    diagnostics.consoleError(errorArgs);
    assert.throws(() => diagnostics.assertClean(), /Unexpected renderer/);
    diagnostics.finish({});
    const summary = JSON.parse(fs.readFileSync(path.join(diagnostics.directory, 'summary.json')));
    assert.equal(summary.passed, false);
    assert.match(summary.errors[0], /controlled error/);
});

test('empty console.error calls also fail', t => {
    const diagnostics = fixture(t);
    diagnostics.consoleError([]);
    assert.throws(() => diagnostics.assertClean());
});

test('one explicitly expected typed timeout error is accepted', t => {
    const diagnostics = fixture(t);
    const verify = diagnostics.expectConsoleError(matchesTimeout);
    diagnostics.consoleError(timeoutArgs);
    verify();
    diagnostics.assertClean();
});

test('an expectation cannot swallow duplicate errors', t => {
    const diagnostics = fixture(t);
    const verify = diagnostics.expectConsoleError(matchesTimeout);
    diagnostics.consoleError(timeoutArgs);
    diagnostics.consoleError(timeoutArgs);
    verify();
    assert.throws(() => diagnostics.assertClean());
});

test('an unrelated console.error still fails while a timeout is expected', t => {
    const diagnostics = fixture(t);
    const verify = diagnostics.expectConsoleError(matchesTimeout);
    diagnostics.consoleError(errorArgs);
    diagnostics.consoleError(timeoutArgs);
    verify();
    assert.throws(() => diagnostics.assertClean());
});

test('matching text without the expected error type is rejected', t => {
    const diagnostics = fixture(t);
    const verify = diagnostics.expectConsoleError(matchesTimeout);
    diagnostics.consoleError([{ type: 'string', value: 'Preview error:' },
        { type: 'object', className: 'Error', description: 'AbortError: controlled timeout' }]);
    assert.throws(verify, /was not observed/);
    assert.throws(() => diagnostics.assertClean());
});

test('missing expected errors cannot produce a passing summary', t => {
    const diagnostics = fixture(t);
    const verify = diagnostics.expectConsoleError(matchesTimeout);
    assert.throws(() => diagnostics.assertClean(), /still pending/);
    diagnostics.finish({});
    assert.equal(JSON.parse(fs.readFileSync(path.join(diagnostics.directory, 'summary.json'))).passed, false);
    assert.throws(verify, /was not observed/);
});

test('errors after an expectation closes are not ignored', t => {
    const diagnostics = fixture(t);
    const verify = diagnostics.expectConsoleError(matchesTimeout);
    diagnostics.consoleError(timeoutArgs);
    verify();
    diagnostics.assertClean();
    diagnostics.consoleError(timeoutArgs);
    assert.throws(() => diagnostics.assertClean());
});

test('a console error during cleanup invalidates an earlier clean check', t => {
    const diagnostics = fixture(t);
    diagnostics.assertClean();
    diagnostics.consoleError(errorArgs);
    assert.throws(() => diagnostics.assertClean());
    diagnostics.finish({});
    assert.equal(JSON.parse(fs.readFileSync(path.join(diagnostics.directory, 'summary.json'))).passed, false);
});

test('ordinary log and network diagnostics remain available as artifacts', t => {
    const diagnostics = fixture(t);
    diagnostics.log('info', 'controlled log');
    diagnostics.log('error', 'controlled HTTP error');
    diagnostics.assertClean();
    diagnostics.finish({});
    const logs = JSON.parse(fs.readFileSync(path.join(diagnostics.directory, 'console.json')));
    assert.equal(logs.length, 2);
});

test('HTTP cleanup closes a stalled real response body before joining the server', async t => {
    const server = http.createServer((_request, response) => {
        response.writeHead(200, { 'Content-Type': 'text/plain' });
        response.write('partial body');
    });
    t.after(() => closeTestServer(server));
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const response = await fetch('http://127.0.0.1:' + server.address().port);
    const body = response.text();
    const rejectedBody = assert.rejects(body);
    await closeTestServer(server);
    await rejectedBody;
    assert.equal(server.listening, false);
    await closeTestServer(server);
});

test('profile cleanup removes only a directly owned temporary profile', () => {
    const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-remote-voice-profile-'));
    fs.writeFileSync(path.join(profile, 'owned.txt'), 'controlled');
    removeTestProfile(profile);
    assert.equal(fs.existsSync(profile), false);
    assert.throws(() => removeTestProfile(path.join(os.tmpdir(), 'unowned-profile')), /unexpected test profile/);
    assert.throws(() => removeTestProfile(path.join(os.tmpdir(), 'nested', 'neko-remote-voice-profile-fake')), /unexpected test profile/);
});
