'use strict';
const {app,BrowserWindow,ipcMain,screen}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=path.resolve(process.env.NEKO_DESKTOP_SOURCE || process.cwd());
if (!process.env.NEKO_DESKTOP_BASE_SOURCE) throw new Error('NEKO_DESKTOP_BASE_SOURCE must name an untouched source copy');
const original=path.resolve(process.env.NEKO_DESKTOP_BASE_SOURCE);
assert.notEqual(fs.realpathSync(original),fs.realpathSync(source),'before source must differ from patched source');
assert.equal(fs.readFileSync(path.join(original,'src/renderer/settings/settings.js'),'utf8').includes('renderSilentStartupRow'),false,'before source must be unpatched');
const out=process.env.NEKO_STARTUP_TEST_OUTPUT ? path.resolve(process.env.NEKO_STARTUP_TEST_OUTPUT) : fs.mkdtempSync(path.join(require('node:os').tmpdir(),'neko-silent-startup-'));fs.mkdirSync(out,{recursive:true});
app.setName('NEKO Silent Startup Verification');app.setPath('userData',path.join(out,'fixture-user-data'));
const {createDesktopSettingsActions}=require(path.join(source,'src/main/desktop-settings-actions'));
const {trayMenuLocales}=require(path.join(source,'src/main/tray-menu-locales'));
let cfg={silentStartup:false,autoLaunch:true,globalAlwaysOnTop:false,widgetModeEnabled:true};
let language='zh-CN',fail=false,throwWrite=false,throwIpc=false,delay=0,writes=0;
const replies=[];let productionPatch;
const actions=createDesktopSettingsActions({getAppConfig:()=>cfg,saveConfig:next=>{if(throwWrite)throw new Error('fixture ENOSPC');if(fail)return false;writes++;fs.writeFileSync(path.join(out,'fixture-config.json'),JSON.stringify(next));return true;},process,t:key=>key,getInterfaceLanguage:()=>language});
let win;
function payload(){const snapshot=actions.getSnapshot();snapshot.autoLaunch={enabled:true,supported:true};snapshot.petPrefs={available:true,values:{}};return {snapshot,language,texts:trayMenuLocales[language],section:'general'};}
const {createUnifiedSettingsWindowController}=require(path.join(source,'src/main/unified-settings-window'));
const controller=createUnifiedSettingsWindowController({
 BrowserWindow,screen,desktopSettingsActions:actions,getAppConfig:()=>cfg,getCurrentLanguage:()=>language,
 getIcon:()=>null,getOriginalUrl:()=> 'http://127.0.0.1:48911',log:()=>{},t:key=>key,trayMenuLocales,
 ipcMain:{
  handle(channel,handler){
   if(channel==='neko:desktop-settings-patch') {
    productionPatch=handler;
    ipcMain.handle(channel,async(event,payload)=>{
     if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
     if(throwIpc)throw new Error('fixture IPC failure');
     const result=await handler(event,payload);replies.push(result);return result;
    });
   } else ipcMain.handle(channel,handler);
  },
  on:(...args)=>ipcMain.on(...args),
 },
});
ipcMain.handle('neko:host:get-pin-state',()=>({available:false}));
const evaluate=code=>win.webContents.executeJavaScript(code);
async function until(code){for(let i=0;i<120;i++){if(await evaluate(code))return;await new Promise(r=>setTimeout(r,25));}throw new Error('timed out: '+code);}
async function screenshot(name){await new Promise(r=>setTimeout(r,150));fs.writeFileSync(path.join(out,name+'.png'),(await win.webContents.capturePage()).toPNG());}
app.whenReady().then(async()=>{
 win=controller.createUnifiedSettingsWindow({section:'general'});
 await until("!!document.querySelector('[data-patch=silentStartup]')");
 const beforeWrites=writes;
 assert.deepEqual(await productionPatch({sender:{}},{key:'silentStartup',value:true}),{ok:false});
 assert.equal(writes,beforeWrites,'untrusted sender cannot persist');
 await win.loadFile(path.join(original,'src/renderer/settings/index.html'));await until("!!document.querySelector('[data-patch=autoLaunch]')");
 await evaluate("document.querySelector('[data-patch=autoLaunch]').closest('.group').scrollIntoView({block:'start'})");await screenshot('before');
 await win.loadFile(path.join(source,'src/renderer/settings/index.html'));await until("!!document.querySelector('[data-patch=silentStartup]')");
 await evaluate("document.querySelector('[data-patch=autoLaunch]').closest('.group').scrollIntoView({block:'start'})");
 assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),false);
 assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').getAttribute('aria-labelledby')"),'silent-startup-label');
 delay=250;await evaluate("document.querySelector('[data-patch=silentStartup]').focus();document.querySelector('[data-patch=silentStartup]').click()");
 await until("document.querySelector('[data-patch=silentStartup]').disabled");
 // A second click during an asynchronous save cannot queue a second write.
 await evaluate("document.querySelector('[data-patch=silentStartup]').click()");
 await until("document.getElementById('silent-startup-status').textContent.includes('已保存')");assert.equal(cfg.silentStartup,true);assert.equal(writes,1);
 assert.equal(replies.at(-1).ok,true);assert.equal(replies.at(-1).snapshot.silentStartup,true);
 assert.equal(await evaluate("document.activeElement?.dataset.patch"),'silentStartup','keyboard focus restored after save');
 await screenshot('after-enabled');
 fail=true;await evaluate("document.querySelector('[data-patch=silentStartup]').click()");await until("document.getElementById('silent-startup-status').textContent.includes('保存失败')");
 assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),true);assert.equal(cfg.silentStartup,true);await screenshot('save-error');
 assert.equal(replies.at(-1).ok,false);assert.equal(replies.at(-1).snapshot.silentStartup,true);
 fail=false;throwWrite=true;
 await evaluate("document.querySelector('[data-patch=silentStartup]').click()");
 await until("document.getElementById('silent-startup-status').textContent.includes('保存失败') && !document.querySelector('[data-patch=silentStartup]').disabled");
 assert.equal(replies.at(-1).ok,false);assert.equal(replies.at(-1).snapshot.silentStartup,true);assert.equal(cfg.silentStartup,true);
 throwWrite=false;throwIpc=true;await evaluate("document.querySelector('[data-patch=silentStartup]').click()");await until("document.getElementById('silent-startup-status').textContent.includes('保存失败')");assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),true);
 throwIpc=false;await evaluate("document.querySelector('[data-patch=silentStartup]').click()");await until("document.getElementById('silent-startup-status').textContent.includes('已保存')");assert.equal(cfg.silentStartup,false);
 // Reload verifies hydration from the main-process preference.
 await win.reload();await until("!!document.querySelector('[data-patch=silentStartup]')");assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),false);
 for(const lang of ['zh-CN','zh-TW','en','ja','ko','ru','es','pt']){
  language=lang;cfg.darkMode=lang==='en';win.webContents.send('neko:desktop-settings-changed',payload());
  await until(`document.documentElement.lang === ${JSON.stringify(lang)}`);
  await evaluate("document.querySelector('[data-patch=autoLaunch]').closest('.group').scrollIntoView({block:'start'})");
  const expected=trayMenuLocales[lang];
  for(const [id,key] of [['silent-startup-label','settingsSilentStartup'],['silent-startup-desc','settingsSilentStartupDesc']]) {
   assert.ok(expected[key],lang+'.'+key);
   assert.equal(await evaluate(`document.getElementById(${JSON.stringify(id)}).textContent`),expected[key],lang+'.'+id);
  }
  for(const [failed,key] of [[false,'settingsSilentStartupSaved'],[true,'settingsSilentStartupSaveFailed']]) {
   fail=failed;
   await evaluate("document.querySelector('[data-patch=silentStartup]').click()");
   await until(`document.getElementById('silent-startup-status').textContent === ${JSON.stringify(expected.settingsSilentStartupSaving)}`);
   await until(`document.getElementById('silent-startup-status').textContent === ${JSON.stringify(expected[key])}`);
  }
  fail=false;
  const overflow=await evaluate("(()=>{const r=document.getElementById('silent-startup-desc');return r.scrollWidth>r.clientWidth+1})()");assert.equal(overflow,false,lang);
  await screenshot('locale-'+lang);
 }
 fs.writeFileSync(path.join(out,'ui-results.json'),JSON.stringify({passed:true,cases:['default-off','accessible-label','pending-disables-double-click','persist-on','failed-write-rollback','ipc-rejection-rollback','persist-off','reload-hydration','production-controller-contract','untrusted-sender-rejected','thrown-write-rollback','focus-restored','eight-locales-exact-copy-and-no-overflow','dark-mode'],writes},null,2));
 console.log('Electron UI verification passed: '+out);win.destroy();app.quit();
}).catch(e=>{console.error(e);app.exit(1);});
