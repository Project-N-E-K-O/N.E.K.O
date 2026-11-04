'use strict';
const {app,BrowserWindow,ipcMain}=require('electron');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const source=path.resolve(process.env.NEKO_DESKTOP_SOURCE || process.cwd());
const original=path.resolve(process.env.NEKO_DESKTOP_BASE_SOURCE || source);
const out=path.resolve(process.env.NEKO_STARTUP_TEST_OUTPUT || 'output/silent-startup');fs.mkdirSync(out,{recursive:true});
app.setName('NEKO Silent Startup Verification');app.setPath('userData',path.join(out,'fixture-user-data'));
const {createDesktopSettingsActions}=require(path.join(source,'src/main/desktop-settings-actions'));
const {trayMenuLocales}=require(path.join(source,'src/main/tray-menu-locales'));
let cfg={silentStartup:false,autoLaunch:true,globalAlwaysOnTop:false,widgetModeEnabled:true};
let language='zh-CN',fail=false,throwIpc=false,delay=0,writes=0;
const actions=createDesktopSettingsActions({getAppConfig:()=>cfg,saveConfig:next=>{if(fail)return false;writes++;fs.writeFileSync(path.join(out,'fixture-config.json'),JSON.stringify(next));return true;},process,t:key=>key,getInterfaceLanguage:()=>language});
let win;
function payload(){const snapshot=actions.getSnapshot();snapshot.autoLaunch={enabled:true,supported:true};snapshot.petPrefs={available:true,values:{}};return {snapshot,language,texts:trayMenuLocales[language],section:'general'};}
ipcMain.handle('neko:desktop-settings-get',()=>payload());
ipcMain.handle('neko:desktop-settings-patch',async(e,p)=>{if(delay)await new Promise(r=>setTimeout(r,delay));if(throwIpc)throw new Error('fixture IPC failure');const ok=await actions.applyPatch(p.key,p.value);win.webContents.send('neko:desktop-settings-changed',payload());return {ok,snapshot:payload().snapshot};});
ipcMain.handle('neko:host:get-pin-state',()=>({available:false}));
const evaluate=code=>win.webContents.executeJavaScript(code);
async function until(code){for(let i=0;i<120;i++){if(await evaluate(code))return;await new Promise(r=>setTimeout(r,25));}throw new Error('timed out: '+code);}
async function screenshot(name){await new Promise(r=>setTimeout(r,150));fs.writeFileSync(path.join(out,name+'.png'),(await win.webContents.capturePage()).toPNG());}
app.whenReady().then(async()=>{
 win=new BrowserWindow({width:900,height:720,show:false,webPreferences:{nodeIntegration:true,contextIsolation:false}});
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
 await screenshot('after-enabled');
 fail=true;await evaluate("document.querySelector('[data-patch=silentStartup]').click()");await until("document.getElementById('silent-startup-status').textContent.includes('保存失败')");
 assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),true);assert.equal(cfg.silentStartup,true);await screenshot('save-error');
 fail=false;throwIpc=true;await evaluate("document.querySelector('[data-patch=silentStartup]').click()");await until("document.getElementById('silent-startup-status').textContent.includes('保存失败')");assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),true);
 throwIpc=false;await evaluate("document.querySelector('[data-patch=silentStartup]').click()");await until("document.getElementById('silent-startup-status').textContent.includes('已保存')");assert.equal(cfg.silentStartup,false);
 // Reload verifies hydration from the main-process preference.
 await win.reload();await until("!!document.querySelector('[data-patch=silentStartup]')");assert.equal(await evaluate("document.querySelector('[data-patch=silentStartup]').checked"),false);
 for(const lang of ['zh-CN','zh-TW','en','ja','ko','ru','es','pt']){
  language=lang;cfg.darkMode=lang==='en';win.webContents.send('neko:desktop-settings-changed',payload());
  await until(`document.documentElement.lang === ${JSON.stringify(lang)}`);
  await evaluate("document.querySelector('[data-patch=autoLaunch]').closest('.group').scrollIntoView({block:'start'})");
  const overflow=await evaluate("(()=>{const r=document.getElementById('silent-startup-desc');return r.scrollWidth>r.clientWidth+1})()");assert.equal(overflow,false,lang);
  await screenshot('locale-'+lang);
 }
 fs.writeFileSync(path.join(out,'ui-results.json'),JSON.stringify({passed:true,cases:['default-off','accessible-label','pending-disables-double-click','persist-on','failed-write-rollback','ipc-rejection-rollback','persist-off','reload-hydration','eight-locales-no-overflow','dark-mode'],writes},null,2));
 console.log('Electron UI verification passed');win.destroy();app.quit();
}).catch(e=>{console.error(e);app.exit(1);});
