/* Production React, invitation controller and native geometry in isolated Electron windows. */
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const [base, output] = process.argv.slice(-2);
app.setPath('userData', path.join(output, 'profile'));
const windows = [], popups = [], errors = [];
let stage = 'starting';
async function waitFor(win, expression, timeout = 10000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (await win.webContents.executeJavaScript(expression)) return;
        await new Promise(resolve => setTimeout(resolve, 40));
    }
    throw new Error('Timed out: ' + expression);
}
async function click(win, selector) {
    // The shared composer deduplicates clicks until its next animation frame.
    await win.webContents.executeJavaScript('new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))');
    const point = await win.webContents.executeJavaScript(`(() => {
        const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect();
        if(!e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)))throw Error('button is obscured');
        return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
    })()`);
    win.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});
    win.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});
}
async function capture(win, name) {
    // Count painted consent pixels inside the invitation, excluding the blue capsule.
    const deadline = Date.now()+5000;
    while (Date.now()<deadline) {
        const region=await win.webContents.executeJavaScript("({r:document.querySelector('.mini-game-invite').getBoundingClientRect().toJSON(),viewport:innerWidth})");
        const bitmap = await win.webContents.capturePage();
        const pixels = bitmap.getBitmap();
        const size=bitmap.getSize(), ratio=size.width/region.viewport;
        let blue=0;
        for(let y=Math.max(0,Math.ceil(region.r.top*ratio));y<Math.min(size.height,region.r.bottom*ratio);y++){
            for(let x=Math.max(0,Math.ceil(region.r.left*ratio));x<Math.min(size.width,region.r.right*ratio);x++){
                const i=(y*size.width+x)*4;
                if(pixels[i]>pixels[i+2]+40 && pixels[i+2]<160)blue++;
            }
        }
        if(blue>100){fs.writeFileSync(path.join(output,name),bitmap.toPNG());return;}
    }
    throw Error('Invitation was not painted');
}
async function run() {
    for (const [index, route] of ['/chat?mode=full','/?mode=compact'].entries()) {
        const win = new BrowserWindow({show:false,width:460,height:680,webPreferences:{partition:'persist:invite-'+index,contextIsolation:true,backgroundThrottling:false}});
        windows.push(win);
        win.webContents.on('console-message', (_event, level, message) => { if(level>=3 && !message.includes('503'))errors.push(message); });
        win.webContents.setWindowOpenHandler(()=>({action:'allow',overrideBrowserWindowOptions:{show:false}}));
        win.webContents.on('did-create-window', popup=>popups.push(popup));
        await win.loadURL(base+route);
        await waitFor(win,'window.smokeReady');
        await win.webContents.executeJavaScript("window.offerInvite('shared-invite')");
    }
    const [full, compact] = windows;
    stage = 'full-card-and-compact-bubble';
    await waitFor(full,"document.querySelectorAll('.message-list .mini-game-invite-card button').length===3");
    await waitFor(compact,"document.querySelectorAll('.compact-chat-choice-anchor .mini-game-invite-bubble button').length===3");
    await capture(full,'invite-full-card.png');
    await capture(compact,'invite-compact-bubble.png');
    for(const win of windows) assert.deepEqual(await win.webContents.executeJavaScript('window.guideTargets()'),['accept','decline','later'],'tutorial finds all invitation choices in either layout');
    stage = 'native-click-geometry';
    const geometry = await compact.webContents.executeJavaScript("({g:window.inviteGeometry(),r:document.querySelector('.mini-game-invite').getBoundingClientRect().toJSON()})");
    fs.writeFileSync(path.join(output,'compact-geometry.json'),JSON.stringify(geometry,null,2));
    const native = geometry.g.surfaceItems.find(item=>item.id==='choice');
    assert.ok(native && native.hitRect,'invitation contributes native window and click regions');
    assert.ok(native.nativeRect.top<=geometry.r.top+1 && native.nativeRect.bottom>=geometry.r.bottom-1,'native bounds include the whole bubble');
    for(const choice of ['accept','decline','later']){
        stage = 'click-'+choice;
        const id='decision-'+choice;
        for(const win of windows) await win.webContents.executeJavaScript(`window.offerInvite(${JSON.stringify(id)})`);
        await waitFor(compact,"document.querySelector('[data-invite-session-id="+JSON.stringify(id)+"]')!==null");
        const actor=choice==='decline'?full:compact;
        await click(actor,`[data-invite-choice="${choice}"]`);
        await waitFor(actor,"fetch('/decisions').then(r=>r.json()).then(xs=>xs.some(x=>x.session_id==="+JSON.stringify(id)+"))");
        // Existing WebSocket/IPC consumers deliver this public resolved event to each surface.
        for(const win of windows) await win.webContents.executeJavaScript(`window.resolveInvite({sessionId:${JSON.stringify(id)},action:${JSON.stringify(choice==='accept'?'open_game':'cooldown')}})`);
        for(const win of windows) await waitFor(win,"!document.querySelector('.mini-game-invite')");
        if(choice==='accept'){
            const popupDeadline = Date.now() + 10000;
            while (popups.length < 1 && Date.now() < popupDeadline) {
                await new Promise(resolve => setTimeout(resolve, 40));
            }
            assert.equal(popups.length,1,'accept opens one game window');
            await waitFor(popups[0],"location.pathname==='/game'");
        } else assert.equal(popups.length,1,'skip/later do not open games');
    }
    stage = 'failed-request-restores-invitation';
    await compact.webContents.executeJavaScript("window.offerInvite('retry-test')");
    await waitFor(compact,"!!document.querySelector('.mini-game-invite button')");
    await click(compact,'[data-invite-choice="decline"]');
    await waitFor(compact,"!!document.querySelector('.mini-game-invite button')");
    await click(compact,'[data-invite-choice="decline"]');
    await waitFor(compact,"fetch('/decisions').then(r=>r.json()).then(xs=>xs.some(x=>x.session_id==='retry-test'))");
    stage = 'narrow-and-dark';
    full.setSize(340,680);
    await full.webContents.executeJavaScript("document.documentElement.dataset.theme='dark';window.offerInvite('narrow-test')");
    await waitFor(full,"document.querySelector('.mini-game-invite').getBoundingClientRect().width<340");
    assert.equal(await full.webContents.executeJavaScript("document.querySelector('.mini-game-invite').scrollWidth>document.querySelector('.mini-game-invite').clientWidth+1"),false);
    await capture(full,'invite-full-narrow-dark.png');
    const decisions=await full.webContents.executeJavaScript("fetch('/decisions').then(r=>r.json())");
    assert.equal(decisions.length,4,'one HTTP response per action, including a successful retry');
    assert.equal(errors.length,0,errors.join('\n'));
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,electron:process.versions.electron,productionInviteController:true,productionNativeGeometry:true,decisions,errors},null,2));
}
app.whenReady().then(run).then(()=>{for(const win of [...windows,...popups])if(!win.isDestroyed())win.destroy();app.exit(0);}).catch(async error=>{
    const renderers=[];
    for(const win of windows)try{renderers.push(await win.webContents.executeJavaScript("({prompt:window.__appReactChatWindowParts.state.choicePrompt,choices:document.querySelector('.mini-game-invite')?.outerHTML})"));}catch{}
    for(const [i,win] of windows.entries())try{fs.writeFileSync(path.join(output,'failure-'+i+'.png'),(await win.webContents.capturePage()).toPNG());}catch{}
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:false,stage,error:String(error.stack),errors,renderers},null,2));
    for(const win of [...windows,...popups])if(!win.isDestroyed())win.destroy();app.exit(1);
});
