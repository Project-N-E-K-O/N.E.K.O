const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..');
const source = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care.js'), 'utf8');
const clientSource = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-pet-care-client.js'),
  'utf8',
);
const css = fs.readFileSync(path.join(root, 'static', 'css', 'pet-care.css'), 'utf8');
const assetAdapter = fs.readFileSync(
  path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'idle-actions-and-audio.js'),
  'utf8',
);
const returnSurface = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-ui', 'surface-floating-controls.js'),
  'utf8',
);
const returnTransitions = fs.readFileSync(
  path.join(root, 'static', 'app', 'app-ui', 'return-transitions.js'),
  'utf8',
);
const dragSource = fs.readFileSync(
  path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'idle-drag-and-subactions.js'),
  'utf8',
);
const journeySource = fs.readFileSync(
  path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'idle-journey-and-presentation.js'),
  'utf8',
);
const questionSource = fs.readFileSync(
  path.join(root, 'static', 'avatar', 'avatar-ui-buttons', 'idle-assets-and-question.js'),
  'utf8',
);

test('stage 7 uses service exploration endpoints and stable activity identity', () => {
  assert.match(clientSource, /\/explorations/);
  assert.match(source, /\.explorationStatus\(/);
  assert.match(source, /exploration_id/);
  assert.match(source, /idempotency_key/);
  assert.match(source, /startExploration/);
  assert.match(source, /completeExploration/);
  assert.match(source, /commitExplorationRecall/);
  assert.match(source, /neko:cat-return-abort/);
});

test('stage 7 keeps the exploration gate separate from Cat Mind and preserves the nest panel', () => {
  assert.match(source, /neko:pet-care:exploration-gate/);
  assert.match(source, /neko:pet-care:exploration-recall-request/);
  assert.match(source, /neko:pet-care:exploration-gate/);
  assert.match(source, /panel and nest controls available/);
});

test('releasing Cat Mind compatibility restores audio providers outside exploration', () => {
  assert.match(assetAdapter, /detail\.source === 'cat_mind' && detail\.suppressed === false/);
  assert.match(assetAdapter, /!window\.nekoPetCareMode\?\.isExplorationAway\?\.\(\)/);
  assert.match(assetAdapter, /_syncNekoIdleSleepSoundForTier\(tier\)/);
  assert.match(assetAdapter, /_syncNekoIdleCat1AmbientSoundForTier\(tier\)/);
});

test('stage 7 keeps the shared transparent pet asset in the desktop adapter, not the panel', () => {
  assert.match(source, /static\/assets\/neko-idle\/cat-bad\.png/);
  assert.match(source, /neko:pet-care:exploration-departure-request/);
  assert.match(source, /explorationDepartureTimer/);
  assert.doesNotMatch(source, /CARE_CONTENTS[\s\S]*exploration[\s\S]*yarn/);
  assert.match(source, /neko-pet-care-exploration-summary/);
  assert.doesNotMatch(source, /neko-pet-care-exploration-image/);
  assert.doesNotMatch(source, /location\.image_url[\s\S]{0,120}cat-bad/);
  assert.match(assetAdapter, /neko-pet-care-exploration-note-overlay/);
  assert.doesNotMatch(assetAdapter, /neko-pet-care-exploration-letter-bubble/);
  assert.doesNotMatch(source, /neko:pet-care:exploration-letters/);
  assert.match(assetAdapter, /neko:pet-care:exploration-asset/);
  assert.match(assetAdapter, /neko:pet-care:exploration-departure-request/);
  assert.match(assetAdapter, /neko:pet-care:exploration-gate/);
  assert.match(assetAdapter, /nekoPetCareExplorationAsset/);
  assert.match(assetAdapter, /explorationPresentationSuppressedByModel/);
  assert.match(assetAdapter, /!petState\.catLocalActive/);
});

test('stage 7 separates cat-form activity recall from the model return chain', () => {
  assert.match(source, /recalled/);
  assert.match(source, /requestedGeneration/);
  assert.match(source, /presentation: options\.presentation \|\| 'cat-form'/);
  assert.match(source, /settleExploration\('recall', payload\)/);
  assert.match(returnSurface, /neko:pet-care:exploration-recall-request/);
  assert.match(returnSurface, /detail\.presentation !== 'model-return'/);
  assert.match(returnSurface, /neko:cat-return-commit/);
  assert.match(source, /ensureExplorationEndedBeforeModelExit/);
  assert.match(returnSurface, /__petCarePreflightDone/);
});

test('stage 7 keeps the chat follower out of the lifecycle and model switch owner', () => {
  const characterSource = fs.readFileSync(
    path.join(root, 'static', 'app', 'app-character.js'),
    'utf8',
  );
  const modelDisplaySource = fs.readFileSync(
    path.join(root, 'static', 'app', 'app-ui', 'model-display.js'),
    'utf8',
  );
  assert.match(source, /PET_CARE_FOLLOWER_PATH/);
  assert.match(source, /if \(!isPetCareOwnerSurface\(\)\) return;/);
  assert.match(source, /neko:pet-care:context-change-request/);
  assert.match(characterSource, /if \(isPetCareModelOwnerSurface\(\)\)/);
  assert.doesNotMatch(characterSource, /return \{ success: true, follower: true \}/);
  assert.match(returnSurface, /ownsLifecycle === false/);
  assert.match(modelDisplaySource, /ensureExplorationEndedBeforeModelExit/);
});

test('stage 7 fences late context recalls against newer account or role state', () => {
  assert.match(source, /function contextFenceMatches\(expected\)/);
  assert.match(source, /detail\.current && typeof detail\.current === 'object'/);
  assert.match(source, /!contextFenceMatches\(expectedFence\) \|\| state\.authStatus !== 'logged-in'/);
});

test('stage 7 protects model hot-reload as another model-switch exit path', () => {
  const reloadSource = fs.readFileSync(
    path.join(root, 'static', 'app', 'app-interpage', 'bootstrap-resources-and-model-reload.js'),
    'utf8',
  );
  assert.match(reloadSource, /skipPetCareExplorationRecall/);
  assert.match(reloadSource, /model-reload-exploration-recall/);
  assert.match(reloadSource, /explorationActive/);
  assert.match(reloadSource, /returnFromGoodbye/);
});

test('stage 7 protects character switching as a model-switch exit path', () => {
  const characterSource = fs.readFileSync(
    path.join(root, 'static', 'app', 'app-character.js'),
    'utf8',
  );
  assert.match(characterSource, /character-switch-exploration-recall/);
  assert.match(characterSource, /explorationActive/);
  assert.match(characterSource, /exploration_model_return_unavailable/);
  assert.match(characterSource, /returnFromGoodbye/);
});

test('stage 7 only exposes rewards after a confirmed exploration envelope', () => {
  assert.match(source, /applyExplorationEnvelope\(body/);
  assert.match(source, /explorationReward/);
  assert.match(source, /function notifyExplorationTerminal\(/);
  assert.match(source, /showPetCareToast\(message, 5000, \{ priority: 10 \}\)/);
  const detailsStart = source.indexOf('function renderExplorationDetails');
  const detailsEnd = source.indexOf('function render()', detailsStart);
  assert.ok(detailsStart >= 0 && detailsEnd > detailsStart);
  assert.doesNotMatch(source.slice(detailsStart, detailsEnd), /explorationReward|已确认收获|猫已召回|探索已完成/);
  assert.match(source, /exploration-start-rejected/);
  assert.match(source, /exploration-settle-timeout/);
  assert.match(source, /neko:pet-care:exploration-settled/);
  assert.match(source, /showAlertDialog|showAlert/);
  assert.match(source, /EXPLORATION_NOTICE_STORAGE_KEY/);
  assert.match(source, /AUTH_PRESENCE_STORAGE_KEY/);
  const terminalStart = source.indexOf('function notifyExplorationTerminal');
  const terminalEnd = source.indexOf('\n    function str(', terminalStart);
  assert.ok(terminalStart >= 0 && terminalEnd > terminalStart);
  assert.match(source.slice(terminalStart, terminalEnd), /showPetCareToast/);
  assert.doesNotMatch(
    source.slice(terminalStart, terminalEnd),
    /showPetCareNotice|showAlertDialog|showAlert\(/,
  );
});

test('stage 7 does not expose the unrevealed letter pool while away', () => {
  assert.match(source, /function explorationLetters\(exploration\)/);
  assert.match(source, /if \(!explorationActive\(exploration\).*Array\.isArray\(exploration\.letters\)/s);
});

test('stage 7 closes refresh, nest visibility and bounded return presentation', () => {
  assert.match(source, /requestContextExplorationRecall/);
  assert.match(source, /executeNewPendingWrite\('exploration-' \+ operation/);
  assert.match(source, /exploration-return-request/);
  assert.match(source, /exploration-return-complete/);
  assert.match(source, /exploration-start-recovery/);
  assert.match(assetAdapter, /data-neko-return-visible/);
  assert.match(assetAdapter, /neko:idle-return-ball-state/);
  assert.match(assetAdapter, /petCare\.getState/);
  assert.match(assetAdapter, /explorationRecallRequested/);
  assert.match(source, /!state\.explorationRecallRequested && !state\.explorationPending/);
  assert.match(source, /ensureExplorationEndedBeforeModelExit\(\{[\s\S]{0,120}source: detail\.reason \|\| 'cat-form-left'/);
  assert.match(returnTransitions, /showNekoReturnBallContainer = I\.showReturnBallContainer/);
  assert.match(source, /sessionStorage/);
  assert.match(source, /exploration-recovery-lease/);
  assert.match(returnTransitions, /__nekoExplorationRecallTransitionActive/);
  assert.match(returnTransitions, /Math\.min\(900, normalDuration\)/);
  assert.match(returnTransitions, /getModelReturnEnterCleanupMs/);
  assert.match(returnSurface, /explorationRecallTransitionToken/);
  assert.match(returnTransitions, /explorationAway: explorationAway/);
  assert.match(returnTransitions, /isPetCareExplorationAway\(\)/);
  assert.match(returnTransitions, /pet-care-exploration-away/);
    assert.match(returnTransitions, /neko:pet-care:exploration-gate/);
    assert.match(returnTransitions, /pet-care-exploration-returned/);
});

test('exploration owns the visual tier and rejects stale or unfinished nest transitions', () => {
  const autoGoodbye = fs.readFileSync(
    path.join(root, 'static', 'app', 'app-auto-goodbye.js'),
    'utf8',
  );
  assert.match(autoGoodbye, /if \(isPetCareExplorationAway\(\)\) \{[\s\S]{0,180}return;/);
  assert.match(autoGoodbye, /pet-care-exploration-away/);
  assert.match(autoGoodbye, /pet-care-exploration-returned/);
  assert.match(journeySource, /_shouldApplyNekoIdleVisualTierDuringExploration/);
  assert.match(journeySource, /source === 'pet-care-user-action' \|\| source === 'pet-care-exploration-away'/);
  assert.match(assetAdapter, /_cleanupNekoIdleArtTransition\(art\)/);
  assert.match(assetAdapter, /detailId && currentId && detailId !== currentId/);
  assert.match(assetAdapter, /currentExploration\.active === false/);
  assert.match(source, /function adoptCatFactFromAnchor\(reason\)/);
  const adoptAnchorStart = source.indexOf('function adoptCatFactFromAnchor(reason)');
  const adoptAnchorBlock = source.slice(adoptAnchorStart, adoptAnchorStart + 900);
  assert.match(adoptAnchorBlock, /explorationActive\(currentExploration\(\)\)/);
  assert.match(adoptAnchorBlock, /nekoPetCareMode\.isExplorationAway\(\)/);
});

test('pet-care anchor follows runtime movement markers without treating presentation actions as busy', () => {
  const anchorStart = returnTransitions.indexOf('window.nekoCatInteractionAnchor');
  assert.ok(anchorStart >= 0, 'semantic cat interaction anchor is missing');
  const anchorSource = returnTransitions.slice(anchorStart, anchorStart + 5000);
  const busyStart = anchorSource.indexOf('const busySelector');
  const busyEnd = anchorSource.indexOf('const busy =', busyStart);
  assert.ok(busyStart >= 0 && busyEnd > busyStart, 'busy selector is missing');
  const busySource = anchorSource.slice(busyStart, busyEnd);

  assert.match(busySource, /\[data-dragging="pending"\]/);
  assert.match(busySource, /\[data-dragging="true"\]/);
  assert.match(busySource, /\[data-neko-cat1-playground-dragging\]/);
  assert.doesNotMatch(busySource, /is-desktop-window-gravity/);
  assert.match(busySource, /is-cat1-edge-peek-(?:left|right)/);
  assert.match(busySource, /is-cat1-desktop-window-edge-peek-walking/);
  assert.match(busySource, /is-cat1-desktop-window-edge-peeking/);
  assert.match(busySource, /is-cat1-desktop-window-top-edge-walking/);
  assert.match(busySource, /is-cat1-desktop-window-top-edge-dropping/);
  assert.doesNotMatch(busySource, /data-neko-(?:dragging|gravity)/);
  assert.doesNotMatch(busySource, /is-cat1-(?:stretching|eating|playing)/);

  assert.match(anchorSource, /actionPresentationSelector/);
  assert.match(anchorSource, /is-cat1-stretching/);
  assert.match(anchorSource, /is-cat1-eating/);
  assert.match(anchorSource, /is-cat1-playing/);
  assert.match(anchorSource, /markedBusy && !actionPresentation/);
  assert.match(anchorSource, /NekoDesktopWindowGravity/);
  assert.match(anchorSource, /gravityController\.isMoving\(gravityButton\) === true/);
  assert.match(anchorSource, /!!busy \|\| gravityMoving \|\|/);
});

test('pet-care anchor distinguishes moving gravity from a resting gravity session', () => {
  const anchorStart = returnTransitions.indexOf('window.nekoCatInteractionAnchor');
  const anchorEndMarker = '\n    });';
  const anchorEnd = returnTransitions.indexOf(anchorEndMarker, anchorStart);
  assert.ok(anchorStart >= 0 && anchorEnd > anchorStart, 'semantic cat interaction anchor is missing');

  let gravityMoving = false;
  let actionPresentation = false;
  let markedBusy = false;
  const gravityButton = {};
  const container = {
    getAttribute(name) {
      if (name === 'data-neko-idle-tier') return 'cat1';
      if (name === 'data-neko-return-busy') return markedBusy ? 'true' : null;
      return null;
    },
    getBoundingClientRect() {
      return { left: 10, top: 20, right: 74, bottom: 84, width: 64, height: 64 };
    },
    matches() { return false; },
    querySelector(selector) {
      if (selector === '.is-desktop-window-gravity') return gravityButton;
      if (actionPresentation && selector.includes('.is-cat1-stretching')) return {};
      return null;
    },
  };
  const anchorWindow = {
    NekoDesktopWindowGravity: {
      isMoving(owner) {
        assert.equal(owner, gravityButton);
        return gravityMoving;
      },
    },
  };
  const anchorParts = { getVisibleIdleReturnBallContainer: () => container };
  const anchorDefinition = returnTransitions.slice(
    anchorStart,
    anchorEnd + anchorEndMarker.length,
  );
  Function('window', 'I', anchorDefinition)(anchorWindow, anchorParts);

  gravityMoving = true;
  assert.equal(anchorWindow.nekoCatInteractionAnchor.getState().busy, true);
  gravityMoving = false;
  assert.equal(anchorWindow.nekoCatInteractionAnchor.getState().busy, false);
  markedBusy = true;
  actionPresentation = true;
  assert.equal(anchorWindow.nekoCatInteractionAnchor.getState().busy, false);
});

test('stage 7 gates late play and independent entity drag/question outputs', () => {
  assert.match(source, /explorationActive\(currentExploration\(\)\)[\s\S]{0,160}state\.playPending/);
  assert.match(source, /category: 'exploration'/);
  assert.match(source, /!state\.catLocalActive/);
  assert.match(dragSource, /source: 'return-ball-drag'/);
  assert.match(journeySource, /explorationBlocked/);
  assert.match(questionSource, /source: 'question-mark'/);
  assert.match(questionSource, /exploration-gate/);
});
