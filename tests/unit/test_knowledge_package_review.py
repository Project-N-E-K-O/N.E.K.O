"""Regression fixtures for portable coverage, registry admission and readiness."""

import hashlib
import json
import random
import struct

import pytest

from knowledge.models import KnowledgeSource, parse_pack
from knowledge.registry import KnowledgeRegistryError, PackRecord, load_registry
from knowledge.store import KnowledgeStore
from tests.unit.test_knowledge_archive_v2 import make_archive, specification
from utils.knowledge_package import PackageError, _source_is_covered, canonical_json, validate_package


pytestmark = pytest.mark.unit_fast


def _replace_entries(manifest, members, entries, bodies):
    """Write independent wire rows and recompute every declared member digest."""
    pack = json.loads(members["pack.json"])
    pack["entries"] = entries
    rows = []
    for entry, excerpts in zip(entries, bodies):
        for index, body in enumerate(excerpts):
            text = entry["title"] + "\n" + body
            rows.append({"entry_title": entry["title"], "chunk_index": index,
                         "embedding_text": text, "text_hash": hashlib.sha256(text.encode()).hexdigest()})
    members["pack.json"] = canonical_json(pack)
    members["chunks.jsonl"] = b"".join(canonical_json(row) + b"\n" for row in rows)
    members["vectors.f32"] = struct.pack("<64f", 1.0, *([0.0] * 63)) * len(rows)
    manifest["counts"] = {"documents": len(entries), "chunks": len(rows)}
    for name, raw in members.items():
        manifest["files"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def test_repeated_windows_keep_a_valid_nongreedy_coverage_path(tmp_path):
    """The furthest second match loses the real final window of repeated text."""
    content = "a" * 2499 + "b"

    def mutate(manifest, members):
        _replace_entries(manifest, members, [{"title": "Example", "content": content}],
                         [[content[start:start + 914] for start in (0, 794, 1588)]])

    checked = validate_package(make_archive(tmp_path / "repeated.zip", mutate=mutate))
    assert len(checked.chunks) == 3


def test_candidate_coverage_matches_exhaustive_paths_including_short_overlaps():
    """Bounded bitset propagation retains precisely the legal cursor paths."""
    randomizer = random.Random(3765)
    for _ in range(150):
        content = "".join(randomizer.choice("aab") for _ in range(randomizer.randint(1, 180)))
        bodies = []
        for _ in range(randomizer.randint(1, 5)):
            start = randomizer.randrange(len(content))
            bodies.append(content[start:start + randomizer.randint(1, 150)])
        if randomizer.random() < 0.1:
            bodies.append("foreign")
        cursors = {0}
        for body in bodies:
            cursors = {max(cursor, start + len(body)) for cursor in cursors
                       for start in range(max(0, cursor - 120), cursor + 1)
                       if content.startswith(body, start)}
        assert _source_is_covered(content, bodies) == (len(content) in cursors)
    assert _source_is_covered("abc", ["abc", "b"])
    assert not _source_is_covered("abc", ["ab", "c", "foreign"])


@pytest.mark.parametrize("titles", [("Title", "title"), ("Title", "Ｔｉｔｌｅ"),
                                    ("A  title", "A\ttitle"), ("Straße", "STRASSE")])
def test_portable_titles_use_the_runtime_identity_key(tmp_path, titles):
    """Market acceptance must not admit titles the local pack parser rejects."""
    def mutate(manifest, members):
        _replace_entries(manifest, members, [{"title": title, "content": "body"} for title in titles],
                         [["body"], ["body"]])

    with pytest.raises(PackageError, match="duplicate_title"):
        validate_package(make_archive(tmp_path / "duplicate.zip", mutate=mutate))


@pytest.mark.parametrize("origin", [[], {}, None, 3, True])
def test_invalid_registry_origin_reports_a_domain_error(tmp_path, origin):
    """Unhashable metadata must use the normal damaged-registry boundary."""
    record = PackRecord("imp.example", "a" * 64, KnowledgeSource("Example", "", ""),
                        "knowledge", 1, 1).to_json()
    record["origin"] = origin
    (tmp_path / "registry.json").write_text(json.dumps({"schema_version": 1, "enabled": True,
                                                       "packs": {"imp.example": record}}))
    with pytest.raises(KnowledgeRegistryError):
        load_registry(tmp_path)


@pytest.mark.parametrize("kind,ready", [("old", 0), ("unbound", 0), ("current", 1)])
def test_chunk_readiness_requires_the_current_resource_specification(tmp_path, kind, ready):
    """The model profile name alone cannot establish current vector readiness."""
    payload = {"schema_version": 1, "pack_id": "imp.example", "material_type": "knowledge",
               "source": {"name": "Example", "homepage": "", "license": ""},
               "entries": [{"title": "Example", "content": "body"}]}
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    store.replace_pack(parse_pack(payload), pack_sha256="a" * 64)
    current = specification()
    previous = {**current, "weights_sha256": {key: "b" * 64 for key in current["weights_sha256"]}}
    row, = store.pending_chunks(model_id=current["model_id"], pack_ids=["imp.example"], limit=1)
    store.store_vectors(model_id=current["model_id"], rows=[(row[0], row[1], struct.pack("<64f", 1.0, *([0.0] * 63)))],
                        specification=None if kind == "unbound" else current if kind == "current" else previous)
    assert store.chunk_stats(current["model_id"], specification=current)["imp.example"] == {
        "total": 1, "ready": ready, "failed": 0,
    }
    assert store.chunk_stats(current["model_id"])["imp.example"]["ready"] == 1


def test_chunk_pack_ids_maps_only_the_requested_rows(tmp_path):
    """A failed replan can be scoped to the pack owning the oversized input."""
    store = KnowledgeStore(tmp_path / "knowledge.db")
    store.initialize()
    rows = {}
    for pack_id in ("imp.first", "imp.second"):
        payload = {"schema_version": 1, "pack_id": pack_id, "material_type": "knowledge",
                   "source": {"name": "Example", "homepage": "", "license": ""},
                   "entries": [{"title": "Example", "content": "body"}]}
        store.replace_pack(parse_pack(payload), pack_sha256="a" * 64)
        row, = store.pending_chunks(model_id="model", pack_ids=[pack_id], limit=1)
        rows[pack_id] = row[0]
    assert store.chunk_pack_ids([]) == {}
    assert store.chunk_pack_ids([rows["imp.second"], rows["imp.second"], 99999]) == {
        rows["imp.second"]: "imp.second",
    }
    assert store.chunk_pack_ids(list(rows.values())) == {value: key for key, value in rows.items()}
