import type { LocalAvatarToolLimits } from './localTools';

export type AvatarToolImageValidationIssue = 'invalid' | 'too-large' | 'too-many-pixels';

const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10] as const;

function readFilePrefix(file: File, length: number): Promise<Uint8Array> {
  const source = file.slice(0, length) as Blob & { arrayBuffer?: () => Promise<ArrayBuffer> };
  if (typeof source.arrayBuffer === 'function') {
    return source.arrayBuffer().then(buffer => new Uint8Array(buffer));
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error ?? new Error('file_read_failed'));
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.readAsArrayBuffer(source);
  });
}

export async function validateAvatarToolPng(
  file: File,
  limits: Pick<LocalAvatarToolLimits, 'maxImageBytes' | 'maxImagePixels'>,
): Promise<AvatarToolImageValidationIssue | null> {
  if (
    !file.name.toLocaleLowerCase('en-US').endsWith('.png')
    || (file.type !== '' && file.type !== 'image/png')
  ) return 'invalid';
  if (file.size > limits.maxImageBytes) return 'too-large';

  try {
    const bytes = await readFilePrefix(file, 24);
    if (
      bytes.length < 24
      || PNG_SIGNATURE.some((value, index) => bytes[index] !== value)
      || bytes[12] !== 73
      || bytes[13] !== 72
      || bytes[14] !== 68
      || bytes[15] !== 82
    ) return 'invalid';
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const width = view.getUint32(16, false);
    const height = view.getUint32(20, false);
    if (width <= 0 || height <= 0 || width > Math.floor(limits.maxImagePixels / height)) {
      return 'too-many-pixels';
    }
  } catch {
    return 'invalid';
  }
  return null;
}
