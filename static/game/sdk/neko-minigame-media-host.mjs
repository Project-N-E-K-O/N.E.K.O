/** Trusted host: the only owner of pre-recorded reaction audio. */
import { ReactionClock } from './media-clock.mjs';
// A real 100 ms mono PCM WAV also plays in a video element. It lets the first
// automatic-search gesture authorize that element before a video is available.
const silence = (() => {
  const bytes=new Uint8Array(44+3200), view=new DataView(bytes.buffer);
  const tag=(at,text)=>{for(let i=0;i<text.length;i++)bytes[at+i]=text.charCodeAt(i);};
  tag(0,'RIFF');view.setUint32(4,bytes.length-8,true);tag(8,'WAVE');tag(12,'fmt ');
  view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,16000,true);view.setUint32(28,32000,true);
  view.setUint16(32,2,true);view.setUint16(34,16,true);tag(36,'data');view.setUint32(40,3200,true);
  return 'data:audio/wav;base64,'+btoa(String.fromCharCode(...bytes));
})();
// A media element can be attached to Web Audio only once, including across mounts.
const videoGraphs = new WeakMap();
function videoGraph(video) {
  let graph=videoGraphs.get(video);
  if(!graph) {
    const context=new AudioContext(), soundtrack=context.createGain();
    context.createMediaElementSource(video).connect(soundtrack);soundtrack.connect(context.destination);
    const audio=new Audio(), reactionSource=context.createMediaElementSource(audio);
    graph={context,soundtrack,audio,reactionSource};videoGraphs.set(video,graph);
  }
  return graph;
}

// Called by the trusted page directly inside the user's click, before network work.
export function unlock(video) {
  const graph=videoGraph(video);
  void graph.context.resume().catch(()=>{});
  // Enabling automatic mode during playback must not pause video or reaction.
  if(!video.paused)return;
  if(!video.src)video.src=silence;
  const started=video.play();
  video.pause();
  void started?.catch(()=>{});
  // Reuse this exact reaction element across mounts; Safari gates each element.
  if(!graph.audio.src)graph.audio.src=silence;
  const reactionStarted=graph.audio.play();
  graph.audio.pause();
  void reactionStarted?.catch(()=>{});
}

export async function mount({ video, timeline, signal, onEvent = () => {}, onCue = () => {}, onMouth = () => {}, keepPlayingWhenHidden = () => false }) {
  if (!(video instanceof HTMLVideoElement) || timeline.status !== 'ready') throw Error('Media is not ready');
  const prefix = `/api/watch-together/media/${timeline.id}/${timeline.version}`;
  const resources = new Map();
  const cues=timeline.events || [];
  if(cues.length>1000)throw Error('Reaction preload budget exceeded');
  const urls=new Set(cues.map(cue=>cue.audio).filter(url=>url!=null && url!==''));
  if(urls.size>256)throw Error('Reaction preload budget exceeded');
  let remaining=64*1024*1024;
  try {
    for (const url of urls) {
      if (url == null || url === '') continue;
      if (typeof url !== 'string' || !url.startsWith(`${prefix}/`)) throw Error('Unregistered timeline resource');
      const response = await fetch(url, {signal});
      if (!response.ok) throw Error('Reaction preload failed');
      const reader=response.body?.getReader();
      if(!reader)throw Error('Reaction preload failed');
      const chunks=[];
      try {
        while(true) {
          const {done,value}=await reader.read();
          if(done)break;
          remaining-=value.byteLength;
          if(remaining<0)throw Error('Reaction preload budget exceeded');
          chunks.push(value);
        }
      } catch(error) {await reader.cancel().catch(()=>{});throw error;}
      finally {reader.releaseLock();}
      resources.set(url, URL.createObjectURL(new Blob(chunks,{type:response.headers.get('content-type') || ''})));
    }
    if(signal?.aborted)throw new DOMException('Media mount cancelled','AbortError');
  } catch(error) {for(const url of resources.values())URL.revokeObjectURL(url);throw error;}
  const clock = new ReactionClock(timeline.events || []);
  let active = null, disposed = false, waiting = false, frame = 0, release = null;
  let lockPending = null;
  let generation = 0, playingGeneration = -1, playAttempt = 0;
  const graph = videoGraph(video), audio = graph.audio;
  let context = null, analyser = null, soundtrack = null;
  const voiceNodes = [];
  let outputStopped = true, ducked = false;
  const waveform = new Uint8Array(128);
  audio.preload = 'auto';
  const listeners = [];
  const running = () => !disposed && !waiting && !video.paused && !video.seeking && video.readyState >= 3;
  const emit = (type, cue = '') => onEvent({ type, cue, position: video.currentTime });
  const duckSoundtrack = value => {
    if(ducked===value)return;
    ducked=value;
    if (soundtrack) soundtrack.gain.setTargetAtTime(value ? 0.25 : 1, context.currentTime, value ? 0.03 : 0.15);
  };
  function stop(clear = false) {
    if(outputStopped && (!clear || !active))return;
    outputStopped=true;
    playAttempt++;
    audio.pause();
    duckSoundtrack(false);
    onMouth(0);
    if (clear) { active = null; audio.removeAttribute('src'); onCue(null); }
  }
  function sync() {
    if (!active || !running()) { stop(); return; }
    const offset = video.currentTime - active.at;
    const duration=Number.isFinite(active.duration) && active.duration>0?active.duration:3;
    if (offset < 0 || offset >= duration) { stop(true); return; }
    if (!active.audio) return;
    audio.playbackRate = video.playbackRate;
    if (audio.paused) {
      outputStopped=false;
      audio.currentTime = offset;
      playingGeneration = generation;
      const attemptGeneration = generation, attemptCue = active, attempt = ++playAttempt;
      audio.play().catch(error => {
        if (disposed || attempt !== playAttempt || generation !== attemptGeneration || active !== attemptCue) return;
        video.pause(); stop(true);
        emit(error.name==='NotAllowedError'?'autoplay-blocked':'error');
      });
    } else if (Math.abs(audio.currentTime - offset) > 0.15) audio.currentTime = offset;
  }
  function listen(target, type, fn) { target.addEventListener(type, fn); listeners.push(() => target.removeEventListener(type, fn)); }
  const syncVolume = () => {
    audio.volume = video.volume; audio.muted = video.muted;
  };
  syncVolume();
  listen(video, 'volumechange', syncVolume);
  listen(audio, 'playing', () => {
    if (!running() || playingGeneration !== generation) { stop(); return; }
    duckSoundtrack(true);
    emit('audio-started', active?.id); onCue(active);
  });
  listen(audio, 'waiting', () => duckSoundtrack(false));
  listen(audio, 'ended', () => { emit('audio-ended', active?.id); stop(true); });
  listen(video, 'waiting', () => { waiting = true; stop(); });
  listen(video, 'playing', () => { waiting = false; sync(); });
  listen(video, 'pause', () => { stop(); emit('pause'); });
  listen(video, 'play', () => { if (!release) { video.pause(); return; } emit('play'); });
  listen(video, 'seeking', () => { generation++; stop(true); });
  listen(video, 'seeked', () => { clock.seek(video.currentTime); waiting = false; emit('seek'); });
  listen(video, 'ratechange', () => { stop(); sync(); emit('rate'); });
  listen(video, 'ended', () => { stop(true); emit('ended'); });
  listen(document, 'visibilitychange', () => { if (document.hidden && !keepPlayingWhenHidden()) video.pause(); });
  // Background windows may stop animation frames; media-clock events still drive cues.
  listen(video, 'timeupdate', () => {if(document.hidden && keepPlayingWhenHidden())update();});
  listen(video, 'error', () => {stop(true);emit('error');});
  video.src = timeline.video;
  if (timeline.cover) video.poster = timeline.cover;
  function update() {
    if (disposed) return;
    const cue = clock.tick(video.currentTime, running());
    if (cue) {
      stop(true);
      if (typeof cue.audio === 'string' && cue.audio.startsWith(prefix)) {
        active = cue; audio.src = resources.get(cue.audio); sync();
      } else if (cue.audio == null || cue.audio === '') {
        active = cue; onCue(cue);
      }
    }
    sync();
    if (analyser && !audio.paused) {
      analyser.getByteTimeDomainData(waveform);
      onMouth(Math.min(1,Math.sqrt(waveform.reduce((sum,n)=>sum+(n-128)**2,0)/128)/30));
    }
  }
  function tick(){if(disposed)return;update();frame=requestAnimationFrame(tick);}
  tick();
  return Object.freeze({
    async play() {
      if (disposed) throw Error('Media controller disposed');
      if (!context) {
        context = graph.context;soundtrack = graph.soundtrack;
        analyser=context.createAnalyser();analyser.fftSize=256;
        // Lift quiet reactions above the soundtrack without boosting the video.
        const voiceGain = context.createGain(), compressor = context.createDynamicsCompressor();
        voiceGain.gain.value = 3;
        compressor.threshold.value = -3;
        compressor.knee.value = 3;
        compressor.ratio.value = 20;
        compressor.attack.value = 0.003;
        compressor.release.value = 0.1;
        const source = graph.reactionSource;
        voiceNodes.push(source,analyser,voiceGain,compressor);
        source.connect(analyser);
        analyser.connect(voiceGain);voiceGain.connect(compressor);compressor.connect(context.destination);
      }
      await context.resume();
      if (!release) {
        if (!navigator.locks) throw Object.assign(Error('Exclusive audio ownership unavailable'),{name:'AudioOwnershipError'});
        if(!lockPending)lockPending = new Promise((resolve, reject) => {
          navigator.locks.request('neko:media-timeline:audio', { ifAvailable: true }, async lock => {
            if (!lock) { reject(Object.assign(Error('Another watch scene owns the audio'),{name:'AudioOwnershipError'})); return; }
            await new Promise(done => { release = done; resolve(); });
          }).catch(reject);
        }).finally(()=>{lockPending=null;});
        await lockPending;
      }
      if (disposed) { release?.(); release = null; return; }
      await video.play();
    },
    pause() { video.pause(); stop(); },
    interrupt() { video.pause(); generation++; stop(true); clock.seek(video.currentTime); },
    dispose() {
      if (disposed) return;
      video.pause(); stop(true); disposed = true; generation++;
      cancelAnimationFrame(frame); listeners.forEach(remove => remove());
      video.removeAttribute('src'); video.load(); audio.load(); release?.(); release = null;
      voiceNodes.forEach(node=>node.disconnect());
      // Keep the video's graph reusable for the next timeline; release audio work.
      context?.suspend().catch(()=>{});
      for(const url of resources.values())URL.revokeObjectURL(url);
    },
  });
}

window.NekoMiniGameMediaHost = Object.freeze({ mount });
