'use strict';
// Standalone Chromium fallback when the desktop Browser plugin has no browser.
const { chromium } = require(process.env.NEKO_TEST_PLAYWRIGHT_MODULE || 'playwright');
const { createVoiceManagerServer } = require('./remote_voice_manager_server.cjs');
const { verifyVoiceRaces } = require('./remote_voice_manager_races.cjs');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
(async () => {
    const { server, state } = createVoiceManagerServer();
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const channel = process.env.NEKO_TEST_BROWSER_CHANNEL;
    let browser;
    const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-remote-voice-web-'));
    try {
        browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
        const context = await browser.newContext({ viewport: { width: 1120, height: 850 } });
        const page = await context.newPage();
        const errors = []; page.on('pageerror', error => errors.push(error.message));
        await page.goto('http://127.0.0.1:' + server.address().port + '/voice_clone?lanlan_name=Test');
        await page.waitForFunction(() => document.getElementById('voiceProvider').value === 'cosyvoice' && !document.getElementById('importExistingVoice').hidden && !document.getElementById('importExistingVoice').disabled);
        await page.locator('#importExistingVoice').click();
        await page.locator('.remote-voice-table tbody tr').first().waitFor();
        await page.waitForFunction(() => !!window.pageTutorialManager._modalTutorialWaitCleanup);
        assert.equal(await page.evaluate(() => window.pageTutorialManager.isTutorialRunning), false);
        assert.equal(await page.locator('.driver-popover').count(), 0);
        state.remotePages = [[{ voice_id: 'unrelated', name: 'Unrelated' }], [{ voice_id: 'TargetLater', name: 'Target in later page', status: 'ready' }]];
        await page.locator('.remote-voice-toolbar input[type=search]').fill('Target');
        await page.getByText('Target in later page', { exact: true }).waitFor();
        assert.ok(state.listQueries.some(item => item.query === 'Target' && item.cursor === '1'));
        assert.equal(state.imports.length, 0);
        delete state.remotePages;
        await page.locator('.remote-voice-toolbar input[type=search]').fill('');
        await page.getByText('ExistingVoice123', { exact: true }).waitFor();
        const listScreenshot = path.join(scratch, 'list.png'); await page.screenshot({ path: listScreenshot });
        await page.locator('.remote-voice-table input[type=radio]').first().check();
        await page.getByRole('button', { name: '导入所选音色', exact: true }).click();
        await page.locator('[data-voice-id=voice_00000000000000000000000000000001]').waitFor();
        assert.equal(state.imports.length, 1); assert.equal(state.binding, '');
        assert.equal(await page.locator('[data-voice-id=voice_00000000000000000000000000000001] .voice-id').innerText(), 'ID: ExistingVoice123');
        assert.equal(await page.locator('[data-voice-id=voice_00000000000000000000000000000001] .voice-preview-btn').first().isDisabled(), false);
        await page.locator('.remote-voice-dialog').getByRole('button', { name: '关闭', exact: true }).click();
        await page.locator('#neko-page-tutorial-skip-btn').waitFor();
        await page.locator('#neko-page-tutorial-skip-btn').click();
        await page.waitForFunction(() => !window.pageTutorialManager.isTutorialRunning);
        await page.locator('#voiceProvider').selectOption('cosyvoice');
        await page.locator('#importExistingVoice').click();
        await page.getByRole('button', { name: '手动填写ID', exact: true }).click();
        await page.locator('input[name=remote_voice_id]').fill('CosyManual');
        await page.locator('input[name=display_name]').fill('网页手动音色');
        assert.equal(await page.locator('input[name=clone_model]').inputValue(), 'cosyvoice-v3-plus');
        await page.waitForFunction(() => !Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button => button.textContent === window.t('voice.remote.import')).disabled);
        const manualScreenshot = path.join(scratch, 'manual.png'); await page.screenshot({ path: manualScreenshot });
        await page.getByRole('button', { name: '导入', exact: true }).click();
        await page.locator('[data-voice-id=voice_00000000000000000000000000000002]').waitFor();
        assert.equal(state.imports[1].metadata.clone_model, 'cosyvoice-v3-plus');
        assert.equal(state.imports[1].display_name, '网页手动音色');
        assert.equal(state.binding, '');
        await page.keyboard.press('Escape');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'importExistingVoice');
        await page.setViewportSize({ width: 420, height: 820 });
        await page.locator('#importExistingVoice').click();
        await page.locator('.remote-voice-table tbody tr').first().waitFor();
        const bbox = await page.locator('.remote-voice-dialog').boundingBox();
        assert.ok(bbox.x >= 0 && bbox.x + bbox.width <= 420);
        await page.locator('.remote-voice-footer button').first().focus();
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(() => document.activeElement.classList.contains('remote-voice-close')), true);
        const screenshot = path.join(scratch, 'narrow.png'); await page.screenshot({ path: screenshot });
        await page.keyboard.press('Escape');
        await page.locator('#voiceProvider').selectOption('mimo');
        assert.equal(await page.locator('#importExistingVoice').isHidden(), true);
        const races = await verifyVoiceRaces({ run: code => page.evaluate(code),
            waitFor: expression => page.waitForFunction(expression), state });
        assert.deepEqual(errors, []);
        console.log(JSON.stringify({ browser: await browser.version(), actualProductAssets: true, controlledApiOnly: true,
            explicitImport: true, rawIdAndAvailablePreview: true, paginatedSearch: true, manualRequiredFields: true, noImplicitBinding: true,
            ...races,
            tutorialDeferredAndResumed: true, keyboardFocus: true, narrowViewport: true, unsupportedProviderHidden: true, listScreenshot, manualScreenshot, screenshot }, null, 2));
    } finally { if (browser) await browser.close(); await new Promise(resolve => server.close(resolve)); }
})().catch(error => { console.error(error); process.exitCode = 1; });
