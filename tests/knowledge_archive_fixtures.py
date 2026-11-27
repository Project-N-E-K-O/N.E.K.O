"""Independent v2 wire samples and certified synthetic vectors for knowledge tests.

The archive factory writes the public wire format directly, keeping validator
tests independent of the production exporter. No real model is loaded; tests
explicitly install synthetic vectors into their own isolated service.
"""

import hashlib
import math
import struct
import zipfile
from pathlib import Path

from utils import knowledge_package as package
from utils.embedding_contract import EmbedDeferred


def specification(dimension=64, quantization="int8"):
    """Use a complete synthetic identity, never loading a native encoder."""
    return {"model_id": f"local-text-retrieval-v1-{dimension}d-{quantization}-mlen1024",
            "profile": "local-text-retrieval-v1", "dimension": dimension,
            "quantization": quantization, "max_length": 1024,
            "processing_version": "neko-onnx-last-token-truncate-l2-v1",
            "weights_sha256": {key: "a" * 64 for key in ("model", "external_data", "tokenizer")}}


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
