const test = require('node:test');
const assert = require('node:assert/strict');

const graph = require('../../static/js/emote_node_graph.js');

function node(type, params, id) {
    return graph.makeNode(type, 0, 0, params, id || type);
}

test('default graph is one 默认节点 feeding one idle output', () => {
    const initial = graph.defaultGraph();
    assert.deepEqual(initial.nodes.map((n) => n.type), ['source.default', 'output.state']);
    assert.equal(initial.links.length, 1);
    const evaluated = graph.evaluate(initial, { sceneId: 'a.json#0', skins: [], slots: [], transform: { x: 0, y: 0, scale: 1, mirror: false } });
    assert.equal(evaluated.outputs.length, 1);
    assert.equal(evaluated.outputs[0].stateKey, 'idle_image');
    assert.equal(evaluated.outputs[0].look.sceneId, 'a.json#0', '默认节点继承编辑器当前状态');
});

test('unknown node types are dropped and dangling links go with them', () => {
    const dirty = {
        schemaVersion: 1,
        nodes: [
            { id: 'ok', type: 'source.default' },
            { id: 'gone', type: 'not.a.real.node' },
            { id: 'out', type: 'output.state', params: { stateKey: 'happy_image' } },
        ],
        links: [
            { from: 'ok', to: 'gone' },
            { from: 'gone', to: 'out' },
        ],
    };
    const clean = graph.normalizeGraph(dirty);
    assert.deepEqual(clean.nodes.map((n) => n.id), ['ok', 'out']);
    assert.equal(clean.links.length, 0, '经过被丢弃节点的连线必须一起消失');
    assert.equal(clean.dropped.length, 1);
});

test('garbage in, empty graph out — never a throw', () => {
    for (const junk of [null, undefined, {}, { nodes: [] }, 'string', 42, { nodes: {}, links: {} }]) {
        const result = graph.normalizeGraph(junk);
        assert.deepEqual(result.nodes, []);
        assert.deepEqual(result.links, []);
    }
});

test('socket typing is enforced in both directions', () => {
    const source = node('source.default', null, 's');
    const mod = node('mod.skin', {}, 'm');
    const out = node('output.state', null, 'o');
    assert.equal(graph.canConnect(source, 'look', mod, 'look'), true);
    assert.equal(graph.canConnect(mod, 'look', out, 'look'), true);
    // outputs take nothing, sources produce nothing
    assert.equal(graph.canConnect(out, 'look', mod, 'look'), false);
    assert.equal(graph.canConnect(mod, 'look', source, 'look'), false);
    assert.equal(graph.canConnect(source, 'look', mod, 'nope'), false);
    assert.equal(graph.canConnect(source, 'look', source, 'look'), false, '不能连到自己');
});

test('a second link into the same input replaces the first', () => {
    let g = graph.emptyGraph();
    g.nodes.push(node('source.default', null, 'a'));
    g.nodes.push(node('source.scene', { sceneId: 'x#1' }, 'b'));
    g.nodes.push(node('output.state', null, 'o'));
    g = graph.addLink(g, 'a', 'look', 'o', 'look');
    assert.equal(g.links.length, 1);
    g = graph.addLink(g, 'b', 'look', 'o', 'look');
    assert.equal(g.links.length, 1);
    assert.equal(g.links[0].from, 'b');
});

test('a link that would create a cycle is rejected, leaving the graph untouched', () => {
    let g = graph.defaultGraph();
    g.nodes.push(node('mod.skin', {}, 't'));
    g = graph.addLink(g, 'default', 'look', 't', 'look');
    g = graph.addLink(g, 't', 'look', 'out-idle', 'look');
    const before = JSON.stringify(g);
    const cyclic = graph.addLink(g, 'out-idle', 'look', 't', 'look');
    assert.equal(JSON.stringify(cyclic), before, 'output 节点没有输出端口，本就接不上');
    // 真正的环：mod -> mod 自身回路
    let g2 = graph.emptyGraph();
    g2.nodes.push(node('mod.skin', {}, 'm1'));
    g2.nodes.push(node('mod.transform', {}, 'm2'));
    g2 = graph.addLink(g2, 'm1', 'look', 'm2', 'look');
    const snapshot = JSON.stringify(g2);
    const result = graph.addLink(g2, 'm2', 'look', 'm1', 'look');
    assert.equal(JSON.stringify(result), snapshot, '成环的连线必须被拒绝而不是留下坏图');
});

test('evaluation folds mods in topological order', () => {
    let g = graph.emptyGraph();
    g.nodes.push(node('source.default', null, 'src'));
    g.nodes.push(node('mod.transform', { x: 10, y: 0, scale: 2, mirror: true }, 'tf'));
    g.nodes.push(node('mod.skin', { groups: [{ group: 'eyes', value: '2' }] }, 'skin'));
    g.nodes.push(node('output.state', { stateKey: 'happy_image', seconds: 1.5 }, 'out'));
    g = graph.addLink(g, 'src', 'look', 'tf', 'look');
    g = graph.addLink(g, 'tf', 'look', 'skin', 'look');
    g = graph.addLink(g, 'skin', 'look', 'out', 'look');
    const defaults = {
        sceneId: 'a.json#0',
        skins: [{ group: 'hair', value: '1' }],
        slots: [],
        transform: { x: 5, y: 0, scale: 3, mirror: false },
    };
    const evaluated = graph.evaluate(g, defaults);
    assert.equal(evaluated.cycle, false);
    assert.equal(evaluated.outputs.length, 1);
    const look = evaluated.outputs[0].look;
    assert.equal(evaluated.outputs[0].stateKey, 'happy_image');
    assert.equal(evaluated.outputs[0].seconds, 1.5);
    assert.equal(look.transform.x, 15, '位移是叠加的 (5 + 10)');
    assert.equal(look.transform.scale, 6, '缩放是相乘的 (3 * 2)');
    assert.equal(look.transform.mirror, true);
    assert.deepEqual(look.skins, [{ group: 'hair', value: '1' }, { group: 'eyes', value: '2' }]);
});

test('a mod node with no input still starts from the defaults base', () => {
    let g = graph.emptyGraph();
    g.nodes.push(node('mod.transform', { x: 12 }, 'm'));
    g.nodes.push(node('output.state', { stateKey: 'talking_image' }, 'out'));
    g = graph.addLink(g, 'm', 'look', 'out', 'look');
    const evaluated = graph.evaluate(g, {
        sceneId: 'keep.json#2', skins: [], slots: [], transform: { x: 0, y: 0, scale: 1, mirror: false },
    });
    assert.equal(evaluated.outputs[0].look.sceneId, 'keep.json#2');
    assert.equal(evaluated.outputs[0].look.transform.x, 12);
});

test('scene source overrides the inherited scene', () => {
    let g = graph.emptyGraph();
    g.nodes.push(node('source.scene', { sceneId: 'b.json#7' }, 's'));
    g.nodes.push(node('output.state', { stateKey: 'click_image' }, 'out'));
    g = graph.addLink(g, 's', 'look', 'out', 'look');
    const evaluated = graph.evaluate(g, {
        sceneId: 'a.json#0', skins: [], slots: [], transform: { x: 0, y: 0, scale: 1, mirror: false },
    });
    assert.equal(evaluated.outputs[0].look.sceneId, 'b.json#7');
});

test('retired slider and text nodes keep the saved animation connected to its state', () => {
    const saved = graph.normalizeGraph({
        schemaVersion: 1,
        nodes: [
            { id: 'scene', type: 'source.scene', params: { sceneId: 'animation:idle' } },
            { id: 'slider', type: 'mod.slider', params: { name: 'height', value: 1.5 } },
            { id: 'text', type: 'mod.text', params: { content: 'Previous caption' } },
            { id: 'idle', type: 'output.state', params: { stateKey: 'idle_image' } },
        ],
        links: [
            { from: 'scene', to: 'slider' },
            { from: 'slider', to: 'text' },
            { from: 'text', to: 'idle' },
        ],
    });
    const { outputs } = graph.evaluate(saved, { sceneId: 'animation:other' });
    const plan = graph.planOutputs(saved, outputs, ['idle_image'], ['idle_image']);
    assert.equal(plan.kept.length, 1);
    assert.equal(plan.kept[0].look.sceneId, 'animation:idle');
});

test('slot tint only touches slots whose name carries the tag', () => {
    let g = graph.emptyGraph();
    g.nodes.push(node('mod.slotTint', { tag: 'Hair', color: '#112233', secondary: '#000000' }, 'm'));
    g.nodes.push(node('output.state', { stateKey: 'sad_image' }, 'out'));
    g = graph.addLink(g, 'm', 'look', 'out', 'look');
    const evaluated = graph.evaluate(g, {
        sceneId: 'a#0',
        skins: [],
        transform: { x: 0, y: 0, scale: 1, mirror: false },
        slots: [
            { name: 'Hair_Front', tintColor: { r: 1, g: 1, b: 1, a: 1 } },
            { name: 'Eyes', tintColor: { r: 1, g: 1, b: 1, a: 1 } },
        ],
    });
    const slots = evaluated.outputs[0].look.slots;
    assert.equal(slots[0].tintColor.r, 17 / 255);
    assert.equal(slots[1].tintColor.r, 1, '没带标签的槽位不该被改');
});

test('params are coerced, clamped and never trusted', () => {
    const bad = {
        schemaVersion: 1,
        nodes: [
            { id: 'n', type: 'mod.transform', params: { x: 99999, scale: -4, mirror: 'yes' } },
            { id: 'c', type: 'mod.slotTint', params: { tag: 42, color: 'red' } },
        ],
        links: [],
    };
    const clean = graph.normalizeGraph(bad);
    const tf = clean.nodes[0].params;
    assert.equal(tf.x, 500);
    assert.equal(tf.scale, 0.2);
    assert.equal(tf.mirror, false);
    assert.equal(clean.nodes[1].params.tag, '');
    assert.equal(clean.nodes[1].params.color, '#ffffff');
});

test('duplicate node ids are made unique instead of confusing the evaluator', () => {
    const dupes = graph.normalizeGraph({
        schemaVersion: 1,
        nodes: [
            { id: 'same', type: 'source.default' },
            { id: 'same', type: 'output.state' },
        ],
        links: [],
    });
    assert.equal(new Set(dupes.nodes.map((n) => n.id)).size, 2);
});

test('cycle detection reports rather than hanging', () => {
    const cyclic = graph.normalizeGraph({
        schemaVersion: 1,
        nodes: [
            { id: 'm1', type: 'mod.text', params: { content: 'a' } },
            { id: 'm2', type: 'mod.text', params: { content: 'b' } },
        ],
        links: [
            { from: 'm1', fromSocket: 'look', to: 'm2', toSocket: 'look' },
            { from: 'm2', fromSocket: 'look', to: 'm1', toSocket: 'look' },
        ],
    });
    const evaluated = graph.evaluate(cyclic, {});
    assert.equal(evaluated.cycle, true);
    assert.deepEqual(evaluated.outputs, [], '有环时不产出，而不是产出一半');
});

test('removeNode takes its links with it', () => {
    let g = graph.defaultGraph();
    const trimmed = graph.removeNode(g, 'default');
    assert.equal(trimmed.nodes.length, 1);
    assert.equal(trimmed.links.length, 0);
});

test('every supported node round-trips through normalize', () => {
    for (const type of Object.keys(graph.TYPES)) {
        const made = graph.makeNode(type, 12, 34, {}, `id-${type}`);
        assert.ok(made, type);
        const round = graph.normalizeGraph({ nodes: [made], links: [] });
        assert.equal(round.nodes.length, 1, type);
        assert.equal(round.nodes[0].type, type);
        assert.equal(round.nodes[0].x, 12, type);
        assert.equal(round.nodes[0].y, 34, type);
    }
});

// ── 装配计划：这几条规则各自对应一次真实故障 ────────────────────────────────
const KEYS = ['idle_image', 'talking_image', 'happy_image'];

function planOf(nodes, links, outputs, normalize = true) {
    const g = { schemaVersion: 1, nodes, links };
    return graph.planOutputs(normalize ? graph.normalizeGraph(g) : g, outputs, KEYS, ['idle_image']);
}

function outList(pairs) {
    return pairs.map(([nodeId, stateKey]) => ({
        nodeId, stateKey, look: { sceneId: 'scene-a' },
        startAt: 0, seconds: 0, scale: 1, rotation: 0,
    }));
}

test('只是点过去的格子（跟随默认外观）不会进包', () => {
    const plan = planOf([
        node('source.default', {}, 'root'),
        node('output.state', { stateKey: 'idle_image' }, 'o-idle'),
        node('output.state', { stateKey: 'happy_image' }, 'o-happy'),
    ], [
        { from: 'root', to: 'o-idle', fromSocket: 'look', toSocket: 'look' },
        { from: 'root', to: 'o-happy', fromSocket: 'look', toSocket: 'look' },
    ], outList([['o-idle', 'idle_image'], ['o-happy', 'happy_image']]));
    // 必需槽（待机）跟随默认外观是合法的；其余没选动作就不该产出
    assert.deepEqual(plan.kept.map((o) => o.stateKey), ['idle_image']);
    assert.deepEqual(plan.dropped.map((d) => d.reason), ['unassigned']);
});

test('绑用户图片的格子算"已设置"，空图片 id 不算', () => {
    // 复现 2026-10-18 故障：图片槽在规划层被当"未选动作"丢弃，导出里直接没有
    const plan = planOf([
        node('source.default', {}, 'root'),
        node('source.image', { imageId: 'img-x' }, 'img'),
        node('output.state', { stateKey: 'happy_image' }, 'o-img'),
        node('output.state', { stateKey: 'talking_image' }, 'o-bad'),
    ], [
        { from: 'img', to: 'o-img', fromSocket: 'look', toSocket: 'look' },
        { from: 'root', to: 'o-bad', fromSocket: 'look', toSocket: 'look' },
    ], outList([['o-img', 'happy_image'], ['o-bad', 'talking_image']]));
    assert.deepEqual(plan.kept.map((o) => o.nodeId), ['o-img']);
    assert.deepEqual(plan.dropped.map((d) => d.reason), ['unassigned']);
});

test('图片轮播：单值旧图自动升级成单元素列表，列表去重去空', () => {
    const legacy = graph.makeNode('source.image', 0, 0, { imageId: 'img-a' });
    assert.deepEqual(legacy.params.imageIds, ['img-a']);
    assert.equal(legacy.params.imageId, 'img-a');
    const multi = graph.makeNode('source.image', 0, 0, {
        imageIds: ['img-a', ' img-b ', '', 'img-a'],
    });
    assert.deepEqual(multi.params.imageIds, ['img-a', 'img-b']);
    assert.equal(multi.params.imageId, 'img-a');
});

test('轮播列表随 evaluate 带到输出，规划照常放行', () => {
    const g = graph.normalizeGraph({
        schemaVersion: 1,
        nodes: [
            graph.makeNode('source.image', 0, 0, { imageIds: ['img-a', 'img-b'] }, 'img'),
            graph.makeNode('output.state', 0, 0, { stateKey: 'happy_image' }, 'o'),
        ],
        links: [{ from: 'img', to: 'o', fromSocket: 'look', toSocket: 'look' }],
    });
    const evaluated = graph.evaluate(g, {});
    assert.deepEqual(evaluated.outputs[0].imageIds, ['img-a', 'img-b']);
    assert.equal(evaluated.outputs[0].imageId, 'img-a');
    const plan = graph.planOutputs(
        g, evaluated.outputs, ['idle_image', 'talking_image', 'happy_image'], ['idle_image']);
    assert.deepEqual(plan.kept.map((o) => o.stateKey), ['happy_image']);
});

test('接了动画的输出留下，中间夹了加工节点也认得', () => {
    const g = {
        schemaVersion: 1,
        nodes: [
            node('source.scene', { sceneId: 'scene-a' }, 'sc'),
            node('mod.skin', {}, 'mod'),
            node('output.state', { stateKey: 'happy_image' }, 'o-happy'),
        ],
        links: [
            { from: 'sc', to: 'mod', fromSocket: 'look', toSocket: 'look' },
            { from: 'mod', to: 'o-happy', fromSocket: 'look', toSocket: 'look' },
        ],
    };
    const plan = graph.planOutputs(g, outList([['o-happy', 'happy_image']]), KEYS, ['idle_image']);
    assert.equal(plan.kept.length, 1, '中间有皮肤节点不该被当成"没设置"');
    assert.equal(graph.findSource(g, 'o-happy', 'source').params.sceneId, 'scene-a');
});

test('同一槽位重复只留一条，未知槽位丢掉', () => {
    // 分层装配遇到重复键直接抛错，平铺遇到未知键也抛 —— 装配前就得拦掉
    const plan = planOf([
        node('source.scene', { sceneId: 'scene-a' }, 'sc'),
        node('output.state', { stateKey: 'happy_image' }, 'a'),
        node('output.state', { stateKey: 'happy_image' }, 'b'),
        node('output.state', { stateKey: 'nope_image' }, 'c'),
    ], [
        { from: 'sc', to: 'a', fromSocket: 'look', toSocket: 'look' },
        { from: 'sc', to: 'b', fromSocket: 'look', toSocket: 'look' },
        { from: 'sc', to: 'c', fromSocket: 'look', toSocket: 'look' },
    ], outList([['a', 'happy_image'], ['b', 'happy_image'], ['c', 'nope_image']]), false);
    assert.deepEqual(plan.kept.map((o) => o.nodeId), ['a']);
    assert.deepEqual(plan.dropped.map((d) => `${d.output.nodeId}:${d.reason}`),
        ['b:duplicate', 'c:unknown-state']);
});

test('上游成环时查找会停住而不是转圈', () => {
    const g = {
        schemaVersion: 1,
        nodes: [node('mod.skin', {}, 'a'), node('mod.transform', {}, 'b')],
        links: [
            { from: 'a', to: 'b', fromSocket: 'look', toSocket: 'look' },
            { from: 'b', to: 'a', fromSocket: 'look', toSocket: 'look' },
        ],
    };
    assert.equal(graph.findSource(g, 'a', 'source'), null);
});

const DEFAULT_LOOK = {
    sceneId: 'scene-a', skins: [], slots: [], texts: [],
    transform: { x: 0, y: 0, scale: 1, mirror: false },
};

test('重复槽位收成一条时保留"选过动作"的那条', () => {
    // 复现真实故障：界面上报"跟随默认外观"，烘包却用了另一条动画
    const dirty = {
        schemaVersion: 1,
        nodes: [
            node('source.default', {}, 'root'),
            node('source.scene', { sceneId: 'scene-a' }, 'sc'),
            node('output.state', { stateKey: 'happy_image' }, 'happy-default'),
            node('output.state', { stateKey: 'happy_image' }, 'happy-scene'),
        ],
        links: [
            { from: 'root', to: 'happy-default', fromSocket: 'look', toSocket: 'look' },
            { from: 'sc', to: 'happy-scene', fromSocket: 'look', toSocket: 'look' },
        ],
    };
    const clean = graph.normalizeGraph(dirty);
    assert.deepEqual(clean.nodes.map((n) => n.id).filter((id) => id.startsWith('happy')), ['happy-scene'],
        '用户挑过的动作不能被一条点选时顺手生成的默认外观节点盖掉');
    assert.deepEqual(clean.links.map((l) => `${l.from}>${l.to}`), ['sc>happy-scene']);
    const evaluated = graph.evaluate(clean, DEFAULT_LOOK);
    assert.equal(evaluated.outputs.length, 1, '同一槽位只剩一条输出，界面和装配不可能再分叉');
    assert.equal(evaluated.outputs[0].look.sceneId, 'scene-a');
});

test('两条都没接动作时保留第一条并去掉悬空线', () => {
    const dirty = {
        schemaVersion: 1,
        nodes: [
            node('source.default', {}, 'root'),
            node('output.state', { stateKey: 'sad_image' }, 'sad-a'),
            node('output.state', { stateKey: 'sad_image' }, 'sad-b'),
        ],
        links: [
            { from: 'root', to: 'sad-a', fromSocket: 'look', toSocket: 'look' },
            { from: 'root', to: 'sad-b', fromSocket: 'look', toSocket: 'look' },
        ],
    };
    const clean = graph.normalizeGraph(dirty);
    assert.deepEqual(clean.nodes.map((n) => n.id), ['root', 'sad-a']);
    assert.deepEqual(clean.links.map((l) => `${l.from}>${l.to}`), ['root>sad-a']);
});

test('source.image 整格替换为用户图片：输出带 imageId、不报 no-scene', () => {
    const g = graph.emptyGraph();
    g.nodes.push(
        node('source.image', { imageId: 'smile.png' }, 'img'),
        node('output.state', { stateKey: 'happy_image', seconds: 1.5 }, 'out'),
    );
    g.links.push({ from: 'img', fromSocket: graph.LOOK, to: 'out', toSocket: graph.LOOK });
    const evaluated = graph.evaluate(g, { sceneId: 'a.json#0', skins: [], slots: [], transform: { x: 0, y: 0, scale: 1, mirror: false } });
    assert.equal(evaluated.outputs.length, 1);
    const output = evaluated.outputs[0];
    assert.equal(output.imageId, 'smile.png', '图片 id 要跟到输出上');
    assert.equal(output.look.imageId, 'smile.png');
    assert.equal(output.look.sceneId, '', '图片状态没有场景');
    assert.equal(output.seconds, 1.5);
    assert.deepEqual(evaluated.warnings, [], '图片状态不缺场景，别报 no-scene');
});

test('source.image 没填 imageId 时回落到上游默认，按缺场景如实报告', () => {
    const g = graph.emptyGraph();
    g.nodes.push(
        node('source.image', { imageId: '' }, 'img'),
        node('output.state', { stateKey: 'idle_image' }, 'out'),
    );
    g.links.push({ from: 'img', fromSocket: graph.LOOK, to: 'out', toSocket: graph.LOOK });
    const evaluated = graph.evaluate(g, { sceneId: '', skins: [], slots: [], transform: { x: 0, y: 0, scale: 1, mirror: false } });
    assert.equal(evaluated.outputs[0].imageId, '');
    assert.ok(evaluated.warnings.some((w) => w.startsWith('no-scene:')),
        '什么内容都没接时该如实报缺场景');
});

test('imageId 会随输出状态进 normalizeGraph 往返（存档往返不丢）', () => {
    const g = graph.emptyGraph();
    g.nodes.push(
        node('source.image', { imageId: 'wink.png' }, 'img'),
        node('output.state', { stateKey: 'sad_image', seconds: 2 }, 'out'),
    );
    g.links.push({ from: 'img', fromSocket: graph.LOOK, to: 'out', toSocket: graph.LOOK });
    const restored = graph.normalizeGraph(g);
    assert.equal(restored.nodes.length, 2);
    const img = restored.nodes.find((n) => n.type === 'source.image');
    assert.equal(img.params.imageId, 'wink.png');
    const evaluated = graph.evaluate(restored, { sceneId: '', skins: [], slots: [], transform: { x: 0, y: 0, scale: 1, mirror: false } });
    assert.equal(evaluated.outputs[0].imageId, 'wink.png');
});
