"""Verify portable unified packs and reject malicious archives without inference."""

import hashlib
import json
import math
import stat
import struct
import zipfile
from datetime import datetime

import pytest

# Shared factories are not collected tests and never own global test state.
from tests.knowledge_archive_fixtures import make_archive, specification

from utils import knowledge_package as package

pytestmark = pytest.mark.unit_fast






def test_unified_archive_contains_a_v1_pack_and_streamed_vectors(tmp_path):
    checked = package.validate_package(make_archive(tmp_path / "complete.zip"))
    assert checked.manifest["format_version"] == 2
    assert checked.pack["schema_version"] == 1
    assert checked.pack["pack_id"] == "imp.example"
    assert len(checked.chunks) == 1
    assert checked.metadata()["package_id"] == "imp.example"
    assert checked.size == checked.path.stat().st_size


def test_zip64_local_header_is_refused_even_with_a_small_central_directory(tmp_path):
    with pytest.raises(package.PackageError):
        package.validate_package(make_archive(tmp_path / "zip64.zip", zip64=True))


@pytest.mark.parametrize("damage", ["zero", "nan", "hash", "foreign_title", "duplicate", "foreign_input", "missing_tail"])
def test_invalid_vectors_and_chunk_bindings_fail_closed(tmp_path, damage):
    def mutate(manifest, members):
        if damage in {"zero", "nan"}:
            members["vectors.f32"] = struct.pack("<64f", *([0.0] * 64 if damage == "zero" else [float("nan")] * 64))
        elif damage == "hash":
            row = json.loads(members["chunks.jsonl"])
            row["text_hash"] = "b" * 64
            members["chunks.jsonl"] = package.canonical_json(row) + b"\n"
        elif damage == "foreign_title":
            row = json.loads(members["chunks.jsonl"])
            row["entry_title"] = "not in pack"
            members["chunks.jsonl"] = package.canonical_json(row) + b"\n"
        elif damage in {"foreign_input", "missing_tail"}:
            row = json.loads(members["chunks.jsonl"])
            row["embedding_text"] = "Example\n" + ("unrelated instructions" if damage == "foreign_input" else "A violet lighthouse")
            row["text_hash"] = hashlib.sha256(row["embedding_text"].encode()).hexdigest()
            members["chunks.jsonl"] = package.canonical_json(row) + b"\n"
        else:
            members["chunks.jsonl"] *= 2
            members["vectors.f32"] *= 2
            manifest["counts"]["chunks"] = 2
        for name, raw in members.items():
            manifest["files"][name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    with pytest.raises(package.PackageError):
        package.validate_package(make_archive(tmp_path / "invalid.zip", mutate=mutate))


@pytest.mark.parametrize("name", ["../pack.json", "pack.json\x00hidden"])
def test_archive_members_do_not_accept_path_or_nul_aliases(tmp_path, name):
    original = make_archive(tmp_path / "base.zip")
    with zipfile.ZipFile(original) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    members[name] = members.pop("pack.json")
    target = tmp_path / "aliased.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for key, value in members.items():
            archive.writestr(key, value)
    if "\x00" in name:
        # ZipFile's writer strips NUL suffixes; put the forbidden byte on the
        # wire so the reader sees the malicious original member name.
        target.write_bytes(target.read_bytes().replace(b"pack.json", b"pack\x00json"))
    with pytest.raises(package.PackageError):
        package.validate_package(target)


def test_symbolic_link_member_is_not_a_data_file(tmp_path):
    original = make_archive(tmp_path / "base.zip")
    with zipfile.ZipFile(original) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    target = tmp_path / "link.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, raw in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, raw)
    with pytest.raises(package.PackageError):
        package.validate_package(target)


@pytest.mark.parametrize("target_dim,quantization,seeds", [(32, "int8", 1), (64, "int8", 1), (128, "int8", 0), (64, "fp32", 0)])
def test_archive_installs_only_real_compatible_prefixes(tmp_path, target_dim, quantization, seeds):
    """Low-memory consumers borrow a prefix; incompatible widths never get zeros."""
    from services.knowledge.archive import read_archive

    source = make_archive(tmp_path / "source.zip")
    result = read_archive(source, expected_spec=specification(target_dim, quantization))
    assert result["pack"]["pack_id"] == "imp.example"
    assert len(result["seed_vectors"]) == seeds
    if seeds:
        row = result["seed_vectors"][0]
        vector = struct.unpack(f"<{target_dim}f", row["vector"])
        assert math.hypot(*vector) == pytest.approx(1.0)
        assert row["specification"] == specification(target_dim, quantization)
    assert read_archive(source)["seed_vectors"] == []


def test_export_reuses_existing_packed_vectors_in_one_complete_archive(tmp_path):
    """The only export preserves text and vectors in the validated v2 contract."""
    from services.knowledge.archive import export_archive

    source = make_archive(tmp_path / "source.zip")
    checked = package.validate_package(source)
    with zipfile.ZipFile(source) as archive:
        rows = [{**checked.chunks[0], "vector": archive.read("vectors.f32")}]
    output = tmp_path / "artifacts"
    created_at = "2026-11-19T07:30:42Z"
    v2 = export_archive(output, checked.pack, specification=specification(), vectors=rows, version="2.0.0", created_at=created_at)
    artifact = output / (v2["sha256"] + package.PACKAGE_SUFFIX)
    assert package.validate_package(artifact).pack == checked.pack
    assert v2["format_version"] == 2
    assert v2["name"] == "Example"
    assert v2["filename"].endswith(package.PACKAGE_SUFFIX)
    local_time = datetime.fromisoformat(created_at.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d_%H-%M-%S")
    assert v2["filename"] == f"Example_{local_time}{package.PACKAGE_SUFFIX}"
    assert checked.pack["pack_id"] not in v2["filename"]
    assert not list(output.glob("*.neko-knowledge.json"))
    with zipfile.ZipFile(artifact) as archive:
        assert archive.read("vectors.f32") == rows[0]["vector"]
    assert not artifact.read_bytes().startswith(b"{")
    assert v2["bytes"] == artifact.stat().st_size


@pytest.mark.parametrize("name", ["My notes", "日本語の資料", '../CON.txt:*?\r\n', "CON.txt", "資料" * 200, "..."])
def test_export_filename_is_portable_without_losing_unicode(name):
    """Readable download names cannot become paths or Windows device names."""
    from services.knowledge.archive import export_filename

    filename = export_filename(name, created_at="2026-11-19T07:30:42Z")
    assert filename.endswith(package.PACKAGE_SUFFIX)
    assert len(filename.encode("utf-8")) <= 240
    assert not any(char in filename for char in '<>:"/\\|?*\r\n')
    assert not filename.startswith(".") and not filename.endswith((".", " "))
    assert filename.split(".", 1)[0].upper() not in {"CON", "PRN", "AUX", "NUL"}
    if name == "日本語の資料":
        assert filename.startswith(name + "_")


def test_incomplete_vectors_do_not_publish_a_v2_archive(tmp_path):
    from services.knowledge.archive import export_archive
    from services.knowledge.models import KnowledgePackError

    checked = package.validate_package(make_archive(tmp_path / "source.zip"))
    output = tmp_path / "artifacts"
    with pytest.raises(KnowledgePackError, match="vectors_not_ready"):
        export_archive(output, checked.pack, specification=specification(), vectors=[])
    assert not list(output.glob("*.zip"))


def test_repeated_source_text_accepts_real_token_fitted_chunks(tmp_path):
    """A repeated excerpt may also occur inside the preceding overlap window."""
    from services.knowledge.archive import export_archive
    from services.knowledge.chunking import derive_chunks
    from services.knowledge.models import parse_pack

    checked = package.validate_package(make_archive(tmp_path / "source.zip"))
    payload = checked.pack
    payload["entries"][0]["content"] = "A lighthouse marks the coast. " * 90
    entry = parse_pack(payload).entries[0]
    chunks = derive_chunks(entry, fit=lambda text: len(text) <= 770)
    rows = [{"entry_title": entry.title, "chunk_index": chunk.chunk_index,
             "text_hash": chunk.text_hash, "embedding_text": chunk.embed_text,
             "vector": struct.pack("<64f", 1.0, *([0.0] * 63))} for chunk in chunks]
    result = export_archive(tmp_path / "artifacts", payload, specification=specification(), vectors=rows)
    target = tmp_path / "artifacts" / (result["sha256"] + package.PACKAGE_SUFFIX)
    assert len(package.validate_package(target).chunks) == len(chunks)


def test_export_refuses_publication_when_final_task_gate_is_cancelled(tmp_path):
    """The owner may accept cancellation while staged bytes are being synced."""
    from services.knowledge.archive import export_archive

    checked = package.validate_package(make_archive(tmp_path / "source.zip"))
    with zipfile.ZipFile(tmp_path / "source.zip") as archive:
        rows = [{**checked.chunks[0], "vector": archive.read("vectors.f32")}]
    output = tmp_path / "artifacts"
    with pytest.raises(InterruptedError, match="cancelled"):
        export_archive(output, checked.pack, specification=specification(), vectors=rows, commit_gate=lambda: False)
    assert not list(output.iterdir())


def test_valid_deflate_variants_survive_independent_expansion_checks(tmp_path):
    """Bounded raw-DEFLATE validation must still accept normal producer streams."""
    original = make_archive(tmp_path / "base.zip")
    with zipfile.ZipFile(original) as archive:
        members = {item.filename: archive.read(item) for item in archive.infolist()}
    for level in (0, 1, 6, 9):
        target = tmp_path / f"compression-{level}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=level) as archive:
            for name, raw in members.items():
                archive.writestr(name, raw)
        assert package.validate_package(target).pack["pack_id"] == "imp.example"
