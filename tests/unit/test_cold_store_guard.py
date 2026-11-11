"""Static guards for cold storage (memory/cold_store.py).

Cold rows (dedup losers, retracted facts, archived group subjects, archived
reflections and persona entries) are kept on this disk only: nothing recalls,
searches or organizes them, nothing syncs their text, and the Steam cloud save
leaves them out. These checks keep new code from reaching them by accident:

1. only the files listed below name the cold directories or call the
   full-row readers (``read_full`` / ``restore``);
2. the cloud-save manifest never lists a cold directory.

The marker filters in the recall, retrieval and forge readers stay as a
second line of defence; tests/unit/test_cold_store_canary.py checks their
output.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Names of the cold directories and of the helpers that resolve them.
_DIRECTORY_NAMES = re.compile(
    r"(?<![A-Za-z0-9_])(?:fact_cold|reflection_archive|persona_archive|FACT_COLD_DIRNAME|COLD_DIRNAMES"
    r"|REFLECTION_ARCHIVE_DIRNAME|PERSONA_ARCHIVE_DIRNAME|_reflections_archive_dir|_persona_archive_dir)"
    r"(?![A-Za-z0-9_])"
)
# The calls that hand out full cold rows, text included.
_FULL_READS = re.compile(r"\bcold_store\s*\.\s*a?(?:read_full|restore)\b")

# Who may name a cold directory, and why.
DIRECTORY_ALLOWED = {
    "memory/cold_store.py": "the only reader of cold text",
    "memory/archive_shards.py": "the shard layout every cold kind shares",
    "memory/facts.py": "fact demotion writers and the cold restore entry (through cold_store)",
    "memory/reflection/persistence.py": "writes reflection archive shards; erasure reads their ids",
    "memory/reflection/evidence_flow.py": "event-sourced reflection archiving and the flat-file migration",
    "memory/persona/persistence.py": "resolves the persona archive directory",
    "memory/persona/facts.py": "event-sourced persona archiving",
    "memory/evidence_handlers.py": "event replay of archive writes and journal compaction",
    "memory/subject_archive.py": "group subject restore",
}
# Who may read full cold rows.
FULL_READ_ALLOWED = {
    "memory/cold_store.py": "defines them",
    "memory/facts.py": "privacy erasure and the restore entry",
}


def _python_sources():
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        top = relative.split("/", 1)[0]
        if top in {"tests", ".venv", "venv", "node_modules", "build", "dist"} or top.startswith("."):
            continue
        yield relative, path.read_text(encoding="utf-8", errors="replace")


def test_only_listed_files_name_cold_directories():
    found = {relative for relative, text in _python_sources() if _DIRECTORY_NAMES.search(text)}
    assert found - set(DIRECTORY_ALLOWED) == set(), (
        "a new file names a cold directory; cold rows are read through memory/cold_store.py "
        "(stubs via cold_store.status). If it really must, add it to DIRECTORY_ALLOWED with the reason."
    )


def test_only_listed_files_read_full_cold_rows():
    found = {relative for relative, text in _python_sources() if _FULL_READS.search(text)}
    assert found - set(FULL_READ_ALLOWED) == set(), (
        "full cold rows are for restore, export to a file and privacy erasure only"
    )


def test_the_guards_see_what_they_guard():
    """The patterns still match the real code, so the two checks above cannot pass vacuously."""
    sources = dict(_python_sources())
    assert _DIRECTORY_NAMES.search(sources["memory/cold_store.py"])
    assert _FULL_READS.search(sources["memory/facts.py"])
    assert not _DIRECTORY_NAMES.search("make_reflection_archive_handler(engine)")
    assert _DIRECTORY_NAMES.search("os.path.join(base, 'fact_cold')")
    assert _FULL_READS.search("rows = await cold_store.aread_full(path)")


def test_cloud_save_manifest_leaves_cold_storage_out():
    from memory import cold_store
    from utils.cloudsave_runtime import MANAGED_MEMORY_FILENAMES

    for name in MANAGED_MEMORY_FILENAMES:
        top = str(name).replace("\\", "/").split("/", 1)[0]
        assert top not in cold_store.COLD_DIRNAMES.values(), name
        assert not top.startswith(cold_store.FACT_COLD_DIRNAME), name
