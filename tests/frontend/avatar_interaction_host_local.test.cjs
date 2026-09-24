const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../static/app/app-buttons.js'), 'utf8');

function section(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `missing Host section: ${start}`);
  return source.slice(from, to);
}

const host = vm.createContext({ console: { warn() {} }, Date });
vm.runInContext([
  section('var AVATAR_INTERACTION_CONTRACT =', '// The backend sends the final ack'),
  section('function sanitizeAvatarInteractionTextContext(', 'function resolveAvatarInteractionRoundResult('),
  section('function normalizeAvatarInteractionPayload(', 'async function sendAvatarInteractionPayload('),
].join('\n'), host);

const TOOL_ID = 'local-12345678-1234-4123-8123-123456789abc';
const base = {
  interactionId: 'host-v3-1',
  toolId: TOOL_ID,
  target: 'avatar',
  actionId: 'interact',
  intensity: 'normal',
  touchZone: 'head',
  timestamp: 1,
};
const normalize = payload => {
  const result = host.normalizeAvatarInteractionPayload(payload);
  return result ? JSON.parse(JSON.stringify(result)) : null;
};

test('Host keeps v2 changeIndex and accepts only a v3 stable image fact', () => {
  assert.equal(normalize({ ...base, toolRevision: '2-1', changeIndex: 0 }).change_index, 0);
  const v3 = normalize({ ...base, toolRevision: '3-1', imageId: 'img-a' });
  assert.equal(v3.image_id, 'img-a');
  assert.equal(v3.tool_revision, '3-1');
  assert.equal(Object.hasOwn(v3, 'change_index'), false);
  assert.equal(normalize({ ...base, toolRevision: '3-1', imageId: 'img-a', changeIndex: 0 }), null);
  assert.equal(normalize({ ...base, toolRevision: '2-1', changeIndex: 0, imageId: 'img-a' }), null);
  assert.equal(normalize({ ...base, toolRevision: '3-1', imageId: 'img-INVALID' }), null);
  assert.equal(normalize({ ...base, toolRevision: '4-1', imageId: 'img-a' }), null);
});

test('Host preserves an explicit v3 special miss and refuses ambiguous or coerced facts', () => {
  const v3 = { ...base, toolRevision: '3-1', imageId: 'img-a' };
  assert.equal(normalize({ ...v3, specialTriggered: false }).special_triggered, false);
  assert.equal(normalize({ ...v3, specialTriggered: 'false' }), null);
  assert.equal(normalize({ ...v3, specialTriggered: true, special_triggered: false }), null);
  assert.equal(normalize({ ...v3, image_id: 'img-b' }), null);
});
