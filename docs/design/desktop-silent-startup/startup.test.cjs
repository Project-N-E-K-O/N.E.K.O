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
const loadingFunction=main.slice(main.indexOf('function createLoadingWindow('),main.indexOf('\nfunction updateLoadingWindowStatus('));
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
// Execute the real declaration, creation, status and cleanup in one context.
function startupContext() {
 const {EventEmitter}=require('node:events');
 let allocated=0, shown=0, focused=0;
 class Window extends EventEmitter {
  constructor(){super();allocated++;this.webContents=new EventEmitter();this.webContents.executeJavaScript=()=>Promise.resolve();}
  isDestroyed(){return !!this.destroyed;} isMinimized(){return false;} isVisible(){return !!this.visible;}
  show(){this.visible=true;shown++;} focus(){focused++;} hide(){this.visible=false;}
  setAlwaysOnTop(){} destroy(){this.destroyed=true;this.emit('closed');}
 }
 const ctx={appConfig:{silentStartup:true},mainWindow:null,log:()=>{},console:{log:()=>{}},
  loadingText:key=>key,isAppQuitRequested:()=>false,requestAppQuit:()=>{},
  isLoadingWindowLive:win=>!!win&&!win.isDestroyed(),BrowserWindow:Window,process:{platform:'darwin'},
  screen:{getPrimaryDisplay:()=>({workAreaSize:{width:1920,height:1080}})},
  getLauncherBannerSize:()=>({width:1000,height:600}),getTaskbarExclusionWindowOptions:()=>({}),getIcon:()=>null,
  installLinuxWindowListVisibility:()=>{},writeLoadingWindowHtml:()=>'/fixture.html',startLoadingWindowLoad:()=>{}};
 vm.createContext(ctx);
 vm.runInContext(main.slice(main.indexOf('let loadingWindow;'),main.indexOf('let isMobileMode =')),ctx);
 vm.runInContext(main.slice(main.indexOf('function revealSilentStartupProgress('),main.indexOf('\nlet backendRuntime =')),ctx);
 const app=new EventEmitter();ctx.app=app;
 vm.runInContext(main.slice(main.indexOf("  app.on('second-instance'"),main.indexOf('\n// ===== 日志系统')).replace(/\n}\s*$/, ''),ctx);
 return {ctx,app,run:code=>vm.runInContext(code,ctx),counts:()=>({allocated,shown,focused})};
}
test('silent creation, status updates and cleanup share the real status declaration',()=>{
 const h=startupContext();h.run("createLoadingWindow();updateLoadingWindowStatus({title:'still starting'});closeLoadingWindow();");
 assert.equal(h.run('loadingWindowStatus.title'),'still starting');assert.equal(h.counts().allocated,0);
 assert.equal(h.run('isSilentStartupPending'),false);
});
for(const event of ['second-instance','activate']) test(event+' reveals pending progress once and stops after handoff',()=>{
 const h=startupContext();h.run("createLoadingWindow();updateLoadingWindowStatus({title:'slow backend'});");
 h.app.emit(event);assert.equal(h.counts().allocated,1);assert.ok(h.counts().shown);assert.ok(h.counts().focused);
 assert.equal(h.run('loadingWindowStatus.title'),'slow backend');h.app.emit(event);assert.equal(h.counts().allocated,1);
 h.run('closeLoadingWindow()');h.app.emit(event);assert.equal(h.counts().allocated,1);
});
test('late preference changes do not reveal a splash after startup or during quit',()=>{
 const h=startupContext();h.app.emit('activate');assert.equal(h.counts().allocated,0);
 h.run('createLoadingWindow()');h.ctx.isAppQuitRequested=()=>true;h.app.emit('activate');assert.equal(h.counts().allocated,0);
});
for(const code of ['NEKO_BACKEND_PROCESS_EXIT','NEKO_BACKEND_HEALTH_TIMEOUT']) test(code+' reaches the independent failure dialog without a splash',async()=>{
 const h=startupContext();h.run('createLoadingWindow()');let shown=false,quit=false;
 const error=Object.assign(new Error('fixture failure'),{code});
 h.ctx.err=error;h.ctx.originalUrl='http://127.0.0.1:48911';
 h.ctx.loadFailureDialog={show:async failure=>{assert.equal(failure.code,code);shown=true;assert.equal(quit,false);}};
 h.ctx.requestAppQuit=()=>{quit=true;};
 const begin=main.indexOf('.catch(async (err) => {',main.indexOf('      startPythonBackend()'));
 const end=main.indexOf('\n        });',begin);
 assert.ok(begin>=0&&end>begin,'production backend failure callback exists');
 await h.run('(async (err) => {'+main.slice(begin+'.catch(async (err) => {'.length,end)+'})(err)');
 assert.equal(shown,true);assert.equal(quit,true);assert.equal(h.run('isSilentStartupPending'),false);
 h.app.emit('activate');assert.equal(h.counts().allocated,0);
});
test('production load failure dialog presents native feedback and an error-box fallback',async()=>{
 const {createLoadFailureDialog}=require(path.join(root,'src/main/load-failure-dialog'));
 for(const reject of [false,true]){
  let boxes=0,dialogs=0;
  const d=createLoadFailureDialog({dialog:{showMessageBox:async options=>{dialogs++;assert.equal(options.type,'error');if(reject)throw Error('fixture');return {response:1};},showErrorBox:()=>boxes++},
   clipboard:{},getLanguage:()=> 'en',getLogSnapshot:()=>({text:''}),getContext:()=>({}),inspectPorts:async()=>({status:'ok',listeners:[]})});
  await d.show({url:'http://127.0.0.1:48911',code:'NEKO_BACKEND_HEALTH_TIMEOUT',description:'fixture timeout'});
  assert.equal(dialogs,1);assert.equal(boxes,reject?1:0);
 }
});
const renderer=fs.readFileSync(path.join(root,'src/renderer/settings/settings.js'),'utf8');
function saveContext(initial,reply){
 const ctx={payload:initial,document:{activeElement:null,body:{},querySelector:()=>null},renderSection:()=>{},ipcRenderer:{invoke:reply},PATCH:'fixture'};
 vm.createContext(ctx);vm.runInContext("let silentStartupActivity='';"+renderer.slice(renderer.indexOf('function snapshot()'),renderer.indexOf('function escapeHtml('))+renderer.slice(renderer.indexOf('async function saveSilentStartup('),renderer.indexOf('function renderGeneral()')),ctx);
 return {ctx,run:code=>vm.runInContext(code,ctx)};
}
test('renderer accepts success without a snapshot and preserves other fields',async()=>{
 const h=saveContext({snapshot:{autoLaunch:true}},async()=>({ok:true}));await h.run('saveSilentStartup(true)');
 assert.equal(h.ctx.payload.snapshot.silentStartup,true);assert.equal(h.ctx.payload.snapshot.autoLaunch,true);assert.equal(h.run('silentStartupActivity'),'saved');
});
test('renderer success can initialize a missing snapshot',async()=>{
 const h=saveContext({},async()=>({ok:true}));await h.run('saveSilentStartup(true)');assert.equal(h.ctx.payload.snapshot.silentStartup,true);
});
test('renderer rejection without a snapshot exits saving and can retry',async()=>{
 const h=saveContext({},async()=>{throw Error('fixture IPC rejection');});await h.run('saveSilentStartup(true)');assert.equal(h.run('silentStartupActivity'),'error');
 h.ctx.ipcRenderer.invoke=async()=>({ok:true,snapshot:{silentStartup:true}});await h.run('saveSilentStartup(true)');assert.equal(h.run('silentStartupActivity'),'saved');
});
test('renderer preserves state on rejection and accepts authoritative rollback',async()=>{
 const h=saveContext({snapshot:{silentStartup:true}},async()=>{throw Error('fixture');});await h.run('saveSilentStartup(false)');assert.equal(h.ctx.payload.snapshot.silentStartup,true);
 h.ctx.ipcRenderer.invoke=async()=>({ok:false,snapshot:{silentStartup:false}});await h.run('saveSilentStartup(true)');assert.equal(h.ctx.payload.snapshot.silentStartup,false);assert.equal(h.run('silentStartupActivity'),'error');
});
test('all eight desktop locales provide the complete feature copy',()=>{
 const {petSettingsLocales}=require(path.join(root,'src/main/tray-settings-pet-locales'));
 for(const lang of ['zh-CN','zh-TW','en','ja','ko','ru','es','pt'])for(const key of ['settingsSilentStartup','settingsSilentStartupDesc','settingsSilentStartupSaving','settingsSilentStartupSaved','settingsSilentStartupSaveFailed'])assert.ok(petSettingsLocales[lang][key],`${lang}.${key}`);
});
