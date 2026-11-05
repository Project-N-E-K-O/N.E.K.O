const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const patch = fs.readFileSync(`${__dirname}/window-level.patch`, 'utf8');
const added = patch.split('\n').filter(line => line.startsWith('+') && !line.startsWith('+++')).map(line=>line.slice(1));
const resetIndex = added.findIndex(line=>line.includes('petWindow.setAlwaysOnTop'));
function create(type, enabled, show) {
  const calls=[];
  const context={winOptions:{type,show}, _topCoordinator:{isEnabled:()=>enabled}, petWindow:{
    setAlwaysOnTop:value=>calls.push(['level',value]), showInactive:()=>calls.push(['show']),
  }};
  vm.createContext(context);
  vm.runInContext(added.slice(0,resetIndex).join('\n'),context);
  calls.push(['construct',context.winOptions.show]);
  vm.runInContext(added.slice(resetIndex).join('\n'),context);
  return calls;
}
for (const show of [undefined,true]) test(`normal panel preserves visible startup (${show})`,()=>{
  assert.deepEqual(create('panel',false,show),[['construct',false],['level',false],['show']]);
});
test('explicitly hidden panel stays hidden',()=>assert.deepEqual(create('panel',false,false),[['construct',false],['level',false]]));
test('enabled preference preserves default construction',()=>assert.deepEqual(create('panel',true,undefined),[['construct',undefined]]));
test('non-panel retains hidden startup',()=>assert.deepEqual(create(undefined,false,false),[['construct',false]]));
