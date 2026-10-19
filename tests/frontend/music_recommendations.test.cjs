const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const source = fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');
const rec = { id: '19f3bbca-a275-43d4-923d-1b2ceee91943', state: 'pending', expires_at: Date.now()/1000 + 1800,
    track: { name: 'Song', artist: 'Artist', url: 'https://example.com/song.mp3', cover: '' } };
async function flush() { for (let i=0;i<40;i++) await Promise.resolve(); }
function surface(t, options={}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/chat', runScripts: 'outside-only' });
    t.after(() => dom.window.close());
    const w = dom.window;
    const messages = new Map(), calls=[], plays=[];
    w.AbortSignal.timeout = () => undefined;
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'test' }) };
    w.reactChatWindowHost = {
        appendMessage(m) { messages.set(m.id,m); },
        updateMessage(id, patch) { Object.assign(messages.get(id),patch); }
    };
    w.fetch = async (url, init) => {
        const event = JSON.parse(init.body); calls.push(event);
        const result = options.respond ? await options.respond(event) : {success:true, applied:true, state:event.kind, playback_grant:event.kind==='accepted'};
        return {ok:result.success, status:result.success?200:409, json:async()=>result};
    };
    w.dispatchMusicPlayDetailed = async (track, opts) => { plays.push({track,opts}); return {ok:true}; };
    if(options.storage) for(const [key,value] of options.storage) w.localStorage.setItem(key,value);
    w.eval(source);
    w.nekoMusicRecommendations.offer(rec, 'turn');
    const message = messages.values().next().value;
    const choose = (kind) => w.dispatchEvent(new w.CustomEvent('react-chat-window:action', { detail: {
        message, action: { action: 'music_recommendation_decision', payload:{recommendationId:rec.id,kind} }
    }}));
    return {w,messages,calls,plays,message,choose};
}
test('offering never plays; acceptance commits before dispatch and repeated clicks only play once', async t=>{
    let release;
    const gate=new Promise(resolve=>{release=resolve;});
    const s=surface(t,{respond:async event=>{await gate;return {success:true,state:event.kind,playback_grant:true};}});
    assert.equal(s.plays.length,0);
    s.choose('accepted');s.choose('accepted');
    await flush();assert.equal(s.plays.length,0);assert.equal(s.calls.length,1);
    release();await flush();assert.equal(s.plays.length,1);
    assert.equal(s.plays[0].opts.recommendationId,rec.id);
    s.choose('accepted');await flush();assert.equal(s.plays.length,1);
});
test('skip records only skip and does not start audio',async t=>{
    const s=surface(t);s.choose('skipped');await flush();
    assert.equal(s.plays.length,0);assert.equal(s.calls[0].kind,'skipped');
    assert.equal(s.message.blocks[0].state,'skipped');
});
test('losing a cross-window decision never starts playback',async t=>{
    const s=surface(t,{respond:async()=>({success:true,state:'accepted',playback_grant:false})});
    s.choose('accepted');await flush();assert.equal(s.plays.length,0);
});
test('failed saves retain the same decision id on retry and do not play early',async t=>{
    let offline=true;
    const s=surface(t,{respond:async event=>{if(offline)throw new Error('offline');return {success:true,state:'accepted',playback_grant:true};}});
    s.choose('accepted');await flush();assert.equal(s.plays.length,0);
    assert.equal(s.message.blocks[0].state,'pending');
    offline=false;s.choose('accepted');await flush();
    assert.equal(s.calls[0].event_id,s.calls[1].event_id);assert.equal(s.plays.length,1);
});
test('leaving while a decision is pending prevents late playback',async t=>{
    let release;
    const gate=new Promise(resolve=>{release=resolve;});
    const s=surface(t,{respond:async()=>{await gate;return {success:true,state:'accepted',playback_grant:true};}});
    s.choose('accepted');await flush();
    s.w.dispatchEvent(new s.w.Event('pagehide'));release();await flush();
    assert.equal(s.plays.length,0);
});
test('offline evidence persists, survives reload and is removed only after acknowledgement',async t=>{
    const s=surface(t,{respond:async()=>{throw new Error('offline');}});
    s.w.nekoMusicRecommendations.recordPlayback(rec.id,'paused',{playback_id:'p',listened_seconds:12});
    await flush();assert.equal(s.w.localStorage.length,1);
    const storage=[];for(let i=0;i<s.w.localStorage.length;i++){const key=s.w.localStorage.key(i);storage.push([key,s.w.localStorage.getItem(key)]);}
    const reloaded=surface(t,{storage});await flush();
    assert.equal(reloaded.calls[0].kind,'paused');assert.equal(reloaded.calls[0].listened_seconds,12);
    assert.equal(reloaded.w.localStorage.length,0);
});
test('account conflict disables stale cards without playing',async t=>{
    const s=surface(t,{respond:async()=>({success:false,error_code:'recommendation_not_found'})});
    s.choose('accepted');await flush();
    assert.equal(s.message.blocks[0].state,'unavailable');assert.equal(s.plays.length,0);
});

test('mirrored decisions release only their own pending proposal, with expiry preserved',async t=>{
    const s=surface(t);
    const other=JSON.parse(JSON.stringify(s.message));
    other.id='music-recommendation-other';other.blocks[0].recommendationId='other';
    s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{type:'append',message:other}}));
    s.choose('skipped');await flush();
    assert.equal(s.w.nekoMusicRecommendations.hasPending(),true,'another proposal remains pending');
    s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{type:'update',messageId:other.id,
        patch:{blocks:[{...other.blocks[0],state:'skipped'}]}}}));
    assert.equal(s.w.nekoMusicRecommendations.hasPending(),false);
});

test('late-window snapshots append once and preserve full metadata for the next window',t=>{
    const s=surface(t), sent=[];
    s.w.__nekoBroadcastChatMirror=data=>sent.push(data);
    const other=JSON.parse(JSON.stringify(s.message));
    other.id='late-card';other.blocks[0].recommendationId='late';
    const deliver=data=>s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:data}));
    deliver({type:'recommendation_snapshot',messages:[other]});
    deliver({type:'recommendation_snapshot',messages:[other]});
    assert.equal(s.messages.size,2);
    deliver({type:'update',messageId:other.id,patch:{blocks:[{...other.blocks[0],state:'skipped'}]}});
    deliver({type:'recommendation_request',sender:'new-window'});
    assert.equal(sent.length,1);assert.equal(sent[0].target,'new-window');
    const restored=sent[0].messages.find(m=>m.id===other.id);
    assert.equal(restored.role,'assistant');assert.equal(restored.blocks[0].state,'skipped');
    assert.equal(s.plays.length,0);
});

for (const outcome of ['accepted', 'network failure']) {
    test(`a late ${outcome} decision response cannot undo remote playback`,async t=>{
        let release;
        const gate=new Promise(resolve=>{release=resolve;});
        const s=surface(t,{respond:async()=>{
            await gate;
            if(outcome==='network failure')throw new Error('offline');
            return {success:true,state:'accepted',playback_grant:false};
        }});
        s.choose('accepted');await flush();
        s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{type:'update',messageId:s.message.id,
            patch:{blocks:[{...s.message.blocks[0],state:'playing'}]}}}));
        release();await flush();
        s.w.__nekoBroadcastChatMirror=data=>{
            assert.equal(data.messages[0].blocks[0].state,'playing');
        };
        s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{type:'recommendation_request',sender:'late'}}));
        assert.equal(s.plays.length,0);assert.equal(s.w.nekoMusicRecommendations.hasPending(),false);
    });
}
