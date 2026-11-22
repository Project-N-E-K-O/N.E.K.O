"""Exercise session restoration and competing captures in the actual editor."""
import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.emote_fixtures import spine_zip


@pytest.fixture
def editor(page, running_server, emote_template_library_dir):
    page._review_errors = []
    page.on('console', lambda message: page._review_errors.append(message.text)
            if message.type in ('warning', 'error') else None)
    source = (Path(__file__).resolve().parents[2] / "static/js/emote_maker.js").read_text(encoding="utf-8")
    marker = "    if (document.readyState === 'loading') {"
    hooks = """    window.reviewEditor = {
        state, importTemplateFile, persistTemplateToLibrary, getImportedScenes,
        queueStageCapture, captureGifFrames, bakePackJobs, produceGroupFile, currentLookDefaults,
        importStageImages, persistExportSettings, previewGroupMember, renderLook,
        applyTemplateFromRegistry, currentTemplateKey, setPackBusy, buildPngtuberPackage, flatStates, exportGroup,
        captureStageSource, openStateStage,
        restoreStageImages,
        switchTemplate, downloadPng, refreshTemplateArchiveSizes,
        stageImages: () => [...stageUserImages].map(([id,image])=>({id,name:image.name})),
        renderer: () => spineRenderer,
        generation: () => spineRendererGeneration,
    };
"""
    assert source.count(marker) == 1
    capture_marker = '    function captureCurrentEditorFrame(maxDimension) {'
    assert source.count(capture_marker) == 1
    source = source.replace(capture_marker, capture_marker + '\n        window.reviewCaptureHook?.(spineRenderer);')
    source = source.replace('    function yieldToBrowser() {',
                            '    function yieldToBrowser() {\n        if (window.reviewYieldGate) return window.reviewYieldGate();')
    source = source.replace('reportImportFailure(error);', 'window.reviewImportFailure = error.stack || String(error); reportImportFailure(error);')
    page.route("**/static/js/emote_maker.js*", lambda route: route.fulfill(
        body=source.replace(marker, hooks + marker), content_type="application/javascript"))
    page.goto(running_server + "/emote_maker", wait_until="networkidle")
    page.wait_for_function("Boolean(window.reviewEditor)")
    return page


def _import(page, data, name="character.zip"):
    encoded = base64.b64encode(data).decode("ascii")
    imported = page.evaluate("""async ([encoded, name]) => {
        const file = new File([Uint8Array.from(atob(encoded), c => c.charCodeAt(0))], name,
            {type: 'application/zip'});
        return await reviewEditor.importTemplateFile(file);
    }""", [encoded, name])
    assert imported, page.evaluate('window.reviewImportFailure') or '; '.join(page._review_errors)


def _two_skins():
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(spine_zip())) as source, zipfile.ZipFile(result, "w") as target:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename.endswith(".json"):
                model = json.loads(content)
                model["skins"].append({**model["skins"][0], "name": "alternate"})
                content = json.dumps(model).encode()
            target.writestr(entry, content)
    return result.getvalue()


def _different_starting_bounds():
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(spine_zip())) as source, zipfile.ZipFile(result, 'w') as target:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename.endswith('.json'):
                model = json.loads(content)
                model['animations']['idle1']['bones']['root']['scale'] = [{'x': 1, 'y': 3}]
                content = json.dumps(model).encode()
            target.writestr(entry, content)
    return result.getvalue()


def _unicode_resources(legacy):
    class LegacyZipInfo(zipfile.ZipInfo):
        def _encodeFilenameFlags(self):
            return self.filename.encode('gb18030'), self.flag_bits & ~0x800

    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(spine_zip())) as source, zipfile.ZipFile(result, 'w') as target:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename.endswith('.json'):
                model = json.loads(content)
                model['skins'][0]['attachments']['body']['body']['path'] = '眼睛'
                content = json.dumps(model, ensure_ascii=False).encode('utf-8')
                name = 'assets/角色.json'
            elif entry.filename.endswith('.atlas'):
                content = content.decode().replace('page.png', './贴图.png').replace('\nbody\n', '\n眼睛\n').encode()
                name = 'assets/角色.atlas'
            else:
                name = 'assets/贴图.png'
            target.writestr(LegacyZipInfo(name) if legacy else name, content)
    return result.getvalue()


@pytest.mark.parametrize('legacy', [False, True])
def test_unicode_zip_and_atlas_regions_render_without_network_texture_fallback(editor, legacy):
    external = []
    editor.on('request', lambda request: external.append(request.url) if '/assets/' in request.url else None)
    _import(editor, _unicode_resources(legacy))
    result = editor.evaluate('''async () => {
        const q = reviewEditor, scene = q.getImportedScenes()[0];
        const frames = await q.captureGifFrames({scene, maxFrames: 1, maxDimension: 32});
        return {loaded: q.renderer().loaded, visible: frames[0].data.some((value, index) => index % 4 === 3 && value > 0)};
    }''')
    assert result == {'loaded': True, 'visible': True}
    assert external == []


def test_single_png_download_starts_while_capture_controls_are_locked(editor):
    from PIL import Image

    _import(editor, spine_zip())
    editor.evaluate("Object.assign(reviewEditor.state, {exportFormat:'png', exportResolution:64})")
    with editor.expect_download(timeout=15000) as download:
        editor.locator('#export-png-btn').click()
    with Image.open(download.value.path()) as image:
        assert image.format == 'PNG'
        assert image.size == (64, 64)
    assert editor.locator('#export-png-btn').is_enabled()


@pytest.mark.parametrize(('pack_format', 'fps', 'start', 'seconds', 'expected_frames'), [
    ('flat', 8, 0, .2, 2), ('layered', 8, 0, .2, 2),
    ('flat', 8, 0, .3, 3), ('layered', 8, 0, .3, 3),
    ('flat', 8, .09, .12, 3), ('layered', 8, .09, .12, 12),
    ('flat', 30, .09, .12, 3), ('layered', 30, .09, .12, 12),
    ('flat', 8, .08, .15, 3), ('layered', 8, .08, .15, 15),
    ('layered', 8, .0913, .1184, 120),
    ('flat', 10, .2, .2, 2), ('flat', 30, .2, .2, 2),
    ('layered', 10, .2, .2, 2), ('layered', 30, .2, .2, 6),
    ('flat', 30, .3, .1, 1),
    ('flat', 60, 0, 1, 10), ('layered', 60, 0, 1, 60),
    ('flat', 60, 0, .95, 10), ('layered', 60, 0, .95, 57),
], ids=[
    'flat-8-0-0.2', 'layered-8-0-0.2', 'flat-8-0-0.3', 'layered-8-0-0.3',
    'flat-8-0.09-0.12', 'layered-8-0.09-0.12',
    'flat-30-0.09-0.12', 'layered-30-0.09-0.12',
    'flat-8-0.08-0.15', 'layered-8-0.08-0.15', 'layered-8-fine-window-budget',
    'flat-10-0.2-0.2', 'flat-30-0.2-0.2',
    'layered-10-0.2-0.2', 'layered-30-0.2-0.2', 'flat-30-0.3-0.1',
    'flat-60-0-1', 'layered-60-0-1', 'flat-60-0-0.95', 'layered-60-0-0.95',
])
def test_png_image_pack_uses_the_selected_range_and_timing(editor, pack_format, fps, start, seconds, expected_frames):
    _assert_png_image_pack_timing(editor, pack_format, fps, start, seconds, expected_frames)


@pytest.mark.parametrize(('pack_format', 'start', 'seconds', 'expected_frames', 'gif_delays'), [
    ('flat', .084, .132, 3, [20, 100, 20]),
    ('layered', .084, .132, 33, None),
    ('flat', .099, .102, 3, [10, 100, 10]),
    ('layered', .099, .102, 102, None),
], ids=['flat-16ms-edges', 'layered-16ms-edges', 'flat-1ms-edges', 'layered-1ms-edges'])
def test_png_image_pack_short_boundaries_obey_format_timing_limits(
        editor, pack_format, start, seconds, expected_frames, gif_delays):
    _assert_png_image_pack_timing(editor, pack_format, 30, start, seconds, expected_frames, gif_delays)


def _assert_png_image_pack_timing(editor, pack_format, fps, start, seconds, expected_frames, gif_delays=None):
    from PIL import Image

    _import(editor, spine_zip())
    source_count = max(4, round((start + seconds) * 10))
    exported = editor.evaluate('''async ([format, fps, start, seconds, count]) => {
        const q = reviewEditor;
        const palette = ['#ff0000','#00ff00','#0000ff','#ffff00'];
        const files = Array.from({length:count}, (_, index) => {
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 16;
            const ctx = canvas.getContext('2d');
            ctx.fillStyle = palette[index % palette.length]; ctx.fillRect(0,0,16,16);
            const bytes = Uint8Array.from(atob(canvas.toDataURL().split(',')[1]), c=>c.charCodeAt(0));
            return new File([bytes], `${index}.png`, {type:'image/png'});
        });
        await q.importStageImages(files);
        const names = JSON.parse(localStorage.getItem('neko-emote-maker-stage-image-names'));
        const ids = Object.keys(names).sort((a,b)=>names[a].localeCompare(names[b]));
        if (ids.length !== count) throw new Error('missing-imported-images');
        Object.assign(q.state, {packFormat:format, packMode:'graph', exportResolution:64, exportFps:fps,
            nodeGraph: NekoEmoteNodeGraph.normalizeGraph({schemaVersion:1, nodes:[
                {id:'root',type:'source.default',params:{}},
                {id:'images',type:'source.image',params:{imageIds:ids}},
                {id:'idle',type:'output.state',params:{stateKey:'idle_image',startAt:start,seconds}},
                {id:'talk',type:'output.state',params:{stateKey:'talking_image',startAt:.3,seconds:.1}},
            ],links:['idle','talk'].map(to=>({from:'images',to,fromSocket:'look',toSocket:'look'}))})});
        const built = await q.buildPngtuberPackage();
        if (!built) throw new Error('image-package-failed');
        return built.files.map(file=>({path:file.path,data:btoa(String.fromCharCode(...file.data))}));
    }''', [pack_format, fps, start, seconds, source_count])
    files = {item['path'].split('/', 1)[1]: base64.b64decode(item['data']) for item in exported}
    palette = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    yellow = palette[-1]
    wanted = [palette[index % len(palette)] for index in range(source_count)
              if index / 10 < start + seconds - 1e-9 and (index + 1) / 10 > start + 1e-9]
    wanted_durations = {}
    for index in range(source_count):
        delay = max(0, min(start + seconds, (index + 1) / 10) - max(start, index / 10)) * 1000
        color = palette[index % len(palette)]
        if delay > 1e-6:
            wanted_durations[color] = wanted_durations.get(color, 0) + delay
    if pack_format == 'flat':
        for name, duration, colors, count in (
            ('idle.gif', seconds * 1000, wanted, expected_frames),
            ('talking.gif', 100, [yellow], 1),
        ):
            with Image.open(io.BytesIO(files[name])) as image:
                assert image.n_frames == count
                observed, delays, elapsed, color_durations = [], [], 0, {}
                for index in range(image.n_frames):
                    image.seek(index)
                    color = image.convert('RGB').getpixel((32, 32))
                    observed.append(color)
                    delay = image.info['duration']
                    delays.append(delay)
                    elapsed += delay
                    color_durations[color] = color_durations.get(color, 0) + delay
                quantized_edges = gif_delays is not None and name == 'idle.gif'
                if quantized_edges:
                    # A 100ms PNG slide remains exact; each of its two cropped
                    # edges may need up to 10ms for the compatible GIF tick.
                    assert delays == gif_delays
                assert elapsed == pytest.approx(duration, abs=20 if quantized_edges else 1)
                assert set(observed) == set(colors)
                assert observed[0] == colors[0] and observed[-1] == colors[-1]
                assert color_durations == pytest.approx(
                    wanted_durations if name == 'idle.gif' else {yellow: 100}, abs=10 if quantized_edges else 1)
    else:
        metadata = json.loads(files['metadata.json'])
        for layer in metadata['layers']:
            state = next(state for state in layer['states'] if state['visible'])
            duration = seconds if layer['name'] == 'idle_image' else .1
            colors = wanted if layer['name'] == 'idle_image' else [yellow]
            assert state['frames'] == (expected_frames if layer['name'] == 'idle_image' else round(duration * fps))
            assert state['frames'] / (state['animation_speed'] * 4) == pytest.approx(duration)
            with Image.open(io.BytesIO(files[layer['image']])) as sheet:
                width, height = state['frame_width'], state['frame_height']
                observed = [sheet.convert('RGB').getpixel((
                    index % state['hframes'] * width + width // 2,
                    index // state['hframes'] * height + height // 2,
                )) for index in range(state['frames'])]
            assert set(observed) == set(colors)
            assert observed[0] == colors[0] and observed[-1] == colors[-1]
            frame_delay = 1000 / (state['animation_speed'] * 4)
            color_durations = {color: observed.count(color) * frame_delay for color in set(observed)}
            tolerance = frame_delay if expected_frames == 120 else 1e-6
            assert color_durations == pytest.approx(
                wanted_durations if layer['name'] == 'idle_image' else {yellow: 100}, abs=tolerance)


@pytest.mark.parametrize('pack_format', ['flat', 'layered'])
def test_low_fps_gif_keeps_native_flat_timing_and_can_thin_layered_frames(editor, pack_format):
    from PIL import Image

    source = io.BytesIO()
    images = [Image.new('RGB', (16, 16), color) for color in ('red', 'green', 'blue')]
    images[0].save(source, 'GIF', save_all=True, append_images=images[1:], duration=100, loop=0)
    _import(editor, spine_zip())
    result = editor.evaluate('''async ([format, encoded]) => {
        const q = reviewEditor;
        await q.importStageImages([new File([Uint8Array.from(atob(encoded), c=>c.charCodeAt(0))],
            'timed.gif', {type:'image/gif'})]);
        const imageIds = Object.keys(JSON.parse(localStorage.getItem('neko-emote-maker-stage-image-names')));
        const [baked] = await q.bakePackJobs([{stateKey:'idle_image',imageIds,imageSeconds:.3}],
            {format,fps:8,edge:32});
        return {count:baked.frames.length,duration:baked.frames.reduce((sum,f)=>sum+f.delayMs,0)};
    }''', [pack_format, base64.b64encode(source.getvalue()).decode('ascii')])
    assert result == {'count': 3 if pack_format == 'flat' else 2, 'duration': 300}


def test_flat_pack_encoder_uses_baked_fps_for_frames_without_delays(editor):
    from PIL import Image

    encoded = editor.evaluate('''() => {
        const frames = Array.from({length:30}, (_, index) => ({
            width:1,height:1,data:new Uint8ClampedArray([index%2 ? 255 : 0,0,255,255]),
        }));
        const [state] = reviewEditor.flatStates([{stateKey:'idle_image',edge:1,fps:30,frames}]);
        return btoa(String.fromCharCode(...state.bytes));
    }''')
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.n_frames == 30
        elapsed = 0
        for index in range(image.n_frames):
            image.seek(index)
            elapsed += image.info['duration']
        assert elapsed == 1000


@pytest.mark.parametrize('pack_format', ['flat', 'layered'])
@pytest.mark.parametrize(('speed', 'start', 'end'), [(1, 0, .95), (2, 0, .95), (1, .1, .3)])
def test_spine_capture_keeps_fractional_clip_duration_at_sixty_fps(editor, pack_format, speed, start, end):
    from PIL import Image

    _import(editor, spine_zip())
    result = editor.evaluate('''async ([format, speed, start, end]) => {
        const q = reviewEditor;
        q.state.animationSpeed = speed;
        const [baked] = await q.bakePackJobs([{stateKey:'idle_image', sceneId:q.getImportedScenes()[0].id,
            look:q.currentLookDefaults(),frames:60,timeWindow:{from:start,to:end}}],
            {format,fps:60,edge:32});
        return {count:baked.frames.length,duration:baked.frames.reduce((sum,f)=>sum+f.delayMs,0),
            fps:baked.fps, gif:format === 'flat'
                ? btoa(String.fromCharCode(...q.flatStates([baked])[0].bytes)) : null};
    }''', [pack_format, speed, start, end])
    counts = {'flat': {1:47, 2:23}, 'layered': {1:57, 2:28}}
    expected = {'flat':10, 'layered':12}[pack_format] if start else counts[pack_format][speed]
    duration = (end - start) * 1000 / speed
    assert result['count'] == expected
    assert result['duration'] == pytest.approx(duration)
    assert result['fps'] <= (50 if pack_format == 'flat' else 60) + 1e-9
    if pack_format == 'flat':
        with Image.open(io.BytesIO(base64.b64decode(result['gif']))) as image:
            elapsed = 0
            for index in range(image.n_frames):
                image.seek(index)
                elapsed += image.info['duration']
            assert elapsed == pytest.approx(duration, abs=5)


def test_export_unlock_keeps_controls_enabled_by_an_async_load(editor):
    _import(editor, spine_zip())
    result = editor.evaluate('''async () => {
        const button = document.createElement('button');
        button.disabled = true;
        document.querySelector('.emote-maker-page').append(button);
        let loaded = false;
        window.reviewCaptureHook = () => {
            if (loaded) return;
            loaded = true;
            queueMicrotask(() => { button.disabled = false; });
        };
        try {
            await reviewEditor.captureGifFrames({scene:reviewEditor.getImportedScenes()[0],
                maxFrames:4, maxDimension:32});
            return {loaded, enabled:!button.disabled};
        } finally {
            delete window.reviewCaptureHook;
            button.remove();
        }
    }''')
    assert result == {'loaded':True, 'enabled':True}


@pytest.mark.parametrize('release_first', ['capture', 'pack'])
def test_nested_exports_keep_controls_locked_until_both_finish(editor, release_first):
    _import(editor, spine_zip())
    result = editor.evaluate('''async releaseFirst => {
        const q = reviewEditor, main = document.querySelector('.emote-maker-page');
        const scale = document.querySelector('#adjust-scale');
        const button = document.querySelector('#pack-export-btn');
        const before = q.state.adjustments.scale;
        let finish;
        const capture = q.queueStageCapture('nested-review', 100, 'export',
            () => new Promise(resolve => { finish = resolve; }));
        const added = document.createElement('button');
        const disabled = document.createElement('button');
        disabled.disabled = true;
        main.append(added, disabled);
        q.setPackBusy(true);
        try {
            if (releaseFirst === 'capture') { finish(); await capture; }
            else q.setPackBusy(false);
            const locked = scale.disabled && button.disabled && added.disabled && disabled.disabled
                && main.getAttribute('aria-busy') === 'true';
            scale.value = '180';
            scale.dispatchEvent(new Event('input', {bubbles:true}));
            const unchanged = q.state.adjustments.scale === before;
            if (releaseFirst === 'capture') q.setPackBusy(false);
            else { finish(); await capture; }
            return {locked, unchanged,
                unlocked: !scale.disabled && !button.disabled && !added.disabled && disabled.disabled
                    && !main.hasAttribute('aria-busy')};
        } finally {
            q.setPackBusy(false);
            finish();
            await capture;
            added.remove(); disabled.remove();
        }
    }''', release_first)
    assert result == {'locked':True, 'unchanged':True, 'unlocked':True}


def test_batch_stop_becomes_available_when_folder_preparation_overlaps_a_capture(editor):
    _import(editor, spine_zip())
    editor.evaluate("document.querySelector('#inspector-tab-export').click()")
    pending, writes = [], []

    def export_request(route):
        if route.request.url.endswith('/folder'):
            pending.append(route)
        else:
            if route.request.url.endswith('/file'):
                writes.append(route.request)
            route.fulfill(json={'success':True})

    editor.route('**/api/emote-maker/export/**', export_request)
    with editor.expect_request('**/api/emote-maker/export/folder'):
        editor.evaluate('''() => {
            const q = reviewEditor;
            q.state.exportFormat = 'png'; q.state.exportResolution = 64;
            const group = {id:'review-stop',name:'review-stop',readOnly:true,
                items:q.getImportedScenes().slice(0,2).map(scene=>({kind:'scene',ref:scene.id}))};
            if (group.items.length !== 2) throw new Error('expected-two-batch-items');
            window.reviewBatch = q.exportGroup(group, document.querySelector('#export-group-open-btn'),
                Promise.resolve({kind:'path',path:'Z:/test-only-intercepted'}));
            reviewBatch.finally(()=>{window.reviewBatchDone=true;});
        }''')
    editor.wait_for_function("!document.querySelector('#export-group-open-btn').disabled")
    editor.evaluate('''() => {
        window.reviewBlockingCapture = reviewEditor.queueStageCapture('stop-review',100,'export',
            () => new Promise(resolve => {window.reviewFinishBlockingCapture = resolve;}));
    }''')
    editor.wait_for_function("typeof window.reviewFinishBlockingCapture === 'function'")
    assert editor.locator('#export-group-open-btn').is_enabled()
    assert len(pending) == 1
    editor.locator('#export-group-open-btn').click(force=True)
    try:
        editor.wait_for_function('window.reviewBatchDone===true', timeout=5000)
        assert editor.locator('#adjust-scale').is_disabled()
    finally:
        pending[0].fulfill(json={'success':True,'directory':'Z:/test-only-intercepted','folder':'review-stop'})
        editor.evaluate('reviewFinishBlockingCapture()')
    editor.wait_for_function("!document.querySelector('.emote-maker-page').hasAttribute('aria-busy')", timeout=5000)
    assert writes == []
    assert not editor.locator('.emote-maker-page').get_attribute('aria-busy')
    assert editor.locator('#export-group-open-btn').is_enabled()


def _start_waiting_batch(editor, wait_at):
    editor.evaluate('''waitAt => {
        const q=reviewEditor, original=window.fetch;
        q.state.exportFormat='png'; q.state.exportResolution=32;
        window.reviewBatchWrites=0; window.reviewBatchReadbacks=0;
        window.reviewCaptureHook=()=>{reviewBatchReadbacks+=1;};
        const gate=()=>new Promise(resolve=>{window.reviewReleasePreparation=resolve;});
        const file=()=>({createWritable:async()=>({write:async()=>{reviewBatchWrites+=1;},close:async()=>{}})});
        const folder={getFileHandle:async()=>file()};
        const parent={kind:'directory',entries:async function*(){if(waitAt==='browser-name') await gate();},
            getDirectoryHandle:async()=>{if(waitAt==='browser-folder') await gate();return folder;},
            getFileHandle:async()=>file()};
        window.fetch=async (...args)=>{
            if(String(args[0]).includes('/api/emote-maker/export/')){
                if(String(args[0]).endsWith('/folder')) await gate();
                if(String(args[0]).endsWith('/file')) reviewBatchWrites+=1;
                return new Response(JSON.stringify({success:true,directory:'Z:/intercepted',folder:'A'}),
                    {headers:{'Content-Type':'application/json'}});
            }
            return original(...args);
        };
        const picked=waitAt==='picker' ? gate().then(()=>null)
            : Promise.resolve(waitAt==='native-folder' ? {kind:'path',path:'Z:/intercepted'} : parent);
        const look=q.currentLookDefaults();look.transform.x=170;
        const group={id:'A-group',name:'A-group',look,readOnly:false,
            items:q.getImportedScenes().map(scene=>({kind:'scene',ref:scene.id}))};
        window.reviewBatch=q.exportGroup(group,null,picked);
        reviewBatch.finally(()=>{window.reviewBatchDone=true;});
    }''', wait_at)
    editor.wait_for_function('Boolean(window.reviewReleasePreparation)')


@pytest.mark.parametrize('wait_at', ['picker', 'native-folder', 'browser-name', 'browser-folder'])
def test_batch_preparation_cancels_when_the_selected_template_changes(editor, wait_at):
    _import(editor, spine_zip(), 'A.zip')
    downloads = []
    editor.on('download', lambda download: downloads.append(download))
    _start_waiting_batch(editor, wait_at)
    _import(editor, spine_zip('other'), 'B.zip')
    editor.evaluate('reviewEditor.state.adjustments.x=-35')
    editor.evaluate('''async () => {reviewReleasePreparation();await reviewBatch;}''')
    assert editor.evaluate('({reads:reviewBatchReadbacks,writes:reviewBatchWrites,x:reviewEditor.state.adjustments.x})') == {
        'reads': 0, 'writes': 0, 'x': -35}
    assert downloads == []
    assert not editor.locator('.emote-maker-page').get_attribute('aria-busy')
    assert editor.locator('#export-png-btn').is_enabled()


@pytest.mark.parametrize('wait_at', ['picker', 'native-folder', 'browser-name', 'browser-folder'])
def test_stopping_batch_export_releases_the_editor_without_waiting_for_directory_preparation(editor, wait_at):
    _import(editor, spine_zip())
    _start_waiting_batch(editor, wait_at)
    editor.locator('#export-group-open-btn').click(force=True)
    try:
        editor.wait_for_function('window.reviewBatchDone===true', timeout=5000)
        assert editor.locator('#export-png-btn').is_enabled()
        assert not editor.locator('.emote-maker-page').get_attribute('aria-busy')
    finally:
        editor.evaluate('reviewReleasePreparation()')
    editor.evaluate('async () => {await new Promise(requestAnimationFrame);}')
    assert editor.evaluate('({reads:reviewBatchReadbacks,writes:reviewBatchWrites})') == {'reads':0,'writes':0}


def test_batch_directory_preparation_blocks_a_second_package_export(editor):
    _import(editor, spine_zip())
    result = editor.evaluate('''async () => {
        const q=reviewEditor;
        let release;
        const group={id:'waiting',name:'waiting',items:[{kind:'scene',ref:q.getImportedScenes()[0].id}]};
        const batch=q.exportGroup(group,null,new Promise(r=>{release=r;}));
        try {
            const pack=await q.buildPngtuberPackage();
            return {pack:pack===null,locked:document.querySelector('#adjust-scale').disabled};
        } finally {release('cancelled');await batch;}
    }''')
    assert result == {'pack': True, 'locked': True}
    assert editor.locator('#adjust-scale').is_enabled()
    assert not editor.locator('.emote-maker-page').get_attribute('aria-busy')


def test_template_library_search_matches_alias_and_original_name(editor):
    _import(editor, spine_zip(), 'original-character.zip')
    editor.locator('.template-lib-card:not([data-template-built-in])').dblclick()
    rename = editor.locator('.template-lib-rename')
    rename.fill('周末版')
    rename.press('Enter')
    search = editor.locator('#asset-search-input')
    for query in ['周末版', 'ORIGINAL-CHARACTER', 'no-such-template']:
        search.fill(query)
        assert editor.locator('.template-lib-card').count() == (0 if query == 'no-such-template' else 1)


def test_switching_loaded_templates_survives_an_oversized_library_without_discarding_sessions(editor):
    _import(editor, spine_zip(), 'A.zip')
    a_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    editor.evaluate('''() => {
        reviewEditor.state.groups=[{id:'a',name:'a',notes:'a'.repeat(NekoEmoteTemplateLibrary.BUDGET_CHARS*.55)}];
        reviewEditor.persistTemplateToLibrary();
    }''')
    _import(editor, spine_zip('other'), 'B.zip')
    result = editor.evaluate('''async aKey => {
        const q=reviewEditor, bKey=q.currentTemplateKey();
        q.state.groups=[{id:'b',name:'b',notes:'b'.repeat(NekoEmoteTemplateLibrary.BUDGET_CHARS*.55)}];
        q.persistTemplateToLibrary();
        const persisted=localStorage.getItem(NekoEmoteTemplateLibrary.STORAGE_KEY);
        const a=await q.applyTemplateFromRegistry(aKey);
        const aLoaded=q.renderer()?.loaded && q.currentTemplateKey()===aKey;
        const savedUnchanged=persisted===localStorage.getItem(NekoEmoteTemplateLibrary.STORAGE_KEY);
        const frames=await q.captureGifFrames({maxFrames:1,maxDimension:32});
        const b=await q.applyTemplateFromRegistry(bKey);
        return {a,b,aLoaded,bLoaded:q.renderer()?.loaded && q.currentTemplateKey()===bKey,
            visible:frames[0].data.some((v,i)=>i%4===3 && v>0),
            keptB:q.state.groups.some(g=>g.id==='b' && g.name==='b'),savedUnchanged};
    }''', a_key)
    assert result == {'a':True,'b':True,'aLoaded':True,'bLoaded':True,'visible':True,
                      'keptB':True,'savedUnchanged':True}
    assert editor.locator('#adjust-scale').is_enabled()


def test_archive_only_cards_do_not_block_saving_an_almost_full_session_library(editor):
    _import(editor, spine_zip('other'), 'orphan.zip')
    orphan_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    _import(editor, spine_zip(), 'A.zip')
    editor.wait_for_function('''async () => (await (await fetch('/api/emote-maker/library')).json()).entries.length===2''')
    editor.evaluate('''() => {
        const q=reviewEditor,L=NekoEmoteTemplateLibrary;
        q.state.groups=[{id:'near',name:'near',notes:''}];q.persistTemplateToLibrary();
        const size=localStorage.getItem(L.STORAGE_KEY).length;
        q.state.groups[0].notes='a'.repeat(L.BUDGET_CHARS-size-1024);q.persistTemplateToLibrary();
    }''')
    editor.add_init_script('''(() => {
        const name='neko-emote-maker-template-library',store=JSON.parse(localStorage.getItem(name));
        store.entries=store.entries.filter(e=>e.key!==__KEY__);
        localStorage.setItem(name,JSON.stringify(store));
    })();'''.replace('__KEY__', json.dumps(orphan_key)))

    def archive_index(route):
        if route.request.method != 'GET':
            route.continue_()
            return
        response = route.fetch()
        payload = response.json()
        payload['entries'] += [{'key':f'spine-{i:064x}','bytes':1} for i in range(100)]
        route.fulfill(json=payload)

    editor.route('**/api/emote-maker/library', archive_index)
    editor.reload(wait_until='networkidle')
    orphan = editor.locator(f'.template-lib-card[data-template-key="{orphan_key}"]')
    orphan.wait_for(state='visible')
    assert editor.evaluate('JSON.parse(localStorage.getItem(NekoEmoteTemplateLibrary.STORAGE_KEY)).entries.length') == 1
    editor.locator('.template-lib-card:not([data-template-built-in]) .template-lib-open').first.click()
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    assert editor.evaluate('''() => {
        const q=reviewEditor,L=NekoEmoteTemplateLibrary;
        q.state.adjustments.x=33;q.persistTemplateToLibrary();
        const entries=JSON.parse(localStorage.getItem(L.STORAGE_KEY)).entries;
        return entries.length===1 && entries[0].session.adjustments.x===33;
    }''')
    orphan.locator('.template-lib-remove').click()
    editor.locator('#emote-confirm-ok').click()
    orphan.wait_for(state='detached')
    assert editor.evaluate('JSON.parse(localStorage.getItem(NekoEmoteTemplateLibrary.STORAGE_KEY)).entries.length') == 1


@pytest.mark.parametrize('failed_store', ['recent', 'library'])
def test_reopening_uses_the_newer_complete_snapshot_after_one_storage_write_fails(editor, failed_store):
    _import(editor, spine_zip())
    editor.wait_for_function('''async () => (await (await fetch('/api/emote-maker/library')).json()).entries.length===1''')
    snapshots = editor.evaluate('''failed => {
        const q=reviewEditor,L=NekoEmoteTemplateLibrary;
        q.state.adjustments.x=10;q.persistTemplateToLibrary();
        const original=Storage.prototype.setItem;
        Storage.prototype.setItem=function(key,value){
            if(key===(failed==='recent'?'neko-emote-maker-last-template':L.STORAGE_KEY))
                throw new DOMException('test quota','QuotaExceededError');
            return original.call(this,key,value);
        };
        q.state.adjustments.x=23;q.persistTemplateToLibrary();
        const recent=JSON.parse(localStorage.getItem('neko-emote-maker-last-template'));
        const saved=JSON.parse(localStorage.getItem(L.STORAGE_KEY)).entries[0].session;
        return {recentX:recent.adjustments.x,savedX:saved.adjustments.x,
            recentRevision:recent.saveRevision,savedRevision:saved.saveRevision};
    }''', failed_store)
    assert snapshots['recentX'] == (10 if failed_store == 'recent' else 23)
    assert snapshots['savedX'] == (23 if failed_store == 'recent' else 10)
    assert (snapshots['recentRevision'] < snapshots['savedRevision']) is (failed_store == 'recent')
    editor.reload(wait_until='networkidle')
    editor.locator('.template-lib-card:not([data-template-built-in]) .template-lib-open').first.click()
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    assert editor.evaluate('reviewEditor.state.adjustments.x') == 23


def test_import_recomputes_history_buttons_after_unlocking_the_previous_template(editor):
    _import(editor, spine_zip())
    editor.locator('#inspector-tab-adjust').click()
    editor.locator('#adjust-overwrite').check()
    slider = editor.locator('#adjust-scale')
    slider.focus()
    slider.press('ArrowRight')
    slider.blur()
    assert editor.locator('#undo-btn').is_enabled()
    _import(editor, spine_zip('other'), 'B.zip')
    assert editor.locator('#undo-btn').is_disabled()
    assert editor.locator('#redo-btn').is_disabled()


def test_registry_activation_waits_for_renderer_creation_and_skips_duplicate_switches(editor):
    _import(editor, spine_zip(), 'A.zip')
    a_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    _import(editor, spine_zip('other'), 'B.zip')
    editor.evaluate('''key => {
        const q=reviewEditor,api=NekoEmoteSpineRenderer,original=api.create;
        let release;
        const gate=new Promise(r=>{release=r;});window.reviewReleaseRenderer=release;
        window.reviewRendererCreations=0;
        window.NekoEmoteSpineRenderer={...api,
            create:async (...args)=>{reviewRendererCreations+=1;await gate;return original(...args);}};
        let releaseQueue;
        window.reviewActivationBarrier=q.queueStageCapture(Symbol(),100,'barrier',()=>new Promise(r=>{releaseQueue=r;}));
        window.reviewRegistrySwitch=q.switchTemplate(key);
        window.reviewDuplicateSwitch=q.switchTemplate(key);
        releaseQueue();
    }''', a_key)
    editor.wait_for_function('reviewRendererCreations===1')
    assert editor.locator('#adjust-scale').is_disabled()
    editor.evaluate('''() => {
        window.reviewWaitingFrames=reviewEditor.captureGifFrames({maxFrames:1,maxDimension:32});
    }''')
    result = editor.evaluate('''async () => {
        reviewReleaseRenderer();
        const [,,frames]=await Promise.all([reviewRegistrySwitch,reviewDuplicateSwitch,reviewWaitingFrames]);
        return {creations:reviewRendererCreations,loaded:reviewEditor.renderer()?.loaded,
            count:frames.length,visible:frames[0].data.some((v,i)=>i%4===3&&v>0)};
    }''')
    assert result == {'creations':1,'loaded':True,'count':1,'visible':True}
    assert editor.locator('#adjust-scale').is_enabled()


def test_a_stale_png_export_shows_a_failure_instead_of_rejecting_the_click_handler(editor):
    _import(editor, spine_zip())
    downloads = []
    editor.on('download', lambda item: downloads.append(item))
    result = editor.evaluate('''async encoded => {
        const q=reviewEditor;
        let release;
        const barrier=q.queueStageCapture(Symbol(),100,'barrier',()=>new Promise(r=>{release=r;}));
        const imported=q.importTemplateFile(new File([
            Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],'B.zip'));
        const png=q.downloadPng();
        release();
        const [,success]=await Promise.all([barrier,imported,png]);
        return {success,notice:document.querySelector('#emote-toast').classList.contains('is-visible')};
    }''', base64.b64encode(spine_zip('other')).decode('ascii'))
    assert result == {'success':True,'notice':True}
    assert downloads == []


def test_closing_a_capturing_stage_waits_before_accepting_and_saving_main_editor_adjustments(editor):
    _import(editor, spine_zip())
    editor.locator('#inspector-tab-adjust').click()
    editor.locator('#adjust-overwrite').check()
    editor.evaluate('''() => {
        const q=reviewEditor;
        q.state.adjustments.x=23;q.persistTemplateToLibrary();
        q.openStateStage();
        window.reviewYieldGate=()=>{
            delete window.reviewYieldGate;
            return new Promise(resolve=>{window.reviewReleaseClosedCapture=resolve;});
        };
        window.reviewPreview=q.captureStageSource(q.getImportedScenes()[1].id,'full',1500);
        window.reviewQueuedFramesRan=0;
        window.reviewQueuedFrames=q.queueStageCapture(Symbol(),1,'frames',()=>{
            reviewQueuedFramesRan+=1;return [];
        });
    }''')
    editor.wait_for_function('typeof window.reviewReleaseClosedCapture === "function"', timeout=5000)
    editor.locator('.st-close').click()
    try:
        assert editor.locator('#adjust-x').is_disabled()
        editor.locator('#adjust-x').evaluate('''input=>{
            input.value='42';input.dispatchEvent(new Event('input',{bubbles:true}));
            input.dispatchEvent(new Event('change',{bubbles:true}));
        }''')
        assert editor.evaluate('reviewEditor.state.adjustments.x') == 23
    finally:
        editor.evaluate('reviewReleaseClosedCapture()')
    editor.wait_for_function("!document.querySelector('#adjust-x').disabled", timeout=5000)
    assert editor.evaluate('reviewQueuedFramesRan') == 0
    editor.locator('#adjust-x').evaluate('''input=>{
        input.value='42';input.dispatchEvent(new Event('input',{bubbles:true}));
        input.dispatchEvent(new Event('change',{bubbles:true}));
    }''')
    assert editor.evaluate('''() => {
        const recent=JSON.parse(localStorage.getItem('neko-emote-maker-last-template'));
        const library=JSON.parse(localStorage.getItem(NekoEmoteTemplateLibrary.STORAGE_KEY));
        return [reviewEditor.state.adjustments.x,recent.adjustments.x,
            library.entries.find(e=>e.key===reviewEditor.currentTemplateKey()).session.adjustments.x];
    }''') == [42,42,42]


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'failure', 'pagehide'])
def test_batch_restores_its_own_snapshot_after_directory_preparation_overlaps_a_stage_capture(editor, outcome):
    _import(editor, spine_zip())
    result = editor.evaluate('''async outcome => {
        const q=reviewEditor,scenes=q.getImportedScenes();
        Object.assign(q.state,{selectedSceneId:scenes[0].id,animationId:scenes[0].animation.id,
            animationProgress:37,animationPlaying:false,exportFormat:'png',exportResolution:32});
        let batch,temporary=false,started=false,writes=0,zipSaved=false;
        const saved=new Map();
        const file=name=>({getFile:async()=>saved.get(name),createWritable:async()=>({
            write:async data=>{
                if(outcome==='failure') throw Error('test write');
                if(name.endsWith('.zip')) zipSaved=true;
                else {
                    writes+=1;saved.set(name,new Blob([data]));
                    if(writes===2 && outcome==='pagehide') window.dispatchEvent(new PageTransitionEvent('pagehide'));
                }
            },close:async()=>{},abort:async()=>{}})});
        const folder={getFileHandle:async name=>file(name)};
        const parent={entries:async function*(){},getDirectoryHandle:async()=>folder,getFileHandle:async name=>file(name)};
        window.reviewCaptureHook=()=>{
            if(!started){
                started=true;temporary=q.state.selectedSceneId===scenes[1].id;
                batch=q.exportGroup({id:'batch',name:'batch',readOnly:true,
                    items:scenes.slice(0,2).map(scene=>({kind:'scene',ref:scene.id}))},null,Promise.resolve(parent));
            }
            else if(q.state.selectedSceneId===scenes[0].id && outcome==='cancel')
                document.querySelector('#export-group-open-btn').click();
        };
        try{
            const preview=q.captureStageSource(scenes[1].id,'full',1500);
            await preview;
            if(!batch) throw Error('batch request was not triggered during capture');
            await Promise.race([batch,new Promise((_,reject)=>setTimeout(()=>reject(Error('batch timed out')),10000))]);
            return {temporary,scene:q.state.selectedSceneId,animation:q.state.animationId,
                progress:q.state.animationProgress,playing:q.state.animationPlaying,writes,zipSaved,
                expectedScene:scenes[0].id,expectedAnimation:scenes[0].animation.id,
                savedScene:JSON.parse(localStorage.getItem('neko-emote-maker-last-template')).sceneId};
        }finally{delete window.reviewCaptureHook;}
    }''', outcome)
    assert result['temporary']
    assert result['writes'] == (0 if outcome == 'failure' else 1 if outcome == 'cancel' else 2)
    assert result['zipSaved'] is (outcome != 'failure')
    if outcome == 'pagehide':
        assert result['savedScene'] == result['expectedScene']
        return
    assert result['scene'] == result['expectedScene']
    assert result['animation'] == result['expectedAnimation']
    assert result['progress'] == 37
    assert result['playing'] is False


@pytest.mark.parametrize('pack_format', ['flat', 'layered'])
def test_a_package_clip_starting_after_the_animation_ends_exports_one_still_frame(editor, pack_format):
    from PIL import Image

    _import(editor, spine_zip())
    exported = editor.evaluate('''async format => {
        const q=reviewEditor,scene=q.getImportedScenes()[0];
        Object.assign(q.state,{packFormat:format,packMode:'graph',exportResolution:32,exportFps:8,
            nodeGraph:NekoEmoteNodeGraph.normalizeGraph({schemaVersion:1,nodes:[
                {id:'source',type:'source.scene',params:{sceneId:scene.id}},
                {id:'idle',type:'output.state',params:{stateKey:'idle_image',startAt:999,seconds:.05}},
            ],links:[{from:'source',to:'idle',fromSocket:'look',toSocket:'look'}]})});
        const pack=await q.buildPngtuberPackage();
        if(!pack) throw Error('end-of-clip package failed');
        return pack.files.map(f=>({path:f.path,data:btoa(String.fromCharCode(...f.data))}));
    }''', pack_format)
    files = {entry['path'].split('/', 1)[1]: base64.b64decode(entry['data']) for entry in exported}
    if pack_format == 'flat':
        with Image.open(io.BytesIO(files['idle.gif'])) as gif:
            assert gif.n_frames == 1
    else:
        layer = next(layer for layer in json.loads(files['metadata.json'])['layers'] if layer['name'] == 'idle_image')
        assert next(state for state in layer['states'] if state['visible'])['frames'] == 1


@pytest.mark.parametrize('pack_format', ['flat', 'layered'])
def test_full_package_keeps_a_fifty_millisecond_spine_clip_at_eight_fps(editor, pack_format):
    from PIL import Image

    _import(editor, spine_zip())
    exported = editor.evaluate('''async format => {
        const q=reviewEditor, scene=q.getImportedScenes()[0];
        Object.assign(q.state,{packFormat:format,packMode:'graph',exportResolution:32,exportFps:8,
            nodeGraph:NekoEmoteNodeGraph.normalizeGraph({schemaVersion:1,nodes:[
                {id:'source',type:'source.scene',params:{sceneId:scene.id}},
                {id:'idle',type:'output.state',params:{stateKey:'idle_image',startAt:.2,seconds:.05}},
            ],links:[{from:'source',to:'idle',fromSocket:'look',toSocket:'look'}]})});
        const pack=await q.buildPngtuberPackage();
        if(!pack) throw new Error('short-clip-package-failed');
        return pack.files.map(f=>({path:f.path,data:btoa(String.fromCharCode(...f.data))}));
    }''', pack_format)
    files = {entry['path'].split('/', 1)[1]: base64.b64decode(entry['data']) for entry in exported}
    if pack_format == 'flat':
        with Image.open(io.BytesIO(files['idle.gif'])) as gif:
            assert gif.n_frames == 1
            assert gif.info['duration'] == 50
    else:
        metadata = json.loads(files['metadata.json'])
        layer = next(layer for layer in metadata['layers'] if layer['name'] == 'idle_image')
        state = next(state for state in layer['states'] if state['visible'])
        assert state['frames'] == 1
        assert state['frames'] / (state['animation_speed'] * 4) == pytest.approx(.05)


@pytest.mark.parametrize('capturing', [False, True])
def test_pagehide_saves_the_editor_view_instead_of_temporary_capture_appearance(editor, capturing):
    _import(editor, _two_skins())
    editor.wait_for_function('''async () => {
        const key=reviewEditor.currentTemplateKey();
        return (await (await fetch('/api/emote-maker/library')).json()).entries.some(e=>e.key===key);
    }''')
    result = editor.evaluate('''async capturing => {
        const q=reviewEditor;
        q.state.adjustments={x:23,y:-8,rotation:31,scale:123};
        q.persistTemplateToLibrary();
        let temporary=false;
        if(capturing){
            q.state.nodeGraph=NekoEmoteNodeGraph.normalizeGraph({schemaVersion:1,nodes:[
                {id:'root',type:'source.default',params:{}},
                {id:'skin',type:'mod.skin',params:{groups:[{group:'skin',value:'alternate'}]}},
                {id:'move',type:'mod.transform',params:{x:147}},
                {id:'idle',type:'output.state',params:{stateKey:'idle_image'}},
            ],links:[['root','skin'],['skin','move'],['move','idle']].map(([from,to])=>({
                from,to,fromSocket:'look',toSocket:'look'}))});
            let sent=false;
            window.reviewCaptureHook=()=>{
                if(sent) return;sent=true;
                temporary=q.state.adjustments.x===170 && q.state.templateSkins[0].value==='alternate';
                window.dispatchEvent(new PageTransitionEvent('pagehide'));
            };
            try {await q.captureStageSource('root:idle_image','full',100);}catch(_){}
            delete window.reviewCaptureHook;
        }else{
            q.state.adjustments.x=55;
            q.state.templateSkins=[{group:'skin',value:'alternate'}];
            window.dispatchEvent(new PageTransitionEvent('pagehide'));
        }
        const last=JSON.parse(localStorage.getItem('neko-emote-maker-last-template'));
        const library=JSON.parse(localStorage.getItem('neko-emote-maker-template-library'));
        const entry=library.entries.find(e=>e.key===last.fingerprint);
        return {temporary,lastX:last.adjustments.x,libraryX:entry.session.adjustments.x,
            lastSkin:last.character.skins[0].value,librarySkin:entry.session.character.skins[0].value,key:entry.key};
    }''', capturing)
    expected_x, expected_skin = (23, 'default') if capturing else (55, 'alternate')
    assert result['temporary'] is capturing
    assert result['lastX'] == result['libraryX'] == expected_x
    assert result['lastSkin'] == result['librarySkin'] == expected_skin
    editor.reload(wait_until='networkidle')
    editor.locator(f'.template-lib-card[data-template-key="{result["key"]}"] .template-lib-open').click()
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    assert editor.evaluate('reviewEditor.state.adjustments.x') == expected_x
    assert editor.evaluate('reviewEditor.state.templateSkins[0].value') == expected_skin


def test_thirteenth_template_keeps_saved_edits_and_owned_archives_remain_accessible(editor):
    _import(editor, _two_skins(), 'A.zip')
    a_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    expected = editor.evaluate('''() => {
        const q=reviewEditor, scenes=q.getImportedScenes();
        const group=NekoEmoteGroupLibrary.makeGroup({name:'Saved A',look:q.currentLookDefaults(),
            items:[{kind:'scene',ref:scenes[1].id}]});
        q.state.groups=[group]; q.state.groupId=group.id;
        q.state.packSceneIds={idle_image:scenes[0].id,talking_image:scenes[1].id};
        q.state.templateSkins=[{group:'skin',value:'alternate'}];
        q.state.adjustments={x:23,y:-8,rotation:31,scale:123};
        q.persistTemplateToLibrary();
        return {groups:q.state.groups,packSceneIds:q.state.packSceneIds,
            skins:q.state.templateSkins,adjustments:q.state.adjustments};
    }''')
    other_keys = []
    for index in range(12):
        _import(editor, spine_zip(f'other-{index}'), f'B-{index}.zip')
        other_keys.append(editor.evaluate('reviewEditor.currentTemplateKey()'))
    assert editor.evaluate("JSON.parse(localStorage.getItem('neko-emote-maker-template-library')).entries.length") == 13
    editor.wait_for_function('''async () => (await (await fetch('/api/emote-maker/library')).json()).entries.length === 13''')
    editor.reload(wait_until='networkidle')
    assert editor.locator('.template-lib-card:not([data-template-built-in])').count() == 13
    editor.locator(f'[data-template-key="{a_key}"] .template-lib-open').click()
    editor.wait_for_function('key => reviewEditor.currentTemplateKey()===key && Boolean(reviewEditor.renderer()?.loaded)', arg=a_key)
    assert editor.evaluate('''() => {
        const q=reviewEditor;return {groups:q.state.groups,packSceneIds:q.state.packSceneIds,
            skins:q.state.templateSkins,adjustments:q.state.adjustments};
    }''') == expected
    # Model an entry evicted by the previous capped recent list; the owned ZIP remains.
    orphan_key = other_keys[0]
    editor.add_init_script('''(() => {
        const key=__ORPHAN_KEY__;
        const name='neko-emote-maker-template-library',store=JSON.parse(localStorage.getItem(name));
        store.entries=store.entries.filter(entry=>entry.key!==key);
        localStorage.setItem(name,JSON.stringify(store));
    })();'''.replace('__ORPHAN_KEY__', json.dumps(orphan_key)))
    editor.reload(wait_until='networkidle')
    orphan = editor.locator(f'.template-lib-card[data-template-key="{orphan_key}"]')
    orphan.wait_for(state='visible')
    assert orphan.locator('.asset-card-label').inner_text() == 'B-0'
    orphan.locator('.template-lib-open').click()
    editor.wait_for_function('key => reviewEditor.currentTemplateKey()===key && Boolean(reviewEditor.renderer()?.loaded)', arg=orphan_key)
    orphan.locator('.template-lib-remove').click()
    editor.locator('#emote-confirm-ok').click()
    orphan.wait_for(state='detached')
    assert editor.locator('.template-lib-card:not([data-template-built-in])').count() == 12


@pytest.mark.parametrize('path', ['registry', 'import', 'reimport'])
def test_template_activation_clears_the_previous_group_preview(editor, path):
    original = _two_skins()
    _import(editor, original, 'A.zip')
    if path == 'registry':
        a_key = editor.evaluate('reviewEditor.currentTemplateKey()')
        _import(editor, spine_zip(), 'B.zip')
        b_key = editor.evaluate('reviewEditor.currentTemplateKey()')
        editor.evaluate('key => reviewEditor.applyTemplateFromRegistry(key)', a_key)
        editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    editor.evaluate('''() => {
        const q = reviewEditor, look = q.currentLookDefaults();
        look.transform.x = 170;
        look.skins = [{group:'skin', value:'alternate'}];
        q.previewGroupMember({kind:'scene', ref:q.getImportedScenes()[0].id}, look);
    }''')
    assert editor.evaluate('reviewEditor.renderLook().transform.x') == 170
    if path == 'registry':
        editor.evaluate('key => reviewEditor.applyTemplateFromRegistry(key)', b_key)
        editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    else:
        _import(editor, original if path == 'reimport' else spine_zip(), 'next.zip')
    assert editor.evaluate('reviewEditor.renderLook()') is None
    assert editor.evaluate('reviewEditor.state.adjustments.x') == 0
    assert editor.evaluate('reviewEditor.state.templateSkins[0].value') == 'default'


def test_failed_import_keeps_the_current_group_preview(editor):
    _import(editor, spine_zip())
    result = editor.evaluate('''async () => {
        const q = reviewEditor, look = q.currentLookDefaults();
        look.transform.x = 170;
        q.previewGroupMember({kind:'scene', ref:q.getImportedScenes()[0].id}, look);
        const imported = await q.importTemplateFile(new File(['invalid'], 'bad.zip'));
        return {imported, x:q.renderLook()?.transform.x, loaded:q.renderer().loaded};
    }''')
    assert result == {'imported': False, 'x': 170, 'loaded': True}


@pytest.mark.parametrize('path', ['registry', 'import'])
@pytest.mark.parametrize('fail', [False, True])
def test_template_change_waits_for_closed_stage_capture_before_saving_appearances(editor, path, fail):
    original = _two_skins()
    _import(editor, original, 'A.zip')
    a_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    editor.evaluate('''() => {
        reviewEditor.state.adjustments = {x:23,y:-8,rotation:31,scale:123};
        reviewEditor.persistTemplateToLibrary();
    }''')
    other = spine_zip(model='other')
    _import(editor, other, 'B.zip')
    b_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    editor.evaluate('''() => {
        reviewEditor.state.adjustments = {x:-35,y:12,rotation:-9,scale:87};
        reviewEditor.persistTemplateToLibrary();
    }''')
    editor.evaluate('key => reviewEditor.applyTemplateFromRegistry(key)', a_key)
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    result = editor.evaluate('''async ([path, fail, aKey, bKey, encoded]) => {
        const q=reviewEditor, model=NekoEmoteNodeGraph;
        const saved=JSON.parse(localStorage.getItem('neko-emote-maker-template-library'));
        const before=Object.fromEntries(saved.entries.map(e=>[e.key,JSON.stringify({
            character:e.session.character,adjustments:e.session.adjustments})]));
        q.state.nodeGraph=model.normalizeGraph({schemaVersion:1,nodes:[
            {id:'root',type:'source.default',params:{}},
            {id:'skin',type:'mod.skin',params:{groups:[{group:'skin',value:'alternate'}]}},
            {id:'move',type:'mod.transform',params:{x:170}},
            {id:'idle',type:'output.state',params:{stateKey:'idle_image'}},
        ],links:[['root','skin'],['skin','move'],['move','idle']].map(([from,to])=>({
            from,to,fromSocket:'look',toSocket:'look'}))});
        let release, change, readbacks=0, temporary=false;
        const barrier=q.queueStageCapture(Symbol(),1000,'barrier',()=>new Promise(r=>{release=r;}));
        q.openStateStage();
        const pending=q.captureStageSource('root:idle_image','full',100).catch(()=>[]);
        window.reviewCaptureHook=()=>{
            readbacks+=1;
            if(readbacks===1){
                temporary=q.state.templateSkins.some(s=>s.value==='alternate') && q.state.adjustments.x!==23;
                queueMicrotask(()=>{
                    document.querySelector('.st-close').click();
                    change=path==='registry' ? q.applyTemplateFromRegistry(bKey)
                        : q.importTemplateFile(new File([Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],'B.zip'));
                });
            }
            if(fail && readbacks>2) throw new Error('expected-preview-failure');
        };
        try {
            release();
            await Promise.all([barrier,pending]);
            if(!change) throw new Error('template change did not start');
            await change;
            await q.queueStageCapture(Symbol(),0,'barrier',()=>{});
            q.persistTemplateToLibrary();
            const after=JSON.parse(localStorage.getItem('neko-emote-maker-template-library'));
            const snapshot=key=>{
                const s=after.entries.find(e=>e.key===key).session;
                return JSON.stringify({character:s.character,adjustments:s.adjustments});
            };
            return {temporary,closed:!document.querySelector('.st-close'),key:q.currentTemplateKey(),
                aUnchanged:snapshot(aKey)===before[aKey],bUnchanged:snapshot(bKey)===before[bKey],
                activeX:q.state.adjustments.x,activeSkin:q.state.templateSkins[0].value};
        } finally {delete window.reviewCaptureHook;}
    }''', [path, fail, a_key, b_key, base64.b64encode(other).decode('ascii')])
    assert result == {'temporary': True, 'closed': True, 'key': b_key, 'aUnchanged': True,
                      'bUnchanged': True, 'activeX': -35, 'activeSkin': 'default'}


@pytest.mark.parametrize('trigger', ['button', 'shortcut'])
def test_standalone_gif_after_group_preview_uses_selected_animation_and_restores_preview(editor, trigger):
    from PIL import Image

    changed = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(spine_zip())) as source, zipfile.ZipFile(changed, 'w') as target:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename.endswith('.json'):
                model = json.loads(content)
                model['animations']['idle1']['bones']['root']['rotate'][-1] = {'time':2,'value':120}
                content = json.dumps(model).encode()
            target.writestr(entry, content)
    _import(editor, changed.getvalue())
    baseline = editor.evaluate('''async () => {
        const q=reviewEditor, scenes=q.getImportedScenes();
        Object.assign(q.state,{selectedSceneId:scenes[0].id,animationId:scenes[0].animation.id,
            exportFormat:'gif',exportResolution:64,exportFps:20});
        const frames=await q.captureGifFrames({scene:scenes[0],fps:20,maxDimension:64});
        const bytes=q.flatStates([{stateKey:'idle_image',edge:64,fps:20,frames}])[0].bytes;
        const look=q.currentLookDefaults();
        look.transform.x=130;
        q.previewGroupMember({kind:'scene',ref:scenes[1].id},look);
        window.reviewExportAnimations=[];
        window.reviewCaptureHook=r=>reviewExportAnimations.push(r.currentAnimation);
        return btoa(String.fromCharCode(...bytes));
    }''')
    with editor.expect_download(timeout=15000) as download:
        if trigger == 'shortcut':
            editor.keyboard.press('Control+s')
        else:
            editor.locator('#export-png-btn').click()
    assert download.value.suggested_filename == 'neko-emote-idle0.gif'
    with Image.open(download.value.path()) as actual, Image.open(io.BytesIO(base64.b64decode(baseline))) as expected:
        assert actual.n_frames == expected.n_frames == 20
        elapsed = 0
        for index in range(actual.n_frames):
            actual.seek(index)
            expected.seek(index)
            assert actual.convert('RGBA').getchannel('A').tobytes() == expected.convert('RGBA').getchannel('A').tobytes()
            elapsed += actual.info['duration']
        assert elapsed == 1000
    editor.wait_for_function('reviewEditor.renderer()?.currentAnimation === "idle1"')
    assert set(editor.evaluate('reviewExportAnimations')) == {'idle0'}
    assert editor.evaluate('reviewEditor.state.animationId') == 'idle0'
    assert editor.evaluate('reviewEditor.renderLook().transform.x') == 130


@pytest.mark.parametrize('outcome', ['success', 'failure', 'selection_cancel', 'selection_choose'])
def test_template_import_blocks_exports_and_keeps_resource_selection_usable(editor, outcome):
    _import(editor, spine_zip(), 'A.zip')
    original_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    status_before = editor.locator('#template-import-status').text_content()
    tone_before = editor.locator('#template-import-status').get_attribute('data-tone')
    candidate = spine_zip('other', invalid_image=outcome == 'failure')
    if outcome.startswith('selection_'):
        result = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(candidate)) as source, zipfile.ZipFile(result, 'w') as target:
            for entry in source.infolist():
                target.writestr(entry, source.read(entry))
            target.writestr('assets/second.json', source.read('assets/other.json'))
        candidate = result.getvalue()
    downloads = []
    editor.on('download', lambda download: downloads.append(download))
    editor.evaluate('''encoded => {
        const original=window.fetch;
        let held=false;
        window.fetch=async (...args)=>{
            if(!held && args[0]==='/api/emote-maker/import'){
                held=true;
                await new Promise(resolve=>{window.reviewReleaseImport=resolve;});
            }
            return original(...args);
        };
        window.reviewImportReadbacks=0;
        window.reviewCaptureHook=()=>{reviewImportReadbacks+=1;};
        window.reviewPendingImport=reviewEditor.importTemplateFile(new File([
            Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],'B.zip'));
    }''', base64.b64encode(candidate).decode('ascii'))
    editor.wait_for_function('Boolean(window.reviewReleaseImport)')
    assert editor.evaluate('''document.dispatchEvent(new KeyboardEvent('keydown',{
        key:'s',ctrlKey:true,bubbles:true,cancelable:true}))''') is False
    for selector in ['#export-png-btn', '#pack-export-btn', '#export-group-open-btn']:
        assert editor.locator(selector).is_disabled()
        editor.locator(selector).dispatch_event('click')
    editor.keyboard.press('Control+s')
    assert editor.evaluate('reviewImportReadbacks') == 0
    assert downloads == []
    editor.evaluate('reviewReleaseImport()')
    if outcome.startswith('selection_'):
        dialog = editor.locator('.emote-template-resource-choice')
        dialog.wait_for(state='visible')
        assert dialog.locator('.emote-confirm-cancel').is_enabled()
        if outcome == 'selection_choose':
            select = dialog.locator('select')
            assert select.is_visible()
            select.click()
            editor.keyboard.press('ArrowDown')
            editor.keyboard.press('Enter')
            assert select.input_value() == 'assets/other.json'
            dialog.locator('.emote-confirm-ok').click()
        else:
            dialog.locator('.emote-confirm-cancel').click()
    succeeded = outcome in ('success', 'selection_choose')
    assert editor.evaluate('reviewPendingImport') is succeeded
    if outcome == 'selection_cancel':
        assert editor.locator('#template-import-status').text_content() == status_before
        assert editor.locator('#template-import-status').get_attribute('data-tone') == tone_before
    assert (editor.evaluate('reviewEditor.currentTemplateKey()') != original_key) is succeeded
    assert editor.evaluate('reviewImportReadbacks') == 0
    assert downloads == []
    assert editor.locator('#export-png-btn').is_enabled()
    editor.evaluate("reviewEditor.state.exportFormat='png'")
    with editor.expect_download(timeout=15000):
        editor.keyboard.press('Control+s')


def test_cancelling_the_second_resource_choice_restores_the_original_status(editor):
    _import(editor, spine_zip())
    before = (editor.locator('#template-import-status').text_content(),
              editor.locator('#template-import-status').get_attribute('data-tone'))
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(spine_zip('other'))) as source, zipfile.ZipFile(result, 'w') as target:
        target.writestr('assets/other.json', source.read('assets/other.json'))
        target.writestr('assets/second.json', source.read('assets/other.json'))
        target.writestr('assets/a.atlas', source.read('assets/other.atlas'))
        target.writestr('assets/b.atlas', source.read('assets/other.atlas'))
        target.writestr('assets/page.png', source.read('assets/page.png'))
    editor.evaluate('''encoded => {
        window.reviewNestedImport=reviewEditor.importTemplateFile(new File([
            Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],'multi.zip'));
    }''', base64.b64encode(result.getvalue()).decode('ascii'))
    dialog = editor.locator('.emote-template-resource-choice')
    dialog.wait_for(state='visible')
    dialog.locator('select').click()
    editor.keyboard.press('ArrowDown')
    editor.keyboard.press('Enter')
    with editor.expect_response(lambda response: '/api/emote-maker/import' in response.url
                                and response.request.method == 'POST'):
        dialog.locator('.emote-confirm-ok').click()
    editor.wait_for_function("document.querySelector('.emote-template-resource-choice select')?.options[1]?.value.endsWith('.atlas')")
    dialog.locator('.emote-confirm-cancel').click()
    assert editor.evaluate('reviewNestedImport') is False
    assert (editor.locator('#template-import-status').text_content(),
            editor.locator('#template-import-status').get_attribute('data-tone')) == before
    assert editor.locator('#export-png-btn').is_enabled()


@pytest.mark.parametrize(('failure_status', 'failed_image'), [(503,'one.png'), (503,'two.png'), (404,'two.png')])
def test_stage_texture_restore_retries_temporary_errors_but_keeps_missing_file_handling(editor, failure_status, failed_image):
    from urllib.parse import unquote

    _import(editor, spine_zip())
    seed = editor.evaluate('''async failedImage => {
        const q=reviewEditor;
        const files=['one.png','two.png'].map((name,index)=>{
            const canvas=document.createElement('canvas');canvas.width=canvas.height=8;
            const ctx=canvas.getContext('2d');ctx.fillStyle=index?'#0000ff':'#00ff00';ctx.fillRect(0,0,8,8);
            return new File([Uint8Array.from(atob(canvas.toDataURL().split(',')[1]),c=>c.charCodeAt(0))],name,{type:'image/png'});
        });
        await q.importStageImages(files);
        const id=q.stageImages().find(image=>image.name==='two.png').id;
        Object.assign(q.state,{packMode:'graph',exportResolution:32,exportFps:10,
            nodeGraph:NekoEmoteNodeGraph.normalizeGraph({schemaVersion:1,nodes:[
                {id:'image',type:'source.image',params:{imageId:id}},
                {id:'idle',type:'output.state',params:{stateKey:'idle_image',seconds:.1}},
            ],links:[{from:'image',to:'idle',fromSocket:'look',toSocket:'look'}]})});
        q.persistTemplateToLibrary();
        return {key:q.currentTemplateKey(),id,failed:q.stageImages().find(image=>image.name===failedImage).id};
    }''', failed_image)
    editor.reload(wait_until='networkidle')
    editor.locator(f'.template-lib-card[data-template-key="{seed["key"]}"] .template-lib-open').click()
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    attempts = []

    def texture(route):
        if f'StageImages/{seed["failed"]}.png' in unquote(route.request.url):
            attempts.append(route.request.url)
            if len(attempts) == 1:
                route.fulfill(status=failure_status, json={'error': 'expected-test-failure'})
                return
        route.continue_()

    editor.route('**/api/emote-maker/library/texture?*', texture)
    editor.evaluate('async () => {await reviewEditor.restoreStageImages();}')
    assert len(attempts) == 1
    restored = [image['id'] for image in editor.evaluate('reviewEditor.stageImages()')]
    assert seed['failed'] not in restored
    if failed_image == 'one.png':
        assert seed['id'] in restored, 'a failed first image must not prevent restoring the later idle source'
    result = editor.evaluate('''async () => {
        reviewEditor.state.packMode='graph';
        return Boolean(await reviewEditor.buildPngtuberPackage());
    }''')
    assert result is (failure_status == 503)
    assert len(attempts) == (2 if failure_status == 503 else 1)
    assert (seed['failed'] in [image['id'] for image in editor.evaluate('reviewEditor.stageImages()')]) is (failure_status == 503)


def test_template_activation_does_not_overtake_an_already_queued_export(editor):
    _import(editor, spine_zip(), 'A.zip')
    original_key = editor.evaluate('reviewEditor.currentTemplateKey()')
    result = editor.evaluate('''async encoded => {
        const q=reviewEditor;
        let release;
        const barrier=q.queueStageCapture(Symbol(),1000,'barrier',()=>new Promise(r=>{release=r;}));
        const captured=[];
        window.reviewCaptureHook=()=>captured.push(q.currentTemplateKey());
        const exported=q.captureGifFrames({maxFrames:1,maxDimension:32});
        const imported=q.importTemplateFile(new File([
            Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],'B.zip'));
        release();
        const [,frames,success]=await Promise.all([barrier,exported,imported]);
        delete window.reviewCaptureHook;
        return {captured,count:frames.length,success,key:q.currentTemplateKey()};
    }''', base64.b64encode(spine_zip('other')).decode('ascii'))
    assert result['captured'] == [original_key]
    assert result['count'] == 1
    assert result['success']
    assert result['key'] != original_key


def test_stale_template_pack_jobs_are_discarded_before_applying_their_look(editor):
    _import(editor, _two_skins(), 'A.zip')
    result = editor.evaluate('''async encoded => {
        const q=reviewEditor, generation=q.generation(), scene=q.getImportedScenes()[0];
        const look=q.currentLookDefaults();
        look.skins=[{group:'skin',value:'alternate'}];
        look.transform.x=170;
        const jobs=[{stateKey:'idle_image',sceneId:scene.id,look,frames:1}];
        await q.importTemplateFile(new File([
            Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],'B.zip'));
        q.state.adjustments.x=-35;
        let readbacks=0;
        window.reviewCaptureHook=()=>{readbacks+=1;};
        try {
            const baked=await q.bakePackJobs(jobs,{generation});
            return {count:baked.length,readbacks,x:q.state.adjustments.x,skin:q.state.templateSkins[0].value,
                sameScene:q.getImportedScenes()[0].id===scene.id};
        } finally {delete window.reviewCaptureHook;}
    }''', base64.b64encode(spine_zip('other')).decode('ascii'))
    assert result == {'count': 0, 'readbacks': 0, 'x': -35, 'skin': 'default', 'sameScene': True}


@pytest.mark.parametrize('pack_format', ['flat', 'layered'])
def test_package_plans_default_look_only_after_a_closed_stage_preview_finishes(editor, pack_format):
    _import(editor, _different_starting_bounds())
    result = editor.evaluate(r'''async format => {
        const q=reviewEditor, scenes=q.getImportedScenes();
        Object.assign(q.state,{selectedSceneId:scenes[0].id,animationId:scenes[0].animation.id,
            animationPlaying:false,exportResolution:64,exportFps:10,packFormat:format,packMode:'graph'});
        q.state.nodeGraph=NekoEmoteNodeGraph.normalizeGraph({schemaVersion:1,nodes:[
            {id:'default',type:'source.default',params:{}},
            {id:'idle',type:'output.state',params:{stateKey:'idle_image'}},
        ],links:[{from:'default',to:'idle',fromSocket:'look',toSocket:'look'}]});
        const pictures=pack=>{
            if(!pack) throw new Error('pack did not build');
            return pack.files.filter(f=>/\.(gif|png)$/.test(f.path))
                .map(f=>({path:f.path,data:Array.from(f.data)}));
        };
        const baseline=pictures(await q.buildPngtuberPackage());
        let release, pending, temporary=false, requested=false;
        const barrier=q.queueStageCapture(Symbol(),2000,'barrier',()=>new Promise(r=>{release=r;}));
        q.openStateStage();
        const preview=q.captureStageSource(scenes[1].id,'full',1500);
        window.reviewCaptureHook=()=>{
            if(requested) return;
            requested=true;
            temporary=q.state.selectedSceneId===scenes[1].id;
            queueMicrotask(()=>{
                document.querySelector('.st-close').click();
                pending=q.buildPngtuberPackage();
            });
        };
        try {
            release();
            await Promise.all([barrier,preview]);
            if(!pending) throw new Error('package request did not start');
            const actual=pictures(await pending);
            return {temporary,closed:!document.querySelector('.st-close'),count:actual.length,
                equal:JSON.stringify(actual)===JSON.stringify(baseline),scene:q.state.selectedSceneId,
                selected:scenes[0].id};
        } finally {delete window.reviewCaptureHook;}
    }''', pack_format)
    assert result['temporary'] and result['closed']
    assert result['count'] > 0
    assert result['equal']
    assert result['scene'] == result['selected']


@pytest.mark.parametrize('path', ['registry', 'reimport'])
def test_reactivation_preserves_framing_for_an_animation_with_different_starting_bounds(editor, path):
    original = _different_starting_bounds()
    _import(editor, original, 'A.zip')
    result = editor.evaluate('''async ([path, aBytes, bBytes]) => {
        const q=reviewEditor, key=q.currentTemplateKey(), scene=q.getImportedScenes()[1];
        Object.assign(q.state,{selectedSceneId:scene.id,animationId:scene.animation.id,animationPlaying:false});
        q.state.adjustments={x:10,y:-8,rotation:31,scale:80};
        const before=(await q.captureGifFrames({scene,maxFrames:1,maxDimension:64}))[0];
        const zoom=q.renderer().renderer.camera.zoom;
        q.persistTemplateToLibrary();
        const file=(encoded,name)=>new File([Uint8Array.from(atob(encoded),c=>c.charCodeAt(0))],name);
        if(!await q.importTemplateFile(file(bBytes,'B.zip'))) throw new Error('B import failed');
        if(q.currentTemplateKey()===key) throw new Error('B did not replace A');
        if(path==='registry'){
            if(!await q.applyTemplateFromRegistry(key)) throw new Error('A activation failed');
        }else if(!await q.importTemplateFile(file(aBytes,'A.zip'))) throw new Error('A reimport failed');
        if(q.currentTemplateKey()!==key) throw new Error('A was not restored');
        const deadline=performance.now()+10000;
        while(!q.renderer()?.loaded){
            if(performance.now()>deadline) throw new Error('renderer activation timed out');
            await new Promise(requestAnimationFrame);
        }
        const restored=q.state.animationId;
        const after=(await q.captureGifFrames({scene:q.getImportedScenes()[1],maxFrames:1,maxDimension:64}))[0];
        return {zoom,restoredZoom:q.renderer().renderer.camera.zoom,restored,
            equal:before.data.length===after.data.length && before.data.every((value,i)=>value===after.data[i])};
    }''', [path, base64.b64encode(original).decode('ascii'), base64.b64encode(spine_zip('other')).decode('ascii')])
    assert result['restored'] == 'idle1'
    assert result['restoredZoom'] == pytest.approx(result['zoom'])
    assert result['equal']


@pytest.mark.parametrize(('fmt', 'edge'), [('png', 512), ('png', 1024), ('gif', 512)])
def test_batch_file_uses_the_selected_resolution(editor, fmt, edge):
    from PIL import Image

    _import(editor, spine_zip())
    encoded = editor.evaluate('''async ([format, edge]) => {
        const q = reviewEditor;
        q.state.exportResolution = edge;
        const bytes = await q.produceGroupFile({readOnly:true},
            {kind:'scene', ref:q.getImportedScenes()[0].id}, format);
        let binary = '';
        for (const byte of bytes) binary += String.fromCharCode(byte);
        return btoa(binary);
    }''', [fmt, edge])
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
        assert image.format.lower() == fmt
        assert image.size == (edge, edge)


@pytest.mark.parametrize('fail', [False, True])
def test_export_blocks_animation_and_appearance_edits_and_unlocks_afterward(editor, fail):
    _import(editor, spine_zip())
    result = editor.evaluate('''async fail => {
        const q = reviewEditor, scene = q.getImportedScenes()[0];
        const animation = document.querySelectorAll('#animation-list .animation-card')[1];
        const scale = document.querySelector('#adjust-scale');
        const before = {animation:q.state.animationId, scale:q.state.adjustments.scale};
        let tested = false;
        window.reviewCaptureHook = () => {
            if (tested) return;
            tested = true;
            // Leave the attempted edits pending until a readback yields.
            queueMicrotask(() => {
                animation?.dispatchEvent(new MouseEvent('click', {bubbles:true}));
                scale.value = '180';
                scale.dispatchEvent(new Event('input', {bubbles:true}));
                window.reviewEditsBlocked = scale.disabled;
            });
        };
        let count = 0;
        const hook = window.reviewCaptureHook;
        window.reviewCaptureHook = () => {
            hook();
            if (++count > 2 && fail) throw new Error('expected-capture-failure');
            if (q.state.animationId !== before.animation || q.state.adjustments.scale !== before.scale)
                throw new Error('capture-mutated');
        };
        let error = '';
        try { await q.captureGifFrames({scene, maxFrames:8, maxDimension:32}); }
        catch (e) { error = e.message; }
        finally { delete window.reviewCaptureHook; }
        const unchanged = q.state.animationId === before.animation && q.state.adjustments.scale === before.scale;
        const unlocked = !scale.disabled && !document.querySelector('.emote-maker-page').hasAttribute('aria-busy');
        scale.value = '125';
        scale.dispatchEvent(new Event('input', {bubbles:true}));
        return {tested:tested && Boolean(animation), blocked:window.reviewEditsBlocked,
            unchanged, unlocked, editable:q.state.adjustments.scale === 125, error};
    }''', fail)
    assert result == {'tested': True, 'blocked': True, 'unchanged': True, 'unlocked': True,
                      'editable': True, 'error': 'expected-capture-failure' if fail else ''}


def test_browser_decodes_zip_in_a_dedicated_worker(editor):
    editor.evaluate('''() => {
        const OriginalWorker = window.Worker;
        window.reviewWorkers = [];
        window.Worker = class extends OriginalWorker {
            constructor(...args) { super(...args); window.reviewWorkers.push(String(args[0])); }
        };
    }''')
    _import(editor, spine_zip())
    assert any(url.endswith('/static/js/emote_template_zip_worker.js') for url in editor.evaluate('reviewWorkers'))


@pytest.mark.parametrize('bad_kind', ['signature', 'decode'])
def test_invalid_png_does_not_enter_or_persist_in_the_image_library(editor, bad_kind):
    _import(editor, spine_zip())
    uploads = []
    editor.on('request', lambda request: uploads.append(request.url) if '/library/textures?' in request.url
              and request.method == 'POST' else None)
    editor.evaluate("""async kind => {
        const bytes=kind==='signature'?'not a PNG':new Uint8Array([137,80,78,71,13,10,26,10]);
        await reviewEditor.importStageImages([new File([bytes], 'bad.png', {type: 'image/png'})]);
    }""", bad_kind)
    assert uploads == []
    assert editor.evaluate('reviewEditor.stageImages()') == []
    editor.evaluate('''async () => {
        const canvas=document.createElement('canvas');canvas.width=canvas.height=8;
        canvas.getContext('2d').fillRect(0,0,8,8);
        const bytes=Uint8Array.from(atob(canvas.toDataURL().split(',')[1]),c=>c.charCodeAt(0));
        await reviewEditor.importStageImages([new File([bytes],'valid.png',{type:'image/png'})]);
    }''')
    assert [image['name'] for image in editor.evaluate('reviewEditor.stageImages()')] == ['valid.png']
    assert uploads


def test_reimport_restores_its_own_groups_skin_and_transform(editor):
    first = _two_skins()
    _import(editor, first, "A.zip")
    editor.evaluate("""() => {
        const q = reviewEditor;
        const scene = q.getImportedScenes()[1];
        q.state.groups = [{id: 'saved-group', name: 'saved-group', items: [{kind: 'scene', ref: scene.id}]}];
        q.state.shots = [{id: 'saved-shot', name: 'pose', sourceSceneId: scene.id, progress: 75}];
        q.state.templateSkins = [{group: 'skin', value: 'alternate'}];
        q.state.adjustments = {x: 37, y: 11, rotation: 24, scale: 125};
        q.persistTemplateToLibrary();
    }""")
    _import(editor, spine_zip(model="other", animations=3), "B.zip")
    defaults = editor.evaluate("({groups: reviewEditor.state.groups, transform: reviewEditor.state.adjustments})")
    assert defaults == {"groups": [], "transform": {"x": 0, "y": 0, "rotation": 0, "scale": 100}}
    _import(editor, first, "A-again.zip")
    restored = editor.evaluate("""() => ({groups: reviewEditor.state.groups, shots: reviewEditor.state.shots,
        skin: reviewEditor.state.templateSkins[0].value, transform: reviewEditor.state.adjustments})""")
    assert restored["groups"][0]["id"] == "saved-group"
    assert restored["shots"][0]["progress"] == 75
    assert restored["skin"] == "alternate"
    assert restored["transform"] == {"x": 37, "y": 11, "rotation": 24, "scale": 125}


@pytest.mark.parametrize('mode', ['speech-reload', 'pause-click', 'hide-click'])
def test_layered_reload_and_click_cleanup(page, running_server, mode):
    from tests.frontend.test_emote_pngtuber_layered_runtime import SLOT_STATES, _runtime_page, _upload_states
    folder = 'review-lifecycle-' + mode
    _upload_states(page, running_server, folder, SLOT_STATES)
    _runtime_page(page, running_server)
    try:
        result = page.evaluate("""async ([folder,mode]) => {
            const div=document.createElement('div'); div.id='review-lifecycle'; document.body.append(div);
            const root='/user_pngtuber/'+folder;
            const config=(await (await fetch(root+'/model.json')).json()).pngtuber;
            config.layered_metadata=root+'/metadata.json'; config.idle_image=root+'/idle.png';
            const manager=new PNGTuberManager(div.id);
            try {
                await manager.load(config);
                if(mode==='speech-reload') {
                    manager.startSpeakingMouthAnimation();
                    await manager.load(config);
                    return {speaking:manager.isSpeaking,index:manager.layeredStateIndex};
                }
                manager.showClickImage();
                const clicked=manager.layeredStateIndex;
                if(mode==='pause-click') manager.pauseRendering(); else manager.hide();
                return {clicked,index:manager.layeredStateIndex,hasClick:manager.layeredSlotStack().some(s=>s.kind==='click')};
            } finally {manager.dispose();}
        }""", [folder,mode])
        if mode == 'speech-reload':
            assert result == {'speaking':True,'index':1}
        else:
            assert result == {'clicked':3,'index':0,'hasClick':False}
    finally:
        page.evaluate("""folder=>fetch('/api/model/pngtuber/model',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({folder})})""", folder)


@pytest.mark.parametrize('legacy_snapshot', [False, True])
def test_skin_change_survives_reopening_and_a_stale_library_snapshot(editor, legacy_snapshot):
    _import(editor, _two_skins())
    editor.locator('[data-template-skin-group="skin"]').select_option('alternate', force=True)
    assert editor.evaluate("""() => {
        const library=JSON.parse(localStorage.getItem('neko-emote-maker-template-library'));
        return library.entries.find(entry=>entry.key===library.currentKey).session.character.skins[0].value;
    }""") == 'alternate'
    editor.wait_for_function("""async () => {
        const key=JSON.parse(localStorage.getItem('neko-emote-maker-template-library')).currentKey;
        return (await (await fetch('/api/emote-maker/library')).json()).entries.some(entry=>entry.key===key);
    }""")
    if legacy_snapshot:
        # Model the older page: LAST_TEMPLATE_KEY is current, the library is stale.
        editor.add_init_script("""(() => {
            const key='neko-emote-maker-template-library';
            const library=JSON.parse(localStorage.getItem(key));
            const entry=library.entries.find(entry=>entry.key===library.currentKey);
            entry.session.character.skins=[{group:'skin',value:'default'}];
            delete entry.session.saveRevision;
            const recentName='neko-emote-maker-last-template';
            const recent=JSON.parse(localStorage.getItem(recentName));delete recent.saveRevision;
            localStorage.setItem(recentName,JSON.stringify(recent));
            localStorage.setItem(key,JSON.stringify(library));
        })();""")
    editor.reload(wait_until='networkidle')
    if legacy_snapshot:
        assert editor.evaluate("""() => {
            const library=JSON.parse(localStorage.getItem('neko-emote-maker-template-library'));
            return library.entries.find(entry=>entry.key===library.currentKey).session.character.skins[0].value;
        }""") == 'default'
    editor.locator('.template-lib-card:not([data-template-built-in]) .template-lib-open').first.click()
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    assert editor.evaluate('reviewEditor.state.templateSkins[0].value') == 'alternate'


@pytest.mark.parametrize("mode", ["gif", "pack", "group"])
def test_exports_wait_for_an_active_preview_capture(editor, mode):
    _import(editor, spine_zip())
    result = editor.evaluate("""async mode => {
        const q = reviewEditor, renderer = q.renderer(), scenes = q.getImportedScenes();
        await q.queueStageCapture(Symbol(), 0, 'barrier', () => {});
        const trace = [];
        window.reviewCaptureHook = r => trace.push(r.currentAnimation);
        try {
            const preview = q.queueStageCapture(Symbol(), 1, 'thumb', async () => {
                await new Promise(r => setTimeout(r, 35));
                renderer.selectAnimation(scenes[1].animation.id);
                await new Promise(r => setTimeout(r, 70));
                trace.push('preview-done');
            });
            const work = mode === 'gif'
                ? q.captureGifFrames({scene: scenes[0], maxFrames: 6, fps: 20, maxDimension: 64})
                : mode === 'pack'
                    ? q.bakePackJobs([{stateKey: 'idle_image', sceneId: scenes[0].id,
                        look: q.currentLookDefaults(), frames: 6}], {edge: 64, fps: 20})
                    : q.produceGroupFile({readOnly: true}, {kind: 'scene', ref: scenes[0].id}, 'png');
            await Promise.all([preview, work]);
            return {trace, requested: scenes[0].animation.id};
            } finally { delete window.reviewCaptureHook; }
    }""", mode)
    assert result["trace"][0] == "preview-done", result
    assert result["trace"][1:]
    assert set(result["trace"][1:]) == {result["requested"]}, result


def test_reimport_keeps_the_latest_basic_assignments_and_exports_them(editor):
    _import(editor, spine_zip())
    editor.locator('#inspector-tab-export').click()
    editor.locator('#pack-format').select_option('flat', force=True)
    editor.locator('#export-resolution').select_option('128', force=True)
    editor.locator('#pack-scene-talking').select_option('animation:idle1', force=True)
    snapshots = editor.evaluate("""() => {
        const library=JSON.parse(localStorage.getItem('neko-emote-maker-template-library'));
        const recent=JSON.parse(localStorage.getItem('neko-emote-maker-last-template'));
        const saved=library.entries.find(entry=>entry.key===library.currentKey).session;
        return {recent:recent.packSceneIds.talking_image, library:saved.packSceneIds.talking_image};
    }""")
    assert snapshots == {'recent':'animation:idle1','library':'animation:idle1'}
    _import(editor, spine_zip(), 'same-template.zip')
    assert editor.evaluate('reviewEditor.state.packSceneIds.talking_image') == 'animation:idle1'
    with editor.expect_download(timeout=45000) as download:
        editor.locator('#pack-export-btn').click()
    with zipfile.ZipFile(download.value.path()) as package:
        assert any(name.endswith('/talking.gif') for name in package.namelist()), package.namelist()


def test_global_export_placement_survives_import_and_reopening(editor):
    editor.evaluate("""() => {
        Object.assign(reviewEditor.state,{packScale:1.5,packOffsetX:12,packOffsetY:-8,packMirror:true});
        reviewEditor.persistExportSettings();
    }""")
    _import(editor, spine_zip())
    placement = "({scale:reviewEditor.state.packScale,x:reviewEditor.state.packOffsetX,y:reviewEditor.state.packOffsetY,mirror:reviewEditor.state.packMirror})"
    expected = {'scale':1.5,'x':12,'y':-8,'mirror':True}
    assert editor.evaluate(placement) == expected
    editor.wait_for_function("""async () => {
        const key=JSON.parse(localStorage.getItem('neko-emote-maker-template-library')).currentKey;
        return (await (await fetch('/api/emote-maker/library')).json()).entries.some(entry=>entry.key===key);
    }""")
    editor.reload(wait_until='networkidle')
    editor.locator('.template-lib-card:not([data-template-built-in]) .template-lib-open').first.click()
    editor.wait_for_function('Boolean(reviewEditor.renderer()?.loaded)')
    assert editor.evaluate(placement) == expected


def test_png_shot_exports_the_recorded_pose_instead_of_time_zero(editor):
    _import(editor, spine_zip())
    result = editor.evaluate("""async () => {
        const q = reviewEditor, scene = q.getImportedScenes()[0], sheet = NekoEmoteSheet;
        q.state.exportResolution = 64;
        q.state.shots = [{id: 'pose', sourceSceneId: scene.id, progress: 75}];
        const actual = await q.produceGroupFile({readOnly: true}, {kind: 'shot', ref: 'pose'}, 'png');
        async function expected(at) {
            const frames = await q.captureGifFrames({scene, maxFrames: 1, fps: 20, maxDimension: 64,
                timeWindow: {from: at, to: at + 1/20}});
            return await sheet.canvasToPngBytes(sheet.firstFrameCanvas(frames[0]));
        }
        const correct = await expected(.75), zero = await expected(0);
        const same = (a,b) => a.length === b.length && a.every((v,i) => v === b[i]);
        return {matchesSavedPose: same(actual, correct), differsFromZero: !same(actual, zero)};
    }""")
    assert result == {"matchesSavedPose": True, "differsFromZero": True}


@pytest.mark.parametrize("fail", [False, True])
def test_pack_capture_restores_the_live_character_even_after_failure(editor, fail):
    _import(editor, _two_skins())
    result = editor.evaluate("""async fail => {
        const q = reviewEditor, r = q.renderer(), scene = q.getImportedScenes()[0];
        q.state.adjustments = {x: 23, y: -8, rotation: 31, scale: 123};
        q.state.templateSkins = [{group: 'skin', value: 'default'}];
        const before = JSON.stringify({skins: q.state.templateSkins, slots: q.state.templateSlots,
            transform: q.state.adjustments});
        const look = q.currentLookDefaults();
        look.skins = [{group: 'skin', value: 'alternate'}];
        look.transform = {...look.transform, x: 70, rotation: -40};
        if (fail) window.reviewCaptureHook = () => { throw new Error('capture-test-failure'); };
        let rejected = false;
        try { await q.bakePackJobs([{stateKey: 'idle_image', sceneId: scene.id, look, frames: 2}],
            {edge: 64, fps: 10}); } catch (_) { rejected = true; }
        finally { delete window.reviewCaptureHook; }
        const after = JSON.stringify({skins: q.state.templateSkins, slots: q.state.templateSlots,
            transform: q.state.adjustments});
        return {restored: before === after, rejected};
    }""", fail)
    assert result == {"restored": True, "rejected": fail}
