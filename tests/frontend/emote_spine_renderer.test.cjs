const assert = require('node:assert/strict');
const test = require('node:test');

const {
    SpineRenderer,
    SpineRendererError,
    extractAtlasPageNames,
    normalizePath,
} = require('../../static/js/emote_spine_renderer.js');

function fakeRuntime() {
    const calls = [];
    class ManagedWebGLRenderingContext {
        constructor(canvas, config) {
            calls.push(['context.config', config]);
            this.canvas = canvas;
            this.gl = {
                COLOR_BUFFER_BIT: 0x4000,
                RGBA: 0x1908,
                UNSIGNED_BYTE: 0x1401,
                clearColor: (...args) => calls.push(['clearColor', ...args]),
                clear: (...args) => calls.push(['clear', ...args]),
                viewport: (...args) => calls.push(['viewport', ...args]),
                readPixels: (x, y, width, height, format, type, output) => {
                    assert.equal(format, 0x1908);
                    assert.equal(type, 0x1401);
                    // Bottom row red, top row blue: captureFrame must flip it.
                    for (let row = 0; row < height; row += 1) {
                        for (let column = 0; column < width; column += 1) {
                            const offset = (row * width + column) * 4;
                            output[offset] = row < height / 2 ? 255 : 0;
                            output[offset + 1] = 0;
                            output[offset + 2] = row >= height / 2 ? 255 : 0;
                            output[offset + 3] = 255;
                        }
                    }
                },
            };
            canvas.addEventListener?.('webglcontextlost', () => {});
            canvas.addEventListener?.('webglcontextrestored', () => {});
        }
        dispose() { calls.push(['context.dispose']); }
    }

    class SceneRenderer {
        constructor(canvas, context) {
            this.canvas = canvas;
            this.context = context;
            this.camera = {
                viewportWidth: 0,
                viewportHeight: 0,
                zoom: 1,
                position: {
                    x: 0,
                    y: 0,
                    z: 0,
                    set(x, y, z) { this.x = x; this.y = y; this.z = z; },
                },
                direction: {
                    x: 0,
                    y: 0,
                    z: -1,
                    set(x, y, z) { this.x = x; this.y = y; this.z = z; },
                },
                up: {
                    x: 0,
                    y: 1,
                    z: 0,
                    set(x, y, z) { this.x = x; this.y = y; this.z = z; },
                },
                setViewport(width, height) {
                    this.viewportWidth = width;
                    this.viewportHeight = height;
                },
                update() {},
            };
        }
        begin() { calls.push(['renderer.begin']); }
        drawSkeleton() { calls.push(['renderer.drawSkeleton']); }
        end() { calls.push(['renderer.end']); }
        dispose() { calls.push(['renderer.dispose']); }
    }

    class AssetManager {
        constructor() { this.raw = new Map(); }
        setRawDataURI(path, uri) { this.raw.set(path, uri); }
        async loadTextureAtlasAsync(path) {
            calls.push(['atlas', path, this.raw.size]);
            return { pages: [] };
        }
        dispose() { calls.push(['assets.dispose']); }
    }

    class AtlasAttachmentLoader {
        constructor(atlas) { this.atlas = atlas; }
    }

    class SkeletonBinary {
        constructor(loader) { this.loader = loader; }
        readSkeletonData(bytes) {
            assert.ok(bytes instanceof Uint8Array);
            return skeletonData();
        }
    }

    class SkeletonJson {
        constructor(loader) { this.loader = loader; }
        readSkeletonData() { return skeletonData(); }
    }

    class Skeleton {
        constructor(data) { this.data = data; this.x = 0; this.y = 0; }
        getBounds(offset, size) {
            offset.x = -10;
            offset.y = -20;
            size.x = 100;
            size.y = 200;
        }
        update(delta) { this.time = (this.time || 0) + delta; }
        updateWorldTransform() { calls.push(['skeleton.updateWorldTransform']); }
    }

    class AnimationStateData {
        constructor(data) { this.data = data; }
    }

    class AnimationState {
        constructor(data) { this.data = data; this.track = null; }
        clearTracks() { this.track = null; }
        setAnimation(trackIndex, name, loop) {
            assert.equal(trackIndex, 0);
            this.track = {
                animation: this.data.data.findAnimation(name),
                loop,
                trackTime: 0,
            };
            return this.track;
        }
        getTrack() { return this.track; }
        update(delta) {
            if (this.track) this.track.trackTime += delta;
        }
        apply() { calls.push(['state.apply']); }
    }

    class Vector2 {
        constructor(x = 0, y = 0) { this.x = x; this.y = y; }
    }

    function skeletonData() {
        const animations = [
            { name: 'ANIM/Idle', duration: 1.25 },
            { name: 'ANIM/Hello', duration: 2.5 },
        ];
        return {
            animations,
            findAnimation(name) { return animations.find((animation) => animation.name === name) || null; },
        };
    }

    return {
        ManagedWebGLRenderingContext,
        SceneRenderer,
        AssetManager,
        TextureAtlas: class {},
        AtlasAttachmentLoader,
        SkeletonBinary,
        SkeletonJson,
        Skeleton,
        AnimationStateData,
        AnimationState,
        Vector2,
        Physics: { update: 2 },
        version: '4.3.13',
        calls,
    };
}

function manifest() {
    return {
        source: { kind: 'spine', spineVersion: '4.3.23' },
        model: {
            skeleton: { path: 'pkg/model/Demo.skel.bytes', spineVersion: '4.3.23' },
            atlas: {
                path: 'pkg/model/Demo.atlas.txt',
                pages: [{ name: 'Page.png', path: 'pkg/model/Page.png' }],
            },
        },
        scenes: [{ animationName: 'ANIM/Idle', trackTime: 0.25 }],
    };
}

function canvas() {
    return {
        width: 320,
        height: 240,
        clientWidth: 320,
        clientHeight: 240,
        getContext() { return {}; },
        addEventListener() {},
        removeEventListener() {},
    };
}

function store() {
    const assets = new Map([
        ['pkg/model/Demo.atlas.txt', 'Page.png\nsize: 2, 2\nscale: 1\n'],
        ['pkg/model/Page.png', new Uint8Array([137, 80, 78, 71])],
        ['pkg/model/Demo.skel.bytes', new Uint8Array([4, 3, 2, 3])],
    ]);
    return {
        async open(path, options = {}) {
            const value = assets.get(path);
            if (value == null) throw new Error(`missing ${path}`);
            if (options.as === 'text') return String(value);
            return value;
        },
        list() { return [...assets.keys()]; },
    };
}

test('renderer exports safe path and atlas helpers', () => {
    assert.equal(normalizePath('pkg\\assets\\Demo\\Demo.skel.bytes'), 'pkg/assets/Demo/Demo.skel.bytes');
    assert.equal(normalizePath('../outside.png'), '');
    assert.deepEqual(
        extractAtlasPageNames('Page.png\nsize: 2, 2\n\nPage.png\nOther.png\n'),
        ['Page.png', 'Other.png'],
    );
});

test('raw atlas page registration preserves a relative dot segment without a double slash', () => {
    const renderer = new SpineRenderer({canvas:canvas(),manifest:{},assetStore:store()});
    const keys=[];
    renderer.assetManager={setRawDataURI:(key)=>keys.push(key)};
    renderer._registerRawPage('./贴图.png','assets/贴图.png','assets/',new Uint8Array([137,80,78,71]));
    assert.ok(keys.includes('assets/./贴图.png'));
    assert.ok(!keys.some(key=>key.includes('//')));
});


test('renderer fails clearly when no runtime or loader is available', async () => {
    await assert.rejects(
        () => SpineRenderer.create({ canvas: canvas(), manifest: manifest(), assetStore: store() }),
        (error) => error instanceof SpineRendererError && error.code === 'runtime_unavailable',
    );
});


test('renderer advances skeleton physics time while playing and simulates seeks', async () => {
    const runtime = fakeRuntime();
    const renderer = await SpineRenderer.create({ canvas: canvas(), manifest: manifest(), assetStore: store(), runtime, renderOnLoad: false });
    renderer.selectAnimation('ANIM/Idle', { autoplay: true, loop: true, render: false });
    renderer.tick(0.1, { render: false });
    assert.ok(Math.abs(renderer.skeleton.time - 0.1) < 1e-8);
    renderer.setTime(0.5, { render: false });
    assert.ok(Math.abs(renderer.skeleton.time - 0.5) < 1e-8);
    renderer.setTime(0.2, { render: false });
    assert.ok(Math.abs(renderer.skeleton.time - 0.2) < 1e-8);
    renderer.pause();
    renderer.tick(0.1, { render: false });
    assert.ok(Math.abs(renderer.skeleton.time - 0.2) < 1e-8);
    const frame = renderer.captureFrame({ maxDimension: 64 });
    assert.ok(frame.width <= 64 && frame.height <= 64);
    renderer.dispose();
    assert.equal(renderer.status().disposed, true);
});
test('skin names remain exact and are not interpreted as part groups or default tags', () => {
    const renderer = new SpineRenderer({ canvas: canvas(), manifest: {}, assetStore: store() });
    const skin = { name: 'Hair/[Required]/A' };
    renderer.skeletonData = { skins: [skin], findSkin: (name) => name === skin.name ? skin : null };
    let applied;
    renderer.skeleton = { setSkin: (value) => { applied = value; }, setupPoseSlots() {} };
    assert.deepEqual(renderer.skinOptions().skin.options, [skin.name]);
    renderer._characterSkinSelections = [{ group: 'skin', value: skin.name }];
    renderer._applyCharacterSkins();
    assert.equal(applied, skin);
});

test('DPR does not compound when the renderer resizes repeatedly', async () => {
    const target = canvas();
    const renderer = await SpineRenderer.create({canvas: target, manifest: manifest(), assetStore: store(),
        runtime: fakeRuntime(), devicePixelRatio: 2, renderOnLoad: false});
    for (let index = 0; index < 4; index++) renderer.render();
    assert.equal(target.width, 640);
    assert.equal(target.height, 480);
    renderer.dispose();
});

test('capture returns straight RGB for translucent framebuffer pixels', async () => {
    const renderer = await SpineRenderer.create({canvas: canvas(), manifest: manifest(), assetStore: store(),
        runtime: fakeRuntime(), renderOnLoad: false});
    renderer.gl.readPixels = (x,y,w,h,format,type,output) => {
        for (let offset = 0; offset < output.length; offset += 4) output.set([50,25,0,128], offset);
    };
    const frame = renderer.captureFrame();
    assert.deepEqual([...frame.data.slice(0,4)], [100,50,0,128]);
    renderer.dispose();
});
test('mismatched Spine versions are rejected by default', async () => {
    const packageData = manifest(); packageData.source.spineVersion = '4.2.0';
    await assert.rejects(SpineRenderer.create({ canvas: canvas(), manifest: packageData, assetStore: store(), runtime: fakeRuntime() }),
        (error) => error.code === 'runtime_version_mismatch');
});
test('the bundled runtime physics clock is advanced by the real renderer pose loop', () => {
    const fs = require('node:fs'), vm = require('node:vm');
    const ctx = { console, TextEncoder, TextDecoder, Float32Array, Uint8Array, ArrayBuffer };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(require.resolve('../../static/libs/spine/spine-webgl-4.3.13.min.js'), 'utf8'), ctx);
    const spine = ctx.spine;
    const data = new spine.SkeletonData(); data.bones.push(new spine.BoneData(0, 'root', null));
    const skeleton = new spine.Skeleton(data);
    const state = new spine.AnimationState(new spine.AnimationStateData(data));
    const receiver = { runtime: spine, skeleton, state, _restoreRootRotationBasis() {}, _restoreSlotColors() {}, _restoreCharacterAttachments() {}, _applyCharacterSlotStyles() {}, _captureRootRotationBasis() {} };
    for (let i = 0; i < 60; i++) SpineRenderer.prototype._applyPose.call(receiver, 1 / 60);
    assert.ok(Math.abs(skeleton.time - 1) < 1e-8);
});

test('global color changes stay stable during playback and restore original slot colors', () => {
    const fs = require('node:fs'), vm = require('node:vm');
    const ctx = { console, TextEncoder, TextDecoder, Float32Array, Uint8Array, ArrayBuffer };
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync(require.resolve('../../static/libs/spine/spine-webgl-4.3.13.min.js'), 'utf8'), ctx);
    const spine = ctx.spine;
    const data = new spine.SkeletonJson({}).readSkeletonData({
        skeleton: { spine: '4.3.13' }, bones: [{ name: 'root' }],
        slots: [{ name: 'body', bone: 'root', color: '80402080' }], skins: [], animations: {},
    });
    const renderer = new SpineRenderer({ canvas: canvas(), manifest: {}, assetStore: store() });
    renderer.runtime = spine;
    renderer.skeleton = new spine.Skeleton(data);
    renderer.state = new spine.AnimationState(new spine.AnimationStateData(data));
    renderer.loaded = true;
    const color = renderer.skeleton.slots[0].pose.color;
    const original = { r: color.r, g: color.g, b: color.b, a: color.a };
    renderer.setColorMode({ mode: 'two-tone', primary: { r: 0.5, g: 0.75, b: 1, a: 0.5 } }, { render: false });
    for (let i = 0; i < 120; i++) renderer._applyPose(1 / 60);
    assert.ok(Math.abs(color.r - original.r * 0.5) < 1e-8);
    assert.ok(Math.abs(color.a - original.a * 0.5) < 1e-8);
    renderer.setColorMode({ mode: 'default' }, { render: false });
    for (const key of ['r', 'g', 'b', 'a']) assert.ok(Math.abs(color[key] - original[key]) < 1e-8);
});
