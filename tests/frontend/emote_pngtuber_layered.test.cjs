const test = require('node:test');
const assert = require('node:assert/strict');

const layered = require('../../static/js/emote_pngtuber_layered.js');

function state(overrides) {
    return {
        stateKey: 'idle_image',
        label: 'Idle',
        frames: 8,
        width: 256,
        height: 256,
        sheetWidth: 256 * 8,
        sheetHeight: 256,
        fps: 12,
        pngBytes: new Uint8Array([0x89, 0x50, 0x4e, 0x47, 1, 2, 3, 4]),
        ...overrides,
    };
}

function twoStates() {
    return [
        state({}),
        state({ stateKey: 'talking_image', frames: 1, sheetWidth: 256, fps: 1 }),
    ];
}

test('emits a manifest the runtime recognises as layered', () => {
    const built = layered.buildLayeredPackage({ states: twoStates(), canvasWidth: 512, canvasHeight: 512 });
    const m = built.metadata;
    assert.equal(m.runtime, 'layered_canvas');
    assert.equal(built.adapter, 'layered_canvas_v1');
    assert.equal(m.state_count, 2);
    assert.deepEqual(m.canvas, { width: 512, height: 512 });
    assert.deepEqual(
        m.state_catalog.map((entry) => [entry.state_index, entry.state_number, entry.name]),
        [[0, 1, 'idle_image'], [1, 2, 'talking_image']],
    );
});

test("layer.states is positional: length equals state_count, not the layer's own count", () => {
    // 运行时用 states[this.layeredStateIndex] 取值，不是按 state_index 搜索。
    // 写成"每层只放自己那一条"会让下标取到 undefined，包能加载但什么都不画。
    const built = layered.buildLayeredPackage({ states: twoStates() });
    built.metadata.layers.forEach((layer, index) => {
        assert.equal(layer.states.length, 2, `layer ${index} 的 states 必须铺满 state_count`);
        layer.states.forEach((entry, position) => {
            assert.equal(entry.state_index, position);
            assert.equal(entry.visible, position === index,
                `layer ${index} 在状态 ${position} 的可见性应当是 ${position === index}`);
        });
        assert.equal(layer.states[index].frames > 1 || layer.states[index].frames === 1, true);
    });
});

test('animation_speed is fps divided by the runtime 4x multiplier', () => {
    // 帧进度 = floor(elapsed * animation_speed * REMIX_FRAME_SPEED_MULTIPLIER)
    const built = layered.buildLayeredPackage({ states: twoStates() });
    const idle = built.metadata.layers[0].states[0];
    assert.equal(idle.animation_speed, 3, '12fps 要写成 3，写成 12 会快四倍');
    assert.equal(idle.animation_speed * layered.FRAME_SPEED_MULTIPLIER, 12);
});

test('preserves the fractional frame rate of a sampled loop', () => {
    const built = layered.buildLayeredPackage({ states: [state({ fps: 8 / 3 })] });
    const idle = built.metadata.layers[0].states[0];
    assert.equal(idle.frames / (idle.animation_speed * layered.FRAME_SPEED_MULTIPLIER), 3);
});

test('sheet addressing describes a horizontal strip', () => {
    const built = layered.buildLayeredPackage({ states: twoStates() });
    const idle = built.metadata.layers[0].states[0];
    assert.deepEqual(
        { h: idle.hframes, v: idle.vframes, f: idle.frames, fw: idle.frame_width, iw: idle.image_width },
        { h: 8, v: 1, f: 8, fw: 256, iw: 2048 },
    );
});

test('each layer carries its own frame data at its own position only', () => {
    // 运行时按 states[layeredStateIndex] 取值，所以"自己的位置"才是真数据，
    // 其余位置必须是 hframes:1 + visible:false 的占位。只对 index 0 成立的断言
    // 抓不到这个错位。
    const states = [
        state({ stateKey: 'idle_image', frames: 6, sheetWidth: 256 * 6 }),
        state({ stateKey: 'talking_image', frames: 1, sheetWidth: 256, fps: 1 }),
        state({ stateKey: 'happy_image', frames: 4, sheetWidth: 256 * 4 }),
    ];
    const built = layered.buildLayeredPackage({ states });
    built.metadata.layers.forEach((layer, index) => {
        assert.equal(layer.states.length, 3, `layer ${index} 长度`);
        const own = layer.states[index];
        assert.equal(own.visible, true, `layer ${index} 自己位置应可见`);
        assert.equal(own.hframes, [6, 1, 4][index], `layer ${index} 自己位置的 hframes`);
        assert.equal(own.frames, [6, 1, 4][index]);
        assert.equal(own.animation_speed, [3, 0.25, 3][index], '帧速应为 fps/4');
        layer.states.forEach((entry, position) => {
            if (position === index) return;
            assert.equal(entry.visible, false, `layer ${index} 在位置 ${position} 应不可见`);
            assert.equal(entry.hframes, 1);
            assert.equal(entry.animation_speed, 0);
        });
    });
});

test('sprite_sheet_animation is on and the rest are explicitly off', () => {
    // runtime_features 缺省即关；没写出来的特性等于 false，所以要显式列全。
    const features = layered.buildLayeredPackage({ states: twoStates() }).metadata.runtime_features;
    assert.equal(features.sprite_sheet_animation, true);
    ['layer_motion', 'layered_breathing', 'physics_v2', 'mesh_deformation', 'costume_change_bounce']
        .forEach((name) => assert.equal(features[name], false, name));
});

test('does not claim capabilities the baked composite cannot honour', () => {
    const caps = layered.buildLayeredPackage({ states: twoStates() }).metadata.capabilities;
    assert.equal(caps.mesh_runtime, false);
    assert.equal(caps.physics_runtime, false);
    assert.equal(caps.hotkeys, false);
    assert.equal(caps.animated_states, true);
});

test('emotion names map onto their state index', () => {
    const states = [
        state({ stateKey: 'idle_image' }),
        state({ stateKey: 'happy_image' }),
        state({ stateKey: 'surprised_image' }),
    ];
    const m = layered.buildLayeredPackage({ states }).metadata;
    assert.deepEqual(m.emotion_mappings, {
        happy: { state_index: 1 },
        surprised: { state_index: 2 },
    });
});

test('sheets and metadata are all returned as files', () => {
    const built = layered.buildLayeredPackage({ states: twoStates() });
    assert.deepEqual(
        built.files.map((file) => file.path).sort(),
        ['layers/0-idle_image.png', 'layers/1-talking_image.png', 'metadata.json'],
    );
    const meta = built.files.find((file) => file.path === 'metadata.json');
    assert.deepEqual(
        JSON.parse(new TextDecoder().decode(meta.data)).state_catalog.map((s) => s.name),
        ['idle_image', 'talking_image'],
    );
});

test('rejects an empty or duplicated state list', () => {
    assert.throws(() => layered.buildLayeredPackage({ states: [] }), { code: 'no_states' });
    assert.throws(() => layered.buildLayeredPackage({
        states: [state({}), state({})],
    }), { code: 'bad_state_key' });
});

test('rejects a sheet wider than the encoder ceiling instead of truncating', () => {
    assert.throws(() => layered.buildLayeredPackage({
        states: [state({ frames: 40, sheetWidth: 40 * 256 })],   // 10240 > 4096
    }), { code: 'sheet_too_large' });
});

test('rejects a state without sheet bytes', () => {
    assert.throws(() => layered.buildLayeredPackage({
        states: [state({ pngBytes: null })],
    }), { code: 'missing_sheet' });
});

test('frame and canvas numbers never become 0 or negative', () => {
    const built = layered.buildLayeredPackage({
        states: [state({ frames: 0, width: -5, height: null, fps: 0 })],
        canvasWidth: 'abc',
        canvasHeight: undefined,
    });
    const entry = built.metadata.layers[0].states[0];
    assert.equal(Number.isInteger(entry.frames) && entry.frames >= 1, true);
    assert.equal(Number.isInteger(entry.frame_width) && entry.frame_width >= 1, true);
    assert.ok(built.metadata.canvas.width >= 1 && built.metadata.canvas.height >= 1);
});

test('state count beyond the ceiling is refused rather than emitted half-broken', () => {
    const many = Array.from({ length: layered.MAX_STATES + 1 }, (unused, i) => (
        state({ stateKey: `s${i}_image`, frames: 1, sheetWidth: 256 })
    ));
    assert.throws(() => layered.buildLayeredPackage({ states: many }), { code: 'too_many_states' });
});

// ── 折行雪碧图：metadata 必须照实写网格，且不能把补齐空格当动画帧 ─────────────
test('多行雪碧图写出真实网格（不是一行摆完）', () => {
    // 60 帧 × 512px 会超过 4096px 上限，合成方按 8×8 折行；
    // 曾经 metadata 谎报成"1 行 60 格"，运行时于是把每格切成 68px 窄条来回闪
    const png = new Uint8Array([1, 2, 3]);
    const built = layered.buildLayeredPackage({
        canvasWidth: 512, canvasHeight: 512,
        states: [{
            stateKey: 'idle_image', frames: 64, frameCount: 60,
            hframes: 8, vframes: 8,
            width: 512, height: 512, sheetWidth: 4096, sheetHeight: 4096,
            fps: 12, pngBytes: png,
        }],
    });
    const state = built.metadata.layers[0].states[0];
    assert.equal(state.hframes, 8);
    assert.equal(state.vframes, 8);
    assert.equal(state.frames, 60, '补齐的 4 个空格不该被当成动画帧');
});

test('没传网格时仍按单行处理（老调用方不受影响）', () => {
    const built = layered.buildLayeredPackage({
        canvasWidth: 256, canvasHeight: 256,
        states: [{ stateKey: 'idle_image', frames: 6, width: 256, height: 256,
            sheetWidth: 1536, sheetHeight: 256, fps: 12, pngBytes: new Uint8Array([1]) }],
    });
    const state = built.metadata.layers[0].states[0];
    assert.equal(state.hframes, 6);
    assert.equal(state.vframes, 1);
    assert.equal(state.frames, 6);
});

// 网格的来源是"随包发出去的那张贴图"，不是调用方顺口报的数字：调用方谎报一次
// 就是刚才那个闪烁故障，所以这里要证明谎报不会写进 metadata。
test('调用方谎报网格时，以贴图尺寸为准', () => {
    const built = layered.buildLayeredPackage({
        canvasWidth: 512, canvasHeight: 512,
        states: [{ stateKey: 'idle_image', frames: 64, frameCount: 60,
            hframes: 64, vframes: 1,            // ← 曾经的 bug：一行摆完
            width: 512, height: 512, sheetWidth: 4096, sheetHeight: 4096,
            fps: 12, pngBytes: new Uint8Array([1]) }],
    });
    const state = built.metadata.layers[0].states[0];
    assert.deepEqual(
        { h: state.hframes, v: state.vframes, f: state.frames, iw: state.image_width },
        { h: 8, v: 8, f: 60, iw: 4096 },
    );
});

test('每个状态按自己的格子尺寸取格，不跟着第一个状态走', () => {
    const built = layered.buildLayeredPackage({
        canvasWidth: 512, canvasHeight: 512,
        states: [
            { stateKey: 'idle_image', frames: 8, width: 256, height: 256,
                sheetWidth: 2048, sheetHeight: 256, fps: 12, pngBytes: new Uint8Array([1]) },
            { stateKey: 'happy_image', frames: 2, width: 512, height: 512,
                sheetWidth: 1024, sheetHeight: 512, fps: 12, pngBytes: new Uint8Array([1]) },
        ],
    });
    assert.deepEqual(
        built.metadata.layers.map((layer) => {
            const own = layer.states[layer.states.findIndex((s) => s.visible !== false)];
            return { h: own.hframes, fw: own.frame_width, iw: own.image_width };
        }),
        [{ h: 8, fw: 256, iw: 2048 }, { h: 2, fw: 512, iw: 1024 }],
    );
});

test('状态目录带上去掉 _image 的别名，运行时传状态名也能命中', () => {
    const built = layered.buildLayeredPackage({ states: twoStates() });
    assert.deepEqual(
        built.metadata.state_catalog.map((entry) => [entry.name, entry.aliases]),
        [['idle_image', ['idle']], ['talking_image', ['talking']]],
    );
    // 调用方自己给了别名就用它的，不能被默认值覆盖
    const custom = layered.buildLayeredPackage({
        states: [state({ aliases: ['站立', 'stand'] })],
    });
    assert.deepEqual(custom.metadata.state_catalog[0].aliases, ['站立', 'stand']);
});

test('声明的帧数多过格子数时按格子数夹住（否则尾部永远取到图外）', () => {
    const built = layered.buildLayeredPackage({
        canvasWidth: 256, canvasHeight: 256,
        states: [{ stateKey: 'idle_image', frames: 6, frameCount: 5000, width: 256, height: 256,
            sheetWidth: 1536, sheetHeight: 256, fps: 12, pngBytes: new Uint8Array([1]) }],
    });
    assert.equal(built.metadata.layers[0].states[0].frames, 6);
});

test('each state can carry its own scale and rotation', () => {
    const states = [
        { stateKey: 'idle_image', frames: 4, width: 64, height: 64, sheetWidth: 256, fps: 12,
            pngBytes: new Uint8Array([1, 2, 3]) },
        { stateKey: 'happy_image', frames: 2, width: 64, height: 64, sheetWidth: 128, fps: 12,
            pngBytes: new Uint8Array([4, 5, 6]), scale: 1.4, rotation: 0.35, x: -20, y: 12 },
    ];
    const m = layered.buildLayeredPackage({ canvasWidth: 64, canvasHeight: 64, states }).metadata;
    const happy = m.layers[1];
    // layer 1 的"自己位置"是索引 1；索引 0 是占位
    assert.equal(happy.states[0].visible, false, '别的状态的位置应为占位');
    assert.equal(happy.states[0].scale, 1, '占位槽要显式中性，不能留空');
    assert.equal(happy.states[0].rotation, 0);
    assert.equal(happy.states[1].visible, true);
    assert.equal(happy.rotation, 0.35, '层上的静态角度要写进去');
    assert.deepEqual(happy.scale, [1.4, 1.4]);
    assert.equal(happy.x, -20);
    assert.equal(happy.y, 12);
});

test('non-finite scale and rotation do not poison the manifest', () => {
    const m = layered.buildLayeredPackage({
        canvasWidth: 64, canvasHeight: 64,
        states: [{ stateKey: 'idle_image', frames: 1, width: 64, height: 64, sheetWidth: 64,
            fps: 12, pngBytes: new Uint8Array([1]), scale: 'big', rotation: NaN }],
    }).metadata;
    assert.equal(m.layers[0].scale[0], 1);
    assert.equal(m.layers[0].rotation, 0);
});
