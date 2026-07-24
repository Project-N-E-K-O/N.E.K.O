from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CROSS_PLATFORM_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop.yml"
WINDOWS_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop-windows.yml"


def _load_workflow(path: Path) -> dict:
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _steps_by_name(workflow: dict, job_name: str) -> dict[str, dict]:
    steps = workflow["jobs"][job_name]["steps"]
    return {step["name"]: step for step in steps if "name" in step}


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
    workflow = _load_workflow(CROSS_PLATFORM_WORKFLOW)
    build_steps = _steps_by_name(workflow, "build-electron")

    disable_macos_signing = build_steps["Disable macOS code signing"]
    assert disable_macos_signing["if"] == (
        "runner.os == 'macOS' && "
        "(github.event_name == 'schedule' || inputs.skip_signing == 'true')"
    )

    unsigned_windows = build_steps[
        "Build Electron app (Windows ZIP Portable directory, unsigned)"
    ]
    assert unsigned_windows["if"] == (
        "runner.os == 'Windows' && "
        "(github.event_name == 'schedule' || inputs.skip_signing == 'true')"
    )
    assert unsigned_windows["run"] == (
        "node scripts/build-electron-distribution.js windows --dir --publish never"
    )
    assert unsigned_windows["env"]["CSC_IDENTITY_AUTO_DISCOVERY"] == "false"
    assert "WIN_CSC_LINK" not in unsigned_windows["env"]
    assert "WIN_CSC_KEY_PASSWORD" not in unsigned_windows["env"]

    signed_windows = build_steps[
        "Build Electron app (Windows ZIP Portable directory, signed)"
    ]
    assert signed_windows["if"] == (
        "runner.os == 'Windows' && github.event_name != 'schedule' "
        "&& inputs.skip_signing != 'true'"
    )
    assert signed_windows["run"] == (
        "node scripts/build-electron-distribution.js windows --dir --publish never"
    )
    assert signed_windows["env"]["WIN_CSC_LINK"] == "${{ secrets.WIN_CSC_LINK }}"
    assert signed_windows["env"]["WIN_CSC_KEY_PASSWORD"] == (
        "${{ secrets.WIN_CSC_KEY_PASSWORD }}"
    )

    distribution = build_steps["Build Electron app (macOS/Linux)"]
    assert distribution["run"] == (
        "node scripts/build-electron-distribution.js "
        "${{ matrix.builder_platform }} ${{ matrix.portable_arch_args }} "
        "--publish never"
    )

    nightly_steps = _steps_by_name(workflow, "nightly")
    windows_nightly = nightly_steps["Create or update Windows nightly release"]
    assert windows_nightly["if"] == "${{ inputs.windows_only }}"
    assert "gh release upload nightly release/* --clobber" in windows_nightly["run"]


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
    workflow = _load_workflow(CROSS_PLATFORM_WORKFLOW)
    nightly_steps = _steps_by_name(workflow, "nightly")

    assert nightly_steps["Delete old nightly release"]["if"] == (
        "${{ !inputs.windows_only }}"
    )
    assert nightly_steps["Create nightly release"]["if"] == (
        "${{ !inputs.windows_only }}"
    )
    windows_nightly = nightly_steps["Create or update Windows nightly release"]
    assert windows_nightly["if"] == "${{ inputs.windows_only }}"
    assert "gh release upload nightly release/* --clobber" in windows_nightly["run"]
