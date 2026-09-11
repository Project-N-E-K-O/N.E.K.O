import hashlib
import json

import pytest

from main_logic.watch_together.library import Library


JOB = "0b3d279153c34ddfa8b88175d18c2e6f"


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
        "id": JOB, "status": "ready", "events": [{"audio": f"/media/{JOB}/laugh.mp3"}],
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
