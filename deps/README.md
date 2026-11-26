# Vendored Local Dependencies (`deps/`)

This directory holds vendored dependencies required to guarantee reproducible builds when upstream PyPI packages are unavailable or intentionally slimmed.

## `pyncm-async` Wheels

- **Upstream project**: `pyncm-async` (`pyncm` async fork)
- **Author / Copyright**: `greats3an`
- **Upstream URLs**: `https://github.com/greats3an/pyncm` / `https://github.com/greats3an/pyncm/tree/async`
- **License**: Apache-2.0
- **Context**: `pyncm-async` was removed from PyPI (along with the upstream repository), so local wheels are vendored for reproducible `uv lock` / `uv sync` in offline and CI environments.
- **Included versions**:
  - `deps/pyncm_async-1.8.1-py3-none-any.whl` (`sha256:f0a5247c743ed4dae009d9a8ff330bd5a9f2a0e4800c5cbef459450d095d66c0`) — active version pinned in `pyproject.toml`, `requirements.txt`, and `uv.lock`.
  - `deps/pyncm_async-1.8.2-py3-none-any.whl` (`sha256:65645bb88a3c2c91eff2ac747914dc2b58c037c8e03ef55550c1f4552ae5f64b`) — archived upgrade backup (committed in `7c917acd8`; `main_routers/music_router.py` retains a Python `<3.12` compatibility note for 1.8.2).

### Checksum Verification

From the repository root:

```bash
sha256sum -c deps/SHA256SUMS
# or on macOS:
shasum -a 256 -c deps/SHA256SUMS
```

## `rapidocr_pillow/`

Local slimmed OCR package (`rapidocr-pillow`) referenced by `[tool.uv.sources]` in `pyproject.toml`. See `deps/rapidocr_pillow/` for package metadata.
