"""Integrity and provenance contract tests for vendored wheels under deps/ (C10-002)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPS_DIR = ROOT / "deps"
SHA256SUMS = DEPS_DIR / "SHA256SUMS"
DEPS_README = DEPS_DIR / "README.md"
NOTICE = ROOT / "NOTICE"


def _parse_sha256sums() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in SHA256SUMS.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"([0-9a-f]{64})\s+(.+)", stripped)
        assert match, f"Malformed SHA256SUMS line: {line!r}"
        digest, rel_path = match.groups()
        entries[rel_path] = digest
    return entries


def test_deps_wheels_match_sha256sums_manifest():
    """Every vendored wheel under deps/ must match deps/SHA256SUMS with no gaps."""
    assert SHA256SUMS.is_file()
    manifest = _parse_sha256sums()
    wheels = sorted(p.relative_to(ROOT).as_posix() for p in DEPS_DIR.glob("*.whl"))
    assert wheels, "Expected vendored wheels in deps/"
    assert set(manifest) == set(wheels)

    for rel_path, expected_digest in manifest.items():
        actual_digest = hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest()
        assert actual_digest == expected_digest, f"SHA-256 mismatch for {rel_path}"


def test_pyproject_uv_lock_and_requirements_match_deps_sha256sums():
    """Active pyncm-async pin in pyproject.toml, requirements.txt, and uv.lock must align with deps/SHA256SUMS."""
    manifest = _parse_sha256sums()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    uv_lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    assert 'pyncm-async = { path = "./deps/pyncm_async-1.8.1-py3-none-any.whl" }' in pyproject
    assert "./deps/pyncm_async-1.8.1-py3-none-any.whl" in requirements
    assert "deps/README.md" in pyproject and "deps/SHA256SUMS" in pyproject

    expected_181 = manifest["deps/pyncm_async-1.8.1-py3-none-any.whl"]
    assert f'filename = "pyncm_async-1.8.1-py3-none-any.whl", hash = "sha256:{expected_181}"' in uv_lock


def test_deps_readme_and_notice_document_pyncm_async_provenance_and_license():
    """Both deps/README.md and NOTICE must register pyncm-async provenance and Apache-2.0 license."""
    readme = DEPS_README.read_text(encoding="utf-8")
    notice = NOTICE.read_text(encoding="utf-8")

    for wheel_name in ("pyncm_async-1.8.1-py3-none-any.whl", "pyncm_async-1.8.2-py3-none-any.whl"):
        assert wheel_name in readme
        assert wheel_name in notice

    assert "Apache-2.0" in readme
    assert "Apache License 2.0" in notice
    assert "greats3an" in readme and "greats3an" in notice
