"""Regression cases for shared sources and asynchronous editor teardown."""
import pytest

from tests.frontend.test_emote_state_stage import BOOT, REVEAL, _boot


@pytest.mark.parametrize('source_type', ['source.scene', 'source.image'])
def test_replacing_one_state_does_not_edit_its_shared_source(page, running_server, source_type):
    _boot(page, running_server)
    result = page.evaluate("""async type => {
        NekoEmoteStateStage.setUserImages([{id:'one', label:'one'}, {id:'two', label:'two'}]);
        const params = type === 'source.scene' ? {sceneId:'anim-a'} : {imageIds:['one'], imageId:'one'};
        const g = {
            schemaVersion:1, nodes:[
                {id:'root', type:'source.default', params:{}},
                {id:'shared', type, params},
                {id:'a', type:'output.state', params:{stateKey:'idle_image'}},
                {id:'b', type:'output.state', params:{stateKey:'talking_image'}},
            ], links:[
                {from:'shared',to:'a',fromSocket:'look',toSocket:'look'},
                {from:'shared',to:'b',fromSocket:'look',toSocket:'look'},
            ]
        };
        __STAGE.setGraph(g);
        if (type === 'source.scene') await __STAGE.assignScene('idle_image','anim-b');
        else await __STAGE.assignImage('idle_image','two');
        const after = __STAGE.getGraph();
        const source = state => {
            const out = after.nodes.find(n => n.params.stateKey === state);
            const link = after.links.find(l => l.to === out.id);
            return after.nodes.find(n => n.id === link.from).params;
        };
        return {idle:source('idle_image'), talking:source('talking_image')};
    }""", source_type)
    if source_type == 'source.scene':
        assert result['idle']['sceneId'] == 'anim-b'
        assert result['talking']['sceneId'] == 'anim-a'
    else:
        assert result['idle']['imageIds'] == ['one', 'two']
        assert result['talking']['imageIds'] == ['one']



@pytest.mark.parametrize('foreign_event', ['pointermove', 'pointerup', 'pointercancel', 'lostpointercapture'])
def test_strip_gestures_ignore_other_pointer_ids(page, running_server, foreign_event):
    _boot(page, running_server)
    box = page.locator('.st-strip-canvas').bounding_box()
    x, y = box['x'], box['y'] + box['height'] / 2
    page.mouse.move(x + box['width'] * .98, y)
    page.mouse.down()
    page.mouse.move(x + box['width'] * .7, y)
    before = page.evaluate("__STAGE.getGraph().nodes.find(n=>n.params.stateKey==='idle_image').params.seconds")
    page.evaluate('''([event,x,y])=>document.querySelector('.st-strip-canvas').dispatchEvent(
        new PointerEvent(event,{bubbles:true,pointerId:987,pointerType:'touch',clientX:x,clientY:y}))''',
        [foreign_event, x + box['width'] * .2, y])
    assert page.evaluate("__STAGE.getGraph().nodes.find(n=>n.params.stateKey==='idle_image').params.seconds") == before
    page.mouse.move(x + box['width'] * .45, y, steps=4)
    page.mouse.up()
    assert page.evaluate("__STAGE.getGraph().nodes.find(n=>n.params.stateKey==='idle_image').params.seconds") < before


def test_flat_stage_double_click_preserves_saved_layered_pose_and_layered_still_resets(page, running_server):
    _boot(page, running_server)
    page.evaluate('''async () => {
        const graph=__STAGE.getGraph(),node=graph.nodes.find(n=>n.params.stateKey==='idle_image');
        node.params.scalePercent=135;node.params.degrees=25;
        __STAGE.setGraph(graph);window.__FLAT=true;await __STAGE.refresh();
    }''')
    page.locator('.st-stage').dblclick()
    pose = page.evaluate("__STAGE.getGraph().nodes.find(n=>n.params.stateKey==='idle_image').params")
    assert (pose['scalePercent'], pose['degrees']) == (135,25)
    page.evaluate('async()=>{window.__FLAT=false;await __STAGE.refresh();}')
    page.locator('.st-stage').dblclick()
    pose = page.evaluate("__STAGE.getGraph().nodes.find(n=>n.params.stateKey==='idle_image').params")
    assert (pose['scalePercent'], pose['degrees']) == (100,0)


def test_delayed_thumbnail_cannot_replace_the_authoritative_trimmed_frame_canvas(page, running_server):
    page.goto(running_server + '/emote_maker', wait_until='networkidle')
    page.evaluate(REVEAL)
    page.evaluate(BOOT.replace("            return '';",
        "            return new Promise(resolve=>{window.reviewOldThumb=resolve;});"), .1)
    page.wait_for_function("typeof window.reviewOldThumb==='function'")
    page.locator('.st-tile[data-state="talking_image"]').click()
    page.wait_for_function("document.querySelector('.st-tile[data-state=\"talking_image\"] .st-tile-thumb canvas')!==null")
    box = page.locator('.st-strip-canvas').bounding_box()
    page.mouse.move(box['x'] + 1, box['y'] + box['height'] / 2)
    page.mouse.down()
    page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2, steps=4)
    page.mouse.up()
    assert page.evaluate('''async () => {
        const host=document.querySelector('.st-tile[data-state="talking_image"] .st-tile-thumb');
        const authoritative=host.firstChild;
        const old=document.createElement('canvas');old.width=old.height=44;
        const context=old.getContext('2d');context.fillStyle='red';context.fillRect(0,0,44,44);
        reviewOldThumb(old.toDataURL());
        await new Promise(requestAnimationFrame);
        return host.firstChild===authoritative && host.firstChild.tagName==='CANVAS';
    }''')


def test_reduced_motion_preview_is_centered_on_the_first_draw_after_a_dpr_resize(page, running_server):
    from tests.frontend.test_emote_state_stage import _open_library

    page.add_init_script("Object.defineProperty(window,'devicePixelRatio',{get:()=>2});")
    page.emulate_media(reduced_motion='reduce')
    _boot(page, running_server)
    _open_library(page)
    result = page.evaluate('''async () => {
        const canvas=document.querySelector('.st-lib-preview-canvas');
        canvas.width=canvas.height=1;
        await __STAGE.refresh();
        const ctx=canvas.getContext('2d'),data=ctx.getImageData(0,0,canvas.width,canvas.height).data;
        let left=canvas.width,right=-1,top=canvas.height,bottom=-1;
        for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
            if(!data[(y*canvas.width+x)*4+3])continue;
            left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);
        }
        return {scale:ctx.getTransform().a,width:canvas.width,height:canvas.height,left,right,top,bottom};
    }''')
    assert result['scale'] == 2
    assert (result['left'] + result['right'] + 1) / 2 == pytest.approx(result['width'] / 2, abs=2)
    assert (result['top'] + result['bottom'] + 1) / 2 == pytest.approx(result['height'] / 2, abs=2)
    expected_edge = min(result['width'], result['height']) * .9
    assert result['right'] - result['left'] + 1 == pytest.approx(expected_edge, abs=3)
    assert result['bottom'] - result['top'] + 1 == pytest.approx(expected_edge, abs=3)

def test_fine_zoom_and_closed_drawer_focus(page, running_server):
    _boot(page, running_server)
    page.locator('.st-stage').focus()
    page.keyboard.press('Shift+ArrowUp')
    value = page.evaluate("__STAGE.getGraph().nodes.find(n=>n.params.stateKey==='idle_image').params.scalePercent")
    assert value == 101
    page.locator('.st-rail').click()
    page.locator('.st-library-search').focus()
    page.locator('.st-rail').click()
    assert page.evaluate("document.querySelector('.st-drawer').inert")
    assert page.evaluate("document.activeElement === document.querySelector('.st-rail')")


def test_replacing_a_shared_modified_source_preserves_the_target_transform(page, running_server):
    _boot(page, running_server)
    result = page.evaluate("""async () => {
        __STAGE.setGraph({schemaVersion:1,nodes:[
            {id:'root',type:'source.default',params:{}},
            {id:'scene',type:'source.scene',params:{sceneId:'anim-a'}},
            {id:'transform',type:'mod.transform',params:{x:22,y:0,scale:1}},
            {id:'a',type:'output.state',params:{stateKey:'idle_image'}},
            {id:'b',type:'output.state',params:{stateKey:'talking_image'}},
        ],links:[['scene','transform'],['transform','a'],['transform','b']].map(([from,to])=>({from,to,fromSocket:'look',toSocket:'look'}))});
        for(let i=0;i<3;i++) await __STAGE.assignScene('idle_image','anim-b');
        const graph=__STAGE.getGraph();
        const outputs=NekoEmoteNodeGraph.evaluate(graph,{sceneId:'',skins:[],slots:[],transform:{x:0,y:0,scale:1,rotation:27}}).outputs;
        return {count:graph.nodes.length, outputs:outputs.map(o=>({state:o.stateKey,scene:o.look.sceneId,x:o.look.transform.x,rotation:o.look.transform.rotation}))};
    }""")
    assert result['count'] == 7, result
    assert result['outputs'] == [
        {'state':'idle_image','scene':'anim-b','x':22,'rotation':27},
        {'state':'talking_image','scene':'anim-a','x':22,'rotation':27},
    ]


def test_appending_images_through_a_shared_modifier_keeps_existing_images(page, running_server):
    _boot(page, running_server)
    result = page.evaluate("""async () => {
        NekoEmoteStateStage.setUserImages([{id:'one',label:'one'},{id:'two',label:'two'}]);
        __STAGE.setGraph({schemaVersion:1,nodes:[
            {id:'root',type:'source.default',params:{}},
            {id:'mod',type:'mod.transform',params:{x:22,scale:1}},
            {id:'a',type:'output.state',params:{stateKey:'idle_image'}},
            {id:'b',type:'output.state',params:{stateKey:'talking_image'}},
        ],links:[['root','mod'],['mod','a'],['mod','b']].map(([from,to])=>({from,to,fromSocket:'look',toSocket:'look'}))});
        await __STAGE.assignImage('idle_image','one');
        await __STAGE.assignImage('idle_image','two');
        const graph=__STAGE.getGraph();
        const source=state=>NekoEmoteNodeGraph.findSource(graph,graph.nodes.find(n=>n.params.stateKey===state).id,'source');
        return {idle:source('idle_image').params.imageIds,talking:source('talking_image').type,count:graph.nodes.length};
    }""")
    assert result == {'idle':['one','two'],'talking':'source.default','count':6}


def test_restored_image_reloads_the_selected_preview_without_reselecting(page, running_server):
    page.goto(running_server + '/emote_maker', wait_until='networkidle')
    page.evaluate(REVEAL)
    page.evaluate('window.reviewImagesReady = false')
    modified = BOOT.replace("framesFor: (source, quality) => {", "framesFor: (source, quality) => {\n"
                            "if (source.startsWith('images:') && !window.reviewImagesReady) return [];")
    page.evaluate(modified, .1)
    page.evaluate("__STAGE.assignImage('idle_image', 'restored')")
    assert page.locator('.st-tile[data-state="idle_image"] .st-tile-status').inner_text() == '—'
    page.evaluate('''async () => {
        window.reviewImagesReady = true;
        NekoEmoteStateStage.setUserImages([{id:'restored',label:'restored'}]);
        await __STAGE.refreshUserImages();
    }''')
    assert page.locator('.st-tile[data-state="idle_image"] .st-tile-status').inner_text() == '20f'
    assert page.evaluate("__SOURCE_CALLS.includes('images:restored|full')")


def test_image_refresh_keeps_an_inflight_scene_frame_result(page, running_server):
    page.goto(running_server + '/emote_maker', wait_until='networkidle')
    page.evaluate(REVEAL)
    page.evaluate('window.reviewResolveFrames = []')
    modified = BOOT.replace("framesFor: (source, quality) => {", "framesFor: async (source, quality) => {\n"
                            "await new Promise(resolve => window.reviewResolveFrames.push(resolve));")
    page.evaluate(modified, .1)
    result = page.evaluate('''async () => {
        const pending = __STAGE.refreshUserImages();
        reviewResolveFrames[0]();
        await new Promise(resolve => setTimeout(resolve, 0));
        const next = __STAGE.refreshUserImages();
        const cached = await Promise.race([next.then(()=>true),
            new Promise(resolve => setTimeout(()=>resolve(false), 60))]);
        const requests = reviewResolveFrames.length;
        reviewResolveFrames.forEach(resolve=>resolve());
        await Promise.all([pending, next]);
        return {cached, requests};
    }''')
    assert result == {'cached':True, 'requests':2}
    assert page.locator('.st-tile[data-state="idle_image"] .st-tile-status').inner_text() == '20f'


def test_dispose_during_frame_loading_cannot_publish_a_late_graph(page, running_server):
    page.goto(running_server + '/emote_maker', wait_until='networkidle')
    page.evaluate(REVEAL)
    modified = BOOT.replace("framesFor: (source, quality) => {", "framesFor: async (source, quality) => {\n"
                            "await new Promise(resolve => { window.reviewFinishFrames = resolve; });")
    modified = modified.replace('fps: () =>', 'onChange: () => { window.reviewGraphWrites += 1; },\n        fps: () =>')
    page.evaluate('window.reviewGraphWrites = 0')
    page.evaluate(modified, .1)
    page.wait_for_function('Boolean(window.reviewFinishFrames)')
    before = page.evaluate('reviewGraphWrites')
    page.evaluate('__STAGE.dispose(); reviewFinishFrames();')
    page.wait_for_timeout(80)
    assert page.evaluate('reviewGraphWrites') == before


def test_click_guard_preserves_a_new_click_and_secondary_pointer_does_not_release_it(page, running_server):
    page.goto(running_server + '/emote_maker', wait_until='networkidle')
    verdict = page.evaluate("""() => {
        const a = document.createElement('button'), b = document.createElement('button');
        document.body.append(a,b);
        const guard = NekoEmoteDragHold.createClickGuard(600);
        const clickA = {detail:1,currentTarget:a,target:a};
        guard.arm(a, 7);
        window.dispatchEvent(new PointerEvent('pointerup',{pointerId:8}));
        const stillHeld = guard.shouldSwallow(clickA);
        window.dispatchEvent(new PointerEvent('pointerup',{pointerId:7}));
        const releaseClick = guard.shouldSwallow(clickA);
        const other = guard.shouldSwallow({detail:1,currentTarget:b,target:b});
        const keyboard = guard.shouldSwallow({detail:0,currentTarget:a,target:a});
        window.dispatchEvent(new PointerEvent('pointerdown',{pointerId:7}));
        const freshClick = guard.shouldSwallow(clickA);
        a.remove();b.remove();
        return {stillHeld, releaseClick, other, keyboard, freshClick};
    }""")
    assert verdict == {'stillHeld': True, 'releaseClick': True, 'other': False, 'keyboard': False, 'freshClick': False}
