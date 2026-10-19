/* Isolated Electron IPC + real recommendation HTTP/SQLite; external opens stay in a fixture sink. */
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const [url, output] = process.argv.slice(-2);
app.setPath('userData', path.join(output, 'content-profile'));
const windows=[], errors=[], external=[];
let stage='starting';
async function waitFor(win, expression, timeout=15000){
    const deadline=Date.now()+timeout;
    while(Date.now()<deadline){if(await win.webContents.executeJavaScript(expression))return;await new Promise(r=>setTimeout(r,40));}
    throw Error('Timed out: '+expression);
}
async function click(win,selector){
    const point=await win.webContents.executeJavaScript(`(() => {
        const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect();
        if(!e.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)))throw Error('button is obscured');
        return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
    })()`);
    win.webContents.sendInputEvent({type:'mouseDown',button:'left',clickCount:1,...point});
    win.webContents.sendInputEvent({type:'mouseUp',button:'left',clickCount:1,...point});
}
async function capture(win, selector, file){
    const deadline=Date.now()+5000;
    while(Date.now()<deadline){
        const region=await win.webContents.executeJavaScript(`({r:document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect().toJSON(),width:innerWidth})`);
        const capture=await win.webContents.capturePage(),size=capture.getSize(),pixels=capture.getBitmap(),ratio=size.width/region.width;
        let blue=0,dark=0;
        for(let y=Math.max(0,Math.ceil(region.r.top*ratio));y<Math.min(size.height,region.r.bottom*ratio);y++)for(let x=Math.max(0,Math.ceil(region.r.left*ratio));x<Math.min(size.width,region.r.right*ratio);x++){
            const p=(y*size.width+x)*4;if(pixels[p]>pixels[p+2]+40 && pixels[p+2]<180)blue++;
            if(Math.max(pixels[p],pixels[p+1],pixels[p+2])<190)dark++;
        }
        if(blue>30 && dark>15){fs.writeFileSync(path.join(output,file),capture.toPNG());return;}
    }
    throw Error('Recommendation did not paint in the expected region');
}
async function run(){
    const pcRoot=process.env.NEKO_MUSIC_SMOKE_PC_ROOT||path.resolve(path.dirname(process.execPath),'../../..');
    const preload=path.join(output,'content-preload.cjs');
    fs.writeFileSync(preload,`require(${JSON.stringify(path.join(pcRoot,'src/preload/shared/common.js'))}).setupMusicPlayerBridge();
        require('electron').contextBridge.exposeInMainWorld('electronShell',{openExternal:url=>require('electron').ipcRenderer.invoke('fixture:openExternal',url)});`);
    ipcMain.handle('fixture:openExternal',(_event,href)=>{assert.ok(href.startsWith('https://example.test/'));external.push(href);});
    for(let index=0;index<2;index++){
        const win=new BrowserWindow({show:false,width:460,height:680,webPreferences:{partition:'persist:content-'+index,preload,contextIsolation:true,sandbox:false,backgroundThrottling:false}});
        windows.push(win);win.webContents.setAudioMuted(true);
        win.webContents.on('console-message',(_e,level,message)=>{if(level>=3)errors.push(message);});
    }
    const [owner,follower]=windows;
    require(path.join(pcRoot,'src/ipc-router.js')).setupIPCRouter({getWindows:()=>({pet:owner,chat:follower}),log(){}});
    await follower.loadURL(url+'?follower=1');await waitFor(follower,'window.smokeReady');
    await owner.loadURL(url+'?follower=1');await waitFor(owner,'window.smokeReady');
    await owner.webContents.executeJavaScript("for(const item of [...window.smokeContentRecommendations].reverse()){if(item.category==='meme'){item.content.title='表情包_2';item.content.source='斗图啦';}window.nekoRecommendations.offer(item,'content-'+item.category)}");
    stage='all-source-cards-mirror';
    await waitFor(follower,"document.querySelectorAll('.proactive-recommendation-card').length===8");
    stage='retired-placeholder-replay';
    await owner.webContents.executeJavaScript(`for(const category of ['chat','news'])window.__nekoMirrorChatAppend(window.reactChatWindowHost,{
        id:'retired-'+category,role:'assistant',author:'Neko',time:'',blocks:[{
            type:'proactive_recommendation',recommendationId:crypto.randomUUID(),category,
            state:category==='chat'?'accepted':'pending',content:{title:'',source:'',url:''}}]});`);
    await waitFor(follower,"document.querySelectorAll('.proactive-recommendation-card').length===8");
    assert.equal(await follower.webContents.executeJavaScript("!!document.querySelector('[data-proactive-category=chat]')"),false);
    assert.equal(await follower.webContents.executeJavaScript("!!document.querySelector('[data-message-id^=retired-]')"),false,'retired cards do not leave empty history rows');
    await follower.webContents.executeJavaScript("document.querySelector('[data-proactive-category=\"news\"]').scrollIntoView({block:'start',behavior:'instant'})");
    await capture(follower,'[data-proactive-category="news"]','content-full-card.png');
    assert.equal(await owner.webContents.executeJavaScript("fetch('/api/smoke/meme-count').then(r=>r.json()).then(x=>x.count)"),0,'no meme image loads before acceptance');
    stage='late-window-replay';
    await follower.loadURL(url+'?follower=1&late=1');
    await waitFor(follower,"window.smokeReady && document.querySelectorAll('.proactive-recommendation-card').length===8");
    await owner.webContents.executeJavaScript("window.setSmokeSurfaceMode('compact')");
    await waitFor(owner,"!!document.querySelector('.compact-proactive-recommendation-prompt [data-proactive-category=\"news\"]')");
    await capture(owner,'.compact-proactive-recommendation-prompt','content-compact-bubble.png');
    const geometry=await owner.webContents.executeJavaScript('window.smokeGeometry()');
    assert.ok(geometry.surfaceItems.some(item=>item.kind==='musicRecommendation'&&item.nativeRect&&item.hitRect),'the whole bubble contributes native/click geometry');
    fs.writeFileSync(path.join(output,'content-geometry.json'),JSON.stringify(geometry,null,2));
    const seeds=await owner.webContents.executeJavaScript('window.smokeContentRecommendations');
    const skip=new Set(['news','personal']);
    for(const item of seeds){
        stage='decide-'+item.category;
        const selector=`[data-recommendation-id="${item.id}"]`;
        await waitFor(owner,`!!document.querySelector('.compact-proactive-recommendation-prompt ${selector}')`);
        await follower.webContents.executeJavaScript(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center',behavior:'instant'})`);
        await waitFor(follower,`fetch('/api/proactive/recommendations/events',{headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json()).then(x=>x.events.some(e=>e.recommendation_id===${JSON.stringify(item.id)}&&e.kind==='shown'))`);
        const decision=skip.has(item.category)||item.content.title==='这轮屏幕话题先略过。'?'skipped':'accepted';
        const actor=['meme','vision','personal','chat','anti_slack'].includes(item.category)?owner:follower;
        if(item.category==='vision' && decision==='accepted'){
            assert.equal(item.content.url,'','actual visual observation needs no fabricated URL');
            await capture(owner,'.compact-proactive-recommendation-prompt','screen-compact-bubble.png');
            await capture(follower,selector,'screen-full-card.png');
            for(const [win,element,file] of [[owner,'.compact-proactive-recommendation-prompt','screen-bubble-detail.png'],[follower,selector,'screen-card-detail.png']]){
                const rect=await win.webContents.executeJavaScript(`(()=>{const r=document.querySelector(${JSON.stringify(element)}).getBoundingClientRect();return{x:Math.floor(r.x),y:Math.floor(r.y),width:Math.ceil(r.width),height:Math.ceil(r.height)}})()`);
                fs.writeFileSync(path.join(output,file),(await win.webContents.capturePage(rect)).toPNG());
            }
        }
        if(item.category==='meme'){
            await capture(owner,'.compact-proactive-recommendation-prompt','meme-compact-before.png');
            await capture(follower,selector,'meme-card-before.png');
            for(const [win,element,file] of [[owner,'.compact-proactive-recommendation-prompt','meme-bubble-detail.png'],[follower,selector,'meme-card-detail.png']]){
                const rect=await win.webContents.executeJavaScript(`(()=>{const r=document.querySelector(${JSON.stringify(element)}).getBoundingClientRect();return{x:Math.floor(r.x),y:Math.floor(r.y),width:Math.ceil(r.width),height:Math.ceil(r.height)}})()`);
                fs.writeFileSync(path.join(output,file),(await win.webContents.capturePage(rect)).toPNG());
            }
        }
        if(item.category==='community'){
            await Promise.all([click(owner,selector+' button'),click(follower,selector+' button')]);
        } else await click(actor,selector+(decision==='skipped'?' button:nth-child(2)':' button'));
        await waitFor(follower,`fetch('/api/proactive/recommendations/events',{headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json()).then(x=>x.events.some(e=>e.recommendation_id===${JSON.stringify(item.id)}&&e.kind===${JSON.stringify(decision)}))`);
        if(item.category==='vision')await waitFor(follower,`document.querySelector(${JSON.stringify(selector)})?.textContent.includes(${JSON.stringify(decision==='accepted'?'已记住你的兴趣':'这次先略过')})`);
        if(item.category==='meme'){
            await waitFor(owner,"document.querySelector('.compact-proactive-recommendation-prompt img')?.complete && document.querySelector('.compact-proactive-recommendation-prompt img')?.naturalWidth>0");
            // DOM image readiness can precede compositor output in a hidden window.
            const paintDeadline=Date.now()+5000;
            let memePainted=false;
            while(Date.now()<paintDeadline){
                const frame=await owner.webContents.capturePage(),pixels=frame.getBitmap();
                let imagePixels=0;
                for(let p=0;p<pixels.length;p+=4)if(pixels[p]===255&&pixels[p+1]===227&&pixels[p+2]===185)imagePixels++;
                if(imagePixels>500){fs.writeFileSync(path.join(output,'content-meme-accepted.png'),frame.toPNG());memePainted=true;break;}
                await new Promise(resolve=>setTimeout(resolve,40));
            }
            assert.ok(memePainted,'accepted meme must paint, not only load in the DOM');
            await click(owner,'.compact-proactive-recommendation-prompt .proactive-recommendation > button');
            await waitFor(follower,`!document.querySelector('${selector} img')`);
        }
        await waitFor(owner,`!document.querySelector('.compact-proactive-recommendation-prompt ${selector}')`);
    }
    stage='durable-categorized-evidence';
    const data=await follower.webContents.executeJavaScript("fetch('/api/proactive/recommendations/events',{headers:{'X-CSRF-Token':'smoke-token'}}).then(r=>r.json())");
    assert.equal(data.events.filter(e=>e.kind==='accepted').length,5);
    assert.equal(data.events.filter(e=>e.kind==='shown').length,8);
    assert.equal(data.events.filter(e=>e.kind==='skipped').length,3);
    assert.equal(external.length,3,'concurrent acceptance opens community once; video and web open once');
    assert.equal(new Set(data.events.map(e=>e.category)).size,7);
    assert.equal(data.events.some(e=>['playing','paused','ended'].includes(e.kind)),false);
    for(const win of windows)assert.equal(await win.webContents.executeJavaScript("document.querySelectorAll('audio').length"),0);
    stage='narrow-dark';
    follower.setSize(340,680);
    await follower.webContents.executeJavaScript("document.documentElement.dataset.theme='dark';document.querySelector('[data-proactive-category=\"video\"]').scrollIntoView({block:'center',behavior:'instant'})");
    await waitFor(follower,"document.querySelector('.proactive-recommendation').getBoundingClientRect().width<340");
    assert.equal(await follower.webContents.executeJavaScript("[...document.querySelectorAll('.proactive-recommendation')].some(e=>e.scrollWidth>e.clientWidth+1)"),false);
    fs.writeFileSync(path.join(output,'content-narrow-dark.png'),(await follower.webContents.capturePage()).toPNG());
    assert.equal(errors.length,0,errors.join('\n'));
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:true,electron:process.versions.electron,productionDesktopIPC:true,isolatedSessions:true,categories:7,screenInterestAndSkipMirrored:true,retiredPlaceholdersHidden:true,external,events:data.events.map(e=>({category:e.category,kind:e.kind})),errors},null,2));
}
app.whenReady().then(run).then(()=>{for(const win of windows)win.destroy();app.exit(0);}).catch(async error=>{
    for(const [index,win] of windows.entries())try{fs.writeFileSync(path.join(output,'content-failure-'+index+'.png'),(await win.webContents.capturePage()).toPNG());}catch{}
    fs.writeFileSync(path.join(output,'result.json'),JSON.stringify({ok:false,stage,error:String(error.stack),errors},null,2));
    for(const win of windows)win.destroy();app.exit(1);
});
