---
title: 手动发布桌面稳定版
description: 在不依赖标签触发云端构建的情况下，构建、签名、验证并发布稳定桌面资产。
---

# 手动发布桌面稳定版

稳定桌面包在各自的原生构建主机上构建并签名。推送 `v*` 标签不会触发云端构建；唯一的自动稳定版发布动作发生在维护者发布 GitHub Release 后，它只校验必需的 Portable 资产。

在每个目标平台主机上各执行一次 `scripts/build-desktop-release.ps1`。该脚本会为 Portable manifest 签名，将产物暂存到 `release-assets/<version>/`，但不会创建标签、GitHub Release、上传文件或请求更新服务。

运行前，请在对应原生主机上构建同版本 Nuitka 后端，并将其放在相邻的 `N.E.K.O.-PC/bin` 目录：Windows 为 `projectneko_server.exe`，macOS/Linux 为 `projectneko_server`。脚本会用本机构建可用的 Electron 签名身份，将该后端一并打包。

发布脚本还会复用相邻 `N.E.K.O.-PC/src/main/portable-update.js` 中受信任的 manifest 校验器。若 PC 仓库不在相邻位置，需通过 `-ManifestVerifierPath` 传入其完整路径。

```powershell
./scripts/build-desktop-release.ps1 `
  -Version 0.8.4 `
  -ManifestSigningKeyPath D:\secure\portable-manifest-ed25519.pem `
  -PreviousReleaseTag v0.8.3
```

macOS 需在每个架构的主机上执行，并显式指定架构：

```powershell
./scripts/build-desktop-release.ps1 -Version 0.8.4 `
  -Platform macos -Architecture arm64 `
  -ManifestSigningKeyPath /secure/portable-manifest-ed25519.pem `
  -PreviousReleaseTag v0.8.3
```

`-PreviousReleaseTag` 是可选项。提供后，脚本会通过 `gh release download` 读取上一版本 manifest；只有差分包小于完整包时才生成差分包。该步骤不会调用 `gh release create` 或 `gh release upload`。

在创建非 prerelease 的 GitHub Release 前，必须测试每一份暂存包。稳定 Release 必须包含 Windows x64、macOS x64/arm64、Linux x64 及 Linux x64 AppImage 的 Portable 完整包、manifest 和 manifest `.sig` 文件。

发布 GitHub Release 只触发 GitHub 侧资产校验，不会调用 N.E.K.O.-Update。收集完各原生构建主机的产物并放入 `release-assets/<version>/` 后，在本地发布主机上执行：

```powershell
$env:NEKO_UPDATE_ADMIN_TOKEN = '<secret>'
.\scripts\publish-desktop-release-assets.ps1 `
  -Tag 'v0.8.4' `
  -OssReleaseRoot 'oss://<local-bucket>/releases' `
  -CdnBaseUrl 'https://download.project-neko.cn' `
  -ServiceUrl 'https://update.project-neko.cn'
```

此脚本仅支持稳定版。它直接上传已暂存的构建产物，不会从 GitHub 下载它们。上传前会校验暂存文件名与已发布 Release 完全一致，并确认每个 Portable manifest 都有对应 `.sig` 文件。不可变 OSS 对象绝不覆盖：只有对象 SHA-256 与暂存资产相同，才允许重复执行。

在登记 `aliyun` 镜像前，脚本会从 CDN 下载每一份资产，并与暂存资产比较 SHA-256。OSS 凭证、Endpoint 和 Bucket 名仅保存在本地 `ossutil` 配置中，绝不能写入 GitHub Actions、仓库变量或仓库文件。
