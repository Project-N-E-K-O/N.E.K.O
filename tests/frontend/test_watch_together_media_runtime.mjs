import assert from 'node:assert/strict';

class Media extends EventTarget {
  constructor(){super();this.volume=1;this.currentTime=0;this.duration=2;this.paused=true;this.playbackRate=1;this.readyState=4;this.seeking=false;this.ended=false;}
  async play(){this.playCalls=(this.playCalls || 0)+1;this.paused=false;this.dispatchEvent(new Event('play'));this.dispatchEvent(new Event('playing'));}
  pause(){const was=this.paused;this.paused=true;if(!was)this.dispatchEvent(new Event('pause'));}
  removeAttribute(name){delete this[name];}load(){}
}
const audios=[];
globalThis.HTMLVideoElement=Media;
globalThis.Audio=class extends Media{constructor(){super();audios.push(this);}};
let gainUpdates=0;
const gains=[], sources=new WeakSet();
globalThis.AudioContext=class {createAnalyser(){return {connect(){},disconnect(){},getByteTimeDomainData(a){a.fill(128);}};}createMediaElementSource(element){assert.equal(sources.has(element),false);sources.add(element);return {connect(){},disconnect(){}};}createGain(){const node={gain:{value:1,setTargetAtTime(value){gainUpdates++;this.value=value;}},connect(){},disconnect(){}};gains.push(node);return node;}createDynamicsCompressor(){return {threshold:{},knee:{},ratio:{},attack:{},release:{},connect(){},disconnect(){}};}async resume(){}async suspend(){}async close(){}};
globalThis.window={};globalThis.document=new EventTarget();
globalThis.fetch=async()=>new Response('audio');
let nextFrame=null;
globalThis.requestAnimationFrame=fn=>{nextFrame=fn;return 1;};globalThis.cancelAnimationFrame=()=>{nextFrame=null;};
Object.defineProperty(globalThis,'navigator',{value:{locks:{request:async(_name,_options,fn)=>fn({})}}});
const {mount}=await import('../../static/game/sdk/neko-minigame-media-host.mjs');
const savedLocks=navigator.locks;
for(const locks of [null,{request:async(_name,_options,fn)=>fn(null)}]) {
  navigator.locks=locks;
  const owned=await mount({video:new Media(),timeline:{id:'owned',version:'v',status:'ready',video:'/video',events:[]}});
  await assert.rejects(owned.play(),{name:'AudioOwnershipError'});
  owned.dispose();
}
navigator.locks=savedLocks;
for(const granted of [true,false]) {
  let lockRequests=0,grantLock,lockReleased=false;
  navigator.locks={request:async(_name,_options,callback)=>{
    lockRequests++;lockReleased=false;
    const lock=await new Promise(resolve=>{grantLock=resolve;});
    await callback(lock);lockReleased=true;
  }};
  const shared=await mount({video:new Media(),timeline:{id:'shared',version:'v',status:'ready',video:'/video',events:[]}});
  const concurrent=[shared.play(),shared.play()];
  const outcomes=Promise.allSettled(concurrent);
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(lockRequests,1,'concurrent playback must share a pending ownership request');
  grantLock(granted?{}:null);
  const results=await outcomes;
  assert.ok(results.every(result=>granted?result.status==='fulfilled':result.reason?.name==='AudioOwnershipError'));
  if(!granted) {
    const retry=shared.play();
    await new Promise(resolve=>setTimeout(resolve,0));
    assert.equal(lockRequests,2,'failed ownership attempt permits a fresh request');
    grantLock({});await retry;
  }
  shared.dispose();
  await new Promise(resolve=>setTimeout(resolve,0));
  assert.equal(lockReleased,true);
}
navigator.locks=savedLocks;
audios.length=0;gains.length=0;
const events=[];const video=new Media();
const cue={id:'cue-1',at:4,duration:2,audio:'/api/watch-together/media/job/version/comment.mp3'};
const controller=await mount({video,timeline:{id:'job',version:'version',status:'ready',video:'/video',events:[cue]},onEvent:e=>events.push(e)});
await controller.play();
video.currentTime=3.95;nextFrame();assert.equal(audios[0].paused,true,'future cue is not consumed');
video.currentTime=4.05;nextFrame();assert.equal(audios[0].paused,false);assert.ok(Math.abs(audios[0].currentTime-.05)<.001);
assert.equal(gains[0].gain.value,.25,'reaction ducks soundtrack');
assert.equal(video.volume,1,'ducking leaves user volume untouched');
video.dispatchEvent(new Event('volumechange'));
assert.equal(audios[0].volume,1,'ducking preserves voice volume');
video.volume=.25;video.dispatchEvent(new Event('volumechange'));
video.pause();assert.equal(audios[0].paused,true,'pause stops reaction');
assert.equal(gains[0].gain.value,1,'pause restores soundtrack gain');
assert.equal(video.volume,.25,'user volume survives end of ducking');
await controller.play();assert.equal(audios[0].paused,false,'resume uses media offset');
video.dispatchEvent(new Event('waiting'));assert.equal(audios[0].paused,true,'buffering stops reaction');
video.dispatchEvent(new Event('playing'));assert.equal(audios[0].paused,false);
video.playbackRate=2;video.dispatchEvent(new Event('ratechange'));assert.equal(audios[0].playbackRate,2);
video.dispatchEvent(new Event('seeking'));video.currentTime=9;video.dispatchEvent(new Event('seeked'));nextFrame();assert.equal(audios[0].paused,true,'seek discards stale event');
video.dispatchEvent(new Event('seeking'));video.currentTime=3.95;video.dispatchEvent(new Event('seeked'));nextFrame();video.currentTime=4.05;nextFrame();assert.equal(audios[0].paused,false,'rewind rearms cue');
controller.interrupt();assert.equal(video.paused,true);assert.equal(audios[0].paused,true);
controller.dispose();assert.equal(nextFrame,null);assert.equal(audios[0].paused,true);
await assert.rejects(controller.play(),/disposed/);
assert.ok(events.some(e=>e.type==='audio-started'),'records actual audio playing event');
console.log('watch-together media runtime: 12 playback assertions passed');

const {ReactionClock}=await import('../../static/game/sdk/media-clock.mjs');
const duplicateClock=new ReactionClock([{at:1,id:'same'},{at:2,id:'same'},{at:3}]);
assert.equal(duplicateClock.tick(1,true).at,1);
assert.equal(duplicateClock.tick(2,true).at,2);
assert.equal(duplicateClock.tick(3,true).at,3);
duplicateClock.seek(2);assert.equal(duplicateClock.tick(2,true).at,2);
const v2=new Media();v2.volume=.25;v2.muted=true;
const textCues=[];
const textCue={at:0,audio:null,text:'A silent reaction'};
const c2=await mount({video:v2,timeline:{id:'job',version:'version',status:'ready',video:'/video',events:[textCue,cue]},onCue:cue=>textCues.push(cue)});
const a2=audios.at(-1);assert.equal(a2.volume,.25);assert.equal(a2.muted,true);
v2.volume=.5;v2.muted=false;v2.dispatchEvent(new Event('volumechange'));
assert.equal(a2.volume,.5);assert.equal(a2.muted,false);
let rejectOld;
a2.play=()=>{a2.paused=false;return new Promise((_resolve,reject)=>{rejectOld=reject;});};
await c2.play();nextFrame();
assert.equal(textCues.at(-1),textCue,'text-only reaction is displayed');
assert.equal(a2.paused,true,'text-only reaction does not start audio');
v2.currentTime=2;nextFrame();assert.equal(textCues.at(-1),textCue);
v2.currentTime=3;nextFrame();assert.equal(textCues.at(-1),null,'text without duration expires after three media seconds');
v2.currentTime=3.95;nextFrame();v2.currentTime=4.05;nextFrame();
const rejectBeforeRateChange=rejectOld;
v2.playbackRate=1.5;v2.dispatchEvent(new Event('ratechange'));
rejectBeforeRateChange(Error('AbortError'));await Promise.resolve();
assert.equal(v2.paused,false,'superseded rate-change attempt cannot pause the video');
v2.dispatchEvent(new Event('seeking'));v2.currentTime=8;v2.dispatchEvent(new Event('seeked'));
rejectOld(Error('obsolete'));await Promise.resolve();
assert.equal(v2.paused,false,'obsolete audio failure does not pause new video position');
c2.dispose();
await assert.rejects(mount({video:new Media(),timeline:{id:'job',version:'version',status:'ready',events:[{audio:'/unregistered'}]}}),/Unregistered/);
console.log('watch-together review regressions: cue identity, silent cues, volume and stale failures passed');
const gainCount=gains.length;
const again=await mount({video,timeline:{id:'job',version:'version',status:'ready',video:'/video',events:[]}});
await again.play();
const idleUpdates=gainUpdates;
for(let i=0;i<120;i++)nextFrame();
assert.equal(gainUpdates,idleUpdates,'idle frames do not schedule repeated gain automation');
assert.equal(gains.length,gainCount+1,'remount reuses video graph and creates only a voice gain');
again.dispose();
console.log('watch-together audio graph: remount reuses media element source');
const largeChunk=new Uint8Array(33*1024*1024);
let cancelledBudget=false;
globalThis.fetch=async()=>new Response(new ReadableStream({
  start(stream){stream.enqueue(largeChunk);stream.enqueue(largeChunk);},
  cancel(){cancelledBudget=true;},
}));
await assert.rejects(mount({video:new Media(),timeline:{id:'job',version:'version',status:'ready',events:[cue]}}),/budget exceeded/);
assert.equal(cancelledBudget,true);
let budgetFetches=0;
globalThis.fetch=async()=>{budgetFetches++;throw Error('must not fetch');};
await assert.rejects(mount({video:new Media(),timeline:{id:'job',version:'version',status:'ready',events:Array.from({length:257},(_,i)=>({...cue,audio:cue.audio+i}))}}),/budget exceeded/);
assert.equal(budgetFetches,0);
const {unlock}=await import('../../static/game/sdk/neko-minigame-media-host.mjs');
const unlocked=new Media();let gesturePlay=false;
unlocked.play=()=>{
  assert.match(unlocked.src,/^data:audio\/wav;base64,/,'first automatic gesture has a playable source');
  const wav=Buffer.from(unlocked.src.split(',')[1],'base64');
  assert.equal(wav.toString('ascii',0,4),'RIFF');assert.equal(wav.readUInt32LE(40),3200);
  assert.equal(wav.length,3244);
  gesturePlay=true;unlocked.paused=false;return Promise.resolve();
};
unlock(unlocked);
assert.equal(gesturePlay,true,'unlock calls media play synchronously');
assert.equal(unlocked.paused,true,'unlock does not leave video playing during startup');
const blessedReaction=audios.at(-1);
assert.equal(blessedReaction.playCalls,1,'reaction element is played synchronously during the gesture');
assert.equal(blessedReaction.paused,true);
const blessedMount=await mount({video:unlocked,timeline:{status:'ready',id:'j',version:'v',video:'/video',events:[]}});
assert.equal(audios.at(-1),blessedReaction,'mount reuses the already authorized reaction element');
blessedMount.dispose();

const backgroundVideo=new Media(),backgroundEvents=[];
globalThis.fetch=async()=>new Response('audio');
const background=await mount({video:backgroundVideo,timeline:{status:'ready',id:'job',version:'version',video:'/video',events:[cue]},keepPlayingWhenHidden:()=>true,onEvent:event=>backgroundEvents.push(event)});
await background.play();document.hidden=true;document.dispatchEvent(new Event('visibilitychange'));
assert.equal(backgroundVideo.paused,false,'automatic viewing continues when the window is hidden');
backgroundVideo.currentTime=4.1;backgroundVideo.dispatchEvent(new Event('timeupdate'));
assert.equal(audios.at(-1).paused,false,'media clock drives hidden reactions without animation frames');
const playingCalls=backgroundVideo.playCalls,position=backgroundVideo.currentTime;
unlock(backgroundVideo);
assert.equal(backgroundVideo.paused,false,'enabling automatic mode does not pause active video');
assert.equal(backgroundVideo.playCalls,playingCalls,'active video needs no probe play');
assert.equal(backgroundVideo.currentTime,position);
assert.equal(audios.at(-1).paused,false,'active reaction is also preserved');
backgroundVideo.dispatchEvent(new Event('error'));assert.equal(backgroundEvents.at(-1).type,'error');
background.dispose();document.hidden=false;
const cancelled=new AbortController();cancelled.abort();
await assert.rejects(mount({video:new Media(),timeline:{status:'ready',events:[]},signal:cancelled.signal}),{name:'AbortError'});
