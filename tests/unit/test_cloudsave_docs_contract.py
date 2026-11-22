"""Contract tests verifying Cloud Save docs match `MANAGED_MEMORY_FILENAMES` (`R-C14-003`, `R1111-P3-01`)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from utils.cloudsave_runtime._shared import MANAGED_MEMORY_FILENAMES


REPO_ROOT = Path(__file__).resolve().parents[2]
CLOUDSAVE_DOCS = (
    REPO_ROOT / "docs/api/rest/cloudsave.md",
    REPO_ROOT / "docs/zh-CN/api/rest/cloudsave.md",
    REPO_ROOT / "docs/ja/api/rest/cloudsave.md",
)
_ALLOWLIST_BLOCK_RE = re.compile(r"```text\s*\n(.*?)\n```", re.DOTALL)


def _extract_allowlisted_filenames(doc_path: Path) -> list[str]:
    text = doc_path.read_text(encoding="utf-8")
    match = _ALLOWLIST_BLOCK_RE.search(text)
    assert match is not None, f"Missing ```text allowlist block in {doc_path}"
    return [line.strip() for line in match.group(1).splitlines() if line.strip()]


@pytest.mark.parametrize("doc_path", CLOUDSAVE_DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_cloudsave_docs_allowlist_matches_managed_memory_filenames(doc_path: Path):
    """Assert every mirrored Cloud Save doc lists all 17 allowlisted files in exact order."""
    documented = _extract_allowlisted_filenames(doc_path)
    expected = list(MANAGED_MEMORY_FILENAMES)
    assert len(expected) == 17
    assert documented == expected
