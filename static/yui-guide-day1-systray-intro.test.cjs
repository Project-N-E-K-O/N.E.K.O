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

test('Day1 tutorial end schedules the system tray intro for complete, skip and angry exit paths', () => {
  const onTutorialEndBlock = getMethodBlock(universalManagerSource, 'onTutorialEnd');

  assert.match(onTutorialEndBlock, /const day1SystrayIntroPromise = this\.scheduleDay1SystrayIntroAfterTeardown\(/);
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

test('Day1 system tray intro modal uses the existing systray copy and image resource', () => {
  assert.match(universalManagerSource, /showDay1SystrayIntroModal\(endMeta, avatarFloatingEndState\)/);
  assert.match(universalManagerSource, /tutorial\.systray\.location\.title/);
  assert.doesNotMatch(universalManagerSource, /tutorial\.systray\.menu\./);
  assert.match(universalManagerSource, /\/static\/icons\/be75ec4fbd08bf74adfeb2c19e323b3b\.png/);
  assert.doesNotMatch(universalManagerSource, /\/static\/icons\/489d10e622b89904a6441a3df869eff7\.png/);
  assert.match(universalManagerSource, /neko-day1-systray-intro-modal/);

  assert.match(yuiGuideCssSource, /\.neko-day1-systray-intro-modal/);
  assert.match(yuiGuideCssSource, /\.neko-day1-systray-card/);

  const zhCn = JSON.parse(zhCnLocaleSource);
  assert.equal(zhCn.tutorial.systray.location.title, '📍 托盘图标位置');
  assert.equal(
    zhCn.tutorial.systray.location.desc,
    'N.E.K.O 的图标会出现在屏幕右下角的系统托盘里，点击一下就能找到它。鼠标右击就能打开neko菜单面板啦。'
  );
  assert.equal(zhCn.tutorial.systray.location.alt, '系统托盘位置示意图');
  assert.match(i18nBootstrapSource, /LOCALE_VERSION = '2026-07-06-day1-systray-intro-i18n'/);

  for (const [code, source] of Object.entries(localeSources)) {
    const locale = JSON.parse(source);
    assert.equal(typeof locale.tutorial.systray.location.desc, 'string', `${code} systray location desc`);
    assert.ok(locale.tutorial.systray.location.desc.trim(), `${code} systray location desc should not be empty`);
  }
});
