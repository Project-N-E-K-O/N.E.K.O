const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),assert=require('node:assert/strict'),test=require('node:test');
const source=fs.readFileSync(path.join(__dirname,'../../static/js/model_manager/pngtuber-downloads.js'),'utf8');
function fixture(){
 class Element{constructor(){this.style={};this.children=[];this.events={};this.textContent='';}appendChild(e){this.children.push(e);}replaceChildren(){this.children=[];}addEventListener(type,fn){this.events[type]=fn;}}
 const panel=new Element(),document={getElementById:()=>panel,createElement:()=>new Element()};const root={};vm.runInNewContext(source,root);
 let data={id:'yui-chibi',name:'YUI',published:true,download_size:280745880,state:'idle',received:0,installed:false};const calls=[],timers=[];let installed=0,fail=false;
 const controller=root.createPNGTuberDownloadPanel({document,request:async(url,options)=>{calls.push({url,options});if(fail)throw Error('offline');return {success:true,models:[data]};},onInstalled:()=>installed++,setTimer:fn=>{timers.push(fn);return timers.length;},clearTimer:()=>{}});
 return {panel,controller,calls,timers,setData:next=>data={...data,...next},setFail:value=>fail=value,getInstalled:()=>installed};
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
