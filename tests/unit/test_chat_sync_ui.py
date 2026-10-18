"""Execute the served classic script to check identity-only passive restoration."""

import json
from pathlib import Path
import re

import pytest

from tests.node_harness import run_node_script


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "static/app/app-chat-sync.js"


def run_script(assertions: str) -> None:
    """Run the actual served JavaScript with isolated DOM/host boundaries."""
    bootstrap = r"""
        const fs = require('node:fs');
        const vm = require('node:vm');
        const assert = require('node:assert/strict');
        const documentEvents = {}, windowEvents = {}, intervals = [];
        function element() {
            return {children:[],events:{},dataset:{},open:false, setAttribute(){},
                appendChild(child){this.children.push(child);}, addEventListener(name,callback){this.events[name]=callback;},
                replaceChildren(){this.children=[];},
                querySelectorAll(selector){const all=this.children.flatMap(child=>[child,...child.querySelectorAll('*')]);
                    if(selector==='*')return all;
                    return all.filter(child=>selector==='[data-sync-action]'?!!child.dataset.syncAction:
                        selector==='[data-history-action]'?!!child.dataset.historyAction:
                        selector.includes('"'+(child.dataset.syncAction||child.dataset.historyAction)+'"'));},
                querySelector(selector){return this.querySelectorAll(selector)[0];},
                showModal(){this.open=true;},close(){this.open=false;}};
        }
        const headerButton=element(), body=element();
        global.window = { addEventListener(name,callback) {windowEvents[name]=callback;}, confirm(){return true;},
            setInterval(callback,delay){intervals.push({callback,delay});return intervals.length;},clearInterval(){},
            lanlan_config: { lanlan_name: 'Neko' }, t(key,values) { return values && values.name ? key+':'+values.name
                :values && typeof values.count==='number'?key+':'+values.count:key; } };
        global.document = { addEventListener(name,callback) {documentEvents[name]=callback;},
            getElementById(){return headerButton;},createElement:element,body };
        global.fetch = () => { throw new Error('Unexpected network from pure restoration'); };
        vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
        const id = 'a'.repeat(32), other = 'b'.repeat(32);
        const original = {messageId:id,characterSyncId:'c'.repeat(32),role:'user',text:'same',
            originalTime:'1791540000123',originStreamId:'d'.repeat(32),originSeq:1};
    """
    script = (bootstrap + assertions).replace('process.argv[1]', json.dumps(str(SCRIPT)))
    result = run_node_script("node", script, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


HISTORY_CONSENT_SETUP = r"""
    const uid='c'.repeat(32), task='f'.repeat(32), requests=[];
    let binding={enabled:true,ownerId:'owner',bindingId:'binding',generation:1,pendingOriginals:0,pendingDeletions:0};
    let snapshot={taskId:task,characterSyncId:uid,localName:'Neko',cloudName:'Cloud Neko',
        count:3,unverifiedCount:null,status:'preview',registeredCount:0,cancelledCount:0};
    global.fetch=async(path,options)=>({ok:true,async json(){
        requests.push({path,method:options.method,body:options.body});
        if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[{displayName:'Neko',characterSyncId:uid,binding}]};
        if(path.endsWith('/history/preview'))return snapshot;
        if(path.endsWith('/history/approve'))return {...snapshot,status:'approved'};
        if(path.endsWith('/history'))return {originals:[]};
        throw new Error('Unexpected request '+path);
    }});
    documentEvents.DOMContentLoaded();
    await headerButton.events.click();
    const modal=body.children[0], action=name=>modal.querySelector('[data-sync-action="'+name+'"]'),
        choice=name=>modal.querySelector('[data-history-action="'+name+'"]');
    const content=()=>modal.querySelectorAll('*').map(node=>node.textContent||'').join('\n');
    assert.equal(requests.filter(item=>item.path.includes('/history/preview')).length,0);
"""


@pytest.mark.parametrize("broken_observer", [False, True])
def test_optional_trace_observer_never_changes_passive_history(broken_observer):
    run_script(r"""(async()=>{
        const observed=[], merged=[];
        window.__nekoChatSyncTrace=(event,metadata)=>{
            observed.push({event,metadata});
            if(BROKEN)throw new Error('observer unavailable');
        };
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages:items=>merged.push(items)};
        global.fetch=async()=>({ok:true,json:async()=>({originals:[original]})});
        await window.nekoChatSync.loadHistory({characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor:1}}]});
        assert.equal(merged.length,1);
        assert.deepEqual(observed,[{event:'ui_merge_requested',metadata:{character_uid:original.characterSyncId,
            message_id:id,local_message_id:'sync-original-'+id}}]);
        assert.equal(JSON.stringify(observed).includes('same'),false);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("BROKEN", json.dumps(broken_observer)))


@pytest.mark.parametrize("busy", ["none", "turn", "speech", "queue", "unknown", "hidden"])
def test_committed_history_hint_refreshes_now_or_after_real_idle(busy):
    """A committed local hint uses passive reads; busy/hidden states retain it for retry."""
    run_script(r"""(async()=>{
        const requests=[], merged=[];
        window.reactChatWindowHost={getState:()=>({messages:BUSY==='unknown'?[{id:'live',role:'assistant',status:'streaming'}]:[]}),
            setMessages:messages=>merged.push(messages)};
        window.appState=BUSY==='turn'||BUSY==='speech'?{assistantTurnId:'turn',assistantTurnAwaitingBubble:true}:{};
        window._isProcessingRealisticQueue=BUSY==='queue';
        document.visibilityState=BUSY==='hidden'?'hidden':'visible';
        global.fetch=async(path)=>{requests.push(path);return{ok:true,json:async()=>path.endsWith('/status')
            ?{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor:1}}]}
            :{originals:[original]}};};
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:original.characterSyncId});
        if(BUSY==='none'){assert.equal(merged.length,1);assert.equal(requests.length,2);return;}
        assert.equal(merged.length,0);assert.equal(requests.length,0);
        if(BUSY==='turn'||BUSY==='speech'){
            const eventName=BUSY==='turn'?'neko-assistant-turn-end':'neko-assistant-speech-end';
            windowEvents[eventName]({detail:{turnId:'turn'}});
            window.appState.assistantTurnAwaitingBubble=false;
            window.appState[BUSY==='turn'?'assistantTurnCompletedId':'assistantTurnSettledId']='turn';
        }else if(BUSY==='queue'){
            window._isProcessingRealisticQueue=false;
            windowEvents['neko-realistic-queue-idle']({});
        }else if(BUSY==='hidden'){
            document.visibilityState='visible';await documentEvents.visibilitychange();
        }else{
            windowEvents['neko-assistant-turn-end']({detail:{turnId:'other-turn'}});
        }
        for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.equal(merged.length,BUSY==='unknown'?0:1);
        assert.ok(requests.every(path=>!path.endsWith('/sync')));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("BUSY", json.dumps(busy)))


def test_local_history_hints_reject_foreign_uid_and_coalesce_without_losing_late_commit():
    """A second commit during a local read gets one follow-up, without polling or cloud work."""
    run_script(r"""(async()=>{
        let cursor=1, release, waiting=false;
        const requests=[], merged=[];
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages:messages=>merged.push(messages)};
        global.fetch=async(path)=>{requests.push(path);return{ok:true,json:async()=>{
            if(path.endsWith('/status'))return{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor}}]};
            if(!waiting){waiting=true;await new Promise(resolve=>{release=resolve;});}return{originals:[original]};
        }};};
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:other});
        assert.equal(merged.length,0);
        requests.length=0;
        const first=window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:original.characterSyncId});
        for(let i=0;i<10&&!release;i++)await new Promise(resolve=>setImmediate(resolve));
        cursor=2;
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:original.characterSyncId});
        release();await first;
        assert.equal(merged.length,2);
        assert.equal(requests.filter(path=>path.endsWith('/history')).length,2);
        assert.ok(requests.every(path=>path.endsWith('/history')||path.endsWith('/status')));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_foreign_hint_cannot_replace_a_current_role_hint_deferred_by_busy():
    """An old role's socket hint cannot strand the active role until fallback polling."""
    run_script(r"""(async()=>{
        let cursor=0;const merged=[];
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages:messages=>merged.push(messages)};
        global.fetch=async(path)=>({ok:true,json:async()=>path.endsWith('/status')
            ?{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor}}]}
            :{originals:[original]}});
        await window.nekoChatSync.loadHistory();merged.length=0;cursor=1;
        window.appState={assistantTurnId:'turn',assistantTurnAwaitingBubble:true};
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:original.characterSyncId});
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:other});
        windowEvents['neko-assistant-turn-end']({detail:{turnId:'turn'}});
        window.appState.assistantTurnAwaitingBubble=false;window.appState.assistantTurnCompletedId='turn';
        for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.equal(merged.length,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_hint_hidden_during_history_io_keeps_cursor_for_foreground_retry():
    """A hidden window must not consume a committed cursor while its read is in flight."""
    run_script(r"""(async()=>{
        let hide=true;const merged=[];
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages:messages=>merged.push(messages)};
        global.fetch=async(path)=>({ok:true,json:async()=>{
            if(path.endsWith('/status'))return{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor:1}}]};
            if(path.endsWith('/history')){if(hide)document.visibilityState='hidden';return{originals:[original]};}
            return{};
        }});
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:original.characterSyncId});
        assert.equal(merged.length,0);
        hide=false;document.visibilityState='visible';await documentEvents.visibilitychange();
        assert.equal(merged.length,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("script", ["app-chat-adapter.js", "app-chat.js"])
def test_realistic_queue_idle_event_only_after_current_owner_finishes(script):
    """Execute the actual queue function; a replaced owner cannot announce idle."""
    from tests.unit.test_app_websocket_static_routes import _block_after

    source = (ROOT / "static/app" / script).read_text(encoding="utf-8")
    body = _block_after(source, "async function processRealisticQueue(queueVersion) {")
    code = r"""
        global.CustomEvent=function(type){this.type=type;};
        const events=[];window.dispatchEvent=event=>events.push(event.type);
        window._realisticGeminiQueue=[];
        function createRealisticQueueOwnerToken(){return 'owner';}
        function getRealisticQueueItemText(item){return item;}
        function getRealisticQueueItemTurnId(){return 'turn';}
        const chatContainer={parentElement:null}, S={dom:{chatContainer}};
        function createGeminiBubble(){window._realisticProcessingOwner='replacement';}
        async function processRealisticQueue(queueVersion){BODY}
        (async()=>{
            await processRealisticQueue(1);
            assert.deepEqual(events,['neko-realistic-queue-idle']);
            events.length=0;window._realisticGeminiVersion=1;window._realisticGeminiQueue=['synthetic'];
            await processRealisticQueue(1);
            assert.equal(window._realisticProcessingOwner,'replacement');
            assert.deepEqual(events,[]);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """
    run_script(code.replace("BODY", body))


def test_current_local_socket_dispatches_history_hint_and_stale_socket_is_ignored():
    """Execute the actual onmessage handler, preserving its socket replacement fence."""
    from tests.unit.test_app_websocket_static_routes import _block_after

    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    body = _block_after(source, "S.socket.onmessage = function (event) {")
    run_script(r"""
        const calls=[], socket={}, S={socket}, _thisSocket=socket;
        window.nekoChatSync.notifyHistoryCommitted=payload=>{calls.push(payload);return Promise.resolve();};
        function onmessage(event){BODY}
        const hint={type:'chat_sync_history_changed',characterSyncId:original.characterSyncId};
        onmessage({data:JSON.stringify(hint)});
        assert.deepEqual(calls,[hint]);
        S.socket={};onmessage({data:JSON.stringify(hint)});
        assert.deepEqual(calls,[hint]);
    """.replace("BODY", body))


def test_pc_pet_ipc_bridge_delivers_committed_hint_to_full_chat_passive_refresh():
    """Use existing PC bridges/router, including both chat targets and stale-host filtering."""
    from tests.unit.test_app_websocket_static_routes import _block_after

    pc = ROOT.parent / "N.E.K.O.-PC"
    harness = pc / "test/preload-bridge-lifecycle.test.js"
    if not harness.is_file():
        pytest.skip("optional NEKOPC checkout is unavailable")
    source = harness.read_text(encoding="utf-8")
    helpers = "\n".join(
        opener + _block_after(source, opener) + "}"
        for opener in ("function createWindowHarness() {", "function createPetBridgeHarness() {")
    )
    callback = _block_after((ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8"), "S.socket.onmessage = function (event) {")
    run_script(r"""
        const {EventEmitter}=require('node:events'), Module=require('node:module'), pc=PC_ROOT;
        const {createPreloadLifecycle}=require(pc+'/src/preload/shared/lifecycle');
        const {installPetWebSocketBridge}=require(pc+'/src/preload/bridges/pet-websocket-bridge');
        const {installChatWebSocketBridge}=require(pc+'/src/preload/bridges/chat-websocket-bridge');
        const channels=require(pc+'/src/ipc-channels'), ipcMain=new EventEmitter();
        const originalLoad=Module._load;
        let router;
        try{
            Module._load=function(name){return name==='electron'?{ipcMain}:originalLoad.apply(this,arguments);};
            router=require(pc+'/src/ipc-router');
        }finally{Module._load=originalLoad;}
        HELPERS
        global.CloseEvent=class CloseEvent extends Event{constructor(type,options){super(type);Object.assign(this,options);}};
        const pet=createPetBridgeHarness(), clients=[];
        function client(){
            const page=createWindowHarness(), ipc=new EventEmitter();ipc.send=()=>{};page.WebSocket=pet.RealWebSocket;
            const lifecycle=createPreloadLifecycle(page);
            installChatWebSocketBridge({lifecycle,window:page,ipcRenderer:ipc,WS_PROXY_CHANNELS:channels.WS_PROXY_CHANNELS});
            const proxy=new page.WebSocket('ws://synthetic.test');
            const webContents=new EventEmitter();webContents.isDestroyed=()=>false;
            webContents.send=(name,data)=>ipc.emit(name,{},data);
            const native={isDestroyed:()=>false,isVisible:()=>true,webContents};
            clients.push({lifecycle,proxy});return{proxy,native};
        }
        const compact=client(), full=client(), petContents=new EventEmitter();petContents.isDestroyed=()=>false;
        const windows={pet:{isDestroyed:()=>false,webContents:petContents},chat:compact.native,fullChat:full.native};
        router.setupIPCRouter({getWindows:()=>windows,log:()=>{}});
        pet.ipcRenderer.send=(name,data)=>ipcMain.emit(name,{sender:petContents},data);
        installPetWebSocketBridge({lifecycle:pet.lifecycle,window:pet.window,ipcRenderer:pet.ipcRenderer,channels});
        const S={socket:full.proxy}, _thisSocket=full.proxy, mirrored=[], merged=[];
        full.proxy.onmessage=function(event){CALLBACK};
        compact.proxy.onmessage=event=>mirrored.push(JSON.parse(event.data));
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages:messages=>merged.push(messages)};
        global.fetch=async(path)=>({ok:true,json:async()=>path.endsWith('/status')
            ?{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor:1}}]}
            :{originals:[original]}});
        (async()=>{
            const socket=new pet.window.WebSocket('ws://synthetic.test');
            socket.readyState=pet.RealWebSocket.OPEN;socket.emit('open');
            const hint={type:'chat_sync_history_changed',characterSyncId:original.characterSyncId};
            socket.emit('message',{data:JSON.stringify(hint)});
            for(let i=0;i<20;i++)await new Promise(resolve=>setImmediate(resolve));
            assert.deepEqual(mirrored,[hint]);assert.equal(merged.length,1);
            new pet.window.WebSocket('ws://synthetic.test');
            socket.emit('message',{data:JSON.stringify(hint)});
            assert.equal(mirrored.length,1);
            full.proxy.close();pet.lifecycle.dispose();clients.forEach(item=>item.lifecycle.dispose());
            assert.equal(full.proxy.readyState,pet.RealWebSocket.CLOSED);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("PC_ROOT", json.dumps(str(pc))).replace("HELPERS", helpers).replace("CALLBACK", callback))


def test_history_consent_displays_fixed_names_and_count_then_cancels_without_approval():
    """Previewing never uploads, and cancel discards the reviewed task."""
    run_script("(async()=>{" + HISTORY_CONSENT_SETUP + r"""
        await action('history').events.click();
        assert.ok(content().includes('chatSync.historyLocalRole:Neko'));
        assert.ok(content().includes('chatSync.historyCloudRole:Cloud Neko'));
        assert.ok(content().includes('chatSync.historyCount:3'));
        assert.ok(content().includes('chatSync.historyUnverifiedUnknown'));
        await choice('cancel').events.click();
        assert.equal(choice('approve'),undefined);
        assert.equal(requests.filter(item=>item.path.endsWith('/history/approve')).length,0);
        assert.equal(requests.filter(item=>item.path.endsWith('/sync')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_history_consent_approves_only_the_reviewed_task_as_background_work():
    """Approval cannot rescan a new batch or claim that all chats were delivered."""
    run_script("(async()=>{" + HISTORY_CONSENT_SETUP + r"""
        await action('history').events.click();
        snapshot={...snapshot,taskId:'e'.repeat(32),count:20};
        await choice('approve').events.click();
        const approvals=requests.filter(item=>item.path.endsWith('/history/approve'));
        assert.equal(approvals.length,1);
        assert.deepEqual(JSON.parse(approvals[0].body),{taskId:task});
        assert.equal(requests.filter(item=>item.path.endsWith('/history/preview')).length,1);
        assert.equal(requests.filter(item=>item.path.endsWith('/sync')).length,0);
        assert.ok(content().includes('chatSync.historyQueued'));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("name", ["Neko", "Other"])
def test_enable_and_resume_share_one_durable_history_and_profile_choice(paused, name):
    """Opening does not grant anything; the labeled action approves all eligible chat once."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32), requests=[];
        let binding=PAUSED?{enabled:false,ownerId:'owner',bindingId:'binding',generation:2}:null;
        global.fetch=async(path,options)=>({ok:true,async json(){
            requests.push({path,body:options.body});
            if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[{displayName:REQUESTED_NAME,characterSyncId:uid,binding}]};
            if(path.endsWith('/sync-choice'))return {pending:true};
            if(path.endsWith('/sync-choice/run')){binding={enabled:true};return {completed:true,pending:false};}
            if(path.endsWith('/history'))return {originals:[]};
            throw new Error('Unexpected request '+path);
        }});
        documentEvents.DOMContentLoaded();await window.nekoChatSync.openSettings(REQUESTED_NAME);
        const modal=body.children[0];
        assert.equal(modal.querySelectorAll('*').some(n=>n.id==='chat-sync-include-history'),false);
        assert.equal(requests.filter(x=>x.path.endsWith('/sync-choice')).length,0);
        await modal.querySelector('[data-sync-action="enable"]').events.click();
        const choices=requests.filter(x=>x.path.endsWith('/sync-choice'));
        assert.equal(choices.length,1);
        assert.deepEqual(JSON.parse(choices[0].body),{enabled:true,includeHistory:true});
        const runs=requests.filter(x=>x.path.endsWith('/sync-choice/run'));
        assert.equal(runs.length,1);
        assert.ok(requests.indexOf(choices[0])<requests.indexOf(runs[0]));
        assert.equal(requests.filter(x=>x.path.endsWith('/history/approve')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("PAUSED", json.dumps(paused)).replace("REQUESTED_NAME", json.dumps(name)))


@pytest.mark.parametrize('error', ['chat_sync_login_required', 'chat_sync_unavailable'])
def test_manual_enable_retains_consent_and_reports_the_real_attempt_error(error):
    """Persisted consent survives a failed manual pass without hiding a login error."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[];
        global.fetch=async(path,options)=>{
            requests.push(path);
            return {ok:!path.endsWith('/sync-choice/run'),async json(){
                if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[{
                    displayName:'Other',characterSyncId:uid,binding:null,syncPending:true}]};
                if(path.endsWith('/sync-choice'))return {pending:true};
                if(path.endsWith('/sync-choice/run'))return {detail:ERROR};
                throw new Error('Unexpected request '+path);
            }};
        };
        await window.nekoChatSync.openSettings('Other');
        const modal=body.children[0];
        await modal.querySelector('[data-sync-action="enable"]').events.click();
        assert.equal(requests.filter(path=>path.endsWith('/sync-choice')).length,1);
        assert.equal(requests.filter(path=>path.endsWith('/sync-choice/run')).length,1);
        const content=modal.querySelectorAll('*').map(node=>node.textContent||'').join('\n');
        assert.ok(content.includes(ERROR==='chat_sync_login_required'?'chatSync.loginRequired':'chatSync.initialHistoryFailed'));
        assert.equal(requests.filter(path=>path.endsWith('/pause')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('ERROR',json.dumps(error)))


def test_initial_default_origin_does_not_cancel_the_first_enable_click():
    """An absent setting and the built-in origin are the same service, not a context switch."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[];let reads=0;
        global.fetch=async(path)=>({ok:true,async json(){
            requests.push(path);
            if(path.endsWith('/status'))return {serviceUrl:++reads===1?null:'https://sync.nekotech.cn',
                characters:[{displayName:'Neko',characterSyncId:uid,binding:null}]};
            if(path.endsWith('/sync-choice'))return {pending:true};
            if(path.endsWith('/sync-choice/run'))return {completed:true,pending:false};
            if(path.endsWith('/history'))return {originals:[]};
            throw new Error('Unexpected request '+path);
        }});
        await window.nekoChatSync.openSettings();
        await body.children[0].querySelector('[data-sync-action="enable"]').events.click();
        assert.equal(requests.filter(path=>path.endsWith('/sync-choice')).length,1);
        assert.equal(requests.filter(path=>path.endsWith('/sync-choice/run')).length,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_empty_history_consumes_its_cursor_and_only_reloads_after_it_changes():
    """Successful empty restoration must not refetch the same history every local poll."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[];let cursor=1;
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages(){throw new Error('Empty merge');}};
        global.fetch=async(path)=>({ok:true,async json(){
            requests.push(path);
            return path.endsWith('/status')?{characters:[{displayName:'Neko',characterSyncId:uid,binding:{cursor}}]}:
                {originals:[],localReplies:[]};
        }});
        documentEvents.DOMContentLoaded();
        const tick=intervals.find(item=>item.delay===5000).callback;
        await tick();await tick();
        assert.equal(requests.filter(path=>path.endsWith('/history')).length,1);
        cursor=2;await tick();
        assert.equal(requests.filter(path=>path.endsWith('/history')).length,2);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("change", ["role", "closed", "binding", "service"])
def test_combined_enable_rejects_context_changed_before_consent(change):
    """A stale UI response cannot authorize another role, grant or origin."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32), requests=[];let binding=null,service='https://sync.test',reads=0;
        global.fetch=async(path,options)=>({ok:true,async json(){
            requests.push(path);
            if(path.endsWith('/status')){
                if(++reads===2){
                    if(CHANGE==='role')window.lanlan_config.lanlan_name='Other';
                    if(CHANGE==='closed')modal.children.find(n=>n.textContent==='chatSync.close').events.click();
                    if(CHANGE==='binding')binding={enabled:true,ownerId:'owner',bindingId:'other',generation:2};
                    if(CHANGE==='service')service='https://other.test';
                }
                return {serviceUrl:service,characters:[{displayName:'Neko',characterSyncId:uid,binding}]};
            }
            if(path.endsWith('/history'))return {originals:[]};
            throw new Error('Unexpected mutation '+path);
        }});
        documentEvents.DOMContentLoaded();await headerButton.events.click();
        const modal=body.children[0];
        await modal.querySelector('[data-sync-action="enable"]').events.click();
        assert.equal(requests.filter(p=>p.endsWith('/sync-choice')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("CHANGE", json.dumps(change)))


@pytest.mark.parametrize("change", ["role", "generation", "paused", "service", "closed"])
def test_history_consent_rejects_a_changed_context_before_approval(change):
    """The fixed task cannot follow a role, binding, service or dialog change."""
    run_script("(async()=>{" + HISTORY_CONSENT_SETUP + r"""
        await action('history').events.click();
        const approve=choice('approve'), change=CHANGE;
        if(change==='role')window.lanlan_config.lanlan_name='Other';
        if(change==='generation')binding={...binding,generation:2};
        if(change==='paused')binding={...binding,enabled:false};
        if(change==='service')binding={...binding,bindingId:'different'};
        if(change==='closed'){
            modal.children.find(node=>node.textContent==='chatSync.close').events.click();
        }
        await approve.events.click();
        assert.equal(requests.filter(item=>item.path.endsWith('/history/approve')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("CHANGE", json.dumps(change)))


def test_history_consent_empty_snapshot_explains_unverified_local_records():
    """Unknown legacy counts never appear as zero verified or complete backup."""
    run_script("(async()=>{" + HISTORY_CONSENT_SETUP + r"""
        snapshot={...snapshot,count:0};
        await action('history').events.click();
        assert.ok(content().includes('chatSync.historyEmpty'));
        assert.ok(content().includes('chatSync.historyUnverifiedUnknown'));
        assert.equal(choice('approve'),undefined);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("latest_status", ["approved", "preview"])
def test_history_consent_status_keeps_unregistered_members_visible(latest_status):
    """A newer preview cannot hide the remainder of an already approved snapshot."""
    run_script("(async()=>{" + HISTORY_CONSENT_SETUP + r"""
        binding={...binding,pendingOriginals:200,historyBackfill:{...snapshot,
            status:LATEST_STATUS,count:503,registeredCount:200,cancelledCount:2,awaitingRegistration:301}};
        await headerButton.events.click();
        assert.ok(content().includes('chatSync.historyAwaitingRegistration:301'));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("LATEST_STATUS", json.dumps(latest_status)))


def test_history_consent_blocks_duplicate_requests_and_late_closed_dialog_preview():
    """A slow preview disables submissions and cannot recreate dismissed consent."""
    run_script("(async()=>{" + HISTORY_CONSENT_SETUP + r"""
        const previous=global.fetch;
        let release,entered;
        const waiting=new Promise(resolve=>entered=resolve), delayed=new Promise(resolve=>release=resolve);
        global.fetch=async(path,options)=>{
            if(path.endsWith('/history/preview')){entered();await delayed;}
            return previous(path,options);
        };
        const preparing=action('history').events.click();
        await waiting;
        assert.equal(action('history').disabled,true);
        await action('history').events.click();
        modal.children.find(node=>node.textContent==='chatSync.close').events.click();
        release();
        await preparing;
        assert.equal(choice('approve'),undefined);
        assert.equal(requests.filter(item=>item.path.endsWith('/history/preview')).length,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_same_text_distinct_ids_survive_and_exact_live_identity_is_replaced():
    """Only an explicit backend source ID can replace an optimistic bubble."""
    run_script(r"""
        const current = [{id:'optimistic',role:'user',blocks:[{type:'text',text:'same'}],status:'sent'}];
        const originals = [original, {...original,messageId:other,originSeq:2}];
        const merged = window.nekoChatSync.mergeHistory(current, originals, new Set(), new Map([['optimistic',id]]), 'Neko');
        assert.deepEqual(merged.map(x=>x.id), ['optimistic','sync-original-'+other]);
        assert.deepEqual(merged.map(x=>x.blocks[0].text), ['same','same']);
        assert.equal(merged[0].actions,undefined);
    """)


def test_memory_browser_save_receipt_hides_exact_chat_without_bubble_actions():
    """The saved receipt fences old pages and never deletes equal-text siblings."""
    run_script(r"""(async()=>{
        window.location={origin:'http://localhost'};
        let messages=[];
        window.reactChatWindowHost={getState:()=>({messages}),setMessages:next=>{messages=next;}};
        global.fetch=async(path)=>({ok:true,json:async()=>path.endsWith('/status')
            ? {characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{enabled:true}}]}
            : {originals:[original,{...original,messageId:other,originSeq:2}]}});
        await window.nekoChatSync.loadHistory();
        const data={action:'chat_sync_deleted',characterSyncId:original.characterSyncId,
            receipt:{messageIds:[id],localMessageIds:[]}};
        windowEvents.message({origin:'https://foreign.invalid',data});
        assert.equal(messages.length,2);
        global.fetch=async()=>{throw new Error('synthetic history outage');};
        windowEvents.message({origin:window.location.origin,data});
        assert.deepEqual(messages.map(item=>item.id),['sync-original-'+other]);
        assert.equal(messages[0].actions,undefined);
        assert.equal(windowEvents['react-chat-window:action'],undefined);
    })().catch(error=>{console.error(error);process.exitCode=1;});""")


def test_retired_delete_actions_are_removed_without_affecting_other_actions():
    run_script(r"""
        const keep={action:'unrelated',label:'keep'};
        const current=[{id:'live',role:'user',actions:[{action:'chat_sync_delete_original'},keep]}];
        const merged=window.nekoChatSync.mergeHistory(current,[original],new Set(),new Map([['live',id]]),'Neko');
        assert.deepEqual(merged[0].actions,[keep]);
    """)


def test_streaming_and_unsaved_live_messages_survive_download():
    """A displayed source cannot disappear before its final original was stored."""
    run_script(r"""
        const current = [{id:'streaming',role:'assistant',status:'streaming',turnId:'turn-one'},
            {id:'unsaved',role:'user',status:'sending'}];
        const merged = window.nekoChatSync.mergeHistory(current,[original],new Set(),new Map([['unsaved',other]]),'Neko',
            new Map([['turn-one',other]]));
        assert.deepEqual(merged.map(x=>x.id),['sync-original-'+id,'streaming','unsaved']);
        assert.equal(merged[1].status,'streaming');
    """)


def test_local_only_reply_stays_between_older_and_newer_originals():
    """A retained local reply keeps its known time after every passive refresh."""
    run_script(r"""
        const time=Number(original.originalTime);
        const local={id:'local-only',role:'assistant',createdAt:time+1000,status:'sent',
            turnId:'local-turn',blocks:[{type:'text',text:'same'}]};
        const originals=[{...original,messageId:other,originalTime:String(time+2000),originSeq:2},original];
        const current=[local];
        const merged=window.nekoChatSync.mergeHistory(current,originals,new Set(),new Map(),'Neko');
        assert.deepEqual(merged.map(item=>item.id),['sync-original-'+id,'local-only','sync-original-'+other]);
        assert.equal(merged[1].actions,undefined);
        assert.equal(merged[1].turnId,'local-turn');
        assert.deepEqual(merged.map(item=>item.sortKey),[0,1,2]);
        const refreshed=window.nekoChatSync.mergeHistory(merged,originals,new Set([id,other]),new Map(),'Neko');
        assert.deepEqual(refreshed,merged);
        assert.equal(local.sortKey,undefined);
        assert.deepEqual(current,[local]);
    """)


def test_retained_reply_order_preserves_ties_and_unknown_times():
    """Known source times interleave; missing live times are never guessed."""
    run_script(r"""
        const time=Number(original.originalTime);
        const current=[{id:'first-sentence',role:'assistant',createdAt:time,status:'streaming'},
            {id:'second-sentence',role:'assistant',createdAt:time,status:'streaming'},
            {id:'unknown',role:'user',status:'sending'}];
        const originals=[original,{...original,messageId:other,originSeq:2,originalTime:String(time+2000)}];
        const merged=window.nekoChatSync.mergeHistory(current,originals,new Set(),new Map(),'Neko');
        assert.deepEqual(merged.map(item=>item.id),['sync-original-'+id,'first-sentence','second-sentence',
            'sync-original-'+other,'unknown']);
        assert.equal(merged[1].status,'streaming');
        assert.equal(merged[4].status,'sending');
        assert.equal(merged[4].createdAt,undefined);
    """)


@pytest.mark.parametrize("bound", [False, True])
def test_fresh_window_restores_local_only_replies_without_sync_actions(bound):
    """New windows restore explicit local bytes even before cloud consent exists."""
    run_script(r"""(async()=>{
        const merged=[],requests=[];
        window.appendMessage=()=>{throw new Error('restoration submitted a reply');};
        window.sendTextPayload=()=>{throw new Error('restoration submitted input');};
        global.WebSocket=()=>{throw new Error('restoration opened a model socket');};
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages:items=>merged.push(items)};
        const local={messageId:other,characterSyncId:original.characterSyncId,role:'assistant',text:'local only',
            originalTime:String(Number(original.originalTime)+1000)};
        global.fetch=async(path,options)=>{
            requests.push([path,options.method]);
            return {ok:true,json:async()=>({originals:BOUND?[original]:[],localReplies:[local]})};
        };
        const role={displayName:'Neko',characterSyncId:original.characterSyncId,binding:BOUND?{cursor:1}:null};
        await window.nekoChatSync.loadHistory({characters:[role]});
        assert.equal(merged.length,1);
        const restored=merged[0].find(item=>item.id==='local-reply-'+other);
        assert.equal(restored.blocks[0].text,'local only');
        assert.equal(restored.createdAt,Number(local.originalTime));
        assert.equal(restored.actions,undefined);
        assert.deepEqual(requests,[['/api/chat-sync/characters/'+original.characterSyncId+'/history','GET']]);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("BOUND", json.dumps(bound)))


def test_local_only_identity_replaces_exact_split_bubbles_without_text_deduplication():
    """Local restoration joins the same turn; equal text in another turn survives."""
    run_script(r"""
        const time=Number(original.originalTime),local={messageId:other,characterSyncId:original.characterSyncId,
            role:'assistant',text:'same',originalTime:String(time+1000)};
        const current=[{id:'sentence-one',role:'assistant',turnId:'local-turn',createdAt:time+1000},
            {id:'sentence-two',role:'assistant',turnId:'local-turn',createdAt:time+1000},
            {id:'different-turn',role:'assistant',turnId:'other-turn',createdAt:time+2000,blocks:[{type:'text',text:'same'}]}];
        const merge=items=>window.nekoChatSync.mergeHistory(items,[original],new Set(),new Map(),'Neko',
            new Map([['local-turn',other]]),[local]);
        const merged=merge(current);
        assert.deepEqual(merged.map(item=>item.id),['sync-original-'+id,'sentence-one','sentence-two','different-turn']);
        assert.equal(merged[1].actions,undefined);
        assert.deepEqual(merge(merged),merged);
    """)


def test_deleted_known_sources_do_not_reappear_from_live_bubbles():
    """An omitted previously known ID is removed together with its exact live alias."""
    run_script(r"""
        const current = [{id:'sync-original-'+id,role:'user'}, {id:'local-copy',role:'user'}, {id:'untouched',role:'assistant'}];
        const merged = window.nekoChatSync.mergeHistory(current,[],new Set([id]),new Map([['local-copy',id]]),'Neko');
        assert.deepEqual(merged.map(x=>x.id),['untouched']);
    """)


def test_assistant_turn_identity_handles_delayed_realistic_rendering():
    """Delayed split bubbles use an explicit turn map rather than text or position."""
    run_script(r"""
        const current = [{id:'sentence-1',role:'assistant',turnId:'turn-one'}, {id:'sentence-2',role:'assistant',turnId:'turn-one'}];
        const merged = window.nekoChatSync.mergeHistory(current,[{...original,role:'assistant'}],new Set(),new Map(),'Neko',
            new Map([['turn-one',id]]));
        assert.deepEqual(merged.map(x=>x.id),['sentence-1','sentence-2']);
        assert.equal(merged[0].actions,undefined);
        assert.equal(merged[1].actions,undefined);
    """)


def test_sync_keeps_live_split_bubble_keys_and_blocks_without_replaying_entry():
    """Canonical identity adds metadata; it must not remount already visible text."""
    run_script(r"""
        const first=[{type:'text',text:'First.'}],second=[{type:'text',text:'Second.'}];
        const current=[{id:'live-1',role:'assistant',turnId:'turn',blocks:first},
            {id:'live-2',role:'assistant',turnId:'turn',blocks:second}];
        const mappings=new Map([['turn',id]]),record={...original,role:'assistant',text:'First. Second.'};
        const merge=items=>window.nekoChatSync.mergeHistory(items,[record],new Set([id]),new Map(),'Neko',mappings);
        const merged=merge(current);
        assert.deepEqual(merged.map(x=>x.id),['live-1','live-2']);
        assert.equal(merged[0].blocks,first);assert.equal(merged[1].blocks,second);
        assert.deepEqual(merge(merged),merged);
        assert.deepEqual(window.nekoChatSync.mergeHistory(merged,[],new Set([id]),new Map(),'Neko',mappings),[]);
    """)


def test_unknown_original_time_never_becomes_download_time():
    """Unknown source time remains unknown in the current-window message view."""
    run_script(r"""
        const merged = window.nekoChatSync.mergeHistory([],[{...original,originalTime:null}],new Set(),new Map(),'Neko');
        assert.equal(merged[0].createdAt,0);
        assert.equal(merged[0].time,'chatSync.unknownTime');
    """)


def test_history_uses_passive_host_set_without_append_or_models():
    """A local original page restores without generating reactions or re-uploading."""
    run_script(r"""
        (async()=>{
            let calls=[];
            window.reactChatWindowHost={getState(){return {messages:[{id:'live',role:'user',status:'sending'}]};},
                setMessages(messages){calls.push(messages);}, appendMessage(){throw new Error('Active append used');}};
            global.fetch=async(path)=>({ok:true,async json(){
                if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{enabled:true}}]};
                assert.equal(path,'/api/chat-sync/characters/'+'c'.repeat(32)+'/history');
                // A late historical page can arrive after its same-millisecond successor.
                return {originals:[{...original,messageId:other,originSeq:2},original]};
            }});
            await window.nekoChatSync.loadHistory();
            assert.equal(calls.length,1);
            assert.deepEqual(calls[0].map(x=>x.id),['sync-original-'+id,'sync-original-'+other,'live']);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_automatic_local_cursor_refresh_is_passive_and_skips_unchanged_history():
    """The cloud worker's committed cursor reaches the current idle chat window."""
    run_script(r"""
        (async()=>{
            let messages=[], cursor=1, historyCalls=0;
            window.reactChatWindowHost={getState(){return {messages};},setMessages(next){messages=next;},
                appendMessage(){throw new Error('Automatic refresh triggered reaction');}};
            global.fetch=async(path)=>({ok:true,async json(){
                assert.ok(path.startsWith('/api/chat-sync/'),'Browser must only call local backend');
                if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{cursor}}]};
                historyCalls++;
                return {originals:cursor===1?[original]:[original,{...original,messageId:other,originSeq:2}]};
            }});
            documentEvents.DOMContentLoaded();
            assert.equal(intervals[0].delay,5000);
            await intervals[0].callback();
            assert.equal(messages.length,1);
            await intervals[0].callback();
            assert.equal(historyCalls,1);
            cursor=2;
            await intervals[0].callback();
            assert.deepEqual(messages.map(item=>item.id),['sync-original-'+id,'sync-original-'+other]);
            assert.equal(historyCalls,2);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("completion", ["assistantTurnCompletedId", "assistantTurnSettledId"])
@pytest.mark.parametrize("busy", [None, "streaming", "sending", "realistic", "awaiting", "submitted", "pending", "hidden"])
def test_completed_retained_turn_refreshes_only_after_real_busy_state_finishes(completion, busy):
    """A completed ID remains valid while real streaming/queued work still fences UI."""
    run_script("(async()=>{" + r"""
        let applied=0, requests=0;
        const completion=COMPLETION, busy=BUSY;
        window._nekoAssistantTurnId='retained';
        window.appState={assistantTurnId:'retained',[completion]:'retained'};
        window.reactChatWindowHost={getState(){return {messages:busy==='streaming'||busy==='sending'
            ?[{id:'live',status:busy}]:[]};},setMessages(){applied++;},appendMessage(){throw new Error('reaction');}};
        if(busy==='realistic')window._isProcessingRealisticQueue=true;
        if(busy==='awaiting')window.appState.assistantTurnAwaitingBubble=true;
        if(busy==='submitted')window._lastSubmittedRequestId='submitted';
        if(busy==='pending')window.appState.pendingTextTurnSubmitAt=Date.now();
        if(busy==='hidden')document.visibilityState='hidden';
        global.fetch=async(path)=>({ok:true,async json(){
            requests++;
            if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{cursor:1}}]};
            assert.ok(path.endsWith('/history'));return {originals:[original]};
        }});
        documentEvents.DOMContentLoaded();
        await intervals[0].callback();
        assert.equal(applied,busy?0:1);
        assert.equal(requests,busy?0:2);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("COMPLETION", json.dumps(completion)).replace("BUSY", json.dumps(busy)))


@pytest.mark.parametrize("busy", [None, "realistic", "active", "unknown", "unidentified", "sending"])
def test_completed_turn_tail_bubbles_refresh_after_the_render_queue_drains(busy):
    """Exact end events release old tail bubbles without releasing genuine work."""
    run_script("(async()=>{" + r"""
        const busy=BUSY;
        let applied=0,requests=0;
        const messages=['previous','latest'].map(turnId=>({id:turnId,role:'assistant',turnId,status:'streaming'}));
        window.appState={assistantTurnId:'latest',assistantTurnCompletedId:'latest'};
        window.reactChatWindowHost={getState(){return {messages};},setMessages(){applied++;}};
        windowEvents['neko-assistant-turn-end']?.({detail:{turnId:'previous',source:'turn_end'}});
        windowEvents['neko-assistant-turn-end']?.({detail:{turnId:'latest',source:'turn_end'}});
        if(busy==='realistic')window._isProcessingRealisticQueue=true;
        if(busy==='active')window.appState.assistantTurnId='new-active';
        if(busy==='unknown')messages.push({id:'unknown',role:'assistant',turnId:'unconfirmed',status:'streaming'});
        if(busy==='unidentified')messages.push({id:'unidentified',role:'assistant',status:'streaming'});
        if(busy==='sending')messages.push({id:'pending',role:'user',turnId:'latest',status:'sending'});
        global.fetch=async(path)=>({ok:true,async json(){
            requests++;
            if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{cursor:1}}]};
            return {originals:[original]};
        }});
        documentEvents.DOMContentLoaded();
        await intervals[0].callback();
        assert.equal(applied,busy?0:1);
        assert.equal(requests,busy?0:2);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("BUSY", json.dumps(busy)))


@pytest.mark.parametrize("completion", ["assistantTurnCompletedId", "assistantTurnSettledId"])
def test_existing_completion_releases_only_its_exact_assistant_tail(completion):
    """A page can observe completion state even if it missed the end event."""
    run_script("(async()=>{" + r"""
        let applied=0;
        window.appState={assistantTurnId:'retained',[COMPLETION]:'retained'};
        window.reactChatWindowHost={getState(){return {messages:[
            {id:'tail',role:'assistant',turnId:'retained',status:'streaming'}]};},setMessages(){applied++;}};
        global.fetch=async(path)=>({ok:true,async json(){
            if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{cursor:1}}]};
            return {originals:[original]};
        }});
        documentEvents.DOMContentLoaded();
        await intervals[0].callback();
        assert.equal(applied,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("COMPLETION", json.dumps(completion)))


@pytest.mark.parametrize("lifecycle", ["explicit_null", "legacy_missing", "current_active", "unknown_streaming"])
def test_current_lifecycle_distinguishes_cleared_role_from_legacy_fallback(lifecycle):
    """Cleared current state ignores an old role ID while real and legacy work waits."""
    run_script(r"""(async()=>{
        const lifecycle=LIFECYCLE, requests=[], merged=[];
        const messages=lifecycle==='unknown_streaming'
            ?[{id:'unknown-live',role:'assistant',turnId:'unknown-current',status:'streaming'}]:[];
        window._nekoAssistantTurnId='old-role-turn';
        window.appState={assistantTurnId:null,assistantTurnCompletedId:null,assistantTurnSettledId:null,
            assistantTurnAwaitingBubble:false,pendingTextTurnSubmitAt:0};
        if(lifecycle==='legacy_missing')delete window.appState.assistantTurnId;
        if(lifecycle==='current_active')window.appState.assistantTurnId='current-role-turn';
        window.reactChatWindowHost={getState:()=>({messages}),setMessages:items=>merged.push(items),
            appendMessage(){throw new Error('Passive restoration must not enqueue a reaction');}};
        document.visibilityState='visible';
        global.fetch=async(path)=>{requests.push(path);return {ok:true,json:async()=>path.endsWith('/status')
            ?{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor:1}}]}
            :{originals:[original]}};};
        documentEvents.DOMContentLoaded();
        await window.nekoChatSync.notifyHistoryCommitted({type:'chat_sync_history_changed',characterSyncId:original.characterSyncId});
        if(lifecycle==='explicit_null'){
            assert.equal(merged.length,1);
        }else{
            assert.equal(merged.length,0);
            assert.equal(requests.length,0);
            window.appState.assistantTurnCompletedId='unrelated-turn';
            await intervals[0].callback();
            assert.equal(merged.length,0);
            if(lifecycle==='legacy_missing')window._nekoAssistantTurnId=null;
            if(lifecycle==='current_active'){
                window.appState.assistantTurnCompletedId='old-role-turn';
                await intervals[0].callback();
                assert.equal(merged.length,0);
                window.appState.assistantTurnCompletedId='current-role-turn';
            }
            if(lifecycle==='unknown_streaming')messages[0].status='sent';
            await intervals[0].callback();
            assert.equal(merged.length,1);
        }
        assert.deepEqual(merged[0].map(item=>item.id),lifecycle==='unknown_streaming'
            ?['sync-original-'+id,'unknown-live']:['sync-original-'+id]);
        assert.equal(requests.length,2);
        assert.ok(requests.every(path=>path.endsWith('/status')||path.endsWith('/history')));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("LIFECYCLE", json.dumps(lifecycle)))


def test_foreground_wake_coalesces_visibility_and_pageshow_without_manual_sync():
    """Hidden pages stay quiet; returning wakes all roles through the local bridge."""
    run_script(r"""
        (async()=>{
            let wakeCount=0,release;
            const waiting=new Promise(resolve=>release=resolve);
            global.fetch=async(path,options)=>{
                if(path.endsWith('/wake')){
                    assert.equal(options.method,'POST');wakeCount++;await waiting;
                    return {ok:true,json:async()=>({scheduled:true})};
                }
                assert.ok(path.endsWith('/status'),'No manual sync or role-limited network operation');
                return {ok:true,json:async()=>({characters:[]})};
            };
            documentEvents.DOMContentLoaded();
            document.visibilityState='hidden';
            await documentEvents.visibilitychange();
            assert.equal(wakeCount,0);
            document.visibilityState='visible';
            const first=documentEvents.visibilitychange(),second=windowEvents.pageshow();
            assert.equal(wakeCount,1);
            release();await Promise.all([first,second]);
            assert.equal(wakeCount,1);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_automatic_history_defers_streaming_before_and_during_io_without_consuming_cursor():
    """A deferred cursor remains retryable when the same live turn finishes."""
    run_script(r"""
        (async()=>{
            let messages=[], requests=0, applied=0, beginDuringRead=true;
            window.reactChatWindowHost={getState(){return {messages};},setMessages(next){messages=next;applied++;}};
            global.fetch=async(path)=>({ok:true,async json(){
                requests++;
                if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{cursor:1}}]};
                if(beginDuringRead)window._nekoAssistantTurnId='active-turn';
                return {originals:[original]};
            }});
            documentEvents.DOMContentLoaded();
            window._nekoAssistantTurnId='active-turn';
            await intervals[0].callback();
            assert.equal(requests,0);
            window._nekoAssistantTurnId=null;
            await intervals[0].callback();
            assert.equal(applied,0);
            window._nekoAssistantTurnId=null;
            beginDuringRead=false;
            await intervals[0].callback();
            assert.equal(applied,1);
            assert.equal(messages[0].id,'sync-original-'+id);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_automatic_history_cannot_replace_new_role_after_local_io():
    """The automatic path keeps the same role and latest-request fences."""
    run_script(r"""
        (async()=>{
            window.reactChatWindowHost={getState(){return {messages:[]};},setMessages(){throw new Error('Stale auto history applied');}};
            global.fetch=async(path)=>({ok:true,async json(){
                if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{cursor:1}}]};
                window.lanlan_config.lanlan_name='Other';
                return {originals:[original]};
            }});
            documentEvents.DOMContentLoaded();
            await intervals[0].callback();
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_late_old_character_page_cannot_replace_new_character_window():
    """Changing the active local profile while a page waits prevents its application."""
    run_script(r"""
        (async()=>{
            window.reactChatWindowHost={getState(){return {messages:[]};},setMessages(){throw new Error('Stale page applied');}};
            global.fetch=async(path)=>({ok:true,async json(){
                if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{enabled:true}}]};
                window.lanlan_config.lanlan_name='Other';
                return {originals:[original]};
            }});
            await window.nekoChatSync.loadHistory();
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_latest_same_character_history_wins_when_responses_arrive_out_of_order():
    """An older page cannot revive a message after a newer empty deletion view."""
    run_script(r"""
        (async()=>{
            let messages=[], releaseOld, enteredOld;
            const oldEntered=new Promise(resolve=>enteredOld=resolve);
            const oldPage=new Promise(resolve=>releaseOld=resolve);
            let histories=0;
            window.reactChatWindowHost={getState(){return {messages};},setMessages(next){messages=next;}};
            global.fetch=async(path)=>({ok:true,async json(){
                if(path.endsWith('/status'))return {characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{enabled:true}}]};
                if(++histories===1){enteredOld();return await oldPage;}
                return {originals:[]};
            }});
            const old=window.nekoChatSync.loadHistory();
            await oldEntered;
            await window.nekoChatSync.loadHistory();
            assert.deepEqual(messages,[]);
            releaseOld({originals:[original]});
            await old;
            assert.deepEqual(messages,[]);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_clear_receipt_removes_live_original_not_seen_in_previous_history():
    """Clear's exact backend boundary also removes newly saved optimistic aliases."""
    run_script(r"""
        (async()=>{
            let messages=[{id:'new-live',role:'user',status:'sent'}];
            window.reactChatWindowHost={getState(){return {messages};},setMessages(next){messages=next;}};
            window.nekoChatSync.markUserRequest('request','new-live');
            window.nekoChatSync.markAcceptedUser({request_id:'request',message_id:id});
            global.fetch=async(path)=>({ok:true,async json(){
                if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[{displayName:'Neko',
                    characterSyncId:'c'.repeat(32),binding:{enabled:true,pendingOriginals:0,pendingDeletions:0}}]};
                if(path.endsWith('/clear'))return {messageIds:[id],pending:true};
                return {originals:[]};
            }});
            documentEvents.DOMContentLoaded();
            await headerButton.events.click();
            await body.children[0].querySelector('[data-sync-action="clear"]').events.click();
            assert.deepEqual(messages,[]);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("restored", [False, True])
@pytest.mark.parametrize("bound", [False, True])
def test_clear_local_reply_receipt_hides_exact_alias_despite_history_failure(restored, bound):
    """Local IDs are hidden separately, including delayed bubbles after clearing."""
    run_script(r"""(async()=>{
        let messages=[{id:RESTORED?'local-reply-'+other:'live-local',role:'assistant',turnId:'local-turn',status:'sent'}],failed=false;
        window.reactChatWindowHost={getState:()=>({messages}),setMessages:next=>{messages=next;}};
        window.nekoChatSync.markAssistantOriginal(other,'local-turn');
        global.fetch=async(path)=>({ok:true,async json(){
            if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[{displayName:'Neko',
                characterSyncId:original.characterSyncId,binding:BOUND?{enabled:true,pendingOriginals:0,pendingDeletions:0}:null}]};
            if(path.endsWith('/clear')){failed=true;return {messageIds:[],localMessageIds:[other],pending:false};}
            if(failed)throw new Error('history unavailable');
            return {originals:[],localReplies:RESTORED?[{messageId:other,characterSyncId:original.characterSyncId,
                role:'assistant',text:'local reply',originalTime:original.originalTime}]:[]};
        }});
        documentEvents.DOMContentLoaded();
        await headerButton.events.click();
        const clear=body.children[0].querySelector('[data-sync-action="clear"]');
        assert.equal(clear.disabled,false);
        await clear.events.click();
        assert.deepEqual(messages,[]);
        messages=[{id:'delayed-local-tail',role:'assistant',turnId:'local-turn',status:'sent'}];
        global.fetch=async()=>({ok:true,json:async()=>({originals:[],localReplies:[]})});
        await window.nekoChatSync.loadHistory({characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,
            binding:BOUND?{cursor:0}:null}]});
        assert.deepEqual(messages,[]);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("RESTORED", json.dumps(restored)).replace("BOUND", json.dumps(bound)))


@pytest.mark.parametrize("bound", [False, True])
def test_stale_local_history_cannot_revive_clear_after_status_failure(bound):
    """The successful clear receipt fences already-running local history reads."""
    run_script(r"""
    (async()=>{
        const uid=original.characterSyncId;
        const role={displayName:'Neko',characterSyncId:uid,
            binding:BOUND?{enabled:true,pendingOriginals:0,pendingDeletions:0,cursor:0}:null};
        const local={messageId:other,characterSyncId:uid,role:'assistant',
            text:'synthetic local reply',originalTime:original.originalTime};
        let messages=[{id:'local-reply-'+other,role:'assistant',status:'sent'}];
        let cleared=false,release,enter;
        const waiting=new Promise(resolve=>enter=resolve),blocked=new Promise(resolve=>release=resolve);
        window.reactChatWindowHost={getState:()=>({messages}),setMessages:next=>{messages=next;}};
        global.fetch=async(path)=>({ok:true,json:async()=>{
            if(path.endsWith('/status')){
                if(cleared)throw new Error('synthetic post-clear status unavailable');
                return {serviceUrl:'https://sync.test',characters:[role]};
            }
            if(path.endsWith('/clear')){
                cleared=true;
                return {messageIds:[],localMessageIds:[other],pending:false};
            }
            if(path.endsWith('/history')){
                enter();
                await blocked;
                return {originals:[],localReplies:[local]};
            }
            throw new Error('unexpected request');
        }});
        documentEvents.DOMContentLoaded();
        await headerButton.events.click();
        const pending=window.nekoChatSync.loadHistory({characters:[role]});
        await waiting;
        await body.children[0].querySelector('[data-sync-action="clear"]').events.click();
        assert.deepEqual(messages,[],'clear receipt must first hide the local reply');
        release();
        await pending;
        assert.deepEqual(messages,[],'stale in-flight history must not restore a cleared local reply');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("BOUND", json.dumps(bound)))


def test_clear_click_cannot_retarget_a_new_role_during_status_refresh():
    """The clicked role is fixed before I/O rather than following a global active UID."""
    run_script(r"""
        (async()=>{
            let statusRequests=0;
            window.reactChatWindowHost={getState(){return {messages:[]};}};
            global.fetch=async(path)=>({ok:true,async json(){
                assert.ok(path.endsWith('/status'),'A changed role must not reach clear');
                if(++statusRequests===2)window.lanlan_config.lanlan_name='Other';
                return {serviceUrl:'https://sync.test',characters:[
                    {displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{enabled:true}},
                    {displayName:'Other',characterSyncId:'e'.repeat(32),binding:{enabled:true}}]};
            }});
            documentEvents.DOMContentLoaded();
            await headerButton.events.click();
            await body.children[0].querySelector('[data-sync-action="clear"]').events.click();
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_clear_receipt_keeps_its_original_role_after_inflight_role_change():
    """A late clear receipt cannot write deleted display identities into another role."""
    run_script(r"""
        (async()=>{
            let messages=[{id:'other-live',role:'user',status:'sent'}];
            window.reactChatWindowHost={getState(){return {messages};},setMessages(next){messages=next;}};
            window.nekoChatSync.markLiveOriginal(id,'other-live');
            global.fetch=async(path)=>({ok:true,async json(){
                if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[
                    {displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{enabled:true}},
                    {displayName:'Other',characterSyncId:'e'.repeat(32),binding:{enabled:true}}]};
                if(path.endsWith('/clear')){
                    assert.ok(path.includes('c'.repeat(32)));
                    window.lanlan_config.lanlan_name='Other';
                    await window.nekoChatSync.loadHistory();
                    return {messageIds:[id],pending:true};
                }
                return {originals:[]};
            }});
            documentEvents.DOMContentLoaded();
            await headerButton.events.click();
            await body.children[0].querySelector('[data-sync-action="clear"]').events.click();
            assert.deepEqual(messages.map(message=>message.id),['other-live']);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_failed_sync_still_restores_pages_already_committed_locally():
    """A later cloud failure cannot leave earlier committed download pages invisible."""
    run_script(r"""
        (async()=>{
            let messages=[];
            window.reactChatWindowHost={getState(){return {messages};},setMessages(next){messages=next;}};
            global.fetch=async(path)=>({ok:!path.endsWith('/sync'),async json(){
                if(path.endsWith('/status'))return {serviceUrl:'https://sync.test',characters:[{displayName:'Neko',
                    characterSyncId:'c'.repeat(32),binding:{enabled:true,pendingOriginals:0,pendingDeletions:0}}]};
                if(path.endsWith('/sync'))return {detail:'chat_sync_unavailable'};
                return {originals:[original]};
            }});
            documentEvents.DOMContentLoaded();
            await headerButton.events.click();
            await body.children[0].querySelector('[data-sync-action="sync"]').events.click();
            assert.deepEqual(messages.map(message=>message.id),['sync-original-'+id]);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize("busy", ["sending", "streaming", "active-turn"])
def test_clear_is_blocked_by_existing_unfinished_chat_states(busy):
    """An in-progress reply cannot fall outside the user's reviewed clear boundary."""
    run_script(r"""
        (async()=>{
            const busy=BUSY;
            window._nekoAssistantTurnId=busy==='active-turn'?'turn-one':null;
            window.reactChatWindowHost={getState(){return {messages:[{id:'live',status:busy}]};}};
            global.fetch=async(path)=>({ok:true,async json(){
                assert.ok(path.endsWith('/status'),'Clear must not reach its API while busy');
                return {serviceUrl:'https://sync.test',characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32),binding:{enabled:true}}]};
            }});
            documentEvents.DOMContentLoaded();
            await headerButton.events.click();
            const button=body.children[0].querySelector('[data-sync-action="clear"]');
            assert.equal(button.disabled,true);
            await button.events.click();
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("BUSY", json.dumps(busy)))


@pytest.mark.parametrize("locale", ["en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"])
def test_sync_locale_namespace_is_complete(locale):
    """Every supported locale supplies the same feature labels and message placeholders."""
    baseline = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["chatSync"]
    actual = json.loads((ROOT / f"static/locales/{locale}.json").read_text(encoding="utf-8"))["chatSync"]
    assert actual.keys() == baseline.keys()
    assert "{{originals}}" in actual["pending"]
    assert "{{deletions}}" in actual["pending"]


def test_both_served_pages_restore_sync_without_a_header_control():
    """Both chat entrypoints keep passive restoration without an extra user action."""
    for name in ("index", "chat"):
        html = (ROOT / f"templates/{name}.html").read_text(encoding="utf-8")
        assert 'id="chatSyncButton"' not in html
        assert 'id="react-chat-window-root"' in html
        assert '/static/app/app-chat-sync.js?v=' in html


@pytest.mark.parametrize("lifecycle", ["poll", "pageshow", "visibilitychange"])
def test_chat_host_refreshes_and_wakes_without_a_sync_button(lifecycle):
    """Removing the header control must not disable local polling or foreground catchup."""
    run_script(r"""
        (async()=>{
            const lifecycle=LIFECYCLE, requests=[];
            let messages=[], cursor=1;
            document.getElementById=name=>name==='react-chat-window-root'?body:null;
            document.visibilityState='visible';
            window.reactChatWindowHost={getState:()=>({messages}),setMessages:next=>{messages=next;},
                appendMessage(){throw new Error('Passive sync must not invoke message reactions');}};
            global.fetch=async(path,options)=>{
                requests.push({path,method:options.method});
                assert.ok(path.endsWith('/status')||path.endsWith('/history')||path.endsWith('/wake'));
                if(path.endsWith('/wake'))assert.equal(options.method,'POST');
                return {ok:true,json:async()=>path.endsWith('/status')
                    ?{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{cursor}}]}
                    :path.endsWith('/history')?{originals:cursor===1?[original]:[original,{...original,messageId:other,originSeq:2}]}:{}};
            };
            documentEvents.DOMContentLoaded();
            assert.equal(intervals.length,1);
            await intervals[0].callback();
            assert.deepEqual(messages.map(message=>message.id),['sync-original-'+id]);
            cursor=2;
            if(lifecycle==='poll')await intervals[0].callback();
            else if(lifecycle==='pageshow'){
                windowEvents.pagehide();
                await windowEvents.pageshow();
                assert.equal(intervals.length,2);
            }else{
                document.visibilityState='hidden';
                const previous=requests.length;
                await documentEvents.visibilitychange();
                assert.equal(requests.length,previous);
                document.visibilityState='visible';
                await documentEvents.visibilitychange();
            }
            assert.deepEqual(messages.map(message=>message.id),['sync-original-'+id,'sync-original-'+other]);
            assert.equal(requests.filter(request=>request.path.endsWith('/wake')).length,lifecycle==='poll'?0:1);
            assert.equal(body.children.length,0);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("LIFECYCLE", json.dumps(lifecycle)))


@pytest.mark.parametrize("kind", ["chat", "profile"])
def test_sync_dialogs_accept_pointer_input_without_releasing_chat_passthrough(kind):
    """Real CSS inheritance must allow modal clicks while chat/tutorial gates stay intact."""
    from playwright.sync_api import sync_playwright

    template = (ROOT / ("templates/chat.html" if kind == "chat" else "templates/character_card_manager.html")).read_text(encoding="utf-8")
    stylesheets = re.findall(r'<link rel="stylesheet" href="([^"?]+)', template)
    # static/react/ is a gitignored vite output that unit jobs never build. Its
    # chat rules come from the plain source stylesheet, so load that instead and
    # keep this click check running in clean CI (vendor @imports carry no gates).
    react_source = re.sub(r"^@import[^;]*;", "", (ROOT / "frontend/react-neko-chat/src/styles.css").read_text(encoding="utf-8"),
                          flags=re.MULTILINE)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            # This disposable page never reaches the running backend or user data.
            page.route("**/*", lambda route: route.abort())
            page.set_content(('<body class="electron-chat-window subtitle-web-host yui-guide-chat-buttons-disabled">' if kind == 'chat' else '<body>') +
                             '<div id="react-chat-window-shell"><button id="tutorial-control">Tutorial</button></div>'
                             '<button id="chatSyncButton">Sync</button></body>')
            for href in stylesheets:
                built = ROOT / href.lstrip("/")
                if href.startswith("/static/react/") and not built.is_file():
                    page.add_style_tag(content=react_source)
                else:
                    page.add_style_tag(path=str(built))
            for css in re.findall(r"<style>(.*?)</style>", template, re.DOTALL):
                page.add_style_tag(content=css)
            page.evaluate("""() => {
                window.t = key => key;
                window.setInterval = () => 1;
                window.lanlan_config = {lanlan_name: 'Synthetic'};
                window.fetchCalls = [];
                window.fetch = async path => {
                    window.fetchCalls.push(path);
                    return {ok: true, json: async () => path.endsWith('/history') ? {originals: []} : {
                        serviceUrl: 'https://sync.test', characters: [{displayName: 'Synthetic',
                        characterSyncId: 'a'.repeat(32), binding: {enabled: true, pendingOriginals: 0, pendingDeletions: 0}}]}};
                };
            }""")
            if kind == "chat":
                page.add_script_tag(path=str(SCRIPT))
                page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
                page.locator("#chatSyncButton").click()
                modal = page.locator("#chat-sync-dialog")
            else:
                page.add_script_tag(path=str(ROOT / "static/app/app-character-profile-sync.js"))
                page.evaluate("""async () => {
                    const profile = {schemaVersion: 1, name: 'Synthetic', nickname: '', systemPrompt: '', fields: [], persona: null};
                    await window.nekoCharacterProfileSync.review('a'.repeat(32), {local: profile, head: profile,
                        local_hash: 'b'.repeat(64), head_revision: 1, conflict: true, renameRequired: false});
                }""")
                modal = page.locator("dialog")
            assert page.locator("body").evaluate("node => getComputedStyle(node).pointerEvents") == ("none" if kind == "chat" else "auto")
            assert page.locator("#tutorial-control").evaluate("node => getComputedStyle(node).pointerEvents") == ("none" if kind == "chat" else "auto")
            assert modal.locator("button").evaluate_all(
                "nodes => nodes.every(node => getComputedStyle(node).pointerEvents === 'auto')"
            )
            if kind == "chat":
                modal.locator("summary").click()
                assert modal.locator('input[type="url"]').count() == 0
                assert modal.locator('[data-sync-action="save"]').count() == 0
                modal.locator('[data-sync-action="sync"]').click()
                page.wait_for_function("window.fetchCalls.some(path => path.endsWith('/sync'))")
                modal.get_by_role("button", name="chatSync.close", exact=True).click()
            else:
                modal.locator('[data-profile-choice="close"]').click()
            assert page.locator("dialog[open]").count() == 0
        finally:
            browser.close()


def test_history_consent_real_browser_shows_safe_names_and_submits_only_reviewed_task():
    """The served dialog renders actual labels and explicit consent without cloud I/O."""
    from playwright.sync_api import sync_playwright

    labels = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["chatSync"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content('<button id="chatSyncButton">Sync</button>')
            page.evaluate("""labels => {
                window.t = (key, values = {}) => labels[key.replace('chatSync.', '')]
                    .replace(/{{(.*?)}}/g, (_, name) => values[name]);
                window.setInterval = () => 1;
                window.lanlan_config = {lanlan_name: 'Synthetic'};
                window.requests = [];
                window.fetch = async (path, options) => {
                    window.requests.push({path, body: options.body});
                    return {ok: true, json: async () => {
                        if (path.endsWith('/status')) return {serviceUrl: 'https://sync.test', characters: [{displayName: 'Synthetic',
                            characterSyncId: 'c'.repeat(32), binding: {enabled: true, ownerId: 'owner', bindingId: 'binding',
                                generation: 1, pendingOriginals: 0, pendingDeletions: 0}}]};
                        if (path.endsWith('/history/preview')) return {taskId: 'f'.repeat(32), characterSyncId: 'c'.repeat(32),
                            localName: 'Synthetic', cloudName: 'Cloud <b>Neko</b>', count: 3, unverifiedCount: null, status: 'preview'};
                        if (path.endsWith('/history/approve')) return {status: 'approved'};
                        throw new Error('Unexpected request: ' + path);
                    }};
                };
            }""", labels)
            page.add_script_tag(path=str(SCRIPT))
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.locator("#chatSyncButton").click()
            modal = page.locator("#chat-sync-dialog")
            modal.locator("summary").click()
            modal.get_by_role("button", name=labels["previewHistory"], exact=True).click()
            preview = modal.get_by_role("group", name=labels["historyPreview"], exact=True)
            preview.get_by_text("Local character: Synthetic", exact=True).wait_for()
            preview.get_by_text("Cloud character: Cloud <b>Neko</b>", exact=True).wait_for()
            preview.get_by_text("Verified messages in this preview: 3", exact=True).wait_for()
            assert preview.locator("b").count() == 0
            preview.get_by_text(labels["historyUnverifiedUnknown"], exact=True).wait_for()
            preview.get_by_role("button", name=labels["historyCancel"], exact=True).click()
            assert preview.get_by_role("button").count() == 0
            assert page.evaluate("window.requests.some(item => item.path.endsWith('/history/approve'))") is False
            modal.get_by_role("button", name=labels["previewHistory"], exact=True).click()
            preview.get_by_role("button", name=labels["historyApprove"], exact=True).click()
            page.wait_for_function("window.requests.some(item => item.path.endsWith('/history/approve'))")
            modal.get_by_role("status").filter(has_text=labels["historyQueued"]).wait_for()
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/history/approve')).map(item => JSON.parse(item.body))") == [
                {"taskId": "f" * 32}
            ]
            assert page.evaluate("window.requests.some(item => item.path.endsWith('/sync'))") is False
        finally:
            browser.close()


@pytest.mark.parametrize("approve_deletions", [False, True])
def test_writer_recovery_real_browser_requires_separate_exact_task_consents(approve_deletions):
    """Real served controls require cloud checks before either renewed permission."""
    from playwright.sync_api import sync_playwright

    labels = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["chatSync"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content('<button id="chatSyncButton">Sync</button>')
            page.evaluate("""labels => {
                window.t = (key, values = {}) => labels[key.replace('chatSync.', '')]
                    .replace(/{{(.*?)}}/g, (_, name) => values[name]);
                window.setInterval = () => 1;
                window.lanlan_config = {lanlan_name: 'Synthetic'};
                window.requests = []; window.needsRecovery = true;
                window.fetch = async (path, options) => {
                    window.requests.push({path, body: options.body});
                    return {ok: true, json: async () => {
                        if (path.endsWith('/status')) return {serviceUrl: 'https://sync.test',
                            writerRecoveryRequired: window.needsRecovery, characters: [{displayName: 'Synthetic',
                            characterSyncId: 'c'.repeat(32), binding: {enabled: !window.needsRecovery,
                                ownerId: 'owner', bindingId: 'binding', generation: 1,
                                pendingOriginals: 0, pendingDeletions: 0}}]};
                        if (path.endsWith('/recovery/preview')) {
                            window.needsRecovery = false;
                            return {history: {taskId: 'f'.repeat(32), characterSyncId: 'c'.repeat(32),
                                localName: 'Synthetic', cloudName: 'Cloud Neko', count: 3,
                                unverifiedCount: null, status: 'preview'},
                                deletions: {taskId: 'e'.repeat(32), count: 2}};
                        }
                        if (path.endsWith('/recovery/deletions/approve')) return {status: 'registered'};
                        if (path.endsWith('/history/approve')) return {status: 'approved'};
                        if (path.endsWith('/history')) return {originals: []};
                        throw new Error('Unexpected request: ' + path);
                    }};
                };
            }""", labels)
            page.add_script_tag(path=str(SCRIPT))
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.locator("#chatSyncButton").click()
            modal = page.locator("#chat-sync-dialog")
            assert modal.locator('[data-sync-action="enable"]').is_disabled()
            assert modal.locator('[data-sync-action="history"]').is_disabled()
            messages = []

            def consent(dialog):
                messages.append(dialog.message)
                dialog.accept() if approve_deletions else dialog.dismiss()

            page.on("dialog", consent)
            modal.get_by_role("button", name=labels["recoverWriter"], exact=True).click()
            preview = modal.get_by_role("group", name=labels["historyPreview"], exact=True)
            preview.get_by_text("Verified messages in this preview: 3", exact=True).wait_for()
            assert messages and "2" in messages[0] and "Synthetic" in messages[0] and "Cloud Neko" in messages[0]
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/recovery/preview')).map(item => JSON.parse(item.body))") == [{"serviceUrl": "https://sync.test"}]
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/recovery/deletions/approve')).map(item => JSON.parse(item.body))") == ([{"taskId": "e" * 32}] if approve_deletions else [])
            assert page.evaluate("window.requests.some(item => item.path.endsWith('/history/approve'))") is False
            preview.get_by_role("button", name=labels["historyApprove"], exact=True).click()
            page.wait_for_function("window.requests.some(item => item.path.endsWith('/history/approve'))")
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/history/approve')).map(item => JSON.parse(item.body))") == [{"taskId": "f" * 32}]
            assert page.evaluate("window.requests.some(item => item.path.endsWith('/sync') || item.path.endsWith('/enable'))") is False
        finally:
            browser.close()
