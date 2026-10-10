// Run against a locally built bundle; no provider or live installation needed.
// NEKO_CHAT_BUILD points to static/react/neko-chat; NODE_PATH provides playwright.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '../..');
const build = process.env.NEKO_CHAT_BUILD || path.join(root, 'static/react/neko-chat');
const output = process.env.NEKO_TAIL_EVIDENCE || path.join(root, 'test-results/reply-tail');
const html = `<!doctype html><html><head>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="/bundle/neko-chat-window.css">
<style>body{margin:0;background:#f0f3f5}#root{width:100%;height:100vh}</style>
</head><body><div id="root"></div>
<script src="/bundle/neko-chat-window.iife.js"></script>
<script>
window.appState={};window.appChat={};
window._realisticGeminiQueue=[];window._realisticGeminiVersion=1;
window.fixture={messages:[],mode:'full',reports:[],seq:0};
fixture.render=function(){
  NekoChatWindow.mountChatWindow(document.getElementById('root'),{
    title:'Neko',chatSurfaceMode:fixture.mode,messages:fixture.messages,
    composerHidden:true,compactHistoryOpenRequest:fixture.historyRequest
  });
};
window.reactChatWindowHost={
  getState:()=>({messages:fixture.messages}),
  appendMessage(message){
    if(fixture.messages.some(m=>m.id===message.id))return message;
    fixture.seq++;
    fixture.messages.push({sortKey:fixture.seq,createdAt:Date.now(),...message});
    fixture.messages.sort((a,b)=>a.sortKey-b.sortKey);fixture.render();return message;
  },
  updateMessage(id,patch){
    fixture.messages=fixture.messages.map(m=>m.id===id?{...m,...patch}:m);
    fixture.render();
  },
  removeMessage(id){fixture.messages=fixture.messages.filter(m=>m.id!==id);fixture.render();}
};
fixture.emit=(type,detail)=>window.dispatchEvent(new CustomEvent(type,{detail}));
fixture.start=function(turn='turn-A',request='request-A'){
  window._nekoAssistantTurnId=turn;
  fixture.emit('neko-assistant-turn-start',{turnId:turn,requestId:request});
};
fixture.end=function(turn='turn-A',request='request-A'){
  fixture.emit('neko-assistant-turn-end',{turnId:turn,requestId:request});
};
fixture.image=function(tail=true,id='image-A'){
  return appendReactChatBlocks({
    blocks:[{type:'image',url:fixture.gif}],
    metadata:{source:'plugin',source_name:'sticker_manager'},
    ...(tail?{reply_tail:{version:1,reply_id:'reply-A',request_id:'request-A',registration_id:id}}:{})
  });
};
fixture.openHistory=function(){
  fixture.historyRequest={id:'open-'+Date.now(),open:true};fixture.render();
};
window.addEventListener('neko-reply-tail-presentation',e=>{
  fixture.reports.push({...e.detail,time:performance.now()});
});
</script>
<script src="/static/app/app-reply-tail.js"></script>
<script src="/static/app/app-chat-adapter.js"></script>
<script>fixture.render();</script>
</body></html>`;

async function main() {
  fs.mkdirSync(output, { recursive: true });
  const server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(html);
      return;
    }
    const file = url.pathname.startsWith('/bundle/')
      ? path.resolve(build, url.pathname.slice(8))
      : path.resolve(root, '.' + url.pathname);
    const allowed = file.startsWith(path.resolve(build) + path.sep)
      || file.startsWith(path.join(root, 'static') + path.sep);
    if (!allowed || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.statusCode = 404; res.end(); return;
    }
    const mime = { '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png',
      '.ttf': 'font/ttf', '.json': 'application/json' };
    const contentType = mime[path.extname(file)] || 'application/octet-stream';
    res.setHeader('Content-Type', ['.js', '.css', '.json'].includes(path.extname(file))
      ? contentType + '; charset=utf-8' : contentType);
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    // Two visibly different frames allow painted-pixel checks for GIF animation.
    const gif = process.env.NEKO_TAIL_GIF;
    assert.ok(gif && fs.existsSync(gif), 'NEKO_TAIL_GIF must point to an animated fixture');
    const dataUrl = 'data:image/gif;base64,' + fs.readFileSync(gif).toString('base64');
    for (const mode of ['full', 'compact']) {
      for (const segmented of [false, true]) {
        for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
          const page = await browser.newPage({ viewport });
          const errors = [];
          page.on('pageerror', error => { errors.push(error.message); console.error(error.stack); });
          await page.goto(`http://127.0.0.1:${server.address().port}/`);
          await page.waitForFunction(() => window.__nekoReplyTailPresentationVersion === 1);
          await page.evaluate(({ mode, dataUrl }) => {
            fixture.mode = mode; fixture.gif = dataUrl; fixture.render();
          }, { mode, dataUrl });
          if (mode === 'compact' && viewport.width === 1280) await page.evaluate(() => fixture.openHistory());
          await page.evaluate(({ segmented }) => {
            fixture.start();
            window.createGeminiBubble('First sentence.', { turnId: 'turn-A' });
            if (segmented) {
              window._lastBubbleTime = Date.now();
              window._realisticGeminiQueue.push({ text: 'Final sentence.', turnId: 'turn-A' });
            } else {
              const m = fixture.messages[0];
              reactChatWindowHost.updateMessage(m.id, { blocks: [{ type: 'text', text: 'First sentence. Final sentence.' }] });
            }
            fixture.emit('neko-compact-caption-update', {
              turnId: 'turn-A', segmentId: 'segment-A', text: 'First sentence. Final sentence.',
            });
          }, { segmented });
          await page.waitForTimeout(150);
          await page.evaluate(() => {
            fixture.emit('neko-assistant-speech-unavailable', { turnId: 'turn-A' });
            fixture.end(); fixture.image();
          });
          assert.equal(await page.evaluate(() => fixture.messages.filter(m => m.role === 'system').length), 0);
          if (segmented) await page.evaluate(() => { window.processRealisticQueue(1); });
          await page.waitForFunction(() => fixture.messages.some(m => m.id.startsWith('reply-tail:')), null, { timeout: 20000 });
          const ordered = await page.evaluate(() => fixture.messages.map(m => ({ role: m.role, text: m.blocks[0].text, id: m.id })));
          assert.equal(ordered.at(-1).role, 'system');
          assert.ok(ordered.slice(0, -1).some(m => m.text.includes('Final sentence.')));
          if (mode === 'compact') {
            const last = await page.evaluate(() => fixture.reports.filter(r => r.turnId === 'turn-A').at(-1));
            assert.equal(last.complete, true);
            if (viewport.width === 390) await page.evaluate(() => fixture.openHistory());
          }
          const image = page.locator('img[src^="data:image/gif"]').first();
          await image.waitFor({ state: 'visible' });
          await page.waitForFunction(() => Array.from(document.images).some(i => i.src.startsWith('data:image/gif') && i.complete && i.naturalWidth > 0));
          const colors = new Set();
          await page.waitForTimeout(400);
          for (let frame = 0; frame < 8; frame++) {
            // Canvas drawImage may use a GIF's default frame; screenshots
            // inspect the actual currently painted pixels instead.
            colors.add(createHash('sha256').update(await image.screenshot()).digest('hex'));
            await page.waitForTimeout(70);
          }
          assert.ok(colors.size >= 2, 'GIF must animate in the actual browser');
          const name = `${mode}-${segmented ? 'segmented' : 'merged'}-${viewport.width}`;
          await page.screenshot({ path: path.join(output, name + '.png') });
          assert.deepEqual(errors, []);
          results.push({ scenario: name, passed: true, animatedFrames: colors.size });
          await page.close();
        }
      }
    }
    for (const mode of ['full', 'compact']) {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.waitForFunction(() => window.__nekoReplyTailPresentationVersion === 1);
      await page.evaluate(({ mode, dataUrl }) => {
        fixture.mode = mode; fixture.gif = dataUrl; fixture.render();
        fixture.start();
        window.createGeminiBubble('Original final sentence.', { turnId: 'turn-A' });
        fixture.end();
        reactChatWindowHost.appendMessage({
          id: 'next-user', role: 'user', author: 'You', time: '12:00',
          blocks: [{ type: 'text', text: 'Next question' }], status: 'sent',
        });
        fixture.start('turn-B', 'request-B');
        window.createGeminiBubble('Next answer.', { turnId: 'turn-B' });
        fixture.emit('neko-compact-caption-update', { turnId: 'turn-B', segmentId: 'B', text: 'Next answer.' });
        fixture.image(); fixture.image(); // Duplicate packet must not append twice.
        if (mode === 'compact') fixture.openHistory();
      }, { mode, dataUrl });
      await page.waitForFunction(() => fixture.messages.some(m => m.id.startsWith('reply-tail:')));
      assert.deepEqual(await page.evaluate(() => fixture.messages.map(m => m.role)),
        ['assistant', 'system', 'user', 'assistant']);
      await page.screenshot({ path: path.join(output, mode + '-quick-next.png') });
      results.push({ scenario: mode + '-quick-next-with-dedup', passed: true });
      await page.close();
    }
    // Old immediate path is a before-behaviour reference, on the same real renderer.
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    await page.waitForFunction(() => window.__nekoReplyTailPresentationVersion === 1);
    await page.evaluate(dataUrl => {
      fixture.gif = dataUrl; fixture.start();
      window.createGeminiBubble('First sentence.', { turnId: 'turn-A' });
      fixture.image(false);
      window.createGeminiBubble('Final sentence.', { turnId: 'turn-A' });
    }, dataUrl);
    await page.locator('img[src^="data:image/gif"]').first().waitFor({ state: 'visible' });
    assert.deepEqual(await page.evaluate(() => fixture.messages.map(m => m.role)), ['assistant', 'system', 'assistant']);
    await page.waitForFunction(() => Array.from(document.querySelectorAll('[data-message-id]')).every(node =>
      node.getAnimations({ subtree: true }).every(animation => animation.playState !== 'running'
        || animation.effect?.getComputedTiming().iterations === Infinity)));
    await page.screenshot({ path: path.join(output, 'before-immediate.png') });
    results.push({ scenario: 'old-immediate-path', passed: true });
    await page.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
    fs.writeFileSync(path.join(output, 'browser-results.json'), JSON.stringify(results, null, 2));
  }
  console.log(JSON.stringify(results, null, 2));
}
main().catch(error => { console.error(error); process.exitCode = 1; });
