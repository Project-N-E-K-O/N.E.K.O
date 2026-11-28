const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict'),test=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../../static/js/model_manager/pngtuber-downloads.js'),'utf8');
function fixture({initialFailure=false,beforeReply=null}={}){
 class Element{
  constructor(){this.style={};this.children=[];this.events={};this.textContent='';this.parentNode=null;}
  appendChild(e){e.parentNode=this;this.children.push(e);}
  replaceChildren(){for(const e of this.children)e.parentNode=null;this.children=[];}
  remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(e=>e!==this);this.parentNode=null;}
  addEventListener(type,fn){this.events[type]=fn;}
 }
 const panel=new Element(),document={getElementById:()=>panel,createElement:()=>new Element()};const root={};vm.runInNewContext(source,root);
 let data={id:'yui-chibi',name:'YUI',published:true,download_size:280745880,state:'idle',received:0,installed:false};
 const calls=[],timers=[],delays=[],pendingTimers=new Map();let installed=0,fail=initialFailure,nextTimer=0;
 const controller=root.createPNGTuberDownloadPanel({document,request:async(url,options)=>{calls.push({url,options});if(beforeReply)await beforeReply();if(fail)throw Error('offline');return {success:true,models:[data]};},onInstalled:()=>installed++,
 setTimer:(fn,delay)=>{const id=++nextTimer;pendingTimers.set(id,fn);timers.push(fn);delays.push(delay);return id;},clearTimer:id=>pendingTimers.delete(id)});
 return {panel,controller,calls,timers,delays,pendingTimers,root,Element,document,setData:next=>data={...data,...next},setFail:value=>fail=value,getInstalled:()=>installed,
 async fireTimer(){const [id,fn]=pendingTimers.entries().next().value;pendingTimers.delete(id);fn();await flush();}};
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
test('optional model is downloaded only on click and never selected automatically',async()=>{
 const f=fixture();await flush();assert.equal(f.calls.length,1);const row=f.panel.children[0],button=row.children[1];assert.equal(button.textContent,'下载模型');assert.match(row.children[0].textContent,/267.7 MiB/);
 await button.events.click();assert.equal(f.calls.at(-1).options.method,'POST');assert.match(f.calls.at(-1).url,/\/yui-chibi\/download$/);assert.equal(f.getInstalled(),0);
 f.setData({state:'complete',installed:true});await f.controller.refresh();assert.equal(f.getInstalled(),1);await f.controller.refresh();assert.equal(f.getInstalled(),1);
});
test('progress cancellation and atomic-install stage expose appropriate controls',async()=>{
 const f=fixture();await flush();f.setData({state:'downloading',received:40,total:100});await f.controller.refresh();let row=f.panel.children[0];assert.match(row.children[0].textContent,/40%/);assert.equal(row.children[1].disabled,false);
 await row.children[1].events.click();assert.match(f.calls.at(-1).url,/\/cancel$/);
 f.setData({state:'installing'});await f.controller.refresh();assert.equal(f.panel.children[0].children[1].disabled,true);
 f.setData({state:'failed',error:'checksum mismatch'});await f.controller.refresh();row=f.panel.children[0];assert.equal(row.children[1].textContent,'重试下载');assert.equal(row.children[2].textContent,'checksum mismatch');
});
test('unpublished entries cannot download and destroyed panels stop polling',async()=>{
 const f=fixture();await flush();f.setData({published:false});await f.controller.refresh();assert.equal(f.panel.children[0].children[1].disabled,true);
 f.controller.destroy();const count=f.calls.length;await f.controller.refresh();assert.equal(f.calls.length,count);
});

for(const changed of [false,true]) test(`completion list refresh preserves current selection and cannot override a newer choice (${changed})`,async()=>{
 const controllerSource=fs.readFileSync(path.join(__dirname,'../../static/js/model_manager/page-controller.js'),'utf8');
 const start=controllerSource.indexOf("    window.addEventListener('pngtuber-optional-installed', async () => {");
 const end=controllerSource.indexOf('    async function loadPNGTuberModels(',start);assert.ok(start>=0&&end>start);
 let listener,resolve;const pending=new Promise(r=>resolve=r);
 const box={window:{addEventListener:(name,fn)=>listener=fn},currentModelType:'pngtuber',userModelSelectionGeneration:0,modelDisplaySwitchGeneration:0,
 modelSelect:{value:'old',options:[{value:'old'},{value:'new'}]},updateLive2DModelDropdown(){},updateLive2DModelSelectButtonText(){}};
 box.loadPNGTuberModels=async({isStale})=>{await pending;if(!isStale())box.modelSelect.value='default'};
 vm.runInNewContext(controllerSource.slice(start,end),box);const result=listener();
 if(changed){box.userModelSelectionGeneration++;box.modelSelect.value='new';}
 resolve();await result;assert.equal(box.modelSelect.value,changed?'new':'old');
});

test('transient polling failure retains controls, retries with capped backoff, and recovers without reload',async()=>{
 const f=fixture();await flush();f.setData({state:'downloading',received:20,total:100});await f.controller.refresh();
 const row=f.panel.children[0],button=row.children[1];f.setFail(true);
 for(let i=0;i<6;i++)await f.fireTimer();
 assert.deepEqual(f.delays.slice(1),[500,1000,2000,4000,8000,8000]);
 assert.equal(f.panel.children[0],row);assert.equal(row.children[1],button);assert.equal(button.disabled,false);
 assert.match(f.panel.children.at(-1).textContent,/offline/);
 f.setFail(false);f.setData({received:60});await f.fireTimer();assert.match(row.children[0].textContent,/60%/);assert.equal(f.panel.children.at(-1).hidden,true);
 assert.equal(f.delays.at(-1),500);const stale=f.timers.at(-1);f.controller.destroy();const count=f.calls.length;stale();await flush();assert.equal(f.calls.length,count);assert.equal(f.pendingTimers.size,0);
});

test('initial status error retries and closed panels ignore manual refreshes',async()=>{
 const f=fixture({initialFailure:true});await flush();assert.equal(f.pendingTimers.size,1);
 f.setFail(false);await f.fireTimer();assert.equal(f.panel.children.find(row=>row.children.length===3).children[1].textContent,'下载模型');
 f.controller.destroy();const count=f.calls.length;await f.controller.refresh();assert.equal(f.calls.length,count);
});

test('status updates reuse the same button and deletion allows installation notifications again',async()=>{
 const f=fixture();await flush();const row=f.panel.children[0],button=row.children[1];
 f.setData({state:'downloading'});await f.controller.refresh();assert.equal(f.panel.children[0],row);assert.equal(row.children[1],button);
 await button.events.click();assert.match(f.calls.at(-1).url,/cancel$/,'stable button must use the latest action');
 f.setData({state:'complete',installed:true});await f.controller.refresh();assert.equal(f.getInstalled(),1);assert.equal(button.disabled,true);
 f.setData({state:'idle',installed:false});await f.controller.refresh();assert.equal(button.disabled,false);assert.equal(button.textContent,'下载模型');
 f.setData({state:'complete',installed:true});await f.controller.refresh();assert.equal(f.getInstalled(),2);
});

test('visible-panel and successful-deletion events refresh installed status',async()=>{
 const f=fixture();f.controller.destroy();const listeners={},select={value:'pngtuber',addEventListener:(name,fn)=>listeners['select:'+name]=fn};let installed=true,calls=0;
 f.document.getElementById=id=>id==='model-type-select'?select:f.panel;
 const root={document:f.document,addEventListener:(name,fn)=>listeners[name]=fn,setTimeout:()=>1,clearTimeout:()=>{},
 requestOptionalPNGTuber:async()=>{calls++;return {models:[{id:'x',name:'X',download_size:100,published:true,state:'idle',installed}]};},dispatchEvent:()=>{},CustomEvent:class{constructor(type){this.type=type;}}};
 vm.runInNewContext(source,root);listeners.DOMContentLoaded();await flush();let button=f.panel.children[0].children[1];assert.equal(button.disabled,true);
 installed=false;listeners['pngtuber-optional-refresh']();await flush();assert.equal(button.disabled,false);
 installed=true;select.value='live2d';listeners['select:change']();select.value='pngtuber';listeners['select:change']();await flush();assert.equal(button.disabled,true);assert.equal(calls,3);
 const controllerSource=fs.readFileSync(path.join(__dirname,'../../static/js/model_manager/page-controller.js'),'utf8');
 assert.ok(controllerSource.includes("if (deletedPNGTuber) window.dispatchEvent(new CustomEvent('pngtuber-optional-refresh'))"));
 listeners.pagehide();
});

test('a status request failing after pagehide cannot revive polling or replace its panel',async()=>{
 let reject;const pending=new Promise((_,fail)=>reject=fail);const f=fixture({beforeReply:()=>pending});
 f.controller.destroy();const children=f.panel.children.slice();reject(Error('offline after close'));await flush();
 assert.deepEqual(f.panel.children,children);assert.equal(f.pendingTimers.size,0);assert.equal(f.calls.length,1);
});
