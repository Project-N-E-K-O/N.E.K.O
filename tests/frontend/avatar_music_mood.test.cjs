const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const code=fs.readFileSync('static/avatar/avatar-music-mood.js','utf8');
function setup(){
 const events={}, requests=[],timers=new Map();let timerId=0;
 let state={track:{name:'First',url:'/api/music/play/netease/123'},playbackId:'a',audible:true};
 const w={live2dManager:{currentModel:{}},getMusicPlaybackSnapshot:()=>state,isMusicAudible:()=>state.audible,
 addEventListener:(k,fn)=>(events[k]||=[]).push(fn),dispatchEvent:e=>(events[e.type]||[]).forEach(fn=>fn(e)),
 setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),setInterval(){},clearInterval(){}};
 vm.runInNewContext(code,{window:w,Date,AbortController,CustomEvent:class{constructor(type){this.type=type;}},
 fetch:(url,options)=>new Promise((resolve,reject)=>requests.push({options,resolve,reject,body:JSON.parse(options.body)}))});
 const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
 return {w,requests,timers,flush,change:s=>{state={...state,...s};w.nekoMusicMood.sync();},sync:()=>w.nekoMusicMood.sync(),
 async result(i,extra={}){requests[i].resolve({ok:true,json:async()=>({track_id:requests[i].body.track_id,category:'sad',confidence:.78,...extra})});await flush();}};
}

test('late A cannot replace B; repeated state ticks do not classify again',async()=>{
 const h=setup();h.sync();h.sync();assert.equal(h.requests.length,1);
 h.change({playbackId:'b',track:{name:'Second',url:'/api/music/play/netease/456'}});
 assert.ok(h.requests[0].options.signal.aborted);
 await h.result(0);assert.equal(h.w.nekoMusicMood.category(),'unknown');
 await h.result(1,{category:'joyful'});assert.equal(h.w.nekoMusicMood.category(),'joyful');
 assert.equal(h.w.nekoMusicMood.snapshot().result.track_id,'netease:456');
});
for(const stop of ['pause','end','model'])test(`late result after ${stop} cannot activate mood`,async()=>{
 const h=setup();h.sync();
 if(stop==='model')h.w.live2dManager.currentModel={};else h.change({audible:false,ended:stop==='end'});
 await h.result(0);assert.equal(h.w.nekoMusicMood.category(),'unknown');
});
for(const confidence of [0,.3,.59])test(`confidence ${confidence} remains default`,async()=>{
 const h=setup();h.sync();await h.result(0,{confidence});assert.equal(h.w.nekoMusicMood.category(),'unknown');
});
test('timeout aborts even a transport that resolves after abort',async()=>{
 const h=setup();h.sync();[...h.timers.values()][0]();await h.result(0);
 assert.equal(h.w.nekoMusicMood.category(),'unknown');
});
test('failure settles once without retry storms; resume starts a new generation',async()=>{
 const h=setup();h.sync();h.requests[0].reject(new Error('offline'));await h.flush();h.sync();
 assert.equal(h.requests.length,1);assert.equal(h.w.nekoMusicMood.snapshot().state,'unavailable');
 h.change({audible:false});h.change({audible:true});assert.equal(h.requests.length,2);
});
test('request sends bounded metadata, no signed URL or chat data',()=>{
 const h=setup();h.change({track:{id:42,source:'library',name:'x',url:'https://private/?token=secret',chat:'private',tags:['calm']}});
 assert.equal(JSON.stringify(h.requests[0].body).includes('secret'),false);
 assert.equal(JSON.stringify(h.requests[0].body).includes('private'),false);
 assert.equal(h.requests[0].body.track_id,'library:42');
});
