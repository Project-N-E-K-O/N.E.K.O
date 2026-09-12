import assert from 'node:assert/strict';
import {run} from '../../static/game/games/watch-together/scene.mjs';

async function fixture(confirm, afterDownload=false, usage=null, discover=null) {
  const elements = new Map();
  globalThis.document = {createElement(){return {};},getElementById(id) {
    if (!elements.has(id)) elements.set(id,{value:'',textContent:'',disabled:false,children:[],replaceChildren(){this.children=[];},append(child){this.children.push(child);}});
    return elements.get(id);
  }};
  document.getElementById('url');document.getElementById('topic');
  globalThis.location = {search:usage?'?job=selected':'',href:'http://localhost/watch_together'};
  globalThis.history = {replaceState(){}};
  const prompts = [];
  globalThis.window = {i18n:{t:key=>key},confirm:message=>{prompts.push(message);return confirm;}};
  const calls = [];
  let watches = [], record;
  const handlers = {};
  const game = {
    runtime:{state:'idle',configure(){},reset(){this.state='idle';},async end(){},async start(){this.state='running';return {ok:true};}},
    speech:{onState(){}},voice:{onState(){},onTranscript(){}},events:{on(name,fn){handlers[name]=fn;}},
    media:{async mount(options){record=options.onEvent;return {async play(){},dispose(){}};},async request(action,payload) {
      calls.push({action,payload});
      if(action==='character')return {lanlan_name:'cat'};
      if(action==='history')return {analyses:usage?[{job:'selected',version:'v',status:'ready'}]:[],watches};
      if(action==='watches')return {watches};
      if(action==='watch'){watches=[{job:'selected',progress:payload.position || 0,last_watched:'now'}];return {id:'watch-1'};}
      if(action==='load')return {id:'selected',version:'v',bvid:'BV1GJ411x7h7',title:'Cats',usage,events:[]};
      if(action==='discover')return discover ? discover() : {video:null};
      if(action==='prepare') {
        if(afterDownload) {
          if(payload.confirmation_job){game.disposed=true;return {ok:true};}
          return {id:'downloaded'};
        }
        if(!payload.confirmed_duration)return {confirmation_required:true,video:{title:'Long',duration:301,url:'BV1GJ411x7h7'}};
        game.disposed=true;return {id:'prepared'};
      }
      if(action==='preparation' && afterDownload)return {status:'awaiting_confirmation',confirmation_required:true,confirmation_video:{title:'Boundary',duration:300.04}};
      throw Error(action);
    }}
  };
  await run(game,'cat');
  return {game,elements,calls,prompts,handlers,record:event=>record(event)};
}
const cancel = await fixture(false);
cancel.elements.get('url').value='BV1GJ411x7h7';
await cancel.elements.get('prepare').onsubmit({preventDefault(){}});
assert.equal(cancel.prompts.length,1);
assert.equal(cancel.calls.filter(call=>call.action==='prepare').length,1);
assert.match(cancel.elements.get('status').textContent,/cancelled/);
const accept = await fixture(true);
accept.elements.get('url').value='BV1GJ411x7h7';
await accept.elements.get('prepare').onsubmit({preventDefault(){}});
assert.equal(accept.calls.filter(call=>call.action==='prepare').length,2);
assert.equal(accept.calls.at(-1).payload.confirmed_duration,301);
const empty = await fixture(false);
empty.elements.get('topic').value='cats';
await empty.elements.get('discover').onsubmit({preventDefault(){}});
assert.equal(empty.calls.at(-1).payload.topic,'cats');
assert.equal(empty.calls.filter(call=>call.action==='prepare').length,0);
assert.match(empty.elements.get('status').textContent,/noCandidates/);
console.log('watch-together scene: cancel, confirm and empty discovery passed');
for (const accepted of [true,false]) {
  const boundary = await fixture(accepted,true);
  boundary.elements.get('url').value='BV1GJ411x7h7';
  await boundary.elements.get('prepare').onsubmit({preventDefault(){}});
  assert.equal(boundary.prompts.length,1);
  assert.match(boundary.prompts[0],/301s/);
  const decision=boundary.calls.at(-1).payload;
  assert.equal(decision.confirmation_job,'downloaded');
  assert.equal(decision.accepted,accepted);
  assert.equal(decision.confirmed_duration,300.04);
}
console.log('watch-together scene: post-download confirmation and cancellation passed');
const legacy = await fixture(false,false,{input_tokens:24652,output_tokens:918,total_tokens:25570});
assert.match(legacy.elements.get('usage').textContent,/24652/);
assert.match(legacy.elements.get('usage').textContent,/25570/);
await legacy.elements.get('discover').onsubmit({preventDefault(){}});
assert.deepEqual(legacy.calls.at(-1).payload,{topic:'Cats',exclude:['BV1GJ411x7h7']});
const missing = await fixture(false,false,{input_tokens:0,calls:[]});
assert.match(missing.elements.get('usage').textContent,/unrecorded/);
console.log('watch-together scene: legacy totals and manual discovery exclusions passed');
const watching = await fixture(false,false,{total_tokens:1});
assert.match(watching.elements.get('watches').textContent,/noWatches/);
await watching.elements.get('play').onclick();
assert.match(watching.elements.get('watches').textContent,/selected.*0s/);
watching.record({type:'ended',position:122});
await new Promise(resolve=>setTimeout(resolve,0));
assert.match(watching.elements.get('watches').textContent,/selected.*122s/);
watching.handlers['runtime-inactive']();
console.log('watch-together scene: persisted watch start and completion refresh without reload');
let finishOldDiscovery, discoveryCount=0;
const retrying=await fixture(false,false,{total_tokens:1},()=>{
  discoveryCount++;
  return discoveryCount===1 ? new Promise(resolve=>{finishOldDiscovery=resolve;}) : {video:null};
});
const prefetch=retrying.elements.get('prefetch-enabled');
prefetch.checked=true;
await retrying.elements.get('play').onclick();
assert.equal(discoveryCount,1);
prefetch.checked=false;prefetch.onchange();
prefetch.checked=true;prefetch.onchange();
assert.equal(discoveryCount,1,'invalidated work still owns queue');
finishOldDiscovery({video:null});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(discoveryCount,2,'queue release retries current selection exactly once');
retrying.handlers['runtime-inactive']();
console.log('watch-together scene: queue release retries current playback without duplicate prefetch');
for (const terminal of [{status:'ready'},{status:'awaiting_confirmation',confirmation_required:true,confirmation_video:{title:'Old',duration:350}},{status:'error',error:'Old failure'}]) {
const takeover=await fixture(false,false,{total_tokens:1});
let staleDecision;
const originalRequest=takeover.game.media.request;
let finishForeground;
takeover.game.media.request=async(action,payload)=>{
  if(action==='prepare'){if(payload.confirmation_job)staleDecision=payload;return {id:'foreground'};}
  if(action==='preparation')return new Promise(resolve=>{finishForeground=resolve;});
  const result=await originalRequest(action,payload);
  if(action==='history')result.analyses.push({job:'foreground',version:'v',status:'ready'});
  return result;
};
const preparingForeground=takeover.elements.get('prepare').onsubmit({preventDefault(){}});
await new Promise(resolve=>setTimeout(resolve,0));
await takeover.elements.get('history').children[0].onclick();
await takeover.elements.get('play').onclick();
const loadsBeforeReady=takeover.calls.filter(c=>c.action==='load').length;
finishForeground(terminal);
await preparingForeground;
assert.equal(takeover.calls.filter(c=>c.action==='load').length,loadsBeforeReady,'foreground result cannot replace newer history selection');
assert.match(takeover.elements.get('status').textContent,/playing/);
takeover.handlers['runtime-inactive']();
assert.equal(takeover.prompts.length,0,'stale job cannot display confirmation');
if(terminal.confirmation_required)assert.equal(staleDecision.accepted,false,'release stale confirmation without paid work');
}
console.log('watch-together scene: stale completion, confirmation and failure preserve newer playback');
for (const failOld of [false,true]) {
  const racing=await fixture(false,false,{total_tokens:1});
  const baseRequest=racing.game.media.request;
  const pendingLoads=[];
  racing.game.media.request=(action,payload)=>action==='load' ? new Promise((resolve,reject)=>pendingLoads.push({resolve,reject})) : baseRequest(action,payload);
  const button=racing.elements.get('history').children[0];
  const older=button.onclick();await new Promise(resolve=>setTimeout(resolve,0));
  const newer=button.onclick();await new Promise(resolve=>setTimeout(resolve,0));
  pendingLoads[1].resolve({id:'new',version:'v',title:'New selection',events:[]});await newer;
  if(failOld)pendingLoads[0].reject(Error('old load failed'));
  else pendingLoads[0].resolve({id:'old',version:'v',title:'Old selection',events:[]});
  await older;
  assert.equal(racing.elements.get('title').textContent,'New selection');
  assert.match(racing.elements.get('status').textContent,/ready/);
}
console.log('watch-together scene: superseded load success and failure preserve newest selection');
const shutdown=await fixture(false,false,{total_tokens:1});
await shutdown.elements.get('play').onclick();
let finishShutdown, endCalls=0;
shutdown.game.runtime.end=()=>{
  endCalls++;
  if(endCalls>1)throw Error('busy');
  shutdown.game.runtime.state='ending';
  return new Promise(resolve=>{finishShutdown=()=>{shutdown.game.runtime.state='ended';resolve();};});
};
const historyButton=shutdown.elements.get('history').children[0];
const firstSelection=historyButton.onclick();await new Promise(resolve=>setTimeout(resolve,0));
const secondSelection=historyButton.onclick();await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(endCalls,1,'overlapping selections share runtime shutdown');
finishShutdown();await Promise.all([firstSelection,secondSelection]);
assert.match(shutdown.elements.get('status').textContent,/ready/);
assert.equal(shutdown.elements.get('play').disabled,false);
console.log('watch-together scene: overlapping selections serialize active shutdown');
for(const fail of [false,true]) {
  let resolveDiscovery,rejectDiscovery;
  const discovering=await fixture(false,false,{total_tokens:1},()=>new Promise((resolve,reject)=>{resolveDiscovery=resolve;rejectDiscovery=reject;}));
  const pending=discovering.elements.get('discover').onsubmit({preventDefault(){}});
  await discovering.elements.get('history').children[0].onclick();
  await discovering.elements.get('play').onclick();
  if(fail)rejectDiscovery(Error('stale discovery'));
  else resolveDiscovery({video:{url:'old',title:'Old result',duration:60,danmaku_per_minute:200}});
  await pending;
  assert.equal(discovering.calls.filter(c=>c.action==='prepare').length,0);
  assert.match(discovering.elements.get('status').textContent,/playing/);
  discovering.handlers['runtime-inactive']();
}
const exiting=await fixture(false,false,{total_tokens:1});
await exiting.elements.get('play').onclick();
let disposed=false,closed=false;
exiting.game.runtime.end=async()=>{throw Error('shutdown failed');};
exiting.game.dispose=()=>{disposed=true;};
window.close=()=>{closed=true;window.closed=true;};
await exiting.elements.get('exit').onclick();
assert.equal(disposed,true);assert.equal(closed,true);
console.log('watch-together scene: stale discovery ignored and failed shutdown still closes');
let finishCandidate, discoveries=0;
const exclusions=await fixture(false,false,{total_tokens:1},()=>{
  discoveries++;
  return discoveries===1 ? {video:{bvid:'candidate',url:'candidate',title:'Candidate'}} : {video:null};
});
const originalRequest=exclusions.game.media.request;
exclusions.game.media.request=(action,payload)=>{
  if(action==='prepare')return Promise.resolve({id:'candidate-job'});
  if(action==='preparation')return new Promise(resolve=>{finishCandidate=resolve;});
  if(action==='history')return Promise.resolve({analyses:[{job:'candidate-job',version:'v',status:'ready'}]});
  return originalRequest(action,payload);
};
const enabled=exclusions.elements.get('prefetch-enabled');enabled.checked=true;
await exclusions.elements.get('play').onclick();
await new Promise(resolve=>setTimeout(resolve,0));
enabled.checked=false;enabled.onchange();enabled.checked=true;enabled.onchange();
finishCandidate({status:'ready'});
await new Promise(resolve=>setTimeout(resolve,0));
const discoveryCalls=exclusions.calls.filter(call=>call.action==='discover');
assert.equal(discoveryCalls.length,2);
assert.ok(discoveryCalls[1].payload.exclude.includes('candidate'),'invalidated candidate must be excluded from next search');
exclusions.handlers['runtime-inactive']();
const switching=await fixture(false,false,{total_tokens:1});
let rejectMount, finishSelection;
switching.game.media.mount=()=>new Promise((resolve,reject)=>{rejectMount=reject;});
const pendingPlay=switching.elements.get('play').onclick();
await new Promise(resolve=>setTimeout(resolve,0));
const selectionRequest=switching.game.media.request;
switching.game.media.request=(action,payload)=>action==='load'
  ? new Promise(resolve=>{finishSelection=resolve;}) : selectionRequest(action,payload);
const pendingSelection=switching.elements.get('history').children[0].onclick();
await new Promise(resolve=>setTimeout(resolve,0));
rejectMount(Error('cancelled'));
await pendingPlay;
assert.equal(switching.elements.get('play').disabled,true,'old Play must not enable pending selection');
finishSelection({id:'new',version:'v',title:'New',events:[]});
await pendingSelection;
assert.equal(switching.elements.get('play').disabled,false);
const paged=await fixture(false);
const pageRequest=paged.game.media.request;
paged.game.media.request=async(action,payload)=>action==='watches'
  ? {watches:[{job:payload.offset?'Older':'Recent',progress:1}],next_offset:payload.offset?null:50}
  : pageRequest(action,payload);
await paged.elements.get('watches-previous').onclick();
assert.equal(paged.elements.get('watches-next').disabled,false);
await paged.elements.get('watches-next').onclick();
assert.match(paged.elements.get('watches').textContent,/Older/);
assert.equal(paged.elements.get('watches-previous').disabled,false);
assert.equal(paged.elements.get('watches-next').disabled,true);
await paged.elements.get('watches-previous').onclick();
assert.match(paged.elements.get('watches').textContent,/Recent/);
const pagingDuringPlayback=await fixture(false,false,{total_tokens:1});
await pagingDuringPlayback.elements.get('play').onclick();
const playbackRequest=pagingDuringPlayback.game.media.request;
let finishPage, pageRequests=0;
pagingDuringPlayback.game.media.request=(action,payload)=>{
  if(action==='watches') {
    pageRequests++;
    return new Promise(resolve=>{finishPage=resolve;});
  }
  return playbackRequest(action,payload);
};
const navigating=pagingDuringPlayback.elements.get('watches-previous').onclick();
pagingDuringPlayback.record({type:'progress',position:5});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(pageRequests,1,'background refresh must not supersede pending navigation');
finishPage({watches:[{job:'Requested page'}],next_offset:null});
await navigating;
assert.match(pagingDuringPlayback.elements.get('watches').textContent,/Requested page/);
pagingDuringPlayback.handlers['runtime-inactive']();
