/** Local PCM recording. No network operations occur during capture. */
export const AUDIO_SAMPLE_RATE = 16000;
export const AUDIO_MAX_SAMPLES = AUDIO_SAMPLE_RATE * 30;

export type AudioMessagePayload = {
  requestId: string;
  target: string;
  audio: string;
  sampleRateHz: number;
  saveLocal?: boolean;
};

export type Recording = {
  stop: () => Promise<Int16Array>;
  cancel: () => void;
};

type MicrophoneHost = Window & {
  appState?: { selectedMicrophoneId?: string | null };
  nekoMicrophoneInput?: {
    open: (
      devices: MediaDevices, deviceId: string | null, isCurrent: () => boolean, constraints: MediaTrackConstraints,
    ) => Promise<{ stream: MediaStream }>;
  };
};

export async function recordAudio(
  onLimit: () => void,
  isCurrent: () => boolean,
): Promise<Recording> {
  let stream: MediaStream | undefined;
  let context: AudioContext | undefined;
  let source: MediaStreamAudioSourceNode | undefined;
  let worklet: AudioWorkletNode | undefined;
  let closed = false;
  let samples = 0;
  const chunks: Int16Array[] = [];
  let flush: (() => void) | undefined;

  const release = () => {
    if (closed) return;
    closed = true;
    stream?.getTracks().forEach(track => track.stop());
    source?.disconnect();
    if (worklet) {
      worklet.port.postMessage({ type: 'shutdown' });
      worklet.port.onmessage = null;
      worklet.disconnect();
    }
    void context?.close().catch(() => {});
    flush?.();
  };
  const check = () => {
    if (!isCurrent()) throw new DOMException('Recording cancelled', 'AbortError');
  };
  try {
    const host = window as MicrophoneHost;
    const deviceId = host.appState?.selectedMicrophoneId || null;
    const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true };
    if (host.nekoMicrophoneInput) {
      ({ stream } = await host.nekoMicrophoneInput.open(navigator.mediaDevices, deviceId, isCurrent, audio));
    } else {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: deviceId ? { ...audio, deviceId: { exact: deviceId } } : audio,
      });
    }
    check();
    context = new AudioContext();
    await context.audioWorklet.addModule('/static/audio-processor.js');
    check();
    worklet = new AudioWorkletNode(context, 'audio-processor', {
      processorOptions: { originalSampleRate: context.sampleRate, targetSampleRate: AUDIO_SAMPLE_RATE },
    });
    worklet.port.onmessage = event => {
      if (closed || !isCurrent()) return;
      const last = event.data?.type === 'flush_complete';
      const data: Int16Array = last ? event.data.pcmData : event.data;
      if (data instanceof Int16Array && samples < AUDIO_MAX_SAMPLES) {
        const chunk = data.slice(0, AUDIO_MAX_SAMPLES - samples);
        chunks.push(chunk);
        samples += chunk.length;
        if (samples === AUDIO_MAX_SAMPLES) onLimit();
      }
      if (last) flush?.();
    };
    source = context.createMediaStreamSource(stream);
    source.connect(worklet);
    // The worklet has a silent output, keeping capture scheduled without echo.
    worklet.connect(context.destination);
    await context.resume();
    check();
    let stopped: Promise<Int16Array> | undefined;
    return {
      cancel: release,
      stop: () => {
        if (stopped) return stopped;
        stopped = (async () => {
          // Stop the physical device immediately, then collect its last PCM block.
          stream?.getTracks().forEach(track => track.stop());
          await new Promise<void>(resolve => {
            const timer = setTimeout(resolve, 300);
            flush = () => { clearTimeout(timer); resolve(); };
            worklet?.port.postMessage({ type: 'flush' });
          });
          release();
          const pcm = new Int16Array(samples);
          let offset = 0;
          for (const chunk of chunks) {
            pcm.set(chunk, offset);
            offset += chunk.length;
          }
          return pcm;
        })();
        return stopped;
      },
    };
  } catch (error) {
    release();
    throw error;
  }
}

export function recordingBytes(pcm: Int16Array): Uint8Array {
  const bytes = new Uint8Array(pcm.length * 2);
  const view = new DataView(bytes.buffer);
  pcm.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  return bytes;
}

export function recordingBase64(pcm: Int16Array): string {
  const bytes = recordingBytes(pcm);
  let text = '';
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    text += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(text);
}

export function recordingWav(pcm: Int16Array): Blob {
  const bytes = recordingBytes(pcm);
  const header = new ArrayBuffer(44);
  const view = new DataView(header);
  const write = (offset: number, text: string) => {
    [...text].forEach((character, index) => view.setUint8(offset + index, character.charCodeAt(0)));
  };
  write(0, 'RIFF');
  view.setUint32(4, 36 + bytes.length, true);
  write(8, 'WAVEfmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, AUDIO_SAMPLE_RATE, true);
  view.setUint32(28, AUDIO_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, 'data');
  view.setUint32(40, bytes.length, true);
  return new Blob([header, bytes.slice().buffer], { type: 'audio/wav' });
}
