import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {test} from 'node:test';
let completed = false;
process.on('exit', () => {
    if (!completed) {
        console.error('Screen inspection scenarios did not finish');
        process.exitCode = 1;
    }
});
const code = await readFile(new URL('../../static/app/app-screen-inspection.js', import.meta.url),'utf8');
const {handleScreenInspection: run} = await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
global.WebSocket = {OPEN:1};
function fixture() {
    const sent=[], captures=[];
    const state={selectedScreenSourceId:null, proactiveVisionEnabled:true};
    const socket={readyState:1,send:s=>sent.push(JSON.parse(s))}; state.socket=socket;
    global.window={appProactive:{isProactiveVisionEnabledNow:()=>state.proactiveVisionEnabled},
        getAvatarScreenPosition:()=>({centerX:.9,centerY:.8,width:.1,height:.2}),
        electronDesktopCapturer:{sourceEnumerationMayPrompt:false,getSources:async()=>[
            {id:'screen:1',name:'Screen'}, {id:'window:2',name:'Chrome'}, {id:'window:3',name:'Music'}],
            captureSourceAsDataUrl:async id=>{captures.push(id);return {success:true,dataUrl:'data:image/png;base64,test'};}}};
    return {state,socket,sent,captures};
}
{
    const f=fixture(); await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.deepEqual(f.captures,['screen:1']); assert.equal(f.sent[0].windows.length,2);
    assert.ok(f.sent[0].avatar_position); assert.equal(f.state.selectedScreenSourceId,null);
    await run({operation:'window',scope:'desktop',window_id:'window:3',request_id:'2'},f.state,f.socket);
    assert.equal(f.captures[1],'window:3'); assert.equal(f.sent[1].title,'Music');
    assert.equal(f.state.selectedScreenSourceId,null); assert.equal(f.sent[1].avatar_position,null);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.deepEqual(f.captures,['window:2']); assert.equal(f.sent[0].windows.length,1);
    await run({operation:'window',scope:f.sent[0].scope,window_id:'window:3',request_id:'2'},f.state,f.socket);
    assert.equal(f.sent[1].success,false); assert.equal(f.captures.length,1);
}
{
    const f=fixture(); f.state.proactiveVisionEnabled=false;
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.proactiveVisionEnabled=false; return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false); assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    await run({operation:'validate',scope:'desktop',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false);
}
{
    const f=fixture(); window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>({success:false});
    await run({operation:'window',scope:'desktop',window_id:'window:3',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false); assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); const replacement={readyState:1,send:()=>assert.fail('replacement socket received old frame')};
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.socket=replacement; return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'old'},f.state,f.socket);
    assert.equal(f.sent.length,0);
}
{
    const f=fixture(); window.prepareRememberedWindowCapture=async()=>({required:true,allowed:false});
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); window.prepareRememberedWindowCapture=async()=>({required:true,allowed:true,isCurrent:()=>true});
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2'; let current=true;
    window.prepareRememberedWindowCapture=async()=>({required:true,allowed:true,isCurrent:()=>current});
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{current=false;return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed'); assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); window.electronDesktopCapturer.sourceEnumerationMayPrompt=true;
    window.electronDesktopCapturer.getSources=async()=>assert.fail('must not open a portal');
    await run({operation:'window',scope:'desktop',window_id:'window:3',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false);assert.equal(f.captures.length,0);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    window.electronDesktopCapturer.getSources=async()=>[{id:'window:3',name:'Music'}];
    await run({operation:'validate',request_id:'scope'},f.state,f.socket);
    await run({operation:'window',scope:f.sent[0].scope,window_id:'window:2',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[1].error,'window_unavailable'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); window.isNekoHomeTutorialProactiveSuppressed=()=>true;
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'disabled'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); let paused=false,drawn=false;
    const track={id:'browser-tab',getSettings:()=>({displaySurface:'browser'}),stop:()=>assert.fail('shared track stopped')};
    f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
    const video={videoWidth:2048,videoHeight:1200,play:async()=>{},pause:()=>{paused=true;}};
    const canvas={getContext:()=>({drawImage:()=>{drawn=true;}}),toDataURL:()=> 'data:image/png;base64,stream'};
    global.document={createElement:name=>name==='video'?video:canvas};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,true);assert.match(f.sent[0].scope,/^stream:/);
    assert.equal(f.sent[0].avatar_position,null);assert.deepEqual(f.sent[0].windows,[]);
    assert.equal(canvas.width,2048);assert.equal(canvas.height,1200);assert.ok(paused&&drawn);assert.equal(video.srcObject,null);
    assert.equal(f.captures.length,0);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='screen:1';
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.selectedScreenSourceId='window:2';return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed');assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>({success:true,dataUrl:'x'.repeat(12*1024*1024+1)});
    await run({operation:'overview',request_id:'large'},f.state,f.socket);
    assert.equal(f.sent[0].error,'invalid_image');assert.equal(f.sent[0].data_url,undefined);
}
console.log('Frontend: 16 scenarios passed');
// Legacy Linux bridges must use the shared portal-risk inference.
{
    const f=fixture(); delete window.electronDesktopCapturer.sourceEnumerationMayPrompt;
    const helpers=await readFile(new URL('../../static/app/desktop-capture-provider.js',import.meta.url),'utf8');
    const savedNavigator=Object.getOwnPropertyDescriptor(globalThis,'navigator');
    Object.defineProperty(globalThis,'navigator',{configurable:true,value:{userAgent:'Linux'}});
    try {
        (0,eval)(helpers);
        window.electronDesktopCapturer.getSources=async()=>assert.fail('legacy portal enumeration');
        window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>assert.fail('legacy portal capture');
        window.appProactive.captureProactiveChatScreenshotWithSource=async()=>assert.fail('portal fallback');
        await run({operation:'overview',request_id:'legacy-linux'},f.state,f.socket);
        assert.equal(f.sent[0].success,false);
    } finally {
        if(savedNavigator) Object.defineProperty(globalThis,'navigator',savedNavigator);
        else delete globalThis.navigator;
    }
}
// A restricted browser stream must win over a stale selected monitor ID.
{
    const f=fixture(); f.state.selectedScreenSourceId='screen:1';
    const track={id:'restricted',getSettings:()=>({displaySurface:'browser'})};
    f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
    const video={videoWidth:64,videoHeight:64,play:async()=>{},pause(){}};
    global.document={createElement:name=>name==='video'?video:{getContext:()=>({drawImage(){}}),toDataURL:()=> 'stream-pixels'}};
    await run({operation:'overview',request_id:'restricted'},f.state,f.socket);
    assert.equal(f.sent[0].data_url,'stream-pixels');assert.deepEqual(f.captures,[]);
    assert.deepEqual(f.sent[0].windows,[]);
}
// Unnamed monitor streams have different scopes, even when track IDs are reused.
{
    const f=fixture();
    const track={id:'same',getSettings:()=>({displaySurface:'monitor'})};
    const stream=()=>({active:true,getVideoTracks:()=>[track]});
    f.state.screenCaptureStream=stream();
    await run({operation:'validate',request_id:'before'},f.state,f.socket);
    f.state.screenCaptureStream=stream();
    await run({operation:'validate',request_id:'after',scope:f.sent[0].scope},f.state,f.socket);
    assert.equal(f.sent[1].error,'scope_changed');
}
// Stream replacement during capture is rejected even with the same selected ID.
{
    const f=fixture(); f.state.selectedScreenSourceId='screen:1';
    const track={id:'monitor',getSettings:()=>({displaySurface:'monitor'})};
    f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
        return {success:true,dataUrl:'old-pixels'};
    };
    await run({operation:'overview',request_id:'replace-stream'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed');assert.equal(f.sent[0].data_url,undefined);
}
// A proven window stream can be reused without native capture or a new picker.
for (const bound of [true,false]) {
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    window.electronDesktopCapturer.sourceEnumerationMayPrompt=true;
    const track={id:'window',getSettings:()=>({displaySurface:'window'})};
    f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
    window.getScreenCaptureStreamSourceId=()=>bound?'window:2':undefined;
    const video={videoWidth:64,videoHeight:64,play:async()=>{},pause(){}};
    global.document={createElement:name=>name==='video'?video:{getContext:()=>({drawImage(){}}),toDataURL:()=> 'window-pixels'}};
    await run({operation:'overview',request_id:'bound-stream'},f.state,f.socket);
    assert.equal(f.sent[0].success,bound);assert.deepEqual(f.captures,[]);
}
// A blocked video play has a deadline, detaches the video, and ignores late completion.
{
    const f=fixture(); let resume, paused=false, drawn=false;
    const track={id:'slow',getSettings:()=>({displaySurface:'browser'})};
    f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
    const video={videoWidth:64,videoHeight:64,play:()=>new Promise(resolve=>{resume=resolve;}),pause(){paused=true;}};
    global.document={createElement:name=>name==='video'?video:{getContext:()=>({drawImage(){drawn=true;}})}};
    const realSetTimeout=globalThis.setTimeout;
    globalThis.setTimeout=(callback)=>realSetTimeout(callback,10);
    try { await run({operation:'overview',request_id:'slow'},f.state,f.socket); }
    finally { globalThis.setTimeout=realSetTimeout; }
    assert.equal(f.sent[0].error,'timeout');assert.ok(paused);assert.equal(video.srcObject,null);
    resume();await Promise.resolve();assert.equal(drawn,false);assert.equal(f.sent.length,1);
}
console.log('Frontend permission regression scenarios passed');

// Load the real source-selection and manual-sharing module for cross-module contracts.
const appScreenCode = await readFile(new URL('../../static/app/app-screen.js', import.meta.url), 'utf8');
const providerCode = await readFile(new URL('../../static/app/desktop-capture-provider.js', import.meta.url), 'utf8');
function sharingFixture({prompting = false, storage = {}, sources} = {}) {
    const f = fixture();
    const values = new Map(Object.entries(storage));
    const nodes = new Map(['screenButton', 'micButton', 'muteButton', 'stopButton', 'resetSessionButton'].map(id => {
        const classes = new Set();
        return [id, {disabled: true, classList: {
            add: name => classes.add(name), remove: name => classes.delete(name),
            contains: name => classes.has(name), toggle: (name, on) => on ? classes.add(name) : classes.delete(name),
        }}];
    }));
    const document = {
        getElementById: id => nodes.get(id) || null,
        querySelectorAll: () => [], querySelector: () => null,
        createElement: name => name === 'video'
            ? {videoWidth: 640, videoHeight: 480, play: async () => {}, pause() {}}
            : {getContext: () => ({drawImage() {}}), toDataURL: () => 'data:image/png;base64,shared-pixels'},
    };
    const localStorage = {
        getItem: key => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, String(value)),
        removeItem: key => values.delete(key),
    };
    const provider = window.electronDesktopCapturer;
    f.sources = sources || [{id: 'screen:1', name: 'Screen'}, {id: 'window:2', name: 'Editor'}];
    f.enumerations = [];
    provider.sourceEnumerationMayPrompt = prompting;
    provider.getSources = async options => {
        f.enumerations.push(options);
        assert.equal(provider.sourceEnumerationMayPrompt, false, 'inspection must not open a portal');
        return f.sources.filter(source => options.types.includes(source.id.split(':')[0]));
    };
    provider.setSelectedSource = () => {};
    Object.assign(f.state, {screenCaptureStream: null, audioPlayerContext: {state: 'running'},
        isRecording: false, voiceChatActive: true});
    Object.assign(window, {appState: f.state, appConst: {}, appUtils: {isMobile: () => false},
        safeT: (_key, fallback) => fallback, t: key => key, showStatusToast() {},
        addEventListener() {}, dispatchEvent() {}, document});
    const mediaDevices = {
        getUserMedia: async () => assert.fail('unexpected stream acquisition'),
        getDisplayMedia: async () => assert.fail('must not widen capture through a new picker'),
    };
    const context = vm.createContext({window, document, localStorage,
        navigator: {userAgent: prompting ? 'Linux' : 'Windows', mediaDevices},
        console: {log() {}, warn() {}, error: (...args) => f.errors.push(args)},
        CustomEvent: class {}, WebSocket: global.WebSocket,
        setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}});
    f.errors = [];
    vm.runInContext(providerCode, context, {filename: 'desktop-capture-provider.js'});
    vm.runInContext(appScreenCode, context, {filename: 'app-screen.js'});
    global.document = document;
    return Object.assign(f, {window, provider, mediaDevices, values, nodes});
}
function sharedStream(displaySurface) {
    const track = {readyState: 'live', getSettings: () => ({displaySurface}),
        addEventListener() {},
        stop() { this.readyState = 'ended'; }};
    return {active: true, getVideoTracks: () => [track], getTracks: () => [track]};
}

for (const surface of ['window', 'browser', 'monitor']) {
    await test(`picker fallback cannot inspect the stale native window: ${surface}`, async () => {
        const f = sharingFixture();
        await f.window.selectScreenSource('window:2', 'Editor');
        const stream = sharedStream(surface);
        const acquisitions = [];
        f.mediaDevices.getUserMedia = async options => {
            acquisitions.push(options.video.mandatory.chromeMediaSourceId);
            throw new Error('native capture unavailable');
        };
        let prompts = 0;
        f.mediaDevices.getDisplayMedia = async () => { prompts++; return stream; };
        assert.equal(await f.window.acquireOrReuseCachedStream({allowPrompt: true}), stream);
        assert.deepEqual(acquisitions, ['window:2']);
        assert.equal(prompts, 1);
        assert.equal(f.state.selectedScreenSourceId, 'window:2');
        assert.equal(f.window.getScreenCaptureStreamSourceId(stream), undefined);
        const enumerations = f.enumerations.length;
        for (const operation of ['overview', 'window', 'validate']) {
            await run({operation, window_id: 'window:2', request_id: operation}, f.state, f.socket);
            assert.equal(f.sent.at(-1).error, 'scope_changed');
            assert.equal(f.sent.at(-1).data_url, undefined);
            assert.equal(f.sent.at(-1).windows, undefined);
        }
        assert.deepEqual(f.captures, [], 'inspection must not capture the formerly selected window');
        assert.equal(f.enumerations.length, enumerations, 'inspection does not enumerate stale sources');
        assert.equal(prompts, 1, 'inspection does not prompt for another source');
        assert.equal(f.state.screenCaptureStream, stream);
        assert.equal(stream.getVideoTracks()[0].readyState, 'live');
    });
}
await test('a bound native window stream still permits read-only inspection', async () => {
    const f = sharingFixture();
    await f.window.selectScreenSource('window:2', 'Editor');
    const stream = sharedStream('window');
    f.mediaDevices.getUserMedia = async () => stream;
    assert.equal(await f.window.acquireOrReuseCachedStream({allowPrompt: true}), stream);
    assert.equal(f.window.getScreenCaptureStreamSourceId(stream), 'window:2');
    await run({operation: 'overview', request_id: 'bound-native'}, f.state, f.socket);
    assert.equal(f.sent.at(-1).success, true);
    assert.deepEqual(f.captures, ['window:2']);
    assert.equal(f.sent.at(-1).source_id, 'window:2');
    assert.equal(stream.getVideoTracks()[0].readyState, 'live');
});

for (const id of ['window:2', 'screen:1']) {
    await test(`forced reselection invalidates persisted ${id} scopes`, async () => {
        const f = sharingFixture();
        await f.window.selectScreenSource(id, 'Original');
        await run({operation: 'overview', request_id: 'before'}, f.state, f.socket);
        const scope = f.sent.at(-1).scope;
        assert.equal(f.sent.at(-1).success, true);
        await run({operation: 'validate', scope, request_id: 'unchanged'}, f.state, f.socket);
        assert.equal(f.sent.at(-1).success, true, 'an unchanged selection remains valid');
        await f.window.selectScreenSource(id, 'Replacement', undefined, undefined, {force: true});
        const captures = f.captures.length;
        for (const operation of ['validate', 'window']) {
            await run({operation, scope, window_id: 'window:2', request_id: operation}, f.state, f.socket);
            assert.equal(f.sent.at(-1).error, 'scope_changed', 'old pixels must not survive a same-ID source switch');
            assert.equal(f.sent.at(-1).data_url, undefined);
        }
        assert.equal(f.captures.length, captures);
        await run({operation: 'overview', request_id: 'fresh'}, f.state, f.socket);
        assert.equal(f.sent.at(-1).success, true);
        assert.notEqual(f.sent.at(-1).scope, scope);
    });
}
await test('forced same-ID selection rejects a native frame already in flight', async () => {
    const f = sharingFixture();
    await f.window.selectScreenSource('window:2', 'Editor');
    let release, started;
    const capturing = new Promise(resolve => { started = resolve; });
    f.provider.captureSourceAsDataUrl = () => {
        started();
        return new Promise(resolve => { release = resolve; });
    };
    const pending = run({operation: 'overview', request_id: 'pending'}, f.state, f.socket);
    await capturing;
    await f.window.selectScreenSource('window:2', 'Replacement', undefined, undefined, {force: true});
    release({success: true, dataUrl: 'old-pixels'});
    await pending;
    assert.equal(f.sent.at(-1).error, 'scope_changed');
    assert.equal(f.sent.at(-1).data_url, undefined);
});
await test('manual portal sharing binds the authorized window stream for inspection', async () => {
    const f = sharingFixture({prompting: true});
    await f.window.selectScreenSource('window:2', 'Editor');
    const stream = sharedStream('window');
    f.mediaDevices.getUserMedia = async options => {
        assert.equal(options.video.mandatory.chromeMediaSourceId, 'window:2');
        return stream;
    };
    f.state.isRecording = true;
    await f.window.startScreenSharing();
    assert.equal(f.state.screenCaptureStream, stream);
    assert.equal(f.nodes.get('stopButton').disabled, false);
    assert.deepEqual(f.errors, []);
    assert.equal(f.window.getScreenCaptureStreamSourceId(stream), 'window:2');
    await run({operation: 'overview', request_id: 'manual'}, f.state, f.socket);
    assert.equal(f.sent.at(-1).success, true);
    assert.equal(f.sent.at(-1).data_url, 'data:image/png;base64,shared-pixels');
    assert.equal(f.sent.at(-1).source_id, 'window:2', 'source IDs remain separate from versioned scope keys');
    assert.equal(f.sent.at(-1).avatar_position, null);
    assert.deepEqual(f.enumerations, []);
    assert.deepEqual(f.captures, []);
    assert.equal(stream.getVideoTracks()[0].readyState, 'live');
});
await test('manual non-remembered fallback binds the actual monitor source', async () => {
    const f = sharingFixture();
    await f.window.selectScreenSource('window:2', 'Editor');
    const stream = sharedStream('monitor');
    const acquired = [];
    f.mediaDevices.getUserMedia = async options => {
        const id = options.video.mandatory.chromeMediaSourceId;
        acquired.push(id);
        if (id === 'window:2') throw new Error('window capture unavailable');
        return stream;
    };
    f.state.isRecording = true;
    await f.window.startScreenSharing();
    assert.deepEqual(acquired, ['window:2', 'screen:1']);
    assert.equal(f.state.selectedScreenSourceId, 'screen:1');
    assert.equal(f.state.screenCaptureStream, stream);
    assert.deepEqual(f.errors, []);
    assert.equal(f.window.getScreenCaptureStreamSourceId(stream), 'screen:1');
});
for (const scenario of [
    {name: 'explicit duplicate title', explicit: true, duplicate: true, remembered: 'editor', allowed: true},
    {name: 'restored duplicate title', explicit: false, duplicate: true, remembered: 'editor', allowed: false},
    {name: 'explicit different remembered title', explicit: true, duplicate: true, remembered: 'music', allowed: false},
    {name: 'restored unique title', explicit: false, duplicate: false, remembered: 'editor', allowed: true},
]) {
    await test(`read-only remembered capture: ${scenario.name}`, async () => {
        const sources = [{id: 'window:2', name: ' Editor '}, {id: 'window:4', name: 'Music'}];
        if (scenario.duplicate) sources.push({id: 'window:3', name: 'editor'});
        const f = sharingFixture({sources, storage: {selectedScreenSourceId: 'window:2',
            screenSourceTitleMatchEnabled: 'true', selectedScreenWindowTitle: scenario.remembered}});
        if (scenario.explicit) await f.window.selectScreenSource('window:2', ' Editor ');
        // A changed remembered title must not inherit permission from an explicit but different title.
        f.values.set('selectedScreenWindowTitle', scenario.remembered);
        const before = [...f.values];
        await run({operation: 'overview', request_id: scenario.name}, f.state, f.socket);
        assert.equal(f.sent.at(-1).success, scenario.allowed);
        assert.equal(f.state.selectedScreenSourceId, 'window:2', 'inspection cannot remap the selected source');
        assert.deepEqual([...f.values], before, 'read-only validation cannot rewrite remembered settings');
        assert.deepEqual(f.captures, scenario.allowed ? ['window:2'] : []);
        if (!scenario.allowed) assert.equal(f.sent.at(-1).error, 'scope_changed');
    });
}
completed = true;
