const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const window = {};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,
  '../../static/game/sdk/neko-minigame-avatar-host.js'), 'utf8'), { window });
const host = window.NekoMiniGameAvatarHost;
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-6, `${a} != ${b}`);
function model(w, h) {
  return { get width() { return w * this.scale.x; }, get height() { return h * this.scale.y; },
    scale: { x: 1, y: 1, set(x, y = x) { this.x = x; this.y = y; } } };
}

// This assertion fails on the pre-change helper: width mode was treated as contain.
const wideFit = model(100, 400);
host.fitLive2DModel(wideFit, { width: 200, height: 300 }, { mode: 'width' });
near(wideFit.width, 200);
near(wideFit.height, 800);
const native = model(100, 400);
host.fitLive2DModel(native, { width: 200, height: 300 }, { autoScale: false });
near(native.width, 100);
near(native.height, 400);

for (const [w, h] of [[100, 400], [400, 100], [200, 200]]) {
  const m = model(w, h);
  const box = { width: 200, height: 300 };
  const fit = { align: 'bottom-center', padding: 6, minWidth: 100, minHeight: 200 };
  const first = host.fitLive2DModel(m, box, fit);
  for (let i = 0; i < 30; i++) host.fitLive2DModel(m, box, fit);
  near(m.width, first.width);
  near(m.height, first.height);
  assert.ok(m.width <= 188.000001 && m.height <= 288.000001);
  near(m.y + m.height, 294);
  near(m.width / m.height, w / h);
}
const minimum = host.fitRectangle({ width: 100, height: 200 },
  { width: 200, height: 300 }, { scaleMultiplier: 0.1, minHeight: 200 });
near(minimum.height, 200);
assert.equal(minimum.minimumSatisfied, true);
assert.equal(host.fitRectangle({ width: 100, height: 400 },
  { width: 200, height: 300 }, { minWidth: 100 }).minimumSatisfied, false);
for (const bad of [{ autoScale: 'false' }, { minHeight: NaN }, { minWidth: -1 }]) {
  assert.throws(() => host.fitRectangle({ width: 10, height: 10 },
    { width: 200, height: 300 }, bad));
}
async function testPerspective() {
  const { pathToFileURL } = require('node:url');
  const THREE = await import(pathToFileURL(path.resolve(__dirname, '../../static/libs/three.module.js')).href);
  await testEngineFramingOwnership(THREE);
  // A cached T-pose box must not be reused as the current skinned pose.
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(
    [-.2,0,0,.2,0,0,-.2,2,0,.2,2,0,-1.3,1.6,0,1.3,1.6,0], 3));
  geometry.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(
    [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,0,0,0,2,0,0,0], 4));
  geometry.setAttribute('skinWeight', new THREE.Float32BufferAttribute(
    Array.from({length:6}, () => [1,0,0,0]).flat(), 4));
  const skin = new THREE.SkinnedMesh(geometry, new THREE.MeshBasicMaterial());
  const root = new THREE.Bone(), left = new THREE.Bone(), right = new THREE.Bone();
  left.position.set(-.2,1.6,0); right.position.set(.2,1.6,0);
  root.add(left,right); skin.add(root); skin.bind(new THREE.Skeleton([root,left,right]));
  new THREE.Box3().setFromObject(skin);
  left.rotation.z = Math.PI/2; right.rotation.z = -Math.PI/2;
  skin.updateWorldMatrix(true,true);
  const skinCamera = new THREE.PerspectiveCamera(30,2/3,.01,100);
  skinCamera.position.z = 5;
  const viewport = {width:200,height:300}, fit = {padding:6,align:'bottom-center'};
  const firstSkinFit = host.fitPerspectiveModel(THREE,skin,skinCamera,viewport,fit);
  near(firstSkinFit.height,288);
  // Animation must not change the chosen reference on resize/view updates.
  left.rotation.z = 0; right.rotation.z = 0;
  const animatedFit = host.fitPerspectiveModel(THREE,skin,skinCamera,viewport,fit);
  near(animatedFit.width,firstSkinFit.width); near(animatedFit.height,firstSkinFit.height);
  skin.position.set(10,8,-4);
  const translatedFit = host.fitPerspectiveModel(THREE,skin,skinCamera,viewport,fit);
  near(translatedFit.width,firstSkinFit.width); near(translatedFit.height,firstSkinFit.height);
  host.releasePerspectiveReference(skin,skinCamera);
  const rebuilt = host.fitPerspectiveModel(THREE,skin,skinCamera,viewport,fit);
  assert.ok(rebuilt.height < 150);
  const head = new THREE.Bone(), lHand = new THREE.Bone(), rHand = new THREE.Bone();
  head.position.y = 2; root.add(head); left.add(lHand); right.add(rHand);
  lHand.position.x = -1.1; rHand.position.x = 1.1;
  root.name = '下半身'; head.name = '頭'; left.name = '左腕'; right.name = '右腕';
  lHand.name = '左手首'; rHand.name = '右手首';
  skin.updateWorldMatrix(true,true);
  for (const node of [head,lHand,rHand]) {
    skin.skeleton.bones.push(node); skin.skeleton.boneInverses.push(node.matrixWorld.clone().invert());
  }
  const bones = {head, hips:root, leftUpperArm:left, rightUpperArm:right, leftHand:lHand, rightHand:rHand};
  for (const type of ['vrm','mmd']) {
    left.rotation.z = right.rotation.z = 0;
    const loaded = type === 'vrm' ? {vrm:{scene:skin,humanoid:{getRawBoneNode:name=>bones[name]}}} : {mesh:skin};
    const manager = {currentModel:loaded,camera:skinCamera};
    const apply = async () => { left.rotation.z=Math.PI/2; right.rotation.z=-Math.PI/2; return true; };
    if (type === 'vrm') manager.playVRMAAnimation=apply; else manager.loadAnimation=apply;
    const reference = await host.preparePerspectiveReference(THREE,manager,{type});
    assert.equal(reference.source,'standing-reference');
    near(reference.height,2);
    left.rotation.z = right.rotation.z = 0;
    near(host.fitPerspectiveModel(THREE,skin,skinCamera,viewport,fit).height,288);
    // Missing/failed animations and a nominal idle which is still T-pose must
    // report fallback, never a falsely validated reference.
    const method = type === 'vrm' ? 'playVRMAAnimation' : 'loadAnimation';
    manager[method] = async () => { throw Error('missing fixture'); };
    assert.equal((await host.preparePerspectiveReference(THREE,manager,{type})).source,'current-pose-fallback');
    manager[method] = async () => true;
    assert.equal((await host.preparePerspectiveReference(THREE,manager,{type})).source,'current-pose-fallback');
    const abort = new AbortController();
    let settle;
    manager[method] = () => new Promise(resolve=>{settle=resolve;});
    const pending = host.preparePerspectiveReference(THREE,manager,{type,signal:abort.signal});
    abort.abort(); settle(true);
    await assert.rejects(pending,error=>error.code==='disposed');
    // No cancelled reference survives: first fit now measures current T-pose.
    assert.ok(host.fitPerspectiveModel(THREE,skin,skinCamera,viewport,fit).height<150);
    host.releasePerspectiveReference(skin,skinCamera);
  }
  geometry.dispose(); skin.material.dispose(); skin.skeleton.dispose();
  for (const dims of [[1, 4, 1], [4, 1, 0.3], [2, 2, 2], [0.01, 0.06, 0.01]]) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(...dims));
    mesh.position.set(7, -3, 4);
    mesh.rotation.y = 0.6;
    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 2000);
    camera.position.set(0, 10, 70);
    camera.lookAt(mesh.position);
    for (const viewport of [{ width: 200, height: 300 }, { width: 450, height: 160 }]) {
      for (const mode of ['contain', 'width', 'height', 'cover']) {
        const fit = { mode, padding: 6, align: 'bottom-center' };
        const first = host.fitPerspectiveModel(THREE, mesh, camera, viewport, fit);
        for (let i = 0; i < 5; i++) {
          const next = host.fitPerspectiveModel(THREE, mesh, camera, viewport, fit);
          near(first.width, next.width); near(first.height, next.height);
          near(next.y + next.height, viewport.height - 6);
          near(next.x + next.width / 2, viewport.width / 2);
        }
        if (mode === 'contain') {
          assert.ok(first.width <= viewport.width - 12 + 1e-6);
          assert.ok(first.height <= viewport.height - 12 + 1e-6);
        } else if (mode === 'height') near(first.height, viewport.height - 12);
        else if (mode === 'width') near(first.width, viewport.width - 12);
      }
    }
    const nativeFit = { autoScale: false, scaleMultiplier: 2 };
    const original = host.fitPerspectiveModel(THREE, mesh, camera, { width: 200, height: 300 }, nativeFit);
    const resized = host.fitPerspectiveModel(THREE, mesh, camera, { width: 400, height: 180 }, nativeFit);
    near(original.width, resized.width); near(original.height, resized.height);
    mesh.geometry.dispose(); mesh.material.dispose();
  }
  process.stdout.write('mini-game Avatar fit runtime test passed (2D + real Three.js projection)\n');
}

async function testEngineFramingOwnership(THREE) {
  await testMmdEmbeddedControls();
  const engineWindow = { THREE, addEventListener() {} };
  const container = { clientWidth:200, clientHeight:300, style:{setProperty() {}} };
  const canvas = { id:'test-canvas' };
  const document = { getElementById:id=>id==='test-canvas'?canvas:container,
    createElement:()=>({getContext:()=>null}) };
  const context = { window:engineWindow, document, console:{...console,error() {}}, performance };
  for (const name of ['vrm/vrm-core.js','vrm/vrm-interaction.js','mmd/mmd-interaction.js']) {
    vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../static',name),'utf8'),context);
  }
  const manager = {};
  const core = Object.create(engineWindow.VRMCore.prototype);
  core.manager = manager;
  core._ensureThreeReady = () => {};
  // Stop actual init at the WebGL availability check, after camera/ownership
  // setup. Bounds updates below use real Three.js geometry and camera math.
  await core.init('test-canvas','test-container',null,{embed:true,resizeMode:'fixed'});
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(.5,2,.1),new THREE.MeshBasicMaterial());
  manager.currentModel = {vrm:{scene:mesh}};
  manager.renderer = {domElement:{getBoundingClientRect:()=>({left:30,top:40,width:200,height:300})}};
  const interaction = new engineWindow.VRMInteraction(manager);
  host.fitPerspectiveModel(THREE,mesh,manager.camera,{width:200,height:300},{mode:'height',padding:6});
  const lens = manager.camera.fov;
  const projection = manager.camera.projectionMatrix.elements.slice();
  for(let i=0;i<30;i++) interaction.updateModelBoundsCache();
  near(manager.camera.fov,lens);
  assert.deepEqual(manager.camera.projectionMatrix.elements,projection);
  assert.ok(interaction._cachedBox && interaction._cachedScreenBounds,'embed still updates interaction bounds');
  near(interaction._cachedScreenBounds.maxY-interaction._cachedScreenBounds.minY,288);
  // Also suppress the delayed relaxation branch, not just FOV expansion.
  interaction._safeFramingBaselineFov=lens-5;
  interaction._safeFramingLastExpandedAt=0;
  mesh.scale.setScalar(.2);
  interaction.updateModelBoundsCache();
  near(manager.camera.fov,lens);
  host.releasePerspectiveReference(mesh,manager.camera);
  mesh.scale.setScalar(1);
  // Reinitializing the same manager without embed must restore desktop policy.
  await core.init('test-canvas','test-container',null,{resizeMode:'fixed'});
  manager.renderer={domElement:{getBoundingClientRect:()=>({left:30,top:40,width:200,height:300})}};
  host.fitPerspectiveModel(THREE,mesh,manager.camera,{width:200,height:300},{mode:'height',padding:6});
  const desktopLens=manager.camera.fov;
  interaction._safeFramingBaselineFov=null;
  interaction.updateModelBoundsCache();
  assert.ok(manager.camera.fov>desktopLens,'desktop still protects animated extremities');
  interaction._safeFramingLastExpandedAt=0;
  const expanded=manager.camera.fov;
  mesh.scale.setScalar(.2);
  interaction.updateModelBoundsCache();
  assert.ok(manager.camera.fov<expanded,'desktop still relaxes to its baseline');
  // MMD's corresponding bounds loop is already read-only for the camera.
  const MMDInteraction = vm.runInNewContext('MMDInteraction',context);
  const mmd = Object.create(MMDInteraction.prototype);
  mmd.manager={...manager,currentModel:{mesh}};
  mmd._boundsUpdateInterval=0; mmd._lastBoundsUpdateTime=0;
  const mmdProjection=manager.camera.projectionMatrix.elements.slice();
  for(let i=0;i<30;i++) mmd.updateScreenBounds();
  assert.ok(mmd._cachedScreenBounds);
  assert.deepEqual(manager.camera.projectionMatrix.elements,mmdProjection);
  host.releasePerspectiveReference(mesh,manager.camera);
  mesh.geometry.dispose(); mesh.material.dispose();
}
async function testMmdEmbeddedControls() {
  const w = { dispatchEvent() {} };
  vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../../static/mmd/mmd-manager.js'), 'utf8'), {
    window: w, CustomEvent: class {}, console: { log() {}, warn() {}, error() {} },
  });
  const manager = new w.MMDManager();
  let buttons = 0; let drag = 0; let tracking = 0; let fallback = false;
  manager.setupFloatingButtons = () => { buttons++; };
  manager.core = { init: async () => {}, loadModel: async model => {
    if (fallback && model !== w.MMDManager.DEFAULT_MODEL_PATH) throw new Error('test fallback');
    return { name: 'example' };
  } };
  manager.interaction = { initDragAndZoom() { drag++; } };
  manager.cursorFollow = { init() { tracking++; }, refresh() {}, setLocalTrackingEnabled() {} };
  for (const embed of [true, false]) {
    const before = buttons;
    await manager.init('canvas', 'container', { embed });
    fallback = false; await manager.loadModel('/models/example.pmx');
    fallback = true; await manager.loadModel('/models/example.pmx');
    assert.equal(buttons - before, embed ? 0 : 3, 'MMD model/fallback load borrowed desktop buttons');
    assert.equal(drag, embed ? 0 : 1);
    assert.equal(tracking, embed ? 0 : 1);
  }
}
testPerspective().catch(error => { console.error(error); process.exitCode = 1; });
