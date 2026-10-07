'use strict';
// Standalone Chromium fallback when the desktop Browser plugin has no browser.
const { chromium } = require(process.env.NEKO_TEST_PLAYWRIGHT_MODULE || 'playwright');
const { createVoiceManagerServer } = require('./remote_voice_manager_server.cjs');
const { verifyVoiceRaces } = require('./remote_voice_manager_races.cjs');
const { verifyManagementSettings } = require('./remote_voice_management_settings.cjs');
const { createPageDiagnostics, closeTestServer } = require('./remote_voice_page_diagnostics.cjs');
const assert = require('node:assert/strict');
const path = require('node:path');
const diagnostics = createPageDiagnostics('chromium');
const { server, state } = createVoiceManagerServer();
let browser, page, result, finishing;
const scratch = diagnostics.directory;
function bounded(operation, milliseconds, label) {
    let timer;
    return Promise.race([operation, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(label + ' timed out')), milliseconds);
    })]).finally(() => clearTimeout(timer));
}
function finish(error) {
    const explicitFailure = arguments.length > 0;
    const cause = explicitFailure ? (error instanceof Error ? error : new Error(String(error))) : undefined;
    if (explicitFailure) diagnostics.error(cause);
    if (finishing) return finishing;
    finishing = (async () => {
        clearTimeout(watchdog);
        let failure = cause;
        if (cause && page) await bounded(page.screenshot({ path: path.join(scratch, 'failure.png'), timeout: 2000 }), 2000, 'Failure screenshot')
            .catch(captureError => diagnostics.log('screenshot-error', captureError));
        const cleanup = await Promise.allSettled([
            bounded(Promise.resolve().then(() => browser && browser.close()), 5000, 'Browser cleanup'),
            bounded(closeTestServer(server), 5000, 'HTTP cleanup')
        ]);
        for (const item of cleanup) if (item.status === 'rejected') {
            const cleanupError = item.reason instanceof Error ? item.reason : new Error(String(item.reason));
            diagnostics.error(cleanupError); failure ||= cleanupError;
        }
        await new Promise(resolve => setImmediate(resolve));
        try { diagnostics.assertClean(); }
        catch (lateError) { failure ||= lateError; }
        diagnostics.finish(result, failure);
        if (failure) console.error(failure);
        process.exitCode = failure ? 1 : 0;
        if (cleanup.some(item => item.status === 'rejected')) process.exit(1);
    })();
    return finishing;
}
const watchdog = setTimeout(() => { void finish(new Error('REMOTE_VOICE_CHROMIUM_TIMEOUT')); }, 60000);
process.on('unhandledRejection', error => { void finish(error); });
process.on('uncaughtException', error => { void finish(error); });
(async () => {
    await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
    const channel = process.env.NEKO_TEST_BROWSER_CHANNEL;
        browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
        if (finishing) { await bounded(browser.close(), 5000, 'Late browser cleanup'); return; }
        const context = await browser.newContext({ viewport: { width: 1120, height: 850 } });
        page = await context.newPage();
        page.on('pageerror', error => diagnostics.error(error));
        page.on('console', message => diagnostics.log(message.type(), message.text()));
        await page.goto('http://127.0.0.1:' + server.address().port + '/voice_clone?lanlan_name=Test');
        await page.waitForFunction(() => document.getElementById('voiceProvider').value === 'cosyvoice' && !document.getElementById('importExistingVoice').hidden && !document.getElementById('importExistingVoice').disabled);
        // Activate before the tutorial's delayed overlay can intercept the pointer.
        // The modal's real tutorial deferral and resumption are asserted below.
        await page.evaluate(() => document.getElementById('importExistingVoice').click());
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
        await page.goto('http://127.0.0.1:' + server.address().port + '/api_key');
        const managementSettings = await verifyManagementSettings({ run: code => page.evaluate(code),
            waitFor: expression => page.waitForFunction(expression), state });
        diagnostics.assertClean();
        result = { browser: await browser.version(), actualProductAssets: true, controlledApiOnly: true,
            explicitImport: true, rawIdAndAvailablePreview: true, paginatedSearch: true, manualRequiredFields: true, noImplicitBinding: true,
            ...races, ...managementSettings,
            tutorialDeferredAndResumed: true, keyboardFocus: true, narrowViewport: true, unsupportedProviderHidden: true, listScreenshot, manualScreenshot, screenshot };
        console.log(JSON.stringify(result, null, 2));
})().then(() => finish(), error => finish(error));
