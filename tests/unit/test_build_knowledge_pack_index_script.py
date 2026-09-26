from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

from knowledge.pack_jobs import process_pack_jobs
from knowledge.packs import pack_payload as validated_pack_payload
from knowledge.packs import validate_pack
from knowledge.prebuilt_index import PREBUILT_DIMENSIONS, PREBUILT_MODEL_ID
from knowledge.service import KnowledgeService
from knowledge.subscriptions import canonical_pack_bytes, load_canonical_pack_artifact


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "build_knowledge_pack_index.py"
)
SPEC = importlib.util.spec_from_file_location("build_knowledge_pack_index", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _pack_payload():
    return {
        "schema_version": 1,
        "pack_id": "publisher-fixture",
        "material_type": "corpus",
        "source": {"name": "Fixture", "homepage": "", "license": "CC0"},
        "entries": [
            {
                "title": "Published fact",
                "terms": {"alias": [], "recognition": []},
                "tags": [],
                "summary": "A fact",
                "content": "The published fact has a grounded answer.",
            }
        ],
    }


class _EmbeddingService:
    async def request_load(self):
        return True

    def model_id(self):
        return PREBUILT_MODEL_ID

    def dim(self):
        return PREBUILT_DIMENSIONS

    async def embed_batch(self, texts):
        return [[1.0, *([0.0] * (PREBUILT_DIMENSIONS - 1))] for _ in texts]


def test_build_and_verify_prebuilt_sidecars(tmp_path, monkeypatch, capsys):
    pack_path = tmp_path / "fixture.neko-knowledge.json"
    pack_path.write_bytes(canonical_pack_bytes(_pack_payload()))
    released = []
    monkeypatch.setattr(MODULE, "get_local_embedding_service", _EmbeddingService)

    async def _release():
        released.append(True)

    monkeypatch.setattr(MODULE, "release_local_embedding_service", _release)

    assert MODULE.main([str(pack_path), "--output-dir", str(tmp_path)]) == 0
    built = json.loads(capsys.readouterr().out)
    manifest = Path(built["manifest"])
    vectors = Path(built["vectors"])
    assert manifest.is_file()
    assert vectors.is_file()
    assert released == [True]

    assert (
        MODULE.main(
            [
                str(pack_path),
                "--verify",
                "--manifest",
                str(manifest),
                "--vectors",
                str(vectors),
            ]
        )
        == 0
    )
    verified = json.loads(capsys.readouterr().out)
    assert verified["ok"] is True
    assert verified["chunk_count"] == 1


def test_builder_rejects_the_wrong_runtime_model(tmp_path, monkeypatch, capsys):
    pack_path = tmp_path / "fixture.neko-knowledge.json"
    pack_path.write_bytes(canonical_pack_bytes(_pack_payload()))
    service = _EmbeddingService()
    service.model_id = lambda: "another-model"
    monkeypatch.setattr(MODULE, "get_local_embedding_service", lambda: service)

    async def _release():
        return None

    monkeypatch.setattr(MODULE, "release_local_embedding_service", _release)

    assert MODULE.main([str(pack_path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["error_type"] == "RuntimeError"


def test_builder_output_is_validation_stable_and_staging_compatible(
    tmp_path,
    monkeypatch,
    capsys,
):
    payload = _pack_payload()
    payload["entries"][0]["content"] = "First paragraph. \n\nSecond paragraph."
    source_path = tmp_path / "source" / "fixture.neko-knowledge.json"
    source_path.parent.mkdir()
    source_raw = canonical_pack_bytes(payload)
    source_path.write_bytes(source_raw)
    output_dir = tmp_path / "release"
    from memory import local_embedding_provider

    monkeypatch.setattr(
        local_embedding_provider,
        "bind_process_local_embedding_provider",
        lambda: None,
    )
    capsys.readouterr()
    monkeypatch.setattr(MODULE, "get_local_embedding_service", _EmbeddingService)

    async def _release():
        return None

    monkeypatch.setattr(MODULE, "release_local_embedding_service", _release)

    assert MODULE.main([str(source_path), "--output-dir", str(output_dir)]) == 0
    built = json.loads(capsys.readouterr().out)
    published_path = Path(built["pack"])
    published_raw = published_path.read_bytes()
    published_pack = validate_pack(load_canonical_pack_artifact(published_raw))

    assert published_path == output_dir / source_path.name
    assert published_raw != source_raw
    assert canonical_pack_bytes(validated_pack_payload(published_pack)) == published_raw
    assert built["pack_sha256"] == hashlib.sha256(published_raw).hexdigest()
    assert built["pack_sha256"] != hashlib.sha256(source_raw).hexdigest()

    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    service = KnowledgeService.from_root(knowledge_root)
    service.stage_pack(
        published_pack,
        subscription={
            "provider": "plugin-market",
            "provider_package_id": "7",
            "remote_id": "knowledge/publisher-fixture",
            "version": "1.0.0",
            "channel": "stable",
            "artifact_sha256": built["pack_sha256"],
            "material_type": "corpus",
            "index_manifest_sha256": built["manifest_sha256"],
            "vectors_sha256": built["vectors_sha256"],
            "trust": "trusted_market",
        },
        index_manifest=Path(built["manifest"]).read_bytes(),
        vectors=Path(built["vectors"]).read_bytes(),
    )

    result = asyncio.run(
        process_pack_jobs(service, batch_size=4, ready_vector_chunks=0)
    )
    assert result["state"] == "ready_hybrid"
    assert service.list_packs()[0]["index_validation"] == "accepted"
