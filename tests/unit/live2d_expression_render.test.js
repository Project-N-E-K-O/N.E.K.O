const test = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const {loadRuntime,createMao,read} = require('./fixtures/live2d_runtime_fixture.cjs');

test('real Cubism draw uses expression envelope after runtime blink and clearing restores it',async()=>{
 const runtime=await loadRuntime();
 vm.runInContext(read('static/live2d/live2d-emotion.js'),runtime.context);
 const f=createMao(runtime);
 try {
  Object.setPrototypeOf(f.model.internalModel,EventEmitter.prototype);
  EventEmitter.call(f.model.internalModel);
  f.motionManager.state.currentPriority=0;
  f.manager._autoEyeBlinkEnabled=true;
  f.manager._eyeBlinkParams=['ParamEyeLOpen','ParamEyeROpen'].map(id=>({id,idx:f.core.getParameterIndex(id),openValue:1,closedValue:0}));
  f.manager._eyeBlinkState=0;f.manager._eyeBlinkTimer=0;f.manager._eyeBlinkNextTime=100;
  f.manager.mouthValue=0;f.manager.installMouthOverride();
  const params=[{Id:'ParamMouthForm',Value:.8},{Id:'ParamAngleZ',Value:12}];
  assert.equal(f.manager._installManualExpressionOverride(params,900,{blinkScale:.85,mouthRest:.12}),true);
  runtime.context.clock+=1200;
  const native=f.raw.update;let rendered;
  f.raw.update=function(){rendered=Object.fromEntries(['ParamEyeLOpen','ParamMouthOpenY','ParamMouthForm','ParamAngleZ'].map(id=>[id,f.value(id)]));return native.call(this);};
  f.model.internalModel.emit('beforeModelUpdate');f.core.update();
  assert.ok(Math.abs(rendered.ParamEyeLOpen-.85)<1e-6);
  assert.equal(rendered.ParamAngleZ,12);
  // A fully closed blink stays closed even with the expression active.
  f.manager._eyeBlinkState=1;f.manager._eyeBlinkTimer=.079;
  f.model.internalModel.emit('beforeModelUpdate');f.core.update();
  assert.equal(rendered.ParamEyeLOpen,0);
  await f.manager.clearExpression();
  f.manager._eyeBlinkState=0;f.manager._eyeBlinkTimer=0;f.core.update();
  assert.equal(rendered.ParamEyeLOpen,1);
 } finally{f.release();}
});
