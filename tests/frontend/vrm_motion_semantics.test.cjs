const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.cwd();
global.window = global;
global.document = { documentElement: { lang: 'zh-CN' } };
global.navigator = { language: 'zh-CN' };

vm.runInThisContext(
    fs.readFileSync(path.join(root, 'static/vrm/motion/core.js'), 'utf8'),
    { filename: 'static/vrm/motion/core.js' }
);

const semantics = JSON.parse(fs.readFileSync(path.join(root, 'static/vrm/motion/semantics.json'), 'utf8'));
const manifest = JSON.parse(fs.readFileSync(path.join(root, 'static/vrm/motion/manifest.json'), 'utf8'));
const core = new global.NekoMotionCore(semantics).registerActionCards(manifest.assets);

function intent(text, options) {
    const result = core.analyze(text, Object.assign({ locale: 'zh-CN' }, options));
    return result.plan[0] && result.plan[0].intent || null;
}

const cases = [
    ['脑袋很小心地往下轻颤着点了一下，随即立刻抬起', 'nod'],
    ['立刻跟着小幅度摇头，动作比刚才点头更小心，生怕做错', 'shake'],
    ['红着脸轻轻把耳边的头发别到耳后', 'shy'],
    ['害羞地撩了一下头发', 'shy'],
    ['手掌按在胸口，像是被你感动了', 'like'],
    ['抬手搭在眉前向远处眺望', 'look'],
    ['立刻并拢双腿端正坐下，尾巴绷直贴在身后', 'sit'],
    ['侧过身慢慢躺下休息', 'lie'],
    ['趴在桌边闭上眼睛睡着了', 'sleep'],
    ['双手合十认真地向你道歉', 'plead'],
    ['耳朵耷拉下来，低着头小声说对不起', 'sad']
];

cases.forEach(function ([text, expected]) {
    assert.equal(intent(text), expected, text);
});

assert.notEqual(intent('立刻并拢双腿端正坐下，尾巴绷直贴在身后'), 'lie');
assert.notEqual(intent('立刻跟着小幅度摇头，动作比刚才点头更小心，生怕做错'), 'nod');
assert.notEqual(intent('双手合十认真地向你道歉'), 'point');
assert.notEqual(intent('耳朵耷拉下来，低着头小声说对不起'), 'sit');
assert.notEqual(intent('只是听着钢琴曲'), 'piano');
assert.notEqual(intent('停下舞步，只听着音乐'), 'dance');

assert.deepEqual(
    global.NekoMotionText.extractClosedStages('（轻轻点头）好的').map(function (stage) { return stage.raw; }),
    ['轻轻点头']
);
assert.deepEqual(
    global.NekoMotionText.extractClosedStages('(轻轻摇头)好的').map(function (stage) { return stage.raw; }),
    ['轻轻摇头']
);
assert.deepEqual(global.NekoMotionText.extractClosedStages('（还没有说完'), []);

console.log('VRM motion semantics: OK (' + cases.length + ' realistic cases)');
