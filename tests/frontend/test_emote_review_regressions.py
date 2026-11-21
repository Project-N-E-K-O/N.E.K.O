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
        importStageImages,
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
