'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const MEMORY_BROWSER_PATH = path.resolve(__dirname, '../../static/js/memory_browser.js');
const EN_LOCALE_PATH = path.resolve(__dirname, '../../static/locales/en.json');

function extractFunction(source, name) {
  const asyncStart = source.indexOf(`async function ${name}(`);
  if (asyncStart !== -1) {
    return extractBraceBlock(source, asyncStart);
  }
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `Could not find ${name}`);
  return extractBraceBlock(source, start);
}

function extractBraceBlock(source, start) {
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  let inSingle = false;
  let inDouble = false;
  let inTemplate = false;
  let escaped = false;
  for (let i = braceStart; i < source.length; i += 1) {
    const ch = source[i];
    if (escaped) {
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      escaped = true;
      continue;
    }
    if (inSingle) {
      if (ch === "'") inSingle = false;
      continue;
    }
    if (inDouble) {
      if (ch === '"') inDouble = false;
      continue;
    }
    if (inTemplate) {
      if (ch === '`') inTemplate = false;
      continue;
    }
    if (ch === "'") { inSingle = true; continue; }
    if (ch === '"') { inDouble = true; continue; }
    if (ch === '`') { inTemplate = true; continue; }
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error('Unclosed function block');
}

function buildSaveHarness(responsePayload) {
  const source = fs.readFileSync(MEMORY_BROWSER_PATH, 'utf8');
  const enLocale = JSON.parse(fs.readFileSync(EN_LOCALE_PATH, 'utf8'));

  const interpolateFn = extractFunction(source, 'interpolateText');
  const translateFn = extractFunction(source, 'translate');
  const saveOnceFn = extractFunction(source, 'saveCurrentMemoryOnce');
  const saveFn = extractFunction(source, 'saveCurrentMemory');

  const statuses = [];
  const context = {
    currentMemoryFile: 'recent_demo.json',
    currentMemoryFingerprint: 'fp-1',
    currentMemoryIdentityToken: 'id-1',
    memoryFileRequestId: 1,
    memorySaveRequestId: 0,
    memorySaveInFlight: null,
    memoryEditRevision: 1,
    chatData: [{ role: 'human', text: 'hi' }],
    PARENT_ORIGIN: 'http://127.0.0.1:48911',
    window: {
      t(key, params = {}) {
        const raw = key.split('.').reduce((acc, part) => (acc && typeof acc === 'object' ? acc[part] : undefined), enLocale);
        if (typeof raw !== 'string') return key;
        return raw.replace(/\{\{\s*([\w.-]+)\s*\}\}/g, (m, name) => (
          Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : m
        ));
      },
    },
    fetch: async () => ({
      ok: false,
      status: 409,
      json: async () => responsePayload,
    }),
    setMemoryDirty() {},
    showSaveStatus(msg, ok) {
      statuses.push({ msg, ok });
    },
    Promise,
    JSON,
    String,
    Object,
    Array,
  };

  vm.createContext(context);
  vm.runInContext(`${interpolateFn}\n${translateFn}\n${saveOnceFn}\n${saveFn}`, context);
  return { context, statuses, enLocale };
}

test('R1111-P2-31: saveCurrentMemory maps 409 conflict/theater codes via i18n instead of raw Chinese data.error', async () => {
  for (const [code, rawError, localeKey] of [
    ['RECENT_FILE_CONFLICT', '近期记忆已在其他任务中更新，请重新加载并合并后再保存', 'recentFileConflict'],
    ['RECENT_FILE_THEATER_READONLY', '小剧场记忆不能在记忆浏览器中编辑，请重新加载后再保存', 'recentFileTheaterReadonly'],
  ]) {
    const { context, statuses, enLocale } = buildSaveHarness({
      success: false,
      code,
      error: rawError,
    });
    const ok = await context.saveCurrentMemory();
    assert.equal(ok, false);
    assert.equal(statuses.length, 1);
    assert.equal(statuses[0].ok, false);
    assert.ok(
      statuses[0].msg.includes(enLocale.memory[localeKey]),
      `expected localized message for ${code}, got: ${statuses[0].msg}`,
    );
    assert.equal(/[\u4e00-\u9fff]/.test(statuses[0].msg), false, `unexpected Chinese in en status: ${statuses[0].msg}`);
  }
});
