// 五元音共振峰口型分析器（FormantLipSyncAnalyzer）的 Node 行为测试。
//
// 放在 tests/frontend/ 并由 tests/unit/test_vrm_lipsync_formant_static_contracts.py
// 通过 `node --test` 拉起，与 tests/frontend/vrm_motion_*.test.cjs 的做法一致。
// 早先这份测试放在 tests/unit/*.test.js，仓库里没有任何 runner 会收集该后缀，
// 十条断言从未在 CI 里执行过。
//
// 用 vm 把经典脚本加载进一个带 window 的上下文，再用合成频谱驱动 mock AnalyserNode，
// 验证：F1/F2 共振峰 → 正确元音（含圆唇 お/う）、top-2 混合、静音门限、
// 词内间隙保持、释放确实慢于攻击、帧率无关平滑、脏 delta 不污染状态。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

// 与 tests/frontend/vrm_motion_*.test.cjs 同一套定位方式：直接 `node <file>` 时
// __dirname 可用；经 tests/node_harness.run_node_script 拉起时脚本被写到临时目录，
// __dirname 指不到仓库，此时回落到 harness 传进来的 cwd。
const fileRoot = path.resolve(__dirname, '..', '..');
const projectRoot = fs.existsSync(path.join(fileRoot, 'static')) ? fileRoot : process.cwd();
const modulePath = path.join(projectRoot, 'static', 'vrm', 'vrm-lipsync-formant.js');
const moduleSource = fs.readFileSync(modulePath, 'utf8');

// 宿主 analyser 的真实参数：app-audio-playback 的 S.globalAnalyser 建出来就是
// fftSize=2048 / 默认 smoothingTimeConstant。分析器必须适配这个格式而不是改写它。
const SAMPLE_RATE = 48000;
const HOST_FFT_SIZE = 2048;
const HOST_SMOOTHING = 0.8;

// 加载经典脚本，返回 { FormantLipSyncAnalyzer, VOWEL_KEYS, setNow, context }。
function loadModule() {
    let nowMs = 0;
    const context = {
        console,
        performance: { now: () => nowMs },
        Math,
        Number,
        Object,
        Uint8Array,
        window: {},
    };
    vm.createContext(context);
    vm.runInContext(moduleSource, context, { filename: modulePath });
    return {
        FormantLipSyncAnalyzer: context.window.FormantLipSyncAnalyzer,
        VOWEL_KEYS: context.window.VRM_LIPSYNC_VOWEL_KEYS,
        setNow: (v) => { nowMs = v; },
        loadAgain: () => vm.runInContext(moduleSource, context, { filename: modulePath }),
        context,
    };
}

// 构造 mock AnalyserNode：给定若干 {hz, level} 谱峰，合成 byte 频谱。
// 每个峰带 ±2 bin 的裙边，比单 bin 冲激更接近真实共振峰。
function makeAnalyser(peaks, overrides = {}) {
    const binCount = HOST_FFT_SIZE / 2;
    const nyquist = SAMPLE_RATE / 2;
    const bins = new Uint8Array(binCount);
    for (const { hz, level } of peaks) {
        const center = Math.round((hz / nyquist) * binCount);
        for (let d = -2; d <= 2; d++) {
            const i = center + d;
            if (i >= 0 && i < binCount) {
                bins[i] = Math.max(bins[i], Math.round(level * (1 - Math.abs(d) * 0.18)));
            }
        }
    }
    return {
        fftSize: HOST_FFT_SIZE,
        smoothingTimeConstant: HOST_SMOOTHING,
        frequencyBinCount: binCount,
        context: { sampleRate: SAMPLE_RATE },
        getByteFrequencyData(out) { out.set(bins.subarray(0, out.length)); },
        ...overrides,
    };
}

// 某元音的一帧发声：F1 最强、F2 次强，再加谐波与高频底噪。
//
// 注意这是"孤立谱峰"语料：峰之间是硬零。它够用来验平滑、门限、键映射这类
// 与谱形无关的行为，但**不能**用来判断共振峰检测的好坏——真实语音在 F1 之后
// 是单调下降的连续谱，F1 的裙边会盖过高频，而这里没有裙边。判别质量一律用
// 下面的 source-filter 合成语料（synthVowelAnalyser）来测。
function vowelAnalyser(f1, f2) {
    return makeAnalyser([
        { hz: f1, level: 210 },
        { hz: f2, level: 170 },
        { hz: f1 * 2, level: 70 },
        { hz: 2600, level: 55 },
        { hz: 3400, level: 40 },
    ]);
}

// ───────────────── source-filter 元音合成（判别质量专用）─────────────────
//
// 声门脉冲串 → 两个一极点(-12dB/oct) → 级联二极点共振器 → 唇辐射(+6dB/oct)，
// 再按 Web Audio 的真实映射转成 byte：Blackman 窗、DFT、20log10、
// [minDecibels,maxDecibels] = [-100,-30] 夹到 0..255。
// 这样谱形带真实的裙边与倾斜，"F1 裙边压过 F2 峰"这类失效模式才可能被表达出来。
function synthSignal(f0, formants, n) {
    const y = new Float64Array(n);
    const period = SAMPLE_RATE / f0;
    for (let k = 0; k * period < n; k++) y[Math.floor(k * period)] = 1;
    for (let p = 0; p < 2; p++) {                       // 声门源 -12dB/oct
        const a = Math.exp(-2 * Math.PI * 100 / SAMPLE_RATE);
        let prev = 0;
        for (let i = 0; i < n; i++) { y[i] = (1 - a) * y[i] + a * prev; prev = y[i]; }
    }
    for (const [F, B] of formants) {                    // 共振器（DC 归一化）
        const r = Math.exp(-Math.PI * B / SAMPLE_RATE);
        const th = 2 * Math.PI * F / SAMPLE_RATE;
        const a1 = 2 * r * Math.cos(th), a2 = -r * r, b0 = 1 - a1 - a2;
        let y1 = 0, y2 = 0;
        for (let i = 0; i < n; i++) { const v = b0 * y[i] + a1 * y1 + a2 * y2; y2 = y1; y1 = v; y[i] = v; }
    }
    let prev = 0;                                       // 唇辐射 +6dB/oct
    for (let i = 0; i < n; i++) { const v = y[i] - prev; prev = y[i]; y[i] = v; }
    let peak = 0;
    for (let i = 0; i < n; i++) peak = Math.max(peak, Math.abs(y[i]));
    // 归一到真实音频量级；不归一的话整条谱会被 minDecibels 夹成 0，
    // 那时测的是搜索窗常量而不是算法。
    if (peak > 0) { const g = 0.5 / peak; for (let i = 0; i < n; i++) y[i] *= g; }
    return y;
}

const BLACKMAN = new Float64Array(HOST_FFT_SIZE);
for (let i = 0; i < HOST_FFT_SIZE; i++) {
    BLACKMAN[i] = 0.42
        - 0.5 * Math.cos(2 * Math.PI * i / (HOST_FFT_SIZE - 1))
        + 0.08 * Math.cos(4 * Math.PI * i / (HOST_FFT_SIZE - 1));
}

function byteSpectrum(sig) {
    const N = HOST_FFT_SIZE;
    const re = new Float64Array(N), im = new Float64Array(N);
    for (let i = 0; i < N; i++) re[i] = sig[i] * BLACKMAN[i];
    for (let i = 1, j = 0; i < N; i++) {                // bit reversal
        let bit = N >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; }
    }
    for (let len = 2; len <= N; len <<= 1) {            // radix-2 FFT
        const ang = -2 * Math.PI / len;
        const wr = Math.cos(ang), wi = Math.sin(ang);
        for (let i = 0; i < N; i += len) {
            let cwr = 1, cwi = 0;
            for (let k = 0; k < len / 2; k++) {
                const ur = re[i + k], ui = im[i + k];
                const vr = re[i + k + len / 2] * cwr - im[i + k + len / 2] * cwi;
                const vi = re[i + k + len / 2] * cwi + im[i + k + len / 2] * cwr;
                re[i + k] = ur + vr; im[i + k] = ui + vi;
                re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
                const nwr = cwr * wr - cwi * wi; cwi = cwr * wi + cwi * wr; cwr = nwr;
            }
        }
    }
    const bins = new Uint8Array(N / 2);
    for (let i = 0; i < N / 2; i++) {
        const mag = Math.hypot(re[i], im[i]) / N;
        const db = 20 * Math.log10(Math.max(mag, 1e-12));
        bins[i] = Math.max(0, Math.min(255, Math.round((db + 100) / 70 * 255)));
    }
    return bins;
}

/** 一个说着给定共振峰的嗓音，做成 analyser。 */
function synthVowelAnalyser(f0, [F1, F2, F3, F4], b1 = 90) {
    const bins = byteSpectrum(
        synthSignal(f0, [[F1, b1], [F2, 110], [F3, 140], [F4, 200]], HOST_FFT_SIZE)
    );
    return makeAnalyser([], { getByteFrequencyData(out) { out.set(bins.subarray(0, out.length)); } });
}

// 真实共振峰（Peterson-Barney / Hillenbrand 量级 + 日语五元音），映射到分析器的键
const REAL_VOWELS = [
    ['aa', '男 /ɑ/', [730, 1090, 2440, 3400]],
    ['aa', '女 /ɑ/', [850, 1220, 2810, 3600]],
    ['aa', '日 /a/', [750, 1200, 2500, 3400]],
    ['ee', '男 /ɛ/', [530, 1840, 2480, 3500]],
    ['ee', '女 /ɛ/', [610, 2330, 2990, 3700]],
    ['ee', '日 /e/', [500, 1900, 2600, 3500]],
    ['ih', '男 /i/', [270, 2290, 3010, 3700]],
    ['ih', '女 /i/', [310, 2790, 3310, 3900]],
    ['ih', '日 /i/', [300, 2300, 3000, 3700]],
];

// 驱动 analyzer 若干帧，同步推进被 mock 的 performance.now()。
// 时钟必须跟着走：IDLE_MS 的判定读的是 performance.now()，不推进的话
// idleExpired 恒为 false，release 分支永远不会被执行到。
function runFrames(analyzer, setNow, frames, { delta = 1 / 60, startNow = 0 } = {}) {
    let now = startNow;
    let out = null;
    for (let i = 0; i < frames; i++) {
        now += delta * 1000;
        setNow(now);
        out = analyzer.update(delta);
    }
    return { out, now };
}

function ranked(out) {
    return Object.entries(out).sort((a, b) => b[1] - a[1]);
}

test('加载后挂载 window.FormantLipSyncAnalyzer 与五元音键表', () => {
    const { FormantLipSyncAnalyzer, VOWEL_KEYS } = loadModule();
    assert.equal(typeof FormantLipSyncAnalyzer, 'function');
    assert.deepEqual([...VOWEL_KEYS].sort(), ['aa', 'ee', 'ih', 'oh', 'ou']);
});

test('重复加载幂等：第二次执行不抛、类身份不变', () => {
    // 该脚本同时挂在四条模块加载链上，model_manager 的 runtime-loaders.js
    // 更是在 VRM/MMD 两个并行 IIFE 里各列一次。顶层 const 重复声明会抛
    // "Identifier 'VOWEL_FORMANTS' has already been declared" 并打断整条链。
    const { FormantLipSyncAnalyzer, loadAgain, context } = loadModule();
    assert.doesNotThrow(() => loadAgain());
    assert.equal(context.window.FormantLipSyncAnalyzer, FormantLipSyncAnalyzer);
});

// ─────────────────────────── 五元音判别 ───────────────────────────
//
// 判别质量一律用下面的 source-filter 合成语料。曾经这里有一组"把参考 F1/F2
// 原样喂回去"的孤立谱峰用例，两个毛病：
//   1. 语料把参考表当输入又当答案，测不出距离度量由什么构成——把 d1 整个删掉
//      （F1 不参与判别）它照样全绿；
//   2. 谱峰之间是硬零，没有 F1 裙边，真实的失效模式表达不出来，
//      反倒对 F2 窗内那个人造底噪峰过度敏感。
// vowelAnalyser 仍用于与谱形无关的行为（平滑、门限、键映射）。

test('真实语音谱：前元音不会塌到圆唇元音（F2 窗下调的回归锁）', () => {
    // 这条用 source-filter 合成语料，不用孤立谱峰——因为要守的失效模式正是
    // "F1 的裙边盖过真实 F2 峰"，而孤立谱峰语料里根本没有裙边，表达不出来。
    //
    // 历史：曾把 F2 窗下界从 1000Hz 放到 700Hz（想让 ou/oh 的参考中心进窗），
    // 结果 _peakBetween 在窗底就撞上 F1 的裙边、直接返回窗底，/e/ 与 /a/ 成片
    // 翻成 oh。本语料下正确率 96.9% -> 84.4%。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const failures = [];
    let total = 0, correct = 0;
    for (const [want, label, formants] of REAL_VOWELS) {
        for (const f0 of [95, 110, 130, 180, 230]) {
            for (const b1 of [60, 90, 130]) {
                const a = new FormantLipSyncAnalyzer(synthVowelAnalyser(f0, formants, b1));
                const { out } = runFrames(a, setNow, 30);
                const got = ranked(out)[0][0];
                total++;
                if (got === want) correct++;
                else failures.push(`${label}(f0=${f0},B1=${b1}) 期望 ${want} 实得 ${got}`);
            }
        }
    }
    const rate = correct / total;
    assert.ok(rate >= 0.9,
        `真实共振峰语料判别率 ${(rate * 100).toFixed(1)}% (${correct}/${total}) 应 >= 90%。\n` +
        failures.slice(0, 8).join('\n'));
});

test('圆唇元音在真实语音谱下不被判成展唇元音', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const ROUNDED = [
        ['oh', '男 /ɔ/', [570, 840, 2410, 3400]],
        ['oh', '日 /o/', [500, 900, 2500, 3400]],
        ['ou', '男 /u/', [300, 870, 2240, 3400]],
        ['ou', '女 /u/', [370, 950, 2670, 3600]],
    ];
    const bad = [];
    for (const [want, label, formants] of ROUNDED) {
        for (const f0 of [110, 180]) {
            const a = new FormantLipSyncAnalyzer(synthVowelAnalyser(f0, formants));
            const { out } = runFrames(a, setNow, 30);
            const got = ranked(out)[0][0];
            // 同为圆唇（oh/ou 互判）视觉上可接受；翻成 aa/ee/ih 才是唇形判反。
            if (got !== 'oh' && got !== 'ou') bad.push(`${label}(f0=${f0}) -> ${got}`);
        }
    }
    assert.deepEqual(bad, [], `圆唇元音被判成展唇口型: ${bad.join(', ')}`);
});

test('top-2：最多两个元音有非零权重', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(650, 1150));
    const { out } = runFrames(a, setNow, 40);
    const nonZero = Object.values(out).filter((v) => v > 0.001);
    assert.ok(nonZero.length <= 2, `top-2 应至多两个非零，实际 ${JSON.stringify(out)}`);
    assert.ok(nonZero.length >= 1, '至少一个元音应激活');
});

// ─────────────── 静音 / idle 窗口 ───────────────

test('静音超过 IDLE_MS 后所有元音从张嘴态收敛到 0', () => {
    // 必须先驱动到有声稳态再切静音：直接用静音分析器构造，断言的只是
    // "0 保持 0"，release 逻辑一行都没跑到，IDLE_MS 改成多少都绿。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const voiced = runFrames(a, setNow, 60);
    assert.ok(voiced.out.aa > 0.1, `前置条件：有声稳态 aa=${voiced.out.aa} 应 > 0.1`);

    a.attach(makeAnalyser([]));
    // 30 帧 × 16.7ms ≈ 500ms > IDLE_MS(160ms)，足够走完 release
    const { out } = runFrames(a, setNow, 30, { startNow: voiced.now });
    for (const [k, v] of Object.entries(out)) {
        assert.ok(v <= 0.001, `静音超时后 ${k} 应归零，实际 ${v}`);
    }
});

test('idle 窗口内短暂静音不归零，保持当前嘴型', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const voiced = runFrames(a, setNow, 60);
    assert.ok(voiced.out.aa > 0.1, `有声稳态 aa=${voiced.out.aa} 应 > 0.1`);
    const steadyAa = voiced.out.aa;

    a.attach(makeAnalyser([]));
    // 8 帧 ≈ 133ms < IDLE_MS(160ms)
    const idle = runFrames(a, setNow, 8, { startNow: voiced.now });
    assert.ok(idle.out.aa > steadyAa * 0.7,
        `idle 窗口内 aa=${idle.out.aa} 应保持接近稳态 ${steadyAa}，不应归零`);
});

// ─────────────── 平滑特性 ───────────────

test('平滑是渐进的：单帧不会从 0 跳到目标（攻击平滑）', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const first = runFrames(a, setNow, 1);
    const steady = runFrames(a, setNow, 60, { startNow: first.now });
    assert.ok(first.out.aa < steady.out.aa,
        `首帧 ${first.out.aa} 应小于稳态 ${steady.out.aa}`);
});

test('释放慢于攻击：同一段位移，闭嘴耗时严格多于张嘴', () => {
    // 这条断言必须让时钟真正越过 IDLE_MS，否则分析器走的是"idle 窗口内保持
    // state"分支（target === state），位移恒为 0，断言会对任意 RELEASE 取值
    // 都成立 —— 早先的版本正是这样空转通过的。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const delta = 1 / 60;

    // 张嘴：从 0 升到稳态的一半需要几帧
    const opening = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const warm = runFrames(opening, setNow, 90);
    const steadyAa = warm.out.aa;
    assert.ok(steadyAa > 0.05, `稳态 aa=${steadyAa} 太小，用例失去分辨力`);

    const fresh = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    let now = 0;
    let openFrames = 0;
    while (fresh.state.aa < steadyAa / 2 && openFrames < 600) {
        now += delta * 1000;
        setNow(now);
        fresh.update(delta);
        openFrames++;
    }

    // 闭嘴：先跑到稳态，再切静音并把时钟推过 IDLE_MS，让 release 分支真正生效
    const closing = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const hot = runFrames(closing, setNow, 90);
    closing.attach(makeAnalyser([]));
    now = hot.now + 400; // 越过 IDLE_MS(160ms)，进入真停顿
    setNow(now);
    const closeFrom = closing.state.aa;
    let closeFrames = 0;
    while (closing.state.aa > closeFrom / 2 && closeFrames < 600) {
        now += delta * 1000;
        setNow(now);
        closing.update(delta);
        closeFrames++;
    }

    assert.ok(closeFrames < 600, 'release 分支未生效：嘴一直没有闭合');
    assert.ok(closeFrames > openFrames,
        `闭嘴用了 ${closeFrames} 帧，张嘴用了 ${openFrames} 帧；` +
        `攻击(${'50'})应快于释放(${'30'})，故闭嘴帧数必须更多`);
});

test('帧率无关：60fps 与 30fps 在爬升途中就一致，不只是终点一致', () => {
    // 跑满 1 秒两边都早已饱和到同一个稳态，那样比的是"目标值相同"，
    // 对任何依赖帧率的平滑律都成立。要在**上升沿中段**取样才有分辨力：
    // 若把 rate 写成与 delta 无关的常数（如 0.3），30fps 的位移会明显落后。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const MID_MS = 40;   // 远早于饱和（ATTACK=50 的时间常数约 20ms）

    const a60 = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    runFrames(a60, setNow, Math.round(MID_MS / (1000 / 60)), { delta: 1 / 60 });

    const a30 = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    runFrames(a30, setNow, Math.round(MID_MS / (1000 / 30)), { delta: 1 / 30 });

    assert.ok(a60.state.aa > 0.02 && a60.state.aa < 0.95 * 0.7,
        `取样点必须落在上升沿中段，实测 aa=${a60.state.aa}（已饱和则该用例失去分辨力）`);
    const diff = Math.abs(a60.state.aa - a30.state.aa);
    assert.ok(diff < 0.02,
        `帧率无关性：${MID_MS}ms 处 60fps=${a60.state.aa} vs 30fps=${a30.state.aa} 差异 ${diff} 应 < 0.02`);
});

// ─────────────── 宿主 analyser 归属 ───────────────

test('绝不改写宿主 analyser 的 fftSize / smoothingTimeConstant', () => {
    // S.globalAnalyser 是全局共享节点，Live2D 与 PNGTuber 的口型循环都在启动时
    // 按当时的 fftSize 预分配一次时域缓冲。在这里改配置会静默改掉它们的采样窗口。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const host = vowelAnalyser(850, 1400);
    const a = new FormantLipSyncAnalyzer(host);
    runFrames(a, setNow, 10);
    assert.equal(host.fftSize, HOST_FFT_SIZE, 'fftSize 被改写了');
    assert.equal(host.smoothingTimeConstant, HOST_SMOOTHING, 'smoothingTimeConstant 被改写了');
});

test('宿主运行期改 fftSize 时采样缓冲跟着重建', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const host = vowelAnalyser(850, 1400);
    const a = new FormantLipSyncAnalyzer(host);
    runFrames(a, setNow, 5);
    assert.equal(a.bins.length, HOST_FFT_SIZE / 2);

    host.fftSize = 512;
    host.frequencyBinCount = 256;
    runFrames(a, setNow, 5);
    assert.equal(a.bins.length, 256, '缓冲长度未跟随宿主 fftSize');
});

test('analyser 缺少 context 时不抛异常（回退默认 sampleRate）', () => {
    // mmd-animation.getLipSyncValue 对同一件事有显式防御，说明宿主实现
    // 并不保证 .context 存在；构造期抛异常会一路冒泡打断 startLipSync。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const host = makeAnalyser([{ hz: 850, level: 200 }], { context: undefined });
    let a;
    assert.doesNotThrow(() => { a = new FormantLipSyncAnalyzer(host); });
    const { out } = runFrames(a, setNow, 10);
    for (const v of Object.values(out)) assert.ok(Number.isFinite(v));
});

// ─────────────── 脏输入 ───────────────

test('非有限 delta 不会把 NaN latch 进平滑状态', () => {
    // NaN 一旦进 state 就永久留在那里（NaN 参与任何运算仍是 NaN），
    // 之后每帧把 NaN 写进 blendshape / morph influence，模型脸会崩。
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    setNow(100);
    for (const bad of [NaN, undefined, -1, Infinity, '0.016']) {
        a.update(bad);
        for (const [k, v] of Object.entries(a.state)) {
            assert.ok(Number.isFinite(v), `delta=${String(bad)} 让 state.${k} 变成 ${v}`);
        }
    }
    // 脏输入之后仍能正常收敛到 aa
    const { out } = runFrames(a, setNow, 40, { startNow: 100 });
    assert.equal(ranked(out)[0][0], 'aa', `脏 delta 之后未能恢复：${JSON.stringify(out)}`);
});

test('delta === 0 时不推进平滑（合法输入，不是脏值）', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    const warm = runFrames(a, setNow, 30);
    const before = { ...a.state };
    setNow(warm.now);
    a.update(0);
    for (const key of Object.keys(before)) {
        assert.equal(a.state[key], before[key], `delta=0 不应改变 state.${key}`);
    }
});

test('输出恒在 [0,1] 且不超过 CAP', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    // 全频段拉满，逼出最大可能的振幅
    const loud = makeAnalyser(
        Array.from({ length: 40 }, (_, i) => ({ hz: 200 + i * 100, level: 255 }))
    );
    const a = new FormantLipSyncAnalyzer(loud);
    const { out } = runFrames(a, setNow, 120);
    for (const [k, v] of Object.entries(out)) {
        assert.ok(v >= 0 && v <= 0.7 + 1e-9, `${k}=${v} 超出 [0, CAP=0.7]`);
    }
});

test('reset 清空平滑状态', () => {
    const { FormantLipSyncAnalyzer, setNow } = loadModule();
    const a = new FormantLipSyncAnalyzer(vowelAnalyser(850, 1400));
    runFrames(a, setNow, 30);
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
