'use strict';
const { chromium } = require(process.env.NEKO_TEST_PLAYWRIGHT_MODULE || 'playwright');
const { createVoicePreviewServer } = require('./voice_preview_server.cjs');
const { verifyPreviewBodyRaces } = require('./voice_preview_races.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
(async () => {
    const transport = createVoicePreviewServer();
    await new Promise(resolve => transport.server.listen(0, '127.0.0.1', resolve));
    const scratch = process.env.NEKO_TEST_ARTIFACT_DIR || fs.mkdtempSync(path.join(os.tmpdir(), 'neko-preview-browser-'));
    fs.mkdirSync(scratch, { recursive: true });
    let browser, page;
    const errors = [], logs = [];
    try {
        const channel = process.env.NEKO_TEST_BROWSER_CHANNEL;
        browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
        page = await browser.newPage();
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => logs.push(message.type() + ': ' + message.text()));
        await page.goto('http://127.0.0.1:' + transport.server.address().port + '/voice_clone?lanlan_name=Test');
        await page.waitForFunction(() => typeof playPreview === 'function' && typeof window.t === 'function' && document.querySelector('[data-voice-id="preview-body"]'));
        const results = await verifyPreviewBodyRaces({ run: code => page.evaluate(code), transport, page: true });
        assert.deepEqual(errors, []);
        const summary = { browser: await browser.version(), actualProductTemplate: true, controlledHttpTransport: true, ...results };
        fs.writeFileSync(path.join(scratch, 'voice-preview-browser-summary.json'), JSON.stringify(summary, null, 2));
        console.log(JSON.stringify(summary));
    } catch (error) {
        fs.writeFileSync(path.join(scratch, 'voice-preview-browser-summary.json'), JSON.stringify({ success: false, error: error.stack || String(error) }, null, 2));
        if (page) await page.screenshot({ path: path.join(scratch, 'voice-preview-browser-failure.png') }).catch(() => {});
        throw error;
    } finally {
        fs.writeFileSync(path.join(scratch, 'voice-preview-browser-console.json'), JSON.stringify({ errors, logs }, null, 2));
        if (browser) await browser.close();
        await transport.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
