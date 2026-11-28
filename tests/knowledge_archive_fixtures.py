"""Independent v2 wire samples and certified synthetic vectors for knowledge tests.

make_archive writes the public wire format directly, keeping validator tests
independent of the production exporter. make_chunk_plan_archive exercises the
real exporter with a legacy chunk layout for reuse/recovery tests. No real
model is loaded; callers own their isolated service and temporary files.
"""

import hashlib
import math
import struct
import zipfile
from pathlib import Path

from services.knowledge.archive import export_archive, read_archive
from services.knowledge.chunking import derive_chunks
from services.knowledge.models import parse_pack
from utils import knowledge_package as package
from utils.embedding_contract import EmbedDeferred


def specification(dimension=64, quantization="int8"):
    """Use a complete synthetic identity, never loading a native encoder."""
    return {"model_id": f"local-text-retrieval-v1-{dimension}d-{quantization}-mlen1024",
            "profile": "local-text-retrieval-v1", "dimension": dimension,
            "quantization": quantization, "max_length": 1024,
            "processing_version": "neko-onnx-last-token-truncate-l2-v1",
            "weights_sha256": {key: "a" * 64 for key in ("model", "external_data", "tokenizer")}}


def make_chunk_plan_archive(tmp_path, *, reverse=False):
    """Export the legacy 97-row plan shared by reuse and backfill recovery tests.

    Keep the original chunk inputs and optional reversed row order; only the
    synthetic resource identity is shared with the other archive fixtures.
    """
    spec = specification()
    payload = {
        "schema_version": 1, "pack_id": "archive-original-inputs", "material_type": "knowledge",
        "source": {"name": "Original inputs", "homepage": "", "license": "CC0"},
        "entries": [
            {"title": "Reference", "content": "\n\n".join(f"{index:03d} " + "x" * 696 for index in range(100))},
            {"title": "Short", "content": "The short entry also keeps its own vector."},
        ],
    }
    pack = parse_pack(payload)
    blob = struct.pack("<64f", 1.0, *([0.0] * 63))
    rows = [
        {"entry_title": entry.title, "chunk_index": chunk.chunk_index, "text_hash": chunk.text_hash,
         "embedding_text": chunk.embed_text, "vector": blob}
        for entry in pack.entries for chunk in derive_chunks(entry)
    ]
    assert len(rows) == 97  # The long entry used the legacy 96-chunk fallback.
    if reverse:
        rows.reverse()  # Valid archive row order need not match chunk_index order.
    artifact = export_archive(tmp_path / "exports", payload, specification=spec, vectors=rows)
    path = tmp_path / "exports" / (artifact["sha256"] + ".neko-knowledge.zip")
    checked = read_archive(path, expected_spec=spec)
    assert len(checked["seed_vectors"]) == 97
    return pack, path, checked, spec


def make_archive(path: Path, *, mutate=None, zip64=False):
    """Write the public data contract directly so tests do not mirror its builder."""
    text = "Example\nA violet lighthouse marks the coast."
    pack = {"schema_version": 1, "pack_id": "imp.example", "material_type": "knowledge",
            "source": {"name": "Example", "homepage": "", "license": "CC0"},
            "entries": [{"title": "Example", "summary": "", "terms": {"alias": [], "recognition": []}, "tags": [],
                         "content": "A violet lighthouse marks the coast."}]}
    row = {"entry_title": "Example", "chunk_index": 0, "embedding_text": text,
           "text_hash": hashlib.sha256(text.encode()).hexdigest()}
    vector = [1 / math.sqrt(64)] * 64
    members = {"pack.json": package.canonical_json(pack),
               "chunks.jsonl": package.canonical_json(row) + b"\n",
               "vectors.f32": struct.pack("<64f", *vector)}
    manifest = {"format": "neko.knowledge-package", "format_version": 2,
                "package_id": "imp.example", "name": "Example", "version": "1.0.0",
                "source": {"author": "Example", "license": "CC0", "url": ""},
                "chunking_version": "neko-pack-chunks-v1", "embedding_spec": specification(),
                "counts": {"documents": 1, "chunks": 1},
                "files": {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                          for name, raw in members.items()}}
    if mutate:
        mutate(manifest, members)
    members["manifest.json"] = package.canonical_json(manifest)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            if zip64:
                with archive.open(name, "w", force_zip64=True) as output:
                    output.write(raw)
            else:
                archive.writestr(name, raw)
    return path


class _PackageEmbedder:
    """Expose a certified resource identity without loading a native model."""

    def state(self):
        return "ready"

    def model_id(self):
        return self.spec()["model_id"]

    def spec(self):
        return specification()

    async def embed(self, text):
        raise AssertionError("install/export must not compute vectors")

    async def embed_batch(self, texts):
        raise AssertionError("install/export must not compute vectors")

    async def embed_documents(self, texts):
        raise EmbedDeferred(retry_after_ms=1)


def _prepare_export_vectors(service, pack_id):
    """Seed checked synthetic vectors so export tests never load an encoder."""
    service.embedder = _PackageEmbedder()
    spec = service.embedder.spec()
    pending = service._store.pending_chunks(model_id=spec["model_id"], pack_ids=[pack_id], limit=10000)
    vector = struct.pack("<64f", 1.0, *([0.0] * 63))
    stored, failed = service._store.store_vectors(
        model_id=spec["model_id"], rows=[(chunk_id, text_hash, vector) for chunk_id, text_hash, _ in pending],
        specification=spec,
    )
    assert stored == len(pending) and failed == 0
