'use strict';
// Actual Chromium DOM and Electron multi-window smoke, isolated from user data.
// node this-file.cjs <absolute path to an installed electron.exe>
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const electron = process.argv[2];
assert.ok(electron && fs.existsSync(electron), 'Pass an existing Electron executable');
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-topic-ui-'));
const characterId = 'character_' + 'a'.repeat(32);
const rootGeneration = 'c'.repeat(32);
let epoch = 'a'.repeat(32);
let controlsEnabled = true;
let statusUnavailable = false;
const writes = [];
const settingsSource = fs.readFileSync(path.join(root, 'static/app/app-settings.js'), 'utf8');
const sharedApply = settingsSource.slice(settingsSource.indexOf('    function applySharedRuntimeSettings('),
  settingsSource.indexOf('    function isManualScreenShareActive('));
const installSharedApply = `(function(){const S=window.appState;const U={mapRenderQualityToFollowPerf:v=>v};
  const _SHARED_SETTINGS_KEYS=['proactiveChatEnabled','proactiveTopicRecommendationEnabled'];
  ${sharedApply};window.applySharedRecommendationFixture=applySharedRuntimeSettings;})()`;
const scripts = ['/static/app/app-state.js', '/static/avatar/avatar-ui-drag.js',
  '/static/avatar/avatar-ui-popup.js', '/static/app/app-proactive.js'];
const html = '<!doctype html><meta charset="utf-8"><body><script>window.Live2DManager=function(){};</script>' + scripts.map(src => `<script src="${src}"></script>`).join('');
const server = http.createServer(async (req, res) => {
  if (req.url.startsWith('/fixture/status?')) {
    const mode = new URL(req.url, 'http://127.0.0.1').searchParams.get('mode');
    controlsEnabled = mode !== 'disabled'; statusUnavailable = mode === 'unavailable';
    res.end('{}'); return;
  }
  res.setHeader('Content-Type', 'application/json');
  if (req.url === '/' || req.url === '/chat') {
    res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end(html); return;
  }
  if (scripts.includes(req.url)) {
    res.setHeader('Content-Type', 'text/javascript; charset=utf-8');
    res.end(fs.readFileSync(path.join(root, req.url))); return;
  }
  if (req.url === '/static/locales/zh-CN.json') {
    res.end(fs.readFileSync(path.join(root, req.url))); return;
  }
  if (req.url.startsWith('/api/characters')) {
    res.end(JSON.stringify({ '猫娘': { Yui: { _reserved: { character_id: characterId } } } })); return;
  }
  if (req.url.startsWith('/api/proactive/recommendation/status')) {
    if (statusUnavailable) { res.statusCode = 503; res.end(JSON.stringify({ error_code: 'store_unavailable' })); return; }
    res.end(JSON.stringify({ success: true, character_id: characterId, availability: controlsEnabled ? 'ready' : 'user_disabled',
      reset_generation: rootGeneration, reset_confirmation: 'e'.repeat(64),
      capability_enabled: true, controls_enabled: controlsEnabled, epoch, revision: 1 })); return;
  }
  let body = '';
  for await (const chunk of req) { body += chunk; if (body.length > 8192) throw new Error('Fixture body too large'); }
  if (req.url === '/api/proactive/recommendation/reset' || req.url === '/api/proactive/recommendation/recover') {
    const data = JSON.parse(body);
    assert.equal(data.character_id, characterId);
    assert.equal(data.expected_epoch, epoch);
    assert.equal(data.expected_reset_generation, rootGeneration);
    assert.equal(data.expected_confirmation, 'e'.repeat(64));
    assert.equal(req.headers['x-csrf-token'], 'isolated-test-token');
    epoch = epoch === 'a'.repeat(32) ? 'b'.repeat(32) : 'a'.repeat(32);
    writes.push({ url: req.url, data });
    res.end(JSON.stringify({ success: true, character_id: characterId, reset_generation: rootGeneration,
      epoch, revision: 2, request_id: data.request_id })); return;
  }
  if (req.url === '/api/proactive_chat') {
    writes.push({ url: req.url, data: JSON.parse(body) });
    res.end(JSON.stringify({ success: true, action: 'pass' })); return;
  }
  res.statusCode = 404; res.end('{}');
});

const rendererTest = async function () {
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  const state = window.appState;
  await fetch('/fixture/status?mode=enabled');
  check(state.proactiveTopicRecommendationEnabled === false, 'Default must be off');
  window.lanlan_config = { lanlan_name: 'Yui' };
  window.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'isolated-test-token' }) };
  window.confirm = () => true; // Confirmation exercises only synthetic fixture data.
  window.saveNEKOSettings = async () => {};
  const locale = await (await fetch('/static/locales/zh-CN.json')).json();
  window.t = key => key.split('.').reduce((value, part) => value && value[part], locale) || key;
  function Manager() {}
  window.AvatarPopupMixin.apply(Manager.prototype, 'smoke');
  const mgr = new Manager();
  mgr._createChatSettingsSidePanel = () => document.createElement('div');
  mgr._createAnimationSettingsSidePanel = () => document.createElement('div');
  mgr._attachSidePanelHover = () => {};
  mgr._createSettingsMenuItems = () => {};
  const popup = document.createElement('div');
  document.body.appendChild(popup);
  mgr._createSettingsPopupContent(popup);
  const panel = document.querySelector('[data-neko-sidepanel-type="interval-proactive-chat"]');
  check(!!panel, 'Actual sidebar factory must create panel');
  panel.style.display = 'flex'; panel.style.opacity = '1'; panel.style.top = '20px'; panel.style.left = '20px';
  const beta = panel.querySelector('input[id$="topic_recommendation-chat"]');
  check(!!beta && !beta.checked, 'Beta control present and off');
  const labels = [...panel.querySelectorAll('[data-i18n]')].map(e => e.getAttribute('data-i18n'));
  check(labels.indexOf('settings.toggles.proactiveMiniGameInviteChat') < labels.indexOf('settings.toggles.proactiveTopicRecommendation'), 'Sidebar order');
  check(labels.indexOf('settings.menu.mediaCredentials') < labels.indexOf('settings.recommendation.reset'), 'Reset below credentials');
  beta.checked = true; beta.dispatchEvent(new Event('change', { bubbles: true }));
  state.proactiveChatEnabled = true;
  check(state.proactiveTopicRecommendationEnabled, 'Actual checkbox updates shared state');
  for (const key of ['proactiveVisionChatEnabled', 'proactiveNewsChatEnabled', 'proactiveCommunityChatEnabled',
    'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled', 'proactiveMusicEnabled', 'proactiveMemeEnabled',
    'proactiveMiniGameInviteEnabled', 'proactiveVisionEnabled']) state[key] = false;
  await window.appProactive.triggerProactiveChat();
  await panel._refreshRecommendationStatus();
  const status = panel.querySelector('[id$="topic-recommendation-status"]');
  const statusDeadline = performance.now() + 3000;
  while (status.getAttribute('data-i18n') === 'settings.recommendation.checking') {
    check(performance.now() < statusDeadline, 'Status timed out');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  check(status.getAttribute('data-i18n') === 'settings.recommendation.ready', 'Actual status rendering: ' + status.getAttribute('data-i18n'));
  const recover = panel.querySelector('[id$="topic-recommendation-recover"]');
  check(!!recover, 'Non-destructive recovery control present');
  recover.click();
  const recoveryDeadline = performance.now() + 3000;
  while (status.getAttribute('data-i18n') !== 'settings.recommendation.recoverDone') {
    check(performance.now() < recoveryDeadline, 'Recovery did not finish: ' + status.textContent);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  panel.querySelector('[id$="topic-recommendation-reset"]').click();
  const deadline = performance.now() + 3000;
  while (status.getAttribute('data-i18n') !== 'settings.recommendation.resetDone') {
    check(performance.now() < deadline, 'Reset did not finish: ' + status.textContent);
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  check(state.proactiveTopicRecommendationEnabled && state.proactiveChatEnabled, 'Reset preserves both switches');
  async function waitForStatus(key) {
    const deadline = performance.now() + 3000;
    while (status.getAttribute('data-i18n') !== `settings.recommendation.${key}`) {
      check(performance.now() < deadline, 'Status did not settle: ' + status.textContent);
      await new Promise(resolve => setTimeout(resolve, 10));
    }
  }
  window.applySharedRecommendationFixture({ proactiveTopicRecommendationEnabled: false });
  await waitForStatus('saveFailed');
  await fetch('/fixture/status?mode=disabled');
  window.applySharedRecommendationFixture({ proactiveTopicRecommendationEnabled: false });
  await waitForStatus('user_disabled');
  await fetch('/fixture/status?mode=unavailable');
  await panel._refreshRecommendationStatus();
  check(status.getAttribute('data-i18n') === 'settings.recommendation.degraded', 'Unavailable store error mapping');
  beta.checked = false; beta.dispatchEvent(new Event('change', { bubbles: true }));
  check(!window.appProactive.hasAnyChatModeEnabled(), 'Optout disables recommendation-only source');
  return { route: location.pathname, controls: true, reset: true, defaultOff: true,
    sharedIntentConfirmed: true, unavailableStatus: true };
};

(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  const main = path.join(output, 'main.cjs');
  fs.writeFileSync(main, `const {app,BrowserWindow}=require('electron');
  const fs=require('node:fs'),path=require('node:path');
  app.setPath('userData',${JSON.stringify(path.join(output, 'profile'))});
  app.commandLine.appendSwitch('disable-gpu');
  let stage='app-ready',currentRoute=null;
  app.whenReady().then(async()=>{try {
    const results=[]; const windows=[];
    for (const route of ['/', '/chat']) {
      currentRoute=route; stage='window-create';
      const win=new BrowserWindow({show:false,width:900,height:750,webPreferences:{contextIsolation:true,nodeIntegration:false}});
      windows.push(win); stage='page-load'; await win.loadURL(${JSON.stringify(url)}+route);
      await win.webContents.executeJavaScript(${JSON.stringify(installSharedApply)});
      stage='renderer-assertions';
      results.push(await win.webContents.executeJavaScript('('+${JSON.stringify(rendererTest.toString())}+')()'));
      console.log('TOPIC_ELECTRON_STAGE '+JSON.stringify({route,stage:'renderer-verified'}));
      stage='capture-page'; const screenshot=await win.webContents.capturePage();
      stage='screenshot-write';
      fs.writeFileSync(path.join(${JSON.stringify(output)},route==='/'?'web-route.png':'electron-chat.png'),screenshot.toPNG());
      console.log('TOPIC_ELECTRON_STAGE '+JSON.stringify({route,stage:'capture-verified'}));
    }
    console.log('TOPIC_ELECTRON_RESULT '+JSON.stringify(results));
    windows.forEach(win=>win.destroy()); app.exit(0);
  } catch(err) {console.error('TOPIC_ELECTRON_FAILURE '+JSON.stringify({route:currentRoute,stage,error:err.message}));console.error(err.stack);app.exit(1);}});`);
  const env = { ...process.env }; delete env.ELECTRON_RUN_AS_NODE;
  const child = spawn(electron, [main], { env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  let stdout = '', stderr = '';
  child.stdout.on('data', b => { stdout += b; }); child.stderr.on('data', b => { stderr += b; });
  const timeout = setTimeout(() => child.kill(), 30000);
  // Wait for stdout/stderr to close as well as process exit before saving logs.
  const code = await new Promise((resolve, reject) => { child.on('close', resolve); child.on('error', reject); });
  clearTimeout(timeout);
  // Preserve stderr independently of terminal redirection, including failures.
  fs.writeFileSync(path.join(output, 'electron-run.json'), JSON.stringify({ code, stdout, stderr }, null, 2));
  console.log('TOPIC_ELECTRON_ARTIFACTS ' + output);
  console.log(stdout.trim()); if (code !== 0) console.error(stderr.slice(-5000));
  assert.equal(code, 0, 'Real Electron smoke failed');
  const requests = writes.filter(w => w.url === '/api/proactive_chat');
  // Both real windows stay open: the existing leader election prevents a
  // duplicate request from the chat follower on the same origin.
  assert.equal(requests.length, 1);
  requests.forEach(w => assert.deepEqual(w.data.enabled_modes, ['topic_recommendation']));
  assert.equal(writes.filter(w => w.url.endsWith('/reset')).length, 2);
  assert.equal(writes.filter(w => w.url.endsWith('/recover')).length, 2);
  console.log(JSON.stringify({ screenshots: output, model: 'stub', server: 'isolated API fixture' }));
})().catch(err => { console.error(err.stack); process.exitCode = 1; }).finally(() => server.close());
