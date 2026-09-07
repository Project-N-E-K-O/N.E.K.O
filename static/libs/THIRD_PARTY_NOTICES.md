# Third-party notices for the bundled MMD libraries

These notices cover the two three-mmd bundles below and the babylon-mmd code
embedded in the core bundle. This is not an inventory of every library in
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

## Distribution and updates

Keep these notices, the `licenses` directory, and the `/*! ... */` license
comments when copying, patching, replacing, or minifying the bundles. Include
them in both web deployments and desktop packages. The desktop workflows copy
the entire `static` directory; `scripts/check_nuitka_dist.py` also checks for
these files in the built package.

When updating a bundle, recheck the licenses and embedded dependencies of the
actual release and update this provenance record. Do not apply the root
project license in place of a third-party license.
