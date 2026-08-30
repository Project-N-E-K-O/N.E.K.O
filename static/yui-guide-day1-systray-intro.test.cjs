const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const repoRoot = path.resolve(__dirname, '..');
const universalManagerSource = fs.readFileSync(
  path.join(repoRoot, 'static', 'tutorial/core/universal-manager.js'),
  'utf8'
);
const yuiGuideCssSource = fs.readFileSync(
  path.join(repoRoot, 'static', 'css/yui-guide.css'),
  'utf8'
);
const i18nBootstrapSource = fs.readFileSync(
  path.join(repoRoot, 'static', 'i18n-i18next.js'),
  'utf8'
);
const zhCnLocaleSource = fs.readFileSync(
  path.join(repoRoot, 'static', 'locales/zh-CN.json'),
  'utf8'
);
const launcherSpecSource = fs.readFileSync(
  path.join(repoRoot, 'specs', 'launcher.spec'),
  'utf8'
);
const localeCodes = ['en', 'es', 'ja', 'ko', 'pt', 'ru', 'zh-CN', 'zh-TW'];
const localeSources = Object.fromEntries(localeCodes.map((code) => [
  code,
  fs.readFileSync(path.join(repoRoot, 'static', `locales/${code}.json`), 'utf8')
]));

function getMethodBlock(source, methodName) {
  const start = source.indexOf(`\n    ${methodName}(`);
  assert.notEqual(start, -1, `expected ${methodName} method`);
  const openBrace = source.indexOf('{', start);
  assert.notEqual(openBrace, -1, `expected ${methodName} opening brace`);
  let depth = 0;
  for (let index = openBrace; index < source.length; index += 1) {
    const character = source[index];
    if (character === '{') depth += 1;
    if (character === '}') {
      depth -= 1;
      if (depth === 0) {
        return source.slice(start, index + 1);
      }
    }
  }
  assert.fail(`expected ${methodName} closing brace`);
}

function getCssRuleBlock(source, selector) {
  const start = source.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `expected ${selector} CSS rule`);
  const end = source.indexOf('\n}', start);
  assert.notEqual(end, -1, `expected ${selector} CSS rule closing brace`);
  return source.slice(start, end + 2);
}

test('Day1 tutorial end schedules the system tray intro for complete, skip and angry exit paths', () => {
  const onTutorialEndBlock = getMethodBlock(universalManagerSource, 'onTutorialEnd');

  assert.match(onTutorialEndBlock, /const scheduledDay1SystrayIntroPromise = this\.scheduleDay1SystrayIntroAfterTeardown\(/);
  const promiseAllMatch = onTutorialEndBlock.match(
    /const day1SystrayIntroPromise = Promise\.all\(\[([\s\S]*?)\]\)/
  );
  assert.ok(promiseAllMatch, 'expected day1SystrayIntroPromise to be created with Promise.all');
  assert.match(
    promiseAllMatch[1],
    /scheduledDay1SystrayIntroPromise[\s\S]*stateFlushPromise/
  );
  assert.match(onTutorialEndBlock, /return day1SystrayIntroPromise;/);
  assert.match(onTutorialEndBlock, /endMeta\.rawReason/);
  assert.match(onTutorialEndBlock, /avatarFloatingEndState/);

  const shouldShowBlock = getMethodBlock(universalManagerSource, 'shouldShowDay1SystrayIntro');
  assert.match(shouldShowBlock, /avatarFloatingEndState\.day !== 1/);
  assert.match(shouldShowBlock, /endMeta\.reason === 'complete'/);
  assert.match(shouldShowBlock, /endMeta\.reason === 'skip'/);

  const scheduleBlock = getMethodBlock(universalManagerSource, 'scheduleDay1SystrayIntroAfterTeardown');
  assert.match(scheduleBlock, /Promise\.resolve\(teardownPromise\)\.finally/);
  assert.match(scheduleBlock, /showDay1SystrayIntroModal\(endMeta, avatarFloatingEndState\)/);
});

test('Day1 system tray intro modal combines the tray location and menu guidance', () => {
  assert.match(universalManagerSource, /showDay1SystrayIntroModal\(endMeta, avatarFloatingEndState\)/);
  assert.match(universalManagerSource, /tutorial\.systray\.location\.title/);
  assert.match(universalManagerSource, /tutorial\.systray\.menu\.title/);
  assert.match(universalManagerSource, /tutorial\.systray\.menu\.desc/);
  assert.match(universalManagerSource, /tutorial\.systray\.resetPosition/);
  assert.match(universalManagerSource, /tutorial\.systray\.openChat/);
  assert.match(universalManagerSource, /tutorial\.systray\.hotkey/);
  assert.match(universalManagerSource, /tutorial\.systray\.exit/);
  assert.match(universalManagerSource, /\/static\/assets\/tutorial\/systray\/stray_intro\.png/);
  assert.doesNotMatch(universalManagerSource, /neko菜单面板/);
  assert.doesNotMatch(universalManagerSource, /\/static\/icons\/be75ec4fbd08bf74adfeb2c19e323b3b\.png/);
  assert.doesNotMatch(universalManagerSource, /\/static\/icons\/489d10e622b89904a6441a3df869eff7\.png/);
  assert.match(universalManagerSource, /neko-day1-systray-intro-modal/);
  assert.match(
    universalManagerSource,
    /class="neko-day1-systray-media"[\s\S]*tutorial\.systray\.location\.title[\s\S]*class="neko-day1-systray-content"[\s\S]*tutorial\.systray\.menu\.title/
  );
  assert.match(
    universalManagerSource,
    /class="neko-day1-systray-location-copy"[\s\S]*tutorial\.systray\.location\.title[\s\S]*\/static\/assets\/tutorial\/systray\/stray_intro\.png/
  );
  assert.match(universalManagerSource, /t\('common\.confirm', '确认'\)/);
  assert.doesNotMatch(universalManagerSource, /neko-day1-systray-primary" type="button">\$\{this\.safeEscapeHtml\(t\('common\.ok'/);
  assert.match(
    universalManagerSource,
    /<\/div>\s*<\/div>\s*<div class="neko-day1-systray-actions">[\s\S]*neko-day1-systray-primary/
  );

  assert.match(yuiGuideCssSource, /\.neko-day1-systray-intro-modal/);
  assert.match(yuiGuideCssSource, /\.neko-day1-systray-card/);
  assert.match(
    yuiGuideCssSource,
    /\.neko-day1-systray-card\s*\{[\s\S]*display:\s*flex;[\s\S]*flex-direction:\s*column;/
  );
  assert.match(yuiGuideCssSource, /\.neko-day1-systray-layout/);
  assert.match(
    yuiGuideCssSource,
    /\.neko-day1-systray-layout\s*\{[\s\S]*grid-template-rows:\s*minmax\(0,\s*1fr\);[\s\S]*flex:\s*1 1 auto;[\s\S]*min-height:\s*0;[\s\S]*overflow:\s*hidden;/
  );
  assert.match(yuiGuideCssSource, /\.neko-day1-systray-location-copy/);
  assert.match(yuiGuideCssSource, /\.neko-day1-systray-menu-panel/);
  assert.doesNotMatch(
    getCssRuleBlock(yuiGuideCssSource, '.neko-day1-systray-media img'),
    /box-shadow/
  );
  assert.match(yuiGuideCssSource, /@media \(max-width: 620px\)/);
  assert.match(launcherSpecSource, /add_data\('static', 'static'\)/);

  const zhCn = JSON.parse(zhCnLocaleSource);
  assert.equal(zhCn.tutorial.systray.location.title, '📍 托盘图标位置');
  assert.equal(
    zhCn.tutorial.systray.location.desc,
    'N.E.K.O 的图标会出现在屏幕右下角的系统托盘里，点击一下就能找到它。'
  );
  assert.equal(zhCn.tutorial.systray.location.alt, '系统托盘位置示意图');
  assert.equal(zhCn.tutorial.systray.menu.title, '📋 托盘菜单');
  assert.equal(zhCn.tutorial.systray.resetPosition, '重置角色位置');
  // 这条断言真正要保的是：day1 systray 教程文案落地时 locale 缓存键跟着递增过，
  // 老客户端不会拿旧语言包把新 key 渲染成字面量。原来的写法把当时的版本串钉死，
  // 于是每一次与 systray 无关的递增都要顺手来改这一行（#2882 就被迫改过一次），
  // 和 tests/unit/test_window_pin_static_contracts.py 里记下的那次同款腐烂。
  // 版本串固定是 YYYY-MM-DD- 前缀，比日期前缀即可表达“不早于本文案落地的那次递增”。
  const localeVersionMatch = i18nBootstrapSource.match(/LOCALE_VERSION = '([^']+)'/);
  assert.ok(localeVersionMatch, 'i18n-i18next.js 必须声明 LOCALE_VERSION');
  const localeVersion = localeVersionMatch[1];
  assert.match(localeVersion, /^\d{4}-\d{2}-\d{2}-/, `LOCALE_VERSION 需带日期前缀: ${localeVersion}`);
  assert.ok(
    localeVersion.slice(0, 10) >= '2026-07-06',
    `LOCALE_VERSION 早于 day1 systray 文案落地的 2026-07-06-day1-systray-intro-i18n: ${localeVersion}`
  );

  for (const [code, source] of Object.entries(localeSources)) {
    const locale = JSON.parse(source);
    assert.equal(typeof locale.tutorial.systray.location.desc, 'string', `${code} systray location desc`);
    assert.ok(locale.tutorial.systray.location.desc.trim(), `${code} systray location desc should not be empty`);
    assert.doesNotMatch(locale.tutorial.systray.location.desc, /right-click|clic derecho|右クリック|오른쪽 버튼|botão direito|правой кнопкой|鼠标右击|滑鼠右鍵/i);
    assert.doesNotMatch(locale.tutorial.systray.location.desc, /menu panel|panel del menú|メニューパネル|메뉴 패널|painel do menu|панель меню|菜单面板|選單面板/i);
  }
});
