from pathlib import Path

import pytest

pytestmark = pytest.mark.plugin_unit

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_reusable_verify_workflow_owns_the_market_checks_and_small_evidence() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "plugin-market-verify.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "NEKO_REPOSITORY: Project-N-E-K-O/N.E.K.O" in workflow
    assert "uvx ruff==0.12.4 check" in workflow
    assert "check -r" in workflow
    assert "market-evidence.json" in workflow
    assert "Upload Market evidence" in workflow


def test_reusable_release_workflow_publishes_package_digest_evidence() -> None:
    workflow = (
        REPOSITORY_ROOT / ".github" / "workflows" / "plugin-market-release.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert "NEKO_REPOSITORY: Project-N-E-K-O/N.E.K.O" in workflow
    assert "check -r --market-release" in workflow
    assert "market-evidence.json" in workflow
    assert "softprops/action-gh-release" in workflow
    assert "fail_on_unmatched_files: true" in workflow
