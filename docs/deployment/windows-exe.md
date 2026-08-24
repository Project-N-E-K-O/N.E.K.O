# Windows Desktop Builds

## Stable end-user path

Install N.E.K.O. from the [Steam store](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_deployment&utm_content=install_en), launch through Steam, and configure providers in the desktop/Web UI.

The Python-backend executable alone does not provide the Electron windows, tray, Steam integration, routes, or updater. Do not confuse backend-only and desktop artifacts.

## Nightly artifacts

`.github/workflows/build-desktop.yml` builds a Windows x64 Electron artifact and a separate Python-backend artifact. Scheduled runs update the repository's `nightly` prerelease only when required build stages succeed.

Nightlies are unsigned testing builds, can be replaced by the next run, and are not a stable or auto-update channel. Download only from the project's GitHub Releases, verify the built commit, and back up the N.E.K.O. data root.

## Package composition

The desktop workflow combines:

- the Electron frontend from the configured N.E.K.O.-PC repository/revision;
- this repository's Nuitka standalone backend;
- config, templates, static assets, plugins, local embedding/tiktoken assets, and browser resources required by packaging checks.

Preferred ports may change when occupied. Automation should read desktop status/port configuration rather than hardcode 48911.
