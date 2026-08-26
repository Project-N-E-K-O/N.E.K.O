/**
 * vrm-lipsync-formant.js
 *
 * VRM 五元音共振峰口型分析器（formant viseme lip-sync）。
 *
 * 设计说明
 * --------
 * N.E.K.O 原实现把整段音频频谱压成一个标量音量，只驱动 `aa`（张嘴大小）。
 * 说话听感上"啊/咿/呜"的区别在于元音的共振峰（formant）分布，而不是音量。
 * 本模块把 `_updateLipSync` 的能量标量替换为"共振峰 → 五元音权重"分析，
 * 让嘴不仅会"开多大"，还会"开成什么形状"。
 *
 * 算法思想（参考了开源社区的五元音 lip-sync 通行做法，思想不受著作权保护，
 * 此处为本仓库自主实现）：
 *   1. 从 AnalyserNode 频谱中定位第一、第二共振峰（F1/F2）的峰值位置；
 *   2. 在对数频率域度量当前 F1/F2 与各元音参考共振峰的距离，反比加权得到
 *      每个元音的连续权重（非 one-hot 硬分类）；
 *   3. 只取权重最高的两个元音混合（top-2），避免全部混合时被对网格形变
 *      最大的 `aa` 主导；
 *   4. 用"攻击快、释放慢"的不对称指数平滑让嘴快速张开、缓慢闭合，并在
 *      词内短暂停顿时保持嘴型，避免爆破音间隙造成的"突突"感。
 *
 * 与旧单通道实现的关系：本模块只负责"分析"，产出 { volume, weights }；
 * 表情写入与 mixer 防冲突仍由 VRMAnimation._updateLipSync 负责，保留了
 * 原有"每帧先清零其他口型再写入"的防 VRMA 轨道残留机制。
 *
 * 宿主 analyser 归属：本模块只读 analyser 的格式，绝不写它的配置。见 attach()。
 */

// 幂等自执行包装。
//
// 本文件同时出现在四条模块加载链里：vrm-init.js、mmd-init.js、
// model_manager 的 runtime-loaders.js（VRM 与 MMD 两个并行 IIFE 各一份）、
// character_card_manager 的 model-previews.js。其中 runtime-loaders.js 的两个
// IIFE 并行执行且不做 src 去重，同一个经典脚本会被 append 两次；顶层
// const/class 重复执行会抛 "Identifier 'VOWEL_FORMANTS' has already been
// declared"，整条模块链随之断掉。
//
// 把实现关进 IIFE 并在开头幂等短路后，重复加载退化成无害空转；顺带把
// CAP / ATTACK / RELEASE 这类通用名从全局作用域收回来。
(function initFormantLipSync() {
    'use strict';

    // 已由另一条加载链装载过，直接返回（重复 <script> 无害）。
    if (typeof window !== 'undefined' && typeof window.FormantLipSyncAnalyzer === 'function') {
        return;
    }

    // 五个元音的参考共振峰中心（Hz）。取人声大致 F1/F2 范围，
    // 只要沿两个频率轴保持正确相对顺序即可，不追求精确声学表——
    // 输出会被限幅并重度平滑，几十 Hz 的误差在视觉上不可察觉。
    const VOWEL_FORMANTS = Object.freeze({
        aa: { f1: 850, f2: 1400 }, // 开口
        ee: { f1: 550, f2: 2100 }, // 中前
        ih: { f1: 350, f2: 2700 }, // 闭前
        oh: { f1: 500, f2: 900 },  // 中后圆唇
        ou: { f1: 350, f2: 800 },  // 闭后圆唇
    });

    // 元音键（与 mouthExpressions / blendshape 名一致）。
    // 这是五元音键表的单一真相源：vrm-animation.js 与 mmd-expression.js 都从
    // window.VRM_LIPSYNC_VOWEL_KEYS 派生自己的键序，避免三处各写一份。
    const VOWEL_KEYS = Object.freeze(['aa', 'ee', 'ih', 'oh', 'ou']);

    // F1/F2 搜索窗（Hz）。
    //
    // F2 从 1000Hz 起搜，**不要**为了让 ou(800)/oh(900) 的参考中心落进窗内而下调。
    // 试过，是净回归，已回退：
    //
    //   动机看着成立——那两个中心在窗外，对数距离项恒大于零。但权重是按
    //   max 归一化的（见 sample()），"赢家恒为 1"，绝对距离大不妨碍夺冠；
    //   ou 与 oh 靠 F1 轴（350 / 500）就足以和展唇元音分开。所以那两个元音
    //   本来就没有"天然拿不到 winner"的问题，改动想解决的是个不存在的问题。
    //
    //   代价却是实的：语音频谱在 F1 之后单调下降，真实 F2 峰比 F1 低 10~25dB。
    //   窗底压到 700Hz 后，F1 的裙边在窗底往往比真正的 F2 峰还高，_peakBetween
    //   于是返回窗口最低那个 bin。实测 male /ɛ/（F1=530 B=60、F2=1840 B=110、
    //   F0=110、净 -6dB/oct、宿主 2048@48kHz）：byte@680=204 > byte@1840=194，
    //   F2 被测成 680Hz，/e/ 于是驱动 oh 口型；同一帧在 1000Hz 窗下测得 1875Hz，
    //   判定正确。扫 F1 带宽 {60,70,90,130} × 额外声门倾斜 {0,-6,-12} 共 12 组，
    //   10 组两窗判定分歧，几乎全是低窗更差，且 ou/oh 一格也没变好。
    //
    // 结论：1000Hz 这条线是在"避开 F1 裙边"和"够低能测到圆唇 F2"之间的既有折中，
    // 动它需要真机录音佐证，不能只看参考表的区间包含关系。
    const F1_MIN_HZ = 200;
    const F1_MAX_HZ = 1000;
    const F2_MIN_HZ = 1000;
    const F2_MAX_HZ = 3000;

    // 音量取样带宽（Hz）
    const BAND_MIN_HZ = 200;
    const BAND_MAX_HZ = 4000;

    // 峰值频率的下限。见 _peakBetween：低分辨率宿主下 bin 0 会让 log2(0) 变成
    // -Infinity，把五个元音权重一起打成 0。取一个远低于任何参考 F1 的值。
    const MIN_PEAK_HZ = 50;

    // 平滑与门限常数。攻击快于释放是真实发声的肌肉特性（嘴快速张开、缓慢闭合），
    // 反过来会产生廉价 lip-sync 的"橡皮嘴"感。
    const ATTACK = 50;          // 张开速率
    const RELEASE = 30;         // 闭合速率
    const CAP = 0.7;            // 单元音权重上限，防止嘴张到崩坏
    const SILENCE_GAIN = 0.05;  // 主导元音增益门限（weights 归一到"赢家为 1"，
                                // 故 winner 的投影值恒等于 amp，再单独判一次
                                // 原始音量是同一个条件的重复，只保留这一个门限）
    const IDLE_MS = 160;        // 词内间隙保持时长，超过才认定为真停顿

    // delta 在这里统一夹紧，调用点不必各自防御：VRM 与 MMD 两条渲染循环都把自己的
    // delta 直接传进来，非有限值会让指数平滑算出 NaN 并永久 latch 进 state
    // （NaN 参与任何运算仍是 NaN），此后每帧把 NaN 写进 blendshape。
    // 上界与 VRMAnimation.MAX_DELTA_THRESHOLD 对齐，防止切回后台标签页时嘴型瞬跳。
    // delta === 0 是合法输入：本帧不推进平滑。
    const MAX_DELTA = 0.1;
    const DEFAULT_DELTA = 0.016;

    // analyser.context 缺失时的 sampleRate 回退值，
    // 与 mmd-animation.getLipSyncValue 的同款防御取同一个数。
    const DEFAULT_SAMPLE_RATE = 48000;

    class FormantLipSyncAnalyzer {
        /**
         * @param {AnalyserNode} analyser 已接入发声链路的 analyser
         */
        constructor(analyser) {
            this.analyser = null;
            this.bins = null;
            this.nyquist = 0;
            // 平滑状态：五元音各自的当前权重
            this.state = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
            this.lastActiveAt = 0;
            // 每帧复用的中间缓冲。口型分析跑在 60fps 渲染循环里，每帧新建
            // 这几个对象是热路径上的无谓 GC 压力。
            //
            // 只复用不逃逸出本类的三个：update() 的返回值仍然每帧新建。
            // 返回值是唯一交到调用方手里的对象，复用它会制造 aliasing 陷阱——
            // 调用方留住两帧的返回值做对比时会发现它们是同一个引用。
            this._weights = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
            this._projected = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
            this._target = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
            if (analyser) this.attach(analyser);
        }

        /**
         * 绑定/换绑 analyser。
         *
         * 只读取宿主 analyser 的格式，绝不写它的 fftSize / smoothingTimeConstant。
         * 传进来的通常是 app-audio-playback 的 S.globalAnalyser——同一个节点还被
         * Live2D（app-audio-playback.startLipSync）、PNGTuber（pngtuber-core.startLipSync）
         * 与 MMD 旧单通道路径（mmd-animation.getLipSyncValue）共用，它们都在启动时
         * 按当时的 fftSize 预分配一次缓冲。在这里改配置会静默改掉别人的采样窗口，
         * 而且没有还原点。改为按宿主当前格式适配自己。
         */
        attach(analyser) {
            this.analyser = analyser || null;
            this.bins = null;
            this.nyquist = 0;
            if (!this.analyser) return;
            this._syncFormat();
        }

        /**
         * 按 analyser 当前格式对齐采样缓冲与 nyquist。宿主可能在运行期改 fftSize，
         * 故每帧调用一次；长度没变时不重新分配。
         * @returns {boolean} 缓冲是否可用
         */
        _syncFormat() {
            const analyser = this.analyser;
            if (!analyser) return false;
            const binCount = Number(analyser.frequencyBinCount);
            if (!Number.isFinite(binCount) || binCount <= 0) {
                this.bins = null;
                return false;
            }
            if (!this.bins || this.bins.length !== binCount) {
                this.bins = new Uint8Array(binCount);
            }
            // sampleRate 优先取 analyser 自己的 context；缺失时回退默认值。
            // 与 mmd-animation.getLipSyncValue 的防御对齐——测试替身与部分宿主
            // 实现并不保证 .context 存在，构造期抛异常会直接打断 startLipSync。
            const ctx = analyser.context;
            const rate = ctx ? Number(ctx.sampleRate) : NaN;
            this.nyquist = (Number.isFinite(rate) && rate > 0 ? rate : DEFAULT_SAMPLE_RATE) / 2;
            return true;
        }

        reset() {
            for (const key of VOWEL_KEYS) this.state[key] = 0;
            this.lastActiveAt = 0;
        }

        /** 把 Hz 夹到合法 bin 下标。 */
        _binOf(hz) {
            const n = this.bins.length;
            const raw = Math.floor((hz / this.nyquist) * n);
            if (!Number.isFinite(raw)) return 0;
            return Math.min(n - 1, Math.max(0, raw));
        }

        /**
         * 频段 [minHz,maxHz] 内的峰值所在频率（Hz）。
         *
         * 返回值下限夹到 MIN_PEAK_HZ：bin 宽度取决于宿主 fftSize，宿主给一个很小的
         * fftSize 时（MMD 自建 analyser 是 256）低频只剩两三个 bin，bin 0 的中心频率
         * 是 0Hz，一路传到 sample() 里就是 Math.log2(0 / v.f1) = -Infinity，五个元音
         * 权重全变 0、winner 退化成常量。夹一个下限比在下游到处判 Infinity 干净。
         */
        _peakBetween(minHz, maxHz) {
            const n = this.bins.length;
            const from = this._binOf(minHz);
            // to 不得小于 from：否则循环空转、返回值退化成窗口下界，
            // 且 _bandEnergy 的 (to - from + 1) 会变成 0 或负数。
            const to = Math.max(from, this._binOf(maxHz));
            let peak = -1;
            let at = from;
            for (let i = from; i <= to; i++) {
                if (this.bins[i] > peak) {
                    peak = this.bins[i];
                    at = i;
                }
            }
            return Math.max(MIN_PEAK_HZ, (at / n) * this.nyquist);
        }

        /** 频段 [minHz,maxHz] 的平均能量，归一到 0..1。 */
        _bandEnergy(minHz, maxHz) {
            const from = this._binOf(minHz);
            const to = Math.max(from, this._binOf(maxHz));
            let sum = 0;
            for (let i = from; i <= to; i++) sum += this.bins[i];
            return sum / (to - from + 1) / 255;
        }

        /**
         * 采样当前帧，产出连续五元音权重（未平滑）。
         * 内部接口：weights 指向每帧复用的缓冲，只应由 update() 当帧消费。
         * @returns {{volume:number, weights:Record<string,number>}}
         */
        sample() {
            this.analyser.getByteFrequencyData(this.bins);

            // getByteFrequencyData 已是 dB 刻度，bandEnergy 落在行为良好的 0..1。
            const volume = this._bandEnergy(BAND_MIN_HZ, BAND_MAX_HZ);

            const f1 = this._peakBetween(F1_MIN_HZ, F1_MAX_HZ);
            const f2 = this._peakBetween(F2_MIN_HZ, F2_MAX_HZ);

            // 连续权重而非硬分类：one-hot 没有次优项，会让 top-2 混合退化成单元音跳变。
            // 在对数频率域度量距离——共振峰感知上按倍乘变化（300->600 与 1200->2400 同级），
            // 线性度量会把所有元音不合理地压向 aa。
            const weights = this._weights;
            let max = 0;
            for (const key of VOWEL_KEYS) {
                const v = VOWEL_FORMANTS[key];
                const d1 = Math.log2(f1 / v.f1);
                const d2 = Math.log2(f2 / v.f2);
                const w = 1 / (d1 * d1 + d2 * d2 + 0.05); // +eps 防止正中时除零
                weights[key] = Number.isFinite(w) ? w : 0;
                if (weights[key] > max) max = weights[key];
            }
            // 归一化让"赢家为 1"，而非"总和为 1"——后者会让元音越接近权重越小，
            // 反而越确定嘴张得越小，方向相反。
            const norm = max || 1;
            for (const key of VOWEL_KEYS) weights[key] /= norm;

            return { volume, weights };
        }

        /**
         * 推进一帧，产出限幅、平滑后的五元音目标权重。
         * @param {number} delta 距上一帧的秒数（非有限值会被夹到一帧名义时长）
         * @returns {Record<string,number>} 各元音应写入的权重（上限 CAP）。
         *          每帧新建，调用方可以安全留存。
         */
        update(delta) {
            const out = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
            if (!this.analyser || !this._syncFormat()) {
                for (const key of VOWEL_KEYS) this.state[key] = 0;
                return out;
            }

            const step = Number.isFinite(delta)
                ? Math.min(Math.max(delta, 0), MAX_DELTA)
                : DEFAULT_DELTA;

            const { volume, weights } = this.sample();
            // 0.5 次幂（平方根）提升中低音量响应：平稳语音的 bandEnergy 很少顶到 1，
            // 高次幂会把日常说话的振幅进一步压小，导致嘴张不开。
            const amp = Math.min(volume * 0.9, 1) ** 0.5;

            const projected = this._projected;
            for (const key of VOWEL_KEYS) {
                projected[key] = weights[key] * amp;
            }

            // top-2 选择：只混合权重最高的两个元音。全部混合会被形变最大的 aa 主导。
            let winner = 'ih';
            let runner = 'ee';
            let winnerVal = -Infinity;
            let runnerVal = -Infinity;
            for (const key of VOWEL_KEYS) {
                const val = projected[key];
                if (val > winnerVal) {
                    runnerVal = winnerVal;
                    runner = winner;
                    winnerVal = val;
                    winner = key;
                } else if (val > runnerVal) {
                    runnerVal = val;
                    runner = key;
                }
            }

            // 词内短暂停顿保持嘴型（闭嘴于每个爆破音会显得口吃），
            // 超过 IDLE_MS 才认定为真停顿并闭嘴。
            //
            // 关键：idle 窗口内即使增益低于门限也不产生全零 target，
            // 而是保持当前 state 不变（target = state），避免爆破音间隙嘴部
            // 快速闭合→张开的抖动。只有持续 idle 超过 IDLE_MS 才真正归零。
            const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
            // 写成 !(x >= t) 而非 x < t：NaN 也要算作"低于门限"，
            // 否则异常帧会被当成有声、把 lastActiveAt 顶到当前时刻。
            const belowThreshold = !(winnerVal >= SILENCE_GAIN);
            if (!belowThreshold) this.lastActiveAt = now;
            const idleExpired = (now - this.lastActiveAt) > IDLE_MS;

            const target = this._target;
            for (const key of VOWEL_KEYS) target[key] = 0;
            if (idleExpired) {
                // 真停顿：全零 target → release 平滑闭嘴
            } else if (belowThreshold) {
                // idle 窗口内的短暂低音量：保持当前 state，不动
                for (const key of VOWEL_KEYS) target[key] = this.state[key];
            } else {
                target[winner] = Math.min(CAP, winnerVal);
                target[runner] = Math.min(CAP * 0.5, runnerVal * 0.6);
            }

            for (const key of VOWEL_KEYS) {
                const from = this.state[key];
                const to = target[key];
                // 帧率无关指数趋近：30fps 与 144fps 下嘴部运动一致。
                const rate = 1 - Math.exp(-(to > from ? ATTACK : RELEASE) * step);
                const next = from + (to - from) * rate;
                // 夹紧兼防 NaN：非有限值一旦落进 state 就会永久 latch，
                // 之后每帧把 NaN 写进 blendshape / morph influence。
                this.state[key] = Number.isFinite(next) ? Math.min(1, Math.max(0, next)) : 0;
                out[key] = (this.state[key] <= 0.01 ? 0 : this.state[key]);
            }
            return out;
        }
    }

    // 经典脚本挂载（与 window.VRMAnimation 风格一致），供 vrm-animation.js 使用。
    if (typeof window !== 'undefined') {
        window.FormantLipSyncAnalyzer = FormantLipSyncAnalyzer;
        window.VRM_LIPSYNC_VOWEL_KEYS = VOWEL_KEYS;
    }
})();
