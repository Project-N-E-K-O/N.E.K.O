from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CROSS_PLATFORM_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop.yml"
WINDOWS_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop-windows.yml"


def test_windows_workflow_calls_cross_platform_workflow_in_windows_only_mode() -> None:
    workflow = WINDOWS_WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "uses: ./.github/workflows/build-desktop.yml" in workflow
    assert "version: ${{ inputs.version }}" in workflow
    assert "electron_repo: ${{ inputs.electron_repo }}" in workflow
    assert "electron_ref: ${{ inputs.electron_ref }}" in workflow
    assert "previous_portable_release: ${{ inputs.previous_portable_release }}" in workflow
    assert "allow_fork_build: ${{ inputs.allow_fork_build }}" in workflow
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


def test_reusable_build_honors_signing_inputs_and_distribution_wrapper() -> None:
    workflow = CROSS_PLATFORM_WORKFLOW.read_text(encoding="utf-8")

    assert "github.event_name == 'workflow_dispatch' && inputs.skip_signing" not in workflow
    assert "github.event_name == 'schedule' || inputs.skip_signing == 'true'" in workflow
    assert "Build Electron app (Windows ZIP Portable directory, unsigned)" in workflow
    assert "Build Electron app (Windows ZIP Portable directory, signed)" in workflow
    assert "inputs.skip_signing != 'true'" in workflow
    assert "WIN_CSC_LINK: ${{ secrets.WIN_CSC_LINK }}" in workflow
    assert "WIN_CSC_KEY_PASSWORD: ${{ secrets.WIN_CSC_KEY_PASSWORD }}" in workflow
    assert "run: npx electron-builder ${{ matrix.electron_args }} --publish never" not in workflow
    assert (
        "run: node scripts/build-electron-distribution.js "
        "${{ matrix.builder_platform }} ${{ matrix.portable_arch_args }} "
        "--publish never"
    ) in workflow


def test_debug_build_values_are_runtime_inputs_not_test_defaults() -> None:
    windows_workflow = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
    cross_platform_workflow = CROSS_PLATFORM_WORKFLOW.read_text(encoding="utf-8")

    assert "allow_fork_build:" in windows_workflow
    assert "allow_fork_build:" in cross_platform_workflow
    assert "inputs.allow_fork_build" in cross_platform_workflow
    assert "'Project-N-E-K-O/N.E.K.O.-PC'" in cross_platform_workflow
    assert "default: 'Project-N-E-K-O/N.E.K.O.-PC'" in windows_workflow
    assert "default: 'main'" in windows_workflow
    assert "default: false" in windows_workflow


def test_windows_only_nightly_preserves_other_platform_assets() -> None:
    workflow = CROSS_PLATFORM_WORKFLOW.read_text(encoding="utf-8")

    assert "Create or update Windows nightly release" in workflow
    assert "gh release upload nightly release/* --clobber" in workflow
    assert "if: ${{ !inputs.windows_only }}" in workflow
