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
  in `665417441` and `3440b9bb7`.
- License: MIT; full upstream text is in [THREE-MMD-LICENSE.txt](licenses/THREE-MMD-LICENSE.txt)
  and the file's leading comment, retaining both upstream copyright statements.

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
