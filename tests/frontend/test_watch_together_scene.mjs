import assert from 'node:assert/strict';
import {run} from '../../static/game/games/watch-together/scene.mjs';

async function fixture(confirm, afterDownload=false) {
  const elements = new Map();
  globalThis.document = {getElementById(id) {
    if (!elements.has(id)) elements.set(id,{value:'',textContent:'',disabled:false,replaceChildren(){},append(){}});
    return elements.get(id);
  }};
  document.getElementById('url');document.getElementById('topic');
  globalThis.location = {search:''};
  const prompts = [];
  globalThis.window = {i18n:{t:key=>key},confirm:message=>{prompts.push(message);return confirm;}};
  const calls = [];
  const game = {
    runtime:{state:'idle',configure(){},async end(){}},
    speech:{onState(){}},voice:{onState(){},onTranscript(){}},events:{on(){}},
    media:{async request(action,payload) {
      calls.push({action,payload});
      if(action==='character')return {lanlan_name:'cat'};
      if(action==='history')return {analyses:[],watches:[]};
      if(action==='discover')return {video:null};
      if(action==='prepare') {
        if(afterDownload) {
          if(payload.confirmation_job){game.disposed=true;return {ok:true};}
          return {id:'downloaded'};
        }
        if(!payload.confirmed_duration)return {confirmation_required:true,video:{title:'Long',duration:301,url:'BV1GJ411x7h7'}};
        game.disposed=true;return {id:'prepared'};
      }
      if(action==='preparation' && afterDownload)return {status:'awaiting_confirmation',confirmation_required:true,confirmation_video:{title:'Boundary',duration:300.04}};
      throw Error(action);
    }}
  };
  await run(game,'cat');
  return {elements,calls,prompts};
}
const cancel = await fixture(false);
cancel.elements.get('url').value='BV1GJ411x7h7';
await cancel.elements.get('prepare').onsubmit({preventDefault(){}});
assert.equal(cancel.prompts.length,1);
assert.equal(cancel.calls.filter(call=>call.action==='prepare').length,1);
assert.match(cancel.elements.get('status').textContent,/cancelled/);
const accept = await fixture(true);
accept.elements.get('url').value='BV1GJ411x7h7';
await accept.elements.get('prepare').onsubmit({preventDefault(){}});
assert.equal(accept.calls.filter(call=>call.action==='prepare').length,2);
assert.equal(accept.calls.at(-1).payload.confirmed_duration,301);
const empty = await fixture(false);
empty.elements.get('topic').value='cats';
await empty.elements.get('discover').onsubmit({preventDefault(){}});
assert.equal(empty.calls.at(-1).payload.topic,'cats');
assert.equal(empty.calls.filter(call=>call.action==='prepare').length,0);
assert.match(empty.elements.get('status').textContent,/noCandidates/);
console.log('watch-together scene: cancel, confirm and empty discovery passed');
for (const accepted of [true,false]) {
  const boundary = await fixture(accepted,true);
  boundary.elements.get('url').value='BV1GJ411x7h7';
  await boundary.elements.get('prepare').onsubmit({preventDefault(){}});
  assert.equal(boundary.prompts.length,1);
  assert.match(boundary.prompts[0],/301s/);
  const decision=boundary.calls.at(-1).payload;
  assert.equal(decision.confirmation_job,'downloaded');
  assert.equal(decision.accepted,accepted);
  assert.equal(decision.confirmed_duration,300.04);
}
console.log('watch-together scene: post-download confirmation and cancellation passed');
