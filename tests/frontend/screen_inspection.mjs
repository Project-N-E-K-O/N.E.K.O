import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const code = await readFile(new URL('../../static/app/app-screen-inspection.js', import.meta.url),'utf8');
const {handleScreenInspection: run} = await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
global.WebSocket = {OPEN:1};
function fixture() {
    const sent=[], captures=[];
    const state={selectedScreenSourceId:null, proactiveVisionEnabled:true};
    const socket={readyState:1,send:s=>sent.push(JSON.parse(s))}; state.socket=socket;
    global.window={appProactive:{isProactiveVisionEnabledNow:()=>state.proactiveVisionEnabled},
        getAvatarScreenPosition:()=>({centerX:.9,centerY:.8,width:.1,height:.2}),
        electronDesktopCapturer:{getSources:async()=>[
            {id:'screen:1',name:'Screen'}, {id:'window:2',name:'Chrome'}, {id:'window:3',name:'Music'}],
            captureSourceAsDataUrl:async id=>{captures.push(id);return {success:true,dataUrl:'data:image/png;base64,test'};}}};
    return {state,socket,sent,captures};
}
{
    const f=fixture(); await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.deepEqual(f.captures,['screen:1']); assert.equal(f.sent[0].windows.length,2);
    assert.ok(f.sent[0].avatar_position); assert.equal(f.state.selectedScreenSourceId,null);
    await run({operation:'window',scope:'desktop',window_id:'window:3',request_id:'2'},f.state,f.socket);
    assert.equal(f.captures[1],'window:3'); assert.equal(f.sent[1].title,'Music');
    assert.equal(f.state.selectedScreenSourceId,null); assert.equal(f.sent[1].avatar_position,null);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.deepEqual(f.captures,['window:2']); assert.equal(f.sent[0].windows.length,1);
    await run({operation:'window',scope:'window:2',window_id:'window:3',request_id:'2'},f.state,f.socket);
    assert.equal(f.sent[1].success,false); assert.equal(f.captures.length,1);
}
{
    const f=fixture(); f.state.proactiveVisionEnabled=false;
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.proactiveVisionEnabled=false; return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false); assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    await run({operation:'validate',scope:'desktop',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false);
}
{
    const f=fixture(); window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>({success:false});
    await run({operation:'window',scope:'desktop',window_id:'window:3',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false); assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); const replacement={readyState:1,send:()=>assert.fail('replacement socket received old frame')};
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.socket=replacement; return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'old'},f.state,f.socket);
    assert.equal(f.sent.length,0);
}
{
    const f=fixture(); window.prepareRememberedWindowCapture=async()=>({required:true,allowed:false});
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); window.prepareRememberedWindowCapture=async()=>({required:true,allowed:true,isCurrent:()=>true});
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2'; let current=true;
    window.prepareRememberedWindowCapture=async()=>({required:true,allowed:true,isCurrent:()=>current});
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{current=false;return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed'); assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); window.electronDesktopCapturer.sourceEnumerationMayPrompt=true;
    window.electronDesktopCapturer.getSources=async()=>assert.fail('must not open a portal');
    await run({operation:'window',scope:'desktop',window_id:'window:3',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,false);assert.equal(f.captures.length,0);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='window:2';
    window.electronDesktopCapturer.getSources=async()=>[{id:'window:3',name:'Music'}];
    await run({operation:'window',scope:'window:2',window_id:'window:2',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'window_unavailable'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); window.isNekoHomeTutorialProactiveSuppressed=()=>true;
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'disabled'); assert.equal(f.captures.length,0);
}
{
    const f=fixture(); let paused=false,drawn=false;
    const track={id:'browser-tab',getSettings:()=>({displaySurface:'browser'}),stop:()=>assert.fail('shared track stopped')};
    f.state.screenCaptureStream={active:true,getVideoTracks:()=>[track]};
    const video={videoWidth:2048,videoHeight:1200,play:async()=>{},pause:()=>{paused=true;}};
    const canvas={getContext:()=>({drawImage:()=>{drawn=true;}}),toDataURL:()=> 'data:image/png;base64,stream'};
    global.document={createElement:name=>name==='video'?video:canvas};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].success,true);assert.equal(f.sent[0].scope,'stream:browser-tab');
    assert.equal(f.sent[0].avatar_position,null);assert.deepEqual(f.sent[0].windows,[]);
    assert.equal(canvas.width,2048);assert.equal(canvas.height,1200);assert.ok(paused&&drawn);assert.equal(video.srcObject,null);
    assert.equal(f.captures.length,0);
}
{
    const f=fixture(); f.state.selectedScreenSourceId='screen:1';
    window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>{
        f.state.selectedScreenSourceId='window:2';return {success:true,dataUrl:'pixels'};};
    await run({operation:'overview',request_id:'1'},f.state,f.socket);
    assert.equal(f.sent[0].error,'scope_changed');assert.equal(f.sent[0].data_url,undefined);
}
{
    const f=fixture(); window.electronDesktopCapturer.captureSourceAsDataUrl=async()=>({success:true,dataUrl:'x'.repeat(12*1024*1024+1)});
    await run({operation:'overview',request_id:'large'},f.state,f.socket);
    assert.equal(f.sent[0].error,'invalid_image');assert.equal(f.sent[0].data_url,undefined);
}
console.log('Frontend: 16 scenarios passed');
