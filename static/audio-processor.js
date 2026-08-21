// audio-processor.js
class AudioProcessor extends AudioWorkletProcessor {
    constructor(options) {
        super();

        // 获取采样率信息
        const processorOptions = options.processorOptions || {};
        this.originalSampleRate = processorOptions.originalSampleRate || 48000;
        this.targetSampleRate = processorOptions.targetSampleRate || 48000; // 默认不降采样

        // 计算重采样比率
        this.resampleRatio = this.targetSampleRate / this.originalSampleRate;
        this.needsResampling = this.resampleRatio !== 1.0;
        this.needsLowPass = this.targetSampleRate < this.originalSampleRate;
        this.lowPassTaps = this.needsLowPass ? this.createLowPassFilter() : null;
        this.lowPassHistory = this.lowPassTaps
            ? new Float32Array(this.lowPassTaps.length - 1)
            : null;
        this.lowPassHistoryFilled = 0;

        // 缓冲区大小根据目标采样率调整
        // 48kHz: 480 samples (10ms, RNNoise frame size)
        // 16kHz: 512 samples (~32ms)
        this.bufferSize = this.targetSampleRate === 48000 ? 480 : 512;
        this.buffer = new Float32Array(this.bufferSize);
        this.bufferIndex = 0;

        // 用于重采样的固定输入缓冲区
        this.resampleInputSize = this.needsResampling
            ? Math.ceil(this.bufferSize / this.resampleRatio)
            : 0;
        this.resampleBuffer = this.needsResampling
            ? new Float32Array(this.resampleInputSize)
            : null;
        this.resampleBufferIndex = 0;

        console.log(`AudioProcessor初始化: 原始采样率=${this.originalSampleRate}Hz, 目标采样率=${this.targetSampleRate}Hz, 需要重采样=${this.needsResampling}`);
    }

    process(inputs, outputs, parameters) {
        // 获取输入数据 (假设是单声道)
        const input = inputs[0][0];

        if (!input || input.length === 0) {
            return true;
        }

        if (this.needsResampling) {
            // 需要重采样的情况（如16kHz目标）
            for (let i = 0; i < input.length; i++) {
                this.resampleBuffer[this.resampleBufferIndex++] = input[i];
                if (this.resampleBufferIndex >= this.resampleInputSize) {
                    const resampledData = this.resampleAudio(this.resampleBuffer);
                    const pcmData = this.floatToPcm16(resampledData);
                    this.port.postMessage(pcmData);
                    this.resampleBufferIndex = 0;
                }
            }
        } else {
            // 不需要重采样，直接处理（48kHz passthrough）
            for (let i = 0; i < input.length; i++) {
                this.buffer[this.bufferIndex++] = input[i];

                if (this.bufferIndex >= this.bufferSize) {
                    const pcmData = this.floatToPcm16(this.buffer);
                    this.port.postMessage(pcmData);
                    this.bufferIndex = 0;
                }
            }
        }

        return true;
    }

    // Float32 转 Int16 PCM
    floatToPcm16(floatData) {
        const pcmData = new Int16Array(floatData.length);
        for (let i = 0; i < floatData.length; i++) {
            pcmData[i] = Math.max(-1, Math.min(1, floatData[i])) * 0x7FFF;
        }
        return pcmData;
    }

    createLowPassFilter() {
        const tapCount = 31;
        const half = Math.floor(tapCount / 2);
        const cutoff = Math.min(0.5, this.targetSampleRate / this.originalSampleRate / 2);
        const taps = new Float32Array(tapCount);
        let total = 0;

        for (let i = 0; i < tapCount; i++) {
            const offset = i - half;
            const sinc = offset === 0
                ? 2 * cutoff
                : Math.sin(2 * Math.PI * cutoff * offset) / (Math.PI * offset);
            const window = 0.54 - 0.46 * Math.cos((2 * Math.PI * i) / (tapCount - 1));
            const value = sinc * window;
            taps[i] = value;
            total += value;
        }

        for (let i = 0; i < taps.length; i++) {
            taps[i] /= total;
        }
        return taps;
    }

    applyLowPassFilter(audioData) {
        if (!this.lowPassTaps) {
            return audioData;
        }
        const taps = this.lowPassTaps;
        const history = this.lowPassHistory;
        const historyLength = this.lowPassHistoryFilled;
        const inputLength = audioData.length;
        const result = new Float32Array(inputLength);

        for (let i = 0; i < inputLength; i++) {
            let sample = 0;
            for (let tap = 0; tap < taps.length; tap++) {
                sample += this.lowPassSampleAt(
                    history,
                    historyLength,
                    audioData,
                    i - tap
                ) * taps[tap];
            }
            result[i] = sample;
        }

        this.updateLowPassHistory(history, historyLength, audioData);
        return result;
    }

    lowPassSampleAt(history, historyLength, audioData, index) {
        if (index >= 0) {
            return audioData[index];
        }
        const historyIndex = historyLength + index;
        return historyIndex >= 0 ? history[historyIndex] : 0;
    }

    updateLowPassHistory(history, historyLength, audioData) {
        const historyLimit = history.length;
        const combinedLength = historyLength + audioData.length;
        const keep = Math.min(historyLimit, combinedLength);
        const inputKeep = Math.min(audioData.length, keep);
        const historyKeep = keep - inputKeep;

        for (let i = 0; i < historyKeep; i++) {
            history[i] = history[historyLength - historyKeep + i];
        }
        for (let i = 0; i < inputKeep; i++) {
            history[historyKeep + i] = audioData[audioData.length - inputKeep + i];
        }
        this.lowPassHistoryFilled = keep;
    }

    // 低通抗混叠后再做线性插值重采样
    resampleAudio(audioData) {
        const sourceData = this.applyLowPassFilter(audioData);
        const inputLength = sourceData.length;
        const outputLength = Math.floor(inputLength * this.resampleRatio);
        const result = new Float32Array(outputLength);

        for (let i = 0; i < outputLength; i++) {
            const position = i / this.resampleRatio;
            const index = Math.floor(position);
            const fraction = position - index;

            // 线性插值
            if (index + 1 < inputLength) {
                result[i] = sourceData[index] * (1 - fraction) + sourceData[index + 1] * fraction;
            } else {
                result[i] = sourceData[index];
            }
        }

        return result;
    }
}

registerProcessor('audio-processor', AudioProcessor);
