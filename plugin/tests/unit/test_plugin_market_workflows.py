from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.plugin_unit

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

_NEKO_REPOSITORY_INPUT = {
    "required": False,
    "type": "string",
    "default": "Project-N-E-K-O/N.E.K.O",
}
_PLUGIN_ID_CHECK = '[[ ! "$PLUGIN_ID" =~ ^[a-z][a-z0-9_]*$ ]]'
_RUN_ATTEMPT_EVIDENCE = '"workflow_run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])'


def _load_workflow(name: str) -> dict[str, Any]:
    path = REPOSITORY_ROOT / ".github" / "workflows" / name
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _workflow_call(workflow: dict[str, Any]) -> dict[str, Any]:
    # YAML 1.1 reads the bare `on:` key as boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict)
    return triggers["workflow_call"]


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    matches = [step for step in job["steps"] if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def test_reusable_verify_workflow_owns_the_market_checks_and_small_evidence() -> None:
    workflow = _load_workflow("plugin-market-verify.yml")

    assert _workflow_call(workflow)["inputs"]["neko-repository"] == _NEKO_REPOSITORY_INPUT
    job = workflow["jobs"]["verify"]
    assert job["env"]["NEKO_REPOSITORY"] == "${{ inputs.neko-repository }}"
    assert _PLUGIN_ID_CHECK in _step(job, "Validate workflow inputs")["run"]
    assert _step(job, "Ruff check")["run"].startswith("uvx ruff==0.12.4 check")
    assert 'check -r "${PLUGIN_ID}"' in _step(job, "Release check")["run"]

    evidence_script = _step(job, "Write Market evidence")["run"]
    assert _RUN_ATTEMPT_EVIDENCE in evidence_script
    assert "market-evidence.json" in evidence_script

    upload = _step(job, "Upload Market evidence")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["with"]["path"].endswith("/market-evidence.json")


def test_reusable_release_workflow_publishes_package_digest_evidence() -> None:
    workflow = _load_workflow("plugin-market-release.yml")

    assert _workflow_call(workflow)["inputs"]["neko-repository"] == _NEKO_REPOSITORY_INPUT
    jobs = workflow["jobs"]

    release = jobs["release"]
    assert release["env"]["NEKO_REPOSITORY"] == "${{ inputs.neko-repository }}"
    validate = _step(release, "Validate workflow inputs")["run"]
    assert _PLUGIN_ID_CHECK in validate
    assert '[[ "${GITHUB_REF_TYPE}" != "tag" ]]' in validate
    # The workflow checks the actual selected manifest/tag, replacing the CLI's
    # legacy root-directory/repository-name convention checks.
    assert "Release tag must match" in _step(release, "Mount plugin into N.E.K.O tree")["run"]
    assert 'check -r "${PLUGIN_ID}"' in _step(release, "Market release check")["run"]

    evidence_script = _step(jobs["evidence"], "Write Market evidence")["run"]
    assert "Actual package identity/version" in evidence_script
    assert "market-evidence.json" in evidence_script
    assert 'ref_type=os.environ["GITHUB_REF_TYPE"]' in evidence_script
    assert 'ref_name=os.environ["GITHUB_REF_NAME"]' in evidence_script
    assert _RUN_ATTEMPT_EVIDENCE in evidence_script

    attest = jobs["attest"]
    assert attest["if"] == "github.ref_type == 'tag'"
    assert _step(attest, "Upload Market evidence")["with"]["path"].endswith(
        "/market-evidence.json"
    )
    gh_release = _step(attest, "Create GitHub Release")
    assert gh_release["uses"].startswith("softprops/action-gh-release@")
    assert gh_release["with"]["fail_on_unmatched_files"] is True
