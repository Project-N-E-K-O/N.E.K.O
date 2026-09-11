import assert from 'node:assert/strict';

class Media extends EventTarget {
  constructor(){super();this.currentTime=0;this.duration=2;this.paused=true;this.playbackRate=1;this.readyState=4;this.seeking=false;this.ended=false;}
  async play(){this.paused=false;this.dispatchEvent(new Event('play'));this.dispatchEvent(new Event('playing'));}
  pause(){const was=this.paused;this.paused=true;if(!was)this.dispatchEvent(new Event('pause'));}
  removeAttribute(name){delete this[name];}load(){}
}
const audios=[];
globalThis.HTMLVideoElement=Media;
globalThis.Audio=class extends Media{constructor(){super();audios.push(this);}};
globalThis.AudioContext=class {createAnalyser(){return {connect(){},getByteTimeDomainData(a){a.fill(128);}};}createMediaElementSource(){return {connect(){}};}async resume(){}async close(){}};
globalThis.window={};globalThis.document=new EventTarget();
globalThis.fetch=async()=>({ok:true,blob:async()=>new Blob(['audio'])});
let nextFrame=null;
globalThis.requestAnimationFrame=fn=>{nextFrame=fn;return 1;};globalThis.cancelAnimationFrame=()=>{nextFrame=null;};
Object.defineProperty(globalThis,'navigator',{value:{locks:{request:async(_name,_options,fn)=>fn({})}}});
const {mount}=await import('../../static/game/sdk/neko-minigame-media-host.mjs');
const events=[];const video=new Media();
const cue={id:'cue-1',at:4,duration:2,audio:'/api/watch-together/media/job/version/comment.mp3'};
const controller=await mount({video,timeline:{id:'job',version:'version',status:'ready',video:'/video',events:[cue]},onEvent:e=>events.push(e)});
await controller.play();
video.currentTime=3.95;nextFrame();assert.equal(audios[0].paused,true,'future cue is not consumed');
video.currentTime=4.05;nextFrame();assert.equal(audios[0].paused,false);assert.ok(Math.abs(audios[0].currentTime-.05)<.001);
video.pause();assert.equal(audios[0].paused,true,'pause stops reaction');
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
