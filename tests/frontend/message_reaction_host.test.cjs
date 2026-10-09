const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const base = path.join(__dirname, '../../static/app/app-react-chat-window');
const sources = ['geometry-and-messages.js', 'message-bundle-actions-and-prompts.js']
  .map(file => fs.readFileSync(path.join(base, file), 'utf8'));
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  const calls = [];
  const timers = new Map();
  let timerId = 0;
  const I = {
    _sortKeySeq: 0,
    state: { messages: [], _galgameRequestSeq: 0 },
    renderWindow() {},
    isCatLocalChatActive() { return false; },
  };
  const window = {
    __appReactChatWindowParts: I,
    appState: { lanlan_name: 'Neko' },
    _lastSubmittedRequestId: 'test-request',
    nekoLocalMutationSecurity: { async getMutationHeaders() { return { 'X-CSRF-Token': 'test-csrf' }; } },
  };
  const ctx = vm.createContext({
    window, console, Date, AbortController,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, options) {
      return new Promise((resolve, reject) => {
        calls.push({ url, options, body: JSON.parse(options.body), resolve, reject });
      });
    },
  });
  for (const source of sources) vm.runInContext(source, ctx);
  I.renderWindow = () => {};
  I.invalidatePendingGalgameRequest = () => false;
  return { I, window, calls, expire() { for (const fn of [...timers.values()]) fn(); } };
}

function message(id = 'user-1', extra = {}) {
  return { id, role: 'user', author: 'You', time: '10:00', status: 'sent',
    blocks: [{ type: 'text', text: `hello ${id}` }], ...extra };
}


const result = emoji => ({ emotion: 'happy', confidence: 0.9, reaction: { emoji, author: 'Neko' } });
test('reuses existing emotion result without any independent request and refreshes export', () => {
  const {I,window,calls}=fixture(); let refreshed; window.appChatExport={refreshMessageReaction(id){refreshed=id;}};
  I.appendMessage(message()); const target=window.captureMessageReactionTarget('test-request');
  I.appendMessage(message('next')); window.applyMessageReactionFromEmotion(target,result('🥰'));
  assert.equal(I.state.messages[0].reaction.emoji,'🥰'); assert.equal(I.state.messages[1].reaction,undefined);
  assert.equal(refreshed,'user-1'); assert.equal(calls.length,0);
  const snapshot=I.cloneMessage(I.state.messages[0]); snapshot.reaction.emoji='😊';
  assert.equal(I.state.messages[0].reaction.emoji,'🥰');
});
for(const mutate of [f=>f.I.clearMessages(),f=>f.I.setMessages([message()]),f=>f.I.removeMessage('user-1'),f=>f.I.updateMessage('user-1',{blocks:[{type:'text',text:'edited'}]}),f=>f.I.updateMessage('user-1',{status:'failed'}),f=>{f.window.appState.lanlan_name='Other';},f=>{f.I.isCatLocalChatActive=()=>true;}]) {
 test('stale emotion cannot update replaced, edited, failed or different session messages '+mutate.toString(),()=>{
  const f=fixture();f.I.appendMessage(message()); const target=f.window.captureMessageReactionTarget('test-request'); mutate(f);
  f.window.applyMessageReactionFromEmotion(target,result('🎉')); assert.ok(f.I.state.messages.every(m=>!m.reaction));
 });
}
test('restored, tutorial, image-only, failed and local chat messages are ineligible',()=>{
 const f=fixture();f.I.setMessages([message()]);assert.equal(f.window.captureMessageReactionTarget('test-request'),null);
 for(const m of [message('yui-guide-demo'),message('icebreaker-user-demo'),message('image',{blocks:[{type:'image',url:'/a'}]}),message('failed',{status:'failed'}),message('assistant',{role:'assistant'})]){
  f.I.appendMessage(m);assert.equal(f.window.captureMessageReactionTarget('test-request'),null);
 }
 f.I.isCatLocalChatActive=()=>true;f.I.appendMessage(message('local'));assert.equal(f.window.captureMessageReactionTarget('test-request'),null);assert.equal(f.calls.length,0);
});
test('sending waits for sent; null results never rearm on metadata updates',()=>{
 const f=fixture(); f.I.appendMessage(message('u',{status:'sending'}));assert.equal(f.window.captureMessageReactionTarget('test-request'),null);
 f.I.updateMessage('u',{status:'sent'});const target=f.window.captureMessageReactionTarget('test-request');assert.ok(target);
 f.window.applyMessageReactionFromEmotion(target,{emotion:'neutral',reaction:null});f.I.updateMessage('u',{time:'11:00'});
 assert.equal(f.window.captureMessageReactionTarget('test-request'),null);assert.equal(f.calls.length,0);
});
test('request identity mismatch never attaches a reply to a later user',()=>{
 const f=fixture();f.window._lastSubmittedRequestId='second';f.I.appendMessage(message());
 assert.equal(f.window.captureMessageReactionTarget('first'),null);
});
test('late voice transcripts never shift reactions onto the previous turn',()=>{
 const f=fixture();f.window._lastSubmittedRequestId=null;
 const first=f.window.captureMessageReactionTarget();
 f.I.appendMessage(message('v1',{blocks:[{type:'text',text:'I got the job!'}]}));
 const second=f.window.captureMessageReactionTarget();
 f.I.appendMessage(message('v2',{blocks:[{type:'text',text:'my cat is sick'}]}));
 f.window.applyMessageReactionFromEmotion(first,result('😊'));
 f.window.applyMessageReactionFromEmotion(second,result('😢'));
 assert.equal(first,null);assert.equal(second,null);
 assert.ok(f.I.state.messages.every(m=>!m.reaction));
});
test('an unidentified voice reply cannot consume an abandoned text candidate',()=>{
 const f=fixture();f.I.appendMessage(message('abandoned'));
 const target=f.window.captureMessageReactionTarget();
 f.window.applyMessageReactionFromEmotion(target,result('😢'));
 assert.equal(target,null);assert.equal(f.I.state.messages[0].reaction,undefined);
 // A later reply with the actual identity can still safely find its target.
 assert.equal(f.window.captureMessageReactionTarget('test-request').message.id,'abandoned');
});
for(const emoji of ['😊','😄','🥰','✨','🎉','😢','🥺','🤗','💧','😮','😲','👀','❗','😤','😠','💢','😾']) test('accepts configured candidate '+emoji,()=>{
 const f=fixture();f.I.appendMessage(message());f.window.applyMessageReactionFromEmotion(f.window.captureMessageReactionTarget('test-request'),result(emoji));assert.equal(f.I.state.messages[0].reaction.emoji,emoji);
});
for(const r of [null,{error:'failed'},result('not-emoji'),{reaction:{emoji:'😊',author:'Other'}}]) test('invalid result is ignored '+JSON.stringify(r),()=>{
 const f=fixture();f.I.appendMessage(message());f.window.applyMessageReactionFromEmotion(f.window.captureMessageReactionTarget('test-request'),r);assert.equal(f.I.state.messages[0].reaction,undefined);
});

test('overlapping replies use request IDs captured when users send',()=>{
 const f=fixture();f.window._lastSubmittedRequestId='r1';f.I.appendMessage(message('u1'));
 f.window._lastSubmittedRequestId='r2';f.I.appendMessage(message('u2'));
 const first=f.window.captureMessageReactionTarget('r1'),second=f.window.captureMessageReactionTarget('r2');
 f.window.applyMessageReactionFromEmotion(second,result('😾'));f.window.applyMessageReactionFromEmotion(first,result('😊'));
 assert.deepEqual(Array.from(f.I.state.messages,m=>m.reaction.emoji),['😊','😾']);
});
test('overlapping optimistic sends retain identity through reversed completion',()=>{
 const f=fixture();f.window._lastSubmittedRequestId='r1';
 f.I.appendMessage(message('u1',{status:'sending'}));
 f.window._lastSubmittedRequestId='r2';f.I.appendMessage(message('u2',{status:'sending'}));
 assert.equal(f.window.captureMessageReactionTarget('r1'),null);
 f.I.updateMessage('u2',{status:'sent'});f.I.updateMessage('u1',{status:'sent'});
 const first=f.window.captureMessageReactionTarget('r1'),second=f.window.captureMessageReactionTarget('r2');
 assert.equal(first.message.id,'u1');assert.equal(second.message.id,'u2');
 f.window.applyMessageReactionFromEmotion(first,result('😊'));
 f.window.applyMessageReactionFromEmotion(second,result('😢'));
 assert.deepEqual(Array.from(f.I.state.messages,m=>m.reaction.emoji),['😊','😢']);
});
function finalizeFixture(analysis) {
 const websocket=fs.readFileSync(path.join(__dirname,'../../static/app/app-websocket.js'),'utf8');
 const source=websocket.slice(websocket.indexOf('    function finalizeAssistantTurn('),websocket.indexOf('    function ensureAssistantTurnStarted('));
 const timers=[];const applied=[];const S={messageReactionTarget:{id:'original'}};
 const window={_geminiTurnFullText:'reply',analyzeEmotion:analysis,applyMessageReactionFromEmotion:(...args)=>applied.push(args),t:x=>x};
 const context=vm.createContext({window,S,console,Node:{ELEMENT_NODE:1},Promise,setTimeout(fn,delay){timers.push({fn,delay});},emitAssistantLifecycleEvent(){}});
 vm.runInContext(source+';this.finalize=finalizeAssistantTurn;',context);
 return {context,timers,applied,S,window};
}
test('finalization calls emotion once and carries original target through delayed completion',async()=>{
 let resolve;let calls=0;const f=finalizeFixture(()=>{calls++;return new Promise(r=>resolve=r);});
 f.context.finalize('turn1');assert.equal(f.S.messageReactionTarget,null);f.timers.find(t=>t.delay===100).fn();
 f.S.messageReactionTarget={id:'next'};resolve(result('😊'));await flush();
 assert.equal(calls,1);assert.equal(f.applied[0][0].id,'original');
});
test('agent callback still analyzes avatar emotion but cannot react to a user',async()=>{
 const f=finalizeFixture(async()=>result('😊'));f.context.finalize('agent',{enableMusic:false,enableReactions:false});
 await f.timers.find(t=>t.delay===100).fn();assert.equal(f.applied[0][0],null);
});
test('emotion timeout ignores late response',async()=>{
 let resolve;const f=finalizeFixture(()=>new Promise(r=>resolve=r));f.context.finalize('turn');
 const pending=f.timers.find(t=>t.delay===100).fn();f.timers.find(t=>t.delay===5000).fn();await pending;
 resolve(result('😊'));await flush();assert.equal(f.applied.length,0);
});
test('emotion failure never creates a reaction',async()=>{
 const f=finalizeFixture(async()=>{throw new Error('offline');});f.context.finalize('turn');await f.timers.find(t=>t.delay===100).fn();assert.equal(f.applied.length,0);
});

for(const source of ['chat','voice','proactive','game_route']) test('turn-start associates user target only for chat/voice: '+source,()=>{
 const websocket=fs.readFileSync(path.join(__dirname,'../../static/app/app-websocket.js'),'utf8');
 const block=websocket.slice(websocket.indexOf('    function ensureAssistantTurnStarted('),websocket.indexOf('    function emitAssistantSpeechCancel('));
 let captured=null;const S={assistantTurnAwaitingBubble:true};const window={captureMessageReactionTarget(id){captured=id;return {id};}};
 const context=vm.createContext({S,window,Date,allocateAssistantTurnId:()=> 'turn',clearPendingAssistantTurnStart(){},logAssistantLifecycle(){},emitAssistantLifecycleEvent(){},normalizeAssistantTurnId:x=>x,resolveAssistantRequestId:x=>x});
 vm.runInContext(block+';this.start=ensureAssistantTurnStarted;',context);context.start('chunk','server',{source},'r1');
 assert.equal(captured,['chat','voice'].includes(source)?'r1':null);
});
