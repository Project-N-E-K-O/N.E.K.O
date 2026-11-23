# Spine runtime notice

This directory contains the unmodified official `spine-webgl` IIFE from
`@esotericsoftware/spine-webgl@4.3.13`. The WebGL bundle includes the matching
`spine-core` runtime, so a second core bundle is not copied here.

- Upstream: <https://github.com/EsotericSoftware/spine-runtimes>
- Package: `@esotericsoftware/spine-webgl@4.3.13`
- Artifact: `dist/iife/spine-webgl.min.js`
- SHA-256: `FE05B399895CB4F5ACAF00CA91724CBB5F6DBA7972B53D4C7D5D9131FDE5B018`
- License: [SPINE-RUNTIMES-LICENSE-4.3.13.txt](SPINE-RUNTIMES-LICENSE-4.3.13.txt)

The editor calls `window.NekoSpineRuntime.load()` on demand when opening a
template to preview and render its Spine animation.

The Spine Runtimes License Agreement requires each user of a product that
integrates the runtimes to obtain their own Spine Editor license. Review the
full license before redistributing a build containing this file.
