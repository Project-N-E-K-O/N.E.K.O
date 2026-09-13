// Fullscreen the composition, so both avatar providers remain over the video.
export function initializeDisplay() {
  const stage=document.getElementById('stage'), video=document.getElementById('video');
  const size=document.getElementById('stage-size'), fullscreen=document.getElementById('stage-fullscreen');
  let expanded=false, selected=false, wasFullscreen=false;
  const setExpanded=value=>{
    expanded=value;
    stage.classList.toggle('expanded',value);
    document.body.classList.toggle('watch-expanded',value);
    const key=value?'common.restore':'common.maximize';
    size.dataset.i18n=key;size.textContent=window.i18n?.t(key) || key;
    size.setAttribute('aria-expanded',String(value));
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
  document.getElementById('play').addEventListener('click',()=>{
    if(expanded && !document.fullscreenElement)void enterFullscreen();
  });
  document.addEventListener('fullscreenchange',()=>{
    if(!document.fullscreenElement && wasFullscreen)setExpanded(false);
    wasFullscreen=document.fullscreenElement===stage;
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && !document.fullscreenElement)setExpanded(false);
  });
  const observeSelection=()=>{
    if(!selected && video.getAttribute('poster')){selected=true;setExpanded(true);}
  };
  new MutationObserver(observeSelection).observe(video,{attributes:true,attributeFilter:['poster']});
  observeSelection();
}
