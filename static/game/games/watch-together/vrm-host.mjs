// Trusted SDK avatar provider, symmetric with live2d-host.mjs.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
// Same default idle as vrm-manager.js and the SDK standing reference.
const IDLE_URL='/static/vrm/animation/wait03.vrma.gz';
let idlePromise=null;
function loadIdle(){
  idlePromise ||= (async()=>{
    const lib=await import('@pixiv/three-vrm-animation');
    const response=await fetch(IDLE_URL);
    if(!response.ok)throw Error(`Idle animation HTTP ${response.status}`);
    let data=await response.arrayBuffer();
    const head=new Uint8Array(data.slice(0,2));
    // A proxy may already have decoded Content-Encoding; only gunzip real gzip bytes.
    if(head[0]===0x1f && head[1]===0x8b)data=await new Response(new Blob([data]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
    const loader=new GLTFLoader();loader.register(parser=>new lib.VRMAnimationLoaderPlugin(parser));
    const source=(await loader.parseAsync(data,'/static/vrm/animation/')).userData.vrmAnimations?.[0];
    if(!source)throw Error('Idle animation has no VRMC_vrm_animation');
    return {lib,source};
  })().catch(error=>{idlePromise=null;throw error;});
  return idlePromise;
}
// Never show the bind T-pose: the idle clip when available, a relaxed normalized pose otherwise.
function pose(vrm,asset){
  if(asset)try{
    if(vrm.lookAt){const proxy=new asset.lib.VRMLookAtQuaternionProxy(vrm.lookAt);proxy.name='VRMLookAtQuaternionProxy';vrm.scene.add(proxy);}
    const clip=asset.lib.createVRMAnimationClip(asset.source,vrm);
    // Speech owns the mouth (mouth()), so drop the clip's authored 'aa' track.
    const mouth=`${vrm.expressionManager?.getExpression('aa')?.name}.weight`;
    clip.tracks=clip.tracks.filter(track=>track.name!==mouth);
    const mixer=new THREE.AnimationMixer(vrm.scene);mixer.clipAction(clip).play();mixer.update(0);vrm.update(0);
    return {mixer,duration:clip.duration};
  }catch(error){console.warn('[watch-together] VRM idle clip failed',error);}
  // VRM0 normalized rigs face -Z in model space (rotateVRM0 only turns the scene): mirror X/Z.
  const sign=vrm.meta?.metaVersion==='0'?-1:1,bone=name=>vrm.humanoid?.getNormalizedBoneNode(name);
  bone('leftUpperArm')?.rotation.set(0,0,-1.2*sign);bone('rightUpperArm')?.rotation.set(0,0,1.2*sign);
  bone('leftLowerArm')?.rotation.set(0,-.25,0);bone('rightLowerArm')?.rotation.set(0,.25,0);
  vrm.update(0);return null;
}
// Fallback only: breathing and a periodic blink (the idle clip already carries both).
function breathe(vrm,time){
  const chest=vrm.humanoid?.getNormalizedBoneNode('chest') || vrm.humanoid?.getNormalizedBoneNode('spine');
  if(chest)chest.rotation.x=Math.sin(time*1.8)*.015;
  const phase=time%4.2;vrm.expressionManager?.setValue('blink',phase<.16?Math.sin(phase/.16*Math.PI):0);
}
// Mid-thigh to the top of the head of the posed mesh over the whole idle loop, so swaying arms
// stay in frame; the head-relative cap ignores stray far geometry.
const MEASURE_STEPS=10;
function measure(vrm,idle){
  const box=new THREE.Box3(),sample=new THREE.Box3(),point=new THREE.Vector3();
  const y=name=>vrm.humanoid?.getNormalizedBoneNode(name)?.getWorldPosition(point).y;
  let hips=Infinity,head=-Infinity;
  for(let step=0;step<(idle?MEASURE_STEPS:1);step++){
    if(idle){idle.mixer.setTime(idle.duration*step/MEASURE_STEPS);vrm.update(0);}
    vrm.scene.updateMatrixWorld(true);
    box.union(sample.setFromObject(vrm.scene,true));
    hips=Math.min(hips,y('hips') ?? Infinity);head=Math.max(head,y('head') ?? -Infinity);
  }
  if(idle){idle.mixer.setTime(0);vrm.update(0);}
  if(!Number.isFinite(hips))hips=box.min.y;
  if(!Number.isFinite(head))head=box.max.y;
  const top=Math.min(box.max.y,head+(head-hips)*1.5),torso=top-hips;
  return {top:top+torso*.05,bottom:Math.max(box.min.y,hips-torso*.2),halfWidth:Math.max(box.max.x,-box.min.x)};
}
export function create(container) {
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(30,1,.1,20);
  camera.position.set(0,1.2,1.8);camera.lookAt(0,1.2,0);
  const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true});
  renderer.setPixelRatio(Math.min(globalThis.devicePixelRatio || 1,2));container.append(renderer.domElement);
  scene.add(new THREE.HemisphereLight(0xffffff,0x778899,3));
  const loader=new GLTFLoader();loader.register(parser=>new VRMLoaderPlugin(parser));
  let model=null,idle=null,bounds=null,lookTarget=null,disposed=false,paused=false,frame=0,last=performance.now();
  // Frame the measured upper body; refit on every container resize.
  function fitCamera(){
    camera.updateProjectionMatrix();if(!bounds)return;
    const half=(bounds.top-bounds.bottom)/2,center=bounds.bottom+half,tan=Math.tan(THREE.MathUtils.degToRad(camera.fov/2));
    const distance=Math.max(half/tan,bounds.halfWidth/(tan*camera.aspect))*1.06;
    camera.position.set(0,center,distance);camera.lookAt(0,center,0);
    camera.near=Math.max(.01,distance-2);camera.far=distance+2;camera.updateProjectionMatrix();
  }
  function tick(now){
    if(disposed)return;
    if(!paused){
      const delta=Math.min((now-last)/1000,.1);
      if(model){if(idle)idle.mixer.update(delta);else breathe(model,now/1000);model.update(delta);}
      renderer.render(scene,camera);
    }
    last=now;frame=requestAnimationFrame(tick);
  }
  frame=requestAnimationFrame(tick);
  return {
    async setModel(config){
      const clip=loadIdle().catch(error=>{console.warn('[watch-together] VRM idle unavailable',error);return null;});
      const gltf=await loader.loadAsync(config.path),next=gltf.userData.vrm;
      if(!next)throw Error('Invalid VRM');
      VRMUtils.rotateVRM0(next);
      // Posed before it joins the scene, so the bind pose never flashes.
      const nextIdle=pose(next,await clip);
      if(disposed){nextIdle?.mixer.stopAllAction();VRMUtils.deepDispose(next.scene);return;}
      if(model){idle?.mixer.stopAllAction();scene.remove(model.scene);VRMUtils.deepDispose(model.scene);}
      model=next;idle=nextIdle;bounds=measure(model,idle);scene.add(model.scene);fitCamera();
      if(lookTarget && model.lookAt)model.lookAt.target=lookTarget;
    },
    resize({width,height}){
      renderer.setSize(width,height);
      if(width>0 && height>0){camera.aspect=width/height;fitCamera();}
    },
    // A look-at target is applied after the idle clip's gaze each frame, so focus() wins over it.
    focus({x,y}){
      lookTarget ||= new THREE.Object3D();lookTarget.position.set(x,y,3);
      if(model?.lookAt)model.lookAt.target=lookTarget;
    },
    setEmotion(name){model?.expressionManager?.setValue('happy',name==='happy'?0.6:0);},
    mouth(level){model?.expressionManager?.setValue('aa',level);},
    pause(){paused=true;},resume(){paused=false;},getState(){return {ready:!!model};},
    dispose(){disposed=true;cancelAnimationFrame(frame);idle?.mixer.stopAllAction();if(model)VRMUtils.deepDispose(model.scene);renderer.dispose();renderer.domElement.remove();},
  };
}
