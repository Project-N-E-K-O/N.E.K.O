const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { createRequire } = require('node:module');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const readProduction = file => process.env.RECOMMENDATIONS_REVISION
    ? execFileSync('git', ['show', `${process.env.RECOMMENDATIONS_REVISION}:${file}`], {cwd:root,encoding:'utf8'})
    : fs.readFileSync(path.join(root,file),'utf8');
const source = readProduction('static/app/app-recommendations.js');
const rec = { id: '19f3bbca-a275-43d4-923d-1b2ceee91943', state: 'pending', expires_at: Date.now()/1000 + 1800,
    track: { name: 'Song', artist: 'Artist', url: 'https://example.com/song.mp3', cover: '' } };
async function flush() { for (let i=0;i<40;i++) await Promise.resolve(); }
function surface(t, options={}) {
    const dom = new JSDOM('<!doctype html><body></body>', { url: 'http://localhost:48911/chat', runScripts: 'outside-only' });
    t.after(() => dom.window.close());
    const w = dom.window;
    w.lanlan_config = { lanlan_name: 'Neko' };
    const messages = new Map(), calls=[], plays=[], broadcasts=[];
    w.AbortSignal.timeout = () => undefined;
    w.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'test' }) };
    w.__nekoBroadcastChatMirror = data => broadcasts.push(data);
    w.reactChatWindowHost = {
        appendMessage(m) { messages.set(m.id,m); },
        updateMessage(id, patch) { Object.assign(messages.get(id),patch); }
    };
    w.fetch = async (url, init) => {
        // Card choices feeding the backend music cooldown are not evidence events.
        if (url === '/api/proactive/music_card_decision') return {ok:true, status:200, json:async()=>({success:true})};
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
    return {w,messages,calls,plays,message,choose,broadcasts};
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
    const s=surface(t), sent=s.broadcasts;
    const other=JSON.parse(JSON.stringify(s.message));
    other.id='late-card';other.blocks[0].recommendationId='late';
    const deliver=data=>s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:data}));
    const request=sent.find(data=>data.type==='recommendation_request');
    deliver({...request,type:'recommendation_snapshot',messages:[other]});
    deliver({...request,type:'recommendation_snapshot',messages:[other]});
    assert.equal(s.messages.size,2);
    deliver({type:'update',messageId:other.id,patch:{blocks:[{...other.blocks[0],state:'skipped'}]}});
    deliver({type:'recommendation_request',sender:'new-window',character:'Neko',requestId:'new-request'});
    const reply=sent.find(data=>data.type==='recommendation_snapshot');
    assert.equal(reply.target,'new-window');assert.equal(reply.requestId,'new-request');
    const restored=reply.messages.find(m=>m.id===other.id);
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
        s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{type:'recommendation_request',sender:'late',character:'Neko',requestId:'request'}}));
        assert.equal(s.plays.length,0);assert.equal(s.w.nekoMusicRecommendations.hasPending(),false);
    });
}

for (const error_code of ['feedback_capacity_reached','feedback_upload_capacity_reached']) {
    test(`${error_code} retains evidence until a later acknowledgement`,async t=>{
        let full=true;
        const s=surface(t,{respond:async event=>full?{success:false,error_code}:{success:true,state:event.kind}});
        s.w.nekoMusicRecommendations.recordPlayback(rec.id,'stopped',{playback_id:'p',listened_seconds:12});
        await flush();assert.equal(s.w.localStorage.length,1);
        const original=s.calls[0];
        full=false;s.w.dispatchEvent(new s.w.Event('online'));await flush();
        assert.equal(s.calls.length,2);assert.deepEqual(s.calls[1],original);
        assert.equal(s.w.localStorage.length,0);
    });
    test(`${error_code} keeps an uncommitted choice retryable with its original identity`,async t=>{
        let full=true;
        const s=surface(t,{respond:async event=>full?{success:false,error_code}:{success:true,state:event.kind,playback_grant:true}});
        s.choose('accepted');await flush();
        assert.equal(s.message.blocks[0].state,'pending');assert.equal(s.plays.length,0);
        full=false;s.choose('accepted');await flush();
        assert.equal(s.calls[0].event_id,s.calls[1].event_id);assert.equal(s.plays.length,1);
    });
}

function lifecycleSurface(t,{name='First',respond,broadcast,browser=false}={}) {
    const dom=new JSDOM('<!doctype html><body></body>',{url:'http://localhost:48911/chat',runScripts:'outside-only'});
    t.after(()=>dom.window.close());
    const w=dom.window,calls=[],plays=[],opens=[],popups=[],broadcasts=[];
    w.appState={};w.lanlan_config={lanlan_name:name};w.AbortSignal.timeout=()=>undefined;
    for(const file of ['bootstrap-state-and-geometry.js','geometry-and-messages.js','message-bundle-actions-and-prompts.js']) {
        w.eval(readProduction('static/app/app-react-chat-window/'+file));
    }
    const I=w.__appReactChatWindowParts;I.renderWindow=()=>{};
    Object.assign(w.reactChatWindowHost,{appendMessage:I.appendMessage,updateMessage:I.updateMessage,
        removeMessage:I.removeMessage,clearMessages:I.clearMessages,getState:I.getStateSnapshot});
    const deliver=data=>w.dispatchEvent(new w.CustomEvent('neko:chat-mirror',{detail:structuredClone(data)}));
    w.__nekoBroadcastChatMirror=data=>{broadcasts.push(structuredClone(data));broadcast?.(data);};
    w.nekoLocalMutationSecurity={getMutationHeaders:async()=>({'X-CSRF-Token':'fixture'})};
    w.open=()=>{const popup={closed:false,opener:w,location:{href:''},close(){this.closed=true;}};popups.push(popup);return popup;};
    if(!browser)w.electronShell={openExternal:async url=>opens.push(url)};
    w.fetch=async(url,init)=>{
        if(url==='/api/characters')return {ok:true,json:async()=>({'猫娘':{Second:{model_type:'live2d'}}})};
        if(!url.endsWith('/events'))return {ok:true,json:async()=>({domains:[]})};
        const event=JSON.parse(init.body);calls.push(event);
        const result=respond?await respond(event):{success:true,state:event.kind,playback_grant:true,action_grant:true};
        return {ok:result.success,status:result.success?200:409,json:async()=>result};
    };
    w.dispatchMusicPlayDetailed=async track=>{plays.push(track);return {ok:true};};
    w.eval(readProduction('static/app/app-character.js'));
    w.invalidatePendingMusicSearch=()=>{};
    w.eval(source);
    const music=(id=rec.id)=>w.nekoMusicRecommendations.offer({...rec,id},'first-turn');
    const content=(id='023bf0f3-77ac-433a-8138-5ba7dc6da15f')=>w.nekoRecommendations.offer({id,category:'news',state:'pending',
        expires_at:Date.now()/1000+1800,content:{title:'Original article',source:'Fixture',url:'https://example.com/article'}},'first-turn');
    const choose=(id,kind='accepted')=>{
        const message=I.state.messages.find(message=>message.blocks[0]?.recommendationId===id);
        const type=message.blocks[0].type==='music_recommendation'?'music':'proactive';
        w.dispatchEvent(new w.CustomEvent('react-chat-window:action',{detail:{message,action:{action:type+'_recommendation_decision',payload:{recommendationId:id,kind}}}}));
    };
    return {w,I,calls,plays,opens,popups,broadcasts,deliver,music,content,choose,clear:()=>w.reactChatWindowHost.clearMessages()};
}

test('real host clear retires proposals, releases pending and preserves offline/player evidence',async t=>{
    const s=lifecycleSurface(t,{respond:async()=>{throw Error('offline');}});
    const beforeClear=s.w.nekoRecommendations.captureOfferContext();
    assert.equal(beforeClear(),true);
    s.music();s.content();
    s.w.nekoMusicRecommendations.recordPlayback(rec.id,'paused',{playback_id:'p',listened_seconds:12});
    await flush();assert.equal(s.w.localStorage.length,1);
    const stale=structuredClone(s.I.state.messages);
    const request=s.broadcasts.find(data=>data.type==='recommendation_request');
    s.clear();assert.equal(s.I.state.messages.length,0);assert.equal(s.w.nekoMusicRecommendations.hasPending(),false);
    assert.equal(beforeClear(),false,'an HTTP producer captured before this clear has retired');
    assert.equal(s.w.nekoRecommendations.captureOfferContext()(),true);
    s.deliver({...request,type:'recommendation_snapshot',messages:stale});
    for(const message of stale)s.deliver({type:'append',message});
    assert.equal(s.I.state.messages.length,0);
    assert.equal(s.w.localStorage.length,1);
    s.w.nekoMusicRecommendations.recordPlayback(rec.id,'stopped',{playback_id:'p',listened_seconds:13});
    await flush();assert.equal(s.w.localStorage.length,2);assert.equal(s.I.state.messages.length,0);
    assert.equal(s.music(),false,'retired UUID cannot be offered again');
    assert.equal(s.music('01234567-1234-4123-8123-012345678900'),true,'new same-character offer remains available');
});

test('actual character switch and cloud reload cannot seed old cards into a new window',async t=>{
    const s=lifecycleSurface(t);s.music();s.content();
    s.w.dispatchEvent(new s.w.CustomEvent('neko-cloudsave-character-reloaded',{detail:{character_name:'First'}}));
    assert.equal(s.I.state.messages.length,0);assert.equal(s.w.nekoMusicRecommendations.hasPending(),false);
    await s.w.handleCatgirlSwitch('Second','First');
    assert.equal(s.w.lanlan_config.lanlan_name,'Second');assert.equal(s.w.appState.isSwitchingCatgirl,false);
    const b=lifecycleSurface(t,{name:'Second',broadcast:data=>s.deliver({...data,sender:'Second-window'})});
    s.w.__nekoBroadcastChatMirror=data=>b.deliver(data);
    b.w.dispatchEvent(new b.w.Event('pageshow'));
    assert.equal(b.I.state.messages.length,0);
    assert.equal(s.music('01234567-1234-4123-8123-012345678901'),true);
    b.w.dispatchEvent(new b.w.Event('pageshow'));
    assert.equal(b.I.state.messages.length,1);
    assert.equal(b.I.state.messages[0].author,'Second');
});

for(const type of ['music','content'])test(`clearing during ${type} consent prevents late actions and preserves a new proposal`,async t=>{
    let release;
    const gate=new Promise(resolve=>release=resolve);
    const s=lifecycleSurface(t,{respond:async event=>{await gate;return {success:true,state:event.kind,playback_grant:true,action_grant:true};}});
    const id=type==='music'?rec.id:'023bf0f3-77ac-433a-8138-5ba7dc6da15f';
    s[type]();s.choose(id);await flush();s.clear();
    const next='01234567-1234-4123-8123-012345678902';s.music(next);
    release();await flush();
    assert.equal(s.plays.length,0);assert.equal(s.opens.length,0);
    assert.equal(s.I.state.messages.length,1);assert.equal(s.I.state.messages[0].blocks[0].recommendationId,next);
    assert.equal(s.I.state.messages[0].blocks[0].state,'pending');
});

test('reset retires only its UUIDs across windows, without erasing text or a new owner',t=>{
    const a=lifecycleSurface(t),b=lifecycleSurface(t);
    a.music();
    const first=a.I.state.messages[0];
    b.deliver({...b.broadcasts.find(data=>data.type==='recommendation_request'),type:'recommendation_snapshot',messages:[first]});
    const next='01234567-1234-4123-8123-012345678903';b.music(next);
    b.I.appendMessage({id:'ordinary',role:'user',blocks:[{type:'text',text:'Keep this ordinary text'}]});
    a.w.__nekoBroadcastChatMirror=data=>b.deliver(data);
    a.clear();
    assert.deepEqual(Array.from(b.I.state.messages,message=>message.id),['music-recommendation-'+next,'ordinary']);
    assert.equal(b.w.nekoMusicRecommendations.hasPending(),true);
});

test('snapshots require a current request and matching offer character',t=>{
    const s=lifecycleSurface(t);s.music();const message=structuredClone(s.I.state.messages[0]);
    const target=lifecycleSurface(t,{name:'Second'}),request=target.broadcasts.find(data=>data.type==='recommendation_request');
    target.deliver({...request,type:'recommendation_snapshot',messages:[message]});
    assert.equal(target.I.state.messages.length,0,'a matching request never imports another character');
    message.author='Second';
    target.deliver({type:'recommendation_snapshot',messages:[message]});
    target.deliver({...request,type:'recommendation_snapshot',requestId:'unrelated',messages:[message]});
    assert.equal(target.I.state.messages.length,0,'missing and stale request identities are rejected');
    target.deliver({...request,type:'recommendation_snapshot',messages:[message]});
    assert.equal(target.I.state.messages.length,1);
});

test('clearing closes a browser consent placeholder before a late grant arrives',async t=>{
    let release;const gate=new Promise(resolve=>release=resolve);
    const s=lifecycleSurface(t,{browser:true,respond:async event=>{await gate;return {success:true,state:event.kind,action_grant:true};}});
    s.content();s.choose('023bf0f3-77ac-433a-8138-5ba7dc6da15f');await flush();
    assert.equal(s.popups.length,1);assert.equal(s.popups[0].closed,false);
    s.clear();assert.equal(s.popups[0].closed,true);
    release();await flush();assert.equal(s.popups[0].location.href,'');assert.equal(s.I.state.messages.length,0);
});

test('an old finally cannot unlock a newer same-recommendation consent after BFCache restoration',async t=>{
    const releases=[];
    const s=lifecycleSurface(t,{respond:event=>new Promise(resolve=>releases.push(()=>resolve({success:true,state:event.kind,playback_grant:true})))});
    s.music();s.choose(rec.id);await flush();
    s.w.dispatchEvent(new s.w.Event('pagehide'));s.w.dispatchEvent(new s.w.Event('pageshow'));
    s.choose(rec.id);await flush();assert.equal(s.calls.length,2);
    releases[0]();await flush();assert.equal(s.plays.length,0);
    s.choose(rec.id);await flush();assert.equal(s.calls.length,2,'old finally did not release the new operation');
    releases[1]();await flush();assert.equal(s.plays.length,1);
});

test('display-name normalization does not change the offer owner',async t=>{
    const s=lifecycleSurface(t,{name:'<Original name>'});s.content();
    s.choose('023bf0f3-77ac-433a-8138-5ba7dc6da15f');await flush();
    assert.equal(s.opens.length,1);
    const request={type:'recommendation_request',requestId:'same-role',character:'<Original name>',sender:'new-window'};
    s.deliver(request);
    const reply=s.broadcasts.find(data=>data.requestId==='same-role');
    assert.equal(reply.messages[0].author,'<Original name>');
});
