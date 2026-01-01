let cachedUrl: string | null = null;

/**
 * 生成 AudioWorklet 模块 URL（Blob URL）。
 *
 * 这样 WebApp/UMD 都不依赖固定的 `/static/audio-processor.js` 路径，
 * 同时保持与旧版逻辑一致（支持可选重采样 + Int16 帧输出）。
 */
export function getAudioProcessorWorkletUrl(): string {
  if (cachedUrl) return cachedUrl;

  // 注意：worklet 运行在 AudioWorkletGlobalScope，需要纯 JS 字符串。
  const source = `
class AudioProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const processorOptions = (options && options.processorOptions) || {};
    this.originalSampleRate = processorOptions.originalSampleRate || 48000;
    this.targetSampleRate = processorOptions.targetSampleRate || 48000;
    this.resampleRatio = this.targetSampleRate / this.originalSampleRate;
    this.needsResampling = this.resampleRatio !== 1.0;
    this._hasReportedError = false;

    // 旧版约定：
    // - 48kHz: 480 samples (10ms)
    // - 16kHz: 512 samples (~32ms)
    this.bufferSize = this.targetSampleRate === 48000 ? 480 : 512;
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;
    this.tempBuffer = [];
  }

  process(inputs) {
    try {
      const input = inputs[0] && inputs[0][0];
      if (!input || input.length === 0) return true;

      if (this.needsResampling) {
        // concat Float32 -> Array
        for (let i = 0; i < input.length; i++) this.tempBuffer.push(input[i]);

        const requiredSamples = Math.ceil(this.bufferSize / this.resampleRatio);
        if (this.tempBuffer.length >= requiredSamples) {
          const samplesNeeded = Math.min(requiredSamples, this.tempBuffer.length);
          const samplesToProcess = this.tempBuffer.slice(0, samplesNeeded);
          this.tempBuffer = this.tempBuffer.slice(samplesNeeded);

          const resampledData = this.resampleAudio(samplesToProcess);
          const pcmData = this.floatToPcm16(resampledData);
          this.port.postMessage(pcmData);
        }
      } else {
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
    } catch (error) {
      // AudioWorklet 在独立线程中运行：异常若不处理可能导致音频链路中断且难以排查。
      // 这里捕获后通过 port 回传到主线程，保持 worklet 持续运行。
      if (!this._hasReportedError) {
        this._hasReportedError = true;
        let name = "";
        let message = "AudioWorklet process error";
        let stack = "";
        try {
          if (error && typeof error === "object") {
            name = (error && error.name) ? String(error.name) : "";
            message = (error && error.message) ? String(error.message) : String(error);
            stack = (error && error.stack) ? String(error.stack) : "";
          } else {
            message = String(error);
          }
        } catch (_e) {
          // ignore
        }
        try {
          this.port.postMessage({ type: "error", name, message, stack });
        } catch (_e) {
          // ignore
        }
      }
      return true;
    }
  }

  floatToPcm16(floatData) {
    const pcmData = new Int16Array(floatData.length);
    for (let i = 0; i < floatData.length; i++) {
      const v = Math.max(-1, Math.min(1, floatData[i]));
      pcmData[i] = v * 0x7FFF;
    }
    return pcmData;
  }

  // Linear interpolation: performance-first for AudioWorklet real-time thread (no heavy deps).
  // Downsampling (e.g. 48k→16k) may introduce aliasing/distortion; acceptable for voice calls.
  // For higher-quality resampling (sinc/filters), handle outside the worklet (e.g. server-side).
  resampleAudio(audioData) {
    const inputLength = audioData.length;
    const outputLength = Math.floor(inputLength * this.resampleRatio);
    const result = new Float32Array(outputLength);
    for (let i = 0; i < outputLength; i++) {
      const position = i / this.resampleRatio;
      const index = Math.floor(position);
      const fraction = position - index;
      if (index + 1 < inputLength) {
        result[i] = audioData[index] * (1 - fraction) + audioData[index + 1] * fraction;
      } else {
        result[i] = audioData[index];
      }
    }
    return result;
  }
}

registerProcessor('audio-processor', AudioProcessor);
`;

  const blob = new Blob([source], { type: "application/javascript" });
  cachedUrl = URL.createObjectURL(blob);
  return cachedUrl;
}

