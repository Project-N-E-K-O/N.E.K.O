"""Explicitly export genuine builder outputs for the adjacent Market contract test."""

import json
import os
from pathlib import Path

import pytest

from services.knowledge_library.indexing import make_chunks
from services.knowledge_library.models import digest
from services.knowledge_library.package_build import build_package
from services.knowledge_library.package_validate import CHUNKING_VERSION
from tests.unit.knowledge_library.test_packages import Encoder

pytestmark = [pytest.mark.unit, pytest.mark.skipif(not os.getenv("NEKO_BUILDER_OUTPUT_ROOT"),
                                                reason="requires an explicit temporary cross-repository output root")]


async def test_export_builder_versions_for_market():
    """Build all three artifacts; the consumer must not relabel or repack fixtures."""
    directory = Path(os.environ["NEKO_BUILDER_OUTPUT_ROOT"]).resolve()
    results = {}
    for name, version, content in (
        ("v1", "1", "Cross repository version one. 跨仓库知识成品。"),
        ("v2", "2", "Cross repository version two. 更新后的知识成品。"),
        ("conflict", "1", "Different bytes under the original version must be rejected."),
    ):
        document_id = "cross-repository-document"
        snapshot = {"package_id": "cross-repository-builder-work", "name": "Cross-repository references",
                    "version": version, "source": {"author": "Synthetic author", "license": "CC0-1.0", "url": ""},
                    "chunking_version": CHUNKING_VERSION,
                    "documents": [{"document_id": document_id, "name": "reference.txt", "version": name,
                                   "sha256": digest(content.encode()), "content": content}],
                    "chunks": make_chunks(document_id, "reference.txt", [{"text": content, "location": {"paragraph": 1}}])}
        artifact = await build_package(snapshot, directory / name / "requested.zip", Encoder())
        results[name] = artifact["artifact_path"]
    assert results["v1"] != results["conflict"]
    (directory / "paths.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
