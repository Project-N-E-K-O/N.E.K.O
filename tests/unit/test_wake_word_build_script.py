"""Execute build preflight guards without permitting cloning or compilation."""

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_wake_word_runtime.ps1"
PACKAGING_FLAGS = ("SHERPA_ONNX_SPLIT_PYTHON_PACKAGE", "SHERPA_ONNX_IS_FOR_PYPI")
POWERSHELLS = [path for name in ("powershell", "pwsh") if (path := shutil.which(name))]


@pytest.fixture(params=POWERSHELLS or [None], ids=lambda path: Path(path).stem if path else "no-powershell")
def preflight(request, tmp_path):
    if request.param is None:
        pytest.skip("PowerShell is required to execute Windows build preflight")
    marker = tmp_path / "external-command-called.txt"
    wrapper = tmp_path / "preflight.ps1"
    wrapper.write_text(
        "param([string]$BuildScript, [string]$OutputPath, [string]$PythonPath)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "function global:git {\n"
        "  [IO.File]::AppendAllText($env:NEKO_BUILD_TEST_MARKER, 'git')\n"
        "  throw 'EXTERNAL_COMMAND_BLOCKED'\n"
        "}\n"
        "function global:uv {\n"
        "  [IO.File]::AppendAllText($env:NEKO_BUILD_TEST_MARKER, 'uv')\n"
        "  throw 'EXTERNAL_COMMAND_BLOCKED'\n"
        "}\n"
        "try {\n"
        "  & $BuildScript -Python $PythonPath -OutputDirectory $OutputPath\n"
        "} catch { Write-Output $_.Exception.Message; exit 91 }\n",
        encoding="utf-8",
    )

    def run(output, inherited=None):
        env = {key: value for key, value in os.environ.items() if key not in PACKAGING_FLAGS}
        env.update(inherited or {})
        env["NEKO_BUILD_TEST_MARKER"] = str(marker)
        result = subprocess.run(
            [request.param, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(wrapper), "-BuildScript", str(BUILD_SCRIPT),
             "-OutputPath", str(output), "-PythonPath", sys.executable],
            env=env, capture_output=True, text=True, timeout=20,
        )
        return result, marker

    return run


@pytest.mark.parametrize("flag", PACKAGING_FLAGS)
@pytest.mark.parametrize("value", ["0", "1"])
def test_inherited_packaging_flags_fail_before_clone(preflight, tmp_path, flag, value):
    output = tmp_path / "new output"
    result, marker = preflight(output, {flag: value})
    assert result.returncode == 91
    assert f"Unset {flag}" in result.stdout
    assert not marker.exists(), "Preflight must fail before any external command"
    assert not output.exists()


@pytest.mark.parametrize("kind", ["nonempty_directory", "file"])
def test_existing_output_is_preserved_and_rejected_before_clone(preflight, tmp_path, kind):
    output = tmp_path / "existing output"
    if kind == "nonempty_directory":
        output.mkdir()
        retained = output / "build-manifest.json"
    else:
        retained = output
    retained.write_bytes(b"existing build evidence")
    result, marker = preflight(output)
    assert result.returncode == 91
    assert "fresh empty output directory" in result.stdout
    assert not marker.exists(), "Preflight must fail before any external command"
    assert retained.read_bytes() == b"existing build evidence"


def test_clean_preflight_reaches_only_the_blocked_clone(preflight, tmp_path):
    output = tmp_path / "clean output"
    output.mkdir()
    result, marker = preflight(output)
    assert result.returncode == 91
    assert "EXTERNAL_COMMAND_BLOCKED" in result.stdout
    assert marker.read_text() == "git"
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("shell", POWERSHELLS or [None], ids=lambda path: Path(path).stem if path else "no-powershell")
@pytest.mark.parametrize("build_relative", ["b/Release", "b/platform-specific/Release"])
@pytest.mark.parametrize("variant,expected_version,merge,candidates", [
    ("combined", "1.13.8+neko.kws2", "ON", "ON"),
    ("baseline", "1.13.8.dev0+neko.kws2.baseline", "OFF", "OFF"),
    ("merge", "1.13.8.dev0+neko.kws2.merge", "ON", "OFF"),
    ("candidates", "1.13.8.dev0+neko.kws2.candidates", "OFF", "ON"),
])
def test_native_test_build_uses_exact_source_cache(
    tmp_path, shell, build_relative, variant, expected_version, merge, candidates,
):
    if shell is None:
        pytest.skip("PowerShell is required to execute Windows build flow")
    output = tmp_path / "build output"
    observed = tmp_path / "native-build.json"
    wrapper = tmp_path / "mock-build.ps1"
    wrapper.write_text(r'''
param([string]$BuildScript, [string]$OutputPath, [string]$PythonPath, [string]$Variant)
$ErrorActionPreference = 'Stop'
function global:git {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'clone') {
        $mockSource = $args[-1]
        New-Item -ItemType Directory -Path (Join-Path $mockSource 'sherpa-onnx/csrc') -Force | Out-Null
        # Model the post-patch combined source; git apply is a no-op below.
        [IO.File]::WriteAllText((Join-Path $mockSource 'CMakeLists.txt'),
            'set(SHERPA_ONNX_VERSION "1.13.8+neko.kws2")')
        [IO.File]::WriteAllText((Join-Path $mockSource 'sherpa-onnx/csrc/version.cc'),
            'const char *GetVersionStr() { static const char *version = "1.13.8+neko.kws2"; return version; }')
    } elseif ($args -contains 'rev-parse') {
        Write-Output '11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf'
    } elseif (-not ($args -contains 'apply')) {
        throw 'Unexpected git operation'
    }
}
function global:uv {
    $global:LASTEXITCODE = 0
    if (($args -contains 'setup.py') -and ($args -contains 'build')) {
        $expectedVersion = $env:NEKO_TEST_EXPECTED_VERSION
        $cmakeVersion = [IO.File]::ReadAllText((Join-Path (Get-Location).Path 'CMakeLists.txt'))
        $nativeVersion = [IO.File]::ReadAllText((Join-Path (Get-Location).Path 'sherpa-onnx/csrc/version.cc'))
        if (-not $cmakeVersion.Contains('set(SHERPA_ONNX_VERSION "' + $expectedVersion + '")')) {
            throw 'Package version not synchronized before setup build'
        }
        if (-not $nativeVersion.Contains('static const char *version = "' + $expectedVersion + '";')) {
            throw 'Native version not synchronized before setup build'
        }
        foreach ($expectedSwitch in @(
            ('-DSHERPA_ONNX_NEKO_KWS_MERGE=' + $env:NEKO_TEST_EXPECTED_MERGE),
            ('-DSHERPA_ONNX_NEKO_KWS_CANDIDATES=' + $env:NEKO_TEST_EXPECTED_CANDIDATES))) {
            if (-not $env:SHERPA_ONNX_CMAKE_ARGS.Contains($expectedSwitch)) {
                throw "Incorrect variant switch: $expectedSwitch"
            }
        }
        $hasAblation = $env:SHERPA_ONNX_CMAKE_ARGS.Contains('-DSHERPA_ONNX_NEKO_KWS_ABLATION=ON')
        if ($hasAblation -ne ($env:NEKO_TEST_VARIANT -ne 'combined')) {
            throw 'Incorrect ablation release guard'
        }
        $source = (Get-Location).Path.Replace([char]92, [char]47)
        $build = Join-Path (Get-Location).Path $env:NEKO_TEST_BUILD_RELATIVE
        $dependency = Join-Path $build '_deps/vendor-subbuild'
        New-Item -ItemType Directory -Path $dependency -Force | Out-Null
        # The dependency's home starts with the complete source path. A substring
        # match incorrectly treats this as a second root cache.
        [IO.File]::WriteAllText((Join-Path $dependency 'CMakeCache.txt'),
            "CMAKE_HOME_DIRECTORY:INTERNAL=$source/third_party/vendor`n")
        [IO.File]::WriteAllText((Join-Path $build 'CMakeCache.txt'),
            "CMAKE_HOME_DIRECTORY:INTERNAL=$source`nCMAKE_GENERATOR:INTERNAL=Visual Studio 17 2022`n")
    } elseif (($args -contains 'cmake') -and ($args -contains '--build')) {
        $record = @{ arguments = @($args); configuration = $env:SHERPA_ONNX_CMAKE_ARGS }
        [IO.File]::WriteAllText($env:NEKO_TEST_BUILD_OBSERVED, ($record | ConvertTo-Json -Depth 4))
        throw 'NATIVE_BUILD_REACHED'
    } else {
        throw 'Unexpected uv operation'
    }
}
try {
    & $BuildScript -Python $PythonPath -OutputDirectory $OutputPath -Variant $Variant
} catch { Write-Output $_.Exception.Message; exit 91 }
''', encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if key not in PACKAGING_FLAGS}
    env.update(NEKO_TEST_BUILD_RELATIVE=build_relative, NEKO_TEST_BUILD_OBSERVED=str(observed))
    env.update(NEKO_TEST_EXPECTED_VERSION=expected_version, NEKO_TEST_EXPECTED_MERGE=merge,
               NEKO_TEST_EXPECTED_CANDIDATES=candidates, NEKO_TEST_VARIANT=variant)
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper),
         "-BuildScript", str(BUILD_SCRIPT), "-OutputPath", str(output), "-PythonPath", sys.executable,
         "-Variant", variant],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 91
    assert "NATIVE_BUILD_REACHED" in result.stdout, result.stdout + result.stderr
    record = json.loads(observed.read_text(encoding="utf-8"))
    args = record["arguments"]
    assert Path(args[args.index("--build") + 1]) == output / "sherpa-onnx" / build_relative
    assert args[args.index("--config") + 1] == "Release"
    assert args[args.index("--target") + 1:args.index("--parallel")] == [
        "neko-kws-decoder-test", "neko-kws-lifecycle-test",
    ]
    assert '-G "Visual Studio 17 2022" -A x64' in record["configuration"]
    assert not (output / "build-manifest.json").exists()
