import assert from 'node:assert/strict';
import {createNextVideoQueue} from '../../static/game/games/watch-together/next-video.mjs';
for(const analyses of [[],[{job:'bad',status:'incomplete'}]]) {
  const states=[];let polling=0,delays=0;
  const incompleteQueue=createNextVideoQueue({media:{async request(action){
    if(action==='discover')return {video:{url:'bad',bvid:'bad'}};
    if(action==='prepare')return {id:'bad'};
    if(action==='preparation'){polling++;return {status:'ready',persistence_complete:polling>1};}
    if(action==='history')return {analyses};
  }}},state=>states.push(state),async()=>{delays++;});
  await incompleteQueue.start({topic:'cats'});
  assert.equal(polling,2,'wait for persistence before inspecting ready output');
  assert.equal(delays,1,'unplayable persisted output terminates polling');
  assert.equal(incompleteQueue.busy,false);
  assert.ok(states.some(state=>state.status==='error'));
}
const calls=[],updates=[];
let finishDiscovery;
const row={job:'next',version:'v',status:'ready'};
const game={media:{async request(action,payload){
  calls.push({action,payload});
  if(action==='discover')return new Promise(resolve=>{finishDiscovery=resolve;});
  if(action==='prepare')return {id:'next'};
  if(action==='preparation')return {status:'ready'};
  if(action==='history')return {analyses:[row]};
  throw Error('Unexpected operation: '+action);
}}};
const queue=createNextVideoQueue(game,state=>updates.push(state));
const work=queue.start({topic:'cats',exclude:['current'],character:'cat'});
assert.equal(queue.busy,true);
await queue.start({topic:'duplicate'});
assert.equal(calls.length,1,'only one next video is prepared');
finishDiscovery({video:{title:'Next',url:'video'}});await work;
assert.deepEqual(calls[0].payload.exclude,['current']);
assert.equal(calls.find(c=>c.action==='prepare').payload.source,'discovery');
assert.equal(updates.find(s=>s.status==='ready').row,row);
assert.equal(queue.busy,false);
assert.equal(updates.at(-1).released,true,'only completed work signals release');
queue.clear();
assert.equal(updates.at(-1).released,undefined,'idle clear must not trigger a prefetch retry');
assert.ok(calls.every(c=>!['watch','load'].includes(c.action)),'prefetch never switches or records the current player');
updates.length=0;calls.length=0;
const stale=queue.start({topic:'old'});queue.clear();
finishDiscovery({video:{url:'obsolete'}});await stale;
assert.equal(calls.length,1,'changing selection during discovery avoids obsolete preparation');
assert.equal(updates.some(s=>s.status==='ready'),false);
calls.length=0;
const closing=queue.start({topic:'closing'});queue.dispose();
finishDiscovery({video:{url:'obsolete'}});await closing;
assert.equal(calls.length,1,'exit avoids starting background model work');
console.log('next-video queue: single slot, exclusions, no player interruption, invalidation and disposal passed');

const invalidatedUpdates=[];
let completePreparation;
const pendingGame={media:{async request(action){
  if(action==='discover')return {video:{title:'Next',url:'video'}};
  if(action==='prepare')return {id:'next'};
  if(action==='preparation')return new Promise(resolve=>{completePreparation=resolve;});
  if(action==='history')return {analyses:[row]};
}}};
const invalidatedQueue=createNextVideoQueue(pendingGame,state=>invalidatedUpdates.push(state));
const pendingWork=invalidatedQueue.start({topic:'cats'});
await new Promise(resolve=>setTimeout(resolve,0));
invalidatedQueue.clear();
completePreparation({status:'ready'});
await pendingWork;
assert.deepEqual(invalidatedUpdates.find(s=>s.history)?.history.analyses,[row]);
assert.equal(invalidatedUpdates.some(s=>s.row || s.status==='ready'),false,'stale result updates history only');
console.log('next-video queue: invalidated preparation refreshes history without changing next selection');
const submissionStates=[];
const submissionFailure=createNextVideoQueue({media:{async request(action){
  if(action==='discover')return {video:{bvid:'temporary',url:'video'}};
  throw Error('temporary prepare submission failure');
}}},state=>submissionStates.push(state));
await submissionFailure.start({topic:'cats'});
assert.equal(submissionStates.some(state=>state.candidate),false,'a submission failure must not exclude an unattempted video');
for(const failure of ['error','cancelled']) {
  const states=[];
  const failing={media:{async request(action){
    if(action==='discover')return {video:{bvid:'retryable',url:'video',title:'Retry'}};
    if(action==='prepare')return {id:'failed'};
    if(action==='preparation')return {status:failure};
    throw Error(action);
  }}};
  const retryQueue=createNextVideoQueue(failing,state=>states.push(state));
  await retryQueue.start({topic:'cats'});
  assert.equal(states.some(state=>state.candidate),true,'automatic discovery must skip a failing candidate instead of retrying it indefinitely');
}
