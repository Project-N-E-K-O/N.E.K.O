const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '../..');
const script = fs.readFileSync(path.join(root, 'static/app/app-storage-location.js'), 'utf8');
const template = fs.readFileSync(path.join(root, 'templates/memory_browser.html'), 'utf8');
const formatterPosition = template.indexOf('app-storage-location.js');
const browserPosition = template.indexOf('memory_browser.js');
assert(formatterPosition >= 0, 'Shared storage formatter must be loaded.');
assert(browserPosition >= 0, 'Memory browser script must be loaded.');
assert(formatterPosition < browserPosition, 'Shared formatter must load before the memory browser.');

for (const locale of ['en', 'ja', 'zh-CN']) {
  const messages = JSON.parse(fs.readFileSync(path.join(root, `static/locales/${locale}.json`), 'utf8'));
  const window = {
    location: { origin: 'http://localhost' },
    addEventListener() {},
    safeT(key, fallback) {
      return key.split('.').reduce((value, part) => value && value[part], messages) || fallback;
    },
  };
  const context = vm.createContext({
    window, console,
    document: { currentScript: { getAttribute() { return 'false'; } } },
  });
  vm.runInContext(script, context);
  const format = window.appStorageLocation.formatError;
  const payload = { error_code: 'storage_policy_rollback_failed', error: '中文底层异常私有路径' };
  assert.strictEqual(format({ ...payload, error_code: 'startup_release_rollback_failed' }, 'fallback'), messages.storage.startupReleaseRollbackFailed);
  assert.strictEqual(format({ ...payload, error_code: 'restart_rollback_failed' }, 'fallback'), messages.storage.restartRollbackFailed);
  assert.strictEqual(format({ ...payload, error_code: 'storage_operation_failed' }, 'fallback'), messages.storage.storageOperationFailed);
  assert.strictEqual(format({ ...payload, error_code: 'storage_state_invalid' }, 'fallback'), messages.storage.storageStateInvalid);
  assert.strictEqual(format({ ...payload, error_code: 'startup_release_failed' }, 'fallback'), messages.storage.startupReleaseFailed);
  for (const [code, key] of [
    ['migration_already_pending', 'migrationAlreadyPending'],
    ['selected_root_empty', 'selectedRootEmpty'],
    ['selected_root_not_absolute', 'selectedRootNotAbsolute'],
    ['selected_root_inside_project', 'selectedRootInsideProject'],
    ['selected_root_is_file', 'selectedRootNotDirectory'],
    ['selected_root_not_directory', 'selectedRootNotDirectory'],
    ['selected_root_parent_missing', 'selectedRootParentMissing'],
    ['selected_root_parent_not_writable', 'selectedRootParentNotWritable'],
    ['selected_root_inside_staging', 'selectedRootReserved'],
  ]) {
    assert.strictEqual(format({ ...payload, error_code: code }, 'fallback'), messages.storage[key]);
  }
  assert.strictEqual(format({ ...payload, error_code: 'unknown_error' }, 'fallback'), 'fallback');
}
console.log('Storage error formatting passed for English, Japanese and Simplified Chinese.');
