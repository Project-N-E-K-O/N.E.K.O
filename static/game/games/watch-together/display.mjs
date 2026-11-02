// Fullscreen the composition, so both avatar providers remain over the video.
// Maximize and fullscreen are always the viewer's choice; selecting or playing never forces them.
// Returns restore(), which leaves either mode so the page around the stage is reachable again.
export function initializeDisplay() {
  const stage=document.getElementById('stage'), video=document.getElementById('video');
  const size=document.getElementById('stage-size'), fullscreen=document.getElementById('stage-fullscreen');
  let expanded=false, wasFullscreen=false, idleTimer=0;
  const immersive=()=>expanded || document.fullscreenElement===stage;
  // While playing immersively, the title row and dock fade out after a short pause in pointer/keyboard use.
  const wake=()=>{
    stage.classList.remove('idle');clearTimeout(idleTimer);
    idleTimer=setTimeout(()=>{
      if(immersive() && !video.paused && !stage.querySelector('.stage-head:hover,.dock:hover,.dock:focus-within'))stage.classList.add('idle');
    },2500);
  };
  const setExpanded=value=>{
    expanded=value;
    stage.classList.toggle('expanded',value);
    document.body.classList.toggle('watch-expanded',value);
    const key=value?'common.restore':'common.maximize';
    size.dataset.i18n=key;size.textContent=window.i18n?.t(key) || key;
    size.setAttribute('aria-expanded',String(value));
    wake();
  };
  const enterFullscreen=()=>{
    setExpanded(true);
    // A user gesture is required. Window-filling layout remains the fallback.
    return stage.requestFullscreen?.().catch(()=>{});
  };
  size.onclick=async()=>{
    const next=!expanded;
    if(document.fullscreenElement)await document.exitFullscreen();
    setExpanded(next);
  };
  fullscreen.onclick=async()=>{
    if(document.fullscreenElement)await document.exitFullscreen();
    else await enterFullscreen();
  };
  document.addEventListener('fullscreenchange',()=>{
    if(!document.fullscreenElement && wasFullscreen)setExpanded(false);
    wasFullscreen=document.fullscreenElement===stage;
    wake();
  });
  document.addEventListener('keydown',event=>{
    // Escape inside the confirmation dialog only closes the dialog.
    if(event.key==='Escape' && !document.fullscreenElement && !document.querySelector('dialog[open]'))setExpanded(false);
  });
  for(const type of ['pointermove','pointerdown','keydown','focusin'])stage.addEventListener(type,wake,{passive:true});
  for(const type of ['play','pause','ended'])video.addEventListener(type,wake);
  const restore=async()=>{
    if(document.fullscreenElement)await document.exitFullscreen().catch(()=>{});
    setExpanded(false);
  };
  return {restore};
}
