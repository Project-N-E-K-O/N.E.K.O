const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const base = path.join(__dirname, '../../static/app/app-react-chat-window');
const sources = ['geometry-and-messages.js', 'message-bundle-actions-and-prompts.js']
  .map(file => fs.readFileSync(path.join(base, file), 'utf8'));
const adapterSources = ['app-chat-text-utils.js', 'app-chat-adapter.js']
  .map(file => fs.readFileSync(path.join(base, '..', file), 'utf8'));
const flush = () => new Promise(resolve => setImmediate(resolve));

function fixture({ withAdapter = false } = {}) {
  const calls = [];
  const events = [];
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
    addEventListener() {},
    dispatchEvent(event) { events.push(event); return true; },
    _lastSubmittedRequestId: 'test-request',
    nekoLocalMutationSecurity: { async getMutationHeaders() { return { 'X-CSRF-Token': 'test-csrf' }; } },
  };
  const ctx = vm.createContext({
    window, console, Date, AbortController, document: { addEventListener() {} },
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
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
  if (withAdapter) {
    window.reactChatWindowHost = I;
    window.mergeMessagesEnabled = true;
    window._nekoAssistantTurnId = 'turn-1';
    for (const source of adapterSources) vm.runInContext(source, ctx);
  }
  return { I, window, calls, events, expire() { for (const fn of [...timers.values()]) fn(); } };
}

test('host replacement distinguishes an empty reset from an ordinary snapshot', () => {
  const { I, events } = fixture();
  I.setMessages([message()]);
  assert.equal(events.at(-1).type, 'react-chat-window:messages-replaced');
  assert.equal(events.at(-1).detail.conversationReset, false);
  I.setMessages([]);
  assert.equal(events.at(-1).detail.conversationReset, true);
  I.clearMessages();
  assert.equal(events.at(-1).type, 'react-chat-window:messages-cleared');
});

test('desktop history restore keeps the destination streaming bubble and newer local messages', () => {
  const { I, window, events } = fixture({ withAdapter: true });
  const live = message('local-reply', {
    role: 'assistant', status: 'streaming', turnId: 'turn-1', createdAt: 20, sortKey: 20,
    blocks: [{ type: 'text', text: 'Partial' }],
  });
  I.setMessages([
    message('stale', { createdAt: 5, sortKey: 5 }),
    live,
    message('new-local', { role: 'assistant', status: 'streaming', turnId: 'turn-2', createdAt: 30, sortKey: 40 }),
  ]);
  window.currentTurnGeminiBubbles = [{ dataset: { reactChatMessageId: live.id } }];
  window.currentGeminiMessage = window.currentTurnGeminiBubbles[0];
  window._geminiTurnFullText = 'Partial';

  I.restoreChatHistory({ updatedAt: 50, handoffStartedAt: 30, messages: [
    message('saved-user', { createdAt: 10, sortKey: 10 }),
    { ...live, id: 'other-window-reply', blocks: [{ type: 'text', text: 'Partial response' }] },
  ] });

  assert.deepEqual(Array.from(I.state.messages, row => row.id), ['saved-user', 'local-reply', 'new-local']);
  assert.equal(I.state.messages[1].blocks[0].text, 'Partial response');
  assert.equal(I.state.messages[1].status, 'streaming');
  assert.equal(events.at(-1).type, 'react-chat-window:messages-replaced');
  assert.equal(events.at(-1).detail.historyRestore, true);
  assert.equal(events.at(-1).detail.conversationReset, false);
  assert.deepEqual(Array.from(events.at(-1).detail.streamingTurnIds), ['turn-1', 'turn-2']);
  window.appendMessage(' res', 'gemini', false);
  assert.equal(I.state.messages[1].blocks[0].text, 'Partial response');
  window.appendMessage('ponse continued', 'gemini', false);
  assert.equal(I.state.messages[1].id, live.id);
  assert.equal(I.state.messages[1].blocks[0].text, 'Partial response continued');
  assert.equal(I.updateMessage('new-local', { status: 'sent' }).status, 'sent');
});

test('desktop history restore keeps saved attachments and the longer local text across a live continuation', () => {
  const { I, window } = fixture({ withAdapter: true });
  window.appendMessage('Local reply', 'gemini', true);
  const live = I.state.messages[0];
  const image = { type: 'image', url: 'https://example.test/reply.png' };
  I.restoreChatHistory({ updatedAt: Date.now(), messages: [
    { ...live, blocks: [{ type: 'text', text: 'Local' }, image] },
  ] });
  assert.equal(I.state.messages[0].blocks[0].text, 'Local reply');
  assert.equal(I.state.messages[0].blocks[1].url, image.url);
  window.appendMessage(' continues', 'gemini', false);
  assert.equal(I.state.messages[0].id, live.id);
  assert.equal(I.state.messages[0].blocks[0].text, 'Local reply continues');
  assert.equal(I.state.messages[0].blocks[1].url, image.url);
});

test('desktop history restore keeps distinct replies when a streaming prefix has multiple matches', () => {
  const { I, window } = fixture();
  window.mergeMessagesEnabled = true;
  const live = message('local-reply', {
    role: 'assistant', status: 'streaming', turnId: 'turn-1', createdAt: 20, sortKey: 20,
    blocks: [{ type: 'text', text: 'Hello again, friend' }],
  });
  I.setMessages([live]);
  window.currentTurnGeminiBubbles = [{ dataset: { reactChatMessageId: live.id } }];
  I.restoreChatHistory({ updatedAt: 30, messages: [
    { ...live, id: 'first-reply', sortKey: 10, blocks: [{ type: 'text', text: 'Hello' }] },
    { ...live, id: 'second-reply', sortKey: 15, blocks: [{ type: 'text', text: 'Hello again' }] },
  ] });

  assert.deepEqual(Array.from(I.state.messages, row => row.id), ['first-reply', 'second-reply', live.id]);
  assert.equal(I.state.messages[0].blocks[0].text, 'Hello');
  assert.equal(I.state.messages[1].blocks[0].text, 'Hello again');
  assert.equal(I.updateMessage(live.id, { status: 'sent' }).status, 'sent');
});

test('desktop history restore does not assign one saved prefix to either of two live replies', () => {
  const { I, window } = fixture();
  window.mergeMessagesEnabled = true;
  const live = ['Hello again', 'Hello there'].map((text, index) => message(`live-${index}`, {
    role: 'assistant', status: 'streaming', turnId: 'turn-1', createdAt: 20 + index, sortKey: 20 + index,
    blocks: [{ type: 'text', text }],
  }));
  I.setMessages(live);
  window.currentTurnGeminiBubbles = live.map(row => ({ dataset: { reactChatMessageId: row.id } }));
  I.restoreChatHistory({ updatedAt: 30, messages: [
    { ...live[0], id: 'saved', sortKey: 10, blocks: [{ type: 'text', text: 'Hello' }] },
  ] });
  assert.deepEqual(Array.from(I.state.messages, row => [row.id, row.blocks[0].text]), [
    ['saved', 'Hello'], ['live-0', 'Hello again'], ['live-1', 'Hello there'],
  ]);
});

test('desktop history restore keeps an unmatched live reply separate from a completed reply', () => {
  const { I, window } = fixture({ withAdapter: true });
  window.appendMessage('Hello again', 'gemini', true);
  const live = I.state.messages[0];
  I.restoreChatHistory({ updatedAt: live.createdAt + 10, messages: [
    { ...live, id: 'completed', sortKey: live.sortKey - 1, status: 'sent', blocks: [{ type: 'text', text: 'Hello' }] },
  ] });
  assert.deepEqual(Array.from(I.state.messages, row => row.id), ['completed', live.id]);
  window.appendMessage(', friend', 'gemini', false);
  assert.deepEqual(Array.from(I.state.messages, row => row.blocks[0].text), ['Hello', 'Hello again, friend']);
});

test('an empty desktop handoff keeps new local messages while an explicit reset discards older ones', () => {
  const { I } = fixture();
  I.setMessages([
    message('during-handoff', { createdAt: 40, sortKey: 40 }),
    message('after-reset', { createdAt: 47, sortKey: 47 }),
  ]);
  I.restoreChatHistory({ updatedAt: 50, handoffStartedAt: 30, resetAt: 0, messages: [] });
  assert.deepEqual(Array.from(I.state.messages, row => row.id), ['during-handoff', 'after-reset']);
  I.restoreChatHistory({ updatedAt: 50, handoffStartedAt: 30, resetAt: 45, messages: [] });
  assert.deepEqual(Array.from(I.state.messages, row => row.id), ['after-reset']);
});

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
