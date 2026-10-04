const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.resolve(__dirname, '..', '..');

function source(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

test('main app hosts theater runtime while selector remains a child page', () => {
  const index = source('templates/index.html');
  const chat = source('templates/chat.html');
  const selector = source('templates/theater.html');
  const runtime = source('static/app/app-theater-runtime.js');

  // 三个页面都先加载共享协议，但只有聊天宿主加载胶囊运行时。
  assert.match(index, /\/static\/js\/theater_transport\.js/);
  assert.match(chat, /\/static\/js\/theater_transport\.js/);
  assert.match(selector, /\/static\/js\/theater_transport\.js/);
  assert.match(index, /\/static\/app\/app-theater-runtime\.js/);
  assert.match(chat, /\/static\/app\/app-theater-runtime\.js/);
  assert.doesNotMatch(selector, /app-theater-runtime\.js/);
  assert.match(selector, /data-theater-selector-app/);
  assert.match(selector, /\/static\/js\/theater_selector\.js/);
  assert.match(runtime, /new BroadcastChannel\('neko_page_channel'\)/);
  assert.match(runtime, /theater:launch-request/);
  assert.match(runtime, /theater:launch-ready/);
  assert.match(runtime, /theater:selector-ready/);
  assert.match(runtime, /theater:post-end/);
});

test('removed free theater runtime cannot leak into main app', () => {
  const files = [
    'main_routers/theater_router.py',
    'services/theater/free_runtime.py',
    'services/theater/free_seed.py',
    'services/theater/free_role_card.py',
    'static/js/theater.js',
    'templates/theater_home.html',
    'templates/theater_numeric.html',
  ];
  files.forEach((relativePath) => {
    assert.equal(fs.existsSync(path.join(repoRoot, relativePath)), false, `${relativePath} should be removed`);
  });

  assert.doesNotMatch(source('app/main_server/web_app.py'), /from main_routers\.theater_router/);
  assert.doesNotMatch(source('static/app/app-theater-runtime.js'), /\/api\/theater\/free/);
});
