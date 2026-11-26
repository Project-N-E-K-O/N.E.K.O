'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const VOICE_SILENCE_REMOVER_PATH = path.resolve(__dirname, '../../static/js/voice_silence_remover.js');

function createHarness({ fetchImpl } = {}) {
  const elements = new Map();
  const createdAnchors = [];
  const dtItems = [];

  function makeElement(id) {
    const el = {
      id,
      style: {},
      textContent: '',
      children: [],
      attributes: new Map(),
      files: null,
      setAttribute(k, v) { this.attributes.set(k, v); },
      addEventListener() {},
      querySelector() { return null; },
      appendChild(child) { this.children.push(child); },
      removeChild(child) {
        const idx = this.children.indexOf(child);
        if (idx >= 0) this.children.splice(idx, 1);
      },
      click() { this.clicked = true; },
    };
    return el;
  }

  for (const id of [
    'silenceProgressSection',
    'silenceDownloadSection',
    'silenceMd5',
    'silenceBtnCancel',
    'silenceBtnOriginal',
    'silenceBtnTrim',
    'audioFile',
    'fileNameDisplay',
    'silenceProgressFill',
    'silenceProgressText',
  ]) {
    elements.set(id, makeElement(id));
  }

  class MockFile {
    constructor(parts, name, options = {}) {
      this.parts = parts;
      this.name = name;
      this.type = options.type || '';
    }
  }

  class MockBlob {
    constructor(parts, options = {}) {
      this.parts = parts;
      this.type = options.type || '';
    }
  }

  class MockDataTransfer {
    constructor() {
      this._files = [];
      this.items = {
        add: (f) => {
          this._files.push(f);
          dtItems.push(f);
        },
      };
      this.files = this._files;
    }
  }

  class MockFormData {
    constructor() { this.entries = []; }
    append(k, v) { this.entries.push([k, v]); }
  }

  const documentObj = {
    body: makeElement('body'),
    getElementById(id) { return elements.get(id) || null; },
    querySelector() { return null; },
    createElement(tag) {
      const el = makeElement(tag);
      if (tag === 'a') createdAnchors.push(el);
      return el;
    },
    addEventListener() {},
  };

  const context = {
    window: null,
    document: documentObj,
    console: { log() {}, warn() {}, error() {} },
    File: MockFile,
    Blob: MockBlob,
    DataTransfer: MockDataTransfer,
    FormData: MockFormData,
    URL: {
      createObjectURL: () => 'blob:mock-wav',
      revokeObjectURL() {},
    },
    crypto: { randomUUID: () => 'uuid-1' },
    atob: (b64) => Buffer.from(b64, 'base64').toString('binary'),
    fetch: fetchImpl || (async () => ({ ok: true, json: async () => ({}) })),
    setTimeout,
    clearTimeout,
    setInterval() { return 1; },
    clearInterval() {},
    Promise,
    Uint8Array,
    ArrayBuffer,
    String,
  };
  context.window = context;

  const source = fs.readFileSync(VOICE_SILENCE_REMOVER_PATH, 'utf8');
  vm.runInNewContext(source, context, { filename: VOICE_SILENCE_REMOVER_PATH });
  return { context, elements, createdAnchors, dtItems, MockFile };
}

test('F0054: normalizes trimmed WAV filename extensions for upload and download', async () => {
  const wavBase64 = Buffer.from('RIFF....WAVEfmt ').toString('base64');
  for (const [rawFilename, expectedFilename] of [
    ['trimmed_sample.m4a', 'trimmed_sample.wav'],
    ['trimmed_clip.MP3', 'trimmed_clip.wav'],
    ['trimmed_no_ext', 'trimmed_no_ext.wav'],
    ['trimmed_ok.WAV', 'trimmed_ok.wav'],
    ['', 'trimmed_audio.wav'],
  ]) {
    const harness = createHarness({
      fetchImpl: async (url) => {
        if (url === '/api/characters/audio/trim_silence') {
          return {
            ok: true,
            json: async () => ({
              success: true,
              has_changes: true,
              audio_base64: wavBase64,
              filename: rawFilename,
              md5: 'deadbeef',
            }),
          };
        }
        return { ok: true, json: async () => ({}) };
      },
    });

    const origFile = new harness.MockFile(['audio'], 'sample.m4a', { type: 'audio/mp4' });
    await harness.context.onAudioFileSelected(origFile);
    await harness.context.useTrimmedAudio();

    const audioInput = harness.elements.get('audioFile');
    assert.equal(audioInput.files.length, 1);
    assert.equal(audioInput.files[0].name, expectedFilename);
    assert.equal(audioInput.files[0].type, 'audio/wav');
    assert.ok(harness.elements.get('fileNameDisplay').textContent.endsWith(expectedFilename));

    harness.context.downloadTrimmedAudio();
    assert.equal(harness.createdAnchors.at(-1).download, expectedFilename);
  }
});
