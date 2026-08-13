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
const overlaySource = fs.readFileSync(
  path.join(repoRoot, 'static/tutorial/yui-guide/overlay.js'),
  'utf8',
);
const directorSource = fs.readFileSync(
  path.join(repoRoot, 'static/tutorial/yui-guide/director/director-core.js'),
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

test('Niri capsule target keeps its body rect while standard Wayland preserves text alignment', () => {
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
niriShouldAlign = shouldAlignYuiGuideChatSpotlightToCapsuleText(
  'capsule-input',
  '',
  { waylandWorkAreaCarrier: true, niriWaylandRuntime: true }
);
waylandShouldAlign = shouldAlignYuiGuideChatSpotlightToCapsuleText(
  'capsule-input',
  '',
  { waylandWorkAreaCarrier: true, niriWaylandRuntime: false }
);
plainCapsuleShouldAlign = shouldAlignYuiGuideChatSpotlightToCapsuleText('input', 'plain-capsule', null);
niriResult = getYuiGuideChatSpotlightSourceRect(
  'capsule-input',
  '',
  { left: 100, top: 10, width: 400, height: 60 },
  { waylandWorkAreaCarrier: true, niriWaylandRuntime: true }
);
waylandResult = getYuiGuideChatSpotlightSourceRect(
  'capsule-input',
  '',
  { left: 100, top: 10, width: 400, height: 60 },
  { waylandWorkAreaCarrier: true, niriWaylandRuntime: false }
);`,
    context,
  );
  assert.equal(context.niriShouldAlign, false);
  assert.equal(context.waylandShouldAlign, true);
  assert.equal(context.plainCapsuleShouldAlign, true);
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.niriResult.rect)),
    { left: 100, top: 10, width: 400, height: 60 },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.waylandResult.rect)),
    { left: 130, top: 10, width: 400, height: 60 },
  );
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

test('screen conversion rejects workspace-view render bounds without screen provenance', () => {
  assert.match(
    interpageSource,
    /metrics\.renderBoundsCoordinateSpace === 'screen-dip'[\s\S]*metrics\.originSource === 'niri-pet-physical-crop-virtual-bounds'[\s\S]*return metrics\.renderBounds;/,
  );
  assert.match(
    interpageSource,
    /return metrics && \(metrics\.bounds \|\| metrics\.contentBounds\) \|\| \{ x: 0, y: 0 \};/,
  );
  assert.match(
    overlaySource,
    /metrics\.renderBoundsCoordinateSpace === 'screen-dip'[\s\S]*metrics\.originSource === 'niri-pet-physical-crop-virtual-bounds'[\s\S]*metrics\.renderBounds/,
  );
  assert.match(
    directorSource,
    /getGuideScreenCoordinateBounds\(metrics\)[\s\S]*metrics\.renderBoundsCoordinateSpace === 'screen-dip'[\s\S]*metrics\.originSource === 'niri-pet-physical-crop-virtual-bounds'[\s\S]*return metrics\.renderBounds;/,
  );

  const functionSource = extractFunction(
    interpageSource,
    'getYuiGuideScreenCoordinateBounds',
    'normalizeYuiGuideNiriPetPhysicalCropBounds',
  );
  const context = vm.createContext({});
  vm.runInNewContext(
    `${functionSource}
workspaceResult = getYuiGuideScreenCoordinateBounds({
  coordinateSpace: 'screen-dip',
  bounds: { x: 100, y: 50, width: 800, height: 600 },
  renderBounds: { x: -320, y: 20, width: 800, height: 600 },
  renderBoundsCoordinateSpace: 'workspace-view-dip'
});
screenResult = getYuiGuideScreenCoordinateBounds({
  coordinateSpace: 'screen-dip',
  bounds: { x: 100, y: 50, width: 800, height: 600 },
  renderBounds: { x: 140, y: 70, width: 800, height: 600 },
  renderBoundsCoordinateSpace: 'screen-dip'
});
legacyCropResult = getYuiGuideScreenCoordinateBounds({
  coordinateSpace: 'screen-dip',
  bounds: { x: 100, y: 50, width: 800, height: 600 },
  renderBounds: { x: 1, y: 1, width: 1200, height: 800 },
  niriPetPhysicalCrop: true,
  originSource: 'niri-pet-physical-crop-virtual-bounds'
});`,
    context,
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.workspaceResult)),
    { x: 100, y: 50, width: 800, height: 600 },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.screenResult)),
    { x: 140, y: 70, width: 800, height: 600 },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.legacyCropResult)),
    { x: 1, y: 1, width: 1200, height: 800 },
  );
});

test('physical-crop DOM coordinates still receive the layout offset when metrics are virtualized', () => {
  const pointSource = extractFunction(
    interpageSource,
    'toYuiGuideNiriPetPhysicalCropVirtualPointWithState',
    'toYuiGuideNiriPetPhysicalCropVirtualRectWithState',
  );
  const rectSource = extractFunction(
    interpageSource,
    'toYuiGuideNiriPetPhysicalCropVirtualRectWithState',
    'shouldApplyYuiGuideVisualViewportOffset',
  );
  const screenPointStart = interpageSource.indexOf(
    '    function toYuiGuideScreenVirtualPoint(',
  );
  const screenPointEnd = interpageSource.indexOf(
    '    I.toYuiGuideScreenPoint =',
    screenPointStart,
  );
  assert.notEqual(screenPointStart, -1);
  assert.notEqual(screenPointEnd, -1);
  const screenPointSource = interpageSource.slice(screenPointStart, screenPointEnd);
  const context = {
    toYuiGuideNiriPetPhysicalCropVirtualPoint: () => null,
    toYuiGuideNiriPetPhysicalCropVirtualRect: () => null,
  };
  vm.runInNewContext(
    `${pointSource}
${rectSource}
${screenPointSource}
pointResult = toYuiGuideNiriPetPhysicalCropVirtualPointWithState(
  55,
  55,
  { offsetX: 863, offsetY: 323, metricsVirtualized: true }
);
rectResult = toYuiGuideNiriPetPhysicalCropVirtualRectWithState(
  { left: 55, top: 55, width: 98, height: 62 },
  { offsetX: 863, offsetY: 323, metricsVirtualized: true }
);
screenResult = toYuiGuideScreenVirtualPoint(
  pointResult.x,
  pointResult.y,
  {
    virtualBounds: { x: 1, y: 1, width: 1706, height: 1066 },
    cropBounds: { x: 864, y: 324, width: 192, height: 432 }
  }
);`,
    context,
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(context.pointResult)),
    { x: 918, y: 378 },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.rectResult)),
    { left: 918, top: 378, width: 98, height: 62 },
  );
  assert.deepEqual(
    JSON.parse(JSON.stringify(context.screenResult)),
    { x: 919, y: 379 },
  );
});

test('all three tutorial coordinate paths use the DOM layout generation symmetrically', () => {
  const appPointBlock = extractFunction(
    interpageSource,
    'toYuiGuideNiriPetPhysicalCropVirtualPointWithState',
    'toYuiGuideNiriPetPhysicalCropVirtualRectWithState',
  );
  const appRectBlock = extractFunction(
    interpageSource,
    'toYuiGuideNiriPetPhysicalCropVirtualRectWithState',
    'shouldApplyYuiGuideVisualViewportOffset',
  );
  const overlayPointStart = overlaySource.indexOf(
    '        const toNiriPetPhysicalCropVirtualPointWithState =',
  );
  const overlayPointEnd = overlaySource.indexOf(
    '        const shouldApplyVisualViewportOffset =',
    overlayPointStart,
  );
  const directorPointStart = directorSource.indexOf(
    '        toNiriPetPhysicalCropVirtualPointWithState(',
  );
  const directorPointEnd = directorSource.indexOf(
    '        getGuideWindowMetricsSync()',
    directorPointStart,
  );
  assert.notEqual(overlayPointStart, -1);
  assert.notEqual(overlayPointEnd, -1);
  assert.notEqual(directorPointStart, -1);
  assert.notEqual(directorPointEnd, -1);
  const overlayPointBlock = overlaySource.slice(overlayPointStart, overlayPointEnd);
  const directorPointBlock = directorSource.slice(directorPointStart, directorPointEnd);

  for (const block of [appPointBlock, appRectBlock, overlayPointBlock, directorPointBlock]) {
    assert.doesNotMatch(
      block,
      /cropState\s*&&\s*cropState\.metricsVirtualized/,
      'virtualized window bounds must not skip crop-local DOM conversion',
    );
  }
  for (const source of [interpageSource, overlaySource, directorSource]) {
    assert.match(source, /niriPetPhysicalCropLayoutOffsetX/);
    assert.match(source, /niriPetPhysicalCropLayoutOffsetY/);
    assert.match(source, /toLayoutVirtualPoint/);
    assert.match(source, /getLayoutState/);
  }
});
