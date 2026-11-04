'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const vm=require('node:vm');
const root=process.env.NEKO_DESKTOP_SOURCE || process.cwd();
const {createDesktopSettingsActions}=require(path.join(root,'src/main/desktop-settings-actions'));
function harness(config={},saveConfig=()=>true){
 return createDesktopSettingsActions({getAppConfig:()=>config,process,saveConfig,t:key=>key});
}
test('missing and malformed preferences are disabled in snapshots',()=>{
 for(const silentStartup of [undefined,null,false,'true','false',1,{},[]]) assert.equal(harness({silentStartup}).getSnapshot().silentStartup,false);
 assert.equal(harness({silentStartup:true}).getSnapshot().silentStartup,true);
});
test('toggle persists before notification, retains unrelated preferences and survives action recreation',async()=>{
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'neko-startup-'));
 try {
  const file=path.join(dir,'core_config.txt'); const cfg={autoLaunch:false,silentStartup:false,globalAlwaysOnTop:false,customPorts:{MAIN_SERVER_PORT:23456}};
  const actions=harness(cfg,next=>{fs.writeFileSync(file,JSON.stringify(next));return true;}); let notified=0;
  actions.subscribe(()=>{notified++;assert.deepEqual(JSON.parse(fs.readFileSync(file)),cfg);});
  assert.equal(await actions.applyPatch('silentStartup',true),true);assert.equal(notified,1);
  const saved=JSON.parse(fs.readFileSync(file));assert.equal(harness(saved).getSnapshot().silentStartup,true);
  assert.equal(saved.autoLaunch,false);assert.equal(saved.globalAlwaysOnTop,false);assert.deepEqual(saved.customPorts,{MAIN_SERVER_PORT:23456});
  assert.equal(await actions.applyPatch('silentStartup',false),true);assert.equal(harness(JSON.parse(fs.readFileSync(file))).getSnapshot().silentStartup,false);
 } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
test('write rejection leaves live preference unchanged and sends no success notification',async()=>{
 const cfg={silentStartup:false};const actions=harness(cfg,()=>false);let notified=0;actions.subscribe(()=>notified++);
 assert.equal(await actions.applyPatch('silentStartup',true),false);assert.equal(cfg.silentStartup,false);assert.equal(notified,0);
});
test('thrown write leaves live preference unchanged',async()=>{
 const cfg={silentStartup:true};const actions=harness(cfg,()=>{throw new Error('fixture ENOSPC');});
 await assert.rejects(actions.applyPatch('silentStartup',false),/ENOSPC/);assert.equal(cfg.silentStartup,true);
});
test('non-boolean IPC values are rejected without writing',async()=>{
 let writes=0;const actions=harness({},()=>{writes++;});
 for(const value of [undefined,null,1,0,'true','false',{},[]]) assert.equal(await actions.applyPatch('silentStartup',value),false);
 assert.equal(writes,0);
});
test('missing app configuration cannot be mutated',async()=>{assert.equal(await harness(null).applyPatch('silentStartup',true),false);});
const main=fs.readFileSync(path.join(root,'src/main.js'),'utf8');
const loadingFunction=main.slice(main.indexOf('function createLoadingWindow() {'),main.indexOf('\nfunction updateLoadingWindowStatus('));
function runLoading(config){
 let constructed=0;let screenReads=0;
 const context={appConfig:config,log:()=>{},loadingText:key=>key,screen:{getPrimaryDisplay(){screenReads++;return {workAreaSize:{width:1920,height:1080}};}},getLauncherBannerSize:()=>({width:1000,height:600}),getTaskbarExclusionWindowOptions:()=>({}),getIcon:()=>null,BrowserWindow:class{constructor(){constructed++;throw new Error('window allocated');}}};
 vm.createContext(context);vm.runInContext(loadingFunction,context);
 let error;try{vm.runInContext('createLoadingWindow()',context);}catch(e){error=e;}
 return {constructed,screenReads,error};
}
test('silent startup allocates no loading window and never reads screen geometry',()=>{const r=runLoading({silentStartup:true});assert.equal(r.constructed,0);assert.equal(r.screenReads,0);assert.equal(r.error,undefined);});
test('legacy, disabled and malformed settings preserve the original window creation path',()=>{
 for(const cfg of [null,{}, {silentStartup:false},{silentStartup:'true'},{silentStartup:1}]) {const r=runLoading(cfg);assert.equal(r.constructed,1);assert.match(r.error.message,/window allocated/);}
});
test('status updates and cleanup tolerate absent loading window',()=>{
 const source=main.slice(main.indexOf('function updateLoadingWindowStatus('),main.indexOf('\nlet backendRuntime ='));
 const ctx={loadingWindow:null,loadingWindowStatus:{},isLoadingWindowLive:()=>false};vm.createContext(ctx);vm.runInContext(source,ctx);
 vm.runInContext("updateLoadingWindowStatus({title:'fixture'});closeLoadingWindow();",ctx);assert.equal(ctx.loadingWindowStatus.title,'fixture');
});
test('all eight desktop locales provide the complete feature copy',()=>{
 const {petSettingsLocales}=require(path.join(root,'src/main/tray-settings-pet-locales'));
 for(const lang of ['zh-CN','zh-TW','en','ja','ko','ru','es','pt'])for(const key of ['settingsSilentStartup','settingsSilentStartupDesc','settingsSilentStartupSaving','settingsSilentStartupSaved','settingsSilentStartupSaveFailed'])assert.ok(petSettingsLocales[lang][key],`${lang}.${key}`);
});
