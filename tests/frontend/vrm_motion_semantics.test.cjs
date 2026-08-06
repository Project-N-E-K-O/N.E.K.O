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
const requiredLocales = ['zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'ru', 'es', 'pt'];
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
assert.equal(core.analyze('when she waves goodbye', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyze('whenever she waves goodbye', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyze('I will wave if she claps', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyze('wave when she claps', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('I will wave if she claps', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('wave when she claps', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyze('do not wave goodbye', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('The user claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('She nods.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('A player claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('This user claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('Someone nods.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('My friend claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('A young girl claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('A young girl in a red dress claps.', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('My close friend from school claps.', { locale: 'en' }).plan.length, 0);
['I make her clap.', 'I help him wave goodbye.', 'I ask them to nod.'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('Okay.', {
    locale: 'en', userText: 'a young girl clap'
}).plan.length, 0);
assert.equal(core.analyzeSpeech('Okay.', {
    locale: 'en', userText: 'the user clap'
}).plan.length, 0);
assert.equal(core.analyzeSpeech('Okay.', {
    locale: 'en', userText: 'you clap'
}).plan[0].intent, 'clap');
['make her clap', 'make him clap', 'make them clap'].forEach(function (userText) {
    assert.equal(core.analyzeSpeech('Okay.', {
        locale: 'en', userText: userText
    }).plan.length, 0, userText);
});
['ask my close friend from school to clap', 'tell the person in the back to clap'].forEach(function (userText) {
    assert.equal(core.analyzeSpeech('Okay.', {
        locale: 'en', userText: userText
    }).plan.length, 0, userText);
});
['让她鼓掌', '叫他鼓掌', '让她开心地鼓掌', '请他们轻轻挥手'].forEach(function (userText) {
    assert.equal(core.analyzeSpeech('好的', {
        locale: 'zh-CN', userText: userText
    }).plan.length, 0, userText);
});
['do not want to clap', 'not going to wave'].forEach(function (userText) {
    assert.equal(core.analyzeSpeech('Okay.', {
        locale: 'en', userText: userText
    }).plan.length, 0, userText);
});
assert.equal(core.analyzeSpeech("I can't clap", { locale: 'en' }).plan.some(function (item) {
    return item.intent === 'clap';
}), false);
assert.equal(core.analyzeSpeech("I won't wave goodbye", { locale: 'en' }).plan.some(function (item) {
    return item.intent === 'wave';
}), false);
['do not want to clap', 'I am not going to wave'].forEach(function (text) {
    assert.equal(core.analyze(text, { locale: 'en' }).plan.length, 0, text);
});
['no clapping', 'no waving', 'without clapping', 'not clapping', 'stop clapping', 'stop waving']
    .forEach(function (text) {
        assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
    });

assert.equal(core.analyze('不点头', { locale: 'zh-CN' }).plan.length, 0);
assert.equal(core.analyzeSpeech('好的', {
    locale: 'zh-CN', userText: '不能摇头'
}).plan.length, 0);
assert.deepEqual(core.analyze('not clap but wave', { locale: 'en' }).plan.map(function (item) {
    return item.intent;
}), ['wave']);
['挥手', '我挥手', '鼓掌', '我鼓掌', '点头', '摇头'].forEach(function (text) {
    assert.ok(core.analyzeSpeech(text, { locale: 'zh-CN' }).plan.length, text);
});
assert.equal(core.analyze('不过我点头', { locale: 'zh-CN' }).plan[0].intent, 'nod');
assert.equal(core.analyze('不由得点头', { locale: 'zh-CN' }).plan[0].intent, 'nod');
['没点头', '没摇头'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'zh-CN' }).plan.length, 0, text);
});
['沒點頭', '沒搖頭'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'zh-TW' }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('没准我点头', { locale: 'zh-CN' }).plan.length, 0);
assert.equal(core.analyzeSpeech('沉没之后我点头', { locale: 'zh-CN' }).plan[0].intent, 'nod');
assert.equal(core.analyze(
    '不想在今天这个非常非常漫长的直播环节最后点头',
    { locale: 'zh-CN' }
).plan.length, 0);

assert.equal(core.analyzeSpeech('（生怕会点头）', { locale: 'zh-CN' }).plan.length, 0);
assert.equal(core.analyzeSpeech('（怕会点头）', { locale: 'zh-CN' }).plan.length, 0);

[
    ['I wave', 'wave'],
    ['I wave goodbye', 'wave'],
    ['I clap', 'clap'],
    ['I nod', 'nod'],
    ['I play piano', 'piano']
].forEach(function ([text, expected]) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan[0].intent, expected, text);
});

[
    ['waving goodbye', 'wave'],
    ['clapping', 'clap'],
    ['nodding', 'nod'],
    ['playing piano', 'piano']
].forEach(function ([text, expected]) {
    assert.equal(core.analyze(text, { locale: 'en' }).plan[0].intent, expected, text);
    assert.equal(core.analyzeSpeech('I am ' + text, { locale: 'en' }).plan[0].intent, expected, text);
});
['うなずかない', 'うなずきません'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'ja' }).plan.length, 0, text);
});
['Should I clap?', 'Can I wave?', 'May I play piano?'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
});
['Can I wave? Let me know.', 'Should I clap'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
});
[
    ['我可以挥手吗？告诉我。', 'zh-CN'],
    ['我可以揮手嗎？告訴我。', 'zh-TW'],
    ['拍手してもいいですか？教えてください。', 'ja']
].forEach(function ([text, locale]) {
    assert.equal(core.analyzeSpeech(text, { locale: locale }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('我可以挥手了。告诉你一声。', {
    locale: 'zh-CN'
}).plan[0].intent, 'wave');
['I shake my head', 'shake my head', 'shaking my head'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan[0].intent, 'shake', text);
});

assert.equal(core.analyzeSpeech('Okay.', {
    locale: 'zh-CN', userText: 'wave goodbye'
}).plan[0].intent, 'wave');
assert.equal(core.analyzeSpeech('\u597d\u7684\u3002', {
    locale: 'en', userText: '\u6325\u624b\u544a\u522b'
}).plan[0].intent, 'wave');
assert.equal(core.analyzeSpeech("I can't.", {
    locale: 'zh-CN', userText: 'wave goodbye'
}).plan.length, 0);

[
    ['Can I help? I wave goodbye.', 'wave'],
    ['Should I continue? I clap now.', 'clap'],
    ['I look at you and wave.', 'wave'],
    ['I smile at you and clap.', 'clap'],
    ['I turn to you and nod.', 'nod']
].forEach(function ([text, expected]) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan[0].intent, expected, text);
});
['Can I wave? Let me know.', 'You look at me and wave.', 'I ask you to clap.'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
});

[
    ['without hesitation I clap', ['clap']],
    ['I stop and clap', ['clap']],
    ['I will not only clap but wave', ['clap', 'wave']]
].forEach(function ([text, expected]) {
    assert.deepEqual(core.analyzeSpeech(text, { locale: 'en' }).plan.map(function (item) {
        return item.intent;
    }), expected, text);
});
['without clapping', 'stop clapping', 'not clapping'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
});

[
    ['\u89c2\u4f17\u9f13\u638c\u3002', 'zh-CN'],
    ['\u89c0\u773e\u9f13\u638c\u3002', 'zh-TW'],
    ['El p\u00fablico aplaude.', 'es'],
    ['\u89b3\u5ba2\u304c\u62cd\u624b\u3057\u307e\u3059\u3002', 'ja'],
    ['\uad00\uac1d\uc774 \ubc15\uc218\ub97c \uce69\ub2c8\ub2e4\u3002', 'ko'],
    ['\u041e\u043d\u0430 \u0445\u043b\u043e\u043f\u0430\u0435\u0442 \u0432 \u043b\u0430\u0434\u043e\u0448\u0438.', 'ru'],
    ['O p\u00fablico bate palmas.', 'pt']
].forEach(function ([text, locale]) {
    assert.equal(core.analyzeSpeech(text, { locale: locale }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('De acuerdo.', {
    locale: 'es', userText: 'El p\u00fablico aplaude.'
}).plan.length, 0);
assert.equal(core.analyzeSpeech('\u79c1\u306f\u62cd\u624b\u3057\u307e\u3059\u3002', {
    locale: 'ja'
}).plan[0].intent, 'clap');
assert.equal(core.analyzeSpeech('Yo saludo al p\u00fablico y aplaude.', {
    locale: 'es'
}).plan.some(function (item) { return item.intent === 'clap'; }), true);
assert.equal(core.analyzeSpeech('\u89b3\u5ba2\u306b\u624b\u3092\u632f\u3063\u3066\u62cd\u624b\u3057\u307e\u3059\u3002', {
    locale: 'ja'
}).plan.some(function (item) { return item.intent === 'clap'; }), true);

assert.equal(core.analyze('n\u00e3o aplaude', { locale: 'pt' }).plan.length, 0);
assert.equal(core.analyzeSpeech('n\u00e3o aplaude', { locale: 'pt' }).plan.length, 0);
assert.equal(core.analyze('aplaude', { locale: 'pt' }).plan[0].intent, 'clap');

assert.deepEqual(core.analyzeSpeech(
    'gently wave and vigorously clap',
    { locale: 'en' }
).plan.map(function (item) {
    return [item.intent, item.intensity];
}), [['wave', 1], ['clap', 3]]);

assert.equal(core.analyze(
    '\uace0\uac1c\ub97c \ub044\ub355\uc774\uc9c0 \uc54a\uc544\uc694',
    { locale: 'ko' }
).plan.length, 0);
assert.equal(core.analyzeSpeech(
    '\uace0\uac1c\ub97c \ub044\ub355\uc774\uc9c0 \uc54a\uc544\uc694',
    { locale: 'ko' }
).plan.length, 0);
assert.equal(core.analyzeSpeech('\uace0\uac1c\ub97c \ub044\ub355\uc5ec\uc694', {
    locale: 'ko'
}).plan[0].intent, 'nod');

['\u3046\u306a\u305a\u3051\u3070', '\u3046\u306a\u305a\u3044\u305f\u3089', '\u62cd\u624b\u3057\u305f\u3089'].forEach(function (text) {
    assert.equal(core.analyze(text, { locale: 'ja' }).plan.length, 0, text);
    assert.equal(core.analyzeSpeech(text, { locale: 'ja' }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('\u3046\u306a\u305a\u304d\u307e\u3059', {
    locale: 'ja'
}).plan[0].intent, 'nod');

[
    ['wave goodbye', 'wave'],
    ['play piano', 'piano']
].forEach(function ([userText, expected]) {
    assert.equal(core.analyzeSpeech('Okay.', {
        locale: 'en', userText: userText
    }).plan[0].intent, expected, userText);
});
['do not wave goodbye', 'ask the audience to play piano'].forEach(function (userText) {
    assert.equal(core.analyzeSpeech('Okay.', {
        locale: 'en', userText: userText
    }).plan.length, 0, userText);
});

[
    ['sit down', 'sit'],
    ['lie down', 'lie'],
    ['stand up', 'recover']
].forEach(function ([text, expected]) {
    assert.equal(core.analyze(text, { locale: 'en' }).plan[0].intent, expected, text);
});

['For example, I clap', 'The instruction is clap'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'zh-CN' }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('举例，我鼓掌', { locale: 'en' }).plan.length, 0);
assert.equal(core.analyzeSpeech('I clap', { locale: 'zh-CN' }).plan[0].intent, 'clap');

assert.deepEqual(core.analyze(
    'wave now. If she claps, nod later',
    { locale: 'en' }
).plan.map(function (item) { return item.intent; }), ['wave']);
assert.deepEqual(core.analyze(
    'I will wave, and if she claps I will nod',
    { locale: 'en' }
).plan.map(function (item) { return item.intent; }), ['wave']);
assert.equal(core.analyze('If she claps, nod later', { locale: 'en' }).plan.length, 0);
assert.deepEqual(core.analyze(
    '\u5982\u679c\u5979\u9f13\u638c\u3002\u624b\u642d\u5728\u7709\u524d\uff0c\u773a\u671b\u8fdc\u65b9',
    { locale: 'zh-CN' }
).plan.map(function (item) { return item.intent; }), ['look']);
assert.equal(core.analyze(
    '\u5982\u679c\u5979\u9f13\u638c\uff0c\u624b\u642d\u5728\u7709\u524d\uff0c\u773a\u671b\u8fdc\u65b9',
    { locale: 'zh-CN' }
).plan.length, 0);
assert.equal(core.analyze(
    '\u6ca1\u6709\u628a\u624b\u642d\u5728\u7709\u524d\uff0c\u53ea\u662f\u5f80\u8fdc\u5904\u770b\u7740\uff0c\u4e5f\u5e76\u6ca1\u6709\u771f\u7684\u773a\u671b\u8fdc\u65b9',
    { locale: 'zh-CN' }
).plan.length, 0);
assert.deepEqual(core.analyze(
    '\u4e0d\u8981\u70b9\u5934\u3002\u6325\u624b',
    { locale: 'zh-CN' }
).plan.map(function (item) { return item.intent; }), ['wave']);
assert.deepEqual(core.analyze(
    '挥手。如果她鼓掌，就点头',
    { locale: 'zh-CN' }
).plan.map(function (item) { return item.intent; }), ['wave']);

assert.equal(core.actionCards.some(function (card) {
    return card.stableId === 'cheer_01';
}), false);
assert.equal(core.analyze('举手欢呼', { locale: 'zh-CN' }).plan.length, 0);

['The audience claps.', 'The viewers nod.', 'Everyone waves.'].forEach(function (text) {
    assert.equal(core.analyzeSpeech(text, { locale: 'en' }).plan.length, 0, text);
});
assert.equal(core.analyzeSpeech('I will.', {
    locale: 'en', userText: 'ask the audience to clap'
}).plan.length, 0);
assert.equal(core.analyzeSpeech('I wave to the audience.', {
    locale: 'en'
}).plan[0].intent, 'wave');

assert.equal(core.analyzeSpeech('I clap', { locale: 'en' }).plan[0].intensity, 2);
assert.equal(core.analyzeSpeech('I gently clap', { locale: 'en' }).plan[0].intensity, 1);
assert.equal(core.analyzeSpeech('I vigorously clap', { locale: 'en' }).plan[0].intensity, 3);
assert.equal(core.analyzeSpeech('I firmly nod', { locale: 'en' }).plan[0].intensity, 3);
const mixedIntensity = core.analyzeSpeech('gently wave then vigorously clap', { locale: 'en' });
assert.deepEqual(mixedIntensity.plan.map(function (item) {
    return [item.intent, item.intensity];
}), [['wave', 1], ['clap', 3]]);
const trailingMixedIntensity = core.analyzeSpeech('wave gently then clap vigorously', { locale: 'en' });
assert.deepEqual(trailingMixedIntensity.plan.map(function (item) {
    return [item.intent, item.intensity];
}), [['wave', 1], ['clap', 3]]);

const exactCardAccepted = core.analyzeSpeech('好的。', {
    locale: 'zh-CN', userText: '站着连续拍手鼓掌'
});
assert.equal(exactCardAccepted.plan[0].intent, 'clap');
assert.equal(exactCardAccepted.plan[0].evidence.assetId, 'clap_01');
const exactCardRefused = core.analyzeSpeech('抱歉，我不能这么做。', {
    locale: 'zh-CN', userText: '站着连续拍手鼓掌'
});
assert.equal(exactCardRefused.plan.some(function (item) {
    return item.intent === 'clap' || item.evidence.assetId === 'clap_01';
}), false);
assert.equal(core.analyzeSpeech('好的，但我不能这么做。', {
    locale: 'zh-CN', userText: '站着连续拍手鼓掌'
}).plan.length, 0);

requiredLocales.forEach(function (locale) {
    assert.ok(semantics.speech.refusals[locale].length > 0, locale + ' refusal terms');
});
['wave', 'piano'].forEach(function (intentId) {
    const command = semantics.speech.commands.find(function (candidate) {
        return candidate.id === intentId;
    });
    requiredLocales.forEach(function (locale) {
        assert.ok(command.terms[locale].length > 0, intentId + ' ' + locale + ' command terms');
    });
});

const traditionalCases = [
    ['輕輕揮手', 'wave', null],
    ['彈奏鋼琴', 'piano', 'piano_01'],
    ['雙手敲擊鍵盤', 'piano', 'piano_01']
];
traditionalCases.forEach(function ([text, expectedIntent, expectedAsset]) {
    const result = core.analyze(text, { locale: 'zh-TW' });
    assert.equal(result.plan[0].intent, expectedIntent, text);
    if (expectedAsset) assert.equal(result.plan[0].evidence.assetId, expectedAsset, text);
});
['en', 'zh-CN'].forEach(function (locale) {
    const result = core.analyze('彈奏鋼琴', { locale: locale });
    assert.equal(result.plan[0].intent, 'piano', locale + ' Traditional Chinese detection');
    assert.equal(result.plan[0].evidence.assetId, 'piano_01', locale + ' Traditional Chinese asset');
});

['演奏钢琴', '弹奏钢琴'].forEach(function (text) {
    const result = core.analyze(text, { locale: 'zh-CN' });
    assert.equal(result.plan[0].intent, 'piano', text);
    assert.equal(result.plan[0].evidence.assetId, 'piano_01', text);
});
assert.equal(intent('特别用力点头'), 'nod');
assert.equal(intent('告别时挥手'), 'wave');
assert.equal(core.analyze('别点头', { locale: 'zh-CN' }).plan.length, 0);
assert.equal(core.analyze('如果，手，挥手，告别', {
    locale: 'zh-CN'
}).plan.length, 0);

const piano = core.analyze('plays piano', { locale: 'en' });
assert.equal(piano.plan[0].intent, 'piano');
assert.equal(piano.plan[0].evidence.assetId, 'piano_01');
assert.deepEqual(
    core.analyze('waves hello then sits down', { locale: 'en' })
        .plan.map(function (item) { return item.intent; }),
    ['wave', 'sit']
);
assert.deepEqual(
    core.analyze('bows then claps', { locale: 'en' })
        .plan.map(function (item) { return item.intent; }),
    ['bow', 'clap']
);
assert.deepEqual(
    core.analyze('waves hello then sits down then waves goodbye', { locale: 'en' })
        .plan.map(function (item) { return item.intent; }),
    ['wave', 'sit', 'wave']
);
assert.equal(core.analyze('nods three times', { locale: 'en' }).plan[0].count, 3);
assert.equal(core.analyze('claps twice', { locale: 'en' }).plan[0].count, 2);
assert.equal(core.analyze('waves hello 3 times', { locale: 'en' }).plan[0].count, 3);

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
