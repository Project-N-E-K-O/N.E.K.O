const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..', '..');
const app = fs.readFileSync(path.join(root, 'static', 'app', 'app-pet-care.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static', 'css', 'pet-care.css'), 'utf8');

test('stage 9.1 surface keeps overview actions separate from long-term rail', () => {
  const navMatch = app.match(/var navItems = (\[[\s\S]*?\]);/);
  assert.ok(navMatch);
  for (const label of ['overview', 'tasks', 'letters', 'inventory', 'profile']) {
    assert.match(navMatch[1], new RegExp("'" + label + "'"));
  }
  assert.match(app, /var actionCategories = \['feeding', 'cleaning', 'play', 'exploration'\]/);
  assert.doesNotMatch(app, /neko-pet-care-action-orbit/);
  assert.match(app, /neko-pet-care-stat-bar/);
  assert.match(app, /ui\.grid\.hidden = !loggedInSurface \|\| activeExplorationAvailable \|\| view !== 'overview' \|\| !!state\.selectedCategory/);
  assert.match(app, /ui\.actions\.hidden = !loggedInSurface \|\| view !== 'overview'/);
});

test('stage 9.1 uses the selected image assets for navigation and overview actions', () => {
  for (const name of [
    'overview', 'tasks', 'letters', 'inventory', 'profile',
    'feeding', 'cleaning', 'play', 'exploration',
  ]) {
    assert.match(app, new RegExp(name + ": '" + name + "\\.png'"));
    assert.equal(fs.existsSync(path.join(
      root, 'static', 'assets', 'neko-idle', 'pet-care-ui-icon', name + '.png'
    )), true);
  }
  assert.match(app, /function petCareUiIcon\(name, className\)/);
  assert.match(app, /document\.createElement\('img'\)/);
  assert.match(app, /petCareUiIcon\(actionCategories\[index\], 'neko-pet-care-action-icon'\)/);
  assert.match(app, /petCareUiIcon\(entry\[0\], 'neko-pet-care-nav-icon'\)/);
  assert.doesNotMatch(app, /var actionIcons = \['♨', '⌁', '✦', '↗'\]/);
  assert.doesNotMatch(app, /\['overview','总览','⌂'\]/);
  assert.match(css, /\.neko-pet-care-nav-icon \{[\s\S]*width: 30px;[\s\S]*height: 30px;[\s\S]*object-fit: contain;/);
  assert.match(css, /\.neko-pet-care-action-icon \{[\s\S]*width: 34px;[\s\S]*height: 34px;[\s\S]*object-fit: contain;/);
});

test('stage 9.1 uses the shared semantic anchor and does not embed a cat asset', () => {
  assert.match(app, /function anchorState\(/);
  assert.match(app, /function adoptCatFactFromAnchor\(/);
  assert.match(app, /adoptCatFactFromAnchor\('semantic-anchor-initial'\)/);
  assert.match(app, /detail\.visible === true\) adoptCatFactFromAnchor/);
  assert.match(app, /function positionOverlay\(/);
  assert.doesNotMatch(app, /neko-pet-care-entry-anchor/);
  assert.doesNotMatch(app, /button\('养宠',\s*'neko-pet-care-entry-button'\)/);
  assert.doesNotMatch(app, /ui\.button\.textContent\s*=\s*status/);
  assert.doesNotMatch(app, /cat-idle-cat1\.gif|cat1\.gif|exploration-window-path\.svg/);
  assert.match(css, /#neko-pet-care-entry-overlay \.neko-pet-care-surface/);
  assert.doesNotMatch(css, /neko-pet-care-entry-anchor|neko-pet-care-entry-panel/);
  assert.match(css, /#neko-pet-care-entry-overlay \{[\s\S]*pointer-events: none;/);
  assert.match(css, /background: transparent;/);
  assert.match(css, /neko-pet-care-scene/);
  assert.match(css, /neko-pet-care-stats/);
  assert.match(css, /neko-pet-care-stats \{[\s\S]*right: 16px;[\s\S]*top: 42px;[\s\S]*width: 252px;[\s\S]*min-height: 286px/);
  assert.match(css, /font-family: "Avenir Next", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif/);
  assert.match(css, /#neko-pet-care-entry-overlay \{[\s\S]*width: 760px;[\s\S]*height: 560px;[\s\S]*pointer-events: none;/);
  assert.match(css, /\.neko-pet-care-surface \{[\s\S]*left: 0;[\s\S]*top: 0;[\s\S]*width: 760px;[\s\S]*height: 560px;[\s\S]*background: transparent;[\s\S]*pointer-events: none;/);
  assert.doesNotMatch(css, /@media \(max-width: 760px\)/);
  assert.doesNotMatch(app, /dataset\.panelSide|dataset\.placement/);
  assert.doesNotMatch(app, /componentBoxesFor = function/);
  assert.doesNotMatch(app, /var candidates = \[/);
  assert.doesNotMatch(app, /selected\.name/);
  assert.match(app, /var left = centerX - 380;/);
  assert.match(app, /var top = centerY - 280;/);
  assert.match(app, /setProperty\('--pet-scene-center', '380px'\)/);
  assert.doesNotMatch(app, /var clamp = function/);
  assert.match(css, /\.neko-pet-care-surface > \* \{\n    position: absolute/);
  assert.match(css, /#neko-pet-care-entry-overlay \.neko-pet-care-surface > \*,\n#neko-pet-care-entry-overlay \.neko-pet-care-surface \.neko-pet-care-module,[\s\S]*pointer-events: none/);
  const hintStart = app.indexOf('function showFeatureHintOnce');
  const hintEnd = app.indexOf('function syncCatMindCompatibility', hintStart);
  assert.ok(hintStart >= 0 && hintEnd > hintStart);
  assert.doesNotMatch(app.slice(hintStart, hintEnd), /state\.panelOpen\s*=\s*true/);
  const hoverStart = app.indexOf('function showSoon');
  const hoverEnd = app.indexOf('function anchorState', hoverStart);
  assert.ok(hoverStart >= 0 && hoverEnd > hoverStart);
  assert.match(app.slice(hoverStart, hoverEnd), /state\.panelOpen\s*=\s*true/);
});

test('stage 9.1 overview follows the selected demo block hierarchy', () => {
  assert.doesNotMatch(app, /taskPreview|previewVisible/);
  assert.doesNotMatch(app, /textContent\s*=\s*['"]今日任务['"]/);
  assert.match(app, /neko-pet-care-action-icon/);
  assert.match(app, /neko-pet-care-nav-icon/);
  assert.match(app, /neko-pet-care-stat-unit/);
  assert.doesNotMatch(app, /选择具体操作/);
  assert.match(css, /\.neko-pet-care-stat-bar i \{[\s\S]*linear-gradient\(90deg, #83d3ee 0%, #5aaee3 52%, #498ed6 100%\)/);
  assert.match(app, /neko-pet-care-inventory-item/);
  assert.match(app, /neko-pet-care-task-tick/);
  assert.match(app, /function renderProgressUnlocks\(/);
  assert.match(app, /neko-pet-care-progress-unlock-panel/);
  assert.match(app, /state\.progressUnlockOpen/);
  assert.match(css, /neko-pet-care-progress-unlock-panel/);
  assert.doesNotMatch(app, /当前快照/);
  assert.doesNotMatch(app, /neko-pet-care-task-unlocks|textContent = '已解锁'/);
  assert.doesNotMatch(app, /neko-pet-care-dev-tools|neko-pet-care-dev-fill-button/);
  assert.doesNotMatch(css, /neko-pet-care-task-unlocks|neko-pet-care-dev-tools/);
  assert.doesNotMatch(app, /mailPreview/);
  assert.doesNotMatch(css, /neko-pet-care-preview-card|neko-pet-care-exploration-reward/);
  assert.doesNotMatch(css, /neko-pet-care-mail-preview/);
  assert.match(css, /\.neko-pet-care-rail \{[\s\S]*left: 142px;[\s\S]*width: 78px/);
  assert.match(css, /\.neko-pet-care-panel-footer \{[\s\S]*bottom: 168px;/);
  assert.match(css, /380 - \(42 \+ 78 \/ 2\) = 299px/);
  assert.match(css, /\.neko-pet-care-actions \{[\s\S]*left: 380px;/);
  assert.match(css, /\.neko-pet-care-content \{[\s\S]*left: auto;[\s\S]*right: 16px;[\s\S]*top: 42px;[\s\S]*width: 252px/);
  assert.match(css, /\.neko-pet-care-action-details \{[\s\S]*left: auto;[\s\S]*right: 16px;[\s\S]*top: 42px;[\s\S]*width: 252px/);
  assert.match(css, /\.neko-pet-care-nav-item \{[\s\S]*font-size: 13px;[\s\S]*line-height: 1\.1/);
  assert.doesNotMatch(app, /neko-pet-care-window-dots|neko-pet-care-panel-header|neko-pet-care-status-badge/);
  assert.doesNotMatch(css, /neko-pet-care-window-dots|neko-pet-care-panel-header|neko-pet-care-status-badge/);
  assert.equal((app.match(/ui\.footer\.hidden\s*=/g) || []).length, 1);
  assert.doesNotMatch(app, /var footerVisible|var loadedTasks|function togglePanel/);
  assert.match(css, /\.neko-pet-care-action-icon/);
  assert.match(css, /\.neko-pet-care-action-label/);
  assert.match(css, /\.neko-pet-care-panel-footer \{[\s\S]*right: 16px;[\s\S]*bottom: 168px/);
});

test('stage 9.1 keeps transparent layout passthrough and opaque blocks hittable', () => {
  assert.equal((css.match(/#neko-pet-care-entry-overlay \.neko-pet-care-surface \{/g) || []).length, 1);
  assert.equal((css.match(/#neko-pet-care-entry-overlay \.neko-pet-care-rail \{/g) || []).length, 1);
  assert.equal((css.match(/#neko-pet-care-entry-overlay \.neko-pet-care-stats \{/g) || []).length, 1);
  assert.equal((css.match(/#neko-pet-care-entry-overlay \.neko-pet-care-actions \{/g) || []).length, 1);
  assert.doesNotMatch(css, /\.neko-pet-care-scene\[data-exploration="active"\] ~ \.neko-pet-care-action-details/);
  assert.doesNotMatch(css, /\.neko-pet-care-scene\[data-exploration="active"\] ~ \.neko-pet-care-action-error/);
  assert.match(css, /\.neko-pet-care-surface > \*,\n#neko-pet-care-entry-overlay \.neko-pet-care-surface \.neko-pet-care-module/);
  assert.match(css, /\.neko-pet-care-surface \[data-neko-pet-care-interactive="true"\],[\s\S]*data-neko-pet-care-input-surface="true"\] \{\n    pointer-events: auto/);
  assert.doesNotMatch(css, /猫形态自由区|猫咪自由区|场景层|free scene|自由区/);
});

test('stage 9.1 keeps a visible close affordance outside the content cards', () => {
  assert.match(app, /ui\.closeButton\.hidden = !state\.panelOpen;/);
  assert.doesNotMatch(app, /ui\.closeButton\.hidden = [^;]*explorationActive/);
  assert.match(css, /data-expanded="true"\] \.neko-pet-care-close-button:not\(\[hidden\]\)/);
  assert.match(css, /\.neko-pet-care-close-button:hover/);
  assert.match(css, /\.neko-pet-care-close-button:focus-visible/);
});

test('stage 9.1 publishes controls and opaque surfaces without claiming the transparent canvas', () => {
  assert.match(app, /data-neko-pet-care-interactive/);
  assert.match(app, /data-neko-pet-care-input-surface/);
  assert.match(app, /value\.setAttribute\('data-neko-pet-care-interactive', 'true'\)/);
  assert.match(app, /function markPetCareInputSurface/);
  for (const target of ['explorationStatus', 'grid', 'actionDetails', 'actionError', 'task', 'nav', 'lettersModule', 'inventoryModule', 'profileModule']) {
    assert.match(app, new RegExp('markPetCareInputSurface\\(' + target + '\\)'));
  }
  assert.doesNotMatch(app, /root\.setAttribute\(['"]data-neko-pet-care-interactive/);
  assert.doesNotMatch(app, /panel\.setAttribute\(['"]data-neko-pet-care-interactive/);
  assert.doesNotMatch(app, /markPetCareInputSurface\((?:root|panel|scene|content|actions|footer)\)/);
});

test('stage 9.1 keeps an independent nest scene while exploration owns the activity card', () => {
  assert.match(app, /ui\.scene\.hidden = !loggedInSurface \|\| \(!activeExplorationAvailable && view !== 'overview'\)/);
  assert.match(app, /ui\.explorationVisual\.hidden = !activeExplorationAvailable/);
  assert.match(app, /neko-pet-care-exploration-nest/);
  assert.match(app, /neko-pet-care-exploration-nest-image/);
  assert.match(app, /explorationNestImage\.src = nestAsset/);
  assert.doesNotMatch(app, /探索期间不能喂食、清洁或陪玩，桌面原位置会保留离场提示。/);
  assert.match(app, /ui\.grid\.hidden = !loggedInSurface \|\| activeExplorationAvailable \|\| view !== 'overview'/);
  assert.match(app, /renderExplorationDetails\(ui\.actionDetails\)/);
  assert.match(app, /statusBlock\.appendChild\(status\); container\.appendChild\(statusBlock\)/);
  assert.match(css, /neko-pet-care-exploration-nest/);
  assert.match(css, /neko-pet-care-exploration-status-card[\s\S]*left: 16px;[\s\S]*top: 42px/);
  assert.match(css, /neko-pet-care-exploration-nest-image/);
  assert.doesNotMatch(css, /neko-pet-care-exploration-note-card/);
  assert.match(css, /neko-pet-care-exploration-visual \{ position: absolute; inset: 0; \}/);
  assert.match(css, /neko-pet-care-exploration-nest \{\n    position: absolute;/);
  assert.match(css, /neko-pet-care-scene\[data-exploration="active"\][\s\S]*left: 20px/);
  assert.doesNotMatch(app, /猫形态自由区|neko-pet-care-scene-hint/);
  const reconcileStart = app.indexOf('function reconcileExplorationFromSnapshot');
  const activeStart = app.indexOf('if (active)', reconcileStart);
  const timerStart = app.indexOf('scheduleExplorationTimer()', activeStart);
  assert.ok(reconcileStart >= 0 && activeStart > reconcileStart && timerStart > activeStart);
  assert.match(app.slice(activeStart, timerStart), /state\.panelOpen\s*=\s*true/);
  assert.match(app.slice(activeStart, timerStart), /visible\(true\)/);
  const leaseStart = app.indexOf('function applyExplorationRecoveryLease');
  const leaseEnd = app.indexOf('function scheduleExplorationTimer', leaseStart);
  assert.ok(leaseStart >= 0 && leaseEnd > leaseStart);
  assert.match(app.slice(leaseStart, leaseEnd), /state\.panelOpen\s*=\s*true/);
  assert.match(app.slice(leaseStart, leaseEnd), /scheduleExplorationTimer\(\)/);
});

test('stage 9.1 keeps exploration letters in the inbox module only', () => {
  assert.match(app, /function availableServiceLetters\(\)/);
  assert.match(app, /return Array\.isArray\(snapshotLetters\) \? snapshotLetters : explorationLetters\(currentExploration\(\)\)/);
  assert.match(app, /var availableLetters = availableServiceLetters\(\)/);
  assert.match(app, /ui\.lettersList/);
  assert.match(app, /safePetCareImageUrl/);
  assert.match(app, /neko-pet-care-letter-thumbnail/);
  assert.doesNotMatch(app, /mailPreview|neko-pet-care-mail-line/);
  assert.doesNotMatch(css, /neko-pet-care-mail-preview|neko-pet-care-mail-line/);
  assert.match(css, /neko-pet-care-nav-item\.has-alert::after/);
});

test('stage 9.1 renders server-backed empty states for secondary modules', () => {
  assert.match(app, /暂无来信/);
  assert.match(app, /暂无可用道具/);
  assert.match(app, /if \(!Number\.isFinite\(confirmedCount\) \|\| confirmedCount <= 0\) return;/);
  assert.match(app, /ui\.lettersList/);
  assert.match(app, /ui\.inventoryList/);
  assert.match(app, /ui\.profileBody/);
  assert.doesNotMatch(app, /小鱼干.*×\s*0/);
  assert.match(css, /\.neko-pet-care-inventory-list \{[\s\S]*grid-template-columns: repeat\(4, 44px\)/);
  assert.match(css, /\.neko-pet-care-inventory-empty \{[\s\S]*grid-column: 1 \/ -1;[\s\S]*text-align: center;/);
  assert.match(css, /\.neko-pet-care-inventory-item \{/);
  assert.match(css, /\.neko-pet-care-inventory-icon \{/);
  assert.match(css, /\.neko-pet-care-module \{[\s\S]*box-sizing: border-box;[\s\S]*width: 100%;/);
});

test('stage 9.1 uses compact inventory images with bottom-right counts and hover names', () => {
  assert.match(app, /small_fish:\s*'\/static\/assets\/neko-idle\/pet-care-tool\/eat\/small-fish\.png'/);
  assert.match(app, /teaser_wand:\s*'\/static\/assets\/neko-idle\/pet-care-tool\/play\/cat-teaser\.png'/);
  assert.doesNotMatch(app, /bell_teaser/);
  assert.match(app, /className = 'neko-pet-care-inventory-image'/);
  assert.match(app, /contentButton\.dataset\.itemId = item\.action && item\.action\.inventory_item \|\| item\.id/);
  assert.match(app, /toyButton\.dataset\.itemId = item\.action && item\.action\.inventory_item \|\| item\.id/);
  assert.equal(fs.existsSync(path.join(root, 'static', 'assets', 'neko-idle', 'pet-care-tool', 'eat', 'small-fish.png')), true);
  assert.equal(fs.existsSync(path.join(root, 'static', 'assets', 'neko-idle', 'pet-care-tool', 'play', 'cat-teaser.png')), true);
  assert.match(app, /card\.title = presentation\.description[\s\S]*presentation\.label \+ '：' \+ presentation\.description : presentation\.label/);
  assert.match(app, /itemDirectoryAssetForItem\(itemId\)/);
  assert.doesNotMatch(app, /className = 'neko-pet-care-inventory-label'/);
  assert.match(app, /className = 'neko-pet-care-inventory-count'/);
  assert.match(app, /quantity\.textContent = '×' \+ String\(entry\.count\)/);
  assert.match(css, /\.neko-pet-care-inventory-item \{[\s\S]*position: relative;[\s\S]*width: 44px;[\s\S]*height: 44px;[\s\S]*border: 0;[\s\S]*background: transparent;/);
  assert.match(css, /\.neko-pet-care-inventory-image \{[\s\S]*position: absolute;[\s\S]*left: 50%;[\s\S]*top: 50%;[\s\S]*width: 62px;[\s\S]*height: 62px;[\s\S]*transform: translate\(-50%, -50%\);/);
  assert.match(css, /inventory-icon\[data-item-id="teaser_wand"\][\s\S]*width: 112px;[\s\S]*height: 112px;/);
  assert.doesNotMatch(css, /bell_teaser/);
  assert.match(css, /\.neko-pet-care-inventory-count \{[\s\S]*right: -3px;[\s\S]*bottom: -3px;[\s\S]*font-size: 10px;/);
});

test('stage 9.1 shows each care tool image in its complete action row', () => {
  assert.match(app, /contentButton\.dataset\.itemId = item\.action && item\.action\.inventory_item \|\| item\.id/);
  assert.match(app, /className = 'neko-pet-care-content-image'/);
  assert.equal(fs.existsSync(path.join(root, 'static', 'assets', 'neko-idle', 'pet-care-tool', 'wash', 'wool-comb.png')), true);
  assert.match(css, /content-action\[data-item-id="small_fish"\]::before[\s\S]*small-fish\.png[\s\S]*background-size: 50px 50px/);
  assert.match(css, /content-action\[data-item-id="brushing"\]::before[\s\S]*wool-comb\.png[\s\S]*background-size: 72px 72px/);
  assert.match(css, /content-action\[data-item-id="teaser_wand"\]::before[\s\S]*cat-teaser\.png[\s\S]*background-size: 86px 86px/);
});
