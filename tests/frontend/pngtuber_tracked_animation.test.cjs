const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/pngtuber-core.js'), 'utf8');

function fixture({ fakeTimers = false } = {}) {
    let clock = 100, next = 0;
    let fetchImplementation = () => Promise.reject(Error("unexpected fetch"));
    const callbacks = new Map(), images = [];
    const timers = new Map();
    let timerId = 10000;
    const timerSet = fakeTimers ? (fn, delay = 0) => {
        const id = ++timerId;
        timers.set(id, { at: clock + Math.max(0, Number(delay) || 0), fn });
        return id;
    } : setTimeout;
    const timerClear = fakeTimers ? id => timers.delete(id) : clearTimeout;
    const advance = value => {
        if (!fakeTimers) { clock = value; return; }
        assert.ok(value >= clock, 'the shared test clock cannot move backwards');
        let iterations = 0;
        while (true) {
            const due = [...timers].filter(([, timer]) => timer.at <= value).sort((a, b) => a[1].at - b[1].at)[0];
            if (!due) break;
            assert.ok(++iterations < 10000, 'timer callback loop');
            clock = due[1].at;
            timers.delete(due[0]);
            due[1].fn();
        }
        clock = value;
    };
    class Image {
        removeAttribute() {}
        set src(value) { this.url = value; images.push(this); }
    }
    const window = { location: { pathname: '/' }, lanlan_config: {model_type: 'pngtuber'}, dispatchEvent: () => {} };
    vm.runInNewContext(source, { window, document: { getElementById: () => null, createElement: () => ({ getContext: () => context }), body: { classList: { contains: () => false } } },
        console, Image, fetch: (...args) => fetchImplementation(...args), Date: class extends Date { static now() { return 1_000_000 + clock; } }, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } }, performance: { now: () => clock },
        requestAnimationFrame: fn => { callbacks.set(++next, fn); return next; },
        cancelAnimationFrame: id => callbacks.delete(id), setTimeout: timerSet, clearTimeout: timerClear });
    const manager = new window.PNGTuberManager();
    manager.layeredImages = new Map([[0, { name: 'body' }], [1, { name: 'mouth' }]]);
    const metadata = { tracked_animation: { duration_ms: 2000, mouth_crop: [0, 0, 30, 20],
        frames: Array.from({ length: 20 }, (_, i) => ({ time_ms: i * 100, duration_ms: 100,
            image: `frames/${i}.png`, x: 20, y: 25 })) } };
    manager.initializeTrackedPlayback(metadata, '/model/meta.json');
    const context = new Proxy({}, { get: () => () => {} });
    manager.layeredAnimationStart = 100;
    manager.startTalkingHopAnimation = () => {};
    manager.stopTalkingHopAnimation = () => {};
    manager.syncLayeredSpeechSlot = () => {};
    manager.setState = () => {};
    return { setFetch: fn => { fetchImplementation = fn; }, window, manager, metadata, context, callbacks, images, wallNow: () => 1_000_000 + clock, timers, advance };
}

test('rejects a discontinuous frame clock before starting playback', () => {
    const { manager, metadata } = fixture();
    metadata.tracked_animation.frames[1].time_ms = 101;
    assert.throws(() => manager.initializeTrackedPlayback(metadata, '/model/meta.json'), /invalid tracked animation frame/);
});

test('real analyser amplitude drives continuous opening and silence closes it', () => {
    const { manager, callbacks } = fixture();
    let amplitude = 145;
    const analyser = { fftSize: 32, getByteTimeDomainData: data => data.fill(amplitude) };
    assert.equal(manager.startLipSync(analyser), true);
    let id = manager.lipSyncFrame;
    callbacks.get(id)(116);
    assert.ok(manager.lipSyncMouthOpen > 0.4 && manager.lipSyncMouthOpen < 1);
    assert.equal(manager.speakingMouthOpen, true);
    amplitude = 128;
    for (let i = 0; i < 20; i++) { id = manager.lipSyncFrame; callbacks.get(id)(132 + i * 16); }
    assert.ok(manager.lipSyncMouthOpen < 0.001);
    assert.equal(manager.speakingMouthOpen, false);
    manager.stopLipSync();
    assert.equal(manager.lipSyncFrame, null);
    assert.equal(manager.lipSyncMouthOpen, 0);
});

test('speech cancellation releases the mouth gradually without restarting body time', () => {
    const { manager, context } = fixture();
    manager._trackedPlayback.openness = 1;
    manager._trackedPlayback.lastDraw = 100;
    manager.isSpeaking = false;
    manager.drawTrackedPlayback(context, 116);
    assert.ok(manager._trackedPlayback.openness > 0 && manager._trackedPlayback.openness < 1);
    for (let time = 132; time < 1000; time += 16) manager.drawTrackedPlayback(context, time);
    assert.ok(manager._trackedPlayback.openness < 0.001);
    assert.equal(manager.layeredAnimationStart, 100);
});

test('decoded body frames use a bounded cache and stale loads cannot populate a replacement model', async () => {
    const { manager, images, metadata } = fixture();
    for (let i = 1; i < 20; i++) {
        manager.requestTrackedFrame(i);
        images.at(-1).onload();
        await new Promise(resolve => setImmediate(resolve));
    }
    assert.equal(manager._trackedPlayback.cache.size, 12);
    manager.requestTrackedFrame(1);
    const stale = images.at(-1), old = manager._trackedPlayback;
    manager.initializeTrackedPlayback(metadata, '/replacement/meta.json');
    stale.onload();
    await new Promise(resolve => setImmediate(resolve));
    assert.notEqual(manager._trackedPlayback, old);
    assert.equal(manager._trackedPlayback.cache.size, 1);
});


test('idle loop skips settle/hold and joins overlap end to the next cycle without a time jump', () => {
    const { manager } = fixture();
    const data = { duration_ms: 8000, loop: { end_ms: 7000, crossfade_ms: 250 },
        frames: Array.from({ length: 128 }, (_, i) => ({ time_ms: i * 62.5, duration_ms: 62.5 })) };
    for (let elapsed = 0; elapsed < 20000; elapsed += 10) {
        const selection = manager.trackedLoopSelection(data, elapsed);
        assert.ok(data.frames[selection.index].time_ms < 7000);
        assert.ok(selection.mix >= 0 && selection.mix <= 1);
    }
    const before = manager.trackedLoopSelection(data, 6749.99);
    const after = manager.trackedLoopSelection(data, 6750);
    assert.ok(before.mix > 0.999);
    assert.equal(after.mix, 0);
    assert.ok(Math.abs(data.frames[before.blendIndex].time_ms - data.frames[after.index].time_ms) <= 62.5);
});

test('loop blending defaults to the original full duration for other tracked animations', () => {
    const { manager, metadata } = fixture();
    const data = metadata.tracked_animation;
    assert.equal(manager.trackedLoopSelection(data, 1999).index, 19);
    assert.equal(manager.trackedLoopSelection(data, 2000).index, 0);
    assert.equal(manager.trackedLoopSelection(data, 1999).blendIndex, null);
});


test('nonzero loop entry keeps the tail join on the selected matching frame', () => {
    const { manager } = fixture();
    const data = { duration_ms: 7999, loop: { start_ms: 312, end_ms: 6500, crossfade_ms: 62 },
        frames: Array.from({ length: 125 }, (_, i) => ({ time_ms: Math.floor(i * 62.5), duration_ms: Math.floor((i + 1) * 62.5) - Math.floor(i * 62.5) })) };
    const first = manager.trackedLoopSelection(data, 0);
    const before = manager.trackedLoopSelection(data, 6125.999);
    const after = manager.trackedLoopSelection(data, 6126);
    assert.equal(first.index, 5);
    assert.equal(before.index, 103);
    assert.equal(before.blendIndex, 5);
    assert.equal(after.index, 5);
    assert.ok(before.mix > 0.999);
    for (let elapsed = 0; elapsed < 20000; elapsed += 15) {
        const point = manager.trackedLoopSelection(data, elapsed);
        assert.ok(point.index >= 5 && point.index <= 103);
    }
});

function actionFixture(options = {}) {
    const f = fixture(options), m = f.manager;
    const idle = f.metadata.tracked_animation;
    const action = { duration_ms: 80, playback: 'once', lipsync: false, width: 480, height: 640,
        frames: [{time_ms: 0, duration_ms: 80, image: 'angry/0.png', x: 0, y: 0}] };
    f.metadata.canvas = { width: 576, height: 768 };
    f.metadata.state_count = 2;
    f.metadata.state_catalog = [{name: 'idle', state_index: 0}, {name: 'angry', state_index: 1}];
    f.metadata.tracked_animations = { 0: idle, 1: action };
    f.metadata.emotion_mappings = { angry: 1 };
    m.layeredMetadata = f.metadata;
    m.config = {adapter: 'layered_canvas_v1', layered_metadata: '/model/meta.json', state_priority: 'emotion'};
    m.drawLayeredState = () => {};
    m.restartLayeredAnimationLoop = () => { m.layeredAnimationStart = 100; };
    return {...f, action};
}

test('an emotion selects its action timeline and expires back to idle', async () => {
    const {manager: m, action} = actionFixture();
    assert.equal(m.setEmotion('angry'), true);
    assert.equal(m.layeredStateIndex, 1);
    assert.equal(m._trackedPlayback.data, action);
    assert.equal(m._trackedPlayback.cache.size, 0);
    await new Promise(resolve => setTimeout(resolve, 110));
    assert.equal(m.layeredStateIndex, 0);
    assert.equal(m.currentEmotion, null);
    assert.equal(m._trackedPlayback.data, m.layeredMetadata.tracked_animation);
});

test('one-shot action holds its last frame, returns to idle, and explicit hold overrides auto-return', async () => {
    const {manager: m, action} = actionFixture();
    assert.equal(m.trackedLoopSelection(action, 5000).index, 0);
    assert.equal(m.playLayeredAnimation('angry'), true);
    await new Promise(resolve => setTimeout(resolve, 110));
    assert.equal(m.layeredStateIndex, 0);
    m.playLayeredAnimation('angry', { returnToDefaultAfterMs: 0 });
    assert.equal(m.layeredStateReturnTimer, null);
    assert.equal(m.layeredStateIndex, 1);
});

test('emotion change during speech preserves speech and stale frame loads cannot replace new action', async () => {
    const {manager: m, images} = actionFixture();
    m.isSpeaking = true;
    m.lipSyncMouthOpen = 0.65;
    m.setEmotion('angry', { durationMs: 0 });
    m.requestTrackedFrame(0);
    const stale = images.at(-1);
    m.clearEmotion();
    stale.onload();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(m.layeredStateIndex, 0);
    assert.equal(m.isSpeaking, true);
    assert.equal(m.lipSyncMouthOpen, 0.65);
    assert.equal(m._trackedPlayback.cache.size, 1);
    assert.equal(m.setEmotion('unknown'), false);
    assert.equal(m.layeredStateIndex, 0);
});

test('untracked action mouths are never overpainted and action geometry scales to model canvas', () => {
    const {manager: m, images} = actionFixture();
    m.playLayeredAnimation('angry', {returnToDefaultAfterMs: 0});
    const calls = [];
    const ctx = new Proxy({}, {get: (_, key) => (...args) => calls.push([key, ...args])});
    m.drawTrackedMouth(ctx, {}, {}, 1);
    assert.equal(calls.length, 0);
    m._trackedPlayback.cache.set(0, {width: 480, height: 640});
    m.drawTrackedPlayback(ctx, 116);
    assert.deepEqual(calls.find(call => call[0] === 'scale'), ['scale', 1.2, 1.2]);
});

test('model scale limits clamp restored, desktop and mobile values without changing offsets', () => {
    const {manager: m, metadata} = fixture();
    m.layeredMetadata = metadata;
    metadata.scale_limits = { min: 0.1, max: 1 };
    m.config = {scale: 3, offset_x: 25, offset_y: 32, mobile_scale: 4};
    assert.equal(m.getActivePlacement().scale, 1);
    m.setActiveScale(5);
    assert.equal(m.config.scale, 1);
    m.setActiveScale(0.01);
    assert.equal(m.config.scale, 0.1);
    assert.equal(m.config.offset_x, 25);
    assert.equal(m.config.offset_y, 32);
    m.getActiveLayoutFields = () => ({scale: 'mobile_scale',offsetX:'mobile_offset_x',offsetY:'mobile_offset_y'});
    assert.equal(m.getActivePlacement().scale, 1);
    m.setActiveScale(2);
    assert.equal(m.config.mobile_scale, 1);
});

test('models without scale metadata retain their previous range and invalid limits are safe', () => {
    const {manager: m, metadata} = fixture();
    m.layeredMetadata = metadata;
    m.config.scale = 4;
    assert.equal(m.getActivePlacement().scale, 4);
    m.setActiveScale(8);
    assert.equal(m.config.scale, 5);
    metadata.scale_limits = {min: 2, max: 1};
    assert.equal(m.clampModelScale(4), 4);
    metadata.scale_limits = {min: NaN, max: -1};
    assert.equal(m.clampModelScale(4), 4);
    metadata.scale_limits = {max: 0.6};
    assert.equal(m.clampModelScale(undefined, 4), 0.6);
    m.layeredMetadata = null;
    assert.equal(m.clampModelScale(4), 4);
});

test('wheel zoom stops at the model maximum and can still zoom out', async () => {
    const {manager: m, metadata} = fixture();
    m.layeredMetadata = metadata;
    metadata.scale_limits = {max: 1};
    m.config.scale = 1;
    let saved = 0;
    m.cancelEdgeSnapAnimation = () => {};
    m.beginModelManagerPositionEditing = () => {};
    m.applyScale = value => m.setActiveScale(value);
    m.snapModelIntoScreen = () => Promise.resolve();
    m.scheduleSaveCurrentConfig = () => {saved++;};
    const event = deltaY => ({deltaY, preventDefault(){},stopPropagation(){}});
    m.handleWheelZoom(event(-100));
    assert.equal(m.config.scale, 1);
    assert.equal(saved, 0);
    m.handleWheelZoom(event(100));
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(m.config.scale < 1);
    assert.equal(saved, 1);
});

function syntheticVowel(first,second){
    const count=2048,rate=12000,excitation=new Float64Array(count);
    for(let n=0;n<count;n++)excitation[n]=n%80===0?1:0;
    let signal=excitation;
    for(const frequency of [first,second]){
        const result=new Float64Array(count),radius=.97,c=2*radius*Math.cos(2*Math.PI*frequency/rate);
        for(let n=0;n<count;n++)result[n]=(1-radius)*signal[n]+c*(result[n-1]||0)-radius*radius*(result[n-2]||0);
        signal=result;
    }
    const peak=Math.max(...signal.map(Math.abs));
    return Uint8Array.from(signal,value=>Math.round(128+value/peak*70));
}

test('equal-level synthetic vowels produce different visemes from their audio resonances',()=>{
    const {manager:m}=fixture();const analyser={context:{sampleRate:12000}};let time=100;
    for(const [expected,formants] of Object.entries({a:[800,1400],e:[550,2100],i:[300,2600],o:[550,1000],u:[350,900]})){
        const bytes=syntheticVowel(...formants);
        m.updateTrackedViseme(bytes,analyser,.2,time);time+=100;
        m.updateTrackedViseme(bytes,analyser,.2,time);time+=100;
        assert.equal(m._trackedViseme,expected);
    }
    assert.equal(m.estimateTrackedFormants(new Uint8Array(2048).fill(128),12000),null);
});

test('timed mouth cues override audio estimates and are cleared when speech stops',()=>{
    const {manager:m}=fixture();
    assert.equal(m.setTrackedVisemeTimeline([{start_ms:0,end_ms:50,viseme:'i'},{start_ms:50,end_ms:100,viseme:'o'},{start_ms:100,end_ms:150,viseme:'closed'}],100),true);
    const bytes=syntheticVowel(800,1400),analyser={context:{sampleRate:12000}};
    m.updateTrackedViseme(bytes,analyser,.3,125);assert.equal(m._trackedViseme,'i');
    m.updateTrackedViseme(bytes,analyser,.3,175);assert.equal(m._trackedViseme,'o');
    m.updateTrackedViseme(bytes,analyser,.3,225);assert.equal(m._trackedViseme,'closed');
    assert.equal(m.setTrackedVisemeTimeline([{start_ms:0,end_ms:50,viseme:'unknown'}]),false);
    m.stopLipSync();assert.equal(m._trackedVisemeTimeline,null);
});

test('wide and round mouth profiles differ in geometry at the same audio opening',()=>{
    const {manager:m}=fixture();const ellipses=[];
    const ctx=new Proxy({}, {get:(_,key)=>(...args)=>{if(key==='ellipse')ellipses.push(args);},set:()=>true});
    m._trackedViseme='i';m.drawTrackedVectorMouth(ctx,{x:20,y:25},.7);
    const wide=ellipses[0];m._trackedPlayback.visemeShape=null;ellipses.length=0;
    m._trackedViseme='u';m.drawTrackedVectorMouth(ctx,{x:20,y:25},.7);
    const round=ellipses[0];assert.ok(wide[2]>round[2]*2);assert.ok(wide[3]<round[3]);
});

test('24fps prefetch requests every next frame instead of skipping to 80ms intervals', () => {
    const { manager, metadata, context, images } = fixture();
    const frames=Array.from({length:24},(_,i)=>({time_ms:Math.round(i*1000/24),
       duration_ms:Math.round((i+1)*1000/24)-Math.round(i*1000/24),image:`frames/${i}.png`,x:20,y:25}));
    metadata.tracked_animation={duration_ms:1000,mouth_crop:[0,0,30,20],frames};
    manager.initializeTrackedPlayback(metadata,'/model/meta.json');
    for(const phase of [0,16,40,41.99,42,82.99,990]){
      manager.initializeTrackedPlayback(metadata,'/model/meta.json');
      images.length=0;manager.drawTrackedPlayback(context,100+phase);
      const urls=images.map(image=>image.url);
      const index=manager.trackedLoopSelection(metadata.tracked_animation,phase).index;
      for(let k=1;k<=5;k++){const i=(index+k)%24;assert.ok(manager._trackedPlayback.cache.has(i)||urls.some(url=>url.endsWith(`/frames/${i}.png`)),`phase ${phase}: missing frame ${i}`)}
      assert.ok(manager._trackedPlayback.pending.size<=6);
    }
});

test('downloaded frames remain pending until asynchronous pixel decoding finishes', async () => {
    const { manager, images } = fixture();
    manager.requestTrackedFrame(1);
    const image=images.at(-1);
    let finishDecode;
    image.decode=()=>new Promise(resolve=>{finishDecode=resolve});
    image.onload();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(manager._trackedPlayback.cache.has(1),false);
    assert.equal(manager._trackedPlayback.pending.has(1),true);
    finishDecode();
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(manager._trackedPlayback.cache.get(1),image);
    assert.equal(manager._trackedPlayback.pending.has(1),false);
});

test('a decode finishing after a state change cannot flash the previous action into the new cache', async () => {
    const { manager, images, metadata } = fixture();
    manager.requestTrackedFrame(1);
    const image=images.at(-1);let finishDecode;
    image.decode=()=>new Promise(resolve=>{finishDecode=resolve});image.onload();
    await new Promise(resolve=>setImmediate(resolve));
    const previous=manager._trackedPlayback;
    manager.initializeTrackedPlayback(metadata,'/new-action/meta.json');
    finishDecode();await new Promise(resolve=>setImmediate(resolve));
    assert.notEqual(manager._trackedPlayback,previous);
    assert.equal(manager._trackedPlayback.cache.has(1),false);
    assert.equal(manager._trackedPlayback.cache.size,1);
});

test('speech plates cover the baked mouth even during a closed syllable', () => {
    const {manager:m}=fixture(),calls=[];
    m._trackedPlayback.data.speech_mouth_patches=true;
    m._trackedPlayback.data.mouth_renderer='viseme_v1';
    m._trackedPlayback.data.mouth_emotion='sad';m.isSpeaking=true;m._trackedViseme='closed';
    const plate={name:'clean-plate'};m._trackedPlayback.mouthPatches.set('mouth/0.png',plate);
    const ctx=new Proxy({}, {get:(_,key)=>(...args)=>calls.push([key,...args]),set:()=>true});
    m.drawTrackedMouth(ctx,{name:'original-body'},{x:20,y:25,mouth_patch:{image:'mouth/0.png',x:10,y:15,width:30,height:25}},0);
    assert.equal(calls.find(c=>c[0]==='drawImage')[1],plate);
    assert.ok(calls.some(c=>c[0]==='quadraticCurveTo'),'closed speech still draws a lip line');
    calls.length=0;m.isSpeaking=false;
    m.drawTrackedMouth(ctx,{name:'original-body'},{x:20,y:25},0);
    assert.equal(calls.length,0,'silent animation retains its original emotion mouth');
});

test('speech body is not published until its matching plate has decoded', async () => {
    const {manager:m,metadata,images}=fixture();
    metadata.tracked_animation.speech_mouth_patches=true;
    metadata.tracked_animation.frames[1].mouth_patch={image:'mouth/1.png',x:10,y:10,width:30,height:20};
    m.initializeTrackedPlayback(metadata,'/model/meta.json');m.requestTrackedFrame(1);
    const plate=images.find(i=>i.url.endsWith('/mouth/1.png')),body=images.find(i=>i.url.endsWith('/frames/1.png'));
    let finish;plate.decode=()=>new Promise(resolve=>{finish=resolve});body.decode=async()=>{};
    plate.onload();body.onload();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(m._trackedPlayback.cache.has(1),false);
    finish();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(m._trackedPlayback.cache.get(1),body);
    assert.equal(m._trackedPlayback.mouthPatches.get('mouth/1.png'),plate);
});

test('a speech plate decode from an old state cannot populate the next state', async () => {
    const {manager:m,metadata,images}=fixture();metadata.tracked_animation.speech_mouth_patches=true;
    metadata.tracked_animation.frames[1].mouth_patch={image:'mouth/1.png',x:10,y:10,width:30,height:20};
    m.initializeTrackedPlayback(metadata,'/model/meta.json');m.requestTrackedFrame(1);
    let finish;for(const im of images){im.decode=im.url.includes('/mouth/')?()=>new Promise(resolve=>{finish=resolve}):async()=>{};im.onload();}
    await new Promise(resolve=>setImmediate(resolve));m.initializeTrackedPlayback(metadata,'/new/meta.json');
    finish();await new Promise(resolve=>setImmediate(resolve));
    assert.equal(m._trackedPlayback.cache.size,0);assert.equal(m._trackedPlayback.mouthPatches.size,0);
});

test('mouth rotation follows the face for open and closed lips without rotating the skin plate', () => {
    const {manager:m}=fixture(),calls=[];
    m._trackedPlayback.data.speech_mouth_patches=true;
    m._trackedPlayback.data.mouth_renderer='viseme_v1';m.isSpeaking=true;
    m._trackedPlayback.mouthPatches.set('plate',{});
    const frame={x:20,y:25,mouth_rotation:0.1,mouth_patch:{image:'plate',x:10,y:15,width:30,height:25}};
    const ctx=new Proxy({}, {get:(_,key)=>(...args)=>calls.push([key,...args]),set:()=>true});
    for(const amount of [0,0.8]) {
        calls.length=0;m.drawTrackedMouth(ctx,{},frame,amount);
        assert.ok(calls.findIndex(c=>c[0]==='drawImage')<calls.findIndex(c=>c[0]==='rotate'));
        assert.deepEqual(calls.find(c=>c[0]==='translate'),['translate',20,28]);
        assert.deepEqual(calls.find(c=>c[0]==='rotate'),['rotate',0.1]);
        assert.equal(calls.filter(c=>c[0]==='save').length,calls.filter(c=>c[0]==='restore').length);
    }
    calls.length=0;m.drawTrackedVectorMouth(ctx,{x:20,y:25},0.8);
    assert.equal(calls.some(c=>c[0]==='rotate'),false,'legacy frames keep their existing transform');
});

function speechEndFixture(options = {}) {
    const f=actionFixture(options),m=f.manager,restarts=[];
    m.ensureContainer=()=>{};m.applyTransform=()=>{};m.updateLockIconPosition=()=>{};
    m.setState=Object.getPrototypeOf(m).setState;
    m.restartLayeredAnimationLoop=()=>{m.layeredAnimationStart=performance.now();restarts.push(m.layeredStateIndex)};
    m.layeredAnimationFrame=1;
    m.stopSpeakingBounceAnimation=()=>{};
    return {...f,restarts};
}

test('speech end preserves a running tracked emotion and its original expiry', async () => {
    const {manager:m,restarts}=speechEndFixture();
    m.setEmotion('angry',{durationMs:350});
    const originalStart=m.layeredAnimationStart,expiry=m.emotionTimer;
    m.setSpeaking(false);
    await new Promise(r=>setTimeout(r,210));
    assert.equal(m.layeredStateIndex,1);
    assert.equal(m.layeredAnimationStart,originalStart);
    assert.equal(m.emotionTimer,expiry);
    assert.deepEqual(restarts,[1]);
    await new Promise(r=>setTimeout(r,190));
    assert.equal(m.layeredStateIndex,0);assert.equal(m.currentEmotion,null);
    assert.deepEqual(restarts,[1,0]);
});

test('speech end keeps ordinary idle and untracked models return behavior', async () => {
    for(const tracked of [true,false]) {
        const {manager:m,restarts}=speechEndFixture();
        if(!tracked)m._trackedPlayback=null;
        m.state='talking';m.setSpeaking(false);
        await new Promise(r=>setTimeout(r,210));
        assert.equal(m.state,'idle');assert.deepEqual(restarts,[0]);
    }
});

test('a new speech segment cancels the pending idle cleanup', async () => {
    const {manager:m,restarts}=speechEndFixture();
    m.startSpeakingMouthAnimation=()=>{m.isSpeaking=true};
    m.setSpeaking(false);m.setSpeaking(true);
    await new Promise(r=>setTimeout(r,210));
    assert.equal(m.isSpeaking,true);assert.equal(m.returnIdleTimer,null);
    assert.deepEqual(restarts,[]);
});

test('speech cleanup preserves an emotion selected during its delay', async () => {
    const {manager:m,restarts}=speechEndFixture();
    m.setSpeaking(false);m.setEmotion('angry',{durationMs:0});
    const start=m.layeredAnimationStart;
    await new Promise(r=>setTimeout(r,210));
    assert.equal(m.currentEmotion,'angry');assert.equal(m.layeredAnimationStart,start);
    assert.deepEqual(restarts,[1]);m.clearEmotion();
});

test('speech cleanup from the previous model cannot reset a replacement model', async () => {
    const {manager:m,restarts}=speechEndFixture();
    m.setSpeaking(false);m._loadGeneration+=1;
    await new Promise(r=>setTimeout(r,210));
    assert.deepEqual(restarts,[]);assert.equal(m.returnIdleTimer,null);
});


const avatarClickEvent=()=>({preventDefault(){},stopPropagation(){},target:null});

test('clicks without a tracked click action preserve idle and emotion clocks, including repeated clicks', async () => {
    for(const emotion of [false,true]) {
        const {manager:m,restarts}=speechEndFixture();
        if(emotion)m.setEmotion('angry',{durationMs:0});
        const playback=m._trackedPlayback,start=m.layeredAnimationStart;
        restarts.length=0;
        m.showTransientImage=()=>{throw Error('missing click action must not replace the body')};
        m.handleClick(avatarClickEvent());m.handleClick(avatarClickEvent());
        assert.equal(m.clickTimer,null);
        await new Promise(r=>setTimeout(r,650));
        assert.equal(m._trackedPlayback,playback);assert.equal(m.layeredAnimationStart,start);
        assert.deepEqual(restarts,[]);assert.equal(m.currentEmotion,emotion?'angry':null);
        if(emotion)m.clearEmotion();
    }
});

test('tracked models with a dedicated click state still play it and return home', async () => {
    const {manager:m,restarts,metadata}=speechEndFixture();
    metadata.state_catalog[1].name='click';
    m.shouldRenderLayer=()=>true;m.stateFrameInfo=()=>({durationMs:40});
    metadata.layers=[{_imageIndex:0}];
    m.handleClick(avatarClickEvent());
    assert.equal(m.layeredStateIndex,1);assert.ok(m.clickTimer);
    await new Promise(r=>setTimeout(r,90));
    assert.equal(m.layeredStateIndex,0);assert.equal(m.clickTimer,null);
    assert.deepEqual(restarts,[1,0]);
});

test('legacy layered models without tracked animation retain click fallback and restore', async () => {
    const {manager:m,restarts}=speechEndFixture();m._trackedPlayback=null;
    let shown=0;m.showTransientImage=()=>{shown++};
    m.handleClick(avatarClickEvent());assert.equal(shown,1);assert.ok(m.clickTimer);
    await new Promise(r=>setTimeout(r,650));assert.deepEqual(restarts,[0]);
});

test('flat image models retain their click image and delayed state restoration', async () => {
    const {manager:m}=fixture();m.isLayeredActive=()=>false;m.config={click_image:'click.png'};
    const shown=[],states=[];m.showTransientImage=src=>shown.push(src);m.setState=state=>states.push(state);
    m.handleClick(avatarClickEvent());assert.deepEqual(shown,['click.png']);
    await new Promise(r=>setTimeout(r,650));assert.deepEqual(states,['idle']);
});


function pointerClickFixture() {
    const f=speechEndFixture(),m=f.manager;
    m.rememberDragScreenPoint=()=>{};m.resetLayeredDragVelocity=()=>{};
    m.setModelDraggingState=()=>{};m.image=null;
    m.getActivePlacement=()=>({scale:1,offsetX:0,offsetY:0});
    return f;
}

test('full stationary pointer down/up/click preserves tracked idle and emotion playback', async () => {
    for(const emotion of [false,true]) {
        const {manager:m,restarts}=pointerClickFixture();
        if(emotion)m.setEmotion('angry',{durationMs:0});
        restarts.length=0;const start=m.layeredAnimationStart,playback=m._trackedPlayback;
        const event={...avatarClickEvent(),button:0,pointerId:7,clientX:100,clientY:100};
        for(let i=0;i<3;i++) {
            m.startDrag(event);assert.equal(m._dragState.moved,false);
            await m.endDrag(event);m.handleClick(event);
        }
        assert.equal(m._dragState,null);assert.equal(m.clickTimer,null);
        assert.equal(m.layeredAnimationStart,start);assert.equal(m._trackedPlayback,playback);
        assert.deepEqual(restarts,[]);
        if(emotion)m.clearEmotion();
    }
});

test('pointer release keeps legacy restoration and dedicated tracked drag completion', async () => {
    for(const tracked of [true,false]) {
        const {manager:m,restarts}=pointerClickFixture();
        if(!tracked)m._trackedPlayback=null;
        else m.layeredMetadata.state_catalog[1].name='drag';
        let restored=0,saved=0;m.restoreStateImage=()=>{restored++};
        m.checkAndSwitchDisplayAfterDrag=async()=>false;m.isDragCompletionCurrent=()=>true;
        m.recordDragHintPointerEdgeRelease=async()=>{};m.snapModelIntoScreen=async()=>{};
        m.saveOrStageCurrentConfig=async()=>{saved++};
        m._dragState={pointerId:7,moved:tracked};
        await m.endDrag({...avatarClickEvent(),pointerId:7});
        assert.equal(restored,1);assert.deepEqual(restarts,[0]);
        assert.equal(saved,tracked?1:0);
        assert.equal(m._suppressNextClick,tracked);
    }
});


test('full position-only drag preserves tracked idle, emotion and speech while saving the new placement', async () => {
    for(const mode of ['idle','emotion','speech','emotion-expiry']) {
        const {manager:m,restarts}=pointerClickFixture();
        if(mode==='emotion'||mode==='emotion-expiry')m.setEmotion('angry',{durationMs:0});
        m.isSpeaking=mode==='speech';m.lipSyncMouthOpen=0.6;
        restarts.length=0;let saved,placement={scale:0.5,offsetX:10,offsetY:20};
        m.getActivePlacement=()=>placement;
        m.setActiveOffsets=(x,y)=>{placement={...placement,offsetX:x,offsetY:y}};
        m.cancelEdgeSnapAnimation=()=>{};m.beginModelManagerPositionEditing=()=>{};
        m.syncGlobalConfig=()=>{};m.rememberModelCenterPointerOffset=()=>{};
        m.recordDragHintPointerEdgeApproach=async()=>{};
        m.startLayeredAnimationLoop=options=>assert.equal(options.preserveTimeline,true);
        m.checkAndSwitchDisplayAfterDrag=async()=>false;m.isDragCompletionCurrent=()=>true;
        m.recordDragHintPointerEdgeRelease=async()=>{};m.snapModelIntoScreen=async()=>{};
        m.saveOrStageCurrentConfig=async()=>{saved={...placement}};
        m.showTransientImage=()=>{throw Error('position-only drag must not replace body')};
        const down={...avatarClickEvent(),button:0,pointerId:7,clientX:100,clientY:100};
        m.startDrag(down);
        m.moveDrag({...down,clientX:140,clientY:160});
        assert.equal(m._dragState.moved,true);
        if(mode==='emotion-expiry')m.clearEmotion();
        const playback=m._trackedPlayback,start=m.layeredAnimationStart;
        const expectedRestarts=restarts.length;
        await m.endDrag({...down,clientX:140,clientY:160});
        assert.equal(m._trackedPlayback,playback);assert.equal(m.layeredAnimationStart,start);
        assert.equal(restarts.length,expectedRestarts);
        assert.deepEqual(saved,{scale:0.5,offsetX:50,offsetY:80});
        assert.equal(m.isSpeaking,mode==='speech');assert.equal(m.lipSyncMouthOpen,0.6);
        assert.equal(m._suppressNextClick,true);
        m.handleClick(down);assert.equal(m._suppressNextClick,false);assert.equal(m.clickTimer,null);
        if(mode==='emotion')m.clearEmotion();
    }
});

test('dedicated tracked drag state still takes over and returns to its home state', async () => {
    const {manager:m,restarts,metadata}=pointerClickFixture();
    metadata.state_catalog[1].name='drag';
    m.showDragImage();assert.equal(m.layeredStateIndex,1);
    assert.equal(m.layeredTransientSlots.at(-1).kind,'drag');
    m.checkAndSwitchDisplayAfterDrag=async()=>false;m.isDragCompletionCurrent=()=>true;
    m.recordDragHintPointerEdgeRelease=async()=>{};m.snapModelIntoScreen=async()=>{};
    m.saveOrStageCurrentConfig=async()=>{};
    m._dragState={pointerId:7,moved:true};
    await m.endDrag({...avatarClickEvent(),pointerId:7});
    assert.equal(m.layeredStateIndex,0);assert.equal(m.layeredTransientSlots.length,0);
    assert.ok(restarts.length>=2);
});
function reviewedFixture(options = {}) {
    const f=fixture(options),m=f.manager;
    m.isLayeredActive=()=>true;
    const clip=(playback='once')=>({duration_ms:2000,playback,width:720,height:960,mouth_crop:[0,0,1,1],mouth_renderer:'viseme_v1',lipsync:true,frames:[{time_ms:0,duration_ms:1000,image:'a.png',x:20,y:25},{time_ms:1000,duration_ms:1000,image:'b.png',x:20,y:25}]});
    const groups = {
        idle: [0, 1], happy: [2, 3], drag: [4], leisure: [5],
        'visit-return': [6], 'visit-leave': [7]
    };
    const names = ['idle', 'idle', 'happy', 'happy', 'drag', 'leisure', 'visit-return', 'visit-leave'];
    m.layeredMetadata = {
        state_count: names.length,
        canvas: { width: 720, height: 960 },
        reviewed_actions: { version: 1, groups },
        state_catalog: names.map((group, state_index) => ({ state_index, group })),
        tracked_animations: {
            0: clip('loop'), 1: clip('loop'), 2: clip(), 3: clip(),
            4: clip(), 5: { ...clip(), lipsync: false }, 6: clip(), 7: clip()
        }
    };
    m.layeredMetadata.tracked_animation=m.layeredMetadata.tracked_animations[0];m.layeredStateIndex=0;
    m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');m.layeredAnimationStart=100;
    m.prepareReviewedEntry=async()=>[{image:{name:'prepared'}}];
    m.reviewedChooseIdle=()=>0; // deterministic default; entry-selection tests choose the other variant explicitly.
    m.setLayeredStateIndex=(index,options)=>{assert.equal(options.reviewedCommit,true);m.layeredStateIndex=index;m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');m.seedReviewedEntry(options.reviewedEntries);};
    return f;
}
const flushReviewed=()=>new Promise(resolve=>setImmediate(resolve));

test('reviewed entry starts only after decode and latest request wins',async()=>{
    const {manager:m}=reviewedFixture();let resolve;const delayed=new Promise(r=>{resolve=r});m.prepareReviewedEntry=index=>index===2?delayed:Promise.resolve([{image:{name:'latest'}}]);
    m.requestReviewedState(2);assert.equal(m.layeredStateIndex,0);m.requestReviewedState(3);await flushReviewed();assert.equal(m.layeredStateIndex,3);
    resolve([{image:{name:'stale'}}]);await flushReviewed();assert.equal(m.layeredStateIndex,3);assert.equal(m._trackedPlayback.cache.get(0).name,'latest');
});
test('reviewed queued action waits for recovery and actual last frame',async()=>{
    const {manager:m,advance}=reviewedFixture();m.requestReviewedState(2);await flushReviewed();m.requestReviewedState(3);assert.equal(m._reviewedQueued.index,3);
    advance(2200);m.reviewedPlaybackTick(2200);await flushReviewed();assert.equal(m.layeredStateIndex,2);
    m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2200);await flushReviewed();assert.equal(m.layeredStateIndex,3);
});
test('reviewed duplicate state preserves phase and stale model decode cannot commit',async()=>{
    const {manager:m}=reviewedFixture();const old=m._trackedPlayback;m.requestReviewedState(0);await flushReviewed();assert.equal(m._trackedPlayback,old);
    let resolve;m.prepareReviewedEntry=()=>new Promise(r=>{resolve=r});m.requestReviewedState(2);m.layeredMetadata={};resolve([]);await flushReviewed();assert.equal(m.layeredStateIndex,0);
});
test('reviewed variants avoid immediate repeat and use only configured states',()=>{
    const {manager:m}=reviewedFixture();for(let i=0;i<30;i++){const prev=m._reviewedLast?.happy,n=m.reviewedPick('happy');assert([2,3].includes(n));if(prev!==undefined)assert.notEqual(n,prev)}assert.equal(m.reviewedPick('absent'),null);
});
test('reviewed guest visit animations require a matched real departure and deduplicate',()=>{
    const {manager:m}=reviewedFixture(),calls=[];m.requestReviewedState=(n)=>{calls.push(n);return true};
    assert.equal(m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'v1'}),false);
    assert.equal(m.handleReviewedVisit({side:'host',action:'departed',visit_id:'v1'}),false);
    assert.equal(m.handleReviewedVisit({side:'guest',action:'departed',visit_id:'v1',recovery:true}),false);
    for(const action of ['pending','started','cancelled'])assert.equal(m.handleReviewedVisit({side:'guest',action,visit_id:'v1'}),false);
    m.handleReviewedVisit({side:'guest',action:'departed',visit_id:'v1'});m.handleReviewedVisit({side:'guest',action:'departed',visit_id:'v1'});m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'wrong'});assert.deepEqual(calls,[7]);
    m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'v1'});m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'v1'});assert.deepEqual(calls,[7,6]);
});
test('reviewed leisure never replaces speech and legacy data has identity perspective',async()=>{
    const {manager:m,context}=reviewedFixture();m.requestReviewedState(5);await flushReviewed();m.isSpeaking=true;m.reviewedPlaybackTick(200);await flushReviewed();assert.equal(m.layeredStateIndex,0);
    const calls=[];const ctx=new Proxy({},{get:(_,k)=>(...args)=>calls.push([k,...args]),set:()=>true});m._trackedViseme='a';m.drawTrackedVectorMouth(ctx,{x:20,y:25},.8);assert.equal(calls.some(x=>x[0]==='transform'),false);
    for(const amount of [0,.8]){calls.length=0;m.drawTrackedVectorMouth(ctx,{x:20,y:25,mouth_rotation:.1,mouth_perspective:{scale_x:.9,scale_y:.85,shear:.02}},amount);assert(calls.some(x=>x[0]==='transform'));assert.equal(calls.filter(x=>x[0]==='save').length,calls.filter(x=>x[0]==='restore').length)}
});
test('reviewed leisure requires owner, explicit proactive OFF and 30 continuous eligible seconds',()=>{
    const f=reviewedFixture(),m=f.manager,calls=[];m._trackedPlayback.data.duration_ms=999999;
    let activity={proactiveEnabled:true,owner:true,busy:false};f.window.getReviewedPNGTuberActivity=()=>activity;m.requestReviewedState=(index)=>{calls.push(index);return true};
    m.reviewedPlaybackTick(100);m.reviewedPlaybackTick(40000);assert.deepEqual(calls,[]);
    activity.proactiveEnabled=false;activity.owner=false;m.reviewedPlaybackTick(41000);m.reviewedPlaybackTick(80000);assert.deepEqual(calls,[]);
    activity.owner=true;m.reviewedPlaybackTick(81000);m.reviewedPlaybackTick(110999);assert.deepEqual(calls,[]);m.reviewedPlaybackTick(111000);assert.deepEqual(calls,[5]);
    m._reviewedIdleSince=100;activity.busy=true;m.reviewedPlaybackTick(112000);assert.equal(m._reviewedIdleSince,null);
});
test('reviewed drag schedules once without click slot and idle keeps its leisure elapsed time',async()=>{
    const f=reviewedFixture(),m=f.manager;let calls=[];m.requestReviewedState=(index,options)=>{calls.push([index,options.source]);return true};m.showDragImage();assert.deepEqual(calls,[[4,'reviewed-drag']]);
    const g=reviewedFixture(),n=g.manager;g.window.getReviewedPNGTuberActivity=()=>({proactiveEnabled:false,owner:true,busy:false});n._reviewedIdleSince=100;n._reviewedLast={idle:0};for(let i=0;i<2;i++)n.reviewedIdleResources(1).cache.set(i,{name:'warm'});n.reviewedPlaybackTick(4200);await flushReviewed();assert.equal(n.layeredStateIndex,0);assert.equal(n._reviewedIdleSince,100);
});


test('reviewed idle retains all 56 decoded body/plate pairs across loops and state reentry', async()=>{
    const {manager:m,images}=reviewedFixture();
    const frames=Array.from({length:56},(_,i)=>({time_ms:i*40,duration_ms:40,image:`idle/${i}.png`,x:20,y:25,mouth_patch:{image:`plate/${i}.png`}}));
    const data={...m.layeredMetadata.tracked_animations[0],frames,duration_ms:2240,speech_mouth_patches:true};
    m.layeredMetadata.tracked_animations[0]=data;m.layeredMetadata.tracked_animation=data;m._reviewedIdleMetadata=null;
    m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');
    for(let i=0;i<56;i++){
        const before=images.length;m.requestTrackedFrame(i);
        for(const image of images.slice(before))image.onload();
        await flushReviewed();
    }
    assert.equal(m._trackedPlayback.cache.size,56);assert.equal(m._trackedPlayback.mouthPatches.size,56);
    const loaded=images.length,cache=m._trackedPlayback.cache;
    for(let cycle=0;cycle<10;cycle++)for(let i=0;i<56;i++)m.requestTrackedFrame(i);
    assert.equal(images.length,loaded,'ten further loops must not create or decode new images');
    m.layeredStateIndex=2;m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');
    m.layeredStateIndex=0;m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');
    assert.equal(m._trackedPlayback.cache,cache);assert.equal(m._trackedPlayback.mouthPatches.size,56);
    m.requestTrackedFrame(0);assert.equal(images.length,loaded,'return from emotion reuses the idle entry');
});

test('reviewed idle loader deduplicates pending decode and does not publish into a replacement model',async()=>{
    const {manager:m,images}=reviewedFixture();
    const resource=m.reviewedIdleResources(0);
    const first=m.loadReviewedIdleFrame(resource,0,'/meta.json');
    assert.equal(m.loadReviewedIdleFrame(resource,0,'/meta.json'),first);
    const image=images.at(-1),next={...m.layeredMetadata};
    m.layeredMetadata=next;m.initializeTrackedPlayback(next,'/replacement.json');
    image.onload();await first;
    assert.equal(resource.cache.size,0);assert.equal(m._trackedPlayback.cache.size,0);
    assert.notEqual(m._trackedPlayback.idleResource,resource);
});

test('reviewed idle never rotates variants automatically even when the other clip is fully warm',async()=>{
    const {manager:m}=reviewedFixture();m.warmReviewedIdle=()=>{};
    const next=m.reviewedIdleResources(1);next.cache.set(0,{name:'first'});
    m.reviewedPlaybackTick(4100);await flushReviewed();assert.equal(m.layeredStateIndex,0,'partial entry cannot replace the running idle');
    next.cache.set(1,{name:'last'});
    m.reviewedPlaybackTick(4500);await flushReviewed();assert.equal(m.layeredStateIndex,0,'ready midway through a loop must wait');
    m.reviewedPlaybackTick(6100);await flushReviewed();assert.equal(m.layeredStateIndex,0);
    m.reviewedPlaybackTick(200100);await flushReviewed();assert.equal(m.layeredStateIndex,0);
});

test('reviewed idle cache stays within its reservation budget and one-shot clips keep legacy cache policy',()=>{
    const {manager:m}=reviewedFixture();
    assert.equal(m.reviewedIdleResources(2),null);
    m.layeredMetadata.tracked_animations[1]={...m.layeredMetadata.tracked_animations[1],width:10000,height:10000};
    assert.equal(m.reviewedIdleResources(1),null,'oversized decoded clips must fall back instead of exceeding 512 MiB');
    assert.ok([...m._reviewedIdleResources.values()].reduce((n,r)=>n+r.bytes,0)<=512*1024*1024);
    m.layeredMetadata.reviewed_actions.version=0;assert.equal(m.reviewedIdleResources(0),null);
});


test('reviewed completed action chooses another idle once and clears emotion on either idle index',async()=>{
    const {manager:m,advance}=reviewedFixture();let choices=0;m.reviewedChooseIdle=()=>{choices++;return 1};
    m.requestReviewedState(2);await flushReviewed();m.currentEmotion='happy';m.activeLayeredEmotionIndex=2;
    m._trackedPlayback.lastIndex=1;advance(2200);m.reviewedPlaybackTick(2200);await flushReviewed();
    assert.equal(m.layeredStateIndex,1);assert.equal(choices,1);assert.equal(m.currentEmotion,null);assert.equal(m.activeLayeredEmotionIndex,null);
    const clock=m.layeredAnimationStart,playback=m._trackedPlayback;
    for(const source of ['speech-cleanup','emotion-clear','api'])m.requestReviewedState(0,{source,force:true});
    await flushReviewed();assert.equal(m.layeredStateIndex,1);assert.equal(choices,1);assert.equal(m.layeredAnimationStart,clock);assert.equal(m._trackedPlayback,playback);
});

test('reviewed queued return retains its sampled idle rather than sampling twice at recovery',async()=>{
    const {manager:m,advance}=reviewedFixture();let choices=0;m.reviewedChooseIdle=()=>{choices++;return 1};
    m.requestReviewedState(2);await flushReviewed();m.requestReviewedState(0,{source:'emotion-clear'});
    assert.equal(m._reviewedQueued.index,1);assert.equal(choices,1);assert.equal(m.layeredStateIndex,2);
    m._trackedPlayback.lastIndex=1;advance(2200);m.reviewedPlaybackTick(2200);await flushReviewed();
    assert.equal(m.layeredStateIndex,1);assert.equal(choices,1);
});

test('reviewed idle entry permits repeating the same variant and uses only loop states',()=>{
    const {manager:m}=reviewedFixture();delete m.reviewedChooseIdle;
    m.layeredMetadata.reviewed_actions.groups.idle=[1,2]; // 2 is one-shot, never an idle choice.
    m._reviewedLast={idle:1};for(let i=0;i<10;i++)assert.equal(m.reviewedChooseIdle(),1);
    m.layeredMetadata.reviewed_actions.groups.idle=[0];assert.equal(m.reviewedChooseIdle(),0);
});

test('reviewed redundant idle cancels a pending emotion decode without restarting the visible loop',async()=>{
    const {manager:m}=reviewedFixture();let resolve;m.prepareReviewedEntry=()=>new Promise(r=>{resolve=r});
    const visible=m._trackedPlayback,clock=m.layeredAnimationStart;m.requestReviewedState(2);m.requestReviewedState(0);
    resolve([{image:{name:'stale'}}]);await flushReviewed();assert.equal(m._trackedPlayback,visible);assert.equal(m.layeredStateIndex,0);assert.equal(m.layeredAnimationStart,clock);assert.equal(m._reviewedPreparing,false);
});

test('reviewed loop overlap retains source resolution and applies the same canvas scale as ordinary frames',()=>{
 const {manager:m}=reviewedFixture();m.layeredMetadata.canvas={width:576,height:768};m.reviewedPlaybackTick=()=>{};
 const playback=m._trackedPlayback;playback.data.loop={crossfade_ms:200};playback.cache.set(0,{name:'a'});playback.cache.set(1,{name:'b'});
 const scratch={width:0,height:0,getContext:()=>new Proxy({},{get:()=>()=>{}})};playback.mixCanvas=scratch;m.drawTrackedMouth=()=>{};
 const calls=[];const ctx=new Proxy({},{get:(_,key)=>(...args)=>calls.push([key,...args]),set:()=>true});
 m.drawTrackedPlayback(ctx,1750);assert.equal(scratch.width,720);assert.equal(scratch.height,960);assert(calls.some(c=>c[0]==='scale'&&c[1]===.8&&c[2]===.8));
 m.hasReviewedActions=()=>false;calls.length=0;m.drawTrackedPlayback(ctx,1760);assert.equal(scratch.width,720);assert.equal(scratch.height,960);assert(calls.some(c=>c[0]==='scale'&&c[1]===.8&&c[2]===.8));
});

test('reviewed paused playback rejects API and direct state requests without loading entries',()=>{
 const {manager:m}=reviewedFixture();let loads=0;m.prepareReviewedEntry=async()=>{loads++;return[]};m._renderingPaused=true;
 assert.equal(m.playLayeredAnimation(3),false);assert.equal(m.requestReviewedState(2),false);assert.equal(loads,0);assert.equal(m.layeredStateIndex,0);
});

test('reviewed pause during entry decode cancels the stale commit even after resume',async()=>{
 const {manager:m}=reviewedFixture();let resolve;m.prepareReviewedEntry=()=>new Promise(r=>{resolve=r});
 m.clearLayeredTimers=m.clearLayeredTransientSlot=m.stopSpeakingBounceAnimation=()=>{};
 const visible=m._trackedPlayback,clock=m.layeredAnimationStart;m.requestReviewedState(2);m.pauseRendering();m._renderingPaused=false;
 resolve([]);await flushReviewed();assert.equal(m._trackedPlayback,visible);assert.equal(m.layeredStateIndex,0);assert.equal(m.layeredAnimationStart,clock);assert.equal(m._reviewedPreparing,false);
});


// Integration regressions intentionally retain the real commit and animation-loop methods.
function realReviewedFixture(options = {}) {
    const f = reviewedFixture(options), m = f.manager;
    delete m.setLayeredStateIndex;
    delete m.setState;
    m.drawLayeredState = () => {};
    m.ensureContainer = () => {};
    m.applyTransform = () => {};
    m.updateLockIconPosition = () => {};
    m.startLayeredBreathingLoop = () => {};
    m.stopSpeakingMouthAnimation = () => { m.isSpeaking = false; };
    m.detachLayeredHotkeys = m.detachLayeredPlayEvent = m.detachLayeredPointerTracking = () => {};
    return f;
}

test('real reviewed commit initializes, seeds and starts the selected timeline', async () => {
    const { manager: m, callbacks } = realReviewedFixture();
    m.requestReviewedState(2, { emotion: 'happy' });
    assert.equal(m.currentEmotion, null);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    assert.equal(m._trackedPlayback.data, m.layeredMetadata.tracked_animations[2]);
    assert.equal(m._trackedPlayback.cache.get(0).name, 'prepared');
    assert.equal(m.currentEmotion, 'happy');
    assert.ok(callbacks.has(m.layeredAnimationFrame));
    m.clearLayeredTimers();
});

test('speech completion through real setState preserves idle and return-action clocks', async () => {
    for (const index of [0, 1, 2, 6]) {
        const { manager: m, advance } = realReviewedFixture();
        if (index) {
            if (index === 6) {
                m._reviewedVisit = 'speech-clock';
                m.handleReviewedVisit({ action: 'ended', visit_id: 'speech-clock', side: 'guest' });
            } else m.requestReviewedState(index, { reviewedIdleEntry: index === 1, force: true });
            await flushReviewed();
        }
        m.layeredAnimationStart = 123;
        advance(900);
        m.setSpeaking(false);
        await new Promise(resolve => setTimeout(resolve, 180));
        assert.equal(m.layeredAnimationStart, 123, `state ${index} rewound`);
        assert.equal(m.layeredStateIndex, index);
        m.clearLayeredTimers();
    }
});

test('hidden pending entry never commits or restarts the real loop', async () => {
    const { manager: m } = realReviewedFixture();
    let resolve;
    m.prepareReviewedEntry = () => new Promise(r => { resolve = r; });
    m.requestReviewedState(2);
    m.hide();
    resolve([{ image: {} }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    assert.equal(m.layeredAnimationFrame, null);
    assert.equal(m._reviewedPreparing, false);
    assert.equal(m.requestReviewedState(3), false);
});

test('leisure eligibility is checked after pending entry decoding, without an event', async () => {
    for (const change of ['speech', 'proactive', 'busy', 'owner', 'drag', 'zoom', 'visit', 'emotion']) {
        const { manager: m, window } = realReviewedFixture();
        const activity = { proactiveEnabled: false, owner: true, busy: false };
        window.getReviewedPNGTuberActivity = () => activity;
        let resolve, events = 0;
        window.dispatchEvent = () => { events++; };
        m.prepareReviewedEntry = () => new Promise(r => { resolve = r; });
        m.requestReviewedState(5, { source: 'reviewed-leisure' });
        if (change === 'speech') m.isSpeaking = true;
        if (change === 'proactive') activity.proactiveEnabled = true;
        if (change === 'busy') activity.busy = true;
        if (change === 'owner') activity.owner = false;
        if (change === 'drag') m._dragState = {};
        if (change === 'zoom') m._touchZoomState = {};
        if (change === 'visit') m._reviewedVisit = 'a';
        if (change === 'emotion') m.currentEmotion = 'happy';
        resolve([{ image: {} }]);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, 0, change);
        assert.equal(events, 0, change);
        assert.equal(m._reviewedPreparing, false, change);
    }
});

test('a queued entry retries transient failure and consumes the queue only on success', async () => {
    const { manager: m, advance } = realReviewedFixture();
    m.requestReviewedState(2);
    await flushReviewed();
    m.requestReviewedState(3);
    let attempts = 0;
    m.prepareReviewedEntry = async () => {
        if (++attempts === 1) throw Error('temporary');
        return [{ image: { name: 'recovered' } }];
    };
    m._trackedPlayback.lastIndex = 1;
    advance(2200);
    m.reviewedPlaybackTick(2200);
    await flushReviewed();
    assert.equal(m._reviewedQueued.index, 3);
    for (let t = 2201; t < 2400; t++) m.reviewedPlaybackTick(t);
    assert.equal(attempts, 1);
    advance(2500);
    m.reviewedPlaybackTick(2500);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    assert.equal(m._reviewedQueued, null);
    assert.equal(m._trackedPlayback.cache.get(0).name, 'recovered');
    m.clearLayeredTimers();
});

test('permanent entry failure uses bounded delayed idle recovery instead of per-frame retries', async t => {
    const { manager: m, advance } = realReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2);await flushReviewed();m._trackedPlayback.lastIndex=1;
    let attempts=0;m.prepareReviewedEntry=async()=>{attempts++;throw Error('offline')};
    for(const time of [2200,2500,3100,4200,4500,5100]) {advance(time);m.reviewedPlaybackTick(time);await flushReviewed();}
    const count=attempts;assert.ok(m._reviewedRecovery);
    for(let time=5101;time<5350;time++)m.reviewedPlaybackTick(time);
    await flushReviewed();assert.equal(attempts,count);
    advance(5350);m.reviewedPlaybackTick(5350);await flushReviewed();assert.equal(attempts,count+1);
});

test('frame retries back off and only terminal final-frame failure releases the action', async () => {
    const { manager: m, images, advance } = realReviewedFixture();
    m.requestReviewedState(2);
    await flushReviewed();
    for (const time of [100, 400, 1000]) {
        advance(time);
        m.requestTrackedFrame(1);
        images.at(-1).onerror();
        await flushReviewed();
        if (time < 1000) {
            assert.equal(m._trackedPlayback.failed.has(1), false);
            const before = images.length;
            m.requestTrackedFrame(1);
            assert.equal(images.length, before);
        }
    }
    assert.equal(m._trackedPlayback.failed.has(1), true);
    m.requestReviewedState(3);
    advance(2200);
    m.reviewedPlaybackTick(2200);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    m.clearLayeredTimers();
});

test('explicit zero holds the final frame but does not block the next valid request', async () => {
    const { manager: m, advance } = realReviewedFixture();
    m.playLayeredAnimation(3, { returnToDefaultAfterMs: 0 }); // numeric target is one-based.
    await flushReviewed();
    m._trackedPlayback.lastIndex = 1;
    advance(2500);
    m.reviewedPlaybackTick(2500);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    assert.equal(m._reviewedHoldLast, true);
    m.requestReviewedState(3);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    m.clearLayeredTimers();
});

test('failed or paused emotion requests never publish a ghost emotion', async () => {
    const { manager: m } = realReviewedFixture();
    m.prepareReviewedEntry = async () => { throw Error('offline'); };
    m.setLayeredEmotion('happy');
    await flushReviewed();
    assert.equal(m.currentEmotion, null);
    m.pauseRendering();
    assert.equal(m.setLayeredEmotion('happy'), false);
    assert.equal(m.currentEmotion, null);
});

test('metadata validation rejects an invalid unselected clip before the current index changes', () => {
    const { manager: m } = realReviewedFixture();
    m.layeredMetadata.tracked_animations[3].duration_ms = -1;
    assert.throws(() => m.validateReviewedMetadata(m.layeredMetadata), /invalid tracked/);
    assert.throws(() => m.setLayeredStateIndex(3, { reviewedCommit: true }), /invalid tracked/);
    assert.equal(m.layeredStateIndex, 0);
});

test('all production reviewed tracks and groups pass the real runtime validator', () => {
    const { manager: m } = fixture();
    const metadata = JSON.parse(fs.readFileSync(path.join(__dirname, '../../frontend/pngtuber-packs/yui-chibi-source/metadata.actions.json'), 'utf8'));
    m.validateReviewedMetadata(metadata);
    assert.equal(Object.keys(metadata.tracked_animations).length, 23);
    metadata.reviewed_actions.groups.happy.push(999);
    assert.throws(() => m.validateReviewedMetadata(metadata), /invalid reviewed group/);
});

test('Alt+1 skips visit-only states for reviewed models and retains legacy cycling', () => {
    const { manager: m } = realReviewedFixture();
    m.layeredStateIndex = 5;
    let selected;
    m.setLayeredStateIndex = index => { selected = index; return true; };
    m.cycleLayeredState();
    assert.equal(selected, 0);
    delete m.layeredMetadata.reviewed_actions;
    m.cycleLayeredState();
    assert.equal(selected, 6);
});

test('recovered visit cleanup clears only its own marker without playing a return action', () => {
    const { manager: m } = realReviewedFixture();
    m._reviewedVisit = 'a';
    let requests = 0;
    m.requestReviewedState = () => { requests++; };
    m.handleReviewedVisit({ action: 'ended', visit_id: 'b', side: 'guest', recovery: true });
    assert.equal(m._reviewedVisit, 'a');
    m.handleReviewedVisit({ action: 'ended', visit_id: 'a', side: 'guest', recovery: true });
    assert.equal(m._reviewedVisit, null);
    assert.equal(requests, 0);
});

test('prewarm keeps its batch lock until both workers exit after one failure', async () => {
    const { manager: m } = realReviewedFixture();
    let calls = 0, finish;
    const pending = new Promise(resolve => { finish = resolve; });
    m.loadReviewedIdleFrame = () => ++calls === 1 ? Promise.reject(Error('decode')) : pending;
    m.warmReviewedIdle(0);
    const resource = m.reviewedIdleResources(0), batch = resource.warming;
    await flushReviewed();
    assert.equal(resource.warming, batch);
    m.warmReviewedIdle(0);
    assert.equal(calls, 2);
    m.pauseRendering();
    finish({ image: {} });
    await batch;
    assert.equal(resource.warming, null);
    assert.equal(calls, 2);
});

test('dispose releases decoded-entry promises and snapshots, cancelling late commits', async () => {
    const { manager: m } = realReviewedFixture();
    m.detachSpeechListeners = m.detachDragListeners = () => {};
    m._reviewedEntries = new Map([[0, Promise.resolve([{ image: {} }])]]);
    m._reviewedTransition = { snapshot: {} };
    let finish;
    m.prepareReviewedEntry = () => new Promise(resolve => { finish = resolve; });
    m.requestReviewedState(2);
    m.dispose();
    finish([{ image: {} }]);
    await flushReviewed();
    assert.equal(m._reviewedEntries.size, 0);
    assert.equal(m._reviewedTransition, null);
    assert.equal(m._reviewedSessionMetadata, null);
    assert.equal(m._trackedPlayback, null);
});

test('quiet reviewed idle uses the low-rate driver while speech and transitions remain full-rate', () => {
    const { manager: m } = realReviewedFixture();
    assert.equal(m._layeredAnimationNeedsFullRate(300), false);
    m.isSpeaking = true;
    assert.equal(m._layeredAnimationNeedsFullRate(300), true);
    m.isSpeaking = false;
    m._reviewedTransition = { at: 300 };
    assert.equal(m._layeredAnimationNeedsFullRate(320), true);
    m._reviewedTransition = null;
    m._trackedPlayback.data.loop = { crossfade_ms: 100 };
    assert.equal(m._layeredAnimationNeedsFullRate(1950), true);
});


test('hide/show resumes the selected pose without counting hidden time or replaying', async () => {
    const { manager: m, advance } = realReviewedFixture();
    m.container = { classList: { add() {}, remove() {} }, style: {} };
    m.startLayeredBlinkLoop = m.attachLayeredHotkeys = m.attachLayeredPlayEvent = m.attachLayeredPointerTracking = () => {};
    m.layeredAnimationStart = 100;
    advance(1100);
    m.hide();
    advance(5100);
    m.show();
    assert.equal(5100 - m.layeredAnimationStart, 1000);
    m.clearLayeredTimers();
});

test('typing during leisure decode cancels it and typing during playback interrupts it', async () => {
    const { manager: m, window } = realReviewedFixture();
    const activity = { proactiveEnabled: false, owner: true, busy: false, inputAt: 0 };
    window.getReviewedPNGTuberActivity = () => activity;
    let finish;
    m.prepareReviewedEntry = () => new Promise(resolve => { finish = resolve; });
    m.requestReviewedState(5, { source: 'reviewed-leisure' });
    activity.inputAt = 1000;
    finish([{ image: {} }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    m.prepareReviewedEntry = async () => [{ image: {} }];
    m.requestReviewedState(5, { source: 'reviewed-leisure' });
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 5);
    activity.inputAt = 1200;
    m.reviewedPlaybackTick(1500);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    m.clearLayeredTimers();
});


function visitBridgeFixture(throws = false) {
    const sandbox = {};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../static/visit/parent-bridge.js'), 'utf8'), sandbox);
    const messages = [];
    const window = {
        location: { origin: 'http://localhost' }, performance: { now: () => 10 },
        addEventListener() {}, dispatchEvent() {}, console: { warn() {} },
        setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
        pngtuberManager: { handleReviewedVisit(msg) { messages.push(msg); if (throws) throw Error('animation failed'); } }
    };
    const document = { getElementById: () => null, querySelector: () => null,
        body: { appendChild() {}, classList: { contains: () => false } },
        createElement: () => ({ style: {}, setAttribute() {}, remove() {} }) };
    const bridge = sandbox.NekoVisitParentBridge.createParentBridge({ window, document });
    const frame = { contentWindow: { postMessage() {} }, style: {}, remove() {} };
    Object.assign(bridge._state, { visitId: 'a', side: 'guest', phase: 'active', frame });
    return { bridge, frame, messages, window };
}

test('real parent normal-close fallback notifies once and completes bridge teardown', () => {
    const { bridge, frame, messages } = visitBridgeFixture();
    bridge._onFrameMessage({ source: frame.contentWindow, origin: 'http://localhost', data: { t: 'closed', code: 1000 } });
    assert.equal(messages.length, 1);
    assert.equal(messages[0].action, 'ended');
    assert.equal(messages[0].visit_id, 'a');
    assert.equal(bridge._state.frame, null);
    bridge.onStateChange({ type: 'visit_state_change', action: 'ended', visit_id: 'a', side: 'guest' });
    assert.equal(messages.length, 1);
});

test('a throwing model cannot block real parent ended-event teardown', () => {
    const { bridge, messages } = visitBridgeFixture(true);
    bridge.onStateChange({ type: 'visit_state_change', action: 'ended', visit_id: 'a', side: 'guest' });
    assert.equal(messages.length, 1);
    assert.equal(bridge._state.frame, null);
    assert.equal(bridge._state.phase, null);
});


test('a positive explicit API return delay is honored even before the clip ends', async () => {
    const { manager: m, advance } = realReviewedFixture({ fakeTimers: true });
    m.requestReviewedState(2, { returnToDefaultAfterMs: 40 });
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(139);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(140);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    m.clearLayeredTimers();
});


test('a permanently unavailable final mouth plate cannot deadlock the body queue', async () => {
    const { manager: m, images, advance } = realReviewedFixture();
    m.requestReviewedState(2);
    await flushReviewed();
    const data = m._trackedPlayback.data;
    data.speech_mouth_patches = true;
    data.frames[1].mouth_patch = { image: 'plate.png' };
    for (const time of [100, 400, 1000]) {
        advance(time);
        const offset = images.length;
        m.requestTrackedFrame(1);
        images[offset].onerror(); // plate
        images[offset + 1].onload(); // body decodes but is not published without its plate
        await flushReviewed();
        assert.equal(m._trackedPlayback.cache.has(1), false);
    }
    assert.equal(m._trackedPlayback.failed.has(1), true);
    m.requestReviewedState(3);
    advance(2200);
    m.reviewedPlaybackTick(2200);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    m.clearLayeredTimers();
});

test('a transient frame error can recover without resetting the action clock', async () => {
    const { manager: m, images, advance } = realReviewedFixture();
    m.requestReviewedState(2);
    await flushReviewed();
    const start = m.layeredAnimationStart;
    m.requestTrackedFrame(1);
    images.at(-1).onerror();
    await flushReviewed();
    advance(400);
    m.requestTrackedFrame(1);
    images.at(-1).onload();
    await flushReviewed();
    assert.equal(m._trackedPlayback.cache.has(1), true);
    assert.equal(m._trackedPlayback.failed.has(1), false);
    assert.equal(m.layeredAnimationStart, start);
    m.clearLayeredTimers();
});

test('actual input restarts the leisure countdown rather than adding a second quiet window', async () => {
    const { manager: m, window, advance, wallNow } = realReviewedFixture();
    const activity = { proactiveEnabled: false, owner: true, busy: false, inputAt: wallNow() };
    window.getReviewedPNGTuberActivity = () => activity;
    advance(100);
    m.reviewedPlaybackTick(100);
    advance(30090);
    m.reviewedPlaybackTick(30090);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    advance(30110);
    m.reviewedPlaybackTick(30110);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 5);
    m.clearLayeredTimers();
});


test('fallback speech opens on consecutive utterances after stopLipSync closes the viseme', t => {
    const { manager: m, context } = realReviewedFixture();
    delete m.stopSpeakingMouthAnimation;
    t.after(() => { m.stopSpeakingMouthAnimation(); m.clearLayeredTimers(); });
    m._trackedPlayback.cache.set(0, { name: 'decoded body' });
    for (let utterance = 0; utterance < 2; utterance++) {
        m.startSpeakingMouthAnimation();
        m.drawTrackedPlayback(context, 116 + utterance * 200);
        m.drawTrackedPlayback(context, 132 + utterance * 200);
        assert.ok(m._trackedPlayback.openness > 0, `utterance ${utterance + 1} stayed closed`);
        m.stopSpeakingMouthAnimation();
        m._trackedPlayback.openness = 0;
    }
    m.clearLayeredTimers();
});

test('missing-analyser fallback resets a closed viseme without disturbing an active analyser', t => {
    const { manager: m } = realReviewedFixture();
    delete m.stopSpeakingMouthAnimation;
    t.after(() => { m.stopSpeakingMouthAnimation(); m.clearLayeredTimers(); });
    m.stopLipSync();
    assert.equal(m.startLipSync(undefined), false);
    assert.equal(m._trackedViseme, 'a');
    m.stopSpeakingMouthAnimation();
    m.lipSyncFrame = 99;
    m._trackedViseme = 'i';
    m.startSpeakingMouthAnimation();
    assert.equal(m._trackedViseme, 'i');
    m.stopSpeakingMouthAnimation();
    m.clearLayeredTimers();
});


test('explicit return timer hands off to an accepted queued action', async () => {
    const { manager: m, advance } = realReviewedFixture({ fakeTimers: true });
    m.requestReviewedState(2, { returnToDefaultAfterMs: 80 });
    await flushReviewed();
    m.requestReviewedState(3);
    advance(179);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(180);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    m.clearLayeredTimers();
});

test('real commit draws and dispatches only after decoded body and plate are installed', async () => {
    const { manager: m, window } = realReviewedFixture();
    let draws = 0, events = 0;
    m.layeredMetadata.tracked_animations[2].frames[0].mouth_patch = { image: 'plate.png' };
    m.prepareReviewedEntry = async () => [{ image: { name: 'target' }, patch: { name: 'plate' } }];
    m.drawLayeredState = () => {
        draws++;
        assert.equal(m._trackedPlayback.cache.get(0).name, 'target');
        assert.equal(m._trackedPlayback.mouthPatches.get('plate.png').name, 'plate');
    };
    window.dispatchEvent = () => { events++; assert.equal(m._trackedPlayback.cache.get(0).name, 'target'); };
    m.requestReviewedState(2);
    await flushReviewed();
    assert.equal(draws, 1);
    assert.equal(events, 1);
    m.clearLayeredTimers();
});

test('switching to a plain image adapter releases the old reviewed session', async () => {
    const { manager: m } = realReviewedFixture();
    m._reviewedEntries = new Map([[2, Promise.resolve([{ image: {} }])]]);
    m._reviewedTransition = { snapshot: {} };
    m._reviewedVisit = 'a';
    assert.equal(await m.setupLayeredAdapter({ config: { adapter: 'image' } }), false);
    assert.equal(m._reviewedEntries.size, 0);
    assert.equal(m._reviewedTransition, null);
    assert.equal(m._reviewedVisit, null);
    assert.equal(m._trackedPlayback, null);
});

test('a stale adapter setup cannot cancel the newer reviewed session', async () => {
    const { manager: m } = realReviewedFixture();
    const entries = m._reviewedEntries = new Map([[2, Promise.resolve([])]]);
    const token = m._reviewedRequest;
    assert.equal(await m.setupLayeredAdapter({ isCurrentLoad: () => false }), false);
    assert.equal(m._reviewedEntries, entries);
    assert.equal(m._reviewedRequest, token);
});

test('ordinary requests cannot select visit-only actions or forge a source string', async () => {
    const { manager: m } = realReviewedFixture();
    for (const index of [7, 6]) {
        assert.equal(m.requestReviewedState(index, { source: 'api' }), false);
        assert.equal(m.requestReviewedState(index, { source: 'reviewed-visit' }), false);
    }
    assert.equal(m.handleReviewedVisit({ action: 'departed', visit_id: 'a', side: 'guest' }), true);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 7);
    m._trackedPlayback.lastIndex = 1;
    m.layeredAnimationStart = -2500;
    m.handleReviewedVisit({ action: 'ended', visit_id: 'a', side: 'guest' });
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 6);
    m.clearLayeredTimers();
});

test('new typing during leisure retry backoff cancels the original request', async () => {
    const { manager: m, window, advance, wallNow } = realReviewedFixture();
    const activity = { proactiveEnabled: false, owner: true, busy: false, inputAt: 0 };
    window.getReviewedPNGTuberActivity = () => activity;
    let attempts = 0;
    m.prepareReviewedEntry = async () => { attempts++; throw Error('temporary'); };
    m.requestReviewedState(5, { source: 'reviewed-leisure' });
    await flushReviewed();
    activity.inputAt = wallNow();
    advance(500);
    m.reviewedPlaybackTick(500);
    await flushReviewed();
    assert.equal(attempts, 1);
    assert.equal(m.layeredStateIndex, 0);
    assert.equal(m._reviewedRetry, null);
});

test('input during a previous action never backdates the next idle countdown', async () => {
    const { manager: m, window, advance, wallNow } = realReviewedFixture();
    const activity = { proactiveEnabled: false, owner: true, busy: false, inputAt: wallNow() };
    window.getReviewedPNGTuberActivity = () => activity;
    advance(10100); // Enter idle ten seconds after the input.
    m._reviewedIdleSince = null;
    m.reviewedPlaybackTick(10100);
    advance(30100);
    m.reviewedPlaybackTick(30100);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    advance(40101);
    m.reviewedPlaybackTick(40101);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 5);
    m.clearLayeredTimers();
});

test('abnormal guest close followed by an ended recovery snapshot clears the real model marker only', async () => {
    const { bridge, frame, messages, window } = visitBridgeFixture();
    const { manager: m } = realReviewedFixture();
    let actions = 0;
    m._reviewedVisit = 'a';
    m.requestReviewedState = () => { actions++; return true; };
    window.pngtuberManager.handleReviewedVisit = msg => { messages.push(msg); return m.handleReviewedVisit(msg); };
    bridge._onFrameMessage({ source: frame.contentWindow, origin: 'http://localhost', data: { t: 'closed', code: 1006 } });
    assert.equal(bridge._state.side, null);
    assert.equal(bridge._state.closedSide, 'guest');
    window.nekoLocalMutationSecurity = { getMutationHeaders: async () => ({ 'X-CSRF-Token': 'token' }) };
    window.fetch = async () => ({ ok: true, json: async () => ({ active: false }) });
    assert.equal(await bridge.recoverAfterReload('YUI', 10000), true);
    assert.equal(m._reviewedVisit, null);
    assert.equal(actions, 0);
    assert.equal(messages[0].recovery, true);
    assert.equal(bridge._state.closedSide, null);
    assert.equal(m.handleReviewedVisit({ action: 'departed', visit_id: 'b', side: 'guest' }), true);
});

test('an ended event without side after abnormal close still cleans a guest visit', () => {
    const { bridge, frame, messages } = visitBridgeFixture();
    bridge._onFrameMessage({ source: frame.contentWindow, origin: 'http://localhost', data: { t: 'closed', code: 1006 } });
    bridge.onStateChange({ type: 'visit_state_change', action: 'ended', visit_id: 'a' });
    assert.equal(messages.length, 1);
    assert.equal(messages[0].side, 'guest');
    assert.equal(messages[0].visit_id, 'a');
});

test('unknown-visit transport close clears only the matching guest session without a return animation', () => {
    const { bridge, frame, messages } = visitBridgeFixture();
    bridge._onFrameMessage({ source: frame.contentWindow, origin: 'http://localhost', data: { t: 'closed', code: 4404 } });
    assert.equal(messages.length, 1);
    assert.equal(messages[0].action, 'ended');
    assert.equal(messages[0].recovery, true);
});


test('recovery invalidating a queued visit action still allows the current action to finish', async () => {
    const { manager: m, advance } = realReviewedFixture();
    m.requestReviewedState(2);
    await flushReviewed();
    m.handleReviewedVisit({ action: 'departed', visit_id: 'a', side: 'guest' });
    assert.equal(m._reviewedQueued.index, 7);
    m.handleReviewedVisit({ action: 'ended', visit_id: 'a', side: 'guest', recovery: true });
    m._trackedPlayback.lastIndex = 1;
    advance(2500);
    m.reviewedPlaybackTick(2500);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    assert.equal(m._reviewedVisit, null);
    m.clearLayeredTimers();
});


test('reviewed fixture catalog matches disjoint drag and visit groups', () => {
    const { manager: m } = realReviewedFixture();
    const metadata = m.layeredMetadata;
    m.validateReviewedMetadata(metadata);
    const seen = new Set();
    for (const [group, indices] of Object.entries(metadata.reviewed_actions.groups)) {
        for (const index of indices) {
            assert.equal(metadata.state_catalog[index].group, group);
            assert.equal(seen.has(index), false);
            seen.add(index);
        }
    }
    assert.equal(seen.size, metadata.state_count);
});

for (const nextIndex of [4, 3]) {
    test(`queued handoff preserves a newer C request (${nextIndex}) accepted while B decodes`, async t => {
        const { manager: m, window, advance } = realReviewedFixture({ fakeTimers: true });
        t.after(() => m.clearLayeredTimers());
        const committed = [];
        window.dispatchEvent = () => committed.push(m.layeredStateIndex);
        let resolveB;
        const bReady = new Promise(resolve => { resolveB = resolve; });
        m.prepareReviewedEntry = index => index === 3 ? bReady : Promise.resolve([{ image: {} }]);
        m.requestReviewedState(2, { returnToDefaultAfterMs: 80 });
        await flushReviewed();
        m.requestReviewedState(3);
        const bQueue = m._reviewedQueued;
        advance(180);
        await flushReviewed();
        assert.equal(m._reviewedPreparing, true);
        assert.equal(m.layeredStateIndex, 2);
        m.requestReviewedState(nextIndex);
        const cQueue = m._reviewedQueued;
        assert.notEqual(cQueue, bQueue);
        resolveB([{ image: { name: 'decoded B' } }]);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, 3);
        assert.equal(m._reviewedQueued, cQueue);
        m._trackedPlayback.lastIndex = 1;
        advance(2200);
        m.reviewedPlaybackTick(2200);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, nextIndex);
        assert.equal(m._reviewedQueued, null);
        m._trackedPlayback.lastIndex = 1;
        advance(4300);
        m.reviewedPlaybackTick(4300);
        await flushReviewed();
        assert.deepEqual(committed, [2, 3, nextIndex, 0]);
    });
}

test('queued handoff retains newer C through B transient retry', async t => {
    const { manager: m, advance } = realReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    let failB, attempts = 0;
    m.prepareReviewedEntry = index => {
        if (index === 3 && ++attempts === 1) return new Promise((_, reject) => { failB = reject; });
        return Promise.resolve([{ image: {} }]);
    };
    m.requestReviewedState(3);
    m.finishReviewedAction();
    m.requestReviewedState(4);
    const cQueue = m._reviewedQueued;
    failB(Error('temporary B decode failure'));
    await flushReviewed();
    advance(400);
    m.reviewedPlaybackTick(400);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    assert.equal(m._reviewedQueued, cQueue);
    m._trackedPlayback.lastIndex = 1;
    advance(2500);
    m.reviewedPlaybackTick(2500);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
    assert.equal(m._reviewedQueued, null);
});

test('queued handoff does not discard C when B exhausts its retries', async t => {
    const { manager: m, advance } = realReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    let failB;
    m.prepareReviewedEntry = index => index === 3
        ? new Promise((_, reject) => { failB = reject; }) : Promise.resolve([{ image: {} }]);
    m.requestReviewedState(3);
    m.finishReviewedAction();
    m.requestReviewedState(4);
    for (const time of [100, 400, 1000]) {
        advance(time);
        if (time !== 100) m.reviewedPlaybackTick(time);
        failB(Error('permanent B decode failure'));
        await flushReviewed();
    }
    assert.equal(m._reviewedQueued.index, 4);
    advance(2100);
    m.reviewedPlaybackTick(2100);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
    assert.equal(m._reviewedQueued, null);
});

test('queued handoff consumes its own B request so B never replays on completion', async t => {
    const { manager: m, window, advance } = realReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    const committed = [];
    window.dispatchEvent = () => committed.push(m.layeredStateIndex);
    m.requestReviewedState(2);
    await flushReviewed();
    m.requestReviewedState(3);
    m._trackedPlayback.lastIndex = 1;
    advance(2200);
    m.reviewedPlaybackTick(2200);
    await flushReviewed();
    assert.equal(m._reviewedQueued, null);
    m._trackedPlayback.lastIndex = 1;
    advance(4300);
    m.reviewedPlaybackTick(4300);
    await flushReviewed();
    assert.deepEqual(committed, [2, 3, 0]);
});

for (const durationMs of [0, '0']) {
    test(`emotion duration ${JSON.stringify(durationMs)} holds the reviewed final frame`, async t => {
        const { manager: m, advance } = realReviewedFixture();
        t.after(() => m.clearLayeredTimers());
        m.reviewedPick = () => 2;
        assert.equal(m.setEmotion('happy', { durationMs }), true);
        await flushReviewed();
        m._trackedPlayback.lastIndex = 1;
        advance(2500);
        m.reviewedPlaybackTick(2500);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, 2);
        assert.equal(m._reviewedHoldLast, true);
        assert.equal(m.currentEmotion, 'happy');
        m.requestReviewedState(0);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, 0);
    });
}

test('emotion duration positive value hands off at the requested visible delay', async t => {
    const { manager: m, advance } = realReviewedFixture({ fakeTimers: true });
    t.after(() => m.clearLayeredTimers());
    m.reviewedPick = () => 2;
    m.setEmotion('happy', { durationMs: 80 });
    await flushReviewed();
    assert.equal(m._reviewedReturnDeadline, 180);
    assert.equal(m.layeredStateIndex, 2);
    advance(179);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(180);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
    assert.equal(m.currentEmotion, null);
});

test('emotion duration omitted follows the clip rather than creating a return timer', async t => {
    const { manager: m, advance } = realReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.reviewedPick = () => 2;
    m.setEmotion('happy');
    await flushReviewed();
    assert.equal(m._reviewedReturnDeadline, null);
    assert.equal(m._reviewedHoldLast, false);
    assert.equal(m.layeredStateReturnTimer, null);
    m._trackedPlayback.lastIndex = 1;
    advance(2200);
    m.reviewedPlaybackTick(2200);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});


function timedReviewedFixture() {
    const f = realReviewedFixture({ fakeTimers: true });
    const m = f.manager;
    m.container = { style: { display: 'block' }, classList: { contains: () => false, add() {}, remove() {} } };
    m.attachLayeredHotkeys = m.attachLayeredPlayEvent = m.attachLayeredPointerTracking = () => {};
    return f;
}

test('direct replacement C discards pending B and its old queue without replay', async t => {
    const { manager: m, window, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    const events = [];
    window.dispatchEvent = () => events.push(m.layeredStateIndex);
    m.requestReviewedState(2);
    await flushReviewed();
    let resolveB;
    m.prepareReviewedEntry = index => index === 3
        ? new Promise(resolve => { resolveB = resolve; }) : Promise.resolve([{ image: {} }]);
    m.requestReviewedState(3);
    m._trackedPlayback.lastIndex = 1;
    advance(2100);
    m.reviewedPlaybackTick(2100);
    assert.equal(m._reviewedPreparing, true);
    m.requestReviewedState(4);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
    assert.equal(m._reviewedQueued, null);
    resolveB([{ image: { name: 'stale B' } }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
    m._trackedPlayback.lastIndex = 1;
    advance(4100);
    m.reviewedPlaybackTick(4100);
    await flushReviewed();
    assert.deepEqual(events, [2, 4, 0]);
});

test('same playing clip accepts emotion hold policy without restarting its clock', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    const start = m.layeredAnimationStart;
    advance(300);
    m.reviewedPick = () => 2;
    m.setEmotion('happy', { durationMs: 0 });
    assert.equal(m.currentEmotion, 'happy');
    assert.equal(m.activeLayeredEmotionIndex, 2);
    assert.equal(m.layeredAnimationStart, start);
    assert.equal(m.layeredStateReturnTimer, null);
    advance(2600);
    m._trackedPlayback.lastIndex = 1;
    m.reviewedPlaybackTick(2600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
});

test('same playing clip starts a new explicit deadline at the policy update', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    const start = m.layeredAnimationStart;
    advance(400);
    m.reviewedPick = () => 2;
    m.setEmotion('happy', { durationMs: 500 });
    assert.equal(m.layeredAnimationStart, start);
    advance(899);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(900);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('same playing clip with no explicit duration leaves its existing deadline intact', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    advance(300);
    m.requestReviewedState(2);
    advance(599);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('same clip explicit policy invalidates an older pending replacement', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    let resolveB;
    m.prepareReviewedEntry = () => new Promise(resolve => { resolveB = resolve; });
    m.requestReviewedState(3);
    m._trackedPlayback.lastIndex = 1;
    advance(2100);
    m.reviewedPlaybackTick(2100);
    m.reviewedPick = () => 2;
    m.setEmotion('happy', { durationMs: 0 });
    resolveB([{ image: {} }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    assert.equal(m._reviewedHoldLast, true);
    assert.equal(m._reviewedQueued, null);
});

test('fallback idle immediately hands off C accepted during its entry decode', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    let resolveIdle;
    m.prepareReviewedEntry = index => {
        if (index === 3) return Promise.reject(Error('B unavailable'));
        if (index === 0) return new Promise(resolve => { resolveIdle = resolve; });
        return Promise.resolve([{ image: {} }]);
    };
    m.requestReviewedState(3);
    m.finishReviewedAction();
    await flushReviewed();
    for (const time of [350, 850, 1850]) {
        advance(time);
        m.reviewedPlaybackTick(time);
        await flushReviewed();
    }
    assert.equal(m._reviewedPreparing, true);
    m.requestReviewedState(4);
    assert.equal(m._reviewedQueued.index, 4);
    resolveIdle([{ image: {} }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
    assert.equal(m._reviewedQueued, null);
});

for (const nextIndex of [4, 5]) {
    test(`retry handoff reads replacement D (${nextIndex}) identity and options together`, async t => {
        const { manager: m, advance } = timedReviewedFixture();
        t.after(() => m.clearLayeredTimers());
        m.requestReviewedState(2);
        await flushReviewed();
        let failB;
        m.prepareReviewedEntry = index => index === 3
            ? new Promise((_, reject) => { failB = reject; }) : Promise.resolve([{ image: {} }]);
        m.requestReviewedState(3);
        m.finishReviewedAction();
        m.requestReviewedState(4, { returnToDefaultAfterMs: 500 });
        for (const time of [100, 350, 850]) {
            advance(time);
            if (time !== 100) m.reviewedPlaybackTick(time);
            failB(Error('B unavailable'));
            await flushReviewed();
        }
        advance(1000);
        m.requestReviewedState(nextIndex, { returnToDefaultAfterMs: 0 });
        const latest = m._reviewedQueued;
        advance(1850);
        m.reviewedPlaybackTick(1850);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, nextIndex);
        assert.equal(m._reviewedHoldLast, true);
        assert.equal(m._reviewedQueued, null);
        assert.equal(m.layeredStateReturnTimer, null);
        assert.equal(latest.options.returnToDefaultAfterMs, 0);
        advance(4000);
        m._trackedPlayback.lastIndex = 1;
        m.reviewedPlaybackTick(4000);
        await flushReviewed();
        assert.equal(m.layeredStateIndex, nextIndex);
    });
}

test('pause/resume re-arms only the remaining explicit return deadline', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    const start = m.layeredAnimationStart;
    advance(300);
    m.pauseRendering();
    advance(500);
    m.resumeRendering();
    assert.equal(m.layeredAnimationStart, start + 200);
    advance(599);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('pause past an explicit deadline hands off immediately on resume', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    advance(300);
    m.pauseRendering();
    advance(900);
    m.resumeRendering();
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('repeated pause/resume never extends the explicit deadline', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    for (const [paused, resumed] of [[300, 400], [450, 550]]) {
        advance(paused);
        m.pauseRendering();
        advance(resumed);
        m.resumeRendering();
    }
    advance(599);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('pause/resume without an explicit deadline keeps natural completion semantics', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    advance(300);
    m.pauseRendering();
    advance(900);
    m.resumeRendering();
    m._trackedPlayback.lastIndex = 1;
    advance(1000);
    m.reviewedPlaybackTick(1000);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(2699);
    m.reviewedPlaybackTick(2699);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(2700);
    m.reviewedPlaybackTick(2700);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('held-last policy survives pause/resume without a return timer', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 0 });
    await flushReviewed();
    advance(300);
    m.pauseRendering();
    advance(2500);
    m.resumeRendering();
    m._trackedPlayback.lastIndex = 1;
    m.reviewedPlaybackTick(2500);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    assert.equal(m.layeredStateReturnTimer, null);
});

test('hide/show moves the explicit deadline with the frozen body clock', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    advance(300);
    m.hide();
    advance(1300);
    m.show();
    assert.equal(m.layeredAnimationStart, 1100);
    advance(1599);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(1600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('deadline fallback does not wait for the natural final frame', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    m.clearLayeredTimers(); // Exercise the render-clock safety net instead of the timer.
    advance(599);
    m.reviewedPlaybackTick(599);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(600);
    m.reviewedPlaybackTick(600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('an idle deadline settles once without repeatedly scheduling redundant idle requests', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(1, { reviewedIdleEntry: true, returnToDefaultAfterMs: 500 });
    await flushReviewed();
    advance(600);
    await flushReviewed();
    assert.equal(m._reviewedReturnDeadline, null);
    const token = m._reviewedRequest;
    for (let time = 601; time < 620; time++) m.reviewedPlaybackTick(time);
    assert.equal(m._reviewedRequest, token);
});


test('a direct request from the idle commit event wins over an older queued handoff', async t => {
    const { manager: m, window } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    let resolveIdle;
    m.prepareReviewedEntry = index => index === 0
        ? new Promise(resolve => { resolveIdle = resolve; }) : Promise.resolve([{ image: {} }]);
    m.requestReviewedState(0, { force: true, failureFallback: true });
    m.requestReviewedState(4);
    window.dispatchEvent = () => {
        if (m.layeredStateIndex === 0) m.requestReviewedState(5);
    };
    resolveIdle([{ image: {} }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 5);
    assert.equal(m._reviewedQueued, null);
});


test('deadline tests retain the real blink initializer that clears layered timers', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    assert.equal(Object.hasOwn(m, 'startLayeredBlinkLoop'), false);
    m.requestReviewedState(2, { returnToDefaultAfterMs: 500 });
    await flushReviewed();
    advance(300);
    m.pauseRendering();
    advance(500);
    m.resumeRendering();
    assert.notEqual(m.layeredStateReturnTimer, null);
    advance(599);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(600);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 0);
});

test('old deadline cannot supersede a newer B request while B is decoding', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 5000 });
    await flushReviewed();
    m._trackedPlayback.lastIndex = 1;
    advance(3100);
    let resolveB;
    m.prepareReviewedEntry = () => new Promise(resolve => { resolveB = resolve; });
    m.requestReviewedState(3);
    const token = m._reviewedRequest;
    advance(5100);
    m.reviewedPlaybackTick(5100);
    assert.equal(m._reviewedRequest, token);
    assert.equal(m._reviewedPreparing, true);
    resolveB([{ image: {} }]);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
});

test('old deadline cannot supersede a newer B request during retry backoff', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2, { returnToDefaultAfterMs: 5000 });
    await flushReviewed();
    m._trackedPlayback.lastIndex = 1;
    advance(5000);
    let attempts = 0;
    m.prepareReviewedEntry = async () => {
        if (++attempts === 1) throw Error('temporary B decode failure');
        return [{ image: {} }];
    };
    m.requestReviewedState(3);
    await flushReviewed();
    const token = m._reviewedRequest;
    advance(5100);
    assert.equal(m._reviewedRequest, token);
    assert.equal(m._reviewedRetry.at, 5250);
    advance(5250);
    m.reviewedPlaybackTick(5250);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    assert.equal(attempts, 2);
});


test('new queued work recovers from exhausted entry and idle-fallback retries', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    const data = m._trackedPlayback.data;
    data.duration_ms = 10000;
    data.frames[0].duration_ms = 5000;
    data.frames[1].time_ms = 5000;
    data.frames[1].duration_ms = 5000;
    const attempts = { action: 0, idle: 0 };
    m.prepareReviewedEntry = async index => {
        if (index === 3) { attempts.action++; throw Error('action unavailable'); }
        if ([0,1].includes(index)) { attempts.idle++; throw Error('idle unavailable'); }
        return [{ image: {} }];
    };
    m.requestReviewedState(3, { force: true });
    await flushReviewed();
    for (const time of [350, 850, 1850, 2100, 2600]) {
        advance(time);
        m.reviewedPlaybackTick(time);
        await flushReviewed();
    }
    assert.ok(m._reviewedRecovery);
    assert.deepEqual(attempts, { action: 3, idle: 3 });
    assert.equal(m.requestReviewedState(4), true);
    assert.equal(m._reviewedRetry, null);
    advance(2700);
    m.reviewedPlaybackTick(2700);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 2);
    advance(10100);
    m._trackedPlayback.lastIndex = 1;
    m.reviewedPlaybackTick(10100);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
    assert.deepEqual(attempts, { action: 3, idle: 3 });
});

test('accepting queued work does not cancel ordinary retry backoff', async t => {
    const { manager: m, advance } = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    m.requestReviewedState(2);
    await flushReviewed();
    let attempts = 0;
    m.prepareReviewedEntry = async index => {
        if (index === 3 && ++attempts === 1) throw Error('temporary');
        return [{ image: {} }];
    };
    m.requestReviewedState(3, { force: true });
    await flushReviewed();
    const retry = m._reviewedRetry;
    m.requestReviewedState(4);
    assert.equal(m._reviewedRetry, retry);
    advance(349);
    m.reviewedPlaybackTick(349);
    await flushReviewed();
    assert.equal(attempts, 1);
    advance(350);
    m.reviewedPlaybackTick(350);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 3);
    assert.equal(m._reviewedQueued.index, 4);
    advance(2350);
    m._trackedPlayback.lastIndex = 1;
    m.reviewedPlaybackTick(2350);
    await flushReviewed();
    assert.equal(m.layeredStateIndex, 4);
});


for (const index of [0, 2]) test(`touch zoom preserves tracked state ${index} and its return deadline`, async t => {
    const {manager:m, advance} = timedReviewedFixture();
    t.after(() => m.clearLayeredTimers());
    if (index) { m.requestReviewedState(index, {returnToDefaultAfterMs:500}); await flushReviewed(); }
    const playback=m._trackedPlayback, start=m.layeredAnimationStart, deadline=m._reviewedReturnDeadline;
    m.setModelDraggingState=()=>{};
    m.restoreStateImage=()=>{throw Error('tracked zoom must not restore/restart')};
    m.restartLayeredAnimationLoop=()=>{throw Error('tracked zoom must not rewind')};
    let saved=0;
    m.snapModelIntoScreen=async()=>{};m.saveOrStageCurrentConfig=async()=>saved++;
    m.isDragCompletionCurrent=()=>true;
    for(const changed of [false,true]) {
        m._touchZoomState={changed};await m.endTouchZoom();
        assert.equal(m._trackedPlayback,playback);assert.equal(m.layeredAnimationStart,start);
        assert.equal(m._reviewedReturnDeadline,deadline);
    }
    assert.equal(saved,1);
    if(index) {advance(600);await flushReviewed();assert.equal(m.layeredStateIndex,0);}
});

test('untracked touch zoom retains legacy restore behavior', async () => {
    const {manager:m}=fixture();m._trackedPlayback=null;
    let restored=0,restarted=0;
    m.setModelDraggingState=()=>{};m.updateLockIconPosition=()=>{};
    m.restoreStateImage=()=>restored++;m.restartLayeredAnimationLoop=()=>restarted++;
    m._touchZoomState={changed:false};await m.endTouchZoom();
    assert.equal(restored,1);assert.equal(restarted,1);
});

test('cache pressure retains the displayed body and its matching mouth plate', async () => {
    const {manager:m,images,context}=fixture();
    const p=m._trackedPlayback;p.data.speech_mouth_patches=true;
    p.data.frames.forEach((f,i)=>f.mouth_patch={image:`plates/${i}.png`});
    const body=p.cache.get(0), plate={name:'displayed-mouth'};p.mouthPatches.set('plates/0.png',plate);
    for(let i=1;i<15;i++) {
        m.requestTrackedFrame(i);images.at(-2).onload();images.at(-1).onload();
        await new Promise(resolve=>setImmediate(resolve));
    }
    assert.equal(p.cache.size,12);assert.equal(p.cache.get(0),body);
    assert.equal(p.mouthPatches.get('plates/0.png'),plate);
    const drawn=[];context.drawImage=(image)=>drawn.push(image);
    const ctx=new Proxy({drawImage:(image)=>drawn.push(image)},{get:(o,k)=>o[k]||(()=>{})});
    m.drawTrackedPlayback(ctx,1900);
    assert.ok(drawn.includes(body),'slow next frame keeps the displayed body');
});

test('renderTarget snapshots do not advance state, fetch frames, change mouth clocks or draw main canvas', () => {
    const {manager:m,context}=fixture();
    m.isLayeredActive=()=>true;m.canvasElement={getContext:()=>{throw Error('main canvas touched')}};
    m.layeredMetadata={canvas:{width:720,height:960}};
    m.reviewedPlaybackTick=()=>{throw Error('snapshot advanced action')};
    m.requestTrackedFrame=()=>{throw Error('snapshot loaded frames')};
    const transition={at:10,snapshot:{}};m._reviewedTransition=transition;
    const p=m._trackedPlayback;p.lastDraw=100;p.openness=.3;
    m.drawLayeredState('idle',9999,{canvas:{width:720,height:960,getContext:()=>context},scaleX:1,scaleY:1});
    assert.equal(p.lastIndex,0);assert.equal(p.lastDraw,100);assert.equal(p.openness,.3);
    assert.equal(m._reviewedTransition,transition);
});

for(const order of ['pause-hide-resume-show','hide-pause-show-resume']) test(`overlapping ${order} freezes the body only once`, async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    const first=order.startsWith('pause')?'pauseRendering':'hide';
    const second=first==='hide'?'pauseRendering':'hide';
    advance(300);m[first]();advance(500);m[second]();
    advance(900);m[first==='hide'?'show':'resumeRendering']();
    advance(1300);m[second==='hide'?'show':'resumeRendering']();
    assert.equal(m.layeredAnimationStart,1100);
    assert.equal(m._reviewedBodyFrozenAt,null);
});

test('pause keeps legacy model timeline behavior unchanged', () => {
    const {manager:m,advance}=fixture();m.isLayeredActive=()=>false;
    m.container={style:{display:'block'},classList:{contains:()=>false}};
    m.layeredAnimationStart=100;advance(300);m.pauseRendering();advance(900);m.resumeRendering();
    assert.equal(m.layeredAnimationStart,100);
});


for(const explicit of [false,true]) test(`paused queue survives and hands off at ${explicit?'explicit deadline':'natural completion'}`, async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2,explicit?{returnToDefaultAfterMs:500}:{});await flushReviewed();
    m.requestReviewedState(3);const queued=m._reviewedQueued;assert.ok(queued);
    advance(300);m.pauseRendering();m.pauseRendering();advance(900);
    assert.equal(m._reviewedQueued,queued);m.resumeRendering();await flushReviewed();
    if(!explicit) {
        assert.equal(m.layeredStateIndex,2);m._trackedPlayback.lastIndex=1;
        advance(2700);m.reviewedPlaybackTick(2700);await flushReviewed();
    }
    assert.equal(m.layeredStateIndex,3);assert.equal(m._reviewedQueued,null);
});


test('manual drawing while reviewed playback is paused uses the frozen body phase', async t => {
    const {manager:m,advance,context}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2);await flushReviewed();
    const p=m._trackedPlayback;p.cache.set(1,{name:'later'});
    advance(300);m.pauseRendering();advance(1800);
    m.drawTrackedPlayback(context,1800);
    assert.equal(p.lastIndex,0,'show or an explicit redraw cannot skip ahead while paused');
    m.resumeRendering();m.drawTrackedPlayback(context,1800);
    assert.equal(p.lastIndex,0,'resuming uses the same visible phase');
});


test('tracked legacy touch zoom releases a real drag slot but a stationary touch does not replace state', async () => {
    const {manager:m,metadata}=speechEndFixture();
    metadata.state_catalog[1].name='drag';m.setModelDraggingState=()=>{};
    m.isDragCompletionCurrent=()=>true;m.snapModelIntoScreen=async()=>{};m.saveOrStageCurrentConfig=async()=>{};
    m.showDragImage();assert.equal(m.layeredStateIndex,1);
    assert.ok(m.layeredTransientSlots.some(slot=>slot.kind==='drag'));
    m._touchZoomState={changed:true};await m.endTouchZoom();
    assert.equal(m.layeredStateIndex,0);assert.equal(m.layeredTransientSlots.some(slot=>slot.kind==='drag'),false);
    const playback=m._trackedPlayback,start=m.layeredAnimationStart;
    m._touchZoomState={changed:false};await m.endTouchZoom();
    assert.equal(m._trackedPlayback,playback);assert.equal(m.layeredAnimationStart,start);
});

test('crossfade snapshot reuses the displayed weights without advancing or requesting frames', () => {
    const {manager:m,context}=fixture();const p=m._trackedPlayback;
    p.data.loop={crossfade_ms:200};p.data.width=720;p.data.height=960;
    m.layeredMetadata={canvas:{width:720,height:960}};
    for(let i=0;i<p.data.frames.length;i++)p.cache.set(i,{index:i});
    const samples=[];let alpha=1;
    const ctx=new Proxy({}, {get:(o,k)=>k==='drawImage'?()=>samples.push(alpha):()=>{},set:(o,k,v)=>{if(k==='globalAlpha')alpha=v;return true}});
    m.drawTrackedPlayback(ctx,1850);
    const selection=p.lastSelection,weights=samples.slice();assert.ok(selection.mix>0&&selection.mix<1);
    assert.equal(weights.length,2);samples.length=0;
    const lastDraw=p.lastDraw,openness=p.openness,lastIndex=p.lastIndex;
    m.requestTrackedFrame=()=>{throw Error('snapshot requested frames')};
    m.reviewedPlaybackTick=()=>{throw Error('snapshot advanced action')};
    m.drawTrackedPlayback(ctx,99999,true);
    assert.deepEqual(samples,weights);assert.equal(p.lastSelection,selection);
    assert.equal(p.lastDraw,lastDraw);assert.equal(p.openness,openness);assert.equal(p.lastIndex,lastIndex);
});


for (const scale of [1,.5]) test(`transition snapshot uses logical bounds at render scale ${scale}`, () => {
    const {manager:m}=fixture();m.hasReviewedActions=()=>true;
    m.layeredCanvasLogicalWidth=720;m.layeredCanvasLogicalHeight=960;
    const snapshot={width:720*scale,height:960*scale};
    m._reviewedTransition={at:100,snapshot};
    const drawn=[];const ctx={save(){},restore(){},drawImage(...args){drawn.push(args)}};
    m.drawReviewedTransition(ctx,140);
    assert.deepEqual(drawn,[[snapshot,0,0,720,960]]);
    assert.equal(ctx.globalAlpha,.5);
});


test('idle entry exhaustion switches to the other healthy idle and clears recovery', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());delete m.reviewedChooseIdle;
    m.requestReviewedState(2);await flushReviewed();m._trackedPlayback.lastIndex=1;
    const attempts=[];m.prepareReviewedEntry=async index=>{attempts.push(index);if(index===0)throw Error('idle-1 down');return [{image:{}}]};
    m.requestReviewedState(0,{force:true,reviewedIdleEntry:true});await flushReviewed();
    for(const time of [350,850,1100]) {advance(time);m.reviewedPlaybackTick(time);await flushReviewed();}
    assert.equal(m.layeredStateIndex,1);assert.deepEqual(attempts,[0,0,0,1]);assert.equal(m._reviewedRecovery,null);
});

test('both unavailable idle entries recover after cooldown without external requests or frame storms', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());delete m.reviewedChooseIdle;
    m.requestReviewedState(2);await flushReviewed();m._trackedPlayback.lastIndex=1;
    let online=false,calls=0;m.prepareReviewedEntry=async()=>{calls++;if(!online)throw Error('offline');return [{image:{}}]};
    m.requestReviewedState(0,{force:true,reviewedIdleEntry:true});await flushReviewed();
    for(const time of [350,850,1100,1350,1850]) {advance(time);m.reviewedPlaybackTick(time);await flushReviewed();}
    assert.equal(calls,6);assert.ok(m._reviewedRecovery);
    for(let time=2000;time<31000;time+=100){advance(time);m.reviewedPlaybackTick(time);await flushReviewed();}
    assert.equal(calls,6,'known unavailable entries remain quarantined');
    online=true;advance(32000);m.reviewedPlaybackTick(32000);await flushReviewed();
    assert.ok([0,1].includes(m.layeredStateIndex));assert.equal(m._reviewedRecovery,null);
});

test('idle frame failure quarantine expires for real tracked frame loads', async t => {
    const {manager:m,images,advance}=realReviewedFixture();t.after(()=>m.clearLayeredTimers());
    const p=m._trackedPlayback;
    for(const time of [100,400,1000]) {advance(time);m.requestTrackedFrame(1);images.at(-1).onerror();await flushReviewed();}
    assert.equal(p.failed.has(1),true);const count=images.length;
    advance(30999);m.requestTrackedFrame(1);assert.equal(images.length,count);
    advance(31000);m.requestTrackedFrame(1);images.at(-1).onload();await flushReviewed();
    assert.equal(p.failed.has(1),false);assert.ok(p.cache.has(1));assert.equal(p.idleResource.failures.has(1),false);
});

test('idle recovery cannot be replaced by an expired action deadline and accepts newer work', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2,{returnToDefaultAfterMs:500});await flushReviewed();
    m._reviewedRecovery={at:5000};const token=m._reviewedRequest;
    advance(700);assert.equal(m._reviewedRequest,token);assert.equal(m.tryReviewedDeadline(700),false);
    m.requestReviewedState(3,{force:true});await flushReviewed();assert.equal(m.layeredStateIndex,3);assert.equal(m._reviewedRecovery,null);
});

test('new visit pending clears a lost old guest marker without a fake return animation', () => {
    const {manager:m}=reviewedFixture();const {bridge,window}=visitBridgeFixture();
    const a='a'.repeat(22),b='b'.repeat(22);bridge._state.visitId=a;
    const events=[];m.requestReviewedState=(index)=>{events.push(index);return true};window.pngtuberManager=m;
    m.handleReviewedVisit({action:'departed',visit_id:a,side:'guest'});
    bridge.onStateChange({type:'visit_state_change',action:'pending',visit_id:b,side:'guest'});
    assert.equal(m._reviewedVisit,null);assert.deepEqual(events,[7]);
    bridge.onStateChange({type:'visit_state_change',action:'departed',visit_id:b,side:'guest'});
    assert.equal(m._reviewedVisit,b);assert.deepEqual(events,[7,7]);
    bridge.onStateChange({type:'visit_state_change',action:'ended',visit_id:a,side:'guest'});assert.equal(m._reviewedVisit,b);
    bridge.onStateChange({type:'visit_state_change',action:'ended',visit_id:b,side:'guest'});assert.equal(m._reviewedVisit,null);
    assert.equal(events.length,3);
});

for(const source of ['hotkey','alt-one-cycle-hotkey','api']) test(`explicit state zero via ${source} selects idle-1 while automatic return may choose idle-2`, async t => {
    const {manager:m}=realReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(1,{reviewedIdleEntry:true});await flushReviewed();m.reviewedChooseIdle=()=>1;
    if(source==='api')m.playLayeredAnimation('0');else m.setLayeredStateIndex(0,{source});
    await flushReviewed();assert.equal(m.layeredStateIndex,0);
    m.requestReviewedState(2,{force:true});await flushReviewed();m.finishReviewedAction();await flushReviewed();assert.equal(m.layeredStateIndex,1);
});

for(const failedLayer of [false,true]) test(`initial idle decode failure keeps the real layered adapter (${failedLayer?'first body frame':'prefetch'})`, async t => {
    const f=realReviewedFixture(),{manager:m,images}=f;t.after(()=>m.clearLayeredTimers());
    const metadata=JSON.parse(JSON.stringify(m.layeredMetadata));metadata.runtime='layered_canvas';metadata.layers=[{image:'first.webp'}];metadata.canvas={width:720,height:960};
    f.setFetch(async()=>({ok:true,json:async()=>metadata}));
    m.config.adapter='layered_canvas_v1';m.config.idle_image='/compat.png';m.config.layered_metadata='/meta.json';
    m.canvasElement={style:{},getContext:()=>f.context};
    m.initializeLayeredToggleState=()=>{};m.startLayeredBlinkLoop=()=>{};
    m.attachLayeredHotkeys=m.attachLayeredPlayEvent=m.attachLayeredPointerTracking=()=>{};
    m.prepareReviewedEntry=async()=>{throw Error('first idle prefetch failed')};
    const loaded=m.setupLayeredAdapter();await flushReviewed();
    if(failedLayer){images.at(-1).onerror();await flushReviewed();assert.equal(images.at(-1).url,'/compat.png');}
    images.at(-1).onload();assert.equal(await loaded,true);
    assert.equal(m.layeredMetadata.runtime,'layered_canvas');assert.ok(m._trackedPlayback);assert.ok(m.layeredImages.get(0));
});


test('an explicit same-action policy replaces idle recovery without changing body time', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2);await flushReviewed();const start=m.layeredAnimationStart;
    m._reviewedRecovery={at:5000};advance(300);m.requestReviewedState(2,{returnToDefaultAfterMs:500});
    assert.equal(m._reviewedRecovery,null);assert.equal(m.layeredAnimationStart,start);
    advance(800);await flushReviewed();assert.equal(m.layeredStateIndex,0);
});

test('invalid pending events cannot retire the active guest marker', () => {
    const {manager:m}=reviewedFixture();const {bridge,window}=visitBridgeFixture();window.pngtuberManager=m;
    m._reviewedVisit='a';
    for(const data of [{visit_id:'invalid',side:'guest'},{visit_id:'b'.repeat(22),side:'invalid'}])
        bridge.onStateChange({type:'visit_state_change',action:'pending',...data});
    assert.equal(m._reviewedVisit,'a');assert.equal(bridge._state.visitId,'a');
});


for(const trackedSize of [true,false]) test(`legacy loop renders consistent bounds before during and after crossfade (explicit size ${trackedSize})`, () => {
    const {manager:m}=fixture();const p=m._trackedPlayback;
    m.layeredMetadata={canvas:{width:480,height:640}};
    if(trackedSize){p.data.width=720;p.data.height=960;}
    p.data.loop={crossfade_ms:200};p.data.frames.forEach((_,i)=>p.cache.set(i,{index:i}));
    const calls=[];const ctx=new Proxy({}, {get:(_,key)=>(...args)=>calls.push([key,...args]),set:()=>true});
    const elapsed=[100,1750,1800,1900,3600];
    for(const time of elapsed){
        calls.length=0;m.drawTrackedPlayback(ctx,time+100);
        const scales=calls.filter(c=>c[0]==='scale');
        if(trackedSize)assert.deepEqual(scales,[['scale',2/3,2/3]],`phase ${time}`);
        else assert.equal(scales.length,0,`phase ${time}`);
        if(p.lastSelection.mix>0){
            assert.equal(p.mixCanvas.width,trackedSize?720:480);assert.equal(p.mixCanvas.height,trackedSize?960:640);
            const before=p.lastSelection;calls.length=0;m.drawTrackedPlayback(ctx,99999,true);
            assert.equal(p.lastSelection,before);assert.deepEqual(calls.filter(c=>c[0]==='scale'),scales);
        }
    }
});

function legacyEmotionClockFixture() {
    const f=speechEndFixture({fakeTimers:true}),m=f.manager;
    const loop={...f.action,playback:'loop',duration_ms:1200,lipsync:true,mouth_crop:[0,0,30,20],
        frames:[{...f.action.frames[0],duration_ms:1200}]};
    f.metadata.state_count=3;f.metadata.state_catalog.push({name:'happy',state_index:2});
    f.metadata.tracked_animations[1]=loop;f.metadata.tracked_animations[2]={...f.action};
    f.metadata.emotion_mappings={angry:1,happy:2};
    return {...f,loop};
}

test('legacy looping emotion keeps the default five seconds across loops and returns on the exact boundary', () => {
    const {manager:m,advance,loop}=legacyEmotionClockFixture();m.setEmotion('angry');
    assert.equal(m._trackedPlayback.data,loop);
    for(const time of [1300,2500,3700,5099]){advance(time);assert.equal(m.layeredStateIndex,1);assert.equal(m.currentEmotion,'angry');}
    advance(5100);assert.equal(m.layeredStateIndex,0);assert.equal(m.currentEmotion,null);
});

for(const durationMs of [0,900]) test(`legacy loop respects explicit emotion duration ${durationMs}`, () => {
    const {manager:m,advance}=legacyEmotionClockFixture();m.setEmotion('angry',{durationMs});
    if(durationMs===0){advance(10000);assert.equal(m.layeredStateIndex,1);assert.equal(m.emotionTimer,null);m.clearEmotion();}
    else {advance(999);assert.equal(m.layeredStateIndex,1);advance(1000);assert.equal(m.layeredStateIndex,0);}
});

test('switching loop emotion to one-shot during speech cancels the old return and preserves speech', () => {
    const {manager:m,advance}=legacyEmotionClockFixture();m.isSpeaking=true;m.lipSyncMouthOpen=.65;
    m.setEmotion('angry');advance(1100);m.setEmotion('happy');
    assert.equal(m.layeredStateIndex,2);assert.equal(m.isSpeaking,true);assert.equal(m.lipSyncMouthOpen,.65);
    advance(1179);assert.equal(m.layeredStateIndex,2);advance(1180);assert.equal(m.layeredStateIndex,0);
    advance(5100);assert.equal(m.layeredStateIndex,0);assert.equal(m.isSpeaking,true);
});

test('switching one-shot to loop cancels the shorter deadline and clearing allows a fresh loop emotion', () => {
    const {manager:m,advance}=legacyEmotionClockFixture();m.setEmotion('happy');advance(140);m.setEmotion('angry');
    advance(180);assert.equal(m.layeredStateIndex,1);advance(5139);assert.equal(m.layeredStateIndex,1);
    advance(5140);assert.equal(m.layeredStateIndex,0);
    m.setEmotion('angry');advance(5200);m.clearEmotion();m.setEmotion('angry');
    advance(10199);assert.equal(m.layeredStateIndex,1);advance(10200);assert.equal(m.layeredStateIndex,0);
});

test('legacy looping emotion pause keeps its existing wall-clock expiry and resumes to idle', () => {
    const {manager:m,advance}=legacyEmotionClockFixture();
    m.container={style:{display:'block'},classList:{contains:()=>false}};
    m.startLayeredBlinkLoop=()=>{};m.startLayeredAnimationLoop=()=>{};
    m.setEmotion('angry');advance(300);m.pauseRendering();advance(5100);
    assert.equal(m.currentEmotion,null);m.resumeRendering();assert.equal(m.layeredStateIndex,0);
});


for(const speaking of [false,true]) for(const first of ['happy','sad','angry','surprised']) for(const second of ['happy','sad','angry','surprised'])
test(`reviewed ${first} -> ${second} -> idle preserves body clocks and speech=${speaking}`, async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    const labels=['happy','sad','angry','surprised'];
    labels.forEach((name,i)=>{m.layeredMetadata.reviewed_actions.groups[name]=[i+2];m.layeredMetadata.state_catalog[i+2].group=name;m.layeredMetadata.tracked_animations[i+2].lipsync=true;});
    m.isSpeaking=speaking;m.lipSyncMouthOpen=.6;
    m.setEmotion(first);await flushReviewed();const playback=m._trackedPlayback,start=m.layeredAnimationStart;
    advance(300);m.setEmotion(second);await flushReviewed();
    assert.equal(m._trackedPlayback,playback);assert.equal(m.layeredAnimationStart,start);
    assert.equal(m.isSpeaking,speaking);assert.equal(m.lipSyncMouthOpen,.6);
    advance(2200);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2200);await flushReviewed();
    if(first===second)assert.equal(m.layeredStateIndex,0);
    else {
        assert.equal(m.layeredStateIndex,labels.indexOf(second)+2);assert.equal(m.currentEmotion,second);
        assert.equal(m.isSpeaking,speaking);assert.equal(m.lipSyncMouthOpen,.6);
        advance(4300);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(4300);await flushReviewed();
        assert.equal(m.layeredStateIndex,0);
    }
    assert.equal(m.currentEmotion,null);assert.equal(m.isSpeaking,speaking);
});


for(const touch of [false,true]) test(`released ${touch?'touch zoom':'pointer drag'} does not leave a queued performance`, async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.setModelDraggingState=()=>{};m.rememberDragScreenPoint=()=>{};m.snapModelIntoScreen=async()=>{};m.saveOrStageCurrentConfig=async()=>{};m.isDragCompletionCurrent=()=>true;
    m.checkAndSwitchDisplayAfterDrag=async()=>false;m.recordDragHintPointerEdgeRelease=async()=>{};
    m.requestReviewedState(2,{returnToDefaultAfterMs:0});await flushReviewed();
    const gesture=touch?{changed:true}:{moved:true,pointerId:1};m[touch?'_touchZoomState':'_dragState']=gesture;m.showDragImage();
    assert.equal(m._reviewedQueued.index,4);
    if(touch)await m.endTouchZoom();else await m.endDrag({pointerId:1});
    assert.equal(m._reviewedQueued,null);advance(2400);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2400);await flushReviewed();
    assert.equal(m.layeredStateIndex,2,'expired drag must not release a held emotion');
});

test('an active drag can still hand off after the emotion and returns naturally when released', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2);await flushReviewed();m._dragState={moved:true};m.showDragImage();
    advance(2200);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2200);await flushReviewed();assert.equal(m.layeredStateIndex,4);
    m.releaseReviewedDrag(m._dragState);m._dragState=null;
    advance(4300);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(4300);await flushReviewed();assert.equal(m.layeredStateIndex,0);
});

for(const failure of [false,true]) test(`drag entry ${failure?'failure':'decode'} arriving after gesture ends cannot play or retry`, async t => {
    const {manager:m}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());let finish,fail;
    m.prepareReviewedEntry=()=>new Promise((resolve,reject)=>{finish=resolve;fail=reject});
    const gesture={changed:true};m._touchZoomState=gesture;m.showDragImage();assert.equal(m._reviewedPreparing,true);
    m._touchZoomState=null;m.releaseReviewedDrag(gesture);
    if(failure)fail(Error('late failure'));else finish([{image:{}}]);await flushReviewed();
    assert.equal(m.layeredStateIndex,0);assert.equal(m._reviewedPreparing,false);assert.equal(m._reviewedRetry,null);
});

test('queued old drag cannot be revived by a different new gesture', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2);await flushReviewed();m._dragState={moved:true};m.showDragImage();m._dragState={moved:true};
    advance(2200);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2200);await flushReviewed();assert.equal(m.layeredStateIndex,0);
});

test('hold-current request supersedes older queued work while later work can still supersede that hold', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2);await flushReviewed();const start=m.layeredAnimationStart;
    m.requestReviewedState(3);m.requestReviewedState(2,{returnToDefaultAfterMs:0});assert.equal(m._reviewedQueued,null);
    advance(2300);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2300);await flushReviewed();assert.equal(m.layeredStateIndex,2);assert.equal(m.layeredAnimationStart,start);
    m.requestReviewedState(3);await flushReviewed();assert.equal(m.layeredStateIndex,3);
});

test('initial preload completion preserves a newer pending emotion and active transient state', async t => {
    const f=realReviewedFixture({fakeTimers:true}),m=f.manager;t.after(()=>m.clearLayeredTimers());
    const md=JSON.parse(JSON.stringify(m.layeredMetadata));md.runtime='layered_canvas';md.layers=[{image:'first.webp'}];md.canvas={width:720,height:960};md.emotion_mappings={happy:2};md.state_catalog[1].name='talking';
    f.setFetch(async()=>({ok:true,json:async()=>md}));m.config={...m.config,adapter:'layered_canvas_v1',layered_metadata:'/meta.json',state_priority:'talking'};
    m.canvasElement={style:{},getContext:()=>f.context};m.initializeLayeredToggleState=()=>{};m.startLayeredBlinkLoop=()=>{};m.attachLayeredHotkeys=m.attachLayeredPlayEvent=m.attachLayeredPointerTracking=()=>{};
    let finish;m.prepareReviewedEntry=()=>new Promise(resolve=>finish=resolve);const loading=m.setupLayeredAdapter();await flushReviewed();f.images.at(-1).onload();await flushReviewed();
    const finishInitial=finish;m.isSpeaking=true;m.setEmotion('happy');const pending=m.pendingEmotion;assert.ok(pending);
    const slots=m.layeredTransientSlots;m.activeLayeredEmotionIndex=2;
    finishInitial([{image:{}}]);assert.equal(await loading,true);assert.equal(m.pendingEmotion,pending);assert.equal(m.layeredTransientSlots,slots);assert.equal(m.activeLayeredEmotionIndex,2);
});

for(const delay of [1000,60000,0]) test(`reviewed leisure uses configured idle threshold ${delay}`, () => {
    const {manager:m,window}=reviewedFixture();window.getReviewedPNGTuberActivity=()=>({proactiveEnabled:false,owner:true,busy:false});m.layeredMetadata.reviewed_actions.leisure_idle_ms=delay;
    const calls=[];m.requestReviewedState=index=>{calls.push(index);return true};m.reviewedPlaybackTick(100);
    if(delay>0){m.reviewedPlaybackTick(100+delay-1);assert.equal(calls.length,0);m.reviewedPlaybackTick(100+delay);}
    assert.deepEqual(calls,[5]);
});
for(const delay of [undefined,-1,null,'1000',Infinity]) test(`invalid idle threshold ${String(delay)} preserves thirty seconds`, () => {
    const {manager:m}=reviewedFixture();m.layeredMetadata.reviewed_actions.leisure_idle_ms=delay;assert.equal(m.reviewedLeisureDelay(),30000);
});


for(const touch of [false,true]) test(`starting a new ${touch?'pinch':'pointer gesture'} retires an old queued drag`, async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.setModelDraggingState=()=>{};m.rememberDragScreenPoint=()=>{};m.getActivePlacement=()=>({scale:.5,offsetX:0,offsetY:0});
    m.requestReviewedState(2,{returnToDefaultAfterMs:0});await flushReviewed();m._dragState={moved:true};m.showDragImage();assert.ok(m._reviewedQueued);
    const event={preventDefault(){},stopPropagation(){},button:0,pointerId:2,clientX:10,clientY:10,touches:[{clientX:0,clientY:0},{clientX:20,clientY:20}]};
    if(touch)m.startTouchZoom(event);else m.startDrag(event);
    assert.equal(m._reviewedQueued,null);advance(2300);m._trackedPlayback.lastIndex=1;m.reviewedPlaybackTick(2300);await flushReviewed();assert.equal(m.layeredStateIndex,2);
});

test('active pinch is recognized even when another pointer has only pressed without moving', async t => {
    const {manager:m}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());m._dragState={moved:false};m._touchZoomState={changed:true};
    m.showDragImage();await flushReviewed();assert.equal(m.layeredStateIndex,4);
});


test('new positive duration for the active emotion replaces older queued work through the public API', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());m.reviewedPick=()=>2;
    m.setEmotion('happy');await flushReviewed();const start=m.layeredAnimationStart;m.requestReviewedState(3);assert.equal(m._reviewedQueued.index,3);
    advance(300);m.setEmotion('happy',{durationMs:500});assert.equal(m._reviewedQueued,null);assert.equal(m.layeredAnimationStart,start);
    advance(799);await flushReviewed();assert.equal(m.layeredStateIndex,2);
    advance(800);await flushReviewed();assert.equal(m.layeredStateIndex,0);assert.equal(m.currentEmotion,null);
});


test('a released gesture cancels a never-resolving drag preparation and unblocks the return deadline', async t => {
    const {manager:m,advance}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.requestReviewedState(2,{returnToDefaultAfterMs:500});await flushReviewed();
    const gesture={moved:true};m._dragState=gesture;m.showDragImage();
    m.prepareReviewedEntry=index=>index===4?new Promise(()=>{}):Promise.resolve([{image:{}}]);
    advance(600);await flushReviewed();assert.equal(m._reviewedPreparing,true);
    m._dragState=null;m.releaseReviewedDrag(gesture);assert.equal(m._reviewedPreparing,false);assert.equal(m._reviewedPreparingDrag,null);
    advance(601);m.reviewedPlaybackTick(601);await flushReviewed();assert.equal(m.layeredStateIndex,0);
});

test('releasing an old drag does not cancel a newer emotion preparation', async t => {
    const {manager:m}=timedReviewedFixture();t.after(()=>m.clearLayeredTimers());
    m.prepareReviewedEntry=()=>new Promise(()=>{});const gesture={changed:true};m._touchZoomState=gesture;m.showDragImage();
    m.requestReviewedState(2,{force:true});const token=m._reviewedRequest;m._touchZoomState=null;m.releaseReviewedDrag(gesture);
    assert.equal(m._reviewedRequest,token);assert.equal(m._reviewedPreparing,true);assert.equal(m._reviewedPreparingDrag,null);
});


test('releasing an absent gesture is a no-op even while non-drag work is preparing', () => {
    const {manager:m}=reviewedFixture();m._reviewedPreparing=true;m._reviewedPreparingDrag=null;const token=m._reviewedRequest;
    m.releaseReviewedDrag(undefined);m.releaseReviewedDrag(null);assert.equal(m._reviewedPreparing,true);assert.equal(m._reviewedRequest,token);
});
