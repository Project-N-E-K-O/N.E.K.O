import assert from 'node:assert/strict';
import {initializeAvatarPlacement} from '../../static/game/games/watch-together/avatar-placement.mjs';
class Element extends EventTarget {
  style={};
  setPointerCapture(){}
  getBoundingClientRect(){return {left:parseFloat(this.style.left)||600,top:parseFloat(this.style.top)||10,width:parseFloat(this.style.width)||200,height:parseFloat(this.style.height)||250};}
}
const avatar=new Element(),handle=new Element();let bounds={width:1000,height:700},resize,disconnected=false;
const stage={getBoundingClientRect:()=>({left:0,top:0,...bounds})};
globalThis.ResizeObserver=class{constructor(fn){resize=fn;}observe(){}disconnect(){disconnected=true;}};
const dispose=initializeAvatarPlacement(stage,avatar,handle);
function pointer(type,x,y){const event=new Event(type,{cancelable:true});Object.assign(event,{button:0,pointerId:1,clientX:x,clientY:y});avatar.dispatchEvent(event);}
pointer('pointerdown',650,100);pointer('pointermove',850,200);pointer('pointerup',850,200);
assert.equal(avatar.style.left,'800px');assert.equal(avatar.style.top,'110px');
const key=new Event('keydown',{cancelable:true});Object.assign(key,{key:'ArrowRight'});handle.dispatchEvent(key);
assert.equal(avatar.style.width,'210px');assert.equal(avatar.style.left,'790px');
const resizeDown=new Event('pointerdown',{cancelable:true});Object.assign(resizeDown,{button:0,pointerId:1,clientX:1000,clientY:300});Object.defineProperty(resizeDown,'target',{value:handle});avatar.dispatchEvent(resizeDown);
pointer('pointermove',950,250);pointer('pointerup',950,250);
assert.equal(avatar.style.width,'160px');assert.equal(avatar.style.height,'200px');
bounds={width:300,height:200};resize();assert.equal(avatar.style.left,'140px');assert.equal(avatar.style.height,'200px');assert.equal(avatar.style.top,'0px');
dispose();assert.equal(disconnected,true);
console.log('avatar placement: drag, keyboard resize, viewport clamping and disposal passed');
