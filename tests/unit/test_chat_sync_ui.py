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
        // Like a real document, a load event with no registered listener does nothing.
        const documentEvents = {DOMContentLoaded(){}}, windowEvents = {}, intervals = [];
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
            getElementById(){return headerButton;},createElement:element,body,
            dispatchEvent(event){if(documentEvents[event.type])documentEvents[event.type](event);} };
        global.fetch = () => { throw new Error('Unexpected chat-history request'); };
        vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
        const id = 'a'.repeat(32), other = 'b'.repeat(32);
        const original = {messageId:id,characterSyncId:'c'.repeat(32),role:'user',text:'same',
            originalTime:'1791540000123',originStreamId:'d'.repeat(32),originSeq:1};
    """
    script = (bootstrap + assertions).replace('process.argv[1]', json.dumps(str(SCRIPT)))
    result = run_node_script("node", script, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_media_extension_does_not_restore_or_reorder_capsule_history():
    run_script(r"""(async()=>{
        const messages=[{id:'live',role:'user',blocks:[{type:'image',url:'local-image'}]}];
        window.reactChatWindowHost={getState:()=>({messages}),setMessages(){throw new Error('No archive injection');}};
        await window.nekoChatSync.notifyMediaChanged();
        assert.equal(window.nekoChatSync.mergeHistory,undefined);
        assert.equal(messages[0].blocks[0].url,'local-image');
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize('failure', ['configuration', 'not_ready', 'receipt', 'none'])
@pytest.mark.parametrize('restoration', ['success', 'silent_failure', 'exception', 'role_changed'])
def test_cloud_model_failure_is_visible_after_restoration_without_false_admission(failure, restoration):
    run_script(r"""(async()=>{
        const failure=FAILURE,restoration=RESTORATION,reloads=[],receipts=[],toasts=[];
        const candidate={token:'candidate-token',path:'/user_live2d/candidate/model.model3.json',name:'Neko'};
        window.live2dManager={currentModel:{},getCurrentModel(){return this.currentModel;}};
        window.showStatusToast=(...args)=>toasts.push(args);
        window.handleModelReload=async(name,options)=>{
            reloads.push({name,options});
            if(options.temporaryConfig){
                assert.equal(options.preservePreviousUntilLoaded,true);
                assert.equal(options.throwOnError,true);
                if(failure==='configuration'){
                    window.live2dManager.currentModel=null;
                    throw new Error('private provider details');
                }
                window.live2dManager._lastLoadedModelPath=candidate.path;
                window.live2dManager._modelLoadState=failure==='not_ready'?'idle':'ready';
                return true;
            }
            if(restoration==='role_changed')window.lanlan_config.lanlan_name='Other';
            if(restoration==='exception')throw new Error('private restoration details');
            return restoration==='silent_failure'?undefined:true;
        };
        global.fetch=async(path,options)=>({ok:true,json:async()=>{
            if(path.endsWith('/status'))return{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId}]};
            if(path.endsWith('/model-sync'))return{candidate};
            assert(path.endsWith('/model-sync/'+candidate.token));
            receipts.push(JSON.parse(options.body));
            return{applied:failure==='none'};
        }});
        await window.nekoChatSync.notifyMediaChanged();
        assert.deepEqual(receipts[0],{loaded:failure!=='configuration'&&failure!=='not_ready'});
        if(failure==='configuration'||failure==='not_ready')assert(receipts.every(receipt=>!receipt.loaded));
        if(failure==='none'){
            assert.equal(reloads.length,1);
            assert.deepEqual(toasts,[]);
        }else{
            assert(reloads.length>=2);
            assert(reloads.slice(1).every(reload=>reload.name==='Neko'&&!reload.options.temporaryConfig));
            assert.deepEqual(toasts,restoration==='role_changed'?[]:[['app.modelSwitchFailed',3000]]);
        }
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('FAILURE', json.dumps(failure)).replace('RESTORATION', json.dumps(restoration)))


@pytest.mark.parametrize('first_result', ['success', 'empty', 'error', 'role_changed'])
def test_candidate_fetch_has_one_flight_and_releases_every_early_exit(first_result):
    run_script(r"""(async()=>{
        let release, reads=0, receipts=0, reloads=0;
        const toasts=[],candidate={token:'candidate-token',path:'/user_live2d/new/model.model3.json',name:'Neko'};
        window.live2dManager={getCurrentModel(){return{};},_modelLoadState:'ready'};
        window.showStatusToast=(...args)=>toasts.push(args);
        window.handleModelReload=async(name,options)=>{reloads++;window.live2dManager._lastLoadedModelPath=options.temporaryConfig.model_path;};
        global.fetch=async(path)=>{
            if(path.endsWith('/status'))return{ok:true,json:async()=>({characters:[{displayName:'Neko',characterSyncId:original.characterSyncId}]})};
            if(path.endsWith('/model-sync')){
                reads++;
                if(reads===1)return await new Promise(resolve=>{release=resolve;});
                return{ok:true,json:async()=>({})};
            }
            receipts++;
            return{ok:true,json:async()=>({applied:receipts===1})};
        };
        const first=window.nekoChatSync.notifyMediaChanged();
        await new Promise(resolve=>setImmediate(resolve));
        const second=window.nekoChatSync.notifyMediaChanged();
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(reads,1);
        if(RESULT==='role_changed')window.lanlan_config.lanlan_name='Other';
        release({ok:RESULT!=='error',json:async()=>RESULT==='error'?{detail:'chat_sync_unavailable'}:RESULT==='empty'?{}:{candidate}});
        await Promise.all([first,second]);
        assert.equal(receipts,RESULT==='success'?1:0);
        assert.equal(reloads,RESULT==='success'?1:0);
        assert.deepEqual(toasts,[]);
        window.lanlan_config.lanlan_name='Neko';
        await window.nekoChatSync.notifyMediaChanged();
        assert.equal(reads,2);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('RESULT', json.dumps(first_result)))


@pytest.mark.parametrize('action', ['cloud', 'local', 'retry'])
@pytest.mark.parametrize('context', ['same', 'role_changed', 'closed'])
def test_model_action_failure_is_visible_only_in_its_current_dialog(action, context):
    run_script(r"""(async()=>{
        window.t=(key,values={})=>key+JSON.stringify(values);
        const create=document.createElement;
        document.createElement=()=>{
            const node=create(),query=node.querySelector.bind(node);
            node.querySelector=selector=>selector==='[data-sync-model]'?node.children.find(child=>'syncModel' in child.dataset):query(selector);
            return node;
        };
        let reject;
        global.fetch=async(path)=>{
            if(path.endsWith('/status'))return{ok:true,json:async()=>({characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{enabled:true}}]})};
            if(path.endsWith('/model-sync'))return{ok:true,json:async()=>ACTION==='retry'?{failed:true}:{conflict:true}};
            return await new Promise((resolve,fail)=>{reject=fail;});
        };
        await window.nekoChatSync.openSettings();
        const modal=body.children[0],panel=modal.querySelector('[data-sync-model]');
        const button=panel.children.find(child=>child.textContent.startsWith('cloudModel.'+({cloud:'useCloud',local:'keepLocal',retry:'retry'}[ACTION])));
        const status=modal.children.find(child=>child.className==='chat-sync-status'), before=status.textContent;
        const pending=button.events.click();
        assert.equal(button.disabled,true);
        if(CONTEXT==='role_changed')window.lanlan_config.lanlan_name='Other';
        if(CONTEXT==='closed')modal.close();
        reject(new Error('chat_sync_unavailable'));
        await pending;
        assert.equal(button.disabled,false);
        if(CONTEXT==='same')assert(status.textContent.includes('chatSync.errorNetwork')&&status.textContent.includes('chat_sync_unavailable'));
        else assert.equal(status.textContent,before);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('ACTION', json.dumps(action)).replace('CONTEXT', json.dumps(context)))


@pytest.mark.parametrize('lost_confirmation', ['before_commit', 'after_commit'])
def test_model_confirmation_transport_loss_keeps_renderer_success_retryable(lost_confirmation):
    run_script(r"""(async()=>{
        const loss=LOSS,receipts=[],reloads=[];
        let candidate={token:'candidate-token',path:'/user_live2d/candidate/model.model3.json',name:'Neko'};
        const candidatePath=candidate.path;
        let saved='/user_live2d/original/model.model3.json',lost=false,failed=false;
        window.live2dManager={currentModel:{},_lastLoadedModelPath:saved,_modelLoadState:'ready',
            getCurrentModel(){return this.currentModel;}};
        window.showStatusToast=()=>{};
        window.handleModelReload=async(name,options)=>{
            assert.equal(name,'Neko');
            const path=options.temporaryConfig?options.temporaryConfig.model_path:saved;
            reloads.push(path);
            window.live2dManager._lastLoadedModelPath=path;
        };
        global.fetch=async(path,options)=>{
            let value;
            if(path.endsWith('/status'))value={characters:[{displayName:'Neko',characterSyncId:original.characterSyncId}]};
            else if(path.endsWith('/model-sync'))value=candidate?{candidate}:{failed};
            else{
                const receipt=JSON.parse(options.body);receipts.push(receipt);
                if(!lost){
                    lost=true;
                    if(loss==='after_commit'){saved=candidatePath;candidate=null;}
                    throw new Error('chat_sync_unavailable');
                }
                if(!receipt.loaded){failed=true;candidate=null;}
                else{saved=candidatePath;candidate=null;}
                value={applied:receipt.loaded};
            }
            return{ok:true,json:async()=>value};
        };
        await window.nekoChatSync.notifyMediaChanged();
        assert.deepEqual(receipts,[{loaded:true}]);
        assert.equal(failed,false);
        assert.equal(window.live2dManager._lastLoadedModelPath,saved);
        assert.deepEqual(reloads,[candidatePath,saved]);
        assert.equal(Boolean(candidate),loss==='before_commit');
        await window.nekoChatSync.notifyMediaChanged();
        assert(receipts.every(receipt=>receipt.loaded));
        assert.equal(receipts.length,loss==='before_commit'?2:1);
        assert.equal(candidate,null);
        assert.equal(window.live2dManager._lastLoadedModelPath,candidatePath);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('LOSS', json.dumps(lost_confirmation)))


@pytest.mark.parametrize('received_count', [1, 200])
def test_unknown_time_messages_follow_receive_order_and_keep_the_window_stable(received_count):
    run_script(r"""(async()=>{
        const local=Array.from({length:50},(_,i)=>({id:'local-'+i,createdAt:1000+i,status:'sent',blocks:[]}));
        let messages=local.slice(),writes=0;
        const records=Array.from({length:COUNT},(_,i)=>({...original,messageId:i.toString(16).padStart(32,'0'),text:'phone '+i,originalTime:null}));
        const host=window.reactChatWindowHost={maxMessages:50,getState:()=>({messages}),setMessages:value=>{writes++;messages=value;}};
        document.visibilityState='visible';
        global.fetch=async(path)=>({ok:true,json:async()=>path.endsWith('/status')?{characters:[{
            displayName:'Neko',characterSyncId:original.characterSyncId,binding:{enabled:true}}]}:{originals:records}});
        windowEvents['neko-assistant-turn-end']();
        for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.equal(messages.length,host.maxMessages);
        const received=messages.filter(message=>message.id.startsWith('sync-recent-'));
        assert.equal(received.length,Math.min(COUNT,50));
        assert.equal(received.at(-1).blocks[0].text,'phone '+(COUNT-1));
        assert(received.every(message=>message.createdAt===0&&message.time==='chatSync.unknownTime'));
        const snapshot=JSON.stringify(messages);
        windowEvents['neko-assistant-turn-end']();
        for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.equal(JSON.stringify(messages),snapshot);
        assert.equal(writes,1);
        records.push({...original,messageId:'f'.repeat(32),text:'new arrival',originalTime:null});
        windowEvents['neko-assistant-turn-end']();
        for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.equal(messages.at(-1).blocks[0].text,'new arrival');
        assert.equal(messages.length,50);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('COUNT', str(received_count)))


@pytest.mark.parametrize('received_count', [50, 200])
def test_synced_epoch_zero_remains_a_known_original_time(received_count):
    run_script(r"""(async()=>{
        let messages=[];
        window.reactChatWindowHost={maxMessages:50,getState:()=>({messages}),setMessages:value=>{messages=value;}};
        document.visibilityState='visible';
        const records=Array.from({length:COUNT},(_,i)=>({...original,messageId:i.toString(16).padStart(32,'0'),originalTime:'0'}));
        global.fetch=async(path)=>({ok:true,json:async()=>path.endsWith('/status')?{characters:[{
            displayName:'Neko',characterSyncId:original.characterSyncId,binding:{enabled:true}}]}:{originals:records}});
        windowEvents['neko-assistant-turn-end']();
        for(let i=0;i<12;i++)await new Promise(resolve=>setImmediate(resolve));
        assert.equal(messages.length,50);
        assert(messages.every(message=>message.createdAt===0&&message.time===new Date(0).toLocaleString()));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('COUNT', str(received_count)))


def test_model_flight_and_action_error_in_a_real_chromium_page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.set_content('<div id="live2d-container"></div>')
        page.evaluate(r"""() => {
            window.lanlan_config={lanlan_name:'Neko'};
            window.t=(key,values={})=>key==='chatSync.errorDetail'?values.message+':'+values.code:key;
            window.calls={reads:0,receipts:0,reloads:0,toasts:[]};
            window.live2dManager={_modelLoadState:'ready',getCurrentModel(){return{};}};
            window.showStatusToast=(...args)=>calls.toasts.push(args);
            window.handleModelReload=async(name,options)=>{calls.reloads++;live2dManager._lastLoadedModelPath=options.temporaryConfig.model_path;};
            window.fetch=async(path)=>{
                if(path.endsWith('/status'))return{ok:true,json:async()=>({characters:[{displayName:'Neko',characterSyncId:'a'.repeat(32),binding:{enabled:true}}]})};
                if(path.endsWith('/model-sync')){
                    calls.reads++;
                    if(calls.reads===1)return await new Promise(resolve=>{window.releaseModel=resolve;});
                    return{ok:true,json:async()=>({conflict:true})};
                }
                if(path.endsWith('/model-sync-action'))return{ok:false,json:async()=>({detail:'chat_sync_unavailable'})};
                calls.receipts++;
                return{ok:true,json:async()=>({applied:calls.receipts===1})};
            };
        }""")
        page.add_script_tag(path=str(SCRIPT))
        result = page.evaluate(r"""async() => {
            const first=nekoChatSync.notifyMediaChanged();
            while(!window.releaseModel)await new Promise(resolve=>setTimeout(resolve,0));
            const second=nekoChatSync.notifyMediaChanged();
            await new Promise(resolve=>setTimeout(resolve,0));
            const reads=calls.reads;
            releaseModel({ok:true,json:async()=>({candidate:{token:'candidate-token',path:'/new.model3.json',name:'Neko'}})});
            await Promise.all([first,second]);
            return{reads,...calls};
        }""")
        assert result == {'reads': 1, 'receipts': 1, 'reloads': 1, 'toasts': []}
        page.evaluate('nekoChatSync.openSettings()')
        page.get_by_role('button', name='cloudModel.useCloud', exact=True).click()
        page.locator('.chat-sync-status').filter(has_text='chat_sync_unavailable').wait_for()
        assert page.get_by_role('button', name='cloudModel.useCloud', exact=True).is_enabled()
        assert not errors
        browser.close()


@pytest.mark.parametrize('received_count', [50, 200])
def test_unchanged_recent_display_keeps_the_real_host_window_and_pending_feedback(received_count):
    """Comparing an uncropped batch must not replace the host's existing 50-message window."""
    run_script(r"""(async()=>{
        const I=window.__appReactChatWindowParts={_sortKeySeq:0,
            state:{messages:[],_galgameRequestSeq:0},renderWindow(){}};
        for(const name of ['geometry-and-messages.js','message-bundle-actions-and-prompts.js']){
            vm.runInThisContext(fs.readFileSync(HOST_PARTS+'/'+name,'utf8'));
        }
        I.renderWindow=()=>{};
        const host=window.reactChatWindowHost;
        host.getState=()=>({messages:I.state.messages.map(I.cloneMessage)});
        let updates=0,reads=0;
        host.setMessages=messages=>{updates++;return I.setMessages(messages);};
        const local=[
            {id:'local-user',role:'user',author:'User',createdAt:5000,time:'10:00',status:'sent',
                blocks:[{type:'text',text:'local question'}]},
            {id:'local-split-1',role:'assistant',author:'Neko',createdAt:5001,time:'10:01',status:'sent',
                blocks:[{type:'text',text:'first local bubble'}]},
            {id:'local-split-2',role:'assistant',author:'Neko',createdAt:5002,time:'10:01',status:'sent',
                blocks:[{type:'text',text:'second local bubble'}]},
        ];
        I.setMessages(local);
        const originals=Array.from({length:RECEIVED_COUNT},(_,index)=>({...original,
            messageId:String(index).padStart(32,'0'),text:'received '+index,originalTime:String(1000+index)}));
        global.fetch=async path=>({ok:true,json:async()=>path.endsWith('/status')
            ?{characters:[{displayName:'Neko',characterSyncId:original.characterSyncId,binding:{enabled:true}}]}
            :(reads++,{originals})});
        const flush=()=>new Promise(resolve=>setImmediate(resolve));
        documentEvents.DOMContentLoaded();await flush();
        assert.equal(reads,1);assert.equal(updates,1);
        assert.equal(I.state.messages.length,50);
        assert.equal(I.state.messages[0].blocks[0].text,'received '+(RECEIVED_COUNT-47));
        assert.deepEqual(I.state.messages.slice(-3).map(message=>message.id),
            ['local-user','local-split-1','local-split-2']);
        window._lastSubmittedRequestId='pending-reaction';
        I.updateMessage('local-user',{status:'sent'});
        const target=window.captureMessageReactionTarget('pending-reaction');
        assert.ok(target);
        I.state.galgameOptions=[{id:'keep-options'}];
        const sequence=I.state._galgameRequestSeq;
        const poll=intervals.find(interval=>interval.delay===30000).callback;
        poll();await flush();
        assert.equal(reads,2);assert.equal(updates,1,'unchanged host window must not be replaced');
        assert.equal(I.state._galgameRequestSeq,sequence);
        assert.deepEqual(I.state.galgameOptions,[{id:'keep-options'}]);
        window.applyMessageReactionFromEmotion(target,{reaction:{emoji:'😊',author:'Neko'}});
        assert.equal(I.state.messages.find(message=>message.id==='local-user').reaction.emoji,'😊');
        originals[0].text='changed outside the display window';poll();await flush();
        assert.equal(updates,1);
        originals[RECEIVED_COUNT-1].text='changed visible message';poll();await flush();
        assert.equal(updates,2);
        assert.equal(I.state.messages.at(-4).blocks[0].text,'changed visible message');
        assert.deepEqual(I.state.messages.slice(-3).map(message=>message.blocks[0].text),
            ['local question','first local bubble','second local bubble']);
        const beforeAppend=updates;
        window._lastSubmittedRequestId='new-local-pending';
        I.appendMessage({id:'new-local-user',role:'user',author:'User',createdAt:6000,time:'10:02',status:'sent',
            blocks:[{type:'text',text:'a new local question'}]});
        const appendedTarget=window.captureMessageReactionTarget('new-local-pending');
        assert.ok(appendedTarget);
        I.state.galgameOptions=[{id:'local-post-turn-options'}];
        const appendedSequence=I.state._galgameRequestSeq;
        poll();await flush();
        assert.equal(updates,beforeAppend,'appended local sort keys must not replace unchanged content');
        assert.equal(I.state._galgameRequestSeq,appendedSequence);
        assert.deepEqual(I.state.galgameOptions,[{id:'local-post-turn-options'}]);
        window.applyMessageReactionFromEmotion(appendedTarget,{reaction:{emoji:'😊',author:'Neko'}});
        assert.equal(I.state.messages.at(-1).reaction.emoji,'😊');
        const reorderedId='sync-recent-'+original.characterSyncId+'-'+originals[RECEIVED_COUNT-1].messageId;
        I.updateMessage(reorderedId,{sortKey:-1});
        assert.equal(I.state.messages[0].id,reorderedId);
        poll();await flush();
        assert.equal(updates,beforeAppend+1,'an actual order change must still refresh');
        assert.equal(I.state.messages.at(-5).id,reorderedId);
        assert.deepEqual(I.state.messages.slice(-4).map(message=>message.blocks[0].text),
            ['local question','first local bubble','second local bubble','a new local question']);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('HOST_PARTS', json.dumps(str(ROOT / 'static/app/app-react-chat-window')))
        .replace('RECEIVED_COUNT', str(received_count)))


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


def test_sync_settings_reports_pending_work():
    run_script(r"""(async()=>{
        window.t=(key,values={})=>key+JSON.stringify(values);
        global.fetch=async()=>({ok:true,json:async()=>({characters:[{
            displayName:'Neko',characterSyncId:original.characterSyncId,binding:{pendingOriginals:3,
                pendingDeletions:2}
        }]})});
        await window.nekoChatSync.openSettings();
        const shown=body.children[0].querySelectorAll('*').find(node=>node.className==='chat-sync-status').textContent;
        assert(shown.includes('chatSync.pending')&&shown.includes('"originals":3')&&shown.includes('"deletions":2'));
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


def test_sync_settings_explains_long_term_capacity_without_retrying():
    run_script(r"""(async()=>{
        const requests=[];
        window.t=(key,values={})=>key+JSON.stringify(values);
        global.fetch=async(path)=>{requests.push(path);return{ok:true,json:async()=>({characters:[{
            displayName:'Neko',characterSyncId:original.characterSyncId,
            binding:{lastSyncError:'long_term_memory_capacity_exceeded'}
        }]})};};
        await window.nekoChatSync.openSettings();
        const shown=body.children[0].querySelectorAll('*').find(node=>node.className==='chat-sync-status').textContent;
        assert(shown.includes('chatSync.errorLongTermCapacity'));
        assert(shown.includes('long_term_memory_capacity_exceeded'));
        assert(!shown.includes('errorUnknown'));
        assert.deepEqual(requests,['/api/chat-sync/status']);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """)


@pytest.mark.parametrize('preference_error', [False, True])
@pytest.mark.parametrize('old_type', ['vrm', 'pngtuber'])
def test_late_candidate_role_change_preserves_the_new_role_model_in_chromium(preference_error, old_type):
    from playwright.sync_api import sync_playwright
    source = (ROOT / 'static/app/app-interpage/bootstrap-resources-and-model-reload.js').read_text(encoding='utf-8')
    start = source.index('await _waitForLive2DManagerIdle(30000);', source.index('// Ensure PIXI app is initialised'))
    end = source.index('preparedLive2DModel = null;', start) + len('preparedLive2DModel = null;')
    rollback = source.index('            // 回滚提前写入的 config')
    rollback_body = source[rollback:source.index('            if (!suppressToast) {', rollback)]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<canvas id="live2d-canvas"></canvas>')
        page.evaluate(r"""oldType => {
            window.reloadOptions={preservePreviousUntilLoaded:true};
            window.currentLanlanName='Neko';
            window.lanlan_config={lanlan_name:'Neko',model_type:'live2d',live3d_sub_type:'',pngtuber:{}};
            window.typeChanged=true;
            window.oldModelType=oldType;
            window.oldLive3dSubType='mmd';
            window.pngRestores=0;
            window.loadPNGTuberAvatar=async()=>{pngRestores++;};
            window.newModelPath='/candidate.model3.json';
            window.preparedLive2DModel={id:'candidate'};
            window.skipIdleRestore=false;
            window.skipPersistentExpressions=false;
            window._waitForLive2DManagerIdle=async()=>{};
            window.loads=[];
            window.live2dManager={
                currentModel:'original-role-model',
                loadUserPreferences:()=>new Promise((resolve,reject)=>{window.preferencesReady={resolve,reject};}),
                loadModel:async(path)=>{loads.push(path);live2dManager.currentModel='candidate';}
            };
        }""", old_type)
        page.add_script_tag(content='window.commitCandidate=async function(){try{' + source[start:end] +
                            '}catch(error){' + rollback_body + 'throw error;}};')
        page.evaluate('window.result=commitCandidate().then(()=>"loaded",error=>error.message); void 0')
        page.wait_for_function('!!window.preferencesReady')
        page.evaluate(r"""failed => {
            lanlan_config.lanlan_name='Other';
            live2dManager.currentModel='new-role-model';
            if(failed)preferencesReady.reject(new Error('preferences unavailable'));
            else preferencesReady.resolve([]);
        }""", preference_error)
        assert page.evaluate('result') == 'model_role_changed'
        assert page.evaluate('loads') == []
        assert page.evaluate('live2dManager.currentModel') == 'new-role-model'
        assert page.evaluate('lanlan_config.model_type') == 'live2d'
        assert page.evaluate('lanlan_config.live3d_sub_type') == ''
        assert page.evaluate('pngRestores') == 0
        browser.close()


def test_current_socket_refreshes_recent_memory_without_backfilling_capsule_history():
    """Keep the new recent-memory notification and its socket replacement fence."""
    from tests.unit.test_app_websocket_static_routes import _block_after

    source = (ROOT / "static/app/app-websocket.js").read_text(encoding="utf-8")
    body = _block_after(source, "S.socket.onmessage = function (event) {")
    run_script(r"""
        const calls=[], broadcasts=[], socket={}, S={socket}, _thisSocket=socket;
        window.appInterpage={handleMemoryEdited:async (name,options)=>{calls.push({name,options});}};
        global.BroadcastChannel=class {postMessage(data){broadcasts.push(data);}close(){}};
        window.reactChatWindowHost={getState:()=>({messages:[]}),setMessages(){throw new Error('Capsule history must remain unchanged');}};
        function onmessage(event){BODY}
        const hint={type:'chat_sync_history_changed',characterSyncId:original.characterSyncId,memoryEditedName:'Neko'};
        onmessage({data:JSON.stringify(hint)});
        assert.deepEqual(calls,[{name:'Neko',options:{silent:true}}]);
        assert.deepEqual(broadcasts,[{action:'recent_memory_changed',catgirl_name:'Neko'}]);
        S.socket={};onmessage({data:JSON.stringify(hint)});
        assert.deepEqual(calls,[{name:'Neko',options:{silent:true}}]);
    """.replace("BODY", body))


@pytest.mark.parametrize('silent', [False, True])
@pytest.mark.parametrize('recording', [False, True])
def test_memory_refresh_keeps_context_reset_and_manual_notices(silent, recording):
    """Automatic sync stays quiet while manual edits retain their existing feedback."""
    interpage = ROOT / 'static/app/app-interpage/bootstrap-resources-and-model-reload.js'
    run_script(r"""(async()=>{
        const silent=SILENT,recording=RECORDING,toasts=[],sent=[];
        let stopped=0,cleared=0,reconnected=0;
        global.WebSocket={OPEN:1};
        global.setTimeout=callback=>{callback();};
        window.appState={isRecording:recording,isTextSessionActive:true,
            socket:{readyState:1,send:data=>sent.push(JSON.parse(data))}};
        window.stopMicCapture=options=>{assert.deepEqual(options,{normalEnd:false});stopped++;};
        window.clearAudioQueue=async()=>{cleared++;};
        window.showStatusToast=(message,duration)=>toasts.push({message,duration});
        document.getElementById=id=>id==='micButton'?{click(){reconnected++;}}:null;
        vm.runInThisContext(fs.readFileSync(INTERPAGE,'utf8'));
        const handler=window.__appInterpageParts.handleMemoryEdited;
        if(silent)await handler('Neko',{silent:true});
        else await handler('Neko');
        assert.deepEqual(sent,[{action:'end_session'}]);
        assert.equal(window.appState.isTextSessionActive,false);
        assert.equal(cleared,1);
        assert.equal(stopped,recording?1:0);
        assert.equal(reconnected,recording?1:0);
        assert.deepEqual(toasts,silent?[]:[{message:recording?'memory.refreshingContext':'memory.refreshed',
            duration:recording?3000:4000}]);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('SILENT', json.dumps(silent)).replace('RECORDING', json.dumps(recording))
        .replace('INTERPAGE', json.dumps(str(interpage))))


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
    """A legacy saved origin never reaches recovery; the backend owns the built-in service."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[];let needsRecovery=true;
        global.fetch=async(path,options)=>({ok:true,async json(){
            requests.push({path,body:options.body});
            if(path.endsWith('/status'))return {serviceUrl:'https://localhost:8443',
                writerRecoveryRequired:needsRecovery,
                characters:[{displayName:'Neko',characterSyncId:uid,binding:null}]};
            if(path.endsWith('/recovery/preview')){
                needsRecovery=false;
                return {deletions:{count:0}};
            }
            if(path.endsWith('/history'))return {originals:[]};
            throw new Error('Unexpected request '+path);
        }});
        await window.nekoChatSync.openSettings();
        await body.children[0].querySelector('[data-sync-action="recover"]').events.click();
        assert.deepEqual(requests.filter(item=>item.path.endsWith('/recovery/preview')).map(item=>item.body),[undefined]);
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
            assert.deepEqual(intervals.map(item=>item.delay),[30000]);
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
    assert "{{count}}" in actual["recoveryCloudCopiesKept"]


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
        assert.deepEqual(intervals.map(item=>item.delay),[30000]);
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
                             '<div id="react-chat-window-shell"><button id="tutorial-control">Tutorial</button></div></body>')
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
                page.evaluate("async () => await window.nekoChatSync.openSettings()")
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
    """An old recovery result cannot report its outcome for a newly opened character."""
    run_script(r"""(async()=>{
        const uid='c'.repeat(32),requests=[],prompts=[];let release,changed=false;
        window.confirm=message=>{prompts.push(message);return true;};
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
        release({deletions:{count:1}});
        await pending;
        const shown=modal.querySelectorAll('*').find(node=>node.className==='chat-sync-status').textContent;
        assert(!shown.includes('recoveryChecked')&&!shown.includes('recoveryCloudCopiesKept'),shown);
        assert.deepEqual(prompts,[]);
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


@pytest.mark.parametrize("kept", [0, 2])
def test_writer_recovery_reports_kept_deletions_without_prompting_or_history_controls(kept):
    """Recovery shows its result without a confirmation dialog, an approval request or history controls."""
    from playwright.sync_api import sync_playwright

    labels = json.loads((ROOT / "static/locales/en.json").read_text(encoding="utf-8"))["chatSync"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", lambda route: route.abort())
            page.set_content('<body></body>')
            page.add_style_tag(path=str(ROOT / "static/css/chat_sync.css"))
            page.evaluate("""({labels, kept}) => {
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
                            return {deletions: {count: kept}};
                        }
                        if (path.endsWith('/history')) return {originals: []};
                        throw new Error('Unexpected request: ' + path);
                    }};
                };
            }""", {"labels": labels, "kept": kept})
            page.add_script_tag(path=str(SCRIPT))
            page.evaluate("async () => await window.nekoChatSync.openSettings()")
            modal = page.locator("#chat-sync-dialog")
            assert modal.locator('[data-sync-action]').count() == 1
            assert modal.locator('[data-sync-action="recover"]').is_enabled()
            assert modal.locator('[data-history-action], details').count() == 0
            assert page.evaluate("window.requests.every(item => item.body === undefined)")
            dialogs = []

            def record(dialog):
                dialogs.append(dialog.message)
                dialog.dismiss()

            page.on("dialog", record)
            modal.get_by_role("button", name=labels["recoverWriter"], exact=True).click()
            modal.get_by_role("status").filter(has_text=labels["recoveryChecked"]).wait_for()
            expected = labels["recoveryChecked"]
            if kept:
                expected += "\n" + labels["recoveryCloudCopiesKept"].replace("{{count}}", str(kept))
            assert modal.get_by_role("status").text_content() == expected
            # The stylesheet keeps each status sentence on its own rendered line.
            assert modal.get_by_role("status").inner_text() == expected
            assert dialogs == []
            assert page.evaluate("window.requests.filter(item => item.path.endsWith('/recovery/preview')).map(item => item.body === undefined)") == [True]
            assert page.evaluate("window.requests.some(item => item.path.endsWith('/approve'))") is False
            assert page.evaluate("window.requests.some(item => ['/history/approve','/sync-choice','/enable','/sync'].some(suffix => item.path.endsWith(suffix)))") is False
            assert modal.locator('[data-sync-action="recover"]').is_disabled()
        finally:
            browser.close()


@pytest.mark.parametrize('busy', ['text', 'queue', 'audio'])
def test_received_memory_refresh_waits_for_existing_turn_and_playback(busy):
    run_script(r"""(async()=>{
        let refreshed=0;
        window.appState={assistantTurnId:'turn',assistantTurnCompletedId:'turn'};
        window.appInterpage={handleMemoryEdited:async(name,options)=>{
            assert.equal(name,'Neko');assert.equal(options.silent,true);refreshed++;
        }};
        window.reactChatWindowHost={getState:()=>({messages:[]})};
        if(BUSY==='text')window.appState.assistantTurnAwaitingBubble=true;
        if(BUSY==='queue')window._isProcessingRealisticQueue=true;
        if(BUSY==='audio')window.NekoSpeechPlaybackState={active:true};
        window.nekoChatSync.requestMemoryRefresh('Neko');
        assert.equal(refreshed,0);
        windowEvents['neko-assistant-turn-end']();
        assert.equal(refreshed,0);
        window.appState.assistantTurnAwaitingBubble=false;
        window._isProcessingRealisticQueue=false;
        window.NekoSpeechPlaybackState={active:false};
        windowEvents[BUSY==='audio'?'neko-speech-playback-state':'neko-realistic-queue-idle']();
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(refreshed,1);
        window.nekoChatSync.requestMemoryRefresh('Other');
        assert.equal(refreshed,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('BUSY', json.dumps(busy)))


@pytest.mark.parametrize('busy', ['isRecording', 'voiceChatActive', 'voiceStartPending', 'isMicStarting'])
def test_received_memory_refresh_waits_for_the_voice_session_to_end(busy):
    run_script(r"""(async()=>{
        let refreshed=0;
        window.appState={userRecentSpeechTime:Date.now()-800};
        if(BUSY==='isMicStarting')window.isMicStarting=true;
        else window.appState[BUSY]=true;
        window.reactChatWindowHost={getState:()=>({messages:[]})};
        window.appInterpage={handleMemoryEdited:async()=>{refreshed++;}};
        window.nekoChatSync.requestMemoryRefresh('Neko');
        window.nekoChatSync.requestMemoryRefresh('Neko');
        windowEvents['voice-input-lifecycle-changed']();
        assert.equal(refreshed,0);
        window.isMicStarting=false;
        window.appState.isRecording=false;
        window.appState.voiceChatActive=false;
        window.appState.voiceStartPending=false;
        windowEvents['voice-input-lifecycle-changed']();
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(refreshed,1);
    })().catch(error=>{console.error(error);process.exitCode=1;});
    """.replace('BUSY', json.dumps(busy)))
