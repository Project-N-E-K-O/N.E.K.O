import assert from 'node:assert/strict';
import {create} from '../../static/game/games/watch-together/live2d-host.mjs';

for (const parameter of ['ParamMouthOpenY','ParamMouthOpen','ParamA','ParamO',null]) {
  let update;
  const writes=[];
  const model={internalModel:{coreModel:{
    getParameterIndex:id=>id===parameter?0:-1,
    setParameterValueById:(...args)=>writes.push(args),
  },on:(event,callback)=>{update=callback;}},destroy(){}};
  globalThis.PIXI={Application:class {
    stage={addChild(){}};
    destroy(){}
  },live2d:{Live2DModel:{from:async()=>model}}};
  const avatar=create({append(){}});
  await avatar.setModel({path:'model'});
  avatar.mouth(.7);update();
  assert.deepEqual(writes,parameter?[[parameter,.7]]:[]);
  avatar.dispose();
}
console.log('watch-together Live2D: alternate and absent mouth parameters passed');
