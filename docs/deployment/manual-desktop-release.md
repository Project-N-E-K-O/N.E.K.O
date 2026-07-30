---
title: Manual stable desktop release
description: Build, sign, test, and publish stable desktop assets without a tag-triggered cloud build.
---

# Manual stable desktop release

Stable desktop packages are built and signed on their native build hosts. Pushing
`v*` tags does not start a cloud build. The only automatic stable-release action
starts after a maintainer publishes a GitHub Release: it validates the required
Portable assets only.

Run `scripts/build-desktop-release.ps1` once on each target host. It signs the
Portable manifests, stages the resulting assets under `release-assets/<version>/`,
and never creates a tag, GitHub Release, upload, or update-service request.

Before running it, build the matching Nuitka backend on the native host and put
it in the adjacent `N.E.K.O.-PC/bin` directory (`projectneko_server.exe` on
Windows, `projectneko_server` on macOS/Linux). The script packages that backend
with the locally available Electron signing identity.

```powershell
./scripts/build-desktop-release.ps1 `
  -Version 0.8.4 `
  -ManifestSigningKeyPath D:\secure\portable-manifest-ed25519.pem `
  -PreviousReleaseTag v0.8.3
```

For macOS, run PowerShell on each architecture and select it explicitly:

```powershell
./scripts/build-desktop-release.ps1 -Version 0.8.4 `
  -Platform macos -Architecture arm64 `
  -ManifestSigningKeyPath /secure/portable-manifest-ed25519.pem `
  -PreviousReleaseTag v0.8.3
```

The `-PreviousReleaseTag` option is optional. When supplied, the script reads
the prior manifest through `gh release download` and creates a differential
package when it is smaller than the full package. It never calls `gh release
create` or `gh release upload`.

Test every staged package before creating a non-prerelease GitHub Release. A
published stable release must include these Portable full packages, manifests,
and manifest `.sig` files for Windows x64, macOS x64/arm64, Linux x64, and Linux
x64 AppImage.

Publishing the Release runs GitHub-side asset validation only; it does not call
N.E.K.O.-Update. After every native build has been collected under
`release-assets/<version>/`, run the following on the local release host:

```powershell
$env:NEKO_UPDATE_ADMIN_TOKEN = '<secret>'
.\scripts\publish-desktop-release-assets.ps1 `
  -Tag 'v0.8.4' `
  -OssReleaseRoot 'oss://<local-bucket>/releases' `
  -CdnBaseUrl 'https://download.project-neko.cn' `
  -ServiceUrl 'https://update.project-neko.cn'
```

The script supports stable releases only. It uploads the already staged build
artifacts directly and never downloads them from GitHub. Before upload it verifies
that staged filenames exactly match the published Release and that every Portable
manifest has its matching `.sig` file. Existing immutable OSS objects are never
overwritten: a rerun is allowed only when their SHA-256 matches the staged asset.
Before registering the `aliyun` mirror, the script downloads every CDN asset and
compares its SHA-256 with the staged asset. OSS credentials, endpoints, and Bucket
names stay exclusively in the local ossutil configuration and are never added to
GitHub Actions, repository variables, or repository files.
