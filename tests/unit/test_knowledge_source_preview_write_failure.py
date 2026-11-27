"""Failed previews roll back only their own unreturned upload files."""

import logging
from pathlib import Path

import pytest

from services.knowledge.importing import sources


pytestmark = pytest.mark.unit


class PreviewInterrupted(BaseException):
    """Exercise interruption cleanup without interrupting the test runner."""


@pytest.mark.parametrize("pack_id,failure_type,published", [
    ("", OSError, False),
    ("existing-pack", OSError, False),
    ("", PreviewInterrupted, False),
    ("", PreviewInterrupted, True),
])
def test_metadata_failure_removes_only_the_failed_attempt(tmp_path, monkeypatch, pack_id, failure_type, published):
    store = sources.SourceStore(tmp_path)
    kept = store.preview("kept.txt", b"An existing successful preview.", pack_id="kept-pack")
    store.retain_preview(kept["preview_id"], kept["sha256"])
    if pack_id:
        # Even empty directories belonging to an existing target must survive.
        (store.directory(pack_id) / "previews").mkdir(parents=True)
    files_before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    dirs_before = {p.relative_to(store.root) for p in store.root.rglob("*") if p.is_dir()}
    write = sources.atomic_write_bytes
    failure = failure_type("Synthetic metadata persistence failure")
    failed = []

    def fail_metadata(path, raw):
        if Path(path).suffix == ".json":
            failed.append(Path(path))
            if published:
                # An interruption can arrive after the atomic rename.
                write(path, raw)
            raise failure
        write(path, raw)

    monkeypatch.setattr(sources, "atomic_write_bytes", fail_metadata)
    with pytest.raises(failure_type) as caught:
        store.preview("private.txt", b"Private rejected upload.", pack_id=pack_id)
    assert caught.value is failure and len(failed) == 1
    assert not failed[0].with_suffix(".original").exists()
    assert not failed[0].exists()
    assert {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob("*") if p.is_file()} == files_before
    assert {p.relative_to(store.root) for p in store.root.rglob("*") if p.is_dir()} == dirs_before


def test_original_write_interruption_also_removes_its_published_file(tmp_path, monkeypatch):
    store = sources.SourceStore(tmp_path)
    write = sources.atomic_write_bytes
    failure = PreviewInterrupted("Synthetic post-rename interruption")

    def interrupt_original(path, raw):
        write(path, raw)
        raise failure

    monkeypatch.setattr(sources, "atomic_write_bytes", interrupt_original)
    with pytest.raises(PreviewInterrupted) as caught:
        store.preview("private.txt", b"Private interrupted upload.")
    assert caught.value is failure
    assert list(store.root.iterdir()) == []


def test_cleanup_failure_keeps_the_original_error_and_logs_no_private_data(tmp_path, monkeypatch):
    store = sources.SourceStore(tmp_path)
    write = sources.atomic_write_bytes
    unlink = Path.unlink
    failure = OSError("Synthetic private metadata error")
    warnings = []
    originals = []

    def fail_metadata(path, raw):
        if Path(path).suffix == ".json":
            raise failure
        write(path, raw)
        originals.append(Path(path))

    def fail_cleanup(path, *args, **kwargs):
        if path in originals:
            raise PermissionError("Private cleanup path and rejected upload")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(sources, "atomic_write_bytes", fail_metadata)
    monkeypatch.setattr(Path, "unlink", fail_cleanup)
    monkeypatch.setattr(logging.getLogger("N.E.K.O.Knowledge"), "warning",
                        lambda message, *args, **kwargs: warnings.append((message % args, kwargs)))
    with pytest.raises(OSError) as caught:
        store.preview("secret-filename.txt", b"Sensitive rejected body.")
    assert caught.value is failure
    assert len(originals) == 1 and originals[0].read_bytes() == b"Sensitive rejected body."
    assert len(warnings) == 1 and "PermissionError" in warnings[0][0]
    assert not warnings[0][1].get("exc_info")
    for private in ("secret-filename.txt", "Sensitive rejected body.", "Private cleanup path", str(originals[0])):
        assert private not in warnings[0][0]
