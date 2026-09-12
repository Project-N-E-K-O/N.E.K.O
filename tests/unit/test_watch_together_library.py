import hashlib
import json

import pytest

from main_logic.watch_together.library import Library


JOB = "0b3d279153c34ddfa8b88175d18c2e6f"


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
