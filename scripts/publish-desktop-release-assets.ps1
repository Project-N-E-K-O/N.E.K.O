[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$')]
    [string]$Tag,

    [string]$AssetsDirectory = '',

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^oss://[^/]+/releases/?$')]
    [string]$OssReleaseRoot,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://[^/?#]+(?:/[^?#]*)?$')]
    [string]$CdnBaseUrl,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^https://[^/?#]+(?:/[^?#]*)?$')]
    [string]$ServiceUrl,

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$Product = 'N.E.K.O',

    [ValidateSet('stable', 'nightly')]
    [string]$Channel = 'stable',

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$MirrorId = 'aliyun',

    [string]$Repository = 'Project-N-E-K-O/N.E.K.O'
)

<#
.SYNOPSIS
Uploads locally staged desktop release assets to OSS and registers the verified CDN mirror.

.DESCRIPTION
Run this after every target's build-desktop-release.ps1 output has been collected
under release-assets/<version>/ and the exact same files have been published to
the GitHub Release. ossutil must already be configured on this local release host.
The Bucket name, endpoint, and credentials are never stored in this repository or
GitHub Actions.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)] [string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)] [string[]]$Arguments
    )
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $FilePath"
    }
}

foreach ($command in @('gh', 'ossutil', 'curl.exe')) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required local command was not found: $command"
    }
}
$adminToken = [Environment]::GetEnvironmentVariable('NEKO_UPDATE_ADMIN_TOKEN')
if ([string]::IsNullOrWhiteSpace($adminToken)) {
    throw 'NEKO_UPDATE_ADMIN_TOKEN is required to register the verified mirror'
}

$version = $Tag.Substring(1)
if ([string]::IsNullOrWhiteSpace($AssetsDirectory)) {
    $AssetsDirectory = Join-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'release-assets') $version
}
$AssetsDirectory = (Resolve-Path -LiteralPath $AssetsDirectory).Path
$assets = @(
    Get-ChildItem -LiteralPath $AssetsDirectory -Recurse -File |
        Where-Object { $_.Name -ne 'BUILD-INFO.json' }
)
if ($assets.Count -eq 0) {
    throw "No release assets found in $AssetsDirectory"
}
$duplicateNames = @($assets | Group-Object Name | Where-Object { $_.Count -gt 1 })
if ($duplicateNames.Count -gt 0) {
    throw "Duplicate staged asset names: $($duplicateNames.Name -join ', ')"
}

$remoteAssetNames = @(& gh release view $Tag '--repo' $Repository '--json' 'assets' '--jq' '.assets[].name')
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read GitHub Release $Tag"
}
$differences = Compare-Object -ReferenceObject @($remoteAssetNames | Sort-Object) -DifferenceObject @($assets.Name | Sort-Object)
if ($differences) {
    $formatted = $differences | ForEach-Object { "$($_.SideIndicator) $($_.InputObject)" }
    throw "Local staged assets must exactly match GitHub Release assets:`n$($formatted -join "`n")"
}

$latestTag = ((& gh api "repos/$Repository/releases/latest" '--jq' '.tag_name') | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $latestTag -ne $Tag) {
    throw "Tag $Tag must be the current GitHub stable release before registering its update metadata"
}

$ossRoot = $OssReleaseRoot.TrimEnd('/')
$cdnRoot = $CdnBaseUrl.TrimEnd('/')
foreach ($asset in $assets) {
    Write-Host "Uploading staged asset $($asset.Name)"
    $objectUrl = '{0}/{1}/{2}/{3}' -f $ossRoot, $Product, $version, $asset.Name
    Invoke-Checked ossutil 'cp' $asset.FullName $objectUrl '-f' '--meta' 'Cache-Control:public, max-age=31536000, immutable'
}

foreach ($asset in $assets) {
    $cdnUrl = '{0}/releases/{1}/{2}/{3}' -f $cdnRoot, [Uri]::EscapeDataString($Product), [Uri]::EscapeDataString($version), [Uri]::EscapeDataString($asset.Name)
    Write-Host "Verifying CDN asset $($asset.Name)"
    Invoke-Checked curl.exe '--fail' '--location' '--retry' '12' '--retry-all-errors' '--retry-delay' '5' `
        '--connect-timeout' '10' '--max-time' '60' '--range' '0-0' '--output' 'NUL' $cdnUrl
}

$escapedProduct = [Uri]::EscapeDataString($Product)
$escapedChannel = [Uri]::EscapeDataString($Channel)
$endpoint = '{0}/v1/admin/{1}/{2}/sync' -f $ServiceUrl.TrimEnd('/'), $escapedProduct, $escapedChannel
$headers = @{ Authorization = "Bearer $adminToken" }
$body = @{ version = $version; mirror_ids = @($MirrorId) } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri $endpoint -Headers $headers -ContentType 'application/json' -Body $body | Out-Null
Write-Host "Registered mirror '$MirrorId' for $Product $Tag after CDN verification."
