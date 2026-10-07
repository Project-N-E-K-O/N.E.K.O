import hashlib
import json

import pytest

from main_logic.watch_together.library import Library


JOB = "0b3d279153c34ddfa8b88175d18c2e6f"


@pytest.mark.parametrize('at,duration,video_duration,ready', [
    (9, 5, 10, False), (9, 1, 10, True), (10, 1, 10, False), (11, 1, 10, False),
    (0.1, 0.2, 0.3, True), (0.1, 0.200001, 0.3, False),
])
def test_imported_audio_must_finish_within_video(tmp_path, at, duration, video_duration, ready):
    archive = source(tmp_path, 'video-end')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'video')
    original = json.dumps({'status': 'ready', 'duration': video_duration,
        'video': f'/media/{JOB}/video.mp4',
        'events': [{'at': at, 'duration': duration, 'audio': f'/media/{JOB}/laugh.mp3'}]}).encode()
    (folder / 'timeline.json').write_bytes(original)
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert (row['status'] == 'ready') is ready
    assert library.resource(JOB, row['version'], 'timeline.json').read_bytes() == original


@pytest.mark.parametrize('second_at,text_only,ready', [
    (2, False, False), (2, True, False), (1, False, False),
    (11, False, True), (12.2, False, True),
])
def test_imported_overlapping_cues_are_incomplete_without_changing_archive(tmp_path, second_at, text_only, ready):
    archive = source(tmp_path, 'overlap')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'video')
    first = {'at': 1, 'duration': 10, 'audio': f'/media/{JOB}/laugh.mp3'}
    second = {'at': second_at, 'text': 'next'} if text_only else {**first, 'at': second_at, 'duration': 1}
    # Out-of-order input must be checked in playback order.
    data = {'status': 'ready', 'video': f'/media/{JOB}/video.mp4', 'events': [second, first]}
    original = json.dumps(data).encode()
    (folder / 'timeline.json').write_bytes(original)
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert (row['status'] == 'ready') is ready
    assert library.resource(JOB, row['version'], 'timeline.json').read_bytes() == original
    assert (folder / 'timeline.json').read_bytes() == original


@pytest.mark.parametrize('bvid', [123, {}, 'x' * 33, 'BV1GJ411x7h7'])
@pytest.mark.parametrize('cover', ['https://tracker.test/pixel', '/media/' + JOB + '/cover.png', '/media/' + JOB + '/missing.png'])
def test_imported_metadata_cannot_poison_discovery_or_fetch_remote_cover(tmp_path, bvid, cover):
    archive = source(tmp_path, 'metadata-boundary')
    folder = archive / JOB
    (folder / 'cover.png').write_bytes(b'image')
    (folder / 'video.mp4').write_bytes(b'video')
    (folder / 'timeline.json').write_text(json.dumps({'status': 'ready', 'bvid': bvid,
        'cover': cover, 'video': f'/media/{JOB}/video.mp4', 'events': []}))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    loaded = library.timeline(JOB, row['version'])
    assert loaded['bvid'] == ('BV1GJ411x7h7' if bvid == 'BV1GJ411x7h7' else None)
    assert bool(loaded['cover']) is cover.endswith('/cover.png')
    if loaded['cover']:
        assert loaded['cover'].startswith('/api/watch-together/media/')


@pytest.fixture(autouse=True)
def valid_media_probe(monkeypatch):
    # These archive fixtures use placeholder bytes; probe behavior has dedicated tests.
    from main_logic.watch_together import library as module
    monkeypatch.setattr(module, '_probe_media', lambda *args: True)


@pytest.mark.parametrize('count,size,ready', [(256, 1, True), (257, 1, False),
                                            (2, 32 * 1024 * 1024, True),
                                            (2, 32 * 1024 * 1024 + 1, False)])
def test_history_matches_unique_audio_preload_budgets(tmp_path, monkeypatch, count, size, ready):
    archive = source(tmp_path, 'preload-budget')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'video')
    events = []
    for index in range(count):
        name = f'{index}.wav'
        (folder / name).write_bytes(b'audio')
        events.append({'at': index, 'duration': 1, 'audio': f'/media/{JOB}/{name}'})
    # Repeated URLs must not consume the budget twice.
    events += [{**cue, 'at': cue['at'] + count} for cue in events]
    (folder / 'timeline.json').write_text(json.dumps({'status': 'ready',
        'video': f'/media/{JOB}/video.mp4', 'events': events}))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    original_manifest = library.manifest
    def sized_manifest(*args):
        manifest = original_manifest(*args)
        for name, entry in manifest.items():
            if name.endswith('.wav'):
                entry['bytes'] = size
        return manifest
    monkeypatch.setattr(library, 'manifest', sized_manifest)
    assert library.history()[0]['status'] == ('ready' if ready else 'incomplete')


def test_corrupt_playable_object_is_incomplete(tmp_path, monkeypatch):
    from main_logic.watch_together import library as module
    archive = source(tmp_path, 'corrupt-media')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'not a video')
    (folder / 'timeline.json').write_text(json.dumps({'status': 'ready', 'video': f'/media/{JOB}/video.mp4', 'events': []}))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    monkeypatch.setattr(module, '_probe_media', lambda *args: False)
    assert library.history()[0]['status'] == 'incomplete'
    assert (folder / 'video.mp4').read_bytes() == b'not a video'
    monkeypatch.setattr(module, '_probe_media', lambda *args: None)
    assert library.history()[0]['status'] == 'incomplete', 'unknown media must not be offered for playback'
    monkeypatch.setattr(module, '_probe_media', lambda *args: True)
    assert library.history()[0]['status'] == 'ready', 'successful later validation restores readiness without reimport'
    assert (folder / 'video.mp4').read_bytes() == b'not a video'


@pytest.mark.parametrize('surrogate', ['\ud800', '\udfff'])
def test_imported_surrogates_are_safe_for_utf8_responses(tmp_path, surrogate):
    archive = source(tmp_path, 'unicode-metadata')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'video')
    original = json.dumps({'status': 'ready', 'title': f'Cat {surrogate} \U0001f431',
                          'video': f'/media/{JOB}/video.mp4',
                          'warnings': [surrogate], 'usage': {surrogate: {'text': surrogate}},
                          'events': [{'at': 1, 'text': surrogate}],
                          'unused': f'/media/{JOB}/{surrogate}.wav'}).encode('utf-8')
    (folder / 'timeline.json').write_bytes(original)
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    timeline = library.timeline(JOB, row['version'])
    assert timeline['title'] == 'Cat \ufffd \U0001f431'
    assert timeline['warnings'] == ['\ufffd']
    assert timeline['usage'] == {'\ufffd': {'text': '\ufffd'}}
    assert timeline['events'][0]['text'] == '\ufffd'
    assert timeline['unused'].endswith('/%EF%BF%BD.wav')
    json.dumps({'history': library.history_page(), 'timeline': timeline}, ensure_ascii=False).encode('utf-8')
    assert library.resource(JOB, row['version'], 'timeline.json').read_bytes() == original
    assert (folder / 'timeline.json').read_bytes() == original


@pytest.mark.parametrize('second_key', ['\udfff', '\ufffd'])
@pytest.mark.parametrize('nested', [False, True])
def test_normalized_key_collisions_preserve_archive_as_incomplete(tmp_path, second_key, nested):
    archive = source(tmp_path, 'unicode-key-collision')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'video')
    collision = {'\ud800': 'first', second_key: 'second'}
    metadata = {'usage': collision} if nested else collision
    original = json.dumps({'status': 'ready', 'video': f'/media/{JOB}/video.mp4', 'events': [], **metadata}).encode('utf-8')
    (folder / 'timeline.json').write_bytes(original)
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert row['status'] == 'incomplete'
    with pytest.raises(ValueError, match='keys collide'):
        library.timeline(JOB, row['version'])
    assert library.resource(JOB, row['version'], 'timeline.json').read_bytes() == original
    assert (folder / 'timeline.json').read_bytes() == original


@pytest.mark.parametrize('constant', ['NaN', 'Infinity', '-Infinity'])
def test_non_json_constants_do_not_break_history(tmp_path, constant):
    archive = source(tmp_path, 'constant')
    (archive / JOB / 'timeline.json').write_text('{"status":"ready","unused":' + constant + '}')
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    assert library.history()[0]['status'] == 'incomplete'


def test_imported_metadata_is_bounded_and_warning_shapes_are_safe(tmp_path):
    archive = source(tmp_path, 'metadata')
    folder = archive / JOB
    (folder / 'video.mp4').write_bytes(b'video')
    data = {'status': 'ready', 'video': f'/media/{JOB}/video.mp4', 'events': [],
            'title': 'x' * 100000, 'usage': 'x' * 100000, 'warnings': {}, 'warning_keys': 42}
    (folder / 'timeline.json').write_text(json.dumps(data))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert row['status'] == 'ready' and len(row['title']) == 500 and row['usage'] is None
    timeline = library.timeline(JOB, row['version'])
    assert timeline['warnings'] == timeline['warning_keys'] == []


def test_application_library_reuses_instance_per_root(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from main_logic.watch_together import library as module
    from utils import config_manager
    monkeypatch.setattr(config_manager, 'get_config_manager', lambda: SimpleNamespace(app_docs_dir=tmp_path))
    assert module.application_library() is module.application_library()


def test_reserved_resource_names_round_trip_through_urls(tmp_path):
    from urllib.parse import unquote, urlsplit
    archive = source(tmp_path, 'reserved')
    folder = archive / JOB
    names = ['video#1%.mp4', 'reaction#1%23.wav']
    for name in names:
        (folder / name).write_bytes(b'media')
    data = {'status': 'ready', 'video': f'/media/{JOB}/{names[0]}',
            'events': [{'at': 0, 'duration': 1, 'audio': f'/media/{JOB}/{names[1]}'}]}
    (folder / 'timeline.json').write_text(json.dumps(data))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert row['status'] == 'ready'
    timeline = library.timeline(JOB, row['version'])
    for url, name in zip([timeline['video'], timeline['events'][0]['audio']], names):
        parsed = urlsplit(url)
        assert not parsed.fragment and not parsed.query
        assert unquote(parsed.path.rsplit('/', 1)[1]) == name
        assert library.resource(JOB, row['version'], name).read_bytes() == b'media'


@pytest.mark.parametrize('depth', [2, 40, 1500])
def test_deep_timeline_does_not_break_history(tmp_path, depth):
    archive = source(tmp_path, 'nested')
    path = archive / JOB / 'timeline.json'
    (archive / JOB / 'video.mp4').write_bytes(b'video')
    raw = f'{{"status":"ready","video":"/media/{JOB}/video.mp4","unused":' + '[' * depth + '0' + ']' * depth + '}'
    path.write_text(raw)
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    assert library.history()[0]['status'] == ('ready' if depth == 2 else 'incomplete')
    assert path.read_text() == raw


@pytest.mark.parametrize('role', ['video', 'audio'])
def test_image_reference_cannot_be_used_as_playable_media(tmp_path, role):
    archive = source(tmp_path, 'wrong-role')
    path = archive / JOB / 'timeline.json'
    data = json.loads(path.read_text())
    (archive / JOB / 'video.mp4').write_bytes(b'video')
    (archive / JOB / 'cover.jpg').write_bytes(b'image')
    data['video'] = f'/media/{JOB}/video.mp4'
    data['events'] = [{'at': 0, 'audio': f'/media/{JOB}/laugh.mp3', 'duration': 1}]
    if role == 'video':
        data['video'] = f'/media/{JOB}/cover.jpg'
    else:
        data['events'][0]['audio'] = f'/media/{JOB}/cover.jpg'
    path.write_text(json.dumps(data))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    assert library.history()[0]['status'] == 'incomplete'


def test_verify_counts_standalone_deduplicated_audio_and_detects_corruption(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'a.mp3').write_bytes(b'audio')
    (assets / 'b.mp3').write_bytes(b'audio')
    library = Library(tmp_path / 'data')
    imported = library.import_audio_assets(assets)
    report = library.verify()
    assert report['verified'] and report['unique_objects'] == 1
    assert report['audio_assets'] == 2
    (library.objects / imported[0]['sha256']).write_bytes(b'broken')
    assert set(library.verify()['failures']) == {'a.mp3', 'b.mp3'}


def test_concurrent_watch_updates_preserve_all_events(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    library = Library(tmp_path / 'data')
    library.import_sources([source(tmp_path, 'concurrent')])
    watch = library.start_watch(JOB, library.history()[0]['version'], 'cat')
    barrier = Barrier(8)

    def update(index):
        barrier.wait(timeout=10)
        library.record_watch(watch, index, {'type': 'progress', 'index': index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(40)))
    with library.connect() as db:
        events = json.loads(db.execute('SELECT events FROM watches WHERE id=?', (watch,)).fetchone()['events'])
    assert len(events) == 40
    assert {event['index'] for event in events} == set(range(40))


@pytest.mark.parametrize('events', [None, {}, 'invalid', 42, [None], ['invalid'], [42],
                                  [{}], [{'at': '1'}], [{'at': True}], [{'at': float('inf')}],
                                  [{'at': -1}], [{'at': 10**1000}],
                                  [{'at': 1, 'audio': 'clip', 'duration': 10**1000}],
                                  [{'at': 1, 'audio': False}], [{'at': 1, 'audio': 0}],
                                  [{'at': 1, 'audio': []}],
                                  [{'at': 1, 'audio': 'clip', 'duration': None}]])
def test_malformed_legacy_events_remain_readable_as_incomplete(tmp_path, events):
    archive = source(tmp_path, 'malformed')
    path = archive / JOB / 'timeline.json'
    data = json.loads(path.read_text())
    (archive / JOB / 'video.mp4').write_bytes(b'video')
    data['video'] = f'/media/{JOB}/video.mp4'
    data['events'] = events
    path.write_text(json.dumps(data))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert row['status'] == 'incomplete'
    assert library.timeline(JOB, row['version'])['status'] == 'incomplete'
    assert json.loads(library.resource(JOB, row['version'], 'timeline.json').read_text())['events'] == events


@pytest.mark.parametrize('missing', ['video.mp4', 'laugh.mp3', None])
def test_partial_ready_history_is_incomplete(tmp_path, missing):
    archive = source(tmp_path, 'partial')
    folder = archive / JOB
    timeline = json.loads((folder / 'timeline.json').read_text())
    timeline['video'] = f'/media/{JOB}/video.mp4'
    (folder / 'timeline.json').write_text(json.dumps(timeline))
    (folder / 'video.mp4').write_bytes(b'video')
    if missing:
        (folder / missing).unlink()
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    assert row['status'] == ('incomplete' if missing else 'ready')
    if not missing:
        library.resource(JOB, row['version'], 'video.mp4').unlink()
        assert library.history()[0]['status'] == 'incomplete'


@pytest.mark.parametrize("value", [None, [], 3, "text"])
def test_non_object_legacy_timeline_remains_incomplete(tmp_path, value):
    src = source(tmp_path, "legacy")
    (src / JOB / "timeline.json").write_text(json.dumps(value))
    library = Library(tmp_path / "data")
    library.import_sources([src])
    assert library.history()[0]["status"] == "incomplete"


def test_single_job_import_skips_old_jobs_and_report(tmp_path):
    src = source(tmp_path, "legacy")
    other = src / ("a" * 32)
    other.mkdir()
    (other / "timeline.json").write_text('{}')
    (src / "backup-manifest.json").write_text(json.dumps([
        {"path": file.relative_to(src).as_posix(), "bytes": file.stat().st_size,
         "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
        for file in src.glob('*/*') if file.is_file()
    ]))
    library = Library(tmp_path / "data")
    result = library.import_sources([src], only_job=JOB, write_report=False)
    assert result["jobs"] == 1
    assert result["report"] is None
    assert not list(library.root.glob('migration-*.json'))
    with library.connect() as connection:
        connection.execute('SELECT 1')
    import sqlite3
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute('SELECT 1')


def source(tmp_path, name, audio=b"original"):
    root = tmp_path / name
    folder = root / JOB
    folder.mkdir(parents=True)
    (folder / "laugh.mp3").write_bytes(audio)
    (folder / "timeline.json").write_text(json.dumps({
        "id": JOB, "status": "ready", "events": [{"at": 1, "duration": 1, "audio": f"/media/{JOB}/laugh.mp3"}],
        "usage": {"input_tokens": 24652, "output_tokens": 918, "total_tokens": 25570},
    }))
    return root


def test_import_preserves_versions_and_is_idempotent(tmp_path):
    archive = source(tmp_path, "archive")
    live = source(tmp_path, "live", b"new")
    library = Library(tmp_path / "data")
    first = library.import_sources([archive, live])
    assert first["jobs"] == 1 and first["versions"] == 2
    assert len(first["differences"]) == 1
    library.import_sources([archive, live])
    rows = library.history()
    assert len(rows) == 2
    assert {library.resource(JOB, row["version"], "laugh.mp3").read_bytes() for row in rows} == {b"original", b"new"}
    for row in rows:
        assert row["progress"] is None and row["last_watched"] is None and row["completed"] is None
        data = library.timeline(JOB, row["version"])
        assert data["usage"]["total_tokens"] == 25570
        assert data["events"][0]["audio"].startswith("/api/watch-together/media/")
        original = json.loads(library.resource(JOB, row["version"], "timeline.json").read_text())
        assert original["events"][0]["audio"].startswith("/media/")


def test_checksum_failure_and_resume_repairs_object(tmp_path):
    archive = source(tmp_path, "archive")
    library = Library(tmp_path / "data")
    checksum = hashlib.sha256(b"original").hexdigest()
    (archive / "backup-manifest.json").write_text(json.dumps([
        {"path": f"{JOB}\\laugh.mp3", "bytes": 8, "sha256": checksum.upper()}
    ]))
    library.import_sources([archive])
    (library.objects / checksum).write_bytes(b"corrupt")
    library.import_sources([archive])
    assert (library.objects / checksum).read_bytes() == b"original"
    (archive / JOB / "laugh.mp3").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        library.import_sources([archive])


def test_missing_manifest_file_rejected(tmp_path):
    archive = source(tmp_path, "archive")
    (archive / "backup-manifest.json").write_text(json.dumps([
        {"path": f"{JOB}/missing.mp3", "bytes": 1, "sha256": "0" * 64}
    ]))
    with pytest.raises(ValueError, match="missing files"):
        Library(tmp_path / "data").import_sources([archive])


def test_watch_history_pages_keep_all_records(tmp_path):
    library = Library(tmp_path / "data")
    with library.connect() as db:
        db.executemany("INSERT INTO watches(id,job,version,character) VALUES(?,?,?,?)",
                       [(f'{index:03}', JOB, 'v', 'cat') for index in range(125)])
    first = library.watches()
    second = library.watches(50, 50)
    third = library.watches(50, 100)
    assert [len(first), len(second), len(third)] == [50, 50, 25]
    assert len({row['id'] for row in first + second + third}) == 125
    assert len(library.watches(10000)) == 100
    assert library.watch_page(50, 75)['next_offset'] is None
    assert library.watch_page(50, 50)['next_offset'] == 100
    with library.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM watches').fetchone()[0] == 125


def test_analysis_history_validates_only_requested_page(tmp_path, monkeypatch):
    library = Library(tmp_path / 'data')
    with library.connect() as db:
        db.executemany('INSERT INTO versions(job,version,source,manifest) VALUES(?,?,?,?)',
                       [(f'{index:032x}', 'v', 'archive', '{}') for index in range(100)])
    visited = []
    def timeline(job, version):
        visited.append(job)
        return {'status': 'ready'}
    monkeypatch.setattr(library, 'timeline', timeline)
    first = library.history_page()
    assert len(visited) == 50
    assert first['next_offset'] == 50
    second = library.history_page(50, 50)
    assert second['next_offset'] is None
    assert len(set(visited)) == 100


def test_viewing_is_separate_and_completion_survives_exit(tmp_path):
    library = Library(tmp_path / "data")
    library.import_sources([source(tmp_path, "archive")])
    row = library.history()[0]
    watch = library.start_watch(JOB, row['version'], 'character')
    assert library.watches()[0]['last_watched'] is None
    library.record_watch(watch, 10, {'type':'seek'})
    assert library.watches()[0]['last_watched'] is None
    library.record_watch(watch, 42, {'type':'ended'})
    library.record_watch(watch, 42, {'type':'exit'})
    saved = library.watches()[0]
    assert 'events' not in saved
    with library.connect() as db:
        assert len(json.loads(db.execute('SELECT events FROM watches WHERE id=?', (watch,)).fetchone()['events'])) == 3
    assert saved['completed'] == 1 and saved['last_watched']
    assert library.history()[0]['last_watched'] is None


def test_oversized_cue_list_is_incomplete_without_returning_events(tmp_path):
    archive = source(tmp_path, 'many-cues')
    path = next(archive.rglob('timeline.json'))
    data = json.loads(path.read_text())
    data['events'] = [{'at': i} for i in range(1001)]
    path.write_text(json.dumps(data))
    library = Library(tmp_path / 'data')
    library.import_sources([archive])
    row = library.history()[0]
    loaded = library.timeline(row['job'], row['version'])
    assert loaded['status'] == 'incomplete'
    assert loaded['events'] == []
    assert len(json.loads(path.read_text())['events']) == 1001
