'use strict';

// Opt-in native integration: real Electron renderer/IPC, production PC preload
// entries + IPC router, and production backend avatar endpoints on isolated data.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');

async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

async function waitUntil(check, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    try { if (await check()) return; } catch (_) {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  throw new Error('Native avatar acceptance readiness timed out');
}

function electronMain() {
  return String.raw`
const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('node:path');
const assert = require('node:assert/strict');
const fs = require('node:fs');
app.setPath('userData', process.env.NEKO_AVATAR_PROFILE);
app.commandLine.appendSwitch('disable-gpu');
const pc = process.env.NEKO_PC_ROOT;
const base = process.env.NEKO_AVATAR_BASE;
const windows = {};
const evidence = [];
const A = 'a'.repeat(32), B = 'b'.repeat(32);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function evaluate(win, code) { return win.webContents.executeJavaScript(code); }
async function until(win, code, message) {
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (await evaluate(win, code)) return;
    await pause(50);
  }
  throw new Error(message + ': ' + await evaluate(win, 'JSON.stringify({identity:appChatAvatarState.getIdentity(),record:appChatAvatarState.getRecord(),error:appChatAvatarState.getError()?.message,edit:appChatAvatar.getCustomEditState?.()})'));
}
async function makeWindow(name, entry, partition) {
  const win = new BrowserWindow({show:false,width:900,height:900,webPreferences:{
    preload:entry ? path.join(pc,'src/preload/entries',entry+'.js') : undefined,partition,
    contextIsolation:false,nodeIntegration:false,sandbox:false,backgroundThrottling:false,
  }});
  windows[name] = win;
  win.webContents.on('preload-error', (_event, _file, error) => console.error('PRELOAD_ERROR',error.stack));
  // Match the production window manager: newly loaded chat renderers request
  // the pet bridge's current socket readiness through its real IPC handler.
  if (name === 'chat' || name === 'fullChat') win.webContents.on('dom-ready', () => {
    if (windows.pet && !windows.pet.isDestroyed()) windows.pet.webContents.send('neko:ws-trigger-ready-recheck');
  });
  await win.loadURL(base+'/acceptance');
  await until(win, '!!appChatAvatarState.getRecord()', name+' initial record');
  return win;
}
async function choose(win, width, height) {
  await evaluate(win, '(async()=>{ appChatAvatar.showPopup(document.getElementById("avatarPreviewButton")); const canvas=document.createElement("canvas");canvas.width='+width+';canvas.height='+height+';const ctx=canvas.getContext("2d");ctx.fillStyle="#d43256";ctx.fillRect(canvas.width/4,canvas.height/4,canvas.width/2,canvas.height/2);const blob=await new Promise(resolve=>canvas.toBlob(resolve,"image/png"));const file=new File([blob],"transparent.png",{type:"image/png"}); const input=document.getElementById("chat-avatar-file-input");const dt=new DataTransfer();dt.items.add(file);input.files=dt.files;input.dispatchEvent(new Event("change",{bubbles:true})); })()');
  await until(win, 'document.getElementById("chat-avatar-preview-popup").classList.contains("is-cropping")', 'crop opens');
}
async function cropAndSave(win) {
  await evaluate(win, 'document.getElementById("avatar-cropper-save").click()');
  await until(win, '!document.getElementById("chat-avatar-save").hidden', 'candidate ready');
  await evaluate(win, 'document.getElementById("chat-avatar-save").click()');
  await until(win, '!!appChatAvatarState.getRecord()?.data_url', 'upload saved');
  await until(win, '!appChatAvatarEditor.getState().busy && !appChatAvatarEditor.getState().editing && !document.getElementById("chat-avatar-preview-popup").classList.contains("is-cropping")', 'saved editor retires draft');
}
app.whenReady().then(async()=>{
  try {
    ipcMain.handle('get-dark-mode',()=>false);
    ipcMain.handle('set-dark-mode',(_event,value)=>!!value);
    ipcMain.handle('neko:host:is-maximized',()=>false);
    ipcMain.on('neko:input-region-backend', event=>{event.returnValue={backend:'acceptance',canUseSetShape:false,hasSetShapeMethod:false,patch:{verified:false}};});
    require(path.join(pc,'src/ipc-router')).setupIPCRouter({getWindows:()=>windows,log:()=>{}});
    const compact = await makeWindow('chat','compact-chat','persist:neko-avatar-compact-acceptance');
    const full = await makeWindow('fullChat','full-chat','persist:neko-full-chat');
    const pet = await makeWindow('pet','pet','persist:neko-avatar-pet-acceptance');
    const web = await makeWindow('web',null,'persist:neko-avatar-web-acceptance');
    await evaluate(web, 'document.getElementById("avatarPreviewButton").click()');
    await until(web, '!document.getElementById("chat-avatar-preview-popup").hidden', 'ordinary web trigger opens popup without a model');
    await evaluate(web, 'appChatAvatar.hidePopup()');
    evidence.push('ordinary web mode with no PC preload opens the real upload popup without model readiness');
    await until(compact, '__acceptanceSocket.readyState===WebSocket.OPEN', 'compact proxy open');
    await until(full, '__acceptanceSocket.readyState===WebSocket.OPEN', 'full proxy open');
    evidence.push('real pet/compact/full preloads, IPC router and distinct session partitions');
    await choose(compact,32,32);
    const smallBounds=await evaluate(compact,'(()=>{const image=document.getElementById("avatar-cropper-img").getBoundingClientRect(), box=document.getElementById("avatar-cropper-box").getBoundingClientRect(); return {image:{width:image.width,height:image.height},box:{width:box.width,height:box.height}}})()');
    assert.ok(smallBounds.box.width <= smallBounds.image.width+1 && smallBounds.box.height <= smallBounds.image.height+1, JSON.stringify(smallBounds));
    await cropAndSave(compact);
    const saved = await evaluate(compact,'appChatAvatarState.getRecord()');
    assert.equal(await evaluate(compact,'document.getElementById("chat-avatar-preview-note").hidden'),true,'custom display hides stale model error note');
    compact.showInactive();
    await until(compact,'Number(getComputedStyle(document.getElementById("chat-avatar-preview-popup")).opacity)===1','saved popup finishes opening');
    await evaluate(compact,'new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    fs.writeFileSync(path.join(process.env.NEKO_AVATAR_PROFILE,'saved-avatar.png'),(await compact.webContents.capturePage()).toPNG());
    compact.hide();
    await until(full,'appChatAvatarState.getRecord()?.revision==='+JSON.stringify(saved.revision),'hidden full receives real notification');
    assert.equal(full.isVisible(),false);
    const alpha=await evaluate(full,'(async()=>{const img=new Image();img.src=appChatAvatarState.getDataUrl();await img.decode();const canvas=document.createElement("canvas");canvas.width=canvas.height=320;const ctx=canvas.getContext("2d");ctx.drawImage(img,0,0);return {alpha:ctx.getImageData(0,0,1,1).data[3],width:img.width,height:img.height};})()');
    assert.deepEqual(alpha,{alpha:0,width:320,height:320});
    evidence.push('32px transparent upload/crop/save persists PNG320 and reaches hidden full window over real IPC');
    const modelOnly=await evaluate(compact,'({cached:appChatAvatar.getCachedPreview(),modelEvents:__modelEvents.filter(e=>e.dataUrl===appChatAvatarState.getDataUrl()).length})');
    assert.equal(modelOnly.modelEvents,0);
    assert.notEqual(modelOnly.cached?.dataUrl,saved.data_url);
    evidence.push('custom avatar does not enter model cache or model update broadcast');
    const priority=await evaluate(compact,'(()=>{const canvas=document.createElement("canvas");canvas.width=canvas.height=8;const model=canvas.toDataURL();appChatAvatar.setExternalAvatar(model,"live2d");const customWins=appChatAvatar.getCurrentAvatarDataUrl()===appChatAvatarState.getDataUrl();appChatAvatar.setTutorialAvatarOverride(model,"live2d");const tutorialWins=appChatAvatar.getCurrentAvatarDataUrl()===model;appChatAvatar.clearTutorialAvatarOverride();return {customWins,tutorialWins,restored:appChatAvatar.getCurrentAvatarDataUrl()===appChatAvatarState.getDataUrl(),modelKept:appChatAvatar.getCachedPreview().dataUrl===model};})()');
    assert.deepEqual(priority,{customWins:true,tutorialWins:true,restored:true,modelKept:true});
    evidence.push('tutorial override wins temporarily; model refresh preserves custom display and model-only cache');
    console.log('NEKO_AVATAR_RESTART_REQUEST');
    const restartMarker = path.join(process.env.NEKO_AVATAR_PROFILE,'backend-restarted');
    const restartDeadline = Date.now()+30000;
    while (!fs.existsSync(restartMarker) && Date.now()<restartDeadline) await pause(50);
    assert.ok(fs.existsSync(restartMarker),'backend restart acknowledgement');
    await evaluate(compact,'__connectAcceptance()');
    await evaluate(full,'__connectAcceptance()');
    await evaluate(pet,'__connectAcceptance()');
    await evaluate(web,'__connectAcceptance()');
    await evaluate(full,'appChatAvatarState.refresh("backend-restart")');
    assert.equal(await evaluate(full,'appChatAvatarState.getRecord().revision'),saved.revision);
    evidence.push('real backend process restart retains UID record and version on disk');
    await compact.reload();
    await full.reload();
    await web.reload();
    await until(compact,'appChatAvatarState.getRecord()?.revision==='+JSON.stringify(saved.revision),'compact reconstruction after backend CSRF rotation');
    await until(full,'appChatAvatarState.getRecord()?.revision==='+JSON.stringify(saved.revision),'full reconstruction reads persisted avatar');
    await until(compact, '__acceptanceSocket.readyState===WebSocket.OPEN', 'reloaded compact proxy open');
    await until(full, '__acceptanceSocket.readyState===WebSocket.OPEN', 'reloaded full proxy open');
    evidence.push('window recreation restores backend record across session partition');
    await evaluate(compact,'window.__oldBinding=appChatAvatarState.captureEdit();window.__oldUrl=appChatAvatarState.getDataUrl()');
    await evaluate(full,'appChatAvatarState.restore()');
    await until(compact,'appChatAvatarState.getRecord()?.data_url===null && appChatAvatarState.getRecord()?.revision!=="0"','restore tombstone sync');
    assert.equal(await evaluate(compact,'document.getElementById("chat-avatar-preview-note").hidden'),false,'restoring model display restores its note');
    const conflict=await evaluate(compact,'(async()=>{const blob=await(await fetch(__oldUrl)).blob();try{await appChatAvatarState.save(blob,__oldBinding);return "unexpected-success";}catch(e){return e.code;}})()');
    assert.equal(conflict,'chat_avatar_conflict');
    evidence.push('restore writes a new version and prevents stale-window resurrection');
    await choose(compact,4000,100);
    const skinny=await evaluate(compact,'(()=>{const image=document.getElementById("avatar-cropper-img").getBoundingClientRect(),box=document.getElementById("avatar-cropper-box").getBoundingClientRect();return {imageHeight:image.height,boxHeight:box.height};})()');
    assert.ok(skinny.boxHeight<=skinny.imageHeight+1,JSON.stringify(skinny));
    await evaluate(compact,'appChatAvatar.hidePopup()');
    assert.equal(await evaluate(compact,'appChatAvatarState.getDataUrl()'),'');
    evidence.push('extreme aspect ratio crop stays bounded; closing cancels candidate');
    await evaluate(full,'appChatAvatarState.beginCharacterSwitch(10); appChatAvatarState.commitCharacterSwitch(10,{uid:'+JSON.stringify(B)+',name:"B"})');
    await until(full,'appChatAvatarState.getIdentity()?.uid==='+JSON.stringify(B)+' && !!appChatAvatarState.getRecord()','same model B identity');
    assert.equal(await evaluate(full,'appChatAvatarState.getDataUrl()'),'');
    await evaluate(full,'appChatAvatarState.beginCharacterSwitch(11);appChatAvatarState.rollbackCharacterSwitch(11)');
    assert.equal(await evaluate(full,'appChatAvatarState.getIdentity().uid'),B);
    evidence.push('same-model role switch and failed-switch rollback preserve UID ownership');
    await evaluate(web,'appChatAvatarState.setIdentity({uid:'+JSON.stringify(B)+',name:"B"})');
    await evaluate(web,'(async()=>{appChatAvatar.showPopup(document.getElementById("avatarPreviewButton"));const canvas=document.createElement("canvas");canvas.width=80;canvas.height=40;const jpeg=new Uint8Array(await(await new Promise(resolve=>canvas.toBlob(resolve,"image/jpeg"))).arrayBuffer());const exif=Uint8Array.from([255,225,0,34,69,120,105,102,0,0,73,73,42,0,8,0,0,0,1,0,18,1,3,0,1,0,0,0,6,0,0,0,0,0,0,0]);const file=new File([jpeg.slice(0,2),exif,jpeg.slice(2)],"rotated.jpg",{type:"image/jpeg"});const input=document.getElementById("chat-avatar-file-input"),dt=new DataTransfer();dt.items.add(file);input.files=dt.files;input.dispatchEvent(new Event("change",{bubbles:true}));})()');
    await until(web,'document.getElementById("chat-avatar-preview-popup").classList.contains("is-cropping")','web JPEG crop opens');
    assert.deepEqual(await evaluate(web,'(()=>{const img=document.getElementById("avatar-cropper-img");return {width:img.naturalWidth,height:img.naturalHeight};})()'),{width:40,height:80});
    await cropAndSave(web);
    const webSaved=await evaluate(web,'appChatAvatarState.getRecord()');
    await until(full,'appChatAvatarState.getRecord()?.revision==='+JSON.stringify(webSaved.revision),'web upload reaches native window');
    await evaluate(web,'appChatAvatarEditor.chooseFile(new File([Uint8Array.from([137,80,78,71,13,10,26,10,0,0,0,13,73,72,68,82,0,0,0,32,0,0,0,32])],"broken.png",{type:"image/png"}))');
    assert.equal(await evaluate(web,'appChatAvatarEditor.getState().status'),'invalidImage');
    assert.equal(await evaluate(web,'appChatAvatarState.getRecord().revision'),webSaved.revision);
    await choose(web,32,32);
    web.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});
    web.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'});
    await until(web,'!appChatAvatarEditor.getState().editing','native Escape cancels web draft');
    assert.equal(await evaluate(web,'appChatAvatarState.getRecord().revision'),webSaved.revision);
    evidence.push('ordinary web JPEG upload honors EXIF6 and synchronizes with native chat; corrupt PNG and Escape preserve confirmed avatar');
    for (const [name,win] of Object.entries(windows)) {
      assert.equal(await evaluate(win,'document.getElementById("acceptance-errors").textContent'),'','uncaught script errors in '+name);
    }
    console.log('NEKO_CHAT_AVATAR_ACCEPTANCE '+JSON.stringify({electron:process.versions.electron,chromium:process.versions.chrome,checks:evidence}));
    app.exit(0);
  } catch(error) {console.error(error.stack);app.exit(1);}
});
setTimeout(()=>{console.error('Native acceptance deadline');app.exit(9);},90000);
`;
}

test('native avatar upload, disk persistence and hidden Electron window synchronization', {
  skip: process.env.NEKO_RUN_CHAT_AVATAR_ELECTRON !== '1', timeout: 150000,
}, async () => {
  const pc = process.env.NEKO_PC_ROOT || path.resolve(root, '../N.E.K.O.-PC');
  const electron = require(path.join(pc, 'node_modules/electron'));
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'neko-chat-avatar-acceptance-'));
  const port = await freePort();
  const base = `http://127.0.0.1:${port}`;
  const env = {...process.env};
  delete env.ELECTRON_RUN_AS_NODE;
  for (const key of Object.keys(env)) if (key.startsWith('NEKO_STORAGE_')) delete env[key];
  env.NEKO_AVATAR_ACCEPTANCE_ROOT=path.join(temp,'backend');
  let logs = '';
  let server;
  function startServer() {
    server=spawn('uv',['run','--no-sync','python','tests/support/chat_avatar_electron_server.py',String(port)],{
      cwd:root,env,windowsHide:true,stdio:['ignore','pipe','pipe'],
    });
    server.stdout.on('data',chunk=>{logs+=chunk;});
    server.stderr.on('data',chunk=>{logs+=chunk;});
  }
  async function stopServer() {
    if (!server || server.exitCode!==null) return;
    await fetch(base+'/acceptance-shutdown',{method:'POST',signal:AbortSignal.timeout(3000)}).catch(()=>{});
    await waitUntil(()=>server.exitCode!==null,7000).catch(()=>server.kill());
  }
  startServer();
  try {
    await waitUntil(async()=> (await fetch(base+'/acceptance-ready')).ok);
    const main = path.join(temp, 'main.cjs');
    fs.writeFileSync(main, electronMain());
    const result = await new Promise((resolve, reject) => {
      let output = '';
      let restarting=false;
      const child = spawn(electron, [main], {cwd:root,windowsHide:true,
        env:{...env,NEKO_PC_ROOT:pc,NEKO_AVATAR_PROFILE:path.join(temp,'profile'),NEKO_AVATAR_BASE:base},
        stdio:['ignore','pipe','pipe']});
      child.stdout.on('data', chunk => {
        output += chunk;
        if (!restarting && output.includes('NEKO_AVATAR_RESTART_REQUEST')) {
          restarting=true;
          (async()=>{
            await stopServer();startServer();
            await waitUntil(async()=>(await fetch(base+'/acceptance-ready')).ok);
            fs.writeFileSync(path.join(temp,'profile','backend-restarted'),'ready');
          })().catch(error=>{output+='\nRestart failure '+error.stack;child.kill();});
        }
      });
      child.stderr.on('data', chunk => {output += chunk;});
      child.once('error', reject);
      child.once('exit', code => resolve({code,output}));
    });
    fs.writeFileSync(path.join(temp,'acceptance.log'),result.output+'\nBACKEND\n'+logs);
    assert.equal(result.code,0,result.output+'\nBACKEND\n'+logs);
    const line=result.output.split(/\r?\n/).find(item=>item.startsWith('NEKO_CHAT_AVATAR_ACCEPTANCE '));
    assert.ok(line,result.output);
    console.log(line);
    console.log('Native acceptance artifacts: '+temp);
  } finally {
    await stopServer();
  }
});
