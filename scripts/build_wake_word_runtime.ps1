param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [ValidateSet('combined', 'baseline', 'merge', 'candidates')][string]$Variant = 'combined'
)
$ErrorActionPreference = 'Stop'
$wakeSourceCommit = '11afbd009a7f8c08f4bcf2fc1b265d0df4670fbf'
$wakeOutput = [IO.Path]::GetFullPath($OutputDirectory)
$wakeSource = Join-Path $wakeOutput 'sherpa-onnx'
$wakePatches = @(
    (Join-Path $PSScriptRoot 'patches/sherpa-onnx-kws-timestamps.patch'),
    (Join-Path $PSScriptRoot 'patches/sherpa-onnx-kws-decoder.patch')
)
$wakeVersion = '1.13.8+neko.kws2'
if ($Variant -ne 'combined') { $wakeVersion = "1.13.8.dev0+neko.kws2.$Variant" }
foreach ($wakePackagingFlag in @('SHERPA_ONNX_SPLIT_PYTHON_PACKAGE', 'SHERPA_ONNX_IS_FOR_PYPI')) {
    if (Test-Path -LiteralPath "Env:$wakePackagingFlag") { throw "Unset $wakePackagingFlag for a self-contained patched wheel" }
}
foreach ($wakePatch in $wakePatches) {
    if (-not (Test-Path -LiteralPath $wakePatch -PathType Leaf)) { throw "Missing patch: $wakePatch" }
}
if ((Test-Path -LiteralPath $wakeOutput) -and
    ((-not (Test-Path -LiteralPath $wakeOutput -PathType Container)) -or
     @(Get-ChildItem -LiteralPath $wakeOutput -Force).Count -gt 0)) {
    throw "Use a fresh empty output directory: $wakeOutput"
}
New-Item -ItemType Directory -Force -Path $wakeOutput | Out-Null
git clone --depth 1 --branch v1.13.8 https://github.com/k2-fsa/sherpa-onnx.git $wakeSource
if ($LASTEXITCODE -ne 0) { throw 'Clone failed' }
$wakeActualCommit = git -C $wakeSource rev-parse HEAD
if ($wakeActualCommit -ne $wakeSourceCommit) { throw 'Upstream tag commit changed' }
foreach ($wakePatch in $wakePatches) {
    git -C $wakeSource apply --check $wakePatch
    if ($LASTEXITCODE -ne 0) { throw "Patch check failed: $wakePatch" }
    git -C $wakeSource apply $wakePatch
    if ($LASTEXITCODE -ne 0) { throw "Patch failed: $wakePatch" }
}
if ($Variant -ne 'combined') {
    $wakeCmakeFile = Join-Path $wakeSource 'CMakeLists.txt'
    $wakeCmakeText = [IO.File]::ReadAllText($wakeCmakeFile)
    $wakeVersionLine = 'set(SHERPA_ONNX_VERSION "1.13.8+neko.kws2")'
    if (-not $wakeCmakeText.Contains($wakeVersionLine)) { throw 'Patched runtime version missing' }
    [IO.File]::WriteAllText($wakeCmakeFile, $wakeCmakeText.Replace($wakeVersionLine, "set(SHERPA_ONNX_VERSION `"$wakeVersion`")"))
    # Upstream exports a separately hard-coded native version.
    $wakeNativeVersionFile = Join-Path $wakeSource 'sherpa-onnx/csrc/version.cc'
    $wakeNativeVersionText = [IO.File]::ReadAllText($wakeNativeVersionFile)
    $wakeNativeVersionLine = 'static const char *version = "1.13.8+neko.kws2";'
    if (-not $wakeNativeVersionText.Contains($wakeNativeVersionLine)) { throw 'Patched native runtime version missing' }
    [IO.File]::WriteAllText($wakeNativeVersionFile, $wakeNativeVersionText.Replace($wakeNativeVersionLine, "static const char *version = `"$wakeVersion`";"))
}
$wakeSavedCmake = $env:SHERPA_ONNX_CMAKE_ARGS
try {
    $env:SHERPA_ONNX_CMAKE_ARGS = '-G "Visual Studio 17 2022" -A x64 -DCMAKE_BUILD_TYPE=Release -DSHERPA_ONNX_ENABLE_BINARY=OFF -DSHERPA_ONNX_ENABLE_PORTAUDIO=OFF -DSHERPA_ONNX_ENABLE_WEBSOCKET=OFF -DSHERPA_ONNX_ENABLE_TTS=ON -DSHERPA_ONNX_ENABLE_SPEAKER_DIARIZATION=ON -DSHERPA_ONNX_NEKO_KWS_TESTS=ON'
    $wakeMerge = if ($Variant -in @('combined', 'merge')) { 'ON' } else { 'OFF' }
    $wakeCandidates = if ($Variant -in @('combined', 'candidates')) { 'ON' } else { 'OFF' }
    $env:SHERPA_ONNX_CMAKE_ARGS += " -DSHERPA_ONNX_NEKO_KWS_MERGE=$wakeMerge -DSHERPA_ONNX_NEKO_KWS_CANDIDATES=$wakeCandidates"
    if ($Variant -ne 'combined') { $env:SHERPA_ONNX_CMAKE_ARGS += ' -DSHERPA_ONNX_NEKO_KWS_ABLATION=ON' }
    $wakePythonPath = [IO.Path]::GetFullPath($Python).Replace('\', '/')
    $env:SHERPA_ONNX_CMAKE_ARGS += ' -DPython_EXECUTABLE="' + $wakePythonPath + '" -DPYTHON_EXECUTABLE="' + $wakePythonPath + '"'
    Push-Location $wakeSource
    try {
        New-Item -ItemType Directory -Force -Path 'build/sherpa_onnx/bin' | Out-Null
        uv run --no-project --python $Python --with cmake==3.31.10 --with setuptools==83.0.0 --with wheel==0.48.0 python setup.py build --build-temp b
        if ($LASTEXITCODE -ne 0) { throw 'Native build failed' }
        # setuptools can append Release to --build-temp. Locate the source's
        # cache, excluding dependency/subbuild caches, instead of assuming b.
        $wakeNormalizedSource = $wakeSource.Replace([char]92, [char]47)
        $wakeRootCaches = @(Get-ChildItem -LiteralPath 'b' -Recurse -Filter 'CMakeCache.txt' | Where-Object {
            Select-String -LiteralPath $_.FullName -Quiet -Pattern ('^CMAKE_HOME_DIRECTORY:INTERNAL=' + [regex]::Escape($wakeNormalizedSource) + '$')
        })
        if ($wakeRootCaches.Count -ne 1) { throw 'Expected exactly one source CMake cache' }
        $wakeBuildDirectory = $wakeRootCaches[0].DirectoryName
        uv run --no-project --python $Python --with cmake==3.31.10 cmake --build $wakeBuildDirectory --config Release --target neko-kws-decoder-test neko-kws-lifecycle-test --parallel 2
        if ($LASTEXITCODE -ne 0) { throw 'Native test build failed' }
        $wakeNativeTests = @(Get-ChildItem -LiteralPath 'b' -Recurse -Filter 'neko-kws-decoder-test.exe')
        if ($wakeNativeTests.Count -ne 1) { throw 'Expected exactly one native KWS test executable' }
        & $wakeNativeTests[0].FullName
        if ($LASTEXITCODE -ne 0) { throw 'Native KWS tests failed' }
        uv run --no-project --python $Python --with cmake==3.31.10 --with setuptools==83.0.0 --with wheel==0.48.0 python setup.py bdist_wheel --skip-build
        if ($LASTEXITCODE -ne 0) { throw 'Wheel build failed' }
        $wakeWheels = @(Get-ChildItem -LiteralPath (Join-Path $wakeSource 'dist') -Filter '*.whl')
        if ($wakeWheels.Count -ne 1) { throw 'Expected exactly one runtime wheel' }
        # Import outside the source tree so a checkout cannot shadow the wheel.
        Push-Location $wakeOutput
        try {
            $wakeImportJson = uv run --no-project --python $Python --with $wakeWheels[0].FullName python -c 'import json,sys,sherpa_onnx as s; print(json.dumps(dict(package_version=s.__version__,native_version=s.version,onnxruntime_version=s.onnxruntime_version,git_sha1=s.git_sha1,python=sys.version)))'
            if ($LASTEXITCODE -ne 0) { throw 'Built wheel import failed' }
            $wakeImport = $wakeImportJson | ConvertFrom-Json
            if ($wakeImport.package_version -ne $wakeVersion -or $wakeImport.native_version -ne $wakeVersion) {
                throw "Built wheel package/native version check failed: package=$($wakeImport.package_version), native=$($wakeImport.native_version), expected=$wakeVersion"
            }
        } finally { Pop-Location }
        $wakeCache = Get-Content -LiteralPath $wakeRootCaches[0].FullName | Where-Object {
            $_ -match '^(CMAKE_(CXX_COMPILER|C_COMPILER|GENERATOR|BUILD_TYPE)|SHERPA_ONNX_NEKO_KWS_[A-Z_]+):'
        }
        $wakeManifest = [ordered]@{
            upstream_commit = $wakeSourceCommit
            runtime_version = $wakeVersion
            variant = $Variant
            native_helper_tests_passed = $true
            lifecycle_harness_built = $true
            lifecycle_model_tests_passed = $null # Requires explicitly supplied model/WAV fixtures.
            wheel_import_passed = $true
            imported_runtime = $wakeImport
            cmake_args = $env:SHERPA_ONNX_CMAKE_ARGS
            cmake_cache = @($wakeCache)
            patches = @($wakePatches | ForEach-Object { @{ file = [IO.Path]::GetFileName($_); sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash } })
            wheel = @{ file = $wakeWheels[0].Name; sha256 = (Get-FileHash -LiteralPath $wakeWheels[0].FullName -Algorithm SHA256).Hash }
        }
        $wakeNativeOutput = Join-Path $wakeOutput 'native'
        New-Item -ItemType Directory -Path $wakeNativeOutput | Out-Null
        Get-ChildItem -LiteralPath $wakeNativeTests[0].DirectoryName -File | Where-Object {
            $_.Name -like 'neko-kws-*.exe' -or $_.Name -like 'onnxruntime*.dll'
        } | Copy-Item -Destination $wakeNativeOutput
        $wakeManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $wakeOutput 'build-manifest.json') -Encoding utf8
        $wakeWheels | Get-FileHash -Algorithm SHA256
    } finally { Pop-Location }
} finally { $env:SHERPA_ONNX_CMAKE_ARGS = $wakeSavedCmake }
