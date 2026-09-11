export async function run(game, character) {
  const $ = id => document.getElementById(id);
  const t = key => window.i18n?.t?.(`watchTogether.${key}`) || key;
  let media = null, watch = null, selected = null, writing = Promise.resolve();
  let avatar = null;
  let progressTimer = null;
  const status = message => { $('status').textContent = message; };
  function renderUsage(stats) {
    if (!stats) { $('usage').textContent=t('unrecorded');return; }
    const value={...stats};
    for(const key of ['input_tokens','output_tokens','total_tokens']) {
      if(!(stats.calls || []).some(call=>call[key]!=null)) value[key]=t('unrecorded');
    }
    $('usage').textContent=`${t('usageNote')}\n\n${JSON.stringify(value,null,2)}`;
  }
  game.runtime.configure({payload:()=>({lanlan_name:character}),pageExit:true});
  const record = event => {
    if (!watch || game.runtime.state !== 'running') return;
    const payload = {id:watch, position:event.position ?? $('video').currentTime,event};
    writing = writing.then(() => game.media.request('watch', payload))
      .catch(error => status(error.message));
  };
  async function end() {
    clearInterval(progressTimer); progressTimer = null;
    record({type:'exit'}); media?.dispose(); media = null;
    await writing; watch = null;
    if (!['idle','ended','inactive'].includes(game.runtime.state)) await game.runtime.end({reason:'user_exit'});
  }
  async function load(row) {
    $('play').disabled = true;
    await end();
    if (game.runtime.state !== 'idle') game.runtime.reset({newSession:true});
    selected = await game.media.request('load', row);
    const address = new URL(location.href);
    address.searchParams.set('job',selected.id);address.searchParams.set('version',selected.version);
    history.replaceState(null,'',address);
    $('title').textContent = selected.title;
    $('video').poster = selected.cover || '';
    $('voice-label').textContent = `${t('savedAudio')} · ${selected.voice || t('unknown')}`;
    $('warnings').textContent = (selected.warnings || []).join('\n');
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
  }
  $('play').onclick = async () => {
    $('play').disabled = true;
    try {
      if (!media) {
        const response = await game.runtime.start({lanlan_name:character});
        if (!response.ok || response.data?.ok === false) throw Error(response.data?.reason || 'Scene start failed');
        const started = await game.media.request('watch',{action:'start',job:selected.id,version:selected.version});
        watch = started.id;
        media = await game.media.mount({video:$('video'),job:selected.id,version:selected.version,onEvent:record,
          onCue:cue=>{ $('bubble').textContent = cue?.text || ''; avatar?.setEmotion(cue?'happy':'neutral'); }});
        progressTimer = setInterval(()=>{if(!$('video').paused)record({type:'progress'});},5000);
      }
      await media.play(); status(t('playing'));
    } catch(error) { status(error.message); }
    finally { $('play').disabled = false; }
  };
  game.speech.onState(state => { if(state.active || state.pendingAudioWork) media?.interrupt(); });
  game.voice.onTranscript(() => media?.interrupt());
  game.voice.onState(state => {if(state.active || state.starting)media?.interrupt();});
  game.events.on('runtime-inactive',()=>{media?.dispose();media=null;clearInterval(progressTimer);});
  $('rate').onchange = () => { $('video').playbackRate = Number($('rate').value); };
  async function prepareVideo(url, source = 'manual') {
    $('prepare-button').disabled = true; $('discover-button').disabled = true;
    try {
      status(t('checking'));
      let result = await game.media.request('prepare',{url,source,lanlan_name:character});
      while (result.confirmation_required) {
        const info = result.video;
        if (!window.confirm(`${info.title}\n${t('longWarning')}\n${Math.ceil(info.duration)}s`)) {
          status(t('cancelled')); return;
        }
        result = await game.media.request('prepare',{url:info.url,source,lanlan_name:character,confirmed_duration:info.duration});
      }
      await end();
      while (!game.disposed) {
        const state = await game.media.request('preparation',{job:result.id});
        status(state.stage);
        if (state.usage) renderUsage(state.usage);
        if (['error','cancelled'].includes(state.status)) throw Error(state.error || state.stage);
        if (state.status === 'ready') {
          const history = await game.media.request('history');
          const row = history.analyses.find(item=>item.job===result.id && item.status==='ready');
          if (row) { await load(row); break; }
        }
        await new Promise(resolve=>setTimeout(resolve,1000));
      }
    } catch(error) {status(error.message);}
    finally {$('prepare-button').disabled=false;$('discover-button').disabled=false;}
  }
  $('prepare').onsubmit = event => {
    event.preventDefault(); return prepareVideo($('url').value);
  };
  $('discover').onsubmit = async event => {
    event.preventDefault(); $('discover-button').disabled=true;$('prepare-button').disabled=true;
    try {
      status(t('searching'));
      const topic = $('topic').value.trim() || selected?.title || '';
      const result = await game.media.request('discover',{topic});
      if (!result.video) {status(t('noCandidates'));return;}
      const info = result.video;
      $('url').value=info.url;
      $('discovery-result').textContent=`${info.title} · ${info.duration}s · ${info.danmaku_per_minute.toFixed(1)} ${t('density')}`;
      await prepareVideo(info.url,'discovery');
    } catch(error) {status(error.message);}
    finally {$('discover-button').disabled=false;$('prepare-button').disabled=false;}
  };
  $('exit').onclick = async () => {await end();game.dispose();location.href='/';};
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
    for (const row of data.analyses) {
      const button = document.createElement('button');
      button.textContent = `${row.title} · ${row.job.slice(0,8)} · ${row.version.slice(0,8)}`;
      button.disabled = row.status !== 'ready';
      button.onclick = () => load(row).catch(error=>status(error.message));
      $('history').append(button);
    }
    $('watches').textContent = data.watches.length ? data.watches.map(row=>`${row.job} · ${row.progress ?? t('unknown')}s · ${row.last_watched ?? t('unknown')}`).join('\n') : t('noWatches');
    status(t('choose'));
    const job = new URLSearchParams(location.search).get('job');
    const version = new URLSearchParams(location.search).get('version');
    const match = data.analyses.find(row=>row.job===job && (!version || row.version===version) && row.status==='ready');
    if(match) await load(match);
  } catch(error) { status(error.message); }
}
