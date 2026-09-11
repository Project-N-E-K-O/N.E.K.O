/** Trusted host: the only owner of pre-recorded reaction audio. */
import { ReactionClock } from './media-clock.mjs';

export async function mount({ video, timeline, signal, onEvent = () => {}, onCue = () => {}, onMouth = () => {} }) {
  if (!(video instanceof HTMLVideoElement) || timeline.status !== 'ready') throw Error('Media is not ready');
  const prefix = `/api/watch-together/media/${timeline.id}/${timeline.version}`;
  const resources = new Map();
  try {
    for (const url of new Set((timeline.events || []).map(cue=>cue.audio))) {
      if (typeof url !== 'string' || !url.startsWith(`${prefix}/`)) throw Error('Unregistered timeline resource');
      const response = await fetch(url, {signal});
      if (!response.ok) throw Error('Reaction preload failed');
      resources.set(url, URL.createObjectURL(await response.blob()));
    }
  } catch(error) {for(const url of resources.values())URL.revokeObjectURL(url);throw error;}
  const clock = new ReactionClock(timeline.events || []);
  let active = null, disposed = false, waiting = false, frame = 0, release = null;
  let generation = 0, playingGeneration = -1;
  const audio = new Audio();
  let context = null, analyser = null;
  const waveform = new Uint8Array(128);
  audio.preload = 'auto';
  const listeners = [];
  const running = () => !disposed && !waiting && !video.paused && !video.seeking && video.readyState >= 3;
  const emit = (type, cue = '') => onEvent({ type, cue, position: video.currentTime });
  function stop(clear = false) {
    audio.pause();
    onMouth(0);
    if (clear) { active = null; audio.removeAttribute('src'); onCue(null); }
  }
  function sync() {
    if (!active || !running()) { stop(); return; }
    const offset = video.currentTime - active.at;
    if (offset < 0 || offset >= active.duration) { stop(true); return; }
    audio.playbackRate = video.playbackRate;
    if (audio.paused) {
      audio.currentTime = offset;
      playingGeneration = generation;
      audio.play().catch(() => { video.pause(); stop(true); });
    } else if (Math.abs(audio.currentTime - offset) > 0.15) audio.currentTime = offset;
  }
  function listen(target, type, fn) { target.addEventListener(type, fn); listeners.push(() => target.removeEventListener(type, fn)); }
  listen(audio, 'playing', () => {
    if (!running() || playingGeneration !== generation) { stop(); return; }
    emit('audio-started', active?.id); onCue(active);
  });
  listen(audio, 'ended', () => { emit('audio-ended', active?.id); stop(true); });
  listen(video, 'waiting', () => { waiting = true; stop(); });
  listen(video, 'playing', () => { waiting = false; sync(); });
  listen(video, 'pause', () => { stop(); emit('pause'); });
  listen(video, 'play', () => { if (!release) { video.pause(); return; } emit('play'); });
  listen(video, 'seeking', () => { generation++; stop(true); });
  listen(video, 'seeked', () => { clock.seek(video.currentTime); waiting = false; emit('seek'); });
  listen(video, 'ratechange', () => { stop(); sync(); emit('rate'); });
  listen(video, 'ended', () => { stop(true); emit('ended'); });
  listen(document, 'visibilitychange', () => { if (document.hidden) video.pause(); });
  video.src = timeline.video;
  if (timeline.cover) video.poster = timeline.cover;
  function tick() {
    if (disposed) return;
    const cue = clock.tick(video.currentTime, running());
    if (cue) {
      stop(true);
      if (typeof cue.audio === 'string' && cue.audio.startsWith(prefix)) {
        active = cue; audio.src = resources.get(cue.audio); sync();
      }
    }
    sync();
    if (analyser && !audio.paused) {
      analyser.getByteTimeDomainData(waveform);
      onMouth(Math.min(1,Math.sqrt(waveform.reduce((sum,n)=>sum+(n-128)**2,0)/128)/30));
    }
    frame = requestAnimationFrame(tick);
  }
  tick();
  return Object.freeze({
    async play() {
      if (disposed) throw Error('Media controller disposed');
      if (!context) {
        context = new AudioContext(); analyser=context.createAnalyser();analyser.fftSize=256;
        context.createMediaElementSource(audio).connect(analyser);analyser.connect(context.destination);
      }
      await context.resume();
      if (!release) {
        if (!navigator.locks) throw Error('Exclusive audio ownership unavailable');
        await new Promise((resolve, reject) => {
          navigator.locks.request('neko:media-timeline:audio', { ifAvailable: true }, async lock => {
            if (!lock) { reject(Error('Another watch scene owns the audio')); return; }
            await new Promise(done => { release = done; resolve(); });
          }).catch(reject);
        });
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
      context?.close();
      for(const url of resources.values())URL.revokeObjectURL(url);
    },
  });
}

window.NekoMiniGameMediaHost = Object.freeze({ mount });
