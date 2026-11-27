const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../static/pngtuber-core.js'), 'utf8');

function fixture() {
    let clock = 100, next = 0;
    const callbacks = new Map(), images = [];
    class Image {
        removeAttribute() {}
        set src(value) { this.url = value; images.push(this); }
    }
    const window = { location: { pathname: '/' }, lanlan_config: {model_type: 'pngtuber'}, dispatchEvent: () => {} };
    vm.runInNewContext(source, { window, document: { getElementById: () => null, body: { classList: { contains: () => false } } },
        console, Image, CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } }, performance: { now: () => clock },
        requestAnimationFrame: fn => { callbacks.set(++next, fn); return next; },
        cancelAnimationFrame: id => callbacks.delete(id), setTimeout, clearTimeout });
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
    return { window, manager, metadata, context, callbacks, images, advance: value => { clock = value; } };
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

function actionFixture() {
    const f = fixture(), m = f.manager;
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

function speechEndFixture() {
    const f=actionFixture(),m=f.manager,restarts=[];
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
function reviewedFixture() {
    const f=fixture(),m=f.manager;
    m.isLayeredActive=()=>true;
    const clip=(playback='once')=>({duration_ms:2000,playback,width:720,height:960,mouth_crop:[0,0,1,1],mouth_renderer:'viseme_v1',lipsync:true,frames:[{time_ms:0,duration_ms:1000,image:'a.png',x:20,y:25},{time_ms:1000,duration_ms:1000,image:'b.png',x:20,y:25}]});
    m.layeredMetadata={state_count:7,canvas:{width:720,height:960},reviewed_actions:{version:1,groups:{idle:[0,1],happy:[2,3],drag:[4],leisure:[5],'visit-leave':[4],'visit-return':[6]}},state_catalog:[0,1,2,3,4,5,6].map((state_index)=>({state_index,group:['idle','idle','happy','happy','drag','leisure','visit'][state_index]})),tracked_animations:{0:clip('loop'),1:clip('loop'),2:clip(),3:clip(),4:clip(),5:{...clip(),lipsync:false},6:clip()}};
    m.layeredMetadata.tracked_animation=m.layeredMetadata.tracked_animations[0];m.layeredStateIndex=0;
    m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');m.layeredAnimationStart=100;
    m.prepareReviewedEntry=async()=>[{image:{name:'prepared'}}];
    m.reviewedChooseIdle=()=>0; // deterministic default; entry-selection tests choose the other variant explicitly.
    m.setLayeredStateIndex=(index,options)=>{assert.equal(options.reviewedCommit,true);m.layeredStateIndex=index;m.initializeTrackedPlayback(m.layeredMetadata,'/meta.json');};
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
    m.handleReviewedVisit({side:'guest',action:'departed',visit_id:'v1'});m.handleReviewedVisit({side:'guest',action:'departed',visit_id:'v1'});m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'wrong'});assert.deepEqual(calls,[4]);
    m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'v1'});m.handleReviewedVisit({side:'guest',action:'ended',visit_id:'v1'});assert.deepEqual(calls,[4,6]);
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
 m.hasReviewedActions=()=>false;calls.length=0;m.drawTrackedPlayback(ctx,1760);assert.equal(scratch.width,576);assert.equal(scratch.height,768);assert.equal(calls.some(c=>c[0]==='scale'),false);
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
