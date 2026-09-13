import assert from 'node:assert/strict';
import {run} from '../../static/game/games/watch-together/scene.mjs';

async function waitFor(predicate, diagnostics=()=> '') {
  const deadline=Date.now()+5000;
  while(!predicate()) {
    assert.ok(Date.now()<deadline,`scene did not reach the expected state within 5 seconds: ${diagnostics()}`);
    await new Promise(resolve=>setTimeout(resolve,10));
  }
}

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
      if(action==='load')return {status:'ready',video:'/video',id:'selected',version:'v',bvid:'BV1GJ411x7h7',title:'Cats',usage,events:[]};
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
const mediaFailure=await fixture(false,false,{total_tokens:1});
let mediaMounts=0,mediaDisposals=0,failureRouteEnds=0;
const mountBeforeFailure=mediaFailure.game.media.mount;
mediaFailure.game.media.mount=async options=>{
  mediaMounts++;
  const controller=await mountBeforeFailure(options);
  return {...controller,dispose(){mediaDisposals++;mediaFailure.elements.get('video').src='';}};
};
mediaFailure.game.runtime.end=async()=>{failureRouteEnds++;mediaFailure.game.runtime.state='ended';};
await mediaFailure.elements.get('play').onclick();
assert.equal(mediaFailure.elements.get('play').hidden,true);
mediaFailure.record({type:'error'});
await waitFor(()=>failureRouteEnds===1 && !mediaFailure.elements.get('play').disabled);
assert.equal(mediaDisposals,1,'asynchronous failure releases the mounted media controller');
assert.equal(mediaFailure.elements.get('video').src,'/video');
assert.equal(mediaFailure.elements.get('play').hidden,false);
assert.equal(mediaFailure.elements.get('video').controls,false);
assert.ok(mediaFailure.calls.some(call=>call.action==='watch' && call.payload.event?.type==='exit'));
await mediaFailure.elements.get('play').onclick();
assert.equal(mediaMounts,2,'manual retry mounts a fresh controller');
assert.equal(mediaFailure.elements.get('play').hidden,true);
mediaFailure.handlers['runtime-inactive']();
console.log('watch-together scene: asynchronous media error releases playback and permits manual retry');
for(const paused of [true,false]) {
  const enabling=await fixture(false,false,{total_tokens:1});
  let enablingPlays=0,enablingMounts=0;
  const enablingRuntimeCalls={start:0,reset:0};
  for(const method of ['start','reset']) {
    const original=enabling.game.runtime[method];
    enabling.game.runtime[method]=function(...args){enablingRuntimeCalls[method]++;return original.apply(this,args);};
  }
  const originalMount=enabling.game.media.mount;
  enabling.game.media.mount=async options=>{
    enablingMounts++;
    const controller=await originalMount(options);
    return {...controller,async play(){enablingPlays++;enabling.elements.get('video').paused=false;}};
  };
  await enabling.elements.get('play').onclick();
  const runtimeAfterPlay={...enablingRuntimeCalls};
  enabling.elements.get('video').paused=paused;
  enabling.elements.get('automatic-enabled').checked=true;
  enabling.elements.get('automatic-enabled').onchange();
  await waitFor(()=>enabling.calls.some(call=>call.action==='discover'));
  assert.equal(enablingPlays,paused?2:1,'enabling automatic resumes only paused playback');
  assert.equal(enablingMounts,1,'resume reuses the mounted media controller');
  assert.deepEqual(enablingRuntimeCalls,runtimeAfterPlay,'enabling automatic preserves the running route without start or reset');
  assert.equal(enabling.elements.get('video').paused,false);
  await enabling.elements.get('watch-stop').onclick();
}
console.log('watch-together scene: enabling automatic resumes paused playback without restarting active playback');
const overlapping=await fixture(false,false,{total_tokens:1});
let finishWatchStart,overlapStarts=0,overlapMounts=0;
const overlapRequest=overlapping.game.media.request;
overlapping.game.media.request=(action,payload)=>{
  if(action==='watch' && payload.action==='start') {
    overlapStarts++;
    return new Promise(resolve=>{finishWatchStart=()=>resolve({id:'shared-watch'});});
  }
  return overlapRequest(action,payload);
};
const overlapMount=overlapping.game.media.mount;
overlapping.game.media.mount=options=>{overlapMounts++;return overlapMount(options);};
const initialPlay=overlapping.elements.get('play').onclick();
await waitFor(()=>finishWatchStart);
overlapping.elements.get('automatic-enabled').checked=true;
overlapping.elements.get('automatic-enabled').onchange();
await new Promise(resolve=>setTimeout(resolve,20));
assert.equal(overlapStarts,1,'automatic shares the pending manual watch start');
finishWatchStart();await initialPlay;
await waitFor(()=>overlapping.calls.some(call=>call.action==='discover'));
assert.equal(overlapMounts,1);
await overlapping.elements.get('watch-stop').onclick();
assert.ok(overlapping.calls.some(call=>call.action==='watch' && call.payload.id==='shared-watch' && call.payload.event?.type==='exit'));
for(const analyses of [[],[{job:'unplayable',status:'incomplete'}]]) {
  const incomplete=await fixture(false);
  let preparationPolls=0;
  const originalRequest=incomplete.game.media.request;
  incomplete.game.media.request=async(action,payload)=>{
    if(action==='prepare')return {id:'unplayable'};
    if(action==='preparation'){preparationPolls++;return {status:'ready',persistence_complete:true};}
    if(action==='history')return {analyses};
    return originalRequest(action,payload);
  };
  await incomplete.elements.get('prepare').onsubmit({preventDefault(){}});
  assert.equal(preparationPolls,1);
  assert.match(incomplete.elements.get('status').textContent,/prepareFailed/);
  assert.equal(incomplete.elements.get('prepare-button').disabled,false);
}
const ownership=await fixture(false,false,{total_tokens:1});
ownership.game.media.mount=async()=>({dispose(){},async play(){throw Object.assign(Error('Another watch scene owns the audio'),{name:'AudioOwnershipError'});}});
ownership.elements.get('automatic-enabled').checked=true;
ownership.elements.get('automatic-enabled').onchange();
await waitFor(()=>!ownership.elements.get('automatic-enabled').checked && !ownership.elements.get('automatic-enabled').disabled);
assert.equal(ownership.calls.some(call=>['discover','prepare'].includes(call.action)),false,'ownership failure must not spend on replacement videos');
assert.equal(ownership.elements.get('video').src,'/video');
assert.equal(ownership.elements.get('play').disabled,false);
console.log('watch-together scene: shared watch start, terminal unplayable result and ownership stop passed');
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
  pendingLoads[1].resolve({status:'ready',id:'new',version:'v',title:'New selection',events:[]});await newer;
  if(failOld)pendingLoads[0].reject(Error('old load failed'));
  else pendingLoads[0].resolve({status:'ready',id:'old',version:'v',title:'Old selection',events:[]});
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
const exitTimeout=globalThis.setTimeout;
let exitFallback;
globalThis.setTimeout=(callback,delay,...args)=>{
  if(delay===100){exitFallback=()=>callback(...args);return 0;}
  return exitTimeout(callback,delay,...args);
};
try {await exiting.elements.get('exit').onclick();}
finally {globalThis.setTimeout=exitTimeout;}
assert.equal(disposed,true);assert.equal(closed,true);
assert.equal(typeof exitFallback,'function');
// Run the page-exit callback against its own window before the next fixture
// replaces browser globals; otherwise its delayed navigation corrupts that URL.
exitFallback();
assert.equal(location.href,'http://localhost/watch_together');
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
finishSelection({status:'ready',id:'new',version:'v',title:'New',events:[]});
await pendingSelection;
assert.equal(switching.elements.get('play').disabled,false);
const lateMount=await fixture(false,false,{total_tokens:1});
let finishMount, oldDisposed=0, oldPlayed=0, newPlayed=0;
lateMount.game.media.mount=()=>new Promise(resolve=>{finishMount=resolve;});
const oldPlay=lateMount.elements.get('play').onclick();
await new Promise(resolve=>setTimeout(resolve,0));
await lateMount.elements.get('history').children[0].onclick();
finishMount({dispose(){oldDisposed++;},async play(){oldPlayed++;}});
await oldPlay;
assert.equal(oldDisposed,1);
assert.equal(oldPlayed,0,'superseded mount must never start playing');
lateMount.game.media.mount=async()=>({dispose(){},async play(){newPlayed++;}});
await lateMount.elements.get('play').onclick();
assert.equal(newPlayed,1,'new selection must mount a fresh controller');
lateMount.handlers['runtime-inactive']();
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
pagingDuringPlayback.record({type:'ended',position:125});
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(pageRequests,1,'background refresh must not supersede pending navigation');
finishPage({watches:[{job:'Requested page'}],next_offset:null});
await navigating;
assert.match(pagingDuringPlayback.elements.get('watches').textContent,/Requested page/);
assert.equal(pageRequests,2,'final progress refresh must run after navigation');
finishPage({watches:[{job:'Requested page',progress:125}],next_offset:null});
await new Promise(resolve=>setTimeout(resolve,0));
assert.match(pagingDuringPlayback.elements.get('watches').textContent,/125s/);
pagingDuringPlayback.handlers['runtime-inactive']();
const analysisPaging=await fixture(false);
const analysisRequest=analysisPaging.game.media.request;
analysisPaging.game.media.request=async(action,payload)=>action==='history'
  ? {analyses:[{job:'older',version:'v',title:payload.offset?'Older analysis':'Recent analysis',status:'ready'}],next_offset:payload.offset?null:50}
  : analysisRequest(action,payload);
await analysisPaging.elements.get('history-previous').onclick();
await analysisPaging.elements.get('history-next').onclick();
assert.match(analysisPaging.elements.get('history').children[0].textContent,/Older analysis/);
assert.equal(analysisPaging.elements.get('history-next').disabled,true);
await analysisPaging.elements.get('history-previous').onclick();
assert.match(analysisPaging.elements.get('history').children[0].textContent,/Recent analysis/);
const longTitle=await fixture(false,false,{total_tokens:1});
const longRequest=longTitle.game.media.request;
longTitle.game.media.request=async(action,payload)=>{
  const result=await longRequest(action,payload);
  if(action==='load')result.title='猫'.repeat(300);
  return result;
};
await longTitle.elements.get('history').children[0].onclick();
await longTitle.elements.get('discover').onsubmit({preventDefault(){}});
assert.equal(longTitle.calls.filter(c=>c.action==='discover').at(-1).payload.topic.length,200);
const failedSelection=await fixture(false,false,{total_tokens:1});
failedSelection.game.media.mount=async()=>({play:async()=>{},dispose(){failedSelection.elements.get('video').src='';}});
await failedSelection.elements.get('play').onclick();
failedSelection.game.media.request=async(action)=>{if(action==='load')throw Error('load failed');return {watches:[]};};
await failedSelection.elements.get('history').children[0].onclick();
assert.equal(failedSelection.elements.get('video').src,'/video','failed load restores previous source for gesture unlock');
assert.equal(failedSelection.elements.get('play').disabled,false);
failedSelection.handlers['runtime-inactive']();
const incomplete=await fixture(false);
const incompleteRequest=incomplete.game.media.request;
// Route a successful preparation to an incomplete returned timeline.
incomplete.game.media.request=async(action,payload)=>{
 if(action==='load')return {status:'incomplete',id:'broken',events:[]};
 if(action==='prepare')return {id:'broken'};
 if(action==='preparation')return {status:'ready'};
 if(action==='history')return {analyses:[{job:'broken',version:'v',status:'ready'}]};
 return incompleteRequest(action,payload);
};
await incomplete.elements.get('prepare').onsubmit({preventDefault(){}});
assert.equal(incomplete.elements.get('play').disabled,true);
assert.equal(incomplete.calls.filter(c=>c.action==='watch').length,0);
const slowProgress=await fixture(false,false,{total_tokens:1});
await slowProgress.elements.get('play').onclick();
const slowRequest=slowProgress.game.media.request;
const positions=[];let releaseWrite;
slowProgress.game.media.request=async(action,payload)=>{
 if(action==='watch'){positions.push(payload.position);if(positions.length===1)await new Promise(resolve=>{releaseWrite=resolve;});return {};}
 return slowRequest(action,payload);
};
slowProgress.record({type:'progress',position:1});
await new Promise(resolve=>setTimeout(resolve,0));
for(let position=2;position<=12;position++)slowProgress.record({type:'progress',position});
slowProgress.record({type:'seek',position:200});
slowProgress.record({type:'progress',position:250});
releaseWrite();
await new Promise(resolve=>setTimeout(resolve,0));
assert.deepEqual(positions,[1,12,200,250],'only consecutive progress writes coalesce, preserving seek order');
slowProgress.handlers['runtime-inactive']();
const lateStart=await fixture(false,false,{total_tokens:1});
const lateStartRequest=lateStart.game.media.request;
let resolveStart, routeEnded=false;
const exitWrites=[];
lateStart.game.runtime.end=async()=>{routeEnded=true;};
lateStart.game.media.request=async(action,payload)=>{
  if(action==='watch' && payload.action==='start')return new Promise(resolve=>{resolveStart=resolve;});
  if(action==='watch') {
    assert.equal(routeEnded,false,'exit must be persisted before closing the route');
    exitWrites.push(payload);
  }
  return lateStartRequest(action,payload);
};
const startingPlay=lateStart.elements.get('play').onclick();
await new Promise(resolve=>setTimeout(resolve,0));
const switchingDuringStart=lateStart.elements.get('history').children[0].onclick();
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(routeEnded,false);
resolveStart({id:'late-watch'});
await Promise.all([startingPlay,switchingDuringStart]);
assert.equal(routeEnded,true);
assert.equal(exitWrites.length,1);
assert.equal(exitWrites[0].id,'late-watch');
assert.equal(exitWrites[0].event.type,'exit');
const inactiveStart=await fixture(false,false,{total_tokens:1});
const inactiveRequest=inactiveStart.game.media.request;
let finishInactiveStart, inactiveMounts=0;
const inactiveWrites=[];
inactiveStart.game.media.mount=async()=>{inactiveMounts++;throw Error('must not mount');};
inactiveStart.game.media.request=async(action,payload)=>{
  if(action==='watch') {
    if(payload.action==='start')return new Promise(resolve=>{finishInactiveStart=resolve;});
    inactiveWrites.push(payload);
    throw Error('inactive route');
  }
  return inactiveRequest(action,payload);
};
const inactivePlay=inactiveStart.elements.get('play').onclick();
await new Promise(resolve=>setTimeout(resolve,0));
inactiveStart.game.runtime.state='inactive';
inactiveStart.handlers['runtime-inactive']();
finishInactiveStart({id:'interrupted-attempt'});
await inactivePlay;
assert.equal(inactiveMounts,0);
assert.equal(inactiveWrites.length,0,'inactive routes cannot accept fabricated cleanup events');
assert.equal(inactiveStart.elements.get('video').controls,false);
const retryPlayback=await fixture(false,false,{total_tokens:1});
let failPlayback=true;
retryPlayback.game.media.mount=async()=>({
  async play(){if(failPlayback)throw Error('lock unavailable');},
  dispose(){retryPlayback.elements.get('video').src='';}
});
await retryPlayback.elements.get('play').onclick();
assert.equal(retryPlayback.elements.get('video').src,'/video','retry unlock needs the source before async startup');
assert.equal(retryPlayback.elements.get('play').disabled,false);
failPlayback=false;
await retryPlayback.elements.get('play').onclick();
assert.match(retryPlayback.elements.get('status').textContent,/playing/);
retryPlayback.handlers['runtime-inactive']();
assert.equal(retryPlayback.elements.get('video').src,'/video','runtime loss restores the source for trusted retry');
const nextSelection=await fixture(false,false,{total_tokens:1},()=>({video:{bvid:'next',url:'next',title:'Next'}}));
const nextRequest=nextSelection.game.media.request;
nextSelection.game.media.request=async(action,payload)=>{
  if(action==='prepare')return {id:'next-job'};
  if(action==='preparation')return {status:'ready'};
  if(action==='history')return {analyses:[{job:'next-job',version:'v',status:'ready'}]};
  return nextRequest(action,payload);
};
nextSelection.elements.get('prefetch-enabled').checked=true;
await nextSelection.elements.get('play').onclick();
await new Promise(resolve=>setTimeout(resolve,0));
assert.equal(nextSelection.elements.get('next-video').disabled,false);
const startsBeforeNext=nextSelection.calls.filter(call=>call.action==='watch' && call.payload.action==='start').length;
await nextSelection.elements.get('next-video').onclick();
assert.equal(nextSelection.calls.filter(call=>call.action==='watch' && call.payload.action==='start').length,startsBeforeNext,
  'selecting the next video must wait for a fresh Play gesture');
assert.equal(nextSelection.elements.get('play').hidden,false);
assert.equal(nextSelection.elements.get('play').disabled,false);
assert.equal(nextSelection.elements.get('video').src,'/video');
console.log('watch-together scene: all regressions passed, including next-video gesture boundary');

const continuous=await fixture(false,false,{total_tokens:1},()=>({video:{bvid:'next',url:'next',title:'Next'}}));
const continuousRequest=continuous.game.media.request;
let routeStarts=0,routeEnds=0,plays=0;
continuous.game.runtime.start=async()=>{routeStarts++;continuous.game.runtime.state='running';return {ok:true};};
continuous.game.runtime.end=async()=>{routeEnds++;continuous.game.runtime.state='ended';};
continuous.game.media.mount=async options=>{
  continuous.emit=options.onEvent;
  return {play:async()=>{plays++;continuous.elements.get('video').ended=false;},dispose(){}};
};
continuous.game.media.request=async(action,payload)=>{
  if(action==='prepare')return {id:'next-job'};
  if(action==='preparation')return {status:'ready'};
  if(action==='history')return {analyses:[{job:'next-job',version:'v',status:'ready'}]};
  return continuousRequest(action,payload);
};
continuous.elements.get('automatic-enabled').checked=true;
continuous.elements.get('automatic-enabled').onchange();
await waitFor(()=>plays===1 && !continuous.elements.get('next-video').disabled);
assert.equal(plays,1);
continuous.elements.get('video').ended=true;continuous.emit({type:'ended'});
await waitFor(()=>plays===2,()=>JSON.stringify({plays,routeStarts,routeEnds,status:continuous.elements.get('status').textContent,next:continuous.elements.get('next-status').textContent}));
assert.equal(plays,2,'ended automatically loads and plays the prepared next item');
assert.equal(routeStarts,1);assert.equal(routeEnds,0,'speech takeover stays active across videos');
await continuous.elements.get('watch-stop').onclick();
assert.equal(routeEnds,1);assert.equal(continuous.elements.get('automatic-enabled').checked,false);

const delayedAutomatic=await fixture(false,false,{total_tokens:1});
let releaseRoute,endedRoute=0;
delayedAutomatic.game.runtime.start=()=>new Promise(resolve=>{releaseRoute=()=>{delayedAutomatic.game.runtime.state='running';resolve({ok:true});};});
delayedAutomatic.game.runtime.end=async()=>{endedRoute++;delayedAutomatic.game.runtime.state='ended';};
delayedAutomatic.elements.get('automatic-enabled').checked=true;
delayedAutomatic.elements.get('automatic-enabled').onchange();
await waitFor(()=>typeof releaseRoute==='function');
const stopped=delayedAutomatic.elements.get('watch-stop').onclick();releaseRoute();await stopped;
assert.equal(endedRoute,1,'late runtime start is released after stop');
assert.equal(delayedAutomatic.calls.some(c=>c.action==='watch'),false,'late start cannot begin playback');
console.log('watch-together: automatic continuation keeps takeover, stop releases late startup');

let discovered=0,preparedJob='';const automaticLoadedJobs=[];
const emptyAutomatic=await fixture(false,false,null,()=>{const id=`candidate-${++discovered}`;return {video:{bvid:id,url:id,title:id}};});
const emptyRequest=emptyAutomatic.game.media.request;
let autoLoads=0,autoPlays=0;
emptyAutomatic.game.media.mount=async options=>{emptyAutomatic.emit=options.onEvent;return {play:async()=>{autoPlays++;emptyAutomatic.elements.get('video').ended=false;},dispose(){}};};
emptyAutomatic.game.media.request=async(action,payload)=>{
  if(action==='prepare'){preparedJob=payload.url;return {id:preparedJob};}
  if(action==='preparation')return {status:'ready'};
  if(action==='history')return {analyses:[{job:preparedJob,version:'v',status:'ready'}]};
  if(action==='load'){autoLoads++;automaticLoadedJobs.push(payload.job);}
  return emptyRequest(action,payload);
};
emptyAutomatic.elements.get('automatic-enabled').checked=true;emptyAutomatic.elements.get('automatic-enabled').onchange();
await waitFor(()=>autoPlays===1 && !emptyAutomatic.elements.get('next-video').disabled);
assert.equal(autoLoads,1);assert.equal(autoPlays,1,'automatic mode discovers and plays without an existing selection');
emptyAutomatic.elements.get('video').ended=true;emptyAutomatic.emit({type:'ended'});
await waitFor(()=>autoLoads===2 && autoPlays===2);
assert.equal(autoLoads,2,'first completion must load another item instead of replaying the first');
assert.deepEqual(automaticLoadedJobs,['candidate-1','candidate-2']);
await emptyAutomatic.elements.get('watch-stop').onclick();

// A failed current item is skipped, even if it failed before starting prefetch.
const realTimeout=globalThis.setTimeout;
globalThis.setTimeout=(fn,delay,...args)=>realTimeout(fn,Math.min(delay,10),...args);
const recovery=await fixture(false,false,{total_tokens:1},()=>({video:{bvid:'replacement',url:'replacement',title:'Replacement'}}));
const recoveryRequest=recovery.game.media.request;
let attempts=0,replacementLoads=0;const replacementLoadedJobs=[];
recovery.game.media.mount=async()=>({play:async()=>{attempts++;if(attempts===1)throw Error('unplayable current item');},dispose(){}});
recovery.game.media.request=async(action,payload)=>{
  if(action==='prepare')return {id:'replacement-job'};
  if(action==='preparation')return {status:'ready'};
  if(action==='history')return {analyses:[{job:'replacement-job',version:'v',status:'ready'}]};
  if(action==='load'){replacementLoads++;replacementLoadedJobs.push(payload.job);}
  return recoveryRequest(action,payload);
};
try {
  recovery.elements.get('automatic-enabled').checked=true;recovery.elements.get('automatic-enabled').onchange();
  for(let i=0;i<100 && attempts<2;i++)await new Promise(resolve=>realTimeout(resolve,10));
  assert.equal(attempts,2,'a failed Play must eventually play a newly discovered replacement');
  assert.equal(replacementLoads,1,'failure skips the broken selection rather than looping it');
  assert.deepEqual(replacementLoadedJobs,['replacement-job']);
  assert.ok(recovery.calls.some(call=>call.action==='discover'),'retry starts discovery when no next item was prefetched');
} finally {
  await recovery.elements.get('watch-stop').onclick();globalThis.setTimeout=realTimeout;
}
