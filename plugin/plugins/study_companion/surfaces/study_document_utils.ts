export const STUDY_DOCUMENT_MAX_BYTES = 512 * 1024;
export const STUDY_DOCUMENT_DIRECT_MAX_ESTIMATED_TOKENS = 48_000;
export const STUDY_DOCUMENT_MAX_ESTIMATED_TOKENS = 160_000;
export const STUDY_DOCUMENT_TARGET_CHUNK_TOKENS = 10_000;

const SUPPORTED_EXTENSIONS = new Map([
  ['.txt', 'text/plain'],
  ['.md', 'text/markdown'],
  ['.markdown', 'text/markdown'],
]);

export type StudyDocument = {
  name: string;
  type: 'text/plain' | 'text/markdown';
  size: number;
  encoding: string;
  chars: number;
  estimatedTokens: number;
  modified: boolean;
};

export type StudyDocumentAnalysisKind =
  | 'auto'
  | 'literary_book'
  | 'nonfiction_book'
  | 'design_document'
  | 'academic_paper'
  | 'exam'
  | 'course_material'
  | 'general_notes';

export const STUDY_DOCUMENT_ANALYSIS_KINDS: readonly StudyDocumentAnalysisKind[] = [
  'auto',
  'literary_book',
  'nonfiction_book',
  'design_document',
  'academic_paper',
  'exam',
  'course_material',
  'general_notes',
];

export type StudyDocumentErrorCode =
  | 'multiple_files'
  | 'unsupported_type'
  | 'file_too_large'
  | 'empty_document'
  | 'binary_document'
  | 'encoding_unrecognized'
  | 'unsafe_document_content'
  | 'document_too_long';

const MAX_DOCUMENT_LINE_CHARS = 32_768;
const EMBEDDED_DATA_URI = /data:[^\s,;]+(?:;[^\s,;=]+)*;base64,[A-Za-z0-9+/=]{4096,}/i;
const EMBEDDED_BASE64_LINE = /^[A-Za-z0-9+/=]{8192,}$/;

export class StudyDocumentError extends Error {
  readonly code: StudyDocumentErrorCode;

  constructor(code: StudyDocumentErrorCode) {
    super(code);
    this.name = 'StudyDocumentError';
    this.code = code;
  }
}

function assertActive(signal?: AbortSignal) {
  if (signal?.aborted) {
    throw new DOMException('Aborted', 'AbortError');
  }
}

function normalizedExtension(name: string) {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

function documentType(file: File): StudyDocument['type'] {
  const supported = SUPPORTED_EXTENSIONS.get(normalizedExtension(file.name));
  if (!supported) {
    throw new StudyDocumentError('unsupported_type');
  }
  const reportedType = String(file.type || '').toLowerCase();
  if (reportedType && !['text/plain', 'text/markdown', 'application/octet-stream'].includes(reportedType)) {
    throw new StudyDocumentError('unsupported_type');
  }
  return supported as StudyDocument['type'];
}

function decode(bytes: Uint8Array): { text: string; encoding: string } {
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) {
    return {
      text: new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(3)),
      encoding: 'UTF-8 BOM',
    };
  }
  if (bytes[0] === 0xff && bytes[1] === 0xfe) {
    return {
      text: new TextDecoder('utf-16le', { fatal: true }).decode(bytes.subarray(2)),
      encoding: 'UTF-16 LE',
    };
  }
  if (bytes[0] === 0xfe && bytes[1] === 0xff) {
    return {
      text: new TextDecoder('utf-16be', { fatal: true }).decode(bytes.subarray(2)),
      encoding: 'UTF-16 BE',
    };
  }
  try {
    return {
      text: new TextDecoder('utf-8', { fatal: true }).decode(bytes),
      encoding: 'UTF-8',
    };
  } catch {
    try {
      return {
        text: new TextDecoder('gb18030', { fatal: true }).decode(bytes),
        encoding: 'GB18030',
      };
    } catch {
      throw new StudyDocumentError('encoding_unrecognized');
    }
  }
}

function validateText(text: string, bytes: Uint8Array, encoding: string) {
  if (!text.trim()) {
    throw new StudyDocumentError('empty_document');
  }
  const utf16 = encoding.startsWith('UTF-16');
  if (!utf16 && bytes.length > 0) {
    let nulBytes = 0;
    for (const value of bytes) {
      if (value === 0) nulBytes += 1;
    }
    if (nulBytes / bytes.length > 0.005) {
      throw new StudyDocumentError('binary_document');
    }
  }
  let controls = 0;
  let replacements = 0;
  for (const char of text) {
    const code = char.codePointAt(0) || 0;
    if (char === '\ufffd') replacements += 1;
    if ((code < 32 && char !== '\n' && char !== '\r' && char !== '\t') || code === 127) {
      controls += 1;
    }
  }
  const chars = Math.max(1, Array.from(text).length);
  if (controls / chars > 0.01 || replacements / chars > 0.002) {
    throw new StudyDocumentError('binary_document');
  }
  if (EMBEDDED_DATA_URI.test(text) || text.split(/\r?\n/).some((line) => (
    line.length > MAX_DOCUMENT_LINE_CHARS || EMBEDDED_BASE64_LINE.test(line.trim())
  ))) {
    throw new StudyDocumentError('unsafe_document_content');
  }
}

export function estimateDocumentTokens(text: string) {
  return Math.ceil(new TextEncoder().encode(text).byteLength / 3);
}

export function estimatedDocumentAnalysisMode(tokens: number): 'direct' | 'chunked' | 'over_limit' {
  if (tokens <= STUDY_DOCUMENT_DIRECT_MAX_ESTIMATED_TOKENS) return 'direct';
  if (tokens <= STUDY_DOCUMENT_MAX_ESTIMATED_TOKENS) return 'chunked';
  return 'over_limit';
}

export function estimateDocumentChunkCount(tokens: number) {
  return Math.max(1, Math.ceil(tokens / STUDY_DOCUMENT_TARGET_CHUNK_TOKENS));
}

export function metadataForEditedDocument(document: StudyDocument, text: string): StudyDocument {
  const size = new TextEncoder().encode(text).byteLength;
  return {
    ...document,
    size,
    chars: Array.from(text).length,
    estimatedTokens: estimateDocumentTokens(text),
    modified: true,
  };
}

export async function readStudyDocument(file: File, signal?: AbortSignal): Promise<{
  document: StudyDocument;
  text: string;
}> {
  assertActive(signal);
  const type = documentType(file);
  if (file.size > STUDY_DOCUMENT_MAX_BYTES) {
    throw new StudyDocumentError('file_too_large');
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  assertActive(signal);
  const decoded = decode(bytes);
  validateText(decoded.text, bytes, decoded.encoding);
  const estimatedTokens = estimateDocumentTokens(decoded.text);
  return {
    text: decoded.text,
    document: {
      name: file.name.slice(0, 255),
      type,
      size: file.size,
      encoding: decoded.encoding,
      chars: Array.from(decoded.text).length,
      estimatedTokens,
      modified: false,
    },
  };
}

export function oneStudyDocument(files: FileList | File[]) {
  const list = Array.from(files);
  if (list.length !== 1) {
    throw new StudyDocumentError('multiple_files');
  }
  return list[0];
}
