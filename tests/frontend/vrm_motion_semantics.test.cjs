const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const fileRoot = path.resolve(__dirname, '..', '..');
const root = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
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
const registeredCardCount = core.actionCards.length;
const registeredRuleCount = core.pack.rules.length;
core.registerActionCards(manifest.assets);
assert.equal(core.actionCards.length, registeredCardCount, 'card registration must be idempotent');
assert.equal(core.pack.rules.length, registeredRuleCount, 'rule registration must be idempotent');
Object.values(semantics.common).forEach(function (common) {
    (common.negation || []).forEach(function (term) {
        assert.equal(term, term.trim(), 'negation terms must not contain edge whitespace');
    });
});

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
    ['盘着腿坐在地上', 'sit'],
    ['侧过身慢慢躺下休息', 'lie'],
    ['趴在桌边闭上眼睛睡着了', 'sleep'],
    ['双手合十认真地向你道歉', 'plead'],
    ['耳朵耷拉下来，低着头小声说对不起', 'sad'],
    ['靠在床头端正地坐好', 'sit'],
    ['侧过身子慢慢躺下', 'lie'],
    ['趴在桌边托着下巴休息', 'lie'],
    ['趴在桌边闭上眼睛睡着了', 'sleep'],
    ['不再趴着，撑起身子重新站好', 'recover']
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

const bracketed = '（轻轻点头）我这就坐下陪你。';
const bracketedStages = global.NekoMotionText.extractClosedStages(bracketed);
assert.equal(bracketedStages.length, 1);
const bracketPlan = core.analyze(
    bracketedStages[0].raw,
    { locale: 'zh-CN' }
);
const prosePlan = core.analyzeSpeech(bracketed, { locale: 'zh-CN' });
assert.equal(bracketPlan.plan[0].intent, 'nod');
assert.deepEqual(prosePlan.plan.map(function (item) { return item.intent; }), ['sit']);

const sequence = core.analyzeSpeech('我先坐起来，然后站好。', {
    locale: 'zh-CN'
});
assert.deepEqual(sequence.plan.map(function (item) { return item.intent; }), ['sit', 'recover']);

const recoveryByLocale = [
    ['zh-CN', '我这就站起来。', '好的。', '你先站起来'],
    ['zh-TW', '我這就站起來。', '好的。', '請站起來'],
    ['en', "I'm standing up now.", 'Okay.', 'Please stand up'],
    ['ja', '今立ち上がります。', 'はい。', '立ってください'],
    ['ko', '지금 일어날게요.', '네.', '일어나 주세요'],
    ['ru', 'Я сейчас встану.', 'Хорошо.', 'Встань, пожалуйста'],
    ['es', 'Ahora me levanto.', 'De acuerdo.', 'Levántate'],
    ['pt', 'Vou me levantar agora.', 'Está bem.', 'Levante-se']
];

recoveryByLocale.forEach(function ([locale, directText, acknowledgement, userText]) {
    assert.equal(
        core.analyzeSpeech(directText, { locale: locale }).plan[0].intent,
        'recover',
        locale + ' direct recovery'
    );
    assert.equal(
        core.analyzeSpeech(acknowledgement, {
            locale: locale,
            userText: userText
        }).plan[0].intent,
        'recover',
        locale + ' acknowledged recovery command'
    );
});

const acknowledgedPostures = [
    ['坐到我旁边吧', 'sit', null],
    ['侧身躺下休息吧', 'lie', 'side'],
    ['趴到桌边休息吧', 'lie', 'prone'],
    ['闭上眼睛睡一会儿吧', 'sleep', null],
    ['从地上起来站好吧', 'recover', null]
];
acknowledgedPostures.forEach(function ([userText, expectedIntent, expectedStyle]) {
    const result = core.analyzeSpeech('好的，我明白了。', {
        locale: 'zh-CN',
        userText: userText
    });
    assert.equal(result.plan[0].intent, expectedIntent, userText);
    if (expectedStyle) assert.equal(result.plan[0].style, expectedStyle, userText);
});

assert.deepEqual(core.analyzeSpeech('好的，但我这就站起来。', {
    locale: 'zh-CN', userText: '你坐下吧'
}).plan.map(function (item) { return item.intent; }), ['recover']);
assert.deepEqual(core.analyzeSpeech('好的，不过我这就坐下。', {
    locale: 'zh-CN', userText: '你站起来吧'
}).plan.map(function (item) { return item.intent; }), ['sit']);

assert.equal(core.analyzeSpeech('您先别急着起身。', {
    locale: 'zh-CN'
}).plan.length, 0);
assert.equal(core.analyzeSpeech('我帮你坐起来。', {
    locale: 'zh-CN'
}).plan.length, 0);

assert.notEqual(intent('Please wait while I check that.', { locale: 'en' }), 'plead');
assert.equal(core.analyze('if she waves goodbye', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyze('do not wave goodbye', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('The user claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('She nods.', { locale: 'en' }).plan.length, 0);

const boundedFrame = core.toChineseFrame('nod, shake head, wave, clap and dance', 'en');
assert.ok(
    boundedFrame.split('，').length <= semantics.contract.maxPlanItems,
    'normalization must honor maxPlanItems'
);
assert.equal(
    semantics.rules.find(function (rule) { return rule.id === 'overwhelm'; }).emotion,
    'fearful'
);

assert.deepEqual(
    global.NekoMotionText.extractClosedStages('（轻轻点头）好的').map(function (stage) { return stage.raw; }),
    ['轻轻点头']
);
assert.deepEqual(
    global.NekoMotionText.extractClosedStages('(轻轻摇头)好的').map(function (stage) { return stage.raw; }),
    ['轻轻摇头']
);
assert.deepEqual(global.NekoMotionText.extractClosedStages('（还没有说完'), []);

console.log('VRM motion semantics: OK (' + cases.length + ' realistic cases, 8 locales)');
