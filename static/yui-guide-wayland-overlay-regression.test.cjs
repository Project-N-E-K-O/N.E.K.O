const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');
const { readJsParts } = require('./app-part-test-utils.cjs');

const repoRoot = path.resolve(__dirname, '..');
const targetSource = fs.readFileSync(
  path.join(repoRoot, 'static/app/app-interpage/guide-targets.js'),
  'utf8',
);
const interpageSource = readJsParts(
  path.join(repoRoot, 'static/app/app-interpage'),
  { contractView: false },
);
const managerSource = fs.readFileSync(
  path.join(repoRoot, 'static/tutorial/core/universal-manager.js'),
  'utf8',
);

function extractFunction(source, name, nextName) {
  const start = source.indexOf(`    function ${name}(`);
  const end = source.indexOf(`    function ${nextName}(`, start + 1);
  assert.notEqual(start, -1, `missing ${name}`);
  assert.notEqual(end, -1, `missing ${nextName}`);
  return source.slice(start, end);
}

test('plain capsule alignment translates the full-width highlight like macOS', () => {
  const functionSource = extractFunction(
    targetSource,
    'getYuiGuideChatSpotlightSourceRect',
    'getYuiGuideChatVisibleElement',
  );
  const context = {
    YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO: 0.6,
    shouldAlignYuiGuideChatSpotlightToCapsuleText: () => true,
    getYuiGuideChatCapsuleTextAnchor: () => ({
      rect: { left: 150, top: 20, width: 180, height: 40 },
    }),
  };
  vm.runInNewContext(
    `${functionSource}
result = getYuiGuideChatSpotlightSourceRect(
  'input',
  'plain-capsule',
  { left: 100, top: 10, width: 400, height: 60 }
);`,
    context,
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.result.rect)),
    { left: 130, top: 10, width: 400, height: 60 },
  );
});

test('Wayland work-area capsule alignment matches macOS width while correcting the left origin', () => {
  const functionSource = extractFunction(
    targetSource,
    'getYuiGuideChatSpotlightSourceRect',
    'getYuiGuideChatVisibleElement',
  );
  const shouldAlignSource = extractFunction(
    targetSource,
    'shouldAlignYuiGuideChatSpotlightToCapsuleText',
    'getYuiGuideChatSpotlightTarget',
  );
  const context = {
    YUI_GUIDE_CHAT_CAPSULE_TEXT_ALIGNMENT_RATIO: 0.6,
    getYuiGuideChatCapsuleTextAnchor: () => ({
      rect: { left: 150, top: 20, width: 180, height: 40 },
    }),
  };
  vm.runInNewContext(
    `${shouldAlignSource}
${functionSource}
waylandResult = getYuiGuideChatSpotlightSourceRect(
  'capsule-input',
  '',
  { left: 100, top: 10, width: 400, height: 60 },
  { waylandWorkAreaCarrier: true }
);
x11Result = getYuiGuideChatSpotlightSourceRect(
  'capsule-input',
  '',
  { left: 100, top: 10, width: 400, height: 60 },
  { waylandWorkAreaCarrier: false }
);`,
    context,
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.waylandResult.rect)),
    { left: 130, top: 10, width: 400, height: 60 },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.x11Result.rect)),
    { left: 100, top: 10, width: 400, height: 60 },
  );
  assert.equal(context.waylandResult.rect.width, context.x11Result.rect.width);
});

test('PC overlay skip mirrors localized state and relays only through the active lifecycle', () => {
  assert.match(managerSource, /setYuiGuidePcOverlaySkipControl\(true, label\)/);
  assert.match(managerSource, /setYuiGuidePcOverlaySkipControl\(false, ''\)/);
  const functionStart = interpageSource.indexOf(
    'I.setYuiGuidePcOverlaySkipControl = function setYuiGuidePcOverlaySkipControl'
  );
  const functionEnd = interpageSource.indexOf(
    'I.mod.setYuiGuidePcOverlaySkipControl',
    functionStart,
  );
  assert.notEqual(functionStart, -1);
  assert.notEqual(functionEnd, -1);
  const functionSource = interpageSource.slice(functionStart, functionEnd);
  const calls = [];
  const context = {
    I: {
      sendYuiGuidePcOverlayPatch(...args) {
        calls.push(args);
        return true;
      },
    },
    document: {
      documentElement: {
        getAttribute: () => 'light',
        classList: { contains: () => false },
      },
    },
  };
  vm.runInNewContext(`${functionSource}
I.setYuiGuidePcOverlaySkipControl(false, '');
I.setYuiGuidePcOverlaySkipControl(true, '跳过');`, context);
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0].skipControl, null);
  assert.equal(calls[0][2].allowCreateRun, false);
  assert.equal(calls[1][0].skipControl.label, '跳过');
  assert.equal(calls[1][2].allowCreateRun, true);
  assert.match(interpageSource, /YUI_GUIDE_PC_OVERLAY_SKIP_CONTROL_KEY = 'yuiGuidePcOverlaySkipControl'/);
  assert.match(interpageSource, /persistYuiGuidePcOverlaySkipControl\(I\.yuiGuidePcOverlaySkipControl\)/);
  assert.match(interpageSource, /I\.yuiGuidePcOverlaySkipControl = readYuiGuidePcOverlaySkipControl\(\)/);
  assert.match(interpageSource, /payload\.skipControl = I\.yuiGuidePcOverlaySkipControl/);
  assert.match(managerSource, /capabilities\.waylandOverlaySkipInput === true/);
  assert.match(managerSource, /skipButton\.style\.visibility = overlayOwnsSkipInput \? 'hidden' : ''/);
  assert.match(interpageSource, /case 'yui_guide_overlay_skip_request':/);
  assert.match(interpageSource, /new CustomEvent\('neko:yui-guide:desktop-skip-request'/);
  assert.match(
    interpageSource,
    /case 'yui_guide_overlay_skip_request':[\s\S]*return true;[\s\S]*function isYuiGuideLifecycleStartAction/,
  );
});

test('screen conversion prefers an explicitly declared render coordinate space', () => {
  assert.match(
    interpageSource,
    /metrics\.coordinateSpace === 'screen-dip'[\s\S]*return metrics\.renderBounds;/,
  );
  assert.match(
    interpageSource,
    /return metrics && \(metrics\.bounds \|\| metrics\.contentBounds\) \|\| \{ x: 0, y: 0 \};/,
  );
});
