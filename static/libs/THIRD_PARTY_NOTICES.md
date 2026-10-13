# Third-party notices for the bundled MMD libraries and visit vendor SDKs

These notices cover the two three-mmd bundles below, the babylon-mmd code
embedded in the core bundle, and the two real-time SDKs that the visit
transport page loads (`trtc.js`, `livekit-client.umd.js`). This is not an inventory of every library in
`static/libs`. These components retain their upstream licenses; the project's
root Apache-2.0 license does not replace them.

## @moeru/three-mmd

- File: `three-mmd.module.js`
- Source: <https://github.com/moeru-ai/three-mmd>
- Version: `0.1.0-beta.3`. Before the license banners were added, this file
  matched `package/dist/index.js` in the npm release after normalizing line
  endings and the trailing newline.
- Release: <https://registry.npmjs.org/@moeru/three-mmd/-/three-mmd-0.1.0-beta.3.tgz>
- Local changes: license banners only; the JavaScript implementation is unchanged.
- License: MIT; full upstream text is in [THREE-MMD-LICENSE.txt](licenses/THREE-MMD-LICENSE.txt)
  and the file's leading comment, including the copyrights of the three.js
  authors (2010-2024) and Moeru AI (2025).

## @moeru/three-mmd-physics-ammo

- File: `three-mmd-physics-ammo.module.js`
- Source: <https://github.com/moeru-ai/three-mmd>
- Version: locally modified `0.1.0-beta` series; the exact originating release
  was not recorded and has not been established by a byte-for-byte match.
- License reference: <https://registry.npmjs.org/@moeru/three-mmd-physics-ammo/-/three-mmd-physics-ammo-0.1.0-beta.3.tgz>
  (`package/LICENSE.md`; the same MIT text is also present in beta.1 and beta.2).
- Local changes: physics constraint limits and damping, rigid-body behavior,
  floor/distance handling, and model-rotation fixes. The existing implementation
  is preserved; this license update only prepends a comment.
- Local history: introduced in N.E.K.O. commit `450e78744`, subsequently modified
  in `339bda9c0`, `9487b1136`, `665417441`, and `3440b9bb7` (in that order;
  verified against the repository's full file history, not a shallow clone).
- License: MIT; full upstream text is in [THREE-MMD-LICENSE.txt](licenses/THREE-MMD-LICENSE.txt)
  and the file's leading comment, retaining both upstream copyright statements.

### Reproducing the locally patched physics bundle

The exact pre-notice file is available at N.E.K.O. commit
[`3440b9bb77ae6f02c966f6acb9a5f8804ba016a6`](https://github.com/Project-N-E-K-O/N.E.K.O/blob/3440b9bb77ae6f02c966f6acb9a5f8804ba016a6/static/libs/three-mmd-physics-ammo.module.js).
Its Git blob bytes (UTF-8, LF line endings, including the final newline) have
SHA-256 `135190d7ff9618930bfb7accb89bf133fc805e013d11ac30e85970a2c5c1f11b`.
This identifies our locally patched bundle, not an unmodified npm beta.3
artifact. The beta.3 link above is a license reference only.

To reconstruct its local modification history, start with this file at
`450e78744d1367ebf4db9357304e469e0010d9be`, which already contains local changes,
then apply only this file's diffs from `339bda9c0`, `9487b1136`, `665417441`, and
`3440b9bb7`, in order, using the full Git history. Alternatively, retrieve the
file directly at the full commit linked above. To compare the current bundle,
remove only its leading `/*! ... */` license comment and the newline immediately
following the closing `*/`, normalize CRLF to LF without trimming the final
newline, and compute SHA-256. The result must match the digest above. These
steps reproduce the known local artifact; they do not establish the missing
original upstream release or its pre-import patch history.

## babylon-mmd (embedded portions)

- File: `three-mmd.module.js` embeds parser utilities, PMD/PMX/VMD data and
  readers, and shared toon texture data. The bundle's region markers identify
  `babylon-mmd@1.0.0`.
- Source: <https://github.com/noname0310/babylon-mmd>
- Version: `1.0.0`.
- Release: <https://registry.npmjs.org/babylon-mmd/-/babylon-mmd-1.0.0.tgz>
- Copyright (c) 2024 noname.
- License: MIT; full text from `package/LICENSE` is retained in
  [BABYLON-MMD-LICENSE.txt](licenses/BABYLON-MMD-LICENSE.txt) and the core
  bundle's leading comment.

## trtc-sdk-v5 (visit transport, mainland China)

- File: `trtc.js`
- Source: <https://github.com/LiteAVSDK/TRTC_Web>
- Version: `5.20.1`, pinned. The file is byte-for-byte `package/trtc.js` of
  the npm release.
- Release: <https://registry.npmjs.org/trtc-sdk-v5/-/trtc-sdk-v5-5.20.1.tgz>
- SHA-256: `9a79b7f20e258aed4579d352d59ac2482d58937e534417a68b2e61e98f4a6c25`
  (the same digest, base64-encoded, is the `integrity` attribute that
  `static/visit/transport/loader.js` sets when it inserts the script).
- Local changes: none.
- License: ISC (`package.json`; the release ships no separate license file).
  The ISC text applied to the declared author is in
  [TRTC.LICENSE](licenses/TRTC.LICENSE), together with the embedded
  dependencies (core-js MIT, webrtc-adapter BSD-3-Clause).
- Loaded only inside the same-origin visit transport iframe, only when the
  backend's credentials select `transport: 'trtc'`; it is never referenced by a
  template. The iframe is not an isolation boundary, so this file is treated as
  privileged code: never loaded from a CDN, and upgrades go through the same
  review as any new third-party code (record the new version and digest here
  and in `loader.js`, and attach a change summary to the pull request).

## livekit-client (visit transport, outside mainland China)

- File: `livekit-client.umd.js`
- Source: <https://github.com/livekit/client-sdk-js>
- Version: `2.22.3`, pinned. The file is byte-for-byte
  `package/dist/livekit-client.umd.js` of the npm release (its trailing
  `sourceMappingURL` comment is kept; the map is not shipped).
- Release: <https://registry.npmjs.org/livekit-client/-/livekit-client-2.22.3.tgz>
- SHA-256: `7fa17e37af5e996d8a25f15a637dcc0620215bc01b394e5d209f726afe7dc04d`
  (also pinned as the `integrity` attribute in `loader.js`).
- Local changes: none.
- License: Apache-2.0; full text from `package/LICENSE` is in
  [LIVEKIT.LICENSE](licenses/LIVEKIT.LICENSE). The bundle embeds its declared
  dependencies (`@livekit/protocol`, `@livekit/mutex`, `events`, `jose`,
  `loglevel`, `machina`, `sdp-transform`, `tslib`, `typed-emitter`,
  `webrtc-adapter`) under their own permissive licenses.
- Loaded the same way as `trtc.js`, only for `transport: 'livekit'`.

## Distribution and updates

Keep these notices, the `licenses` directory, and the `/*! ... */` license
comments when copying, patching, replacing, or minifying the bundles. Include
them in both web deployments and desktop packages. The desktop workflows copy
the entire `static` directory; `scripts/check_nuitka_dist.py` also checks for
these files in the built package.

When updating a bundle, recheck the licenses and embedded dependencies of the
actual release and update this provenance record. Do not apply the root
project license in place of a third-party license.
