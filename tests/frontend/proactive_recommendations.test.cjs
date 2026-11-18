const assert = require('node:assert/strict');
const test = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '../..');
const { JSDOM } = createRequire(path.join(root, 'frontend/react-neko-chat/package.json'))('jsdom');
const source = fs.readFileSync(path.join(root, 'static/app/app-recommendations.js'), 'utf8');
const recommendation = category => ({ id: '023bf0f3-77ac-433a-8138-5ba7dc6da15f', category, state: 'pending',
    expires_at: Date.now()/1000+1800, content: { title:'Topic',source:'Example',url:'https://example.com/content' } });
const settle = async () => { for(let i=0;i<40;i++)await Promise.resolve(); };
function surface(t, options={}) {
    const dom = new JSDOM('<!doctype html><body></body>',{url:'http://127.0.0.1:48911/chat',runScripts:'outside-only'});
    t.after(()=>dom.window.close());
    const w=dom.window, messages=new Map(), requests=[], opens=[], popups=[], broadcasts=[];
    w.lanlan_config={lanlan_name:'Neko'};
    w.AbortSignal.timeout=()=>undefined;
    w.nekoLocalMutationSecurity={getMutationHeaders:async()=>({'X-CSRF-Token':'fixture'})};
    w.electronShell=options.browser?undefined:{openExternal:async url=>opens.push(url)};
    w.open=()=>{const popup={closed:false,opener:w,location:{href:''},close(){this.closed=true;}};popups.push(popup);return popup;};
    const copy=value=>options.serializedHost?structuredClone(value):value;
    const host={appendMessage:m=>messages.set(m.id,copy(m)),updateMessage:(id,p)=>Object.assign(messages.get(id),copy(p))};
    if(!options.lateHost)w.reactChatWindowHost=host;
    w.__nekoBroadcastChatMirror=payload=>broadcasts.push(payload);
    w.fetch=async(_url,init)=>{
        const event=JSON.parse(init.body);requests.push(event);
        const result=options.respond?await options.respond(event):{success:true,state:event.kind,action_grant:event.kind==='accepted'};
        return {ok:result.success,status:result.success?200:409,json:async()=>result};
    };
    w.eval(source);
    const offer=category=>w.nekoRecommendations.offer(recommendation(category),'source-turn');
    const action=(name,kind)=>{
        const message=messages.values().next().value;
        w.dispatchEvent(new w.CustomEvent('react-chat-window:action',{detail:{message,action:{action:name,payload:{recommendationId:recommendation('news').id,kind}}}}));
    };
    return {w,messages,requests,opens,popups,broadcasts,host,offer,action,choose:kind=>action('proactive_recommendation_decision',kind)};
}

test('nonmusic opens only after the durable grant and duplicate clicks/offer do not replay it',async t=>{
    let release;const gate=new Promise(resolve=>release=resolve);
    const s=surface(t,{respond:async event=>{await gate;return {success:true,state:event.kind,action_grant:true};}});
    s.offer('news');s.offer('news');assert.equal(s.messages.size,1);
    assert.equal(s.w.nekoMusicRecommendations.hasPending(),false);
    s.choose('accepted');s.choose('accepted');await settle();
    assert.equal(s.opens.length,0);assert.equal(s.requests.length,1);
    release();await settle();assert.deepEqual(s.opens,['https://example.com/content']);
    s.offer('news');s.choose('accepted');await settle();assert.equal(s.opens.length,1);
});

test('skip and a losing cross-window decision never open content',async t=>{
    for(const kind of ['accepted','skipped']){
        const s=surface(t,{respond:async()=>({success:true,state:'skipped',action_grant:false})});
        s.offer('community');s.choose(kind);await settle();assert.equal(s.opens.length,0);
        assert.equal(s.messages.values().next().value.blocks[0].state,'skipped');
    }
});

test('a failed choice retries with the same event identity and opens once',async t=>{
    let offline=true;
    const s=surface(t,{respond:async event=>{if(offline)throw Error('offline');return {success:true,state:event.kind,action_grant:true};}});
    s.offer('video');s.choose('accepted');await settle();assert.equal(s.opens.length,0);
    assert.equal(s.messages.values().next().value.blocks[0].state,'pending');
    offline=false;s.choose('accepted');await settle();
    assert.equal(s.requests[0].event_id,s.requests[1].event_id);assert.equal(s.opens.length,1);
});

test('disabled or replaced account prevents external action',async t=>{
    const s=surface(t,{respond:async()=>({success:false,error_code:'recommendation_disabled'})});
    s.offer('personal');s.choose('accepted');await settle();
    assert.equal(s.opens.length,0);assert.equal(s.messages.values().next().value.blocks[0].state,'unavailable');
});

for(const category of ['news','video','personal']) for(const browser of [false,true]) {
    test(`${category} saved acceptance survives local refusal in both mirror orders (browser=${browser})`,async t=>{
        const a=surface(t,{browser,serializedHost:true});
        const b=surface(t,{browser,serializedHost:true,respond:async()=>({success:false,error_code:'recommendation_disabled'})});
        a.offer(category);b.offer(category);
        a.choose('accepted');await settle();
        const accepted=structuredClone(a.messages.values().next().value);
        assert.equal(accepted.blocks[0].state,'accepted');
        b.choose('accepted');await settle();
        const refused=structuredClone(b.messages.values().next().value);
        assert.equal(refused.blocks[0].state,'unavailable');
        const mirror=(s,message)=>{
            const patch={blocks:structuredClone(message.blocks)};
            s.host.updateMessage(message.id,patch);
            s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{type:'update',messageId:message.id,patch:structuredClone(patch)}}));
        };
        mirror(b,accepted);
        assert.equal(b.messages.values().next().value.blocks[0].state,'accepted','saved acceptance replaces a local refusal');
        mirror(a,refused);
        assert.equal(a.messages.values().next().value.blocks[0].state,'accepted','late refusal cannot hide the saved source link');
        mirror(b,refused);
        assert.equal(b.messages.values().next().value.blocks[0].state,'accepted');
        for(const s of [a,b]){
            const block=s.messages.values().next().value.blocks[0];
            assert.equal(block.content.url,recommendation(category).content.url);
            assert.equal(s.requests.length,1,'mirroring does not fabricate another decision');
            s.choose('accepted');await settle();
            assert.equal(s.requests.length,1,'settled cards cannot request another grant');
        }
        assert.equal(a.opens.length,browser?0:1);
        assert.equal(b.opens.length,0,'the refused window never opens from a mirror');
        if(browser){
            assert.equal(a.popups[0].location.href,recommendation(category).content.url);
            assert.ok(b.popups[0].closed);
        }
    });
}

test('browser pre-opens synchronously but closes the placeholder after a refused decision',async t=>{
    const s=surface(t,{browser:true,respond:async()=>({success:true,state:'accepted',action_grant:false})});
    s.offer('news');s.choose('accepted');assert.equal(s.popups.length,1);assert.equal(s.popups[0].location.href,'');
    await settle();assert.ok(s.popups[0].closed);
});

test('navigation and BFCache restoration fence old replies and keep the choice recoverable',async t=>{
    let release, first=true;const gate=new Promise(resolve=>release=resolve);
    const s=surface(t,{browser:true,respond:async event=>{if(first){first=false;await gate;}return {success:true,state:event.kind,action_grant:true};}});
    s.offer('news');s.choose('accepted');await settle();
    s.w.dispatchEvent(new s.w.Event('pagehide'));assert.ok(s.popups[0].closed);
    s.w.dispatchEvent(new s.w.Event('pageshow'));release();await settle();
    assert.equal(s.popups[0].location.href,'');
    assert.equal(s.messages.values().next().value.blocks[0].state,'pending');
    s.choose('accepted');await settle();assert.equal(s.popups[1].location.href,'https://example.com/content');
    assert.equal(s.requests[0].event_id,s.requests[1].event_id);
});

test('new memes reveal directly; closing a preview does not fabricate a choice',async t=>{
    const s=surface(t);s.offer('meme');s.choose('accepted');await settle();
    assert.equal(s.opens.length,0);assert.equal(s.popups.length,0);
    s.action('proactive_recommendation_dismiss');await settle();
    const block=s.messages.values().next().value.blocks[0];
    assert.equal(block.state,'pending');assert.equal(block.directDelivery,true);assert.equal(block.dismissed,true);
    assert.deepEqual(s.requests.map(x=>x.kind),[]);
});

test('unsafe external schemes and embedded credentials are refused',t=>{
    const s=surface(t);
    for(const url of ['javascript:alert(1)','file:///C:/Windows','data:text/html,no','https://name:secret@example.com']){
        assert.equal(s.w.nekoRecommendations.offer({...recommendation('news'),content:{title:'Bad',source:'',url}}),false);
    }
    assert.equal(s.messages.size,0);
});

test('generic topics and source-free recommendations never create a card or evidence',async t=>{
    const s=surface(t);
    assert.equal(s.offer('chat'),false);
    assert.equal(s.w.nekoRecommendations.offer({...recommendation('news'),content:{title:'',source:'',url:''}}),false);
    await settle();
    assert.equal(s.messages.size,0);
    assert.equal(s.requests.length,0);
});

test('late host startup requests a fresh snapshot and replays content once',t=>{
    const s=surface(t,{lateHost:true});
    s.w.reactChatWindowHost=s.host;
    s.w.document.dispatchEvent(new s.w.Event('DOMContentLoaded'));
    assert.ok(s.broadcasts.filter(x=>x.type==='recommendation_request').length>=2);
    const rec=recommendation('news');
    const message={id:'content',role:'assistant',author:'Neko',time:'',blocks:[{type:'proactive_recommendation',recommendationId:rec.id,category:rec.category,content:rec.content,state:'pending',expiresAt:rec.expires_at*1000}]};
    const request=s.broadcasts.filter(data=>data.type==='recommendation_request').at(-1);
    for(let i=0;i<2;i++)s.w.dispatchEvent(new s.w.CustomEvent('neko:chat-mirror',{detail:{...request,type:'recommendation_snapshot',messages:[message]}}));
    assert.equal(s.messages.size,1);
});

test('screen observations deliver directly without a choice and reject empty or unsafe content',async t=>{
    for(const kind of ['accepted','skipped']) {
        const s=surface(t,{browser:true});
        const rec={...recommendation('vision'),content:{title:'You are tending a farm.',source:'',url:''}};
        assert.equal(s.w.nekoRecommendations.offer({...rec,content:{...rec.content,title:'  '}}),false);
        assert.equal(s.w.nekoRecommendations.offer({...rec,content:{...rec.content,url:'javascript:alert(1)'}}),false);
        assert.equal(s.w.nekoRecommendations.offer(rec,'screen-turn'),true);
        s.choose(kind);await settle();
        assert.equal(s.messages.values().next().value.blocks[0].state,'pending');
        assert.equal(s.messages.values().next().value.blocks[0].directDelivery,true);
        assert.deepEqual(s.requests.map(event=>event.kind),[]);
        assert.equal(s.popups.length,0);assert.equal(s.opens.length,0);
    }
});
