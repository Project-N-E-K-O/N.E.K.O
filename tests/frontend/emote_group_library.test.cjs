const test = require('node:test');
const assert = require('node:assert/strict');

const groups = require('../../static/js/emote_group_library.js');

test('模板动作生成只读组，名称与数量分别保留', () => {
    const group = groups.importedAnimationGroup([{ id: 'animation:idle' }, { id: 'animation:wave' }, {}], '动作 2');
    assert.equal(group.name, '动作 2');
    assert.equal(group.count, 2);
    assert.equal(group.readOnly, true);
    assert.deepEqual(group.items, [
        { kind: 'scene', ref: 'animation:idle' }, { kind: 'scene', ref: 'animation:wave' },
    ]);
    assert.equal(group.id, 'list:spine:animations');
    assert.equal(groups.importedAnimationGroup([], '动作'), null);
});

test('只读组不许被改：加人、减人、改名都得原样退回', () => {
    const readOnly = { ...groups.makeGroup({ name: '导入的' }), readOnly: true };
    assert.equal(groups.addItem(readOnly, 'scene', 'x#0'), readOnly);
    assert.equal(groups.removeItem(readOnly, 'scene', 'x#0'), readOnly);
    const list = [readOnly];
    assert.equal(groups.renameGroup(list, readOnly.id, '乱改')[0].name, '导入的');
});

test('同一项不重复入组，顺序就是导出顺序', () => {
    let g = groups.makeGroup({ name: '通知类' });
    g = groups.addItem(g, 'scene', 'a#0');
    g = groups.addItem(g, 'scene', 'a#0');
    g = groups.addItem(g, 'shot', 'shot-1');
    assert.deepEqual(g.items.map((item) => item.ref), ['a#0', 'shot-1']);
    assert.equal(groups.hasItem(g, 'shot', 'shot-1'), true);
});

test('组名去重认大小写不敏感，且不会超长', () => {
    const list = [groups.makeGroup({ name: '通知类' })];
    assert.equal(groups.uniqueGroupName(list, '通知类'), '通知类 2');
    assert.equal(groups.uniqueGroupName(list, ' 通知类 '), '通知类 2');
    assert.equal(groups.uniqueGroupName(list, '新组'), '新组');
    const long = '长'.repeat(60);
    assert.ok(groups.uniqueGroupName([], long).length <= groups.MAX_GROUP_NAME);
});

test('文件名把不能出现的字符换成下划线，并保留中文', () => {
    assert.equal(groups.sanitizeFileName('001_通知/提示 Bits'), '001_通知_提示 Bits');
    assert.equal(groups.sanitizeFileName('a<b>:c*d?e|"f|g'), 'a_b_c_d_e_f_g');
    assert.equal(groups.sanitizeFileName('  ..  '), 'emote', '只剩点号要退回默认名，别导出 ".."');
    assert.equal(groups.sanitizeFileName('尾点.空格  '), '尾点.空格');
});

test('导出文件名带序号，重名不覆盖', () => {
    const planned = groups.planFileNames([
        { kind: 'scene', name: '生气', ext: 'gif' },
        { kind: 'scene', name: '生气', ext: 'gif' },
        { kind: 'shot', name: '到达:近景', ext: 'png' },
    ]);
    assert.deepEqual(planned.map((item) => item.file), [
        '01_生气.gif', '02_生气.gif', '03_到达_近景.png',
    ]);
});

test('导出计划会把来源丢失的取景单独报告，而不是静默少几张', () => {
    const shotOk = groups.makeShot({ id: 'shot-1', name: '近景', sourceSceneId: 'a#0' });
    const shotBroken = groups.makeShot({ id: 'shot-2', name: '没来源', sourceSceneId: '' });
    const group = groups.makeGroup({
        name: '一套',
        items: [
            { kind: 'scene', ref: 'a#0' },
            { kind: 'shot', ref: 'shot-1' },
            { kind: 'shot', ref: 'shot-2' },
            { kind: 'shot', ref: 'shot-不存在' },
        ],
    });
    const plan = groups.planGroupExport({ group, shots: [shotOk, shotBroken], ext: 'gif' });
    assert.equal(plan.items.length, 2, '能导的就两条');
    assert.deepEqual(plan.items.map((item) => item.file), ['01_a#0.gif', '02_近景.gif']);
    assert.equal(plan.skipped.length, 2);
    assert.deepEqual(plan.skipped.map((item) => item.reason).sort(), ['missing-shot', 'no-source']);
    assert.equal(plan.ready, true);
});

test('超过上限就明确截断，不能悄悄砍掉尾巴', () => {
    const many = Array.from({ length: 12 }, (u, i) => ({ kind: 'scene', ref: `s#${i}` }));
    const group = groups.makeGroup({ name: '大组', items: many });
    const plan = groups.planGroupExport({ group, shots: [], ext: 'gif', limit: 10 });
    assert.equal(plan.total, 12);
    assert.equal(plan.items.length, 10);
    assert.equal(plan.truncated, true, 'truncated 是界面要用的信号');
});

test('空组不导出，而不是弹一个空 zip', () => {
    const plan = groups.planGroupExport({ group: groups.makeGroup({ name: '空的' }), shots: [] });
    assert.equal(plan.ready, false);
    assert.equal(plan.items.length, 0);
});

test('读回存储时丢掉认不出的条目，导入组不会被当成自建组', () => {
    const parsed = groups.groupsFrom([
        groups.makeGroup({ name: '我的组' }),
        { id: 'list:whatever', name: '导入的' },
        null,
        'nope',
    ]);
    assert.equal(parsed.length, 1);
    assert.equal(parsed[0].name, '我的组');
    assert.equal(groups.shotsFrom([{ id: 's1' }, null]).length, 1);
    assert.equal(groups.shotsFrom([{ id: 's1' }])[0].look.transform.scale, 1);
});

test('删除组时只清掉没有别的组再用的取景', () => {
    const first = groups.addItem(
        groups.addItem(groups.makeGroup({ id: 'g1', name: '甲' }), 'shot', 'shot:drop'),
        'shot',
        'shot:shared',
    );
    const second = groups.addItem(groups.makeGroup({ id: 'g2', name: '乙' }), 'shot', 'shot:shared');
    const shots = [
        groups.makeShot({ id: 'shot:keep', sourceSceneId: 'a' }),
        groups.makeShot({ id: 'shot:drop', sourceSceneId: 'b' }),
        groups.makeShot({ id: 'shot:shared', sourceSceneId: 'c' }),
    ];
    const removed = groups.deleteGroup([first, second], shots, 'g1');
    assert.deepEqual(removed.groups.map((group) => group.id), ['g2']);
    assert.deepEqual(removed.shots.map((shot) => shot.id).sort(), ['shot:keep', 'shot:shared']);
    const locked = groups.deleteGroup(
        [{ id: 'list:x', name: '导入', readOnly: true, items: [] }],
        shots,
        'list:x',
    );
    assert.equal(locked.groups.length, 1);
});

test('组记下的数值会跟着组走，加成员不会把它丢掉', () => {
    const skins = [{ key: 'Hair', value: 'A' }];
    const made = groups.makeGroup({
        name: '构图',
        look: { skins, transform: { x: 8, y: 0, rotation: 35, scale: 1.2 } },
    });
    skins[0].value = '改了';
    const withScene = groups.addItem(made, 'scene', 'b#0');
    const roundTrip = groups.groupsFrom([withScene])[0];
    assert.equal(roundTrip.look.skins[0].value, 'A');
    assert.deepEqual(roundTrip.look.transform, { x: 8, y: 0, rotation: 35, scale: 1.2 });
    assert.equal(roundTrip.items.length, 1);
    assert.equal(groups.makeGroup({ name: '旧组' }).look, null);
});

test('取景快照保存的是当时的外观，不是共享引用', () => {
    const skins = [{ key: 'Hair', value: 'A' }];
    const shot = groups.makeShot({
        name: '近景', sourceSceneId: 'a#0',
        look: { skins, transform: { x: 3, y: -4, rotation: -12, scale: 1.2 } },
    });
    skins[0].value = '被后面改了';
    assert.equal(shot.look.skins[0].value, 'A', '存下来的取景必须冻结当时的换装，不然导出会和屏幕不一致');
    assert.deepEqual(shot.look.transform, { x: 3, y: -4, rotation: -12, scale: 1.2 },
        "取景得把调用方传的大小/角度/位移原样带走，不是只留 scale");
    assert.equal(shot.progress, 0);
});

test('场景成员用场景名当文件名，查不到才退回内部 id', () => {
    // 整组导出「模板自带」的那份列表时，成员只有 id 引用。文件名要是 id，
    // 用户拿到的是 191 个 `01_ModelA#0.gif`，没人认得出哪张是哪张。
    const group = groups.makeGroup({
        name: '一套',
        items: [{ kind: 'scene', ref: 'ModelA#0' }, { kind: 'scene', ref: 'ModelA#1' }],
    });
    const named = groups.planGroupExport({
        group,
        shots: [],
        ext: 'gif',
        nameOf: (ref) => (ref === 'ModelA#0' ? '通知/提示 Bits' : ''),
    });
    assert.deepEqual(named.items.map((item) => item.file), [
        '01_通知_提示 Bits.gif', '02_ModelA#1.gif',
    ]);
});

/* ── 导出引擎 ─────────────────────────────────────────────────────────────── */
function plan(items) {
    return { items: groups.planFileNames(items), skipped: [], total: items.length };
}

test('引擎按计划的顺序出文件，并把每一项交给 zip', async () => {
    const seen = [];
    const result = await groups.runGroupExport({
        plan: plan([
            { kind: 'scene', name: '生气', ext: 'gif' },
            { kind: 'shot', name: '近景', ext: 'gif' },
        ]),
        produce: async (item) => { seen.push(item.file); return new Uint8Array([1, 2, 3]); },
        zip: async (files) => files.map((f) => f.path).join('+'),
    });
    assert.deepEqual(seen, ['01_生气.gif', '02_近景.gif']);
    assert.equal(result.blob, '01_生气.gif+02_近景.gif');
    assert.equal(result.failed.length, 0);
    assert.equal(result.aborted, false);
});

test('一张烘不出来不能拖垮整批，但要单独报出来', async () => {
    const result = await groups.runGroupExport({
        plan: plan([
            { kind: 'scene', name: '好的', ext: 'gif' },
            { kind: 'scene', name: '坏的', ext: 'gif' },
            { kind: 'shot', name: '空的', ext: 'gif' },
        ]),
        produce: async (item) => {
            if (item.name === '坏的' || item.file.includes('坏的')) throw new Error('渲染上下文丢了');
            if (item.file.includes('空的')) return new Uint8Array(0);
            return new Uint8Array([9]);
        },
        zip: async (files) => files,
    });
    assert.equal(result.files.length, 1, '只有一张成功的该进 zip');
    assert.deepEqual(result.failed.map((f) => f.reason), ['渲染上下文丢了', 'empty']);
    assert.equal(result.done, 3, '进度要数完三条，界面才知道"跑完了"');
});

test('全部失败时不产 zip（免得下载一个空包）', async () => {
    let zipped = 0;
    const result = await groups.runGroupExport({
        plan: plan([{ kind: 'scene', name: '坏', ext: 'gif' }]),
        produce: async () => { throw new Error('炸了'); },
        zip: async () => { zipped += 1; return 'x'; },
    });
    assert.equal(zipped, 0);
    assert.equal(result.blob, undefined);
    assert.equal(result.failed.length, 1);
});

test('中断时停下并回报已经做到第几张', async () => {
    let abort = false;
    const touched = [];
    const result = await groups.runGroupExport({
        plan: plan(Array.from({ length: 6 }, (u, i) => ({ kind: 'scene', name: `s${i}`, ext: 'png' }))),
        produce: async (item) => {
            touched.push(item.file);
            if (touched.length === 2) abort = true;
            return new Uint8Array([1]);
        },
        zip: async () => 'should-not-run',
        shouldAbort: () => abort,
    });
    assert.equal(result.aborted, true);
    assert.equal(touched.length, 2, '点了停止还继续烘下去是最糟的体验');
    assert.equal(result.blob, undefined);
    assert.equal(result.done, 2);
    assert.equal(result.total, 6);
});

test('进度回调每项一次，带文件名（界面要说在导哪张）', async () => {
    const marks = [];
    await groups.runGroupExport({
        plan: plan([{ kind: 'scene', name: 'a', ext: 'gif' }, { kind: 'scene', name: 'b', ext: 'gif' }]),
        produce: async () => new Uint8Array([1]),
        zip: async () => 'z',
        onProgress: (mark) => marks.push(`${mark.done}/${mark.total}:${mark.file}`),
    });
    assert.deepEqual(marks, ['1/2:01_a.gif', '2/2:02_b.gif']);
});

test('stop during the final item prevents memory ZIP packaging', async () => {
    let abort = false;
    let zipped = false;
    const result = await groups.runGroupExport({
        plan: plan([{kind:'scene',name:'last',ext:'png'}]),
        produce: async () => { abort = true; return new Uint8Array([1]); },
        zip: async () => { zipped = true; return 'unexpected'; },
        shouldAbort: () => abort,
    });
    assert.equal(result.aborted, true);
    assert.equal(result.done, 1);
    assert.equal(zipped, false);
});

test('缺少注入就立刻报错，不要跑到一半才崩', async () => {
    await assert.rejects(() => groups.runGroupExport({ plan: plan([]), zip: async () => 'x' }), /produce/);
    await assert.rejects(() => groups.runGroupExport({ plan: plan([]), produce: async () => new Uint8Array([1]) }), /zip/);
});

test('写进目录时不把字节留到最后，压缩只拿到文件名', async () => {
    const stored = [];
    let zipped = null;
    let memoryZip = 0;
    const result = await groups.runGroupExport({
        plan: plan([
            { kind: 'scene', name: '甲', ext: 'gif' },
            { kind: 'scene', name: '乙', ext: 'gif' },
        ]),
        produce: async () => new Uint8Array([4, 5]),
        store: async (file) => {
            stored.push({ path: file.path, length: file.data.length });
        },
        zipStored: async (names) => { zipped = names; return null; },
        zip: async () => { memoryZip += 1; return 'nope'; },
    });
    assert.deepEqual(stored.map((file) => file.path), ['01_甲.gif', '02_乙.gif']);
    assert.deepEqual(stored.map((file) => file.length), [2, 2]);
    assert.equal(result.files.length, 0, '字节不该再留在内存清单里');
    assert.deepEqual(zipped, ['01_甲.gif', '02_乙.gif']);
    assert.equal(memoryZip, 0);
    assert.equal(result.savedToFolder, true);
    assert.equal(result.blob, null);
});

test('写到一半停下时，已经落盘的那些仍然打包', async () => {
    let abort = false;
    const stored = [];
    let zipped = null;
    const result = await groups.runGroupExport({
        plan: plan(Array.from({ length: 4 }, (unused, index) => ({ kind: 'scene', name: `s${index}`, ext: 'png' }))),
        produce: async (item) => {
            if (stored.length === 1) abort = true;
            return new Uint8Array([item.name.length]);
        },
        store: async (file) => { stored.push(file.path); },
        zipStored: async (names) => { zipped = names; },
        shouldAbort: () => abort,
    });
    assert.equal(result.aborted, true);
    assert.deepEqual(stored, ['01_s0.png', '02_s1.png']);
    assert.deepEqual(zipped, stored);
    assert.equal(result.done, 2);
});
