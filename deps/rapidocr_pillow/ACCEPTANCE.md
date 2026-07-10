# rapidocr-pillow acceptance record

This records the downstream N.E.K.O verification status for the local
`rapidocr-pillow` fork.

## Upstream

- Tracking issue: https://github.com/RapidAI/RapidOCR/issues/683
- Upstream PR: not submitted yet. The issue asks whether maintainers want an
  optional Pillow backend PR or prefer downstream maintenance.
- Downstream fork owner: N.E.K.O maintainers until upstream accepts an optional
  Pillow backend. Re-check the upstream issue before release and before any
  future RapidOCR / ONNX model compatibility update.

## Verified

Commands run from the N.E.K.O repository root:

```powershell
uv lock --check
uv run pytest tests/unit/test_galgame_dependency_slimming.py plugin/tests/unit/plugins/test_galgame_rapidocr_support.py plugin/tests/unit/plugins/test_study_companion_service_ui_api.py plugin/tests/unit/plugins/test_study_companion_study_ocr_pipeline.py -q
uv run pytest plugin/tests/integration/test_galgame_bridge_ui_routes.py -q
uv run python scripts/verify_rapidocr_pillow_backend.py --dependency-slimming
uv run --isolated --with numpy --with pillow --with scipy --with opencv-python-headless==4.11.0.86 --with shapely==2.1.2 --with pyclipper --with pyyaml --with onnxruntime python scripts/verify_rapidocr_pillow_backend.py --helper-parity
uv run python scripts/verify_rapidocr_pillow_backend.py --compare-baseline-json .codex-tmp\rapidocr-synthetic-fixtures\rapidocr-original-4.11-output.json --compare-candidate-json .codex-tmp\rapidocr-synthetic-fixtures\rapidocr-pillow-installed-output.json
```

Upstream RapidOCR v1.4.4 ONNXRuntime tests were run against this fork with
`PYTHONPATH` pointing at `deps\rapidocr_pillow`. The temporary test venv includes
`opencv-python-headless` only because upstream `test_ort.py` imports `cv2` at
module import time and uses it for two image-loading fixtures:

```powershell
$env:PYTHONPATH = (Resolve-Path deps\rapidocr_pillow).Path
$env:NO_PROXY = '*'
$env:no_proxy = '*'
.codex-tmp\rapidocr-upstream-ort-venv\Scripts\python.exe -m pytest .codex-tmp\rapidocr-upstream-v1.4.4-c3370580153247079a49079d97cba5b2\python\tests\test_ort.py -q --import-mode=importlib -k "not test_long_img"
.codex-tmp\rapidocr-upstream-ort-venv\Scripts\python.exe -m pytest .codex-tmp\rapidocr-upstream-v1.4.4-c3370580153247079a49079d97cba5b2\python\tests\test_ort.py::test_long_img -q --import-mode=importlib
```

Upstream `test_paddle.py` and `test_vino.py` target the separate
`rapidocr_paddle` and `rapidocr_openvino` packages. They are not part of this
`rapidocr_onnxruntime` drop-in fork.

Current evidence:

- `rapidocr-pillow` metadata excludes OpenCV, Shapely, six, and tqdm.
- `rapidocr-pillow` metadata keeps PyYAML and pyclipper.
- Fork source contains no `cv2` or `shapely` usage.
- `uv run --group galgame` resolves `cv2` and `shapely` to `None`.
- Desktop GitHub Actions and `specs/launcher.spec` do not force `cv2` or
  `shapely` into Nuitka/PyInstaller bundles.
- Helper parity checks pass against OpenCV/Shapely reference behavior in an
  isolated environment: 13 pass, 0 fail.
- Synthetic OCR text output matches upstream
  `rapidocr-onnxruntime==1.4.4` plus `opencv-python==4.11.0.86` exactly on 60
  generated samples:
  - 20 Chinese-like samples
  - 20 Japanese-like samples
  - 10 mixed CJK/English samples
  - 5 vertical samples
  - 5 tilted samples
- Upstream RapidOCR v1.4.4 `python/tests/test_ort.py` ONNXRuntime coverage
  passes against the fork when split around the external GitHub release asset
  download: 27 local tests passed, and `test_long_img` passed separately with
  proxy bypass enabled.
- N.E.K.O galgame/study OCR pipeline coverage passes:
  - `test_galgame_dependency_slimming.py`: 9 passed
  - galgame RapidOCR support plus study service/OCR pipeline tests: 33 passed
  - galgame bridge UI route integration tests: 29 passed

## Remaining acceptance gap

The v2 plan requires real N.E.K.O screenshot corpus validation:

- 20+ Chinese screenshots
- 20+ Japanese galgame screenshots
- 10+ mixed Chinese/English/Japanese screenshots
- 5+ vertical text screenshots
- 5+ extreme tilted screenshots

No in-repository corpus matching those requirements was found. The repository
currently contains UI/documentation screenshots and galgame event/session JSON,
but not real OCR screenshot fixtures with matching upstream baseline output.

To finish this gate, collect the real screenshots into a directory outside
`.codex-tmp`, add a UTF-8 manifest, generate an upstream baseline JSON with
`rapidocr-onnxruntime==1.4.4` and OpenCV 4.11, then compare. The manifest may
be a list of objects:

```json
[
  { "path": "zh/001.png", "category": "zh" },
  { "path": "ja/001.png", "category": "ja" }
]
```

Accepted categories are `zh`, `ja`, `mixed`, `vertical`, and `tilted`; relative
paths are resolved from the manifest directory. If the corpus is arranged with
category directories, the verifier can write the manifest first. Directory
aliases such as `chinese`, `japanese`, `mixed-cjk-en`, and `extreme-tilted` are
normalized to the required category names.

```powershell
uv run python scripts/verify_rapidocr_pillow_backend.py --write-real-corpus-manifest path\to\real-corpus
uv run --group galgame python scripts/verify_rapidocr_pillow_backend.py --runtime-source installed --ocr-images path\to\real-corpus --ocr-output-json path\to\rapidocr-pillow-output.json
uv run python scripts/verify_rapidocr_pillow_backend.py --compare-baseline-json path\to\rapidocr-original-4.11-output.json --compare-candidate-json path\to\rapidocr-pillow-output.json
uv run python scripts/verify_rapidocr_pillow_backend.py --real-corpus-manifest path\to\manifest.json --compare-baseline-json path\to\rapidocr-original-4.11-output.json --compare-candidate-json path\to\rapidocr-pillow-output.json
```
