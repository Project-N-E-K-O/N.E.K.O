// Container placement is shared by Live2D and VRM; the SDK observes its size.
export function initializeAvatarPlacement(stage, avatar, handle) {
  let drag=null;
  const clamp=(n,min,max)=>Math.max(min,Math.min(n,Math.max(min,max)));
  function fit(left,top,width,height) {
    const bounds=stage.getBoundingClientRect();
    width=clamp(width,Math.min(100,bounds.width),bounds.width);
    height=clamp(height,Math.min(120,bounds.height),bounds.height);
    Object.assign(avatar.style,{right:'auto',left:`${clamp(left,0,bounds.width-width)}px`,top:`${clamp(top,0,bounds.height-height)}px`,width:`${width}px`,height:`${height}px`});
  }
  const down=event=>{
    if(event.button!==0)return;
    const box=avatar.getBoundingClientRect(),bounds=stage.getBoundingClientRect();
    drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:box.left-bounds.left,top:box.top-bounds.top,width:box.width,height:box.height,resize:event.target===handle};
    avatar.setPointerCapture(event.pointerId);event.preventDefault();event.stopPropagation();
  };
  const move=event=>{
    if(!drag || event.pointerId!==drag.id)return;
    const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    fit(drag.left+(drag.resize?0:dx),drag.top+(drag.resize?0:dy),drag.width+(drag.resize?dx:0),drag.height+(drag.resize?dy:0));
  };
  const up=()=>{drag=null;};
  const resize=()=>{
    if(!avatar.style.left)return;
    fit(parseFloat(avatar.style.left),parseFloat(avatar.style.top),parseFloat(avatar.style.width),parseFloat(avatar.style.height));
  };
  const key=event=>{
    const delta={ArrowLeft:[-10,0],ArrowRight:[10,0],ArrowUp:[0,-10],ArrowDown:[0,10]}[event.key];
    if(!delta)return;
    const box=avatar.getBoundingClientRect(),bounds=stage.getBoundingClientRect();
    fit(box.left-bounds.left,box.top-bounds.top,box.width+delta[0],box.height+delta[1]);event.preventDefault();
  };
  avatar.addEventListener('pointerdown',down);avatar.addEventListener('pointermove',move);
  avatar.addEventListener('pointerup',up);avatar.addEventListener('pointercancel',up);avatar.addEventListener('lostpointercapture',up);
  handle.addEventListener('keydown',key);
  const observer=new ResizeObserver(resize);observer.observe(stage);
  return ()=>{observer.disconnect();avatar.removeEventListener('pointerdown',down);avatar.removeEventListener('pointermove',move);avatar.removeEventListener('pointerup',up);avatar.removeEventListener('pointercancel',up);avatar.removeEventListener('lostpointercapture',up);handle.removeEventListener('keydown',key);};
}
