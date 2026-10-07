// A migration paused because its source (or an entry in it) is missing keeps
// every copy and waits. The maintenance view must say which directory to
// restore instead of the generic "waiting for recovery" text.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'static/app/app-storage-location.js'), 'utf8');

function loadStorageLocation(locale) {
  const messages = JSON.parse(fs.readFileSync(path.join(root, `static/locales/${locale}.json`), 'utf8'));
  const window = {
    location: { origin: 'http://localhost' },
    addEventListener() {},
    safeT(key, fallback) {
      return key.split('.').reduce((value, part) => value && value[part], messages) || fallback;
    },
  };
  const context = vm.createContext({
    window,
    console,
    document: { currentScript: { getAttribute() { return 'false'; } } },
  });
  vm.runInContext(script, context);
  return { model: window.appStorageLocation.buildMaintenanceProgressModel, messages };
}

for (const locale of ['en', 'zh-CN']) {
  test(`${locale}: paused migration names the directory to restore`, () => {
    const { model, messages } = loadStorageLocation(locale);
    const progress = model({
      lifecycle_state: 'maintenance',
      migration: {
        status: 'rollback_required',
        error_code: 'migration_source_missing',
        source_root: 'D:/Documents/N.E.K.O',
      },
    });

    assert.strictEqual(progress.hasError, true);
    assert.strictEqual(progress.label, `${messages.storage.progressSourceMissing} D:/Documents/N.E.K.O`);
  });

  test(`${locale}: publish conflict points at the transaction directory`, () => {
    const { model, messages } = loadStorageLocation(locale);
    const progress = model({
      lifecycle_state: 'maintenance',
      migration: {
        status: 'rollback_required',
        error_code: 'migration_publish_conflict',
        target_root: 'E:/new/N.E.K.O',
        txid: '0123456789abcdef0123456789abcdef',
      },
    });

    assert.strictEqual(progress.label, `${messages.storage.progressPublishConflict} E:/new/N.E.K.O/.smtx/0123456789ab`);
  });

  test(`${locale}: publish conflict keeps Windows separators consistent`, () => {
    const { model, messages } = loadStorageLocation(locale);
    const backslash = String.fromCharCode(92);
    const windowsRoot = ['E:', 'new', 'N.E.K.O'].join(backslash);
    const progress = model({
      lifecycle_state: 'maintenance',
      migration: {
        status: 'rollback_required',
        error_code: 'migration_publish_conflict',
        target_root: windowsRoot,
        txid: '0123456789abcdef0123456789abcdef',
      },
    });

    assert.strictEqual(progress.label, `${messages.storage.progressPublishConflict} ${windowsRoot}${backslash}.smtx${backslash}0123456789ab`);
  });

  test(`${locale}: other failed migrations keep the generic text`, () => {
    const { model, messages } = loadStorageLocation(locale);
    const progress = model({
      lifecycle_state: 'maintenance',
      migration: { status: 'rollback_required', error_code: 'migration_rollback_required' },
    });

    assert.strictEqual(progress.label, messages.storage.progressFailed);
  });
}
