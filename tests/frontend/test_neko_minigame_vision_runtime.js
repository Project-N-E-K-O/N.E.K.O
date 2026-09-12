const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const sdk = path.resolve(__dirname, '../../static/game/sdk');
global.window = {};
vm.runInThisContext(fs.readFileSync(path.join(sdk, 'neko-minigame-vision-host.js'), 'utf8'));
assert.equal(typeof window.NekoMiniGameVisionHost.capture, 'function');
const helper = window.NekoMiniGameVisionHost;
const tick = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const rectangle = { kind: 'rect', unit: 'px', x: 100, y: 50, width: 400, height: 200 };

function environment() {
  let config = {}; let stops = 0; let videoReleased = 0; let choices = 0;
  const timers = new Set(); const drawCalls = []; const videos = []; const canvases = [];
  const events = new EventTarget();
  const w = {
    isSecureContext: true, innerWidth: 1000, innerHeight: 500, scrollX: 0, scrollY: 0,
    crypto: require('node:crypto').webcrypto, AbortController,
    location: { origin: 'http://127.0.0.1:48911' },
    console: { warn() {}, error() {}, log() {} },
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events),
    setTimeout(fn, ms) { const t = setTimeout(fn, ms); timers.add(t); return t; },
    clearTimeout(t) { clearTimeout(t); timers.delete(t); },
    setInterval(fn, ms) { const t = setInterval(fn, ms); timers.add(t); return t; },
    clearInterval(t) { clearInterval(t); timers.delete(t); },
  };
  w.top = w;
  class Track extends EventTarget {
    getCaptureHandle() { return { handle: config.handle }; }
    getSettings() { return { displaySurface: 'browser' }; }
    stop() { stops++; }
  }
  w.MediaStreamTrack = Track;
  class Video {
    constructor() { this.videoWidth = 2000; this.videoHeight = 1000; videos.push(this); }
    requestVideoFrameCallback(fn) { queueMicrotask(fn); return 1; }
    cancelVideoFrameCallback() {}
    play() { return Promise.resolve(); }
    pause() {}
    remove() { videoReleased++; }
  }
  w.HTMLVideoElement = Video;
  const makeStream = () => { const track = new Track(); return { getTracks: () => [track], getVideoTracks: () => [track] }; };
  const env = { w, timers, drawCalls, videos, canvases, makeStream,
    get stops() { return stops; }, get videoReleased() { return videoReleased; }, get choices() { return choices; },
    get config() { return config; }, choose: async () => makeStream() };
  w.navigator = { mediaDevices: {
    setCaptureHandleConfig(value) { config = value; },
    getDisplayMedia(options) { assert.equal(options.audio, false); choices++; return env.choose(); },
  } };
  env.bounds = { x: 100, y: 50, width: 400, height: 200 };
  w.document = {
    querySelectorAll: selector => selector === '#board' ? [{ isConnected: true, getBoundingClientRect: () => env.bounds }] : [],
    createElement(tag) {
      if (tag === 'video') return new Video();
      if (tag === 'canvas') {
        const canvas = { width: 0, height: 0, getContext: () => ({ drawImage: (...args) => drawCalls.push(args) }),
          toDataURL: () => 'data:image/jpeg;base64,ZXhhbXBsZQ==' };
        canvases.push(canvas); return canvas;
      }
      return { remove() {} };
    },
  };
  return env;
}

async function captureTests() {
  const env = environment(); const { w } = env;
  const region = helper.resolveRegion(rectangle, w);
  for (const timeoutMs of [0, -1, NaN, Infinity]) {
    await assert.rejects(helper.capture(rectangle, { windowImpl: w, timeoutMs }), { code: 'invalid_timeout' });
    assert.equal(env.choices, 0);
    assert.equal(env.timers.size, 0);
  }
  for (const equivalent of [
    { ...rectangle, unit: 'percent', x: 10, y: 10, width: 40, height: 40 },
    { kind: 'element', selector: '#board' },
    { kind: 'edges', unit: 'px', left: 100, top: 50, right: 500, bottom: 250 },
    { kind: 'corners', unit: 'px', topLeft: { x: 100, y: 50 }, topRight: { x: 500, y: 50 },
      bottomLeft: { x: 100, y: 250 }, bottomRight: { x: 500, y: 250 } },
  ]) assert.deepEqual(helper.resolveRegion(equivalent, w), region);
  for (const bad of [null, { ...rectangle, x: -1 }, { ...rectangle, width: Infinity },
    { ...rectangle, x: 999 }, { ...rectangle, width: 0 }, { ...rectangle, unit: 'device-px' },
    { ...rectangle, unit: 'percent', width: 101 }, { kind: 'element', selector: '#missing' },
    { ...rectangle, unexpected: true },
    { kind: 'corners', unit: 'px', topLeft: { x: 0, y: 0 }, topRight: { x: 10, y: 1 },
      bottomRight: { x: 10, y: 10 }, bottomLeft: { x: 0, y: 10 } },
  ]) assert.throws(() => helper.resolveRegion(bad, w), { code: 'invalid_region' });
  const result = await helper.capture(rectangle, { windowImpl: w });
  assert.equal(result.width, 800); assert.equal(result.height, 400);
  assert.deepEqual(env.drawCalls[0].slice(1), [200, 100, 800, 400, 0, 0, 800, 400]);
  assert.equal(env.canvases[0].width, 0); assert.equal(env.videos[0].srcObject, null);
  assert.ok(env.stops > 0 && env.videoReleased > 0); assert.deepEqual(env.config, {});
  assert.equal(env.timers.size, 0);

  env.choose = async () => { const stream = env.makeStream(); stream.getVideoTracks()[0].getCaptureHandle = () => ({ handle: 'other-tab' }); return stream; };
  const previousDraws = env.drawCalls.length;
  await assert.rejects(helper.capture(rectangle, { windowImpl: w }), { code: 'capture_source_mismatch' });
  assert.equal(env.drawCalls.length, previousDraws);
  env.choose = async () => { throw Object.assign(new Error('private cause'), { name: 'NotAllowedError' }); };
  await assert.rejects(helper.capture(rectangle, { windowImpl: w }), { code: 'capture_denied' });
  const late = deferred(); env.choose = () => late.promise;
  const abort = new AbortController();
  const pending = helper.capture(rectangle, { windowImpl: w, signal: abort.signal });
  abort.abort();
  await assert.rejects(pending, { code: 'cancelled' });
  await assert.rejects(helper.capture(rectangle, { windowImpl: w }), { code: 'busy' });
  const beforeLate = env.stops;
  late.resolve(env.makeStream()); await tick();
  assert.ok(env.stops > beforeLate, 'late authorized stream leaked');
  env.choose = async () => { w.scrollY++; return env.makeStream(); };
  await assert.rejects(helper.capture(rectangle, { windowImpl: w }), { code: 'capture_changed' });
  env.choose = async () => { env.bounds.x++; return env.makeStream(); };
  await assert.rejects(helper.capture({ kind: 'element', selector: '#board' }, { windowImpl: w }), { code: 'capture_changed' });
  const timedOut = deferred(); env.choose = () => timedOut.promise;
  await assert.rejects(helper.capture(rectangle, { windowImpl: w, timeoutMs: 1 }), { code: 'timeout' });
  timedOut.resolve(env.makeStream()); await tick();
  assert.equal(env.timers.size, 0);
  w.isSecureContext = false;
  assert.equal(helper.available(w), false);
  await assert.rejects(helper.capture(rectangle, { windowImpl: w }), { code: 'capture_unavailable' });
}

async function optionalVisionBootstrapTests() {
  for (const mode of ['omitted', 'optional', 'required']) {
    const {w} = environment();
    w.fetch = async () => new Response('{}');
    const launch = {remove() {}, textContent:JSON.stringify({registrations:{'example-game':{
      gameId:'example-game', version:'1', mode:'registered', allowedCapabilities:['runtime','logging','vision'],
    }}})};
    w.document.getElementById = () => launch;
    let removed = 0;
    w.document.createElement = () => ({remove() { removed++; }});
    w.document.head = {appendChild(script) {
      if (script.src.endsWith('vision-host.js')) { script.onerror(); return; }
      w.document.currentScript = script;
      vm.runInThisContext(fs.readFileSync(path.join(sdk, path.basename(script.src)), 'utf8'));
      w.document.currentScript = null;
      script.onload();
    }};
    global.window = w;
    vm.runInThisContext(fs.readFileSync(path.join(sdk, 'neko-minigame-same-origin-bootstrap.js'), 'utf8'));
    await w.nekoMiniGameSameOriginHostReady;
    assert.equal(removed, 2, 'failed optional helper and adapter scripts must be detached');
    vm.runInThisContext(fs.readFileSync(path.join(sdk, 'neko-minigame-sdk.js'), 'utf8'));
    const transport = w.createNekoMiniGameSameOriginHost({gameType:'example-game', windowImpl:w});
    const connecting = w.NekoMiniGame.connect({id:'example-game',version:'1',
      requiredCapabilities:['runtime','logging', ...(mode === 'required' ? ['vision'] : [])],
      optionalCapabilities:mode === 'optional' ? ['vision'] : [],
    }, {transport, windowImpl:w, documentImpl:w.document});
    if (mode === 'required') await assert.rejects(connecting, {code:'capability_unavailable'});
    else (await connecting).dispose();
  }
}

async function sdkTests() {
  const env = environment(); const { w } = env; const posts = [];
  w.Blob=Blob; w.btoa=btoa;
  const response = (data) => new Response(JSON.stringify(data), {headers:{'content-type':'application/json'}});
  let bodyWait = null;
  let visionFailure = null;
  let nextVisionResponse = null;
  w.fetch = async (url, init = {}) => {
    if (String(url).endsWith('/cors-denied.png')) throw new TypeError('CORS denied');
    if (String(url).includes('page_config')) return response({ autostart_csrf_token: 'test-token' });
    const body = init.body ? JSON.parse(init.body) : {};
    if (String(url).includes('/route/start')) return response({ ok: true, state: {
      game_route_active: true, session_id: body.session_id, lanlan_name: 'Example',
    } });
    if (String(url).endsWith('/vision/analyze')) {
      if (nextVisionResponse) { const value = nextVisionResponse; nextVisionResponse = null; return value; }
      if (visionFailure) return response({ ok: false, reason: visionFailure });
      posts.push(body);
      assert.ok(env.stops > 0, 'sharing must stop before model request');
      if (bodyWait) return new Response(new ReadableStream({
        async start(controller) {
          controller.enqueue(new TextEncoder().encode(JSON.stringify(await bodyWait.promise)));
          controller.close();
        },
      }));
      return response({ ok: true, text: 'A blue square.' });
    }
    return response({ ok: true, active: false });
  };
  const launch = { remove() {}, textContent: JSON.stringify({ registrations: { 'example-game': {
    gameId: 'example-game', version: '1.0.0', mode: 'registered', allowedCapabilities: ['runtime', 'logging', 'vision'],
  } } }) };
  w.document.getElementById = () => launch;
  w.document.head = { appendChild(script) {
    w.document.currentScript = script;
    vm.runInThisContext(fs.readFileSync(path.join(sdk, path.basename(script.src)), 'utf8'));
    w.document.currentScript = null; script.onload();
  } };
  global.window = w;
  vm.runInThisContext(fs.readFileSync(path.join(sdk, 'neko-minigame-same-origin-bootstrap.js'), 'utf8'));
  await w.nekoMiniGameSameOriginHostReady;
  vm.runInThisContext(fs.readFileSync(path.join(sdk, 'neko-minigame-sdk.js'), 'utf8'));
  // A distinct global realm must not override the explicitly injected host.
  global.window = {};
  const host = w.createNekoMiniGameSameOriginHost({ gameType: 'example-game', windowImpl: w });
  await assert.rejects(host.analyzeGameVision({}), { code: 'capability_denied' });
  const manifest = { id: 'example-game', version: '1.0.0', requiredCapabilities: ['runtime', 'logging', 'vision'] };
  const game = await w.NekoMiniGame.connect(manifest, { transport: host, windowImpl: w, documentImpl: w.document });
  game.runtime.configure({ heartbeat: false, outputs: false, pageExit: false });
  const request = { region: rectangle, prompt: 'Describe this region.' };
  await assert.rejects(game.vision.analyze(request), { code: 'invalid_state' });
  assert.equal(env.choices, 0, 'inactive route opened picker');
  await game.runtime.start({ lanlan_name: 'Example' });
  const result = await game.vision.analyze(request);
  assert.deepEqual(result, { text: 'A blue square.', width: 800, height: 400 });
  assert.equal(posts.length, 1); assert.equal(posts[0].lanlan_name, 'Example');
  assert.equal(posts[0].session_id, game.runtime.session.id);
  assert.equal(posts[0].sdk_route_instance_id, game.runtime.session.routeInstanceId);
  assert.equal(posts[0].game_memory_enabled, undefined);
  assert.equal(Object.isFrozen(result), true);
  await assert.rejects(game.vision.analyze({ ...request, prompt: 'x'.repeat(4097) }), { code: 'invalid_request' });

  const choices=env.choices;
  const bytes=new Uint8Array([1,2,3]);
  const multi=game.vision.analyze({text:'Compare',attachments:[
    {type:'image',source:bytes,mimeType:'image/png',label:'before'},
    {type:'image',source:new Blob([bytes],{type:'image/jpeg'}),label:'after'},
  ]});
  bytes.fill(9);
  assert.deepEqual(await multi,{text:'A blue square.'});
  assert.equal(env.choices,choices);
  assert.equal(posts.length,2);
  assert.equal(posts[1].text,'Compare');
  assert.equal(posts[1].attachments[0].image_data_url,'data:image/png;base64,AQID');
  for (const [reason, code] of Object.entries({
    invalid_image: 'invalid_image', unsupported_attachment: 'unsupported_attachment',
    busy: 'busy', timeout: 'timeout', vision_unavailable: 'capability_unavailable',
    route_inactive: 'session_invalid', invalid_model_response: 'invalid_response',
    vision_failed: 'request_failed', 'private backend detail': 'request_failed',
  })) {
    visionFailure = reason;
    await assert.rejects(game.vision.analyze({text:'Observe', attachments:[
      {type:'image',source:new Uint8Array([1,2,3]),mimeType:'image/png'},
    ]}), error => error.code === code && !JSON.stringify(error).includes('private backend detail'));
  }
  visionFailure = null;
  for (const input of [request, {text:'Observe', attachments:[
    {type:'image',source:new Uint8Array([1,2,3]),mimeType:'image/png'},
  ]}]) {
    for (const [status, body] of [[200, ''], [200, '{broken'], [204, null], [500, '<html>private proxy error</html>']]) {
      nextVisionResponse = new Response(body, {status});
      await assert.rejects(game.vision.analyze(input), error => error.code === 'invalid_response'
        && !JSON.stringify(error).includes('private proxy error'));
      await tick();
      assert.equal(host._visionOperations.size, 0, 'bad JSON retained the vision operation');
      assert.equal(host._rawRequests.size, 0, 'bad JSON retained a raw request slot');
    }
  }
  for (const status of [200, 500]) {
    let cancelled = false;
    let chunks = 0;
    nextVisionResponse = new Response(new ReadableStream({
      pull(controller) {
        controller.enqueue(new Uint8Array(1024 * 1024));
        if (++chunks === 6) controller.close();
      },
      cancel() { cancelled = true; },
    }), {status});
    await assert.rejects(game.vision.analyze({text:'Observe', attachments:[
      {type:'image',source:new Uint8Array([1,2,3]),mimeType:'image/png'},
    ]}), {code:'invalid_response'});
    assert.ok(cancelled && chunks < 6, 'oversized vision body was fully buffered');
  }
  assert.deepEqual(posts[1].attachments.map(item=>item.label),['before','after']);
  const originalAnalyze = host.analyzeGameVision;
  let sparseTransportCalls = 0;
  host.analyzeGameVision = function (...args) {
    sparseTransportCalls += 1;
    return originalAnalyze.apply(this, args);
  };
  const image = {type:'image', source:new Uint8Array([1,2,3]), mimeType:'image/png'};
  try {
    for (const attachments of [new Array(1), [, image], [image, ,], [image, , image]]) {
      await assert.rejects(game.vision.analyze({text:'Observe', attachments}), {code:'invalid_request'});
      assert.equal(sparseTransportCalls, 0, 'sparse attachments reached the transport');
      assert.equal(game.vision.pendingCount, 0, 'invalid attachments retained a request slot');
    }
  } finally { host.analyzeGameVision = originalAnalyze; }
  for(const bad of [{text:'Example',attachments:[]},
    {text:'Example',attachments:[{type:'audio',source:'anything'}]},
    {text:'Example',attachments:[{type:'image',source:bytes}]},
    {text:'Example',attachments:[{type:'image',source:'example.png'}],system:'injected'},
    {...request,text:'Example',attachments:[{type:'image',source:'example.png'}]},
  ]) await assert.rejects(game.vision.analyze(bad));
  assert.equal(posts.length,2);
  await assert.rejects(game.vision.analyze({text:'Observe',attachments:[
    {type:'image',source:'https://example.invalid/cors-denied.png'},
  ]}),{code:'image_unavailable'});

  bodyWait = deferred();
  const pending = game.vision.analyze(request); await tick();
  await assert.rejects(game.vision.analyze(request), { code: 'busy' });
  const rejected = assert.rejects(pending, { code: 'cancelled' });
  await game.runtime.end(); await rejected;
  bodyWait.resolve({ ok: true, text: 'Must not escape into the next round' }); await tick();
  game.dispose(); await tick();
  assert.equal(env.timers.size, 0);
  assert.equal(host._visionOperations.size, 0);
  global.window = w;
}

async function attachmentTests() {
  const env=environment(); const {w}=env;
  w.Blob=Blob; w.btoa=btoa;
  delete w.navigator.mediaDevices.getDisplayMedia;
  let requests=0;
  const bytes=new Uint8Array([1,2,3]);
  w.fetch=async (url,options)=>{
    requests++;
    assert.equal(options.mode,'cors'); assert.equal(options.credentials,'omit');
    assert.equal(options.redirect,'error'); assert.equal(options.referrerPolicy,'no-referrer');
    return new Response(bytes,{headers:{'Content-Type':'image/png'}});
  };
  assert.equal(helper.available(w),true,'images do not depend on tab capture');
  assert.equal(helper.captureAvailable(w),false);
  const options={windowImpl:w};
  const normalized=await helper.normalizeAttachments([
    {type:'image',source:'/example.png',label:'url'},
    {type:'image',source:new Blob([bytes],{type:'image/jpeg'}),label:'blob'},
    {type:'image',source:bytes,mimeType:'image/webp',label:'bytes'},
    {type:'image',source:bytes.buffer,mimeType:'image/png',label:'buffer'},
  ],options);
  assert.equal(requests,1); assert.equal(env.choices,0);
  assert.deepEqual(normalized.map(item=>item.label),['url','blob','bytes','buffer']);
  assert.deepEqual(normalized.map(item=>item.image_data_url),[
    'data:image/png;base64,AQID','data:image/jpeg;base64,AQID',
    'data:image/webp;base64,AQID','data:image/png;base64,AQID',
  ]);
  const data='data:image/png;base64,AQID';
  assert.equal((await helper.normalizeAttachments([{type:'image',source:data}],options))[0].image_data_url,data);
  for(const input of [[],Array(5).fill({type:'image',source:data}),
    [{type:'audio',source:data}], [{type:'image',source:'file:///private.png'}],
    [{type:'image',source:'blob:https://other.invalid/secret'}],
    [{type:'image',source:'https://user:secret@example.invalid/a.png'}],
    [{type:'image',source:'data:image/svg+xml;base64,AQID'}],
    [{type:'image',source:bytes}], [{type:'image',source:data,label:'x'.repeat(129)}],
    [{type:'image',source:new Blob([new Uint8Array(2*1024*1024+1)],{type:'image/png'})}],
    Array(4).fill({type:'image',source:new Blob([new Uint8Array(2*1024*1024)],{type:'image/png'})}),
  ]) await assert.rejects(helper.normalizeAttachments(input,options));
  const oldRequests=requests;
  class MisreportedBlob extends Blob {
    constructor(length) { super([new Uint8Array(1)], {type:'image/png'}); this.length=length; }
    async arrayBuffer() { return new ArrayBuffer(this.length); }
  }
  for (const lengths of [[2*1024*1024+1], [2*1024*1024,2*1024*1024,2*1024*1024,1]]) {
    await assert.rejects(helper.normalizeAttachments(lengths.map(length => ({
      type:'image',source:new MisreportedBlob(length),
    })),options), {code:'invalid_image'});
    assert.equal((await helper.normalizeAttachments([{type:'image',source:data}],options)).length,1);
  }
  w.fetch=async()=>{ throw new TypeError('CORS denial with private URL'); };
  await assert.rejects(helper.normalizeAttachments([{type:'image',source:'https://other.invalid/a.png'}],options),
    error=>error.code==='image_unavailable'&&!error.message.includes('private'));
  assert.equal(requests,oldRequests);
  let cancelled=false;
  w.fetch=async()=>new Response(new ReadableStream({start(c){c.enqueue(new Uint8Array(2*1024*1024+1));},cancel(){cancelled=true;}}),
    {headers:{'Content-Type':'image/png'}});
  await assert.rejects(helper.normalizeAttachments([{type:'image',source:'/big.png'}],options),{code:'invalid_image'});
  assert.ok(cancelled,'oversized streaming body must be cancelled');
  const late=deferred();
  class SlowBlob extends Blob { arrayBuffer(){return late.promise;} }
  const abort=new AbortController();
  const pending=helper.normalizeAttachments([{type:'image',source:new SlowBlob([bytes],{type:'image/png'})}],{...options,signal:abort.signal});
  abort.abort();
  await assert.rejects(helper.normalizeAttachments([{type:'image',source:data}],options),{code:'busy'});
  const rejected=assert.rejects(pending,{code:'cancelled'});
  late.resolve(bytes.buffer); await rejected;
  assert.equal((await helper.normalizeAttachments([{type:'image',source:data}],options)).length,1);
}

(async () => { await captureTests(); await attachmentTests(); await optionalVisionBootstrapTests(); await sdkTests(); console.log('mini-game vision runtime tests passed'); })()
  .catch(error => { console.error(error); process.exitCode = 1; });
