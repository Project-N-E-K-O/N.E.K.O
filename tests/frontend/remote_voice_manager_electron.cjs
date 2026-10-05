'use strict';
// Run with the desktop project's Electron binary; no real provider is contacted.
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const assert = require('node:assert/strict');
const { createVoiceManagerServer } = require('./remote_voice_manager_server.cjs');
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-remote-voice-ui-'));
app.setPath('userData', path.join(scratch, 'user-data'));
const { server, state } = createVoiceManagerServer();
let win;
const watchdog = setTimeout(() => { console.error('REMOTE_VOICE_ELECTRON_TIMEOUT'); app.exit(2); }, 60000);
const run = code => win.webContents.executeJavaScript(code, true).catch(error => { console.error('UI expression failed:', code); throw error; });
async function waitFor(expression) {
    return run(`new Promise((resolve,reject)=>{const check=()=>(${expression});if(check())return resolve(true);const observer=new MutationObserver(()=>{if(check()){clearTimeout(timer);observer.disconnect();resolve(true);}});observer.observe(document.body,{subtree:true,attributes:true,childList:true,characterData:true});const timer=setTimeout(()=>{observer.disconnect();reject(new Error('UI timeout: '+${JSON.stringify(expression)}));},10000);})`);
}
const click = key => run(`(()=>{const buttons=Array.from(document.querySelectorAll('.remote-voice-dialog button'));const target=buttons.find(button=>button.textContent===window.t('voice.remote.${key}'));if(!target)throw Error(JSON.stringify({requested:window.t('voice.remote.${key}'),buttons:buttons.map(button=>button.textContent)}));target.click();return true;})()`);
async function screenshot(name) { await run('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))'); const image = await win.webContents.capturePage(); const target = path.join(scratch, name + '.png'); fs.writeFileSync(target, image.toPNG()); return target; }
app.whenReady().then(async () => {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    win = new BrowserWindow({ show: false, width: 1120, height: 880, skipTaskbar: true,
        webPreferences: { contextIsolation: false, nodeIntegration: false, sandbox: false, backgroundThrottling: false } });
    const errors = [];
    win.webContents.on('console-message', (event, level, message) => { if (level >= 3) { errors.push(message); console.error('Renderer:', message); } });
    await win.loadURL(origin + '/voice_clone?lanlan_name=Test');
    await waitFor("typeof window.t==='function' && document.getElementById('voiceProvider').value==='cosyvoice' && !document.getElementById('importExistingVoice').hidden && !document.getElementById('importExistingVoice').disabled");
    const entryPlacement = await run("(()=>{const button=document.getElementById('importExistingVoice').getBoundingClientRect();const select=document.querySelector('.remote-voice-provider-controls .api-provider-dropdown').getBoundingClientRect();return button.left>=select.right-1 && Math.abs(button.top-select.top)<12;})()");
    if (!entryPlacement) {
        console.log(await run("(()=>{const button=document.getElementById('importExistingVoice').getBoundingClientRect();const select=document.querySelector('.remote-voice-provider-controls .api-provider-dropdown').getBoundingClientRect();return JSON.stringify({button:button.toJSON(),select:select.toJSON()});})()"));
        console.log(await screenshot('entry-placement'));
    }
    assert.equal(entryPlacement, true);
    await run("document.getElementById('importExistingVoice').click();true;");
    await waitFor("document.querySelectorAll('.remote-voice-table tbody tr').length===2");
    await run("new Promise((resolve,reject)=>{let frame;const deadline=performance.now()+5000;const check=()=>{if(window.pageTutorialManager._modalTutorialWaitCleanup)return resolve(true);if(performance.now()>deadline)return reject(Error('Tutorial was not deferred'));frame=requestAnimationFrame(check);};check();})");
    assert.equal(await run("window.pageTutorialManager.isTutorialRunning"), false);
    assert.equal(await run("!!document.querySelector('.driver-popover')"), false);
    assert.equal(state.imports.length, 0);
    const listScreenshot = await screenshot('list');
    await run("document.querySelector('.remote-voice-table input[type=radio]').click();true;");
    await click('importSelected');
    await waitFor("document.querySelector('.remote-voice-status').textContent===window.t('voice.remote.imported') && document.querySelector('[data-voice-id=voice_00000000000000000000000000000001]')");
    assert.equal(state.imports.length, 1); assert.equal(state.binding, '');
    assert.equal(state.imports[0].remote_voice_id, 'ExistingVoice123');
    assert.equal(await run("document.querySelector('[data-voice-id=voice_00000000000000000000000000000001] .voice-id').textContent"), 'ID: ExistingVoice123');
    await run("document.querySelector('.remote-voice-close').click();true;");
    await waitFor("window.pageTutorialManager.isTutorialRunning && document.getElementById('neko-page-tutorial-skip-btn')");
    await run("document.getElementById('neko-page-tutorial-skip-btn').click();true;");
    await waitFor("!window.pageTutorialManager.isTutorialRunning");
    await run("document.querySelector('[data-voice-id=voice_00000000000000000000000000000001]').click();true;");
    await waitFor("document.querySelector('[data-voice-id=voice_00000000000000000000000000000001]').classList.contains('selected')");
    assert.equal(state.binding, 'voice_00000000000000000000000000000001');
    await run("Array.from(document.querySelectorAll('[data-voice-id=voice_00000000000000000000000000000001] button')).find(button=>button.textContent===window.t('voice.remote.overwrite')).click();true;");
    await run("(()=>{const transfer=new DataTransfer();transfer.items.add(new File([new Uint8Array(512)],'controlled.wav',{type:'audio/wav'}));const input=document.querySelector('.remote-voice-dialog input[type=file]');input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));return true;})()");
    await click('overwrite');
    await waitFor("document.querySelector('.remote-voice-status').textContent===window.t('voice.remote.uncertain')");
    assert.equal(state.updates.length, 1); assert.equal(state.binding, 'voice_00000000000000000000000000000001');
    assert.equal(await run("Array.from(document.querySelectorAll('.remote-voice-dialog button')).find(button=>button.textContent===window.t('voice.remote.overwrite')).hidden"), true);
    await waitFor("document.querySelector('.remote-voice-dialog').getAttribute('aria-busy')==='false'");
    await click('refreshStatus');
    await waitFor("document.querySelector('.remote-voice-status').textContent===window.t('voice.remote.completed')");
    assert.equal(state.updates.length, 1);
    await run("document.querySelector('.remote-voice-close').click();document.getElementById('importExistingVoice').click();true;");
    await waitFor("document.querySelectorAll('.remote-voice-table tbody tr').length===2");
    await click('manualEntry');
    await run("const input=document.querySelector('.remote-voice-dialog input[name=remote_voice_id]');input.value='ManualVoice';input.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.remote-voice-dialog input[name=display_name]').value='手动导入测试';true;");
    const manualScreenshot = await screenshot('manual');
    await click('import');
    await waitFor("document.querySelector('[data-voice-id=voice_00000000000000000000000000000002]')");
    assert.equal(state.imports[1].remote_voice_id, 'ManualVoice'); assert.equal(state.binding, 'voice_00000000000000000000000000000001');
    await run("document.querySelector('.remote-voice-close').click();document.getElementById('importExistingVoice').focus();document.getElementById('importExistingVoice').click();true;");
    await waitFor("document.querySelector('.remote-voice-table tbody tr')");
    await run("document.querySelector('.remote-voice-dialog input').dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',isComposing:true,bubbles:true}));true;");
    assert.equal(await run("!!document.querySelector('.remote-voice-dialog')"), true);
    await run("const last=Array.from(document.querySelectorAll('.remote-voice-dialog button')).filter(button=>!button.disabled&&!button.hidden).at(-1);last.focus();last.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true,cancelable:true}));true;");
    assert.equal(await run("document.activeElement.classList.contains('remote-voice-close')"), true);
    win.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'ESCAPE' });
    await waitFor("!document.querySelector('.remote-voice-dialog')");
    assert.equal(await run("document.activeElement.id"), 'importExistingVoice');
    win.setSize(420, 820);
    await run("document.getElementById('importExistingVoice').click();true;");
    await waitFor("document.querySelectorAll('.remote-voice-table tbody tr').length===2");
    assert.equal(await run("(()=>{const panel=document.querySelector('.remote-voice-dialog').getBoundingClientRect();return panel.left>=0&&panel.right<=innerWidth&&document.querySelector('.remote-voice-table-scroll').scrollWidth>=document.querySelector('.remote-voice-table-scroll').clientWidth;})()"), true);
    const narrowScreenshot = await screenshot('narrow');
    await run("document.querySelector('.remote-voice-close').click();true;");
    win.setSize(1120, 880);
    await win.loadURL(origin + '/api_key');
    await waitFor("document.getElementById('doubaoVoiceManagementAccessKey') && document.getElementById('doubaoVoiceManagementAccessKey').dataset.maskedSecret==='true'");
    assert.equal(await run("getRealKey(document.getElementById('doubaoVoiceManagementAccessKey'))"), '__NEKO_SECRET_MASKED__');
    assert.equal(await run("document.getElementById('doubaoVoiceManagementSecretKey').dataset.realKey"), '');
    await run("document.getElementById('doubaoVoiceManagementProjectName').value='Controlled Project';document.getElementById('api-key-form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}));true;");
    await waitFor("document.getElementById('warning-modal').style.display==='flex'");
    await run("confirmApiKeyChange();true;");
    await waitFor("!document.getElementById('main-content').inert && document.getElementById('status').textContent.length>0");
    assert.equal(state.settings.length, 1);
    assert.equal(state.settings[0].doubaoVoiceManagementAccessKey, '__NEKO_SECRET_MASKED__');
    assert.equal(state.settings[0].doubaoVoiceManagementSecretKey, '__NEKO_SECRET_MASKED__');
    assert.equal(state.settings[0].doubaoVoiceManagementProjectName, 'Controlled Project');
    console.log(JSON.stringify({ electron: process.versions.electron, chromium: process.versions.chrome,
        actualProductAssets: true, controlledApiOnly: true, importWithoutBinding: true, localReferenceBinding: true,
        originalRemoteIdVisible: true, manualImport: true, uncertainUpdateNoRetry: true, explicitStatusRefresh: true,
        keyboardImeAndFocus: true, narrowWindow: true, tutorialDeferredAndResumed: true, maskedManagementCredentialRoundTrip: true,
        listScreenshot, manualScreenshot, narrowScreenshot, consoleErrors: errors }, null, 2));
    clearTimeout(watchdog); win.destroy(); server.close(); app.quit();
}).catch(error => { console.error(error); clearTimeout(watchdog); if (win) win.destroy(); server.close(); app.exit(1); });
