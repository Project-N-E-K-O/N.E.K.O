// Trusted SDK avatar provider, symmetric with live2d-host.mjs.
import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
export function create(container) {
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(32,1,.1,20);
  camera.position.set(0,1.35,3);camera.lookAt(0,1.1,0);
  const renderer=new THREE.WebGLRenderer({alpha:true,antialias:true});container.append(renderer.domElement);
  scene.add(new THREE.HemisphereLight(0xffffff,0x778899,3));
  const loader=new GLTFLoader();loader.register(parser=>new VRMLoaderPlugin(parser));
  let model=null,disposed=false,paused=false,frame=0,last=performance.now();
  function tick(now){if(disposed)return;if(!paused){model?.update(Math.min((now-last)/1000,.1));renderer.render(scene,camera);}last=now;frame=requestAnimationFrame(tick);}
  frame=requestAnimationFrame(tick);
  return {
    async setModel(config){
      const gltf=await loader.loadAsync(config.path),next=gltf.userData.vrm;
      if(!next)throw Error('Invalid VRM');
      if(disposed){VRMUtils.deepDispose(next.scene);return;}
      if(model){scene.remove(model.scene);VRMUtils.deepDispose(model.scene);}model=next;VRMUtils.rotateVRM0(model);scene.add(model.scene);
    },
    resize({width,height}){renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();},
    focus({x,y}){const target=new THREE.Object3D();target.position.set(x,y,3);model?.lookAt?.lookAt(target.position);},
    setEmotion(name){model?.expressionManager?.setValue('happy',name==='happy'?0.6:0);},
    mouth(level){model?.expressionManager?.setValue('aa',level);},
    pause(){paused=true;},resume(){paused=false;},getState(){return {ready:!!model};},
    dispose(){disposed=true;cancelAnimationFrame(frame);if(model)VRMUtils.deepDispose(model.scene);renderer.dispose();renderer.domElement.remove();},
  };
}
