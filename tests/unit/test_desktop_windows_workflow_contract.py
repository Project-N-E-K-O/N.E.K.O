from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CROSS_PLATFORM_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop.yml"
WINDOWS_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop-windows.yml"


def test_windows_workflow_calls_cross_platform_workflow_in_windows_only_mode() -> None:
    workflow = WINDOWS_WORKFLOW.read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "'ci/portable-update'" in workflow
    assert "'.github/workflows/build-desktop-windows.yml'" in workflow
    assert "workflow_dispatch:" in workflow
    assert "uses: ./.github/workflows/build-desktop.yml" in workflow
    assert "inputs.version || '0.8.4-nightly.1'" in workflow
    assert "inputs.electron_ref || 'feat/auto-update'" in workflow
    assert "windows_only: true" in workflow
    assert "secrets: inherit" in workflow
    assert "macos-" not in workflow
    assert "ubuntu-" not in workflow


def test_cross_platform_workflow_limits_both_matrices_for_windows_only_calls() -> None:
    workflow = CROSS_PLATFORM_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_call:" in workflow
    assert workflow.count("inputs.windows_only &&") == 2
    assert '"artifact_name":"python-backend-win"' in workflow
    assert '"artifact_name":"desktop-win-x64"' in workflow


def test_windows_only_nightly_preserves_other_platform_assets() -> None:
    workflow = CROSS_PLATFORM_WORKFLOW.read_text(encoding="utf-8")

    assert "Create or update Windows nightly release" in workflow
    assert "gh release upload nightly release/* --clobber" in workflow
    assert "if: ${{ !inputs.windows_only }}" in workflow
