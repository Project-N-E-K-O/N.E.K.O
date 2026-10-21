/* Real Chromium/Electron renderer smoke test, launched by the Python fixture. */
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const [url, output] = process.argv.slice(-2);
app.setPath('userData', path.join(output, 'electron-user-data'));
app.commandLine.appendSwitch('autoplay-policy', 'no-user-gesture-required');
const errors = [];
const windows = [];
let stage = 'starting';
async function waitFor(win, expression, timeout=15000) {
    const start=Date.now();
    while(Date.now()-start<timeout) {
        if(await win.webContents.executeJavaScript(expression))return;
        await new Promise(resolve=>setTimeout(resolve,40));
    }
    throw new Error('Renderer wait timed out: '+expression);
}
async function captureVisibleRecommendation(win, selector) {
    const region = await win.webContents.executeJavaScript(`(() => { const r=document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height,viewport:innerWidth}; })()`);
    const deadline = Date.now()+5000;
    while (Date.now()<deadline) {
        const capture = await win.webContents.capturePage();
        const size = capture.getSize();
        const ratio = size.width/region.viewport;
        const bitmap = capture.getBitmap();
        let colored=0, dark=0;
        for (let y=Math.max(0,Math.ceil(region.y*ratio));y<Math.min(size.height,(region.y+region.height)*ratio);y++) {
            for (let x=Math.max(0,Math.ceil(region.x*ratio));x<Math.min(size.width,(region.x+region.width)*ratio);x++) {
                const offset=(y*size.width+x)*4;
                const colors=[bitmap[offset],bitmap[offset+1],bitmap[offset+2]];
                if(Math.max(...colors)-Math.min(...colors)>40 && Math.min(...colors)<180)colored++;
                if(Math.max(...colors)<190)dark++;
            }
        }
        // Wait for painted text and the colored consent button, not just DOM
        // insertion: hidden Electron windows can still hold the previous frame.
        if(colored>30 && dark>15)return capture;
    }
    throw new Error('Recommendation was laid out but did not become visible in the captured frame');
}
async function run() {
    const pcRoot = process.env.NEKO_MUSIC_SMOKE_PC_ROOT || path.resolve(path.dirname(process.execPath), '../../..');
    assert.ok(fs.existsSync(path.join(pcRoot, 'src/ipc-router.js')), 'desktop source is required for the production IPC smoke test');
    const preload = path.join(output, 'music-preload.cjs');
    fs.writeFileSync(preload, "require(" + JSON.stringify(path.join(pcRoot, 'src/preload/shared/common.js')) + ").setupMusicPlayerBridge();");
    const makeWindow = partition => {
        const win = new BrowserWindow({show:false,width:460,height:680,webPreferences:{preload,partition,contextIsolation:true,sandbox:false,backgroundThrottling:false}});
        windows.push(win);
        win.webContents.setAudioMuted(true);
        win.webContents.on('console-message', (_event, level, message) => { if(level>=3)errors.push(message); });
        return win;
    };
    const owner = makeWindow('persist:music-smoke-primary');
    const follower = makeWindow('persist:music-smoke-full-chat');
    assert.notEqual(owner.webContents.session, follower.webContents.session, 'real isolated Electron sessions');
    require(path.join(pcRoot, 'src/ipc-router.js')).setupIPCRouter({getWindows:()=>({pet:owner,chat:follower}),log(){}});
    stage = 'load-isolated-renderers';
    await follower.loadURL(url + '?follower=1');
    await waitFor(follower,'window.smokeReady === true');
    await owner.loadURL(url);
    await waitFor(owner,"window.smokeReady && document.querySelectorAll('.music-recommendation-card button').length === 6");
    stage = 'mirror-cards-across-partitions';
    await waitFor(follower,"document.querySelectorAll('.music-recommendation-card button').length === 6");
    stage = 'late-window-replays-cards';
    await follower.loadURL(url + '?follower=1&reopened=1');
    await waitFor(follower,"window.smokeReady && document.querySelectorAll('.music-recommendation-card button').length === 6");
    assert.equal(await follower.webContents.executeJavaScript("window.nekoMusicRecommendations.hasPending()"),true);
    stage = 'actual-viewport-exposures';
    for (let index=0;index<3;index++) {
        await follower.webContents.executeJavaScript(`document.querySelectorAll('.music-recommendation-card')[${index}].scrollIntoView({block:'center',behavior:'instant'})`);
        await waitFor(follower,`fetch('/api/proactive/recommendations/events', {headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json()).then(r=>r.events.some(e=>e.kind==='shown' && e.recommendation_id===document.querySelectorAll('.music-recommendation-card')[${index}].dataset.recommendationId))`);
    }
    await follower.webContents.executeJavaScript("document.querySelector('.music-recommendation-card').scrollIntoView({block:'start',behavior:'instant'})");
    stage = 'compact-bubble-and-full-card';
    // Keep one historical pending offer (without a countdown) for the later manual race.
    await owner.webContents.executeJavaScript(`(() => { const r=window.smokeMusicRecommendations[2], messageId='music-recommendation-'+r.id;
        const patch={blocks:[{
            type:'music_recommendation',recommendationId:r.id,track:r.track,state:'pending',expiresAt:r.expires_at*1000
        }]};
        window.__nekoMirrorChatUpdate(window.reactChatWindowHost,messageId,patch);
        window.dispatchEvent(new CustomEvent('neko:chat-mirror',{detail:{type:'update',messageId,patch}})); })()`);
    await owner.webContents.executeJavaScript("window.setSmokeSurfaceMode('compact')");
    await waitFor(owner,"document.querySelectorAll('.compact-music-recommendation-prompt .music-recommendation-bubble button').length === 2");
    await waitFor(owner,"(() => { const e=document.querySelector('.compact-music-recommendation-prompt'); const r=e.getBoundingClientRect(); return r.width>0 && r.top>=0 && r.bottom<=innerHeight && e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); })()");
    assert.equal(await owner.webContents.executeJavaScript("document.querySelectorAll('.compact-music-recommendation-prompt img,.music-recommendation-card').length"),0);
    fs.writeFileSync(path.join(output,'recommendation-compact-before.png'),(await captureVisibleRecommendation(owner,'.compact-music-recommendation-prompt')).toPNG());
    await owner.webContents.executeJavaScript("window.setSmokeSurfaceMode('full')");
    await waitFor(owner,"document.querySelectorAll('.music-recommendation-card').length === 3");
    for (const win of windows) assert.equal(await win.webContents.executeJavaScript("document.querySelectorAll('audio').length"),0,'offering never creates audio');
    fs.writeFileSync(path.join(output,'recommendation-before.png'),(await follower.webContents.capturePage()).toPNG());
    stage = 'skip-from-follower';
    await follower.webContents.executeJavaScript("document.querySelectorAll('.music-recommendation-card')[0].querySelectorAll('button')[1].click()");
    await waitFor(owner,"document.querySelectorAll('.music-recommendation-card')[0].textContent.includes('这次先不听')");
    for (const win of windows) assert.equal(await win.webContents.executeJavaScript("document.querySelectorAll('audio').length"),0,'skip never creates audio');
    stage = 'automatic-countdown-play-pause-resume-ended';
    await waitFor(follower,"fetch('/api/proactive/recommendations/events', {headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json()).then(r=>r.events.some(e=>e.kind==='accepted' && e.reason==='countdown_elapsed'))");
    const deadline = Date.now() + 10000;
    let initialPlayer;
    while (Date.now() < deadline) {
        const playing = await Promise.all(windows.map(win => win.webContents.executeJavaScript("!!(window.aplayer && !window.aplayer.audio.paused)")));
        if (playing.some(Boolean)) { assert.equal(playing.filter(Boolean).length, 1); initialPlayer = windows[playing.findIndex(Boolean)]; break; }
        await new Promise(resolve => setTimeout(resolve, 40));
    }
    assert.ok(initialPlayer, 'the ten-second deadline starts exactly one real player');
    await waitFor(initialPlayer,"window.aplayer.audio.currentTime > 0.1");
    await initialPlayer.webContents.executeJavaScript("window.aplayer.pause()");
    await waitFor(owner,"document.querySelectorAll('.music-recommendation-card')[1].textContent.includes('已暂停')");
    await initialPlayer.webContents.executeJavaScript("window.aplayer.play()");
    await waitFor(initialPlayer,"window.aplayer.audio.ended",20000);
    await waitFor(owner,"document.querySelectorAll('.music-recommendation-card')[1].textContent.includes('已听完')");
    await initialPlayer.webContents.executeJavaScript("window.destroyMusicPlayer(true,true,true)");
    await waitFor(owner,"!window.isRemoteMusicActive()");
    stage = 'concurrent-accept-single-owner';
    await follower.webContents.executeJavaScript("document.querySelectorAll('.music-recommendation-card')[2].scrollIntoView()");
    await waitFor(follower,"fetch('/api/proactive/recommendations/events', {headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json()).then(r=>r.events.filter(e=>e.kind==='shown').length===3)");
    await owner.webContents.executeJavaScript("window.setSmokeSurfaceMode('compact')");
    await waitFor(owner,"!!document.querySelector('.compact-music-recommendation-prompt button')");
    await Promise.all([
        owner.webContents.executeJavaScript("document.querySelector('.compact-music-recommendation-prompt button').click()"),
        follower.webContents.executeJavaScript("document.querySelectorAll('.music-recommendation-card')[2].querySelector('button').click()")
    ]);
    await waitFor(follower,"document.querySelectorAll('.music-recommendation-card')[2].textContent.includes('正在播放')");
    await waitFor(owner,"!document.querySelector('.compact-music-recommendation-prompt')");
    const playbackFlags = await Promise.all(windows.map(win=>win.webContents.executeJavaScript("!!(window.aplayer && !window.aplayer.audio.paused)")));
    assert.equal(playbackFlags.filter(Boolean).length,1,'concurrent acceptance produces one actual audio owner');
    const retired = windows[playbackFlags.findIndex(Boolean)];
    await waitFor(retired,"window.aplayer && window.aplayer.audio.currentTime > 0.1");
    stage = 'remote-owner-preserves-listening-facts';
    const active = windows.find(win => win !== retired);
    const trackUrl = await retired.webContents.executeJavaScript('new URL(window.aplayer.audio.src).pathname');
    const handoff = await active.webContents.executeJavaScript(`window.sendMusicMessageDetailed({name:'Handoff',artist:'Neko',url:${JSON.stringify(trackUrl + '?handoff=1')}},true,{source:'user'})`);
    assert.equal(handoff.ok, true, 'the next window starts a supported local audio source');
    await waitFor(active,"window.aplayer && !window.aplayer.audio.paused");
    await waitFor(retired,"!window.aplayer");
    await waitFor(retired,"fetch('/api/proactive/recommendations/events', {headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json()).then(r=>r.events.some(e=>e.kind==='stopped' && e.listened_seconds>0))");
    await active.webContents.executeJavaScript("document.querySelector('#music-player-bar .music-bar-close').click()");
    await waitFor(follower,"document.querySelectorAll('.music-recommendation-card')[2].textContent.includes('播放已停止')");
    await owner.webContents.executeJavaScript("window.setSmokeSurfaceMode('full')");
    await waitFor(owner,"document.querySelectorAll('.music-recommendation-card')[2].textContent.includes('播放已停止')");
    for (const win of windows) {
        await waitFor(win,"!document.getElementById('music-player-bar') && !document.querySelector('audio')");
    }
    stage = 'narrow-layout-and-durable-evidence';
    follower.setSize(340,680);
    await waitFor(follower,"document.querySelector('.music-recommendation-card').getBoundingClientRect().width < 340");
    assert.equal(await follower.webContents.executeJavaScript("Array.from(document.querySelectorAll('.music-recommendation-card')).some(e => e.scrollWidth > e.clientWidth + 1)"),false);
    fs.writeFileSync(path.join(output,'recommendation-after-narrow.png'),(await follower.webContents.capturePage()).toPNG());
    const events = await owner.webContents.executeJavaScript("fetch('/api/proactive/recommendations/events', {headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json())");
    assert.equal(events.success,true);
    assert.equal(events.events.filter(e=>e.kind==='accepted').length,2);
    assert.equal(events.events.filter(e=>e.kind==='skipped').length,1);
    assert.equal(events.events.filter(e=>e.kind==='shown').length,3,'one exposure per recommendation across windows');
    assert.ok(events.events.some(e=>e.kind==='paused'));
    assert.ok(events.events.some(e=>e.kind==='stopped'));
    const ended=events.events.find(e=>e.kind==='ended');
    assert.ok(ended && ended.listened_seconds>0,'actual media listening is persisted');
    assert.ok(!JSON.stringify(events).includes('smoke-private-token'));
    assert.equal(errors.length,0,errors.join('\n'));
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,electron:process.versions.electron,isolatedSessions:true,productionDesktopBridge:true,events:events.events.map(e=>({kind:e.kind,listened_seconds:e.listened_seconds})),errors},null,2));
}
app.whenReady().then(run).then(()=>{for(const w of windows)w.destroy();app.exit(0);}).catch(async error=>{
    const renderers=[];
    for (const [index,win] of windows.entries()) {
        try {
            renderers.push(await win.webContents.executeJavaScript("({visibility:document.visibilityState,cards:Array.from(document.querySelectorAll('.music-recommendation')).map(e=>({text:e.textContent,rect:e.getBoundingClientRect().toJSON(),visibility:getComputedStyle(e).visibility}))})"));
            fs.writeFileSync(path.join(output,`failure-${index}.png`),(await win.webContents.capturePage()).toPNG());
        } catch (_) {}
    }
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:false,stage,error:String(error.stack),errors,renderers},null,2));
    for(const w of windows)w.destroy();app.exit(1);
});
