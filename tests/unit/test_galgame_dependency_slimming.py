from __future__ import annotations

from collections.abc import Iterator
import importlib.util
import json
import pathlib
import re
import shutil
import sys
import tomllib
import types

import numpy as np
import pytest
import yaml


ROOT = pathlib.Path(__file__).resolve().parents[2]
RAPIDOCR_PILLOW_ROOT = ROOT / "deps" / "rapidocr_pillow"


@pytest.fixture(autouse=True, scope="module")
def _rapidocr_pillow_path() -> Iterator[None]:
    original_sys_path = list(sys.path)
    rapidocr_pillow_path = str(RAPIDOCR_PILLOW_ROOT)
    sys.path.insert(0, rapidocr_pillow_path)
    try:
        yield
    finally:
        sys.path[:] = original_sys_path


def _load_verifier():
    verifier_path = ROOT / "scripts" / "verify_rapidocr_pillow_backend.py"
    spec = importlib.util.spec_from_file_location(
        "verify_rapidocr_pillow_backend",
        verifier_path,
    )
    assert spec is not None
    assert spec.loader is not None
    verifier = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = verifier
    spec.loader.exec_module(verifier)
    return verifier


def _load_pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_galgame_group_uses_local_rapidocr_pillow_fork_without_opencv() -> None:
    pyproject = _load_pyproject()

    galgame_deps = pyproject["dependency-groups"]["galgame"]

    assert galgame_deps == ["rapidocr-pillow"]
    assert "rapidocr-pillow" in pyproject["tool"]["uv"]["sources"]
    assert pyproject["tool"]["uv"]["sources"]["rapidocr-pillow"] == {
        "path": "./deps/rapidocr_pillow"
    }


def test_rapidocr_pillow_metadata_drops_heavy_transitive_deps() -> None:
    with (ROOT / "deps" / "rapidocr_pillow" / "pyproject.toml").open("rb") as handle:
        fork_pyproject = tomllib.load(handle)

    assert fork_pyproject["project"]["name"] == "rapidocr-pillow"

    dependencies = {
        re.split(r"[<>=!~;\[ ]", dependency.strip(), maxsplit=1)[0].lower()
        for dependency in fork_pyproject["project"]["dependencies"]
    }

    assert "opencv-python" not in dependencies
    assert "opencv-python-headless" not in dependencies
    assert "shapely" not in dependencies
    assert "six" not in dependencies
    assert "tqdm" not in dependencies
    assert "pyyaml" in dependencies
    assert "pyclipper" in dependencies


def test_uv_lock_uses_rapidocr_pillow_without_opencv_or_shapely() -> None:
    with (ROOT / "uv.lock").open("rb") as handle:
        lock = tomllib.load(handle)

    packages = {package["name"]: package for package in lock["package"]}

    assert "rapidocr-pillow" in packages
    assert "rapidocr-onnxruntime" not in packages
    assert "opencv-python" not in packages
    assert "opencv-python-headless" not in packages
    assert "shapely" not in packages


def test_rapidocr_pillow_source_has_no_removed_dependency_imports() -> None:
    source_root = ROOT / "deps" / "rapidocr_pillow" / "rapidocr_onnxruntime"
    py_files = list(source_root.rglob("*.py"))
    removed_dependency_imports = re.compile(
        r"^\s*(?:from\s+(?:cv2|shapely|six|tqdm)\b|import\s+(?:cv2|shapely|six|tqdm)\b)",
        re.M,
    )

    assert py_files

    offenders: set[str] = set()
    for py_file in py_files:
        source = py_file.read_text(encoding="utf-8")
        if removed_dependency_imports.search(source):
            offenders.add(py_file.relative_to(ROOT).as_posix())

    assert sorted(offenders) == []


def test_pillow_cv_geometry_helpers_cover_shapely_and_perspective_replacements() -> None:
    from rapidocr_onnxruntime._pillow_cv import (
        dilate,
        min_area_box,
        perspective_transform_matrix,
        polygon_area,
        polygon_perimeter,
        resize,
    )

    rect = np.array([[0, 0], [4, 0], [4, 3], [0, 3]], dtype=np.float32)
    assert polygon_area(rect) == pytest.approx(12.0)
    assert polygon_perimeter(rect) == pytest.approx(14.0)

    box, min_side = min_area_box(
        np.array([[0, 0], [4, 0], [4, 3], [0, 3], [2, 1]], dtype=np.float32)
    )
    assert min_side == pytest.approx(3.0)
    assert polygon_area(box) == pytest.approx(12.0)

    collinear_box, collinear_min_side = min_area_box(
        np.array([[0, 0], [2, 0], [4, 0], [6, 0]], dtype=np.float32)
    )
    assert collinear_min_side == pytest.approx(0.0)
    assert polygon_area(collinear_box) == pytest.approx(0.0)

    src = np.array([[1, 1], [5, 1], [4, 4], [0, 3]], dtype=np.float32)
    dst = np.array([[0, 0], [10, 0], [10, 6], [0, 6]], dtype=np.float32)
    matrix = perspective_transform_matrix(src, dst)
    homogeneous_src = np.column_stack((src, np.ones(len(src))))
    mapped = homogeneous_src @ matrix.T
    mapped = mapped[:, :2] / mapped[:, 2:]

    assert np.allclose(mapped, dst)

    gray = np.arange(3 * 4, dtype=np.uint8).reshape(3, 4)
    expected_gray_resize = np.array(
        [
            [0, 0, 1, 2, 2, 3],
            [2, 2, 3, 3, 4, 5],
            [4, 5, 5, 6, 7, 7],
            [6, 7, 7, 8, 9, 9],
            [8, 8, 9, 10, 10, 11],
        ],
        dtype=np.uint8,
    )
    assert np.max(np.abs(resize(gray, (6, 5)).astype(int) - expected_gray_resize)) <= 1

    rgb = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    expected_rgb_resize = np.array(
        [
            [[0, 1, 2], [1, 2, 3], [3, 4, 5], [5, 6, 7], [6, 7, 8]],
            [[2, 3, 4], [3, 4, 5], [5, 6, 7], [7, 8, 9], [8, 9, 10]],
            [[7, 8, 9], [8, 9, 10], [10, 11, 12], [11, 12, 13], [13, 14, 15]],
            [
                [9, 10, 11],
                [10, 11, 12],
                [12, 13, 14],
                [14, 15, 16],
                [15, 16, 17],
            ],
        ],
        dtype=np.uint8,
    )
    assert np.max(np.abs(resize(rgb, (5, 4)).astype(int) - expected_rgb_resize)) <= 1

    mask = np.array(
        [
            [0, 0, 0, 0, 0],
            [0, 1, 0, 0, 0],
            [0, 0, 0, 1, 0],
            [0, 0, 0, 0, 0],
        ],
        dtype=np.uint8,
    )
    expected_cv2_dilate_2x2 = np.array(
        [
            [0, 0, 0, 0, 0],
            [0, 1, 1, 0, 0],
            [0, 1, 1, 1, 1],
            [0, 0, 0, 1, 1],
        ],
        dtype=np.uint8,
    )
    assert np.array_equal(dilate(mask, np.ones((2, 2), dtype=np.uint8)), expected_cv2_dilate_2x2)


def test_desktop_workflows_do_not_force_cv2_or_shapely_into_nuitka() -> None:
    workflow_paths = [
        ROOT / ".github" / "workflows" / "build-desktop.yml",
        ROOT / ".github" / "workflows" / "build-desktop-linux.yml",
        ROOT / "specs" / "launcher.spec",
    ]

    for workflow_path in workflow_paths:
        workflow = workflow_path.read_text(encoding="utf-8")
        assert "--include-package=cv2" not in workflow
        assert "--include-package-data=cv2" not in workflow
        assert "--include-package=shapely" not in workflow
        assert "--include-package-data=shapely" not in workflow
        assert "'cv2'" not in workflow
        assert "'shapely'" not in workflow


def test_rapidocr_pillow_verifier_covers_documented_ocr_fixture_shape() -> None:
    verifier = _load_verifier()
    verifier_path = ROOT / "scripts" / "verify_rapidocr_pillow_backend.py"

    cases = verifier._synthetic_cases()
    categories = {}
    for case in cases:
        categories[case.category] = categories.get(case.category, 0) + 1

    assert categories["zh"] >= 20
    assert categories["ja"] >= 20
    assert categories["mixed"] >= 10
    assert categories["vertical"] >= 5
    assert categories["tilted"] >= 5

    source = verifier_path.read_text(encoding="utf-8")
    assert "--helper-parity" in source
    assert "--runtime-source" in source
    assert "installed" in source
    assert "--compare-baseline-json" in source
    assert "--compare-candidate-json" in source
    assert "--real-corpus-manifest" in source
    assert "--write-real-corpus-manifest" in source


def test_rapidocr_pillow_verifier_enforces_real_corpus_manifest() -> None:
    verifier = _load_verifier()
    temp_root = ROOT / ".codex-tmp" / "test-real-corpus-manifest"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    try:
        image_dir = temp_root / "images"
        image_dir.mkdir(parents=True)

        manifest = []
        ocr_json = {}
        for category, required_count in verifier.REQUIRED_REAL_CORPUS_COUNTS.items():
            for index in range(required_count):
                image_path = image_dir / f"{category}_{index:02d}.png"
                image_path.write_bytes(b"not a real image; manifest existence check only")
                relative_path = image_path.relative_to(temp_root).as_posix()
                manifest.append({"path": relative_path, "category": category})
                ocr_json[relative_path] = f"{category}-{index}"

        manifest_path = temp_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        ocr_json_path = temp_root / "ocr.json"
        ocr_json_path.write_text(json.dumps(ocr_json), encoding="utf-8")

        results = []
        verifier.check_real_corpus_manifest(results, manifest_path, [ocr_json_path])

        result_by_name = {result.name: result for result in results}
        assert result_by_name["real_corpus_manifest_counts"].status == "PASS"
        assert result_by_name["real_corpus_manifest_files"].status == "PASS"
        assert result_by_name["real_corpus_json_coverage_ocr"].status == "PASS"

        short_manifest_path = temp_root / "short-manifest.json"
        short_manifest_path.write_text(
            json.dumps([{"path": "images/zh_00.png", "category": "zh"}]),
            encoding="utf-8",
        )
        short_results = []
        verifier.check_real_corpus_manifest(short_results, short_manifest_path)

        assert {result.name: result for result in short_results}[
            "real_corpus_manifest_counts"
        ].status == "FAIL"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_rapidocr_pillow_verifier_can_build_manifest_from_category_dirs() -> None:
    verifier = _load_verifier()
    temp_root = ROOT / ".codex-tmp" / "test-real-corpus-manifest-builder"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    try:
        corpus_root = temp_root / "corpus"
        category_dirs = {
            "chinese": "zh",
            "japanese": "ja",
            "mixed-cjk-en": "mixed",
            "vertical": "vertical",
            "extreme-tilted": "tilted",
        }
        for directory_name, category in category_dirs.items():
            category_dir = corpus_root / directory_name
            category_dir.mkdir(parents=True, exist_ok=True)
            required_count = verifier.REQUIRED_REAL_CORPUS_COUNTS[category]
            for index in range(required_count):
                (category_dir / f"{index:02d}.png").write_bytes(b"fixture")
            (category_dir / "notes.txt").write_text("ignored", encoding="utf-8")

        manifest_path = verifier.write_real_corpus_manifest(
            corpus_root,
            temp_root / "manifest.json",
        )
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
        categories = {}
        for record in records:
            categories[record["category"]] = categories.get(record["category"], 0) + 1

        assert categories == verifier.REQUIRED_REAL_CORPUS_COUNTS

        results = []
        verifier.check_real_corpus_manifest(results, manifest_path)
        assert {result.name: result for result in results}[
            "real_corpus_manifest_counts"
        ].status == "PASS"
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_rapidocr_pillow_preprocess_accumulates_resize_ratios() -> None:
    from rapidocr_onnxruntime.main import RapidOCR

    engine = object.__new__(RapidOCR)
    engine.max_side_len = 2000
    engine.min_side_len = 64

    img = np.zeros((80, 8000, 3), dtype=np.uint8)
    resized, ratio_h, ratio_w = engine.preprocess(img)

    assert resized.shape[:2] == (64, 3968)
    assert ratio_h == pytest.approx(80 / 64)
    assert ratio_w == pytest.approx(8000 / 3968)


def test_rapidocr_call_uses_per_call_thresholds_without_mutating_instance() -> None:
    from rapidocr_onnxruntime.main import RapidOCR

    engine = object.__new__(RapidOCR)
    engine.use_det = True
    engine.use_cls = False
    engine.use_rec = False
    engine.text_score = 0.5
    engine.text_det = types.SimpleNamespace(
        postprocess_op=types.SimpleNamespace(box_thresh=0.5, unclip_ratio=1.6)
    )
    engine.load_img = lambda _img: np.zeros((12, 12, 3), dtype=np.uint8)
    engine.preprocess = lambda img: (img, 1.0, 1.0)
    engine.maybe_add_letterbox = lambda img, op_record: (img, op_record)
    engine.get_crop_img_list = lambda img, boxes: [img]

    seen = {}

    def fake_auto_text_det(img, box_thresh=None, unclip_ratio=None):
        seen["box_thresh"] = box_thresh
        seen["unclip_ratio"] = unclip_ratio
        return [np.array([[0, 0], [5, 0], [5, 5], [0, 5]], dtype=np.float32)], 0.0

    engine.auto_text_det = fake_auto_text_det

    result, elapses = engine("unused", box_thresh=0.8, unclip_ratio=2.1, text_score=0.9)

    assert seen == {"box_thresh": 0.8, "unclip_ratio": 2.1}
    assert result == [[[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [0.0, 5.0]]]
    assert elapses == [0.0]
    assert engine.text_det.postprocess_op.box_thresh == 0.5
    assert engine.text_det.postprocess_op.unclip_ratio == 1.6
    assert engine.text_score == 0.5


def test_rapidocr_cli_visualization_handles_empty_and_det_only_results(monkeypatch, tmp_path) -> None:
    from rapidocr_onnxruntime import main as rapidocr_main

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"placeholder")
    writes = []
    calls = []

    class FakeVis:
        def __call__(self, img_path, boxes, txts=None, scores=None, font_path=None):
            calls.append(
                {
                    "img_path": img_path,
                    "boxes": boxes,
                    "txts": txts,
                    "scores": scores,
                    "font_path": font_path,
                }
            )
            return np.zeros((2, 2, 3), dtype=np.uint8)

    monkeypatch.setattr(rapidocr_main, "VisRes", FakeVis)
    monkeypatch.setattr(
        rapidocr_main,
        "imwrite",
        lambda path, img: writes.append((path, img.shape)),
    )

    class EmptyOCR:
        def __init__(self, **_kwargs):
            pass

        def __call__(self, *_args, **_kwargs):
            return None, None

    monkeypatch.setattr(rapidocr_main, "RapidOCR", EmptyOCR)
    monkeypatch.setattr(
        sys,
        "argv",
        ["rapidocr", "-img", str(image_path), "-vis", "--vis_save_path", str(tmp_path), "--no_cls", "--no_rec"],
    )
    rapidocr_main.main()

    assert calls == []
    assert writes == []

    class DetOnlyOCR:
        def __init__(self, **_kwargs):
            pass

        def __call__(self, *_args, **_kwargs):
            return [
                [[0, 0], [4, 0], [4, 4], [0, 4]],
                [[5, 5], [9, 5], [9, 9], [5, 9]],
            ], [0.0]

    monkeypatch.setattr(rapidocr_main, "RapidOCR", DetOnlyOCR)
    rapidocr_main.main()

    assert calls[-1]["boxes"] == [
        [[0, 0], [4, 0], [4, 4], [0, 4]],
        [[5, 5], [9, 5], [9, 9], [5, 9]],
    ]
    assert writes[-1][0] == tmp_path / "sample_vis.png"


def test_rapidocr_pillow_read_yaml_uses_safe_loader(tmp_path) -> None:
    from rapidocr_onnxruntime.utils import read_yaml

    safe_yaml = tmp_path / "safe.yaml"
    safe_yaml.write_text("Global:\n  text_score: 0.5\n", encoding="utf-8")
    assert read_yaml(safe_yaml) == {"Global": {"text_score": 0.5}}

    unsafe_yaml = tmp_path / "unsafe.yaml"
    unsafe_yaml.write_text("!!python/object/apply:os.system ['echo unsafe']\n", encoding="utf-8")
    with pytest.raises(yaml.constructor.ConstructorError):
        read_yaml(unsafe_yaml)


def test_rapidocr_pillow_rgba_conversion_uses_white_background_alpha_composite() -> None:
    from rapidocr_onnxruntime.utils import LoadImage

    rgba = np.array(
        [
            [[255, 0, 0, 255], [0, 255, 0, 128]],
            [[0, 0, 255, 0], [10, 20, 30, 64]],
        ],
        dtype=np.uint8,
    )

    bgr = LoadImage.cvt_four_to_three(rgba)

    assert np.array_equal(bgr[0, 0], np.array([0, 0, 255], dtype=np.uint8))
    assert np.array_equal(bgr[0, 1], np.array([127, 255, 127], dtype=np.uint8))
    assert np.array_equal(bgr[1, 0], np.array([255, 255, 255], dtype=np.uint8))
    assert np.array_equal(bgr[1, 1], np.array([199, 196, 194], dtype=np.uint8))


def test_rapidocr_pillow_cli_list_args_parse_comma_separated_values(monkeypatch, tmp_path) -> None:
    from rapidocr_onnxruntime.utils.parse_parameters import init_args

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rapidocr",
            "-img",
            str(image_path),
            "--cls_image_shape",
            "3,48,192",
            "--cls_label_list",
            "0,180",
            "--rec_img_shape",
            "3,48,320",
        ],
    )

    args = init_args()

    assert args.cls_image_shape == [3, 48, 192]
    assert args.cls_label_list == ["0", "180"]
    assert args.rec_img_shape == [3, 48, 320]


def test_rapidocr_pillow_update_parameters_strips_module_prefixes_consistently() -> None:
    from rapidocr_onnxruntime.utils.parse_parameters import UpdateParameters

    global_dict, det_dict, cls_dict, rec_dict = UpdateParameters().parse_kwargs(
        text_score=0.6,
        det_box_thresh=0.7,
        det_donot_use_dilation=True,
        cls_batch_num=8,
        cls_label_list=["0", "180"],
        rec_batch_num=12,
        rec_img_shape=[3, 48, 320],
    )

    assert global_dict["text_score"] == 0.6
    assert det_dict == {"box_thresh": 0.7, "use_dilation": False}
    assert cls_dict == {"batch_num": 8, "label_list": ["0", "180"]}
    assert rec_dict == {"batch_num": 12, "img_shape": [3, 48, 320]}
