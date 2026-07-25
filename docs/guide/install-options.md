---
title: Should I install N.E.K.O from Steam, GitHub Releases, or source?
description: Compare the Steam, GitHub Release, and source installation paths for Project N.E.K.O, including platform coverage, update expectations, and intended users.
seoSchemaType: WebPage
---

# Should I install N.E.K.O from Steam, GitHub Releases, or source?

Choose Steam for the simplest supported desktop distribution and Steam features, GitHub Releases when you need a published standalone asset such as Linux, and source setup when you are developing, integrating, or customizing the project.

Last fact review: **2026-07-23**. The latest stable GitHub release at that review was **v0.8.3**; always check the current release page rather than treating that version as permanent.

## Installation-channel comparison

| Channel | Platforms visible at the fact review | Best for | Important limitations |
|---|---|---|---|
| [Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_table_en) | Windows and macOS | End users who want Steam installation, Workshop, achievements, and Steam Cloud support | Early Access; platform support follows the current Steam page |
| [GitHub Releases](https://github.com/Project-N-E-K-O/N.E.K.O/releases) | Windows, Linux, and a macOS arm64 asset in v0.8.3 | Users who need standalone release assets or Linux packages | Asset names and platform coverage vary by release |
| Source checkout | Determined by current dependencies and tested environment | Contributors, integrators, custom deployments | Requires Python 3.11, `uv`, compatible Node tooling, and manual setup |
| Nightly prerelease | Depends on the workflow run | Testing recent changes | Not a stable-release promise |

The presence of Windows, macOS, and Linux artifacts in the project does not mean one download URL serves every platform. In particular, the Steam URL should not be used as the Linux download URL.

## Choose Steam when

- you want the normal desktop installation experience;
- you want Steam Workshop, achievements, or Steam Cloud features;
- your platform is listed on the current Steam store page;
- you accept that the product is in Early Access.

The base Steam application is currently free. AI-provider costs and terms remain separate; see [Is N.E.K.O free?](./cost-and-providers).

## Choose GitHub Releases when

- you need an asset published outside Steam;
- you need the Linux AppImage or tar archive offered by the current release;
- you need to inspect release notes and exact filenames;
- you are testing a version independently of Steam delivery.

At the 2026-07-23 review, [v0.8.3](https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/v0.8.3) included:

```text
N.E.K.O_0.8.3.1_win.zip
N.E.K.O_0.8.3_win.zip
N.E.K.O_0.8.3_linux.AppImage
N.E.K.O_0.8.3_linux.tar.gz
N.E.K.O_0.8.3_mac_arm64.zip
```

This list is historical evidence for that release, not a guarantee for later versions.

## Choose source setup when

- you plan to contribute code or documentation;
- you need to inspect or modify model, memory, Agent, plugin, or deployment behavior;
- you are building a custom local or server deployment;
- you can manage the required development tools.

Current source development requires:

- Python **3.11**;
- [`uv`](https://docs.astral.sh/uv/) for Python environments and commands;
- Node compatible with the repository lockfiles—the plugin manager currently requires `^20.19.0 || >=22.12.0`;
- the platform-specific dependencies required by the features you enable.

Start with [Prerequisites](./prerequisites), [Development Setup](./dev-setup), and [Quick Start](./quick-start).

## Stable release versus nightly output

The cross-platform workflow can generate Windows, macOS, and Linux output. Scheduled output is a **nightly prerelease**. A successful nightly artifact is useful for testing, but it should not be presented as the latest stable release or as a long-term supported package.

## Before installing

1. Confirm the platform and architecture on the selected channel.
2. Read the current release notes or Steam Early Access notice.
3. Decide whether you will use the free remote profile, your own provider key, or local components.
4. Review [local and offline boundaries](./local-and-offline).
5. Review [technical data flow and privacy controls](./data-and-privacy).

> Ready for the standard desktop path? [Install N.E.K.O. from Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_footer_en), or use the alternatives above if Steam is not right for your platform.

## Related documentation

- [Prerequisites](./prerequisites)
- [Development Setup](./dev-setup)
- [Quick Start](./quick-start)
- [Deployment Overview](/deployment/)
- [GitHub Releases](https://github.com/Project-N-E-K-O/N.E.K.O/releases)
- [Steam store page](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_source_en)
