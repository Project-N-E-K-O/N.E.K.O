# rapidocr-pillow

Local N.E.K.O fork of `rapidocr-onnxruntime==1.4.4`.

The import package remains `rapidocr_onnxruntime` so the existing lazy runtime
loader continues to work unchanged. This fork removes the heavy opencv and
shapely dependency chain by replacing the small set of image-processing calls
with Pillow, numpy, and scipy equivalents.

Upstream tracking: https://github.com/RapidAI/RapidOCR/issues/683

Downstream verification status is tracked in `ACCEPTANCE.md`.

## Dependency policy

Kept runtime dependencies:

- `numpy`
- `onnxruntime`
- `Pillow`
- `pyclipper`
- `PyYAML`
- `scipy`

Removed dependencies:

- `opencv-python`
- `opencv-python-headless`
- `shapely`
- `six`
- `tqdm`

`PyYAML` is intentionally retained because RapidOCR's `read_yaml()` path uses it
for runtime configuration.

## Verification

From the N.E.K.O repository root:

```powershell
uv run pytest tests/unit/test_galgame_dependency_slimming.py -q
uv run python scripts/verify_rapidocr_pillow_backend.py --dependency-slimming
uv run python scripts/verify_rapidocr_pillow_backend.py --generate-synthetic-corpus .codex-tmp\rapidocr-synthetic-fixtures --ocr-images .codex-tmp\rapidocr-synthetic-fixtures
```

Optional parity checks need OpenCV and Shapely in an isolated environment:

```powershell
$env:PYTHONPATH = (Resolve-Path 'deps\rapidocr_pillow').Path
uv run --isolated --with numpy --with pillow --with scipy --with opencv-python-headless==4.11.0.86 --with shapely==2.1.2 --with pyclipper --with pyyaml --with onnxruntime python scripts/verify_rapidocr_pillow_backend.py --helper-parity
```

Upstream RapidOCR v1.4.4 ONNXRuntime tests can also be run against the fork in
an isolated venv. OpenCV is present there only for upstream test fixtures; it is
not part of the galgame dependency group:

```powershell
$env:PYTHONPATH = (Resolve-Path deps\rapidocr_pillow).Path
$env:NO_PROXY = '*'
$env:no_proxy = '*'
.codex-tmp\rapidocr-upstream-ort-venv\Scripts\python.exe -m pytest path\to\RapidOCR\python\tests\test_ort.py -q --import-mode=importlib
```

For OCR output comparison, generate a baseline with upstream
`rapidocr-onnxruntime==1.4.4` plus OpenCV 4.11 in a temporary environment, then
compare JSON output exactly:

```powershell
uv run python scripts/verify_rapidocr_pillow_backend.py --compare-baseline-json .codex-tmp\rapidocr-synthetic-fixtures\rapidocr-original-4.11-output.json --compare-candidate-json .codex-tmp\rapidocr-synthetic-fixtures\rapidocr-pillow-installed-output.json
```

For the real N.E.K.O screenshot acceptance gate, create a UTF-8 JSON manifest
next to the screenshots:

```json
[
  { "path": "zh/001.png", "category": "zh" },
  { "path": "ja/001.png", "category": "ja" }
]
```

Accepted categories are `zh`, `ja`, `mixed`, `vertical`, and `tilted`. The
verifier enforces the v2 plan counts and can also check that baseline/candidate
OCR JSON output covers the same manifest images:

```powershell
uv run python scripts/verify_rapidocr_pillow_backend.py --write-real-corpus-manifest path\to\real-corpus
uv run python scripts/verify_rapidocr_pillow_backend.py --real-corpus-manifest path\to\manifest.json --compare-baseline-json path\to\rapidocr-original-4.11-output.json --compare-candidate-json path\to\rapidocr-pillow-output.json
```

After editing this local fork, reinstall the path package into the active venv:

```powershell
uv sync --group galgame --reinstall-package rapidocr-pillow
```
