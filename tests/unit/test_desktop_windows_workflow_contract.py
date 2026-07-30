from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
CROSS_PLATFORM_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop.yml"
WINDOWS_WORKFLOW = ROOT / ".github" / "workflows" / "build-desktop-windows.yml"
SYNC_UPDATE_WORKFLOW = ROOT / ".github" / "workflows" / "sync-update-release.yml"
LOCAL_RELEASE_SCRIPT = ROOT / "scripts" / "build-desktop-release.ps1"


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
    assert "permissions:" in workflow
    assert "contents: write" in workflow
    assert "macos-" not in workflow
    assert "ubuntu-" not in workflow


def test_cross_platform_workflow_limits_both_matrices_for_windows_only_calls() -> None:
    workflow = _load_workflow(CROSS_PLATFORM_WORKFLOW)
    jobs = workflow["jobs"]
    matrices = [
        jobs["build-python"]["strategy"]["matrix"]["include"],
        jobs["build-electron"]["strategy"]["matrix"]["include"],
    ]

    assert all("inputs.windows_only &&" in matrix for matrix in matrices)
    assert '"artifact_name":"python-backend-win"' in matrices[0]
    assert '"artifact_name":"desktop-win-x64"' in matrices[1]


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


def test_published_stable_release_is_the_only_update_service_sync_trigger() -> None:
    workflow = _load_workflow(SYNC_UPDATE_WORKFLOW)
    condition = workflow["jobs"]["sync"]["if"]

    assert "!github.event.release.draft" in condition
    assert "!github.event.release.prerelease" in condition
    assert "startsWith(github.event.release.tag_name, 'v')" in condition
    validate = _steps_by_name(workflow, "sync")["Validate stable release assets"]
    expected_signatures = (
        "N.E.K.O_${VERSION}_win_manifest.json.sig",
        "N.E.K.O_${VERSION}_mac_x64_manifest.json.sig",
        "N.E.K.O_${VERSION}_mac_arm64_manifest.json.sig",
        "N.E.K.O_${VERSION}_linux_x64_manifest.json.sig",
        "N.E.K.O_${VERSION}_linux_x64_appimage_manifest.json.sig",
    )
    assert all(f'"{asset}"' in validate["run"] for asset in expected_signatures)


def test_portable_manifest_signing_is_required_for_nightly_and_local_stable_builds() -> None:
    workflow = _load_workflow(CROSS_PLATFORM_WORKFLOW)

    signing = _steps_by_name(workflow, "nightly")["Sign Portable manifests"]
    assert signing["env"]["PORTABLE_UPDATE_MANIFEST_ED25519_PRIVATE_KEY"] == (
        "${{ secrets.PORTABLE_UPDATE_MANIFEST_ED25519_PRIVATE_KEY }}"
    )
    assert signing["env"]["PORTABLE_MANIFEST_SIGNING_KEY_ID"] == (
        "portable-manifest-2026-07"
    )
    assert "is required to publish Portable updates" in signing["run"]
    assert "openssl pkeyutl -sign -rawin" in signing["run"]
    assert '"${manifest}.sig"' in signing["run"]
    assert "shopt -s nullglob" in signing["run"]

    local_script = LOCAL_RELEASE_SCRIPT.read_text(encoding="utf-8")
    assert "function Sign-PortableManifests" in local_script
    assert "openssl 'pkeyutl' '-sign' '-rawin'" in local_script
    assert "ManifestSigningKeyPath" in local_script


def test_local_release_build_clears_stale_electron_dist_output() -> None:
    local_script = LOCAL_RELEASE_SCRIPT.read_text(encoding="utf-8")

    assert "$distDirectory = Join-Path $ElectronPath 'dist'" in local_script
    assert "Remove-Item -LiteralPath $distDirectory -Recurse -Force" in local_script
    assert local_script.index("Portable output already exists") < local_script.index(
        "Remove-Item -LiteralPath $distDirectory -Recurse -Force"
    )


def test_local_release_build_rejects_unsupported_architecture_and_handles_missing_linux_config() -> None:
    local_script = LOCAL_RELEASE_SCRIPT.read_text(encoding="utf-8")

    assert "$buildPlatform -ne 'macos' -and $Architecture -ne 'x64'" in local_script
    assert "$package.PSObject.Properties['build']" in local_script
    assert "$buildConfig.Value.PSObject.Properties['linux']" in local_script


def test_delta_baseline_selects_a_preceding_stable_release() -> None:
    workflow = _load_workflow(CROSS_PLATFORM_WORKFLOW)
    steps = _steps_by_name(workflow, "build-electron")
    download = steps["Download previous Portable manifests"]

    assert "releases?per_page=100" in download["run"]
    assert "select(.tag_name != env.GITHUB_REF_NAME)" in download["run"]
