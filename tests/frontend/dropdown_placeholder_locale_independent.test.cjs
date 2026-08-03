// 下拉框的「占位项」判定必须与界面语言无关（issue #2500）。
//
// 原来 shouldSkipOption 按 **显示文本** 判断某个 <option> 是不是占位项：
//     option.textContent.includes('请先加载') || ... || includes('Select')
// 这只在简体中文界面下成立。项目支持 8 种语言，ja / ko / ru / es / pt 和 zh-TW
// 全部落空——占位项被当成可选项，用户能选中一条「请先加载模型」并触发一次空加载。
// 这不是「拿简体」的软降级，是 7 种语言的功能缺陷。
//
// 现在判据是 data-placeholder 标记；文本判断只作兜底（万一还有没迁的生产点，
// 简中下不至于退化）。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const PROJECT_ROOT = path.resolve(__dirname, '..', '..');
const DROPDOWN_JS = path.join(
    PROJECT_ROOT, 'static', 'js', 'model_manager', 'dropdown-manager.js');
const PAGE_CONTROLLER_JS = path.join(
    PROJECT_ROOT, 'static', 'js', 'model_manager', 'page-controller.js');
const MODEL_MANAGER_HTML = path.join(PROJECT_ROOT, 'templates', 'model_manager.html');

function loadDropdownManager() {
    const source = fs.readFileSync(DROPDOWN_JS, 'utf8');
    const sandbox = {
        window: { addEventListener() {} },
        document: { getElementById: () => null, addEventListener() {} },
        console,
    };
    sandbox.globalThis = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(source + '\n;globalThis.__DM = DropdownManager;', sandbox);
    return sandbox.__DM;
}

const DropdownManager = loadDropdownManager();

/** 最小 <option> 替身：只需要 value / textContent / dataset。 */
function option({ value = '', text = '', placeholder = false } = {}) {
    return { value, textContent: text, dataset: placeholder ? { placeholder: 'true' } : {} };
}

// 同一条占位文案的 8 种语言写法。只有简中那条能被旧的文本判断认出来。
const PLACEHOLDER_TEXT_BY_LOCALE = {
    'zh-CN': '请先加载模型',
    'zh-TW': '請先載入模型',
    en: 'Load a model first',
    ja: 'まずモデルを読み込んでください',
    ko: '먼저 모델을 불러오세요',
    ru: 'Сначала загрузите модель',
    es: 'Primero carga un modelo',
    pt: 'Carregue um modelo primeiro',
};

test('打了标记的占位项，8 种语言下都被识别', () => {
    for (const [locale, text] of Object.entries(PLACEHOLDER_TEXT_BY_LOCALE)) {
        assert.equal(
            DropdownManager.isPlaceholderOption(option({ text, placeholder: true })),
            true,
            `${locale} 的占位项没被识别：${text}`,
        );
    }
});

test('没有标记时，只有简中那条能靠文本兜底认出来——这正是原来的缺陷', () => {
    const recognised = Object.entries(PLACEHOLDER_TEXT_BY_LOCALE)
        .filter(([, text]) => DropdownManager.isPlaceholderOption(option({ text })))
        .map(([locale]) => locale);
    // 前提守卫：兜底确实只覆盖简中。如果哪天它覆盖了更多语言，上面那条测试
    // 就不再证明「标记」在起作用了，得回来重写。
    assert.deepEqual(recognised, ['zh-CN']);
});

test('真实可选项不会被当成占位项', () => {
    const realOptions = [
        option({ value: 'hiyori', text: 'Hiyori' }),
        option({ value: '_no_motion_', text: '无动作' }),
        option({ value: '_no_expression_', text: '无表情' }),
        // ⚠️ 文案里带「没有」但有真实取值 —— 兜底的文本判断要求 value === ''
        option({ value: 'x', text: '没有动作文件' }),
    ];
    for (const opt of realOptions) {
        assert.equal(DropdownManager.isPlaceholderOption(opt), false,
            `真实可选项被当成占位：${opt.textContent}`);
    }
});

test('null / undefined 不炸', () => {
    assert.equal(DropdownManager.isPlaceholderOption(null), false);
    assert.equal(DropdownManager.isPlaceholderOption(undefined), false);
});

test('markAsPlaceholder 打的标记，isPlaceholderOption 认得', () => {
    const opt = option({ text: 'まずモデルを読み込んでください' });
    assert.equal(DropdownManager.isPlaceholderOption(opt), false);
    assert.equal(DropdownManager.markAsPlaceholder(opt), opt);
    assert.equal(DropdownManager.isPlaceholderOption(opt), true);
});

// ── 调用点：别再各写各的 ────────────────────────────────────
test('DropdownManager 的默认 shouldSkipOption 就是这条共用判据', () => {
    // ⚠️ 上面几条只测谓词。默认实现被换回按文本判断的闭包时它们全是绿的——
    // 必须打调用点。构造函数在找不到 button 时提前 return，但 config 已经装好了。
    const manager = new DropdownManager({ buttonId: 'nope', selectId: 'nope' });
    assert.equal(manager.config.shouldSkipOption, DropdownManager.isPlaceholderOption);
});

test('page-controller 里不再有按显示文本判断占位项的闭包', () => {
    const source = fs.readFileSync(PAGE_CONTROLLER_JS, 'utf8');
    const offenders = source
        .split('\n')
        .map((line, i) => [i + 1, line])
        .filter(([, line]) => /textContent\.includes\(/.test(line));
    assert.deepEqual(offenders, [],
        `这些行还在按显示文本判断，改用 DropdownManager.isPlaceholderOption：\n` +
        offenders.map(([n, l]) => `  ${n}: ${l.trim()}`).join('\n'));
});

test('「所有选项都被跳过」这条路径有空值保护', () => {
    // ⚠️ 这条路径对 ja / ko / ru / es / pt 是**新的**：改之前它们一条都不跳过，
    // find() 从来没返回过 undefined。简中早就会走到这里（占位项本来就被跳过），
    // 所以行为是对齐而不是新增，但代码得先扛得住。
    const source = fs.readFileSync(DROPDOWN_JS, 'utf8');
    const findLine = source.indexOf('.find(opt => !this.config.shouldSkipOption(opt))');
    assert.ok(findLine > 0, 'find(...) 那处不见了，这条测试失去前提');
    const after = source.slice(findLine, findLine + 200);
    assert.match(after, /if\s*\(firstDisplayOption\)/,
        'find() 可能返回 undefined，取值前必须判空');
});

test('兜底文案表是旧的 7 个闭包 + 默认实现的并集，简中行为不变', () => {
    // 旧实现里出现过的全部文案（git 里逐条核对过）。'没有动作'/'没有动画'/
    // '没有表情' 被 '没有' 覆盖。
    const oldTexts = ['Select', '加载中', '没有动作', '没有动画', '没有表情',
        '请先加载', '请选择', '选择模型'];
    for (const text of oldTexts) {
        assert.equal(
            DropdownManager.isPlaceholderOption(option({ text: `X${text}X` })),
            true,
            `旧规则会跳过 ${text}，新规则不跳了——简中行为退化`,
        );
    }
});

test('HTML 里 value 为空的占位项要么打了标记，要么本来就不该跳过', () => {
    const html = fs.readFileSync(MODEL_MANAGER_HTML, 'utf8');
    // 只看 value="" 的（没有 value 属性时 option.value 等于文本，不会被判为占位）
    const emptyValueOptions = [...html.matchAll(/<option value=""[^>]*>([^<]*)<\/option>/g)];
    assert.ok(emptyValueOptions.length >= 4, '模板里的占位项都没了？这条测试失去了前提');
    for (const m of emptyValueOptions) {
        const tag = m[0];
        const text = m[1];
        const marked = tag.includes('data-placeholder="true"');
        const wouldSkipByText = ['请先加载', '请选择', '没有', '加载中', '选择模型', 'Select']
            .some((t) => text.includes(t));
        assert.equal(marked, wouldSkipByText,
            `${text}：打标记(${marked}) 与「旧文本规则会不会跳过」(${wouldSkipByText}) 不一致。` +
            `本 PR 只做语言对齐，不改简中下跳不跳的既有判断。`);
    }
});
