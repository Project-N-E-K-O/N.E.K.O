const test = require('node:test');
const assert = require('node:assert/strict');

const library = require('../../static/js/emote_template_library.js');

function pkg(overrides = {}) {
    return {
        id: `spine-${'a'.repeat(64)}`,
        source: { kind: 'spine', packageName: 'demo.zip' },
        model: { displayName: '演示' },
        counts: { scenes: 12, animations: 7 },
        ...overrides,
    };
}

function entry(key, extra = {}) {
    return library.makeEntry({
        key,
        displayName: key,
        thumb: 'data:image/png;base64,AAAA',
        ...extra,
    });
}

function fakeStorage(initial = {}) {
    const data = { ...initial };
    return {
        data,
        getItem: (k) => (k in data ? data[k] : null),
        setItem: (k, v) => { data[k] = String(v); },
        removeItem: (k) => { delete data[k]; },
    };
}

test('同一个模板反复导入只占一个条目', () => {
    const a = library.entryKeyFor(pkg());
    const b = library.entryKeyFor(pkg({ model: { displayName: '改了显示名' } }));
    assert.equal(a, b, '显示名不该参与身份：改了名就变成两个模板是错的');
    assert.notEqual(a, library.entryKeyFor(pkg({ id: `spine-${'b'.repeat(64)}` })),
        '模型资源变了就是另一个模板');
});

test('认不出身份的模板拒绝入库，而不是挤成同一条', () => {
    assert.equal(library.entryKeyFor(null), '');
    assert.equal(library.entryKeyFor({}), '');
    assert.equal(library.entryKeyFor({ source: { packageName: 'demo.zip' }, counts: { scenes: 12 } }), '');
    assert.throws(() => library.makeEntry({}), { code: 'no_key' });
});

test('thirteenth template preserves every saved session and only caps thumbnail caches', () => {
    let store = library.emptyStore();
    const now = Date.now();
    for (let i = 0; i < 12; i += 1) {
        store = library.upsert(store, entry(`old-${i}`, { lastUsedAt: now - (i + 1) * 1000,
            session: { groups: [{name: `group-${i}`}], packSceneIds: {idle_image: `scene-${i}`} } }));
    }
    assert.equal(store.entries.length, 12);
    store = library.touch(store, 'old-11');
    store = library.upsert(store, entry('fresh'));
    const saved = library.parse(library.serialize(store));
    assert.equal(saved.entries.length, 13);
    assert.equal(saved.entries.filter(e => e.thumb).length, 12);
    assert.equal(library.find(saved, 'old-10').thumb, '');
    for (let i = 0; i < 12; i += 1) {
        assert.deepEqual(library.find(saved, `old-${i}`).session.groups, [{name: `group-${i}`}]);
        assert.equal(library.find(saved, `old-${i}`).session.packSceneIds.idle_image, `scene-${i}`);
    }
});

test('更新同一条目时保留加入时间，只刷新会话', () => {
    let store = library.upsert(library.emptyStore(), entry('k', { addedAt: 111, lastUsedAt: 111 }));
    store = library.upsert(store, entry('k', { addedAt: 999, displayName: '新名字' }));
    assert.equal(store.entries.length, 1);
    assert.equal(store.entries[0].addedAt, 111);
    assert.equal(store.entries[0].displayName, '新名字');
});

test('移除当前条目会一起清掉 currentKey', () => {
    let store = library.upsert(library.emptyStore(), entry('a'));
    store = library.touch(store, 'a');
    assert.equal(store.currentKey, 'a');
    store = library.remove(store, 'a');
    assert.equal(library.find(store, 'a'), null);
    assert.equal(store.currentKey, '', '留着指向已删条目的 currentKey，下次开页会找不到东西');
});

test('会话字段只认运行时真看得懂的值', () => {
    const session = library.normalizeSession({
        packFormat: 'layered', packPriority: ' EMOTION ', sceneId: 'a.json#1',
        packSceneIds: { idle_image: 'a.json#0' },
    });
    assert.equal(session.packFormat, 'layered');
    // 大小写/空格宽容，但认不得的一律回默认（别让界面能选出一个运行时不认的值）
    assert.equal(session.packPriority, 'emotion');
    assert.equal(session.sceneId, 'a.json#0'.slice(0, 0) + 'a.json#1');
    assert.deepEqual(session.packSceneIds, { idle_image: 'a.json#0' });
    assert.equal(library.normalizeSession({ packFormat: 'weird' }).packFormat, 'flat');
    assert.equal(library.normalizeSession({ packPriority: 'loud' }).packPriority, 'talking');
    assert.deepEqual(library.normalizeSession(null).character, { skins: [], slots: [] });
});

test('表演台的每状态设置活在节点图里，所以节点图必须跟着模板走', () => {
    const graph = { schemaVersion: 1, nodes: [{ id: 'o1' }], links: [] };
    assert.deepEqual(library.normalizeSession({ nodeGraph: graph }).nodeGraph, graph);
    assert.equal(library.normalizeSession({}).nodeGraph, null);
});

test('过大的缩略图直接丢掉，而不是把整库挤爆', () => {
    const huge = `data:image/png;base64,${'A'.repeat(library.MAX_THUMB_CHARS + 10)}`;
    const made = library.makeEntry({ key: 'k', thumb: huge });
    assert.equal(made.thumb, '');
    assert.equal(library.makeEntry({ key: 'k', thumb: 'data:small' }).thumb, 'data:small');
});

test('超体积时先牺牲缩略图，保住当前模板的缩略图到最后', () => {
    const filler = `data:image/png;base64,${'x'.repeat(8 * 1024)}`;
    let store = library.upsert(library.emptyStore(), entry('current', { thumb: filler }));
    store = library.upsert(store, entry('other', { thumb: filler }));
    store = library.touch(store, 'current');
    const shrunk = library.shrinkToFit(store, JSON.stringify(store).length - 2000);
    assert.ok(shrunk, '还能救');
    assert.equal(shrunk.entries.find((e) => e.key === 'other').thumb, '');
    assert.ok(shrunk.entries.find((e) => e.key === 'current').thumb.length > 0, '当前那条的图不该第一个被扔');
    const hopeless = library.shrinkToFit(store, 8);
    assert.equal(hopeless, null, '救不回来时要如实说，不要偷偷写一半');
});

test('存得下就丢图救回来，真存不下就如实抛错', () => {
    const thumb = `data:image/png;base64,${'y'.repeat(8 * 1024)}`;
    let store = library.upsert(library.emptyStore(), entry('current', { thumb }));
    store = library.upsert(store, entry('other', { thumb }));
    store = library.touch(store, 'current');
    const saved = JSON.parse(library.serialize(store, JSON.stringify(store).length - 2000));
    const current = saved.entries.find((item) => item.key === 'current');
    assert.ok(current, '当前模板不该被丢掉');
    assert.ok(current.thumb.length > 0, '当前那条的缩略图应当保到最后');
    assert.throws(() => library.serialize(store, 64), { code: 'quota' },
        '存不下时报告失败，不能悄悄丢掉其它模板');
});

test('quota failure preserves every older session instead of saving a partial library', () => {
    const current = entry('current', {
        lastUsedAt: 1,
        session: { groups: [{ name: 'saved group', notes: 'c'.repeat(2000) }] },
    });
    const newer = entry('newer', {
        lastUsedAt: 3,
        session: { groups: [{ name: 'another group', notes: 'n'.repeat(2000) }] },
    });
    const store = { ...library.emptyStore(), currentKey: current.key, entries: [newer, current] };
    const budget = JSON.stringify({ ...store, entries: [current] }).length;
    assert.throws(() => library.serialize(store, budget), { code: 'quota' });
    assert.deepEqual(store.entries.map(item => item.key), ['newer', 'current']);
    assert.deepEqual(store.entries[1].session.groups, current.session.groups);
});

test('current thumbnail is discarded before another template session is evicted', () => {
    let store = library.upsert(library.emptyStore(), entry('current', {
        thumb: `data:image/png;base64,${'z'.repeat(8192)}`,
    }));
    store = library.upsert(store, entry('other', {session: {groups: [{name: 'keep this group'}]}}));
    store = library.touch(store, 'current');
    const saved = library.shrinkToFit(store, JSON.stringify(store).length - 2000);
    assert.equal(saved.entries.length, 2);
    assert.equal(saved.entries.find(e => e.key === 'current').thumb, '');
    assert.equal(saved.entries.find(e => e.key === 'other').session.groups[0].name, 'keep this group');
});

test('quota failure never removes the least recently used template', () => {
    const entries = ['recent', 'oldest', 'current'].map((key, index) => entry(key, {
        lastUsedAt: [3, 1, 2][index],
        session: { groups: [{ name: key, notes: 'x'.repeat(2000) }] },
    }));
    const store = { ...library.emptyStore(), currentKey: 'current', entries };
    const budget = JSON.stringify({ ...store, entries: [entries[0], entries[2]] }).length;
    assert.equal(library.shrinkToFit(store, budget), null);
    assert.equal(store.entries.length, 3);
});

test('archive cards do not allocate sessions or mutate the persisted library', () => {
    const a = `spine-${'a'.repeat(64)}`, b = `spine-${'b'.repeat(64)}`;
    const store = library.upsert(library.emptyStore(), entry(a, {session: {groups: [{name:'keep'}]}}));
    const before = library.serialize(store);
    const archives = library.archiveEntries([{key:a,savedAt:1},{key:b,savedAt:2,sourceName:'原模板.zip'},{key:'invalid'}]);
    assert.deepEqual(archives.map(item => item.key), [a,b]);
    assert.ok(archives.every(item => item.session === null));
    assert.equal(library.entryLabel(archives[1]), '原模板');
    assert.equal(library.serialize(store), before);
    assert.equal(store.entries.length, 1);
    assert.equal(library.find(store, a).session.groups[0].name, 'keep');
});

test('session recovery uses the newer complete revision and preserves it through serialization', () => {
    const saved = {saveRevision:20,adjustments:{x:23},nodeGraph:{nodes:[]}};
    const recent = {saveRevision:10,adjustments:{x:10},groups:[{id:'stale'}]};
    assert.equal(library.selectSession(saved,recent), saved);
    assert.equal(library.selectSession(recent,saved), saved);
    assert.equal(library.selectSession(saved,{...recent,saveRevision:20}), saved);
    const store = library.upsert(library.emptyStore(),entry('saved',{session:saved}));
    assert.equal(library.parse(library.serialize(store)).entries[0].session.saveRevision,20);
    assert.equal(library.selectSession(null,saved),saved);
    assert.equal(library.selectSession(saved,null),saved);
});

test('legacy migration prefers versioned copies and merges recent fields only for unversioned pairs', () => {
    const legacy = {adjustments:{x:10},groups:[{id:'old'}]};
    const current = {saveRevision:1,adjustments:{x:23}};
    assert.equal(library.selectSession(current,legacy),current);
    assert.equal(library.selectSession(legacy,current),current);
    assert.deepEqual(library.selectSession(legacy,{adjustments:{x:23}}),
        {adjustments:{x:23},groups:[{id:'old'}]});
    for (const value of [NaN,Infinity,-1,2.5,'42'])
        assert.equal(library.normalizeSession({saveRevision:value}).saveRevision,0);
    const future = Date.now()+100000;
    assert.equal(library.nextSessionRevision({saveRevision:future},{saveRevision:'invalid'}),future+1);
});

test('failed oversized save leaves the previously persisted library untouched', () => {
    const storage = fakeStorage();
    let store = library.upsert(library.emptyStore(), entry('saved'));
    library.save(storage, store);
    const before = storage.getItem(library.STORAGE_KEY);
    store = library.upsert(store, entry('too-large', {session: {groups: [{notes:'x'.repeat(library.BUDGET_CHARS * 2)}]}}));
    assert.throws(() => library.save(storage, store), {code:'quota'});
    assert.equal(storage.getItem(library.STORAGE_KEY), before);
});

test('读回来的库坏了一部分也只丢坏的那条', () => {
    const storage = fakeStorage();
    library.save(storage, library.upsert(library.emptyStore(), entry('good')));
    const raw = JSON.parse(storage.getItem(library.STORAGE_KEY));
    raw.entries.push({ nope: true });
    storage.setItem(library.STORAGE_KEY, JSON.stringify(raw));
    const store = library.load(storage);
    assert.equal(store.entries.length, 1, '认不出的条目该丢');
    assert.equal(store.entries[0].key, 'good');
    assert.equal(library.load(fakeStorage()).entries.length, 0);
    const broken = fakeStorage({ [library.STORAGE_KEY]: '{不是JSON' });
    assert.deepEqual(library.load(broken), library.emptyStore(), '坏数据要当没有，不能抛');
    assert.deepEqual(library.load(null), library.emptyStore());
});

test('存到一半存储炸了不许把异常留给页面', () => {
    assert.throws(() => library.save(null, library.emptyStore()), { code: 'no_storage' });
    const full = {
        getItem: () => { throw new Error('denied'); },
        setItem: () => { throw new Error('QuotaExceededError'); },
    };
    assert.deepEqual(library.load(full), library.emptyStore());
    assert.throws(() => library.save(full, library.emptyStore()), /QuotaExceeded/);
});

test('老数据只有一份会话快照，升级后应当变成库里的一条', () => {
    const storage = fakeStorage();
    const legacy = {
        fingerprint: pkg().id,
        packageName: 'demo.zip', displayName: '演示',
        sceneId: 'animation:wave', packSceneIds: { idle_image: 'animation:idle' },
        adjustOverwrite: false,
    };
    const store = library.migrateFromLegacy(legacy, storage);
    assert.equal(store.entries.length, 1);
    assert.equal(store.currentKey, legacy.fingerprint);
    assert.equal(store.entries[0].session.sceneId, 'animation:wave');
    assert.equal(store.entries[0].session.adjustOverwrite, false);
    // 已经入库过就不该再来一次（否则每次开页都会重播迁移）
    library.save(storage, store);
    assert.equal(library.migrateFromLegacy(legacy, storage), null);
    assert.equal(library.migrateFromLegacy(null, storage), null);
});

test('副本占用讲成人话：KB/MB，不用 0 KB 糊弄', () => {
    assert.equal(library.formatBytes(0), '', '没副本就没字，别挂一个 0 KB');
    assert.equal(library.formatBytes(-5), '');
    assert.equal(library.formatBytes(NaN), '');
    assert.equal(library.formatBytes(3), '1 KB', '不到 1 KB 也算 1 KB，用户不需要字节');
    assert.equal(library.formatBytes(2048), '2 KB');
    assert.equal(library.formatBytes(512 * 1024), '512 KB');
    assert.equal(library.formatBytes(3 * 1024 * 1024), '3 MB');
    assert.equal(library.formatBytes(3.14 * 1024 * 1024), '3.1 MB', '百兆以下保留一位小数');
    assert.equal(library.formatBytes(1234 * 1024 * 1024), '1234 MB', '往上取整，别显示 1234.0');
});

test('卡片名按 用户改名 > 来源名 > 包名(去.zip) > 模板自报名 裁决', () => {
    const base = { key: 'k', packageName: 'pkg.zip' };
    assert.equal(library.entryLabel({ ...base, displayName: '演示', sourceName: 'YUI 道具风格' }),
        'YUI 道具风格', '来源名优先 —— 用户的心智是"从哪拿来的"');
    assert.equal(library.entryLabel({ ...base, alias: '我的份', sourceName: '上传的包' }), '我的份');
    // zip 导入没有来源名：用包名，而不是作者写在 meta 里的内部名
    //（同一作者的整套模型往往全叫一个名字，一排「YUI 道具风格版」没法认）
    assert.equal(library.entryLabel({ ...base, displayName: '演示' }), 'pkg');
    assert.equal(library.entryLabel({ ...base }), 'pkg');
    assert.equal(library.entryLabel({ key: 'k', displayName: '演示' }), '演示');
    assert.equal(library.entryLabel(null), '');
});

test('renameEntry 只动 alias，键与来源保持原样；改成空等于回到默认', () => {
    let store = library.emptyStore();
    store = library.upsert(store, library.makeEntry({
        key: 'k', sourceName: '原始包名', displayName: '演示',
    }));
    store = library.renameEntry(store, 'k', '我的份 ');
    assert.equal(store.entries[0].alias, '我的份');
    assert.equal(store.entries[0].sourceName, '原始包名');
    assert.equal(store.entries[0].key, 'k');
    assert.equal(library.entryLabel(store.entries[0]), '我的份');
    store = library.renameEntry(store, 'k', '');
    assert.equal(store.entries[0].alias, '');
    assert.equal(library.entryLabel(store.entries[0]), '原始包名');
    // 不存在的键：安静返回，别炸
    assert.equal(library.renameEntry(store, 'nope', 'x').entries.length, 1);
});

test('超过长度上限的来源名/改名被截断，不是报错', () => {
    const long = '长'.repeat(80);
    const entry = library.makeEntry({ key: 'k', sourceName: long, alias: long });
    assert.equal(entry.sourceName.length, library.MAX_LABEL_CHARS);
    assert.equal(entry.alias.length, library.MAX_LABEL_CHARS);
});
