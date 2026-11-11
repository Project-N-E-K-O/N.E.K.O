import { vi, it, expect } from 'vitest';
import { recordAudio, recordingWav } from './audioRecording';

it('releases a late getUserMedia stream after its owner was cancelled', async () => {
  let resolve!: (stream: MediaStream) => void;
  const stop = vi.fn();
  const getUserMedia = vi.fn(() => new Promise<MediaStream>(done => { resolve = done; }));
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
  let current = true;
  const result = recordAudio(() => {}, () => current);
  current = false;
  resolve({ getTracks: () => [{ stop }] } as unknown as MediaStream);
  await expect(result).rejects.toMatchObject({ name: 'AbortError' });
  expect(stop).toHaveBeenCalledTimes(1);
});

it('encodes little-endian PCM in a mono 16kHz preview WAV', async () => {
  const wav = recordingWav(new Int16Array([1, -2, 32767, -32768]));
  expect(wav.type).toBe('audio/wav');
  expect(wav.size).toBe(52);
  const bytes = await new Promise<ArrayBuffer>(resolve => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as ArrayBuffer);
    reader.readAsArrayBuffer(wav);
  });
  const view = new DataView(bytes);
  expect(view.getUint32(24, true)).toBe(16000);
  expect(view.getUint16(22, true)).toBe(1);
  expect(view.getInt16(46, true)).toBe(-2);
  expect(view.getInt16(50, true)).toBe(-32768);
});
