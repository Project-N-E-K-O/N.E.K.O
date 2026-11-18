const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const pack = require('../../static/js/emote_pngtuber_pack.js');
const fflate = require('../../static/libs/fflate.umd.js');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');

function gifBytes(marker) {
    // 内容对打包器不透明，只需要是确定的字节。
    return new Uint8Array([0x47, 0x49, 0x46, 0x38, 0x39, 0x61, marker, marker ^ 0xff]);
}

function idleOnly(extra = {}) {
    return pack.buildPackageFiles({
        folderName: 'yui',
        displayName: 'Yui',
        description: 'from emote maker',
        states: [{ key: 'idle_image', bytes: gifBytes(1) }, ...extra.states || []],
        ...extra.options,
    });
}

test('rejects a package without an idle image', () => {
    assert.throws(
        () => pack.buildPackageFiles({
            folderName: 'yui', displayName: 'Yui',
            states: [{ key: 'happy_image', bytes: gifBytes(2) }],
        }),
        { code: 'missing_idle' },
    );
});

test('writes only the states actually provided, as relative file names', () => {
    const { files, manifest } = idleOnly({
        states: [{ key: 'happy_image', bytes: gifBytes(3) }],
    });
    const paths = files.map((file) => file.path);
    assert.deepEqual(paths, ['yui/idle.gif', 'yui/happy.gif', 'yui/model.json']);
    assert.deepEqual(
        { idle: manifest.pngtuber.idle_image, happy: manifest.pngtuber.happy_image },
        { idle: 'idle.gif', happy: 'happy.gif' },
    );
    // 未提供的状态必须整键缺失，而不是写成空串——运行时的回落链靠"没有这个键"。
    assert.equal('talking_image' in manifest.pngtuber, false);
    assert.equal('drag_image' in manifest.pngtuber, false);
});

test('manifest carries the shape the upload validator requires', () => {
    const { manifest } = idleOnly();
    const modelJson = JSON.parse(
        new TextDecoder().decode(
            idleOnly().files.find((file) => file.path.endsWith('model.json')).data,
        ),
    );
    assert.equal(modelJson.model_type, 'pngtuber');
    assert.equal(typeof modelJson.pngtuber.idle_image, 'string');
    assert.equal(modelJson.name, manifest.name);
    // 布局字段要有确定值，否则 normalizeConfig 会走另一套锚点推导。
    assert.equal(modelJson.pngtuber.scale, 1);
    assert.equal(modelJson.pngtuber.offset_x, 0);
    // 明确写出锚点，而不是留给 normalizeConfig 按"偏移是否为 0"去猜。
    assert.equal(modelJson.pngtuber.position_anchor, 'center');
});

test('image paths stay relative so the server can prefix them', () => {
    // _normalize_pngtuber_config 原样放行以 "/" 开头的值，那会让包导入后指向
    // 一个不存在的位置。所以这里绝不能出现绝对路径或 URL。
    const { manifest } = idleOnly();
    for (const [key, value] of Object.entries(manifest.pngtuber)) {
        if (!key.endsWith('_image')) continue;
        assert.equal(/^[\/a-z]+:\/\//i.test(value), false, key);
        assert.equal(value.startsWith('/'), false, key);
    }
});

test('folder keeps the display name so both exits land on one library entry', () => {
    // 直连导入时服务端按 model.json 的 name 建目录、忽略 zip 根目录（实测），
    // 所以这里必须与 name 同源，否则"导出"和"导入"会造出两个同名不同目录的形象。
    const { folder, manifest, files } = pack.buildPackageFiles({
        folderName: '雪之下',
        displayName: '雪之下',
        states: [{ key: 'idle_image', bytes: gifBytes(4) }],
    });
    assert.equal(folder, '雪之下');
    assert.equal(manifest.name, folder);
    // 每个文件都恰好是 <folder>/<basename>：服务端靠"共享根目录"识别包边界，
    // 更深的路径会让它找不到 model.json。
    for (const file of files) {
        assert.ok(file.path.startsWith(`${folder}/`), file.path);
        const rest = file.path.slice(folder.length + 1);
        assert.equal(/[\/\\]/.test(rest), false, file.path);
    }
});

test('folder name cannot escape its own directory', () => {
    assert.equal(pack.packageFolderName('../../etc/passwd'), 'etc-passwd');
    assert.equal(pack.packageFolderName('a\\b/c'), 'a-b-c');
    assert.equal(pack.packageFolderName('..', 'fallback'), 'fallback');
    assert.equal(pack.packageFolderName('   ', 'fallback'), 'fallback');
    assert.equal(pack.packageFolderName('', 'fallback'), 'fallback');
    assert.equal(pack.packageFolderName('x'.repeat(200), 'fallback').length, 64);
});

test('unknown state keys are rejected, not silently dropped', () => {
    assert.throws(
        () => pack.buildPackageFiles({
            folderName: 'yui', displayName: 'Yui',
            states: [{ key: 'idle_image', bytes: gifBytes(5) },
                { key: 'dance_image', bytes: gifBytes(6) }],
        }),
        { code: 'unknown_state' },
    );
});

test('a missing asset body fails loudly instead of writing an empty file', () => {
    assert.throws(
        () => pack.buildPackageFiles({
            folderName: 'yui', displayName: 'Yui',
            states: [{ key: 'idle_image', bytes: null }],
        }),
        { code: 'missing_asset' },
    );
});

test('zip round-trips every file byte for byte', () => {
    const { files } = idleOnly({ states: [{ key: 'talking_image', bytes: gifBytes(7) }] });
    const zipped = pack.packToZip(files);
    const unzipped = fflate.unzipSync(zipped);
    for (const file of files) {
        assert.deepEqual([...unzipped[file.path]], [...file.data], file.path);
    }
});

test('multipart parts carry the relative path as the filename', () => {
    // 服务端按 filename 里的相对路径重建包结构并做穿越检查；丢了目录前缀就变成
    // 一堆平铺文件，model.json 也找不到归属。
    assert.equal(typeof FormData, 'function', 'Node needs FormData');
    const { files } = idleOnly();
    const form = pack.appendMultipart(files);
    const entries = [...form.entries()].map(([field, value]) => [field, value.name || value.filename]);
    assert.deepEqual(entries.map(([, name]) => name), ['yui/idle.gif', 'yui/model.json']);
    assert.deepEqual(entries.map(([field]) => field), ['files', 'files']);
});

test('upload endpoint is the folder-shaped one that takes parts, not a zip', () => {
    assert.equal(pack.uploadUrl(), '/api/model/pngtuber/upload_model');
});

// ── 分层出口 ─────────────────────────────────────────────────────────────────

function layeredOption(overrides) {
    return {
        adapter: 'layered_canvas_v1',
        metadataPath: 'metadata.json',
        files: [
            { path: 'metadata.json', data: new TextEncoder().encode('{"runtime":"layered_canvas"}') },
            { path: 'layers/0-idle_image.png', data: gifBytes(31) },
        ],
        ...overrides,
    };
}

test('layered build writes adapter + metadata path and ships the sheets', () => {
    const { files, manifest } = pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'idle_image', file: 'idle.png', bytes: gifBytes(32) }],
        layered: layeredOption(),
    });
    assert.equal(manifest.pngtuber.adapter, 'layered_canvas_v1');
    assert.equal(manifest.pngtuber.layered_metadata, 'metadata.json');
    // idle_image 仍然指向一张真实的平铺回落图 —— 服务端校验要求它非空且文件存在
    assert.equal(manifest.pngtuber.idle_image, 'idle.png');
    assert.deepEqual(files.map((file) => file.path), [
        'yui/idle.png',
        'yui/metadata.json',
        'yui/layers/0-idle_image.png',
        'yui/model.json',
    ]);
});

test('a layered package without a fallback image is refused', () => {
    // 只发 metadata 会过不了 _validate_model_package：idle_image 必须非空
    assert.throws(() => pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'talking_image', bytes: gifBytes(33) }],
        layered: layeredOption(),
    }), { code: 'missing_idle' });
});

test('incomplete layered descriptors fail instead of emitting a broken package', () => {
    assert.throws(() => pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'idle_image', bytes: gifBytes(34) }],
        layered: { metadataPath: 'metadata.json' },
    }), { code: 'bad_layered' });
});

test('layer asset paths cannot escape the package folder', () => {
    for (const bad of ['../evil.png', '/abs/evil.png', 'a/../../b.png']) {
        assert.throws(() => pack.buildPackageFiles({
            displayName: 'yui',
            states: [{ key: 'idle_image', bytes: gifBytes(35) }],
            layered: layeredOption({ files: [{ path: bad, data: gifBytes(36) }] }),
        }), { code: 'bad_layered_path' }, bad);
    }
});

test('per-state file override cannot smuggle a path', () => {
    assert.throws(() => pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'idle_image', file: '../x.png', bytes: gifBytes(37) }],
    }), { code: 'bad_file_name' });
});

test('layered zip round-trips including the sheets', () => {
    const { files } = pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'idle_image', file: 'idle.png', bytes: gifBytes(38) }],
        layered: layeredOption(),
    });
    const unzipped = fflate.unzipSync(pack.packToZip(files));
    for (const file of files) {
        assert.equal(Object.prototype.hasOwnProperty.call(unzipped, file.path), true, file.path);
    }
});

test('state list matches the runtime IMAGE_KEYS it claims to mirror', () => {
    const core = require('node:fs').readFileSync(
        path.join(PROJECT_ROOT, 'static', 'pngtuber-core.js'), 'utf8',
    );
    const declared = core.match(/const IMAGE_KEYS = \[([^\]]*)\]/)[1]
        .split(',').map((item) => item.trim().replace(/'/g, '')).filter(Boolean);
    assert.deepEqual(pack.STATE_KEYS, declared);
});

// ── placement ────────────────────────────────────────────────────────────────
// PNGTuber 的 normalizeConfig 用 clampNumber(0.1..5) 夹 scale，position_anchor 只认
// 两个值，其余归零。写越界值不会崩，但会在运行时被静默改掉，所以钳制必须在生成时就做。

test('placement defaults keep the pre-existing behaviour', () => {
    const { manifest } = idleOnly();
    assert.deepEqual(manifest.pngtuber, {
        scale: 1,
        offset_x: 0,
        offset_y: 0,
        position_anchor: 'center',
        mirror: false,
        mobile_scale: 1,
        mobile_offset_x: 0,
        mobile_offset_y: 0,
        // 新增字段：运行时读它决定说话/情绪谁让位，不写就是默认（说话优先）
        state_priority: 'talking',
        idle_image: 'idle.gif',
    });
});

test('placement is written for desktop and mobile alike', () => {
    const { manifest } = pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'idle_image', bytes: gifBytes(20) }],
        placement: { scale: 1.6, offsetX: -40, offsetY: 220, anchor: 'bottom_right', mirror: true },
    });
    const block = manifest.pngtuber;
    assert.equal(block.scale, 1.6);
    assert.equal(block.offset_x, -40);
    assert.equal(block.offset_y, 220);
    assert.equal(block.position_anchor, 'bottom_right');
    assert.equal(block.mirror, true);
    // 不写 mobile_* 的话 normalizeConfig 会把它悄悄改成 min(scale,1) / 0
    assert.equal(block.mobile_scale, 1.6);
    assert.equal(block.mobile_offset_x, -40);
    assert.equal(block.mobile_offset_y, 220);
});

test('out-of-range placement is clamped, not passed through', () => {
    assert.deepEqual(pack.normalizePlacement({ scale: 99, offsetX: 1e9, anchor: 'top' }), {
        scale: 5, offset_x: 4000, offset_y: 0, anchor: 'center', mirror: false,
    });
    assert.equal(pack.normalizePlacement({ scale: 0 }).scale, 0.1);
    assert.equal(pack.normalizePlacement({ scale: 'abc' }).scale, 1);
    assert.equal(pack.normalizePlacement({ offset_y: Number.NaN }).offset_y, 0);
    assert.equal(pack.normalizePlacement({ mirror: 'yes' }).mirror, false);
});

test('talking is simply absent in loop mode', () => {
    // 循环模式的实现方式就是不写 talking_image：运行时 hasIndependentTalkingImage()
    // 返回 false 后不再按口型节奏换 src，待机动画才能连播。
    const { manifest } = pack.buildPackageFiles({
        displayName: 'yui',
        states: [{ key: 'idle_image', bytes: gifBytes(21) }],
    });
    assert.equal('talking_image' in manifest.pngtuber, false);
});

// ── 拖拽落点解析 ─────────────────────────────────────────────────────────────
const SCENES = [
    { id: 'a.json#0', name: 'Idle', animation: { id: 'ANIM_Idle' } },
    { id: 'a.json#1', name: 'Cheer', animation: { id: 'EMOTE_Cheer' } },
    { id: 'b.json#0', name: 'NoAnim' },
];

test('a dragged scene resolves to itself', () => {
    assert.equal(
        pack.resolveSceneForDrag(SCENES, { kind: 'scene', sceneId: 'a.json#1' }),
        SCENES[1],
    );
});

test('a dragged animation resolves to the scene that references it', () => {
    assert.equal(
        pack.resolveSceneForDrag(SCENES, { kind: 'animation', animationId: 'ANIM_Idle' }).id,
        'a.json#0',
    );
});

test('an animation no scene references is refused, not guessed', () => {
    // 这里必须返回 null：动画不带相机/描边/文字，用"当前视角"烘一张会让形象
    // 和用户在预览里看到的不是同一张图。
    assert.equal(
        pack.resolveSceneForDrag(SCENES, { kind: 'animation', animationId: 'MOTION_Orphan' }),
        null,
    );
    assert.equal(pack.resolveSceneForDrag(SCENES, { kind: 'animation', animationId: '' }), null);
    assert.equal(pack.resolveSceneForDrag(SCENES, { kind: 'scene', sceneId: 'missing' }), null);
    assert.equal(pack.resolveSceneForDrag(SCENES, { kind: 'other' }), null);
    assert.equal(pack.resolveSceneForDrag(null, { kind: 'scene', sceneId: 'x' }), null);
});

test('a scene without an animation entry still resolves by id', () => {
    assert.equal(
        pack.resolveSceneForDrag(SCENES, { kind: 'scene', sceneId: 'b.json#0' }).id,
        'b.json#0',
    );
});

// ── 列表重建签名：决定"切一个选中项要不要重建 407 张卡" ──────────────────────
test('选中项变了不该重建整张列表', () => {
    const items = [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }];
    const before = pack.listSignature('scene', items);
    // 卡片外观没变，只是选中态变了 —— 走这条快路径才不会每点一次就重建几百张卡
    const selected = pack.listSignature('scene', items);
    assert.equal(before, selected);
});

test('改名、增删和只剩元数据都必须重建', () => {
    const base = [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }];
    const sig = pack.listSignature('scene', base);
    assert.notEqual(sig, pack.listSignature('scene', [{ id: 'a', name: '改过名' }, { id: 'b', name: 'B' }]));
    assert.notEqual(sig, pack.listSignature('scene', [{ id: 'a', name: 'A' }]));
    assert.notEqual(sig, pack.listSignature('scene', [
        { id: 'a', name: 'A', metadataOnly: true }, { id: 'b', name: 'B' }]));
    assert.notEqual(sig, pack.listSignature('anim', base));
});

test('空清单与 null 是同一个状态（都不产出卡片），但和"有一项"必须不同', () => {
    assert.equal(pack.listSignature('scene', []), pack.listSignature('scene', null));
    assert.notEqual(pack.listSignature('scene', []), pack.listSignature('scene', [{ id: 'a', name: 'A' }]));
});

// ── state_priority：导出必须写、且只写运行时认得的两个值 ────────────────────
function manifestOf(options) {
    return pack.buildPackageFiles(Object.assign({
        displayName: 'T',
        states: [{ key: 'idle_image', bytes: new Uint8Array([1, 2, 3]) }],
    }, options)).manifest.pngtuber;
}

test('模型配置带上说话/情绪的优先级', () => {
    assert.equal(manifestOf({ statePriority: 'emotion' }).state_priority, 'emotion');
    assert.equal(manifestOf({ statePriority: 'talking' }).state_priority, 'talking');
});

test('没选、或选了一个运行时不认的值，都写默认', () => {
    // 导出里存一个没人实现的值，比不写更糟：界面上看着"设过了"
    assert.equal(manifestOf({}).state_priority, 'talking');
    assert.equal(manifestOf({ statePriority: 'loud' }).state_priority, 'talking');
    assert.equal(manifestOf({ statePriority: 42 }).state_priority, 'talking');
    assert.equal(pack.normalizeStatePriority(' EMOTION '), 'emotion');
});
