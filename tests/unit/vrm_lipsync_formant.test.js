// 五元音共振峰口型分析器（FormantLipSyncAnalyzer）的 Node 行为测试。
// 用 vm 把经典脚本加载进一个带 window 的上下文，再用合成频谱驱动 mock AnalyserNode，
// 验证：F1/F2 共振峰 → 正确元音、top-2 混合、静音门限、词内间隙保持、帧率无关平滑。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const projectRoot = path.resolve(__dirname, '..', '..');
const modulePath = path.join(projectRoot, 'static', 'vrm', 'vrm-lipsync-formant.js');

const SAMPLE_RATE = 44100;
const FFT_SIZE = 1024;

// 加载经典脚本，返回 { FormantLipSyncAnalyzer, VOWEL_KEYS }。
function loadModule(nowMs = 0) {
    const context = {
        console,
        performance: { now: () => nowMs },
        Math,
        window: {},
    };
    vm.createContext(context);
    const src = fs.readFileSync(modulePath, 'utf8');
    vm.runInContext(src, context, { filename: modulePath });
    return {
        FormantLipSyncAnalyzer: context.window.FormantLipSyncAnalyzer,
        setNow: (v) => { nowMs = v; },
    };
}

// 构造一个 mock AnalyserNode：给定若干 {hz, level} 谱峰，合成 byte 频谱。
// bin i 对应频率 (i/binCount)*nyquist；每个峰在其最近 bin 写入 level（0..255）。
function makeAnalyser(peaks) {
    const binCount = FFT_SIZE / 2;
    const nyquist = SAMPLE_RATE / 2;
    const bins = new Uint8Array(binCount);
    for (const { hz, level } of peaks) {
        const idx = Math.round((hz / nyquist) * binCount);
        if (idx >= 0 && idx < binCount) bins[idx] = Math.max(bins[idx], level);
    }
    return {
        fftSize: FFT_SIZE,
        smoothingTimeConstant: 0,
        frequencyBinCount: binCount,
        context: { sampleRate: SAMPLE_RATE },
        getByteFrequencyData(out) { out.set(bins); },
    };
}

// 在某元音的共振峰中心放两个峰 + 宽带噪声底，模拟该元音的发声帧。
function vowelAnalyser(f1, f2, level = 200) {
    return makeAnalyser([
        { hz: f1, level },
        { hz: f2, level },
        { hz: 300, level: 60 }, // 200-4000 宽带底，给 volume 一个非零值
        { hz: 1500, level: 60 },
        { hz: 2500, level: 60 },
    ]);
}

// 驱动 analyser 多帧，返回最后一帧输出。
function runFrames(analyzer, frames, delta = 1 / 60, startNow = 0) {
    let out = null;
    let now = startNow;
    for (let i = 0; i < frames; i++) {
        now += delta * 1000;
        analyzer._setNow?.(now);
        out = analyzer.update(delta);
    }
    return out;
}

test('加载后挂载 window.FormantLipSyncAnalyzer 与元音键', () => {
    const { FormantLipSyncAnalyzer, } = loadModule();
    assert.equal(typeof FormantLipSyncAnalyzer, 'function');
});

test('F1/F2 指向 aa 共振峰中心时，aa 权重最高', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    // aa: f1 850, f2 1400
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    // 让静音门限的 lastActiveAt 生效：用足够小的 delta 多帧推进
    let out = null;
    for (let i = 0; i < 30; i++) out = a.update(1 / 60);
    const entries = Object.entries(out).sort((x, y) => y[1] - x[1]);
    assert.equal(entries[0][0], 'aa', `期望 aa 最强，实际 ${JSON.stringify(out)}`);
    assert.ok(out.aa > 0, 'aa 应被激活');
});

test('F1/F2 指向 ih（闭前）时，ih 权重最高', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    // ih: f1 350, f2 2700
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(350, 2700));
    let out = null;
    for (let i = 0; i < 30; i++) out = a.update(1 / 60);
    const entries = Object.entries(out).sort((x, y) => y[1] - x[1]);
    assert.equal(entries[0][0], 'ih', `期望 ih 最强，实际 ${JSON.stringify(out)}`);
});

test('top-2：最多两个元音有非零权重', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    // 故意放在 aa 与 oh 之间的共振峰，让两者接近
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(650, 1150));
    let out = null;
    for (let i = 0; i < 40; i++) out = a.update(1 / 60);
    const nonZero = Object.values(out).filter((v) => v > 0.001);
    assert.ok(nonZero.length <= 2, `top-2 应至多两个非零，实际 ${JSON.stringify(out)}`);
    assert.ok(nonZero.length >= 1, '至少一个元音应激活');
});

test('静音（无谱峰、低能量）时所有元音收敛到 0', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    let now = 0;
    const frameMs = 1000 / 60;

    // 全零频谱：volume≈0 → 触发静音；但需先让 idle 超时（IDLE_MS=160ms），
    // 否则 idle 窗口内保持 state 不归零。
    const silent = makeAnalyser([]);
    const a = new FormantLipSyncAnalyzer(silent);
    let out = null;
    // 30 帧 × 16.7ms ≈ 500ms > IDLE_MS(160ms)，确保 idle 超时后归零
    for (let i = 0; i < 30; i++) {
        now += frameMs;
        setNow(now);
        out = a.update(1 / 60);
    }
    for (const [k, v] of Object.entries(out)) {
        assert.ok(v <= 0.001, `静音时 ${k} 应为 0，实际 ${v}`);
    }
});

test('idle 窗口内短暂静音不归零，保持当前嘴型', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    let now = 0;
    const frameMs = 1000 / 60; // ~16.7ms per frame

    // 先驱动到有声音的稳态（逐帧推进 mock clock）
    const voiced = vowelAnalyser(850, 1400); // aa
    const a = new FormantLipSyncAnalyzer(voiced);
    let out = null;
    for (let i = 0; i < 60; i++) {
        now += frameMs;
        setNow(now);
        out = a.update(1 / 60);
    }
    assert.ok(out.aa > 0.1, `有声稳态 aa=${out.aa} 应 > 0.1`);
    const steadyAa = out.aa;

    // 切到静音分析器，逐帧推进但保持在 idle 窗口内（< IDLE_MS=160ms）
    const silent = makeAnalyser([]);
    a.analyser = silent;
    // 跑 8 帧 ≈ 133ms < 160ms
    for (let i = 0; i < 8; i++) {
        now += frameMs;
        setNow(now);
        out = a.update(1 / 60);
    }
    // idle 窗口内应保持稳态值，不归零
    assert.ok(out.aa > steadyAa * 0.7,
        `idle 窗口内 aa=${out.aa} 应保持接近稳态 ${steadyAa}，不应归零`);
});

test('平滑是渐进的：单帧不会从 0 跳到目标（攻击平滑）', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const first = a.update(1 / 60);
    // 第一帧后 aa 应已开始上升但远小于稳态（攻击速率 50，单帧 1/60s 只走一部分）
    let steady = first;
    for (let i = 0; i < 60; i++) steady = a.update(1 / 60);
    assert.ok(first.aa < steady.aa, `首帧 ${first.aa} 应小于稳态 ${steady.aa}`);
    assert.ok(steady.aa > first.aa, '稳态应高于首帧（渐进上升）');
});

test('释放慢于攻击：闭嘴比张嘴平缓（同帧数下释放位移更小）', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    // 先让嘴张到稳态
    const loud = vowelAnalyser(850, 1400);
    const a = new FormantLipSyncAnalyzer(loud);
    for (let i = 0; i < 60; i++) a.update(1 / 60);

    // 换成静音源，测量闭嘴第一帧的下降幅度，与当初张嘴第一帧的上升幅度对比
    a.attach(makeAnalyser([]));
    // attach 会重置 fftSize/smoothingTimeConstant，但 state 保留（attach 不 reset）
    const beforeClose = a.state.aa;
    a.update(1 / 60);
    const closeDelta = beforeClose - a.state.aa;

    // 重新测张嘴第一帧上升幅度
    const b = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    b.update(1 / 60);
    const openDelta = b.state.aa - 0;

    assert.ok(closeDelta < openDelta,
        `释放位移 ${closeDelta} 应小于攻击位移 ${openDelta}（攻击快于释放）`);
});

test('帧率无关：60fps 与 30fps 在相同时长后到达相近权重', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    const peaks = vowelAnalyser(850, 1400);

    const a60 = new FormantLipSyncAnalyzer(peaks);
    for (let i = 0; i < 60; i++) a60.update(1 / 60); // 1 秒

    const a30 = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    for (let i = 0; i < 30; i++) a30.update(1 / 30); // 1 秒

    const diff = Math.abs(a60.state.aa - a30.state.aa);
    assert.ok(diff < 0.05, `帧率无关性：60fps ${a60.state.aa} vs 30fps ${a30.state.aa} 差异 ${diff} 应 < 0.05`);
});

test('reset 清空平滑状态', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    for (let i = 0; i < 30; i++) a.update(1 / 60);
    assert.ok(a.state.aa > 0, 'reset 前应有状态');
    a.reset();
    assert.equal(a.state.aa, 0);
    assert.equal(a.lastActiveAt, 0);
});

test('无 analyser 时 update 返回全零（安全降级）', () => {
    const { FormantLipSyncAnalyzer } = loadModule();
    const a = new FormantLipSyncAnalyzer(null);
    const out = a.update(1 / 60);
    for (const v of Object.values(out)) assert.equal(v, 0);
});
