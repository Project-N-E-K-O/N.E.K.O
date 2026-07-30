[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$ElectronPath = (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'N.E.K.O.-PC'),

    [ValidateSet('auto', 'windows', 'macos', 'linux')]
    [string]$Platform = 'auto',

    [ValidateSet('x64', 'arm64')]
    [string]$Architecture = 'x64',

    [string]$PreviousReleaseTag = '',

    [Parameter(Mandatory = $true)]
    [string]$ManifestSigningKeyPath,

    [string]$ManifestSigningKeyId = 'portable-manifest-2026-07',

    [string]$OutputDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'release-assets'),

    [switch]$SkipNpmInstall
)

<#
.SYNOPSIS
Builds one locally signed desktop target and stages stable-release assets.

.DESCRIPTION
This script deliberately does not create a GitHub Release, upload assets, push a
tag, or call the update service. Run it on each target platform, test the staged
artifacts, and publish the release manually only after approval.

The matching Nuitka backend must already be present in N.E.K.O.-PC/bin.  This
keeps the Electron signing and Portable package generation local while allowing
each platform's backend build to use its native toolchain.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-HostPlatform {
    if ($Platform -ne 'auto') {
        return $Platform
    }
    if ($env:OS -eq 'Windows_NT') {
        return 'windows'
    }
    if ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::OSX)) {
        return 'macos'
    }
    if ([System.Runtime.InteropServices.RuntimeInformation]::IsOSPlatform([System.Runtime.InteropServices.OSPlatform]::Linux)) {
        return 'linux'
    }
    throw 'Unsupported host platform. Specify -Platform only on Windows, macOS, or Linux.'
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)] [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)] [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath $($Arguments -join ' ')"
    }
}

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)] [string]$Path,
        [Parameter(Mandatory = $true)] [string]$Content
    )
    $utf8WithoutBom = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8WithoutBom)
}

function Get-PortableTarget {
    param([Parameter(Mandatory = $true)] [string]$BuildPlatform)
    switch ($BuildPlatform) {
        'windows' { return @{ Key = 'win'; NodePlatform = 'win32'; Bundle = 'dist/win-unpacked' } }
        'macos' {
            if ($Architecture -eq 'arm64') {
                return @{ Key = 'mac_arm64'; NodePlatform = 'darwin'; Bundle = 'dist/portable-stage/mac-arm64/N.E.K.O.app' }
            }
            return @{ Key = 'mac_x64'; NodePlatform = 'darwin'; Bundle = 'dist/portable-stage/mac/N.E.K.O.app' }
        }
        'linux' { return @{ Key = 'linux_x64'; NodePlatform = 'linux'; Bundle = 'dist/portable-stage/linux-unpacked' } }
        default { throw "Unsupported build platform: $BuildPlatform" }
    }
}

function Get-BackendPath {
    param([Parameter(Mandatory = $true)] [string]$BuildPlatform)
    switch ($BuildPlatform) {
        'windows' { return Join-Path $ElectronPath 'bin/projectneko_server.exe' }
        'macos' { return Join-Path $ElectronPath 'bin/projectneko_server' }
        'linux' { return Join-Path $ElectronPath 'bin/projectneko_server' }
    }
}

function Get-PreviousManifest {
    param(
        [Parameter(Mandatory = $true)] [hashtable]$Target,
        [Parameter(Mandatory = $true)] [string]$Destination
    )
    if ([string]::IsNullOrWhiteSpace($PreviousReleaseTag)) {
        return $null
    }
    Invoke-Checked gh 'release' 'download' $PreviousReleaseTag '--repo' 'Project-N-E-K-O/N.E.K.O' '--pattern' "*_$($Target.Key)_manifest.json" '--dir' $Destination
    if ($Target.Key -eq 'linux_x64') {
        Invoke-Checked gh 'release' 'download' $PreviousReleaseTag '--repo' 'Project-N-E-K-O/N.E.K.O' '--pattern' '*_linux_x64_appimage_manifest.json' '--dir' $Destination
    }
    $manifest = @(Get-ChildItem -LiteralPath $Destination -Filter "*_$($Target.Key)_manifest.json" -File)
    if ($manifest.Count -ne 1) {
        throw "Expected exactly one previous $($Target.Key) manifest in $Destination."
    }
    return $manifest[0].FullName
}

function Sign-PortableManifests {
    param([Parameter(Mandatory = $true)] [string]$Directory)
    $manifests = @(Get-ChildItem -LiteralPath $Directory -Filter '*_manifest.json' -File)
    if ($manifests.Count -eq 0) {
        throw "No Portable manifests were generated in $Directory."
    }
    foreach ($manifest in $manifests) {
        $rawSignaturePath = "$($manifest.FullName).rawsig"
        try {
            Invoke-Checked openssl 'pkeyutl' '-sign' '-rawin' '-inkey' $ManifestSigningKeyPath '-in' $manifest.FullName '-out' $rawSignaturePath
            $signature = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($rawSignaturePath))
            $signatureDocument = [ordered]@{
                schemaVersion = 1
                keyId = $ManifestSigningKeyId
                signature = $signature
            } | ConvertTo-Json -Depth 3
            Write-Utf8File -Path "$($manifest.FullName).sig" -Content "$signatureDocument`n"
        }
        finally {
            Remove-Item -LiteralPath $rawSignaturePath -Force -ErrorAction SilentlyContinue
        }
    }
}

$buildPlatform = Get-HostPlatform
$target = Get-PortableTarget -BuildPlatform $buildPlatform
$ElectronPath = (Resolve-Path -LiteralPath $ElectronPath).Path
$ManifestSigningKeyPath = (Resolve-Path -LiteralPath $ManifestSigningKeyPath).Path
$backendPath = Get-BackendPath -BuildPlatform $buildPlatform

if (-not (Test-Path -LiteralPath $backendPath -PathType Leaf)) {
    throw "Backend artifact is missing: $backendPath. Build the matching local Nuitka backend and place it in N.E.K.O.-PC/bin first."
}
if (-not (Test-Path -LiteralPath (Join-Path $ElectronPath 'package.json') -PathType Leaf)) {
    throw "Electron project package.json is missing under $ElectronPath."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw 'Node.js is required.'
}
if (-not (Get-Command openssl -ErrorAction SilentlyContinue)) {
    throw 'OpenSSL is required to sign Portable manifests.'
}
if (-not [string]::IsNullOrWhiteSpace($PreviousReleaseTag) -and -not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI is required when -PreviousReleaseTag is supplied.'
}

$versionOutputDirectory = Join-Path (Join-Path $OutputDirectory $Version) $target.Key
if (Test-Path -LiteralPath $versionOutputDirectory) {
    throw "Refusing to overwrite existing staged assets: $versionOutputDirectory"
}

$packagePath = Join-Path $ElectronPath 'package.json'
$originalPackage = [System.IO.File]::ReadAllBytes($packagePath)
$oldPortableBuild = $env:NEKO_PORTABLE_BUILD
$oldSigningDiscovery = $env:CSC_IDENTITY_AUTO_DISCOVERY
$previousDirectory = $null

Push-Location $ElectronPath
try {
    if (-not $SkipNpmInstall) {
        Invoke-Checked npm 'ci'
    }

    $package = Get-Content -Raw -Encoding UTF8 -LiteralPath $packagePath | ConvertFrom-Json
    $package.version = $Version
    if ($buildPlatform -eq 'linux') {
        if (-not $package.build.linux) { $package.build | Add-Member -NotePropertyName linux -NotePropertyValue ([pscustomobject]@{}) }
        $package.build.linux | Add-Member -Force -NotePropertyName maintainer -NotePropertyValue 'Project N.E.K.O. <projectneko@yahoo.com>'
    }
    Write-Utf8File -Path $packagePath -Content (($package | ConvertTo-Json -Depth 100) + "`n")

    $portableUpdateDirectory = Join-Path $ElectronPath 'dist/portable-update'
    if (Test-Path -LiteralPath $portableUpdateDirectory) {
        throw "Portable output already exists: $portableUpdateDirectory. Remove it after preserving any prior build, then retry."
    }

    $previousDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("neko-previous-portable-" + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $previousDirectory | Out-Null
    $previousManifest = Get-PreviousManifest -Target $target -Destination $previousDirectory

    $env:CSC_IDENTITY_AUTO_DISCOVERY = 'true'
    $archArgs = if ($buildPlatform -eq 'macos') { @("--$Architecture") } else { @() }

    if ($buildPlatform -eq 'windows') {
        Invoke-Checked node 'scripts/build-electron-distribution.js' 'windows' '--dir' '--publish' 'never'
    }
    else {
        Invoke-Checked node 'scripts/build-electron-distribution.js' $buildPlatform @archArgs '--publish' 'never'
        $env:NEKO_PORTABLE_BUILD = '1'
        Invoke-Checked node 'scripts/build-electron-distribution.js' $buildPlatform '--dir' @archArgs '--publish' 'never' '-c.directories.output=dist/portable-stage'
    }

    $updateArgs = @('scripts/create-portable-update.js', '--version', $Version, '--out', $portableUpdateDirectory)
    if ($buildPlatform -eq 'windows') {
        $updateArgs += @('--dir', $target.Bundle)
    }
    else {
        $updateArgs += @('--dir', $target.Bundle, '--platform', $target.NodePlatform, '--arch', $Architecture)
    }
    if ($previousManifest) {
        $updateArgs += @('--previous', $previousManifest)
    }
    Invoke-Checked node @updateArgs

    if ($buildPlatform -eq 'linux') {
        $appImages = @(Get-ChildItem -LiteralPath (Join-Path $ElectronPath 'dist') -Filter '*.AppImage' -File)
        if ($appImages.Count -ne 1) {
            throw "Expected exactly one AppImage in $ElectronPath/dist; found $($appImages.Count)."
        }
        $appImageArgs = @('scripts/create-portable-update.js', '--appimage', $appImages[0].FullName, '--arch', $Architecture, '--version', $Version, '--out', $portableUpdateDirectory)
        $previousAppImageManifest = @(Get-ChildItem -LiteralPath $previousDirectory -Filter '*_linux_x64_appimage_manifest.json' -File)
        if ($previousAppImageManifest.Count -eq 1) {
            $appImageArgs += @('--previous', $previousAppImageManifest[0].FullName)
        }
        Invoke-Checked node @appImageArgs
    }

    Sign-PortableManifests -Directory $portableUpdateDirectory
    New-Item -ItemType Directory -Path $versionOutputDirectory | Out-Null
    Get-ChildItem -LiteralPath $portableUpdateDirectory -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $versionOutputDirectory -Force
    }

    if ($buildPlatform -ne 'windows') {
        $regularAssets = Get-ChildItem -LiteralPath (Join-Path $ElectronPath 'dist') -File | Where-Object { $_.Extension -in '.dmg', '.zip', '.AppImage', '.deb', '.gz' }
        foreach ($asset in $regularAssets) {
            Copy-Item -LiteralPath $asset.FullName -Destination $versionOutputDirectory -Force
        }
    }

    $buildInfo = [ordered]@{
        version = $Version
        platform = $buildPlatform
        architecture = $Architecture
        portableTarget = $target.Key
        backend = $backendPath
        previousReleaseTag = $PreviousReleaseTag
        generatedAt = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json -Depth 3
    Write-Utf8File -Path (Join-Path $versionOutputDirectory 'BUILD-INFO.json') -Content "$buildInfo`n"
    Write-Host "Staged signed release assets: $versionOutputDirectory"
    Write-Host 'Test these assets before manually creating and publishing the GitHub Release.'
}
finally {
    [System.IO.File]::WriteAllBytes($packagePath, $originalPackage)
    $env:NEKO_PORTABLE_BUILD = $oldPortableBuild
    $env:CSC_IDENTITY_AUTO_DISCOVERY = $oldSigningDiscovery
    if ($previousDirectory) {
        Remove-Item -LiteralPath $previousDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
    Pop-Location
}
