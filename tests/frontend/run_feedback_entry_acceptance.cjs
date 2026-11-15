/* Real Chromium and Electron renderers against loopback-only synthetic APIs.
 * Product HTML, device script and Electron preload/controller run unchanged.
 * OAuth/identity/device responses are fixtures; this does not test a real IdP.
 * Set NEKO_TEST_PLAYWRIGHT_MODULE, NEKO_TEST_CHROME, NEKO_TEST_ELECTRON,
 * NEKO_TEST_PC_ROOT, NEKO_TEST_OUTPUT and NEKO_TEST_BASE_REF before running.
 * The baseline is required so committed PR checkouts cannot silently use HEAD.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { execFileSync } = require('node:child_process');
const { chromium, _electron } = require(process.env.NEKO_TEST_PLAYWRIGHT_MODULE);
const root = path.resolve(__dirname, '../..');
assert.ok(process.env.NEKO_TEST_BASE_REF, 'NEKO_TEST_BASE_REF must identify the before-change baseline commit');
const baseline = execFileSync('git', ['rev-parse', '--verify', '--end-of-options', process.env.NEKO_TEST_BASE_REF + '^{commit}'], { cwd: root, encoding: 'utf8' }).trim();
const output = path.resolve(process.env.NEKO_TEST_OUTPUT);
fs.mkdirSync(output, { recursive: true });
const copy = JSON.parse(fs.readFileSync(path.join(root, 'static/locales/zh-CN.json'))).feedbackDevices;
const beforeCopy = JSON.parse(execFileSync('git', ['show', baseline + ':static/locales/zh-CN.json'], { cwd: root, encoding: 'utf8' })).feedbackDevices;
const beforeTemplate = execFileSync('git', ['show', baseline + ':templates/feedback_devices.html'], { cwd: root, encoding: 'utf8' });
const beforeDevices = execFileSync('git', ['show', baseline + ':static/feedback-devices.js'], { cwd: root });
let origin, account = null, protocol = 1, sequence = 0;
const completed = new Set(), calls = [], pendingReplies = [];
let holdStatus = false;
const json = (res, value) => { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(value)); };
function status() {
    return { success: true, protocol_version: protocol, signed_in: !!account, owner_key: account || 'anonymous',
        identity_verified: !!account, pending: 0, settings_journal_pending: 0, has_cloud_receipt: false,
        fully_synced: false, upload_paused: false, control_state: 'ready', upgrade_required: false };
}
const server = http.createServer(async (req, res) => {
    try {
        const url = new URL(req.url, origin), route = url.pathname;
        calls.push({ route, method: req.method });
        if (route === '/api/config/page_config') return json(res, { autostart_csrf_token: 'fixture-only-csrf' });
        if (route === '/api/proactive/feedback/status') {
            const value = status();
            if (holdStatus) { pendingReplies.push(() => { if (!res.destroyed) json(res, value); }); return; }
            return json(res, value);
        }
        if (route === '/api/proactive/feedback/devices') return json(res, {
            success: true, owner_key: account, max_active_devices: 5, activation_context: 'fixture-only-context',
            devices: [{ registration_id: 'fixture-' + account, registration_version: 1,
                display_name: 'Device ' + account, active: true, is_current: false }] });
        if (route === '/api/card-drop/oauth/start') {
            const state = 'fixture-attempt-' + (++sequence);
            return json(res, { state, auth_url: origin + '/fixture-auth?state=' + state, expires_in: 60 });
        }
        if (route === '/api/card-drop/oauth/completion') return json(res, { logged_in: completed.has(url.searchParams.get('state')) });
        if (route === '/fixture-complete') { account = 'A'; completed.add(url.searchParams.get('state')); return json(res, { ok: true }); }
        if (route === '/fixture-auth') {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            return res.end('<button id="finish">Complete fixture login</button><script>document.querySelector("#finish").onclick=async()=>{await fetch("/fixture-complete"+location.search);window.close()};</script>');
        }
        if (route === '/static/i18n-i18next.js' || route === '/fixture-before-i18n.js') {
            // Translation bootstrap is isolated to avoid CDN traffic. Copy is the real locale.
            res.writeHead(200, { 'Content-Type': 'application/javascript' });
            const locale = route === '/fixture-before-i18n.js' ? beforeCopy : copy;
            return res.end('const fixtureCopy=' + JSON.stringify(locale) + ';window.t=k=>fixtureCopy[k.split(".").pop()]||k;document.addEventListener("DOMContentLoaded",()=>{for(const n of document.querySelectorAll("[data-i18n]"))n.textContent=window.t(n.dataset.i18n)});');
        }
        if (route === '/fixture-before-devices.js') { res.writeHead(200, { 'Content-Type': 'application/javascript' }); return res.end(beforeDevices); }
        if (route === '/fixture-before-manage') {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            return res.end(beforeTemplate.replace('/static/feedback-devices.js', '/fixture-before-devices.js')
                .replace('/static/i18n-i18next.js', '/fixture-before-i18n.js'));
        }
        if (route === '/api_key') {
            const source = fs.readFileSync(path.join(root, 'templates/api_key_settings.html'), 'utf8');
            const anchor = source.match(/<a\b[^>]*id="feedback-devices-link"[\s\S]*?<\/a>/);
            assert.equal(anchor, null, 'real API settings must not expose the withdrawn feedback entry');
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            return res.end('<!doctype html><meta charset="utf-8"><title>Fixture settings navigation</title>' + '<p>Settings</p>');
        }
        if (route === '/api/proactive/feedback/manage' || route === '/untrusted') {
            res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
            return res.end(fs.readFileSync(path.join(root, 'templates/feedback_devices.html')));
        }
        if (route.startsWith('/static/')) {
            const file = path.resolve(root, '.' + route);
            if (!file.startsWith(path.join(root, 'static') + path.sep)) { res.writeHead(403); return res.end(); }
            const contentType = route.endsWith('.js') ? 'application/javascript'
                : route.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
            res.writeHead(200, { 'Content-Type': contentType });
            return res.end(fs.readFileSync(file));
        }
        res.writeHead(404); res.end();
    } catch (error) { res.writeHead(500); res.end(error.message); }
});
function releaseReads() { holdStatus = false; for (const reply of pendingReplies.splice(0)) reply(); }
const results = { fixture: 'synthetic HTTP APIs; real product renderers; no real credentials or cloud writes', baseline, stages: [] };
const record = name => results.stages.push(name);
async function noOverflow(page) { assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true); }
async function waitDevices(page, owner) { await page.locator('#devices').getByText('Device ' + owner, { exact: false }).waitFor(); }
function readCount() { return calls.filter(call => call.route === '/api/proactive/feedback/status').length; }
async function main() {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    origin = 'http://127.0.0.1:' + server.address().port;
    const browser = await chromium.launch({ executablePath: process.env.NEKO_TEST_CHROME, headless: true });
    try {
        const context = await browser.newContext({ viewport: { width: 375, height: 850 } });
        await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
        const settings = await context.newPage();
        await settings.goto(origin + '/fixture-before-manage');
        await settings.locator('#device-controls').waitFor({ state: 'hidden' });
        assert.equal(await settings.locator('h1').textContent(), beforeCopy.title);
        assert.equal(await settings.locator('#status').textContent(), beforeCopy.notEnabled);
        await settings.screenshot({ path: path.join(output, 'browser-before.png'), fullPage: true });
        await settings.goto(origin + '/api_key');
        assert.equal(await settings.locator('#feedback-devices-link').count(), 0);
        const page = await context.newPage();
        await page.goto(origin + '/api/proactive/feedback/manage');
        assert.equal(await page.locator('#account-panel').count(), 0);
        assert.equal(await page.locator('script[src*="feedback-account.js"]').count(), 0);
        await page.locator('#device-controls').waitFor({ state: 'hidden' });
        await page.locator('#coming-soon').waitFor({ state: 'visible' });
        await noOverflow(page);
        const information = page.locator('#feedback-information');
        await information.waitFor({ state: 'visible' });
        assert.equal(await page.locator('#feedback-information-data').textContent(), copy.informationData);
        assert.equal(await page.locator('#feedback-information-excluded').textContent(), copy.informationExcluded);
        assert.equal(await page.locator('#disclosure-open').count(), 0);
        await information.screenshot({ path: path.join(output, 'browser-information-375.png') });
        await page.locator('#feedback-information-close').click();
        await information.waitFor({ state: 'hidden' });
        assert.equal(calls.some(c => c.method === 'POST'), false);
        assert.equal(calls.some(c => c.route === '/api/proactive/feedback/devices'), false);
        await page.screenshot({ path: path.join(output, 'browser-signed-out.png'), fullPage: true });
        record('browser: settings omit feedback entry; direct internal page shows short data explanation; closing does not authorize uploads');
        assert.equal(sequence, 0);
        assert.equal(protocol, 1);
        assert.equal(calls.some(c => c.route.includes('/activate')), false);
        record('browser: device management does not start OAuth or activate uploads');
        await page.locator('#coming-soon-title').focus();
        account = 'A';
        protocol = 2;
        // A focus refresh observes the runtime capability change.
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await waitDevices(page, 'A');
        assert.equal(await page.evaluate(() => document.activeElement.id), 'refresh', 'leaving the unopened notice transfers focus to a visible control');
        await noOverflow(page);
        await page.screenshot({ path: path.join(output, 'browser-account-a-375.png'), fullPage: true });
        await page.locator('#device-name').focus();
        holdStatus = true;
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await page.waitForFunction(() => document.querySelector('#devices').children.length === 0);
        assert.equal(await page.evaluate(() => document.activeElement.id), 'refresh', 'retiring a focused input preserves visible focus');
        await settings.bringToFront();
        account = 'B'; releaseReads();
        await page.bringToFront();
        // Headless popup windows do not consistently produce native focus when
        // brought forward. Exercise the real DOM listener explicitly, not an OS claim.
        await page.evaluate(() => window.dispatchEvent(new Event('focus')));
        await waitDevices(page, 'B');
        assert.equal(await page.locator('#devices').getByText('Device A').count(), 0);
        record('browser: DOM focus listener after account switch retires old controls and rejects stale account-A response');
        account = null;
        await page.evaluate(() => window.dispatchEvent(new Event('neko:account-changed')));
        await page.waitForFunction(() => document.querySelector('#devices').children.length === 0 && document.querySelector('#device-controls').hidden);
        for (const locale of ['en','ja','ko','zh-CN','zh-TW','ru','pt','es']) {
            const translations = JSON.parse(fs.readFileSync(path.join(root, 'static/locales', locale + '.json'), 'utf8')).feedbackDevices;
            await page.evaluate(translations => {
                window.t = key => translations[key.split('.').pop()] || key;
                for (const node of document.querySelectorAll('[data-i18n]')) node.textContent = window.t(node.dataset.i18n);
                window.dispatchEvent(new Event('localechange'));
            }, translations);
            assert.equal(await page.locator('#disclosure-open').count(), 0);
            assert.equal(await page.locator('#feedback-information-data').textContent(), translations.informationData);
            // Reopen only inside this fixture to inspect each translation; the
            // product provides no additional settings entry or reopen button.
            await page.evaluate(() => document.querySelector('#feedback-information').showModal());
            for (const height of [850, 360]) {
                await page.setViewportSize({width: 375, height});
                await noOverflow(page);
                assert.equal(await page.evaluate(() => {
                    const dialog = document.querySelector('#feedback-information').getBoundingClientRect();
                    const close = document.querySelector('#feedback-information-close').getBoundingClientRect();
                    return dialog.left >= 0 && dialog.right <= innerWidth && dialog.top >= 0 && dialog.bottom <= innerHeight
                        && close.top >= dialog.top && close.bottom <= dialog.bottom;
                }), true, locale + ': explanation and close button fit the viewport');
            }
            await page.keyboard.press('Escape');
            await information.waitFor({ state: 'hidden' });
        }
        assert.equal(calls.some(c => c.method === 'POST'), false);
        record('browser: short explanation translates in eight languages, fits narrow/short viewports, and closes with Escape without requests');
        await context.close();
    } finally { releaseReads(); await browser.close(); }

    const pcRoot = path.resolve(process.env.NEKO_TEST_PC_ROOT);
    const preload = path.join(output, 'acceptance-preload.cjs');
    const entry = path.join(output, 'acceptance-electron.cjs');
    fs.mkdirSync(path.join(output, 'electron-profile'), { recursive: true });
    fs.writeFileSync(preload, 'require(' + JSON.stringify(path.join(pcRoot, 'src/preload/shared/common.js')) + ').setupElectronShell();');
    fs.writeFileSync(entry, `const {app,BrowserWindow,ipcMain,screen}=require('electron');
        global.fixtureFs=require('node:fs');
        app.setPath('userData',${JSON.stringify(path.join(output, 'electron-profile'))});
        app.whenReady().then(async()=>{
          const HiddenWindow=function(options){const win=new BrowserWindow({...options,show:false,webPreferences:{...options.webPreferences,backgroundThrottling:false}});win.show=()=>{};return win;};
          HiddenWindow.getAllWindows=()=>BrowserWindow.getAllWindows();
          const {createUnifiedSettingsWindowController}=require(${JSON.stringify(path.join(pcRoot, 'src/main/unified-settings-window.js'))});
          const {trayMenuLocales}=require(${JSON.stringify(path.join(pcRoot, 'src/main/tray-menu-locales.js'))});
          global.fixtureSnapshot={desktopAccount:{loggedIn:true,displayName:'Fixture A'},petPrefs:{available:false,loaded:false},language:'zh-CN',darkMode:false,widgetMode:{},autoLaunch:{},preventSystemSleep:{},uiOpacity:{chat:100,floatingMenu:100},hotkeys:{},defaultHotkeys:{},ports:{},defaultPorts:{},customUrls:{}};
          global.controller=createUnifiedSettingsWindowController({BrowserWindow:HiddenWindow,ipcMain,screen,desktopSettingsActions:{getSnapshot:()=>global.fixtureSnapshot,subscribe:cb=>{global.settingsChanged=cb;return()=>{}},applyPatch:async()=>true,refreshPetPreferences:async()=>({})},getOriginalUrl:()=>${JSON.stringify(origin)},getAppConfig:()=>({}),getCurrentLanguage:()=>'zh-CN',getIcon:()=>undefined,t:k=>trayMenuLocales['zh-CN'][k]||k,trayMenuLocales,log:()=>{}});
          global.page=new BrowserWindow({show:false,width:460,height:850,webPreferences:{preload:${JSON.stringify(preload)},contextIsolation:true,sandbox:false,backgroundThrottling:false}});
          await global.page.loadURL(${JSON.stringify(origin + '/api/proactive/feedback/manage')});
        });app.on('window-all-closed',()=>{});`);
    account = 'A'; protocol = 2;
    const electronEnv = { ...process.env };
    delete electronEnv.ELECTRON_RUN_AS_NODE;
    const electronApp = await _electron.launch({ executablePath: process.env.NEKO_TEST_ELECTRON, args: [entry], env: electronEnv });
    try {
        const page = await electronApp.firstWindow();
        await waitDevices(page, 'A');
        await page.locator('#feedback-information').waitFor({ state: 'visible' });
        assert.equal(await page.locator('#feedback-information-data').textContent(), copy.informationData);
        assert.equal(await page.locator('#disclosure-open').count(), 0);
        await electronApp.evaluate(async (_electron, file) => {
            const rect = await global.page.webContents.executeJavaScript(`(() => {
                const r = document.querySelector('#feedback-information').getBoundingClientRect();
                return {x: Math.floor(r.x), y: Math.floor(r.y), width: Math.ceil(r.width), height: Math.ceil(r.height)};
            })()`);
            const image = await global.page.webContents.capturePage(rect);
            if (image.isEmpty()) throw Error('Short explanation did not paint');
            global.fixtureFs.writeFileSync(file, image.toPNG());
        }, path.join(output, 'electron-information.png'));
        await page.locator('#feedback-information-close').click();
        await page.locator('#feedback-information').waitFor({ state: 'hidden' });
        assert.equal(calls.some(c => c.method === 'POST'), false);
        record('electron: short explanation displays and native confirmation closes it without changing upload permission');
        const settingsOpened = electronApp.waitForEvent('window');
        assert.equal(await page.locator('#account-panel').count(), 0);
        assert.deepEqual(await page.evaluate(() => window.electronShell.openAccountSettings()), { ok: true });
        const settings = await settingsOpened;
        const settingWindow = await electronApp.evaluate(() => global.controller.getWindow().id);
        await settings.locator('[data-patch="desktopAccountLogout"]').waitFor();
        await settings.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        results.settingsGeneral = await electronApp.evaluate(async ({ BrowserWindow }, data) => {
            const image = await BrowserWindow.fromId(data.id).webContents.capturePage();
            if (image.isEmpty()) throw Error('Settings general did not paint');
            global.fixtureFs.writeFileSync(data.file, image.toPNG());
            return { bounds: BrowserWindow.fromId(data.id).getBounds(), image: image.getSize() };
        }, { id: settingWindow, file: path.join(output, 'electron-settings-general.png') });
        assert.deepEqual(await page.evaluate(() => window.electronShell.openAccountSettings()), { ok: true });
        assert.equal(await electronApp.evaluate(() => global.controller.getWindow().id), settingWindow);
        assert.equal(sequence, 0, 'native account navigation never starts browser OAuth');
        record('electron: settings account window stays accessible while management omits the duplicate account card');
        assert.equal(await settings.locator('[data-settings-section="management"], [data-open-page="feedbackDevices"]').count(), 0);
        assert.equal(await settings.locator('[data-section="management"]').count(), 1, 'ordinary management section remains available');
        assert.equal(await settings.getByText('帮助改进推荐', {exact: true}).count(), 0);
        assert.equal(await settings.locator('[data-patch="desktopAccountLogout"]').count(), 1);
        await settings.locator('[data-section="management"]').click();
        await settings.locator('[data-settings-section="general"]').waitFor();
        assert.equal(await settings.locator('[data-open-page="feedbackDevices"]').count(), 0);
        assert.equal(await settings.getByText('推荐记录上传设备', {exact: true}).count(), 0);
        assert.equal(await settings.getByText('推荐记录上传使用常规设置中的 N.E.K.O 账号。', {exact: false}).count(), 0);
        await settings.locator('[data-settings-section="general"]').click();
        await settings.locator('[data-patch="desktopAccountLogout"]').waitFor();
        await electronApp.evaluate(() => { global.fixtureSnapshot.petPrefs = { available: true, loaded: true }; global.settingsChanged(); });
        await settings.locator('[data-patch="desktopAccountLogout"]').waitFor();
        assert.equal(await settings.locator('[data-open-page="feedbackDevices"]').count(), 0, 'pet readiness must not restore the hidden entry');
        record('electron: only the three feedback UI positions are hidden; account login and ordinary management navigation survive readiness refresh');
        await electronApp.evaluate(async (_electron, file) => {
            const image = await global.page.webContents.capturePage();
            if (image.isEmpty()) throw Error('Management did not paint');
            global.fixtureFs.writeFileSync(file, image.toPNG());
        }, path.join(output, 'electron-account-a.png'));
        const before = readCount(); account = 'B';
        await electronApp.evaluate(() => global.controller.broadcastAccountChanged());
        await waitDevices(page, 'B');
        assert.ok(readCount() > before);
        assert.equal(await page.locator('#devices').getByText('Device A').count(), 0);
        record('electron: native credential-change broadcast refreshes hidden management child and retires old account controls');
        await page.goto(origin + '/untrusted');
        await page.locator('#refresh').waitFor();
        assert.deepEqual(await page.evaluate(() => window.electronShell.openAccountSettings()), { ok: false });
        record('electron: same-origin non-management page cannot invoke native account settings command');
        await page.setViewportSize({ width: 375, height: 850 });
        await noOverflow(page);
    } finally { await electronApp.close(); }
    results.ok = true;
}
main().catch(error => { results.ok = false; results.error = error.stack; process.exitCode = 1; }).finally(() => {
    releaseReads(); server.closeAllConnections(); server.close();
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results, null, 2));
});
