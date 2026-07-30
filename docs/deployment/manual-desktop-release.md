---
title: Manual stable desktop release
description: Build, sign, test, and publish stable desktop assets without a tag-triggered cloud build.
---

# Manual stable desktop release

Stable desktop packages are built and signed on their native build hosts. Pushing
`v*` tags does not start a cloud build. The only automatic stable-release action
starts after a maintainer publishes a GitHub Release: it validates the required
Portable assets and syncs metadata to the update service.

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
x64 AppImage. Uploading verified CDN/object-storage copies is separate; set
`NEKO_UPDATE_MIRROR_IDS` only after every release asset is available on those
mirrors.
