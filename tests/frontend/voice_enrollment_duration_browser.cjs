'use strict';

// Run: node tests/frontend/voice_enrollment_duration_browser.cjs
// Requires Playwright and its Chromium browser. The page, media capture and
// AudioWorklet are real; the input WAV and HTTP API are controlled test inputs.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const sampleRate = 48000;
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-3347-browser-'));
const quietWav = fs.readFileSync(path.join(root, 'tests/fixtures/voice_identity/issue_3347/rate4.wav'));
assert.equal(quietWav.readUInt32LE(24), sampleRate);
for (let offset = 44; offset < quietWav.length; offset += 2) {
    quietWav.writeInt16LE(Math.round(quietWav.readInt16LE(offset) * 0.25), offset);
}
const inputPath = path.join(scratch, 'quiet-rate4.wav');
fs.writeFileSync(inputPath, quietWav);
const requests = [];
let enrollmentId = null;
let nextSegment = 1;

function status() {
    return {
        has_profile: false, requested_enabled: false, effective_enabled: false,
        runtime_mode: 'enforce', effective_reason: enrollmentId ? 'enrollment_active' : 'no_profile',
        enrollment: enrollmentId ? {
            enrollment_id: enrollmentId, next_segment_index: nextSegment,
            expires_at: Date.now() / 1000 + 45, remaining_seconds: 45,
        } : null,
    };
}

function json(response, payload, code = 200) {
    response.writeHead(code, { 'Content-Type': 'application/json' });
    response.end(JSON.stringify(payload));
}

function audioSummary(pcm) {
    let activeFrames = 0;
    let peak = 0;
    for (let start = 0; start + 960 <= pcm.length; start += 960) {
        let power = 0;
        for (let offset = start; offset < start + 960; offset += 2) {
            const sample = pcm.readInt16LE(offset) / 32768;
            power += sample * sample;
            peak = Math.max(peak, Math.abs(sample));
        }
        if (Math.sqrt(power / 480) >= 0.008) activeFrames += 1;
    }
    return { bytes: pcm.length, rmsActiveSeconds: activeFrames / 100, peak };
}

const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://127.0.0.1');
    const entry = { path: url.pathname, method: request.method };
    requests.push(entry);
    if (url.pathname === '/api/config/page_config') return json(response, { autostart_csrf_token: 'controlled-browser' });
    if (url.pathname === '/api/config/steam_language') return json(response, { ui_language: 'zh-CN' });
    if (url.pathname === '/api/voice-identity/status') return json(response, status());
    if (url.pathname === '/api/voice-identity/resources') return json(response, { can_enroll: true, wake_enabled: false, resources: { campp: { state: 'ready' }, silero: { state: 'ready' }, noise_reduction: { state: 'ready' }, wake_model: { state: 'missing' }, wake_runtime: { state: 'missing' } } });
    if (url.pathname === '/api/voice-identity/audio/check/isolation') return json(response, { token: 'controlled-browser-ticket', ttl_seconds: 60 });
    if (url.pathname === '/api/voice-identity/audio/check/isolation/release') return json(response, { released: true });
    if (url.pathname === '/api/voice-identity/enrollment/cancel') {
        enrollmentId = null;
        return json(response, status());
    }
    if (url.pathname === '/api/voice-identity/audio/check' || url.pathname === '/api/voice-identity/enrollment/segment') {
        const chunks = [];
        request.on('data', chunk => chunks.push(chunk));
        return request.on('end', () => {
            const pcm = Buffer.concat(chunks);
            Object.assign(entry, audioSummary(pcm));
            pcm.fill(0);
            chunks.forEach(chunk => chunk.fill(0));
            if (url.pathname.endsWith('/audio/check')) {
                entry.ticket = request.headers['x-voice-input-check'];
                return json(response, { accepted: true, audio_contract: { revision: 1, noise_reduction_enabled: false } });
            }
            entry.segment = Number(request.headers['x-voice-identity-segment']);
            if (entry.segment === 4) return json(response, { error_code: 'no_speech_detected' }, 422);
            nextSegment = entry.segment + 1;
            return json(response, status());
        });
    }
    if (url.pathname === '/api/voice-identity/enrollment/start') {
        let body = '';
        request.on('data', chunk => { body += chunk; });
        return request.on('end', () => {
            entry.body = JSON.parse(body);
            enrollmentId = 'controlled-browser-enrollment';
            nextSegment = 1;
            json(response, status());
        });
    }
    if (url.pathname === '/voice_identity') {
        response.writeHead(200, { 'Content-Type': 'text/html;charset=utf-8' });
        return response.end(fs.readFileSync(path.join(root, 'templates/voice_identity.html'), 'utf8').replaceAll('{{ static_asset_version }}', 'issue-3347-browser'));
    }
    if (url.pathname.startsWith('/static/')) {
        const filename = path.resolve(root, '.' + decodeURIComponent(url.pathname));
        if (!filename.startsWith(path.join(root, 'static') + path.sep)) {
            response.writeHead(403); return response.end();
        }
        try {
            const data = fs.readFileSync(filename);
            response.writeHead(200, { 'Content-Type': { '.js': 'application/javascript', '.json': 'application/json', '.css': 'text/css', '.svg': 'image/svg+xml' }[path.extname(filename)] || 'application/octet-stream' });
            return response.end(data);
        } catch (_) {}
    }
    response.writeHead(404); response.end('{}');
});

async function main() {
    let browser;
    try {
        await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
        browser = await chromium.launch({ headless: true,
            ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH } : {}),
            args: [
            '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
            '--use-file-for-fake-audio-capture=' + inputPath,
            '--autoplay-policy=no-user-gesture-required',
        ] });
        const page = await browser.newPage({
            locale: process.env.VOICE_ENROLLMENT_BROWSER_LOCALE || 'en-US',
            viewport: { width: 960, height: 900 },
        });
        const pageErrors = [];
        page.on('pageerror', error => pageErrors.push(error.message));
        await page.addInitScript(() => {
            // Observe native objects and message traffic without replacing the
            // browser's input, resampling, audio processing or capture methods.
            const observed = window.__durationEvidence = { streams: [], contexts: [], captures: [] };
            const getUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async constraints => {
                const stream = await getUserMedia(constraints);
                observed.streams.push(stream);
                return stream;
            };
            const NativeContext = window.AudioContext;
            window.AudioContext = class extends NativeContext {
                constructor(options) { super(options); observed.contexts.push(this); }
            };
            const NativeWorklet = window.AudioWorkletNode;
            window.AudioWorkletNode = class extends NativeWorklet {
                constructor(...args) {
                    super(...args);
                    const record = { samples: 0, flushed: false };
                    observed.captures.push(record);
                    this.port.addEventListener('message', event => {
                        const data = event.data;
                        if (data && data.type === 'flush_complete') {
                            record.samples += data.pcmData ? data.pcmData.length : 0;
                            record.flushed = true;
                        } else if (data instanceof Int16Array) record.samples += data.length;
                    });
                    this.port.start();
                }
            };
        });
        await page.goto(`http://127.0.0.1:${server.address().port}/voice_identity`);
        await page.waitForFunction(() => typeof window.t === 'function'
            && window.t('voiceIdentity.errorNoSpeechDetected') !== 'voiceIdentity.errorNoSpeechDetected');
        await page.waitForFunction(() => !document.getElementById('voice-identity-test').disabled);
        assert.equal(await page.locator('#voice-identity-start').isDisabled(), true);
        await page.locator('#voice-identity-test').click();
        await page.waitForFunction(() => !document.getElementById('voice-identity-start').disabled);
        const check = requests.find(entry => entry.path.endsWith('/audio/check'));
        assert.equal(check.bytes, sampleRate * 3 * 2);
        assert.equal(check.ticket, 'controlled-browser-ticket');
        assert.ok(check.peak > 0);
        assert.ok(check.rmsActiveSeconds < 1.5);
        assert.equal(await page.evaluate(() => window.__durationEvidence.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended'))), true);
        await page.locator('#voice-identity-start').click();
        await page.waitForFunction(() => window.__durationEvidence.captures.length === 2 && window.__durationEvidence.captures[1].samples >= 24000);
        const earlySamples = await page.evaluate(() => {
            const count = window.__durationEvidence.captures[1].samples;
            document.getElementById('voice-identity-finish').click();
            return count;
        });
        assert.ok(earlySamples < sampleRate * 1.5);
        assert.match(await page.locator('#voice-identity-message').textContent(), /1\.5/);
        assert.equal(requests.some(entry => entry.path.endsWith('/enrollment/segment')), false);
        const manualCaptures = [];
        for (let segment = 1; segment <= 4; segment += 1) {
            await page.waitForFunction(index => window.__durationEvidence.captures.length > index && window.__durationEvidence.captures[index].samples >= 96000, segment);
            const samplesAtClick = await page.evaluate(index => {
                const count = window.__durationEvidence.captures[index].samples;
                document.getElementById('voice-identity-finish').click();
                return count;
            }, segment);
            assert.ok(samplesAtClick >= sampleRate * 1.5);
            assert.ok(samplesAtClick < sampleRate * (segment === 4 ? 5 : 3), 'manual save must precede automatic ending');
            await page.waitForFunction(index => window.__durationEvidence.captures[index].flushed, segment);
            if (segment < 4) await page.waitForFunction(() => !document.getElementById('voice-identity-next').hidden && !document.getElementById('voice-identity-next').disabled);
            else await page.waitForFunction(() => document.getElementById('voice-identity-message').textContent
                === window.t('voiceIdentity.errorNoSpeechDetected'));
            const captured = await page.evaluate(index => ({ ...window.__durationEvidence.captures[index] }), segment);
            const upload = requests.filter(entry => entry.path.endsWith('/enrollment/segment'))[segment - 1];
            assert.equal(upload.segment, segment);
            assert.equal(upload.bytes, sampleRate * (segment === 4 ? 5 : 3) * 2);
            assert.ok(upload.peak > 0);
            assert.ok(upload.rmsActiveSeconds < 1.5);
            manualCaptures.push({ samplesAtClick, actualSamplesBeforePadding: captured.samples, ...upload });
            if (segment < 4) await page.locator('#voice-identity-next').click();
        }
        await page.locator('#voice-identity-cancel').click();
        await page.waitForFunction(() => window.__durationEvidence.streams.every(stream => stream.getTracks().every(track => track.readyState === 'ended')) && window.__durationEvidence.contexts.every(context => context.state === 'closed'));
        assert.equal(enrollmentId, null);
        assert.deepEqual(pageErrors, []);
        const report = {
            browser: await browser.version(), actualPageAndWorklet: true,
            locale: await page.evaluate(() => navigator.language),
            controlledApi: true, realBackend: false, realMicrophoneCaptured: false,
            fixture: 'rate4.wav', amplitude: 0.25, earlySamples,
            earlySubmissionBlocked: true, trial: check, manualCaptures,
            referencesHaveThreeSecondShape: true, verificationHasFiveSecondShape: true,
            rmsDurationDoesNotGateSubmission: true, cancelledResourcesReleased: true,
            finalVerificationIntentionallyRejectedByControlledApi: true,
        };
        fs.writeFileSync(path.join(scratch, 'result.json'), JSON.stringify(report, null, 2));
        await page.screenshot({ path: path.join(scratch, 'cancelled.png') });
        console.log('VOICE_ENROLLMENT_DURATION_BROWSER ' + JSON.stringify(report));
        console.log('VOICE_ENROLLMENT_DURATION_BROWSER_ARTIFACTS ' + scratch);
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

main().catch(error => { console.error(error.stack); process.exitCode = 1; });
