"""Build or verify a trusted-market knowledge vector cache."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge.chunking import derive_knowledge_chunks
from knowledge.prebuilt_index import (
    PREBUILT_DIMENSIONS,
    PREBUILT_MODEL_ID,
    build_prebuilt_index_artifacts,
    validate_prebuilt_index,
)
from knowledge.packs import pack_payload, validate_pack
from knowledge.subscriptions import canonical_pack_bytes, load_canonical_pack_artifact
from utils.file_utils import atomic_write_bytes
from utils.local_embedding_runtime import (
    get_local_embedding_service,
    release_local_embedding_service,
)


MICROBATCH_SIZE = 4


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or verify the optional cache for a canonical knowledge pack.",
    )
    parser.add_argument(
        "pack", type=Path, help="canonical .neko-knowledge.json artifact"
    )
    parser.add_argument(
        "--output-dir", type=Path, help="directory for generated sidecars"
    )
    parser.add_argument(
        "--verify", action="store_true", help="verify existing sidecars"
    )
    parser.add_argument("--manifest", type=Path, help="manifest path for --verify")
    parser.add_argument("--vectors", type=Path, help="vector path for --verify")
    return parser


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sidecar_paths(pack_path: Path, output_dir: Path) -> tuple[Path, Path]:
    suffix = ".neko-knowledge.json"
    name = pack_path.name
    stem = name[: -len(suffix)] if name.endswith(suffix) else pack_path.stem
    return (
        output_dir / f"{stem}.neko-knowledge.index.json",
        output_dir / f"{stem}.neko-knowledge.vectors.f16",
    )


def _validated_pack_artifact(pack_raw: bytes):
    pack = validate_pack(load_canonical_pack_artifact(pack_raw))
    normalized_raw = canonical_pack_bytes(pack_payload(pack))
    chunks = tuple(
        chunk
        for entry in pack.entries
        for chunk in derive_knowledge_chunks(
            entry,
            entry_key=f"{entry.source_tag}:{entry.title}",
        )
    )
    return normalized_raw, chunks


async def _build(pack_path: Path, output_dir: Path) -> dict[str, object]:
    from memory.local_embedding_provider import bind_process_local_embedding_provider

    bind_process_local_embedding_provider()
    pack_raw, chunks = _validated_pack_artifact(pack_path.read_bytes())
    service = get_local_embedding_service()
    try:
        if not await service.request_load():
            raise RuntimeError("local embedding model is unavailable")
        if (
            service.model_id() != PREBUILT_MODEL_ID
            or int(service.dim() or 0) != PREBUILT_DIMENSIONS
        ):
            raise RuntimeError(
                "local embedding model does not match the publication contract"
            )
        records: list[dict[str, object]] = []
        for offset in range(0, len(chunks), MICROBATCH_SIZE):
            batch = chunks[offset : offset + MICROBATCH_SIZE]
            vectors = await service.embed_batch(
                [chunk.embedding_text for chunk in batch]
            )
            if len(vectors) != len(batch):
                raise RuntimeError(
                    "embedding batch returned an unexpected result count"
                )
            for chunk, vector in zip(batch, vectors, strict=True):
                array = np.asarray(vector, dtype=np.float32)
                if array.shape != (PREBUILT_DIMENSIONS,) or not bool(
                    np.isfinite(array).all()
                ):
                    raise RuntimeError("embedding batch returned an invalid vector")
                records.append(
                    {
                        "chunk_id": chunk.chunk_id,
                        "content_hash": chunk.content_hash,
                        "model_id": PREBUILT_MODEL_ID,
                        "dimensions": PREBUILT_DIMENSIONS,
                        "embedding": array.astype("<f2", copy=False).tobytes(),
                    }
                )
        artifacts = build_prebuilt_index_artifacts(pack_raw, records)
        output_dir.mkdir(parents=True, exist_ok=True)
        published_pack_path = output_dir / pack_path.name
        manifest_path, vectors_path = _sidecar_paths(pack_path, output_dir)
        atomic_write_bytes(published_pack_path, pack_raw)
        atomic_write_bytes(manifest_path, artifacts.manifest)
        atomic_write_bytes(vectors_path, artifacts.vectors)
        return {
            "ok": True,
            "action": "build",
            "pack": str(published_pack_path.resolve()),
            "manifest": str(manifest_path.resolve()),
            "vectors": str(vectors_path.resolve()),
            "chunk_count": len(chunks),
            "pack_sha256": artifacts.pack_sha256,
            "manifest_sha256": artifacts.manifest_sha256,
            "vectors_sha256": artifacts.vectors_sha256,
        }
    finally:
        await release_local_embedding_service()


def _verify(
    pack_path: Path, manifest_path: Path, vectors_path: Path
) -> dict[str, object]:
    pack_raw = pack_path.read_bytes()
    manifest_raw = manifest_path.read_bytes()
    vectors_raw = vectors_path.read_bytes()
    validated = validate_prebuilt_index(
        pack_raw,
        manifest_raw,
        vectors_raw,
        expected_pack_sha256=_digest(pack_raw),
        expected_manifest_sha256=_digest(manifest_raw),
        expected_vectors_sha256=_digest(vectors_raw),
    )
    return {
        "ok": True,
        "action": "verify",
        "chunk_count": len(validated.chunks),
        "pack_sha256": validated.pack_sha256,
        "manifest_sha256": validated.manifest_sha256,
        "vectors_sha256": validated.vectors_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.verify:
            manifest_path, vectors_path = _sidecar_paths(
                args.pack,
                args.output_dir or args.pack.parent,
            )
            result = _verify(
                args.pack,
                args.manifest or manifest_path,
                args.vectors or vectors_path,
            )
        else:
            result = asyncio.run(_build(args.pack, args.output_dir or args.pack.parent))
    except (OSError, RuntimeError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "reason": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
