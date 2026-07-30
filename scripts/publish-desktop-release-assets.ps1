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

function Get-Sha256 {
    param([Parameter(Mandatory = $true)] [string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-OssObjectExists {
    param([Parameter(Mandatory = $true)] [string]$ObjectUrl)
    $null = & ossutil stat $ObjectUrl 2>$null
    return $LASTEXITCODE -eq 0
}

function Invoke-UpdateMirrorSync {
    param(
        [Parameter(Mandatory = $true)] [string]$Endpoint,
        [Parameter(Mandatory = $true)] [hashtable]$Headers,
        [Parameter(Mandatory = $true)] [string]$Body
    )
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        try {
            Invoke-RestMethod -Method Post -Uri $Endpoint -Headers $Headers -ContentType 'application/json' -Body $Body -TimeoutSec 30 | Out-Null
            return
        }
        catch {
            $statusCode = $null
            if ($_.Exception.Response) {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
            $retryable = $null -eq $statusCode -or $statusCode -eq 408 -or $statusCode -eq 429 -or $statusCode -ge 500
            if (-not $retryable -or $attempt -eq 3) {
                throw
            }
            Write-Warning "Mirror registration attempt $attempt failed; retrying."
            Start-Sleep -Seconds (2 * $attempt)
        }
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
$assetHashes = @{}
$assetNameSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::Ordinal)
foreach ($asset in $assets) {
    $assetHashes[$asset.Name] = Get-Sha256 -Path $asset.FullName
    [void]$assetNameSet.Add($asset.Name)
}
foreach ($manifest in @($assets | Where-Object { $_.Name.EndsWith('_manifest.json', [System.StringComparison]::Ordinal) })) {
    if (-not $assetNameSet.Contains("$($manifest.Name).sig")) {
        throw "Portable manifest is missing its signature asset: $($manifest.Name).sig"
    }
}
foreach ($signature in @($assets | Where-Object { $_.Name.EndsWith('.sig', [System.StringComparison]::Ordinal) })) {
    $manifestName = $signature.Name.Substring(0, $signature.Name.Length - 4)
    if (-not $assetNameSet.Contains($manifestName)) {
        throw "Portable signature has no matching manifest: $($signature.Name)"
    }
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
$verificationDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("neko-release-verify-" + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $verificationDirectory | Out-Null
try {
    foreach ($asset in $assets) {
        $objectUrl = '{0}/{1}/{2}/{3}' -f $ossRoot, $Product, $version, $asset.Name
        if (Test-OssObjectExists -ObjectUrl $objectUrl) {
            $existingObject = Join-Path $verificationDirectory ("oss-" + $asset.Name)
            Invoke-Checked -FilePath ossutil -Arguments @('cp', $objectUrl, $existingObject)
            if ((Get-Sha256 -Path $existingObject) -ne $assetHashes[$asset.Name]) {
                throw "Refusing to overwrite immutable OSS object with different content: $objectUrl"
            }
            Write-Host "Existing OSS asset already matches staged content: $($asset.Name)"
            continue
        }

        Write-Host "Uploading staged asset $($asset.Name)"
        Invoke-Checked -FilePath ossutil -Arguments @('cp', $asset.FullName, $objectUrl, '--meta', 'Cache-Control:public, max-age=31536000, immutable')
    }

    foreach ($asset in $assets) {
        $cdnUrl = '{0}/releases/{1}/{2}/{3}' -f $cdnRoot, [Uri]::EscapeDataString($Product), [Uri]::EscapeDataString($version), [Uri]::EscapeDataString($asset.Name)
        $downloadedAsset = Join-Path $verificationDirectory ("cdn-" + $asset.Name)
        Write-Host "Verifying CDN asset bytes $($asset.Name)"
        Invoke-Checked -FilePath curl.exe -Arguments @('--fail', '--location', '--retry', '12', '--retry-all-errors', '--retry-delay', '5', '--connect-timeout', '10', '--max-time', '1800', '--output', $downloadedAsset, $cdnUrl)
        if ((Get-Sha256 -Path $downloadedAsset) -ne $assetHashes[$asset.Name]) {
            throw "CDN returned different content for $cdnUrl"
        }
    }

    $escapedProduct = [Uri]::EscapeDataString($Product)
    $endpoint = '{0}/v1/admin/{1}/stable/sync' -f $ServiceUrl.TrimEnd('/'), $escapedProduct
    $headers = @{ Authorization = "Bearer $adminToken" }
    $body = @{ version = $version; mirror_ids = @($MirrorId) } | ConvertTo-Json -Compress
    Invoke-UpdateMirrorSync -Endpoint $endpoint -Headers $headers -Body $body
}
finally {
    Remove-Item -LiteralPath $verificationDirectory -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host "Registered mirror '$MirrorId' for $Product $Tag after CDN verification."
