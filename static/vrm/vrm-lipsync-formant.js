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
 */

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

// 元音键（与 mouthExpressions / blendshape 名一致）
const VOWEL_KEYS = Object.freeze(['aa', 'ee', 'ih', 'oh', 'ou']);

// 平滑与门限常数。攻击快于释放是真实发声的肌肉特性（嘴快速张开、缓慢闭合），
// 反过来会产生廉价 lip-sync 的"橡皮嘴"感。
const ATTACK = 50;          // 张开速率
const RELEASE = 30;         // 闭合速率
const CAP = 0.7;            // 单元音权重上限，防止嘴张到崩坏
const SILENCE_VOL = 0.04;   // 音量静音门限
const SILENCE_GAIN = 0.05;  // 主导元音增益门限
const IDLE_MS = 160;        // 词内间隙保持时长，超过才认定为真停顿

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
        if (analyser) this.attach(analyser);
    }

    /**
     * 绑定/换绑 analyser，并按需配置 FFT。
     */
    attach(analyser) {
        this.analyser = analyser;
        if (!analyser) {
            this.bins = null;
            return;
        }
        // 1024 在 44.1kHz 下给 ~43Hz 的分辨率，足以分离 F1 与 F2，又足够便宜可每帧跑。
        // smoothingTimeConstant 稳定峰值，避免在相邻 bin 间抖动导致嘴部颤动。
        analyser.fftSize = 1024;
        analyser.smoothingTimeConstant = 0.3;
        this.bins = new Uint8Array(analyser.frequencyBinCount);
        this.nyquist = analyser.context.sampleRate / 2;
    }

    reset() {
        this.state = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
        this.lastActiveAt = 0;
    }

    /** 频段 [minHz,maxHz] 内的峰值所在频率（Hz）。 */
    _peakBetween(minHz, maxHz) {
        const n = this.bins.length;
        const from = Math.max(0, Math.floor((minHz / this.nyquist) * n));
        const to = Math.min(n - 1, Math.ceil((maxHz / this.nyquist) * n));
        let peak = -1;
        let at = from;
        for (let i = from; i <= to; i++) {
            if (this.bins[i] > peak) {
                peak = this.bins[i];
                at = i;
            }
        }
        return (at / n) * this.nyquist;
    }

    /** 频段 [minHz,maxHz] 的平均能量，归一到 0..1。 */
    _bandEnergy(minHz, maxHz) {
        const n = this.bins.length;
        const from = Math.max(0, Math.floor((minHz / this.nyquist) * n));
        const to = Math.min(n - 1, Math.ceil((maxHz / this.nyquist) * n));
        let sum = 0;
        for (let i = from; i <= to; i++) sum += this.bins[i];
        return sum / (to - from + 1) / 255;
    }

    /**
     * 采样当前帧，产出连续五元音权重（未平滑）。
     * @returns {{volume:number, weights:Record<string,number>}}
     */
    sample() {
        this.analyser.getByteFrequencyData(this.bins);

        // getByteFrequencyData 已是 dB 刻度，bandEnergy 落在行为良好的 0..1。
        const volume = this._bandEnergy(200, 4000);

        const f1 = this._peakBetween(200, 1000);
        const f2 = this._peakBetween(1000, 3000);

        // 连续权重而非硬分类：one-hot 没有次优项，会让 top-2 混合退化成单元音跳变。
        // 在对数频率域度量距离——共振峰感知上按倍乘变化（300->600 与 1200->2400 同级），
        // 线性度量会把所有元音不合理地压向 aa。
        const weights = {};
        let max = 0;
        for (const key of VOWEL_KEYS) {
            const v = VOWEL_FORMANTS[key];
            const d1 = Math.log2(f1 / v.f1);
            const d2 = Math.log2(f2 / v.f2);
            const w = 1 / (d1 * d1 + d2 * d2 + 0.05); // +eps 防止正中时除零
            weights[key] = w;
            if (w > max) max = w;
        }
        // 归一化让"赢家为 1"，而非"总和为 1"——后者会让元音越接近权重越小，
        // 反而越确定嘴张得越小，方向相反。
        for (const key of VOWEL_KEYS) weights[key] /= max || 1;

        return { volume, weights };
    }

    /**
     * 推进一帧，产出限幅、平滑后的五元音目标权重。
     * @param {number} delta 距上一帧的秒数
     * @returns {Record<string,number>} 各元音应写入的权重（上限由 CAP 约束）
     */
    update(delta) {
        if (!this.analyser || !this.bins) {
            return { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
        }

        const { volume, weights } = this.sample();
        // 0.5 次幂（平方根）提升中低音量响应：平稳语音的 bandEnergy 很少顶到 1，
        // 高次幂会把日常说话的振幅进一步压小，导致嘴张不开。
        const amp = Math.min(volume * 0.9, 1) ** 0.5;

        const projected = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
        for (const key of VOWEL_KEYS) {
            projected[key] = (weights[key] ?? 0) * amp;
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
        // 关键：idle 窗口内即使 amp/winnerVal 低于门限也不产生全零 target，
        // 而是保持当前 state 不变（target = state），避免爆破音间隙嘴部
        // 快速闭合→张开的抖动。只有持续 idle 超过 IDLE_MS 才真正归零。
        const now = (typeof performance !== 'undefined' ? performance.now() : Date.now());
        const belowThreshold = amp < SILENCE_VOL || winnerVal < SILENCE_GAIN;
        if (!belowThreshold) this.lastActiveAt = now;
        const idleExpired = (now - this.lastActiveAt) > IDLE_MS;

        const target = { aa: 0, ee: 0, ih: 0, oh: 0, ou: 0 };
        if (idleExpired) {
            // 真停顿：全零 target → release 平滑闭嘴
        } else if (belowThreshold) {
            // idle 窗口内的短暂低音量：保持当前 state，不动
            for (const key of VOWEL_KEYS) target[key] = this.state[key];
        } else {
            target[winner] = Math.min(CAP, winnerVal);
            target[runner] = Math.min(CAP * 0.5, runnerVal * 0.6);
        }

        const out = {};
        for (const key of VOWEL_KEYS) {
            const from = this.state[key];
            const to = target[key];
            // 帧率无关指数趋近：30fps 与 144fps 下嘴部运动一致。
            const rate = 1 - Math.exp(-(to > from ? ATTACK : RELEASE) * delta);
            this.state[key] = from + (to - from) * rate;
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
