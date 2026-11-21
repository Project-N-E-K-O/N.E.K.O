"""Exercise session restoration and competing captures in the actual editor."""
import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

from tests.emote_fixtures import spine_zip


@pytest.fixture
def editor(page, running_server):
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
        renderer: () => spineRenderer,
    };
"""
    assert source.count(marker) == 1
    capture_marker = '    function captureCurrentEditorFrame(maxDimension) {'
    assert source.count(capture_marker) == 1
    source = source.replace(capture_marker, capture_marker + '\n        window.reviewCaptureHook?.(spineRenderer);')
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
        }''')
    editor.wait_for_function("!document.querySelector('#export-group-open-btn').disabled")
    editor.evaluate('''() => {
        window.reviewBlockingCapture = reviewEditor.queueStageCapture('stop-review',100,'export',
            () => new Promise(resolve => {window.reviewFinishBlockingCapture = resolve;}));
    }''')
    editor.wait_for_function("typeof window.reviewFinishBlockingCapture === 'function'")
    assert editor.locator('#export-group-open-btn').is_disabled()
    assert len(pending) == 1
    pending[0].fulfill(json={'success':True,'directory':'Z:/test-only-intercepted','folder':'review-stop'})
    editor.wait_for_function("!document.querySelector('#export-group-open-btn').disabled")
    assert editor.locator('#adjust-scale').is_disabled()
    editor.locator('#export-group-open-btn').click(force=True)
    editor.evaluate('''async () => {
        reviewFinishBlockingCapture();
        await Promise.all([reviewBlockingCapture, reviewBatch]);
    }''')
    assert len(writes) == 1
    assert not editor.locator('.emote-maker-page').get_attribute('aria-busy')
    assert editor.locator('#export-group-open-btn').is_enabled()


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


def test_invalid_png_does_not_enter_or_persist_in_the_image_library(editor):
    _import(editor, spine_zip())
    uploads = []
    editor.on('request', lambda request: uploads.append(request.url) if '/library/textures?' in request.url
              and request.method == 'POST' else None)
    editor.evaluate("""async () => {
        await reviewEditor.importStageImages([new File(['not a PNG'], 'bad.png', {type: 'image/png'})]);
    }""")
    assert uploads == []


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
