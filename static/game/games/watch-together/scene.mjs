import {createNextVideoQueue} from './next-video.mjs';

export async function run(game, character) {
  const $ = id => document.getElementById(id);
  const t = key => window.i18n?.t?.(`watchTogether.${key}`) || key;
  const renderLanguage = () => document.documentElement?.lang || window.i18n?.language || new URLSearchParams(location.search).get('ui_lang') || '';
  let media = null, watch = null, selected = null, writing = Promise.resolve();
  let avatar = null;
  let selectionGeneration = 0;
  let ending = null;
  let progressTimer = null;
  let nextRow=null, queuedFor=null, preparing=false;
  const seenVideos=new Set();
  const updatePrepareButtons=()=>{
    $('prepare-button').disabled=preparing || nextQueue.busy;
    $('discover-button').disabled=preparing || nextQueue.busy;
  };
  const nextQueue=createNextVideoQueue(game,state=>{
    if(state.history)renderHistory(state.history.analyses);
    if(state.status) {
      const key={idle:'nextIdle',searching:'nextSearching',preparing:'nextPreparing',ready:'nextReady',empty:'noCandidates',error:'nextFailed'}[state.status];
      $('next-status').textContent=[t(key),state.title,state.stage?t(state.stage):'',state.progress!=null?`${state.progress}%`:''].filter(Boolean).join(' · ');
      if(state.status==='idle')nextRow=null;
      if(state.row)nextRow=state.row;
      $('next-video').disabled=!nextRow;
    }
    updatePrepareButtons();
    if(state.released && media && !$('video').paused)prefetchNext();
  });
  function prefetchNext() {
    if(!$('prefetch-enabled').checked || !selected || nextQueue.busy || preparing || queuedFor===selected.id)return;
    queuedFor=selected.id;
    void nextQueue.start({topic:$('topic').value.trim() || selected.title || '',exclude:[...seenVideos].slice(-128),character,render_language:renderLanguage()});
  }
  const status = message => { $('status').textContent = message; };
  function renderUsage(stats) {
    if (!stats) { $('usage').textContent=t('unrecorded');return; }
    const value={...stats};
    for(const key of ['input_tokens','output_tokens','total_tokens']) {
      if(stats[key]==null || (Array.isArray(stats.calls) && !stats.calls.some(call=>call[key]!=null))) value[key]=t('unrecorded');
    }
    $('usage').textContent=`${t('usageNote')}\n\n${JSON.stringify(value,null,2)}`;
  }
  game.runtime.configure({payload:()=>({lanlan_name:character}),pageExit:true});
  const record = event => {
    if (!watch || game.runtime.state !== 'running') return;
    const payload = {id:watch, position:event.position ?? $('video').currentTime,event};
    writing = writing.then(() => game.media.request('watch', payload))
      .then(refreshWatches)
      .catch(error => status(error.message));
  };
  function end() {
    if(ending)return ending;
    ending=finishEnd().finally(()=>{ending=null;});
    return ending;
  }
  async function finishEnd() {
    clearInterval(progressTimer); progressTimer = null;
    record({type:'exit'}); media?.dispose(); media = null;
    $('video').controls = false; $('play').hidden = false;
    await writing; watch = null;
    if (!['idle','ended','inactive'].includes(game.runtime.state)) await game.runtime.end({reason:'user_exit'});
  }
  async function load(row) {
    const selection = ++selectionGeneration;
    nextQueue.clear();queuedFor=null;
    $('play').disabled = true;
    const previous = selected;
    try {
    await end();
    if(selection!==selectionGeneration)return false;
    if (game.runtime.state !== 'idle') game.runtime.reset({newSession:true});
    const loaded = await game.media.request('load', row);
    if(selection!==selectionGeneration)return false;
    selected = loaded;
    if(selected.bvid)seenVideos.add(selected.bvid);
    const address = new URL(location.href);
    address.searchParams.set('job',selected.id);address.searchParams.set('version',selected.version);
    history.replaceState(null,'',address);
    $('title').textContent = selected.title;
    $('video').poster = selected.cover || '';
    $('voice-label').textContent = `${t('savedAudio')} · ${selected.voice || t('unknown')}`;
    $('warnings').textContent = [...(selected.warnings || []),...(selected.warning_keys || []).map(t)].join('\n');
    renderUsage(selected.usage);
    $('events').replaceChildren();
    for (const cue of selected.events || []) {
      const button = document.createElement('button');
      button.textContent = `${cue.at.toFixed(1)}s · ${cue.text}`;
      button.title = cue.reason || '';
      button.onclick = () => { $('video').currentTime = Math.max(0,cue.at-1); };
      $('events').append(button);
    }
    status(t('ready')); $('play').disabled = false;
    return true;
    } catch(error) { if(selection!==selectionGeneration)return false; selected = previous; throw error; }
    finally { if(selection===selectionGeneration)$('play').disabled = !selected; }
  }
  $('play').onclick = async () => {
    $('play').disabled = true;
    try {
      if (!media) {
        const response = await game.runtime.start({lanlan_name:character});
        if (!response.ok || response.data?.ok === false) throw Error(response.data?.reason || 'Scene start failed');
        const started = await game.media.request('watch',{action:'start',job:selected.id,version:selected.version});
        watch = started.id;
        await refreshWatches();
        media = await game.media.mount({video:$('video'),job:selected.id,version:selected.version,onEvent:record,
          onCue:cue=>{ $('bubble').textContent = cue?.text || ''; avatar?.setEmotion(cue?'happy':'neutral'); }});
        progressTimer = setInterval(()=>{if(!$('video').paused)record({type:'progress'});},5000);
      }
      await media.play();
      $('video').controls = true; $('play').hidden = true;
      status(t('playing'));
      prefetchNext();
    } catch(error) {
      try { await end(); } catch (_) { /* Preserve the original playback failure. */ }
      status(error.message);
    }
    finally { $('play').disabled = false; }
  };
  game.speech.onState(state => { if(state.active || state.pendingAudioWork) media?.interrupt(); });
  game.voice.onTranscript(() => media?.interrupt());
  game.voice.onState(state => {if(state.active || state.starting)media?.interrupt();});
  game.events.on('runtime-inactive',()=>{media?.dispose();media=null;$('video').controls=false;$('play').hidden=false;clearInterval(progressTimer);nextQueue.clear();queuedFor=null;});
  $('rate').onchange = () => { $('video').playbackRate = Number($('rate').value); };
  async function prepareVideo(url, source = 'manual') {
    if(nextQueue.busy)return;
    const selection = selectionGeneration;
    preparing=true;updatePrepareButtons();
    try {
      status(t('checking'));
      let result = await game.media.request('prepare',{url,source,lanlan_name:character,render_language:renderLanguage()});
      while (result.confirmation_required) {
        if(selection!==selectionGeneration)return;
        const info = result.video;
        if (!window.confirm(`${info.title}\n${t('longWarning')}\n${Math.ceil(info.duration)}s`)) {
          status(t('cancelled')); return;
        }
        result = await game.media.request('prepare',{url:info.url,source,lanlan_name:character,confirmed_duration:info.duration,render_language:renderLanguage()});
      }
      if(selection===selectionGeneration)await end();
      while (!game.disposed) {
        const state = await game.media.request('preparation',{job:result.id});
        if(selection===selectionGeneration) {
          status(state.stage_key ? t(state.stage_key) : state.stage);
          if (state.usage) renderUsage(state.usage);
        }
        if (state.status === 'awaiting_confirmation' && state.confirmation_required) {
          const info = state.confirmation_video;
          if(selection!==selectionGeneration) {
            await game.media.request('prepare',{confirmation_job:result.id,lanlan_name:character,accepted:false,confirmed_duration:info.duration});
            return;
          }
          const accepted = window.confirm(`${info.title}\n${t('longWarning')}\n${Math.ceil(info.duration)}s`);
          await game.media.request('prepare',{confirmation_job:result.id,lanlan_name:character,accepted,confirmed_duration:info.duration});
          if (!accepted) {if(selection===selectionGeneration)status(t('cancelled'));return;}
          continue;
        }
        if (['error','cancelled'].includes(state.status)) throw Error(state.stage_key ? t(state.stage_key) : (state.error || state.stage));
        if (state.status === 'ready') {
          const history = await game.media.request('history');
          renderHistory(history.analyses);
          const row = history.analyses.find(item=>item.job===result.id && item.status==='ready');
          if (row) { if(selection===selectionGeneration)await load(row); break; }
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    } catch(error) {if(selection===selectionGeneration)status(error.message);}
    finally {preparing=false;updatePrepareButtons();}
  }
  $('prepare').onsubmit = event => {
    event.preventDefault(); return prepareVideo($('url').value);
  };
  $('discover').onsubmit = async event => {
    event.preventDefault();if(nextQueue.busy || preparing)return;
    preparing=true;updatePrepareButtons();
    try {
      status(t('searching'));
      const topic = $('topic').value.trim() || selected?.title || '';
      const result = await game.media.request('discover',{topic,exclude:[...seenVideos].slice(-128)});
      if (!result.video) {status(t('noCandidates'));return;}
      const info = result.video;
      $('url').value=info.url;
      $('discovery-result').textContent=`${info.title} · ${info.duration}s · ${info.danmaku_per_minute.toFixed(1)} ${t('density')}`;
      await prepareVideo(info.url,'discovery');
    } catch(error) {status(error.message);}
    finally {preparing=false;updatePrepareButtons();}
  };
  $('prefetch-enabled').onchange=()=>{
    if(!$('prefetch-enabled').checked){nextQueue.clear();queuedFor=null;}
    else if(media && !$('video').paused)prefetchNext();
  };
  $('next-video').onclick=async()=>{
    if(!nextRow)return;
    const row=nextRow;
    try {if(await load(row))await $('play').onclick();}catch(error){status(error.message);}
  };
  $('exit').onclick = async () => {
    nextQueue.dispose();
    await end(); game.dispose(); window.close();
    setTimeout(()=>{if(!window.closed)location.href='/';},100);
  };
  async function refreshWatches() {
    try { renderWatches((await game.media.request('watches')).watches); }
    catch(error) { status(error.message); }
  }
  function renderWatches(rows) {
    $('watches').textContent = rows.length ? rows.map(row=>`${row.job} · ${row.progress ?? t('unknown')}s · ${row.last_watched ?? t('unknown')}`).join('\n') : t('noWatches');
  }
  function renderHistory(rows) {
    $('history').replaceChildren();
    for (const row of rows) {
      const button = document.createElement('button');
      button.textContent = `${row.title} · ${row.job.slice(0,8)} · ${row.version.slice(0,8)}`;
      button.disabled = row.status !== 'ready';
      button.onclick = () => load(row).catch(error=>status(error.message));
      $('history').append(button);
    }
  }
  try {
    const info = await game.media.request('character',{name:character});
    character = info.lanlan_name || character;
    const type = info.model_type === 'live2d' ? 'live2d' : 'vrm';
    const path = type==='live2d'?info.live2d_path:info.vrm_path;
    if(path) {
      try { avatar = await game.avatar.mount({slot:'companion',model:{type,path},viewport:{mode:'container'},resize:{mode:'container'}}); }
      catch(error) { $('avatar').textContent = error.message; }
    }
    const data = await game.media.request('history');
    renderHistory(data.analyses);
    renderWatches(data.watches);
    status(t('choose'));
    const job = new URLSearchParams(location.search).get('job');
    const version = new URLSearchParams(location.search).get('version');
    const match = data.analyses.find(row=>row.job===job && (!version || row.version===version) && row.status==='ready');
    if(match) await load(match);
  } catch(error) { status(error.message); }
}
