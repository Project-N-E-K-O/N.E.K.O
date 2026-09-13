import {createNextVideoQueue} from './next-video.mjs';
import {createAutomatic} from './automatic.mjs';

export async function run(game, character) {
  const $ = id => document.getElementById(id);
  const t = key => window.i18n?.t?.(`watchTogether.${key}`) || key;
  const renderLanguage = () => document.documentElement?.lang || window.i18n?.language || new URLSearchParams(location.search).get('ui_lang') || '';
  const discoveryTopic = () => $('topic').value.trim() || Array.from(String(selected?.title || '').trim()).slice(0,200).join('');
  let media = null, watch = null, selected = null, writing = Promise.resolve();
  let avatar = null;
  let selectionGeneration = 0;
  let playbackGeneration = 0;
  let ending = null;
  let watchStarting = null;
  let playbackStart = null;
  let mountingController = null;
  let runtimeStarting = null;
  function startRuntime() {
    if(game.runtime.state==='running')return Promise.resolve({ok:true});
    if(['ended','inactive'].includes(game.runtime.state))game.runtime.reset({newSession:true});
    if(!runtimeStarting)runtimeStarting=game.runtime.start({lanlan_name:character}).finally(()=>{runtimeStarting=null;});
    return runtimeStarting;
  }
  let progressTimer = null;
  let nextRow=null, queuedFor=null, preparing=false;
  let automaticPending=false;
  const automatic=createAutomatic({
    report:error=>{status(error.message);if(error.name==='NotAllowedError')void stopAutomatic();},
    async advance(current) {
      if(!current())return true;
      if(game.runtime.state!=='running') {
        const response=await startRuntime();
        if(!response.ok || response.data?.ok===false)throw Error(response.data?.reason || 'Scene start failed');
      }
      if(!current())return true;
      if(media && !$('video').ended){
        if($('video').paused)await play();
        else prefetchNext();
        return true;
      }
      if(selected && !automaticPending) {
        automaticPending=true;
        await play();return true;
      }
      if(!nextRow) {
        if(!nextQueue.busy && !preparing) {
          queuedFor=null;prefetchNext();
        }
        return false;
      }
      const row=nextRow;nextRow=null;
      if(await load(row,true)) {
        if(!current())return true;
        automaticPending=true;
        await play();return true;
      }
      return false;
    },
  });
  async function stopAutomatic() {
    automatic.stop();automaticPending=false;$('automatic-enabled').checked=false;
    $('automatic-enabled').disabled=true;
    selectionGeneration++;nextQueue.clear();queuedFor=null;
    try {await end();$('video').src=selected?.video || '';$('play').disabled=!selected;}
    finally {$('automatic-enabled').disabled=false;updatePrepareButtons();}
  }
  const seenVideos=new Set();
  const updatePrepareButtons=()=>{
    $('prepare-button').disabled=automatic.enabled || preparing || nextQueue.busy;
    $('discover-button').disabled=automatic.enabled || preparing || nextQueue.busy;
  };
  const nextQueue=createNextVideoQueue(game,state=>{
    if(state.candidate){seenVideos.add(state.candidate);if(seenVideos.size>512)seenVideos.delete(seenVideos.values().next().value);}
    if(state.history)void refreshHistory();
    if(state.status) {
      const key={idle:'nextIdle',searching:'nextSearching',preparing:'nextPreparing',ready:'nextReady',empty:'noCandidates',error:'nextFailed'}[state.status];
      $('next-status').textContent=[t(key),state.title,state.stage?t(state.stage):'',state.progress!=null?`${state.progress}%`:''].filter(Boolean).join(' · ');
      if(state.status==='idle')nextRow=null;
      if(state.row)nextRow=state.row;
      if(state.row && automatic.enabled && (!media || $('video').ended))automatic.next();
      $('next-video').disabled=!nextRow;
    }
    updatePrepareButtons();
    if(state.released && media && !$('video').paused)prefetchNext();
  });
  function prefetchNext() {
    if((!automatic.enabled && (!$('prefetch-enabled').checked || !selected)) || nextQueue.busy || preparing || (selected && queuedFor===selected.id))return;
    queuedFor=selected?.id || null;
    void nextQueue.start({topic:discoveryTopic(),exclude:[...seenVideos].slice(-128),character,render_language:renderLanguage()});
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
  let pendingProgress=null;
  const record = event => {
    if(event.type==='autoplay-blocked'){status(t('play'));void stopAutomatic();return;}
    if(event.type==='error') {
      if(automatic.enabled)void end(true).then(()=>automatic.next()).catch(error=>status(error.message));
      else {
        const selection=selectionGeneration;
        status(t('prepareFailed'));$('play').disabled=true;
        void end().catch(error=>status(error.message)).finally(()=>{
          if(selection!==selectionGeneration || game.disposed)return;
          $('video').src=selected?.video || '';
          $('play').disabled=!selected;
        });
      }
      return;
    }
    if (!watch || game.runtime.state !== 'running') return;
    const payload = {id:watch, position:event.position ?? $('video').currentTime,event};
    let queued={payload};
    if(event.type==='progress') {
      if(pendingProgress){pendingProgress.payload=payload;return;}
      pendingProgress=queued;
    } else pendingProgress=null;
    writing = writing.then(async () => {
      if(pendingProgress===queued)pendingProgress=null;
      await game.media.request('watch', queued.payload);
      void refreshWatches();
    })
      .catch(error => status(error.message));
    if(event.type==='ended' && automatic.enabled)automatic.next();
  };
  function end(keepRoute=false) {
    if(ending)return keepRoute?ending:ending.then(()=>end());
    ending=finishEnd(keepRoute).finally(()=>{ending=null;});
    return ending;
  }
  async function finishEnd(keepRoute=false) {
    playbackGeneration++;
    mountingController?.abort();mountingController=null;
    clearInterval(progressTimer); progressTimer = null;
    try {await runtimeStarting;}catch(_){}
    // Keep the route alive until an in-flight start has supplied its watch ID.
    try { await watchStarting; } catch (_) { /* A failed start has no watch to close. */ }
    record({type:'exit'}); media?.dispose(); media = null;
    $('video').controls = false; $('play').hidden = false;
    await writing; watch = null;
    if (!keepRoute && !['idle','ended','inactive'].includes(game.runtime.state)) await game.runtime.end({reason:'user_exit'});
  }
  async function load(row,keepRoute=false) {
    if(!keepRoute){automatic.stop();automaticPending=false;$('automatic-enabled').checked=false;}
    const selection = ++selectionGeneration;
    nextQueue.clear();queuedFor=null;
    $('play').disabled = true;
    const previous = selected;
    try {
    await end(keepRoute);
    if(selection!==selectionGeneration)return false;
    if (!keepRoute && game.runtime.state !== 'idle') game.runtime.reset({newSession:true});
    const loaded = await game.media.request('load', row);
    if(selection!==selectionGeneration)return false;
    if(loaded.status!=='ready')throw Error('Media is not ready');
    selected = loaded;
    if(selected.bvid)seenVideos.add(selected.bvid);
    const address = new URL(location.href);
    address.searchParams.set('job',selected.id);address.searchParams.set('version',selected.version);
    history.replaceState(null,'',address);
    $('title').textContent = selected.title;
    $('video').poster = selected.cover || '';
    $('video').src = selected.video || '';
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
    } catch(error) {
      if(selection!==selectionGeneration)return false;
      selected = previous;
      $('video').src = previous?.video || '';
      throw error;
    }
    finally { if(selection===selectionGeneration)$('play').disabled = !selected; }
  }
  function play() {
    if(playbackStart?.selection===selectionGeneration)return playbackStart.promise;
    const start={selection:selectionGeneration};
    start.promise=beginPlayback().finally(()=>{if(playbackStart===start)playbackStart=null;});
    playbackStart=start;
    return start.promise;
  }
  async function beginPlayback() {
    const selection = selectionGeneration;
    const playback = ++playbackGeneration;
    const stale = () => selection !== selectionGeneration || playback !== playbackGeneration || game.disposed;
    $('play').disabled = true;
    try {
      if (!media) {
        const response = await startRuntime();
        if(stale())return;
        if (!response.ok || response.data?.ok === false) throw Error(response.data?.reason || 'Scene start failed');
        watchStarting = game.media.request('watch',{action:'start',job:selected.id,version:selected.version})
          .then(started=>{watch=started.id;});
        try { await watchStarting; } finally { watchStarting=null; }
        if(stale())return;
        await refreshWatches();
        if(stale())return;
        const controller=new AbortController();mountingController=controller;
        const mounted = await game.media.mount({video:$('video'),job:selected.id,version:selected.version,signal:controller.signal,onEvent:record,
          onCue:cue=>{ $('bubble').textContent = cue?.text || ''; avatar?.setEmotion(cue?'happy':'neutral'); }});
        if(stale()){mounted.dispose();return;}
        media = mounted;
        progressTimer = setInterval(()=>{if(!$('video').paused)record({type:'progress'});},5000);
      }
      await media.play();
      if(stale())return;
      $('video').controls = true; $('play').hidden = true;
      status(t('playing'));
      prefetchNext();
    } catch(error) {
      if(stale())return;
      try { await end(automatic.enabled); } catch (_) { /* Preserve the original playback failure. */ }
      if(selection!==selectionGeneration || game.disposed)return;
      $('video').src = selected?.video || '';
      status(error.message);
      if(automatic.enabled && error.name==='AudioOwnershipError') {
        await stopAutomatic();status(error.message);return;
      }
      if(automatic.enabled)throw error;
    }
    finally { if(selection===selectionGeneration)$('play').disabled = !selected; }
  };
  $('play').onclick=()=>play().catch(error=>status(error.message));
  // The active scene owns speech. Ordinary/chat/plugin speech must not pause reactions.
  game.events.on('runtime-inactive',()=>{automatic.stop();$('automatic-enabled').checked=false;playbackGeneration++;media?.dispose();media=null;$('video').src=selected?.video || '';$('video').controls=false;$('play').hidden=false;clearInterval(progressTimer);nextQueue.clear();queuedFor=null;});
  $('automatic-enabled').onchange=()=>{
    if($('automatic-enabled').checked){automaticPending=!!media;automatic.start();}
    else void stopAutomatic().catch(error=>status(error.message));
    updatePrepareButtons();
  };
  $('watch-stop').onclick=()=>stopAutomatic().catch(error=>status(error.message));
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
        if (state.status === 'ready' && state.persistence_complete!==false) {
          const history = await game.media.request('history');
          await refreshHistory();
          const row = history.analyses.find(item=>item.job===result.id && item.status==='ready');
          if (row) { if(selection===selectionGeneration)await load(row); break; }
          throw Error(t('prepareFailed'));
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
    const generation=selectionGeneration;
    preparing=true;updatePrepareButtons();
    try {
      status(t('searching'));
      const topic = discoveryTopic();
      const result = await game.media.request('discover',{topic,exclude:[...seenVideos].slice(-128)});
      if(generation!==selectionGeneration || game.disposed)return;
      if (!result.video) {status(t('noCandidates'));return;}
      const info = result.video;
      $('url').value=info.url;
      $('discovery-result').textContent=`${info.title} · ${info.duration}s · ${info.danmaku_per_minute.toFixed(1)} ${t('density')}`;
      await prepareVideo(info.url,'discovery');
    } catch(error) {if(generation===selectionGeneration && !game.disposed)status(error.message);}
    finally {preparing=false;updatePrepareButtons();}
  };
  $('prefetch-enabled').onchange=()=>{
    if(!$('prefetch-enabled').checked){nextQueue.clear();queuedFor=null;}
    else if(media && !$('video').paused)prefetchNext();
  };
  $('next-video').onclick=async()=>{
    if(!nextRow)return;
    const row=nextRow;
    // Playback needs a fresh trusted Play gesture after asynchronous selection.
    try {if(await load(row,automatic.enabled) && automatic.enabled)await play();}catch(error){status(error.message);if(automatic.enabled)automatic.next();}
  };
  $('exit').onclick = async () => {
    automatic.stop();
    nextQueue.dispose();
    try { await end(); }
    catch(error) { status(error.message); }
    finally {
      game.dispose(); window.close();
      setTimeout(()=>{if(!window.closed)location.href='/';},100);
    }
  };
  let watchOffset=0, watchNext=null, watchGeneration=0, watchLoading=false, watchRefreshPending=false;
  $('watches-previous').onclick=()=>refreshWatches(Math.max(0,watchOffset-50));
  $('watches-next').onclick=()=>{if(watchNext!==null)return refreshWatches(watchNext);};
  async function refreshWatches(offset) {
    // Promise.then(record) passes its result; only explicit numeric offsets paginate.
    if(!Number.isInteger(offset)) {
      if(watchLoading){watchRefreshPending=true;return;}
      offset=watchOffset;
    }
    const generation=++watchGeneration;
    watchLoading=true;
    $('watches-previous').disabled=true;$('watches-next').disabled=true;
    try {
      const page=await game.media.request('watches',{offset});
      if(generation!==watchGeneration)return;
      watchOffset=offset;watchNext=page.next_offset ?? null;
      renderWatches(page.watches);
    }
    catch(error) { if(generation===watchGeneration)status(error.message); }
    finally {
      if(generation===watchGeneration) {
        watchLoading=false;
        $('watches-previous').disabled=watchOffset===0;
        $('watches-next').disabled=watchNext===null;
        if(watchRefreshPending) {
          watchRefreshPending=false;
          void refreshWatches();
        }
      }
    }
  }
  function renderWatches(rows) {
    $('watches').textContent = rows.length ? rows.map(row=>`${row.job} · ${row.progress ?? t('unknown')}s · ${row.last_watched ?? t('unknown')}`).join('\n') : t('noWatches');
  }
  let historyOffset=0, historyNext=null, historyGeneration=0, historyLoading=false, historyRefreshPending=false;
  $('history-previous').onclick=()=>refreshHistory(Math.max(0,historyOffset-50));
  $('history-next').onclick=()=>{if(historyNext!==null)return refreshHistory(historyNext);};
  async function refreshHistory(offset) {
    if(!Number.isInteger(offset)) {
      if(historyLoading){historyRefreshPending=true;return;}
      offset=historyOffset;
    }
    const generation=++historyGeneration;
    historyLoading=true;
    $('history-previous').disabled=true;$('history-next').disabled=true;
    try {
      const page=await game.media.request('history',{offset});
      if(generation!==historyGeneration)return;
      historyOffset=offset;historyNext=page.next_offset ?? null;
      renderHistory(page.analyses);
    } catch(error) {if(generation===historyGeneration)status(error.message);}
    finally {
      if(generation===historyGeneration) {
        historyLoading=false;
        $('history-previous').disabled=historyOffset===0;$('history-next').disabled=historyNext===null;
        if(historyRefreshPending){historyRefreshPending=false;void refreshHistory();}
      }
    }
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
    historyNext=data.next_offset ?? null;$('history-next').disabled=historyNext===null;
    await refreshWatches();
    status(t('choose'));
    const job = new URLSearchParams(location.search).get('job');
    const version = new URLSearchParams(location.search).get('version');
    const match = data.analyses.find(row=>row.job===job && (!version || row.version===version) && row.status==='ready');
    if(match) await load(match);
    else if(job && version)await load({job,version});
  } catch(error) { status(error.message); }
}
