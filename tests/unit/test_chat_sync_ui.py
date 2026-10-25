"""Sync controls stay independent of the capsule chat history display."""

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
        global.fetch = () => { throw new Error('Unexpected chat-history request'); };
        vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
        const id = 'a'.repeat(32), other = 'b'.repeat(32);
        const original = {messageId:id,characterSyncId:'c'.repeat(32),role:'user',text:'same',
            originalTime:'1791540000123',originStreamId:'d'.repeat(32),originSeq:1};
    """
    script = (bootstrap + assertions).replace('process.argv[1]', json.dumps(str(SCRIPT)))
    result = run_node_script("node", script, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('code,key', [
    ('chat_sync_account_mismatch', 'errorAccountMismatch'),
    ('chat_sync_writer_recovery_required', 'errorWriterRecovery'),
    ('chat_sync_unavailable', 'errorNetwork'),
    ('chat_sync_retry_later', 'errorRetryLater'),
    ('chat_sync_session_changed', 'errorSessionChanged'),
    ('private token from an unknown error', 'errorUnknown'),
])
def test_recovery_failure_explains_the_actual_error_and_next_step(code, key):
    """The actual recovery listener uses localized reasons without leaking arbitrary details."""
    labels = json.loads((ROOT / 'static/locales/en.json').read_text(encoding='utf-8'))['chatSync']
    run_script(r"""(async()=>{
        const labels=LABELS,code=CODE,key=KEY;
        window.t=(key,values={})=>labels[key.replace('chatSync.','')]
            .replace(/{{(.*?)}}/g,(_,name)=>values[name]);
        global.fetch=async(path)=>({ok:path.endsWith('/status'),json:async()=>path.endsWith('/status')
            ?{writerRecoveryRequired:true,characters:[{displayName:'Neko',characterSyncId:'c'.repeat(32)}]}
            :{detail:code}});
        await window.nekoChatSync.openSettings();
        const modal=body.children[0],recover=modal.querySelector('[data-sync-action="recover"]');
        await recover.events.click();
        const shown=modal.querySelectorAll('*').find(node=>node.className==='chat-sync-status').textContent;
        assert(shown.includes(labels[key]));
        if(key==='errorUnknown')assert(!shown.includes(code));
        else assert(shown.includes(code));
        assert.notEqual(shown,labels.failed);
        assert.equal(recover.disabled,false);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('LABELS', json.dumps(labels)).replace('CODE', json.dumps(code)).replace('KEY', json.dumps(key)))


@pytest.mark.parametrize('source', ['setup', 'binding'])
def test_opening_sync_settings_shows_the_retained_failure_without_retrying(source):
    """A local status read keeps setup and channel errors visible across dialog openings."""
    run_script(r"""(async()=>{
        const requests=[];
        window.t=(key,values={})=>key+JSON.stringify(values);
        global.fetch=async(path)=>{requests.push(path);return{ok:true,json:async()=>({characters:[{
            displayName:'Neko',characterSyncId:'c'.repeat(32),
            syncSetupError:SOURCE==='setup'?'chat_sync_account_mismatch':null,
            binding:{writerRecoveryRequired:true,lastSyncError:SOURCE==='binding'?'chat_sync_account_mismatch':null}
        }]})};};
        await window.nekoChatSync.openSettings();
        const shown=body.children[0].querySelectorAll('*').find(node=>node.className==='chat-sync-status').textContent;
        assert(shown.includes('errorAccountMismatch')&&shown.includes('chat_sync_account_mismatch'));
        assert.deepEqual(requests,['/api/chat-sync/status']);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('SOURCE', json.dumps(source)))


def test_sync_settings_reports_pending_and_local_only_work():
    run_script(r"""(async()=>{
        window.t=(key,values={})=>key+JSON.stringify(values);
        global.fetch=async()=>({ok:true,json:async()=>({characters:[{
            displayName:'Neko',characterSyncId:original.characterSyncId,binding:{pendingOriginals:3,
                pendingDeletions:2,localOnlyOriginals:1,historyBackfill:{awaitingRegistration:4}}
        }]})});
        await window.nekoChatSync.openSettings();
        const shown=body.children[0].querySelectorAll('*').find(node=>node.className==='chat-sync-status').textContent;
        assert(shown.includes('chatSync.pending')&&shown.includes('"originals":3')&&shown.includes('"deletions":2'));
        assert(shown.includes('chatSync.oversizedLocalOnly')&&shown.includes('"count":1'));
        assert(shown.includes('chatSync.historyAwaitingRegistration')&&shown.includes('"count":4'));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_current_socket_refreshes_recent_memory_without_backfilling_capsule_history():
    """Keep the new recent-memory notification and its socket replacement fence."""
    from tests.unit.test_app_websocket_static_routes import _block_after

    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    body = _block_after(source, "S.socket.onmessage = function (event) {")
    run_script(r"""
        const calls=[], broadcasts=[], socket={}, S={socket}, _thisSocket=socket;
        window.appInterpage={handleMemoryEdited:async name=>{calls.push(name);}};
        global.BroadcastChannel=class {postMessage(data){broadcasts.push(data);}close(){}};
        window.reactChatWindowHost={setMessages(){throw new Error('Capsule history must remain unchanged');}};
        function onmessage(event){BODY}
        const hint={type:'chat_sync_history_changed',characterSyncId:original.characterSyncId,memoryEditedName:'Neko'};
        onmessage({data:JSON.stringify(hint)});
        assert.deepEqual(calls,['Neko']);
        assert.deepEqual(broadcasts,[{action:'recent_memory_changed',catgirl_name:'Neko'}]);
        S.socket={};onmessage({data:JSON.stringify(hint)});
        assert.deepEqual(calls,['Neko']);
    """.replace("BODY", body))


def test_pc_pet_ipc_bridge_delivers_hint_without_backfilling_full_chat_history():
    """Keep both PC chat targets and stale-host filtering without replacing bubbles."""
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
            assert.deepEqual(mirrored,[hint]);assert.equal(merged.length,0);
            new pet.window.WebSocket('ws://synthetic.test');
            socket.emit('message',{data:JSON.stringify(hint)});
            assert.equal(mirrored.length,1);
            full.proxy.close();pet.lifecycle.dispose();clients.forEach(item=>item.lifecycle.dispose());
            assert.equal(full.proxy.readyState,pet.RealWebSocket.CLOSED);
        })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace("PC_ROOT", json.dumps(str(pc))).replace("HELPERS", helpers).replace("CALLBACK", callback))


def test_recovery_uses_builtin_service_instead_of_legacy_saved_origin():
    """Old local test setup must not make a user's recovery request fail API validation."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[];let needsRecovery=true;
        global.fetch=async(path,options)=>({ok:true,async json(){
            requests.push({path,body:options.body});
            if(path.endsWith('/status'))return {serviceUrl:'https://localhost:8443',
                writerRecoveryRequired:needsRecovery,
                characters:[{displayName:'Neko',characterSyncId:uid,binding:null}]};
            if(path.endsWith('/recovery/preview')){
                needsRecovery=false;
                return {history:{taskId:'f'.repeat(32),characterSyncId:uid,localName:'Neko',
                    cloudName:'Cloud Neko',count:0,unverifiedCount:null,status:'preview'},deletions:{count:0}};
            }
            if(path.endsWith('/history'))return {originals:[]};
            throw new Error('Unexpected request '+path);
        }});
        await window.nekoChatSync.openSettings();
        await body.children[0].querySelector('[data-sync-action="recover"]').events.click();
        assert.deepEqual(requests.filter(item=>item.path.endsWith('/recovery/preview')).map(item=>JSON.parse(item.body)),
            [{serviceUrl:'https://sync.nekotech.cn'}]);
        assert.equal(requests.filter(item=>item.path.endsWith('/history/approve')
            || item.path.endsWith('/recovery/deletions/approve')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


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
            assert.equal(intervals.length,0);
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


@pytest.mark.parametrize("locale", ["en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"])
def test_sync_locale_namespace_is_complete(locale):
    """Every supported locale supplies the same feature labels and message placeholders."""
    baseline = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["chatSync"]
    actual = json.loads((ROOT / f"static/locales/{locale}.json").read_text(encoding="utf-8"))["chatSync"]
    assert actual.keys() == baseline.keys()
    assert "{{originals}}" in actual["pending"]
    assert "{{deletions}}" in actual["pending"]


@pytest.mark.parametrize('trigger', ['connect', 'foreground', 'turn_end'])
def test_capsule_history_is_not_restored_or_reordered_by_sync_events(trigger):
    run_script(r"""(async()=>{
        const trigger=TRIGGER,requests=[];
        const current=[{id:'live-user',role:'user',createdAt:3,blocks:[{type:'text',text:'same'}]},
            {id:'live-assistant',role:'assistant',status:'streaming',turnId:'live-turn',createdAt:1,
                blocks:[{type:'text',text:'split reply'},{type:'image',url:'local-image'}]}];
        const before=JSON.stringify(current);
        window.reactChatWindowHost={getState:()=>({messages:current}),
            setMessages(){throw new Error('Sync cannot replace chat history');},
            appendMessage(){throw new Error('Sync cannot replay chat bubbles');}};
        global.fetch=async(path,options)=>{
            requests.push(path);assert.equal(path,'/api/chat-sync/wake');assert.equal(options.method,'POST');
            return {ok:true,json:async()=>({scheduled:true})};
        };
        documentEvents.DOMContentLoaded();
        assert.equal(intervals.length,0);
        if(trigger==='foreground')await windowEvents.pageshow();
        else if(trigger==='connect'&&windowEvents['voice-input-socket-open'])await windowEvents['voice-input-socket-open']();
        else if(trigger==='turn_end'&&windowEvents['neko-assistant-turn-end'])await windowEvents['neko-assistant-turn-end']({detail:{turnId:'live-turn'}});
        assert.deepEqual(requests,trigger==='foreground'?['/api/chat-sync/wake']:[]);
        assert.equal(JSON.stringify(current),before);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('TRIGGER', json.dumps(trigger)))


@pytest.mark.parametrize('original_id', [None, 'a' * 32])
def test_voice_transcript_uses_the_pre_sync_merge_window(original_id):
    from tests.unit.test_app_websocket_static_routes import _block_after

    source = (ROOT / 'static/app/app-websocket.js').read_text(encoding='utf-8')
    handler = _block_after(source, "} else if (response.type === 'user_transcript') {")
    run_script(r"""
        const S={isRecording:true,lastVoiceUserMessage:{textContent:'first ',isConnected:true},lastVoiceUserMessageTime:Date.now()};
        const C={VOICE_TRANSCRIPT_MERGE_WINDOW:1000};
        function removeExternalAsrPreview(){}
        function resolveAssistantRequestId(){return '';}
        global.CustomEvent=class {};
        window.dispatchEvent=()=>{};
        window.appendMessage=()=>{throw new Error('A nearby transcript must merge into the existing bubble');};
        function receive(response){HANDLER}
        receive({type:'user_transcript',text:'second',chat_original_id:ORIGINAL_ID});
        assert.equal(S.lastVoiceUserMessage.textContent,'first second');
    """.replace('HANDLER', handler).replace('ORIGINAL_ID', json.dumps(original_id)))


def test_both_served_pages_keep_sync_controls_without_a_header_control():
    """Both chat entrypoints retain account controls and foreground wake."""
    for name in ("index", "chat"):
        html = (ROOT / f"templates/{name}.html").read_text(encoding="utf-8")
        assert 'id="chatSyncButton"' not in html
        assert 'id="react-chat-window-root"' in html
        assert '/static/app/app-chat-sync.js?v=' in html


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
            page.add_style_tag(path=str(ROOT / "static/css/chat_sync.css"))
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
                assert modal.locator('[data-sync-action]').count() == 1
                assert modal.locator('[data-sync-action="recover"]').is_disabled()
                assert modal.locator('input, summary').count() == 0
                modal.get_by_role("button", name="chatSync.close", exact=True).click()
            else:
                modal.locator('[data-profile-choice="close"]').click()
            assert page.locator("dialog[open]").count() == 0
        finally:
            browser.close()


@pytest.mark.parametrize("change", ["closed", "role", "uid"])
def test_recovery_ignores_late_results_after_its_dialog_context_changes(change):
    """An old recovery result cannot prompt deletion for a newly opened character."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[];let release,changed=false;
        window.confirm=()=>{throw new Error('Stale recovery requested deletion consent');};
        global.fetch=async(path,options)=>({ok:true,async json(){
            requests.push({path,body:options.body});
            if(path.endsWith('/status'))return {writerRecoveryRequired:true,characters:[{
                displayName:changed&&CHANGE==='role'?'Other':'Neko',
                characterSyncId:changed&&CHANGE==='uid'?'d'.repeat(32):uid,binding:null}]};
            if(path.endsWith('/recovery/preview'))return await new Promise(resolve=>{release=resolve;});
            throw new Error('Unexpected request '+path);
        }});
        await window.nekoChatSync.openSettings();
        const modal=body.children[0],recover=modal.querySelector('[data-sync-action="recover"]');
        const pending=recover.events.click();
        await new Promise(resolve=>setImmediate(resolve));
        await recover.events.click();
        assert.equal(requests.filter(item=>item.path.endsWith('/recovery/preview')).length,1);
        changed=true;
        if(CHANGE==='closed')modal.close();
        else if(CHANGE==='role')await window.nekoChatSync.openSettings('Other');
        release({history:{localName:'Neko',cloudName:'Cloud Neko'},deletions:{count:1,taskId:'e'.repeat(32)}});
        await pending;
        assert.equal(requests.filter(item=>item.path.endsWith('/recovery/deletions/approve')
            ||item.path.endsWith('/history/approve')).length,0);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('CHANGE', json.dumps(change)))


@pytest.mark.parametrize("theme,language,width,needs_recovery", [
    ("light", "zh-CN", 375, True), ("dark", "zh-CN", 1280, True), ("dark", "ru", 375, False),
])
def test_recovery_panel_tooltip_is_accessible_and_fits_the_viewport(theme, language, width, needs_recovery):
    """Mouse and keyboard help remain usable even while the recovery button is disabled."""
    from playwright.sync_api import sync_playwright

    labels = json.loads((ROOT / "static/locales" / (language + ".json")).read_text(encoding="utf-8"))["chatSync"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": width, "height": 700})
            page.route("**/*", lambda route: route.abort())
            page.set_content('<html data-theme="' + theme + '"><body></body></html>')
            page.add_style_tag(path=str(ROOT / "static/css/chat_sync.css"))
            page.evaluate("""({labels,needsRecovery})=>{
                window.t=key=>labels[key.replace('chatSync.','')];
                window.setInterval=()=>1;window.lanlan_config={lanlan_name:'Synthetic'};
                window.fetch=async()=>({ok:true,json:async()=>({writerRecoveryRequired:needsRecovery,
                    characters:[{displayName:'Synthetic',characterSyncId:'c'.repeat(32),binding:null}]})});
            }""", {"labels": labels, "needsRecovery": needs_recovery})
            page.add_script_tag(path=str(SCRIPT))
            page.evaluate("async()=>await window.nekoChatSync.openSettings()")
            modal = page.locator('#chat-sync-dialog')
            assert modal.locator('[data-sync-action]').evaluate_all("nodes=>nodes.map(node=>node.dataset.syncAction)") == ['recover']
            assert modal.locator('[data-sync-action="recover"]').is_enabled() == needs_recovery
            assert modal.locator('details, [data-history-action]').count() == 0
            help_button = modal.get_by_role('button', name=labels['recoverWriterHelp'], exact=True)
            tooltip = modal.get_by_role('tooltip', include_hidden=True)
            assert not tooltip.is_visible()
            help_button.hover()
            assert tooltip.is_visible()
            page.mouse.move(0, 0)
            help_button.focus()
            assert tooltip.is_visible()
            assert help_button.get_attribute('aria-describedby') == tooltip.get_attribute('id')
            bounds = tooltip.bounding_box()
            assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width
            assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 700
            assert modal.bounding_box()['width'] <= width - 32
            modal.get_by_role('button', name=labels['close'], exact=True).click()
            assert page.locator('dialog[open]').count() == 0
        finally:
            browser.close()


@pytest.mark.parametrize("approve_deletions", [False, True])
@pytest.mark.parametrize("history_available", [False, True])
def test_writer_recovery_keeps_separate_deletion_consent_without_history_controls(approve_deletions, history_available):
    """The compact recovery panel cannot publish history or copied deletes by opening it."""
    from playwright.sync_api import sync_playwright

    labels = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["chatSync"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content('<button id="chatSyncButton">Sync</button>')
            page.evaluate("""({labels, historyAvailable}) => {
                window.t = (key, values = {}) => labels[key.replace('chatSync.', '')]
                    .replace(/{{(.*?)}}/g, (_, name) => values[name]);
                window.setInterval = () => 1;
                window.lanlan_config = {lanlan_name: 'Synthetic'};
                window.requests = []; window.needsRecovery = true;
                window.fetch = async (path, options) => {
                    window.requests.push({path, body: options.body});
                    return {ok: true, json: async () => {
                        if (path.endsWith('/status')) return {serviceUrl: 'https://localhost:8443',
                            writerRecoveryRequired: window.needsRecovery, characters: [{displayName: 'Synthetic',
                            characterSyncId: 'c'.repeat(32), binding: {enabled: !window.needsRecovery,
                                ownerId: 'owner', bindingId: 'binding', generation: 1}}]};
                        if (path.endsWith('/recovery/preview')) {
                            window.needsRecovery = false;
                            return {history: historyAvailable ? {localName: 'Synthetic', cloudName: 'Cloud Neko', count: 3} : null,
                                historyError: historyAvailable ? null : 'chat_sync_unavailable',
                                deletions: {taskId: 'e'.repeat(32), count: 2, localName: 'Synthetic', cloudName: 'Cloud Neko'}};
                        }
                        if (path.endsWith('/recovery/deletions/approve')) return {status: 'registered'};
                        if (path.endsWith('/history')) return {originals: []};
                        throw new Error('Unexpected request: ' + path);
                    }};
                };
            }""", {"labels": labels, "historyAvailable": history_available})
            page.add_script_tag(path=str(SCRIPT))
            page.evaluate("document.dispatchEvent(new Event('DOMContentLoaded'))")
            page.locator("#chatSyncButton").click()
            modal = page.locator("#chat-sync-dialog")
            assert modal.locator('[data-sync-action]').count() == 1
            assert modal.locator('[data-sync-action="recover"]').is_enabled()
            assert modal.locator('[data-history-action], details').count() == 0
            assert page.evaluate("window.requests.every(item => item.body === undefined)")
            messages = []

            def consent(dialog):
                messages.append(dialog.message)
                dialog.accept() if approve_deletions else dialog.dismiss()

            page.on("dialog", consent)
            modal.get_by_role("button", name=labels["recoverWriter"], exact=True).click()
            modal.get_by_role("status").filter(has_text=labels["recoveryChecked"]).wait_for()
            if not history_available:
                assert labels['initialHistoryFailed'] in modal.get_by_role('status').inner_text()
            assert messages and "2" in messages[0] and "Synthetic" in messages[0] and "Cloud Neko" in messages[0]
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/recovery/preview')).map(item => JSON.parse(item.body))") == [{"serviceUrl": "https://sync.nekotech.cn"}]
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/recovery/deletions/approve')).map(item => JSON.parse(item.body))") == ([{"taskId": "e" * 32}] if approve_deletions else [])
            assert page.evaluate("window.requests.some(item => ['/history/approve','/sync-choice','/enable','/sync'].some(suffix => item.path.endsWith(suffix)))") is False
            assert modal.locator('[data-sync-action="recover"]').is_disabled()
        finally:
            browser.close()
