from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
FORK_ROOT = ROOT / "deps" / "rapidocr_pillow"
PACKAGE_ROOT = FORK_ROOT / "rapidocr_onnxruntime"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
REQUIRED_REAL_CORPUS_COUNTS = {
    "zh": 20,
    "ja": 20,
    "mixed": 10,
    "vertical": 5,
    "tilted": 5,
}
CORPUS_CATEGORY_ALIASES = {
    "chinese": "zh",
    "cn": "zh",
    "japanese": "ja",
    "jp": "ja",
    "mixed_cjk": "mixed",
    "mixed_cjk_en": "mixed",
    "mixed_zh_en_ja": "mixed",
    "extreme_tilted": "tilted",
    "tilt": "tilted",
    "skewed": "tilted",
}


@dataclass(frozen=True)
class SyntheticCase:
    name: str
    category: str
    text: str
    rotate_degrees: float = 0.0
    vertical: bool = False


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


@dataclass(frozen=True)
class CorpusItem:
    raw_path: str
    path: Path
    category: str


def _repo_relative(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _emit_result(results: list[CheckResult], name: str, status: str, detail: str) -> None:
    results.append(CheckResult(name=name, status=status, detail=detail))


def _failures(results: Iterable[CheckResult]) -> list[CheckResult]:
    return [result for result in results if result.status == "FAIL"]


def _normalize_category(category: str) -> str:
    key = category.strip().lower().replace("-", "_")
    return CORPUS_CATEGORY_ALIASES.get(key, key)


def _synthetic_cases() -> list[SyntheticCase]:
    chinese = [
        r"\u4eca\u5929\u7684\u98ce\u5f88\u6e29\u67d4",
        r"\u8bf7\u9009\u62e9\u4e0b\u4e00\u4e2a\u9009\u9879",
        r"\u6211\u4eec\u8fd8\u4f1a\u518d\u89c1\u9762",
        r"\u8fd9\u662f\u4e00\u6bb5\u5267\u60c5\u5bf9\u8bdd",
        r"\u4f60\u542c\u89c1\u90a3\u58f0\u949f\u54cd\u4e86\u5417",
        r"\u5b58\u6863\u5df2\u5b8c\u6210",
        r"\u5979\u7ad9\u5728\u6821\u95e8\u53e3\u5fae\u7b11",
        r"\u8bf7\u628a\u8fd9\u5c01\u4fe1\u4ea4\u7ed9\u8001\u5e08",
        r"\u591c\u8272\u50cf\u6df1\u84dd\u8272\u7684\u6d77",
        r"\u6211\u60f3\u77e5\u9053\u771f\u76f8",
        r"\u65b0\u7684\u4efb\u52a1\u5df2\u7ecf\u5f00\u59cb",
        r"\u522b\u62c5\u5fc3\uff0c\u6211\u4f1a\u966a\u7740\u4f60",
        r"\u8fd9\u6761\u8857\u9053\u6bd4\u8bb0\u5fc6\u91cc\u66f4\u5b89\u9759",
        r"\u8bf7\u8f93\u5165\u89d2\u8272\u540d\u79f0",
        r"\u753b\u9762\u4e0b\u65b9\u51fa\u73b0\u4e86\u63d0\u793a",
        r"\u9ed1\u677f\u4e0a\u5199\u7740\u660e\u5929\u7684\u65e5\u7a0b",
        r"\u4ed6\u4f4e\u58f0\u8bf4\u51fa\u4e86\u7b54\u6848",
        r"\u6625\u5929\u7684\u96e8\u843d\u5728\u7a97\u53f0",
        r"\u6211\u4eec\u5df2\u7ecf\u5230\u8fbe\u7ec8\u70b9",
        r"\u8bf7\u7ee7\u7eed\u9605\u8bfb\u4e0b\u4e00\u884c",
    ]
    japanese = [
        r"\u4eca\u65e5\u306e\u98a8\u306f\u3068\u3066\u3082\u512a\u3057\u3044",
        r"\u6b21\u306e\u9078\u629e\u80a2\u3092\u9078\u3093\u3067\u304f\u3060\u3055\u3044",
        r"\u307e\u305f\u3053\u3053\u3067\u4f1a\u3048\u308b\u3088",
        r"\u3053\u308c\u306f\u7269\u8a9e\u306e\u4f1a\u8a71\u3067\u3059",
        r"\u3042\u306e\u9418\u306e\u97f3\u304c\u805e\u3053\u3048\u305f",
        r"\u30bb\u30fc\u30d6\u304c\u5b8c\u4e86\u3057\u307e\u3057\u305f",
        r"\u5f7c\u5973\u306f\u6821\u9580\u3067\u5fae\u7b11\u3093\u3060",
        r"\u3053\u306e\u624b\u7d19\u3092\u5148\u751f\u306b\u6e21\u3057\u3066",
        r"\u591c\u306f\u6df1\u3044\u9752\u306e\u6d77\u307f\u305f\u3044\u3060",
        r"\u79c1\u306f\u771f\u5b9f\u3092\u77e5\u308a\u305f\u3044",
        r"\u65b0\u3057\u3044\u30af\u30a8\u30b9\u30c8\u304c\u59cb\u307e\u3063\u305f",
        r"\u5fc3\u914d\u3057\u306a\u3044\u3067\u3001\u305d\u3070\u306b\u3044\u308b",
        r"\u3053\u306e\u901a\u308a\u306f\u8a18\u61b6\u3088\u308a\u9759\u304b\u3060",
        r"\u30ad\u30e3\u30e9\u540d\u3092\u5165\u529b\u3057\u3066\u304f\u3060\u3055\u3044",
        r"\u753b\u9762\u306e\u4e0b\u306b\u30d2\u30f3\u30c8\u304c\u51fa\u305f",
        r"\u9ed2\u677f\u306b\u660e\u65e5\u306e\u4e88\u5b9a\u304c\u3042\u308b",
        r"\u5f7c\u306f\u5c0f\u3055\u306a\u58f0\u3067\u7b54\u3048\u305f",
        r"\u6625\u306e\u96e8\u304c\u7a93\u8fba\u306b\u843d\u3061\u308b",
        r"\u79c1\u305f\u3061\u306f\u7d42\u70b9\u306b\u7740\u3044\u305f",
        r"\u6b21\u306e\u884c\u3092\u7d9a\u3051\u3066\u8aad\u3093\u3067",
    ]
    mixed = [
        r"NEKO \u7b2c1\u7ae0 Start",
        r"Save slot 03 \u4fdd\u5b58\u3057\u307e\u3057\u305f",
        r"\u30df\u30c9\u30ea: HP 120/120",
        r"\u8bf7\u70b9\u51fb Continue",
        r"Episode 5 \u661f\u7a7a\u306e\u7d04\u675f",
        r"\u4efb\u52a1 Clear! EXP +25",
        r"Auto Mode \u81ea\u52a8\u63a8\u8fdb",
        r"\u9078\u629e\u80a2 A: \u7ea6\u5b9a\u3092\u5b88\u308b",
        r"\u597d\u611f\u5ea6 +3 Affection",
        r"2026/06/11 \u8bfb\u307f\u8fbc\u307f\u4e2d",
    ]
    vertical = [
        r"\u661f\u7a7a\u306e\u7d04\u675f",
        r"\u96e8\u306e\u5e30\u308a\u9053",
        r"\u79d8\u5bc6\u306e\u90e8\u5c4b",
        r"\u590f\u306e\u8a18\u61b6",
        r"\u541b\u3068\u6b69\u304f",
    ]
    tilted = [
        r"\u659c\u3081\u306e\u53f0\u8bcd 30",
        r"\u5c0f\u3055\u306a\u5947\u8de1",
        r"\u8fdc\u3044\u7d04\u675f",
        r"\u591c\u660e\u3051\u524d\u306e\u58f0",
        r"\u7269\u8a9e\u306f\u7d9a\u304f",
    ]

    cases: list[SyntheticCase] = []
    for idx, text in enumerate(chinese, start=1):
        cases.append(SyntheticCase(f"zh_{idx:02d}", "zh", text))
    for idx, text in enumerate(japanese, start=1):
        cases.append(SyntheticCase(f"ja_{idx:02d}", "ja", text))
    for idx, text in enumerate(mixed, start=1):
        cases.append(SyntheticCase(f"mixed_{idx:02d}", "mixed", text))
    for idx, text in enumerate(vertical, start=1):
        cases.append(SyntheticCase(f"vertical_{idx:02d}", "vertical", text, vertical=True))
    for idx, text in enumerate(tilted, start=1):
        cases.append(SyntheticCase(f"tilted_{idx:02d}", "tilted", text, rotate_degrees=30.0))
    return cases


def _decode_text(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def _font_candidates() -> list[Path]:
    windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
    hiragino = "/System/Library/Fonts/" + "\u30d2\u30e9\u30ae\u30ce\u89d2\u30b4\u30b7\u30c3\u30af W3.ttc"
    return [
        windir / "Fonts" / "msyh.ttc",
        windir / "Fonts" / "meiryo.ttc",
        windir / "Fonts" / "msgothic.ttc",
        windir / "Fonts" / "simsun.ttc",
        Path(hiragino),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    ]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in _font_candidates():
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _draw_case(case: SyntheticCase, output_path: Path) -> None:
    width, height = 960, 260
    background = Image.new("RGB", (width, height), (248, 246, 240))
    draw = ImageDraw.Draw(background)
    draw.rounded_rectangle((24, 38, width - 24, height - 28), radius=12, fill=(32, 35, 42))
    draw.rectangle((36, 50, width - 36, height - 40), fill=(42, 45, 54))

    font = _load_font(42)
    text = _decode_text(case.text)
    if case.vertical:
        x, y = width // 2 - 24, 58
        for char in text:
            if char.strip():
                draw.text((x, y), char, fill=(255, 255, 255), font=font)
            y += 42
    else:
        draw.text((64, 96), text, fill=(255, 255, 255), font=font)

    if case.rotate_degrees:
        background = background.rotate(
            case.rotate_degrees,
            expand=True,
            fillcolor=(248, 246, 240),
            resample=Image.Resampling.BICUBIC,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    background.save(output_path)


def generate_synthetic_corpus(output_dir: Path) -> Path:
    cases = _synthetic_cases()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case in cases:
        image_path = output_dir / f"{case.name}.png"
        _draw_case(case, image_path)
        payload = asdict(case)
        payload["text"] = _decode_text(case.text)
        payload["path"] = _repo_relative(image_path)
        manifest.append(payload)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _manifest_records(payload: object) -> list[dict[str, object]]:
    if isinstance(payload, list):
        return [record for record in payload if isinstance(record, dict)]

    if not isinstance(payload, dict):
        raise ValueError("manifest must be a list or object")

    for key in ("items", "images", "cases"):
        records = payload.get(key)
        if isinstance(records, list):
            return [record for record in records if isinstance(record, dict)]

    records: list[dict[str, object]] = []
    for category, entries in payload.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, str):
                records.append({"category": category, "path": entry})
            elif isinstance(entry, dict):
                record = dict(entry)
                record.setdefault("category", category)
                records.append(record)
    return records


def _resolve_manifest_path(raw_path: str, manifest_dir: Path) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path
    return (manifest_dir / path).resolve()


def build_real_corpus_manifest(corpus_root: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for image_path in sorted(corpus_root.rglob("*")):
        if not image_path.is_file() or image_path.suffix.lower() not in IMAGE_SUFFIXES:
            continue

        relative_path = image_path.relative_to(corpus_root)
        category = None
        for part in relative_path.parts[:-1]:
            normalized_part = _normalize_category(part)
            if normalized_part in REQUIRED_REAL_CORPUS_COUNTS:
                category = normalized_part
                break
        if category is None:
            continue

        records.append(
            {
                "path": relative_path.as_posix(),
                "category": category,
            }
        )
    return records


def write_real_corpus_manifest(corpus_root: Path, output_path: Path) -> Path:
    records = build_real_corpus_manifest(corpus_root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output_path


def load_real_corpus_manifest(manifest_path: Path) -> list[CorpusItem]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: list[CorpusItem] = []
    for index, record in enumerate(_manifest_records(payload), start=1):
        raw_path = record.get("path") or record.get("image") or record.get("file")
        raw_category = record.get("category") or record.get("type")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError(f"manifest item {index} is missing path/image/file")
        if not isinstance(raw_category, str) or not raw_category:
            raise ValueError(f"manifest item {index} is missing category/type")
        items.append(
            CorpusItem(
                raw_path=raw_path,
                path=_resolve_manifest_path(raw_path, manifest_path.parent),
                category=_normalize_category(raw_category),
            )
        )
    return items


def _ocr_key_candidates(item: CorpusItem) -> set[str]:
    return {
        item.raw_path.replace("\\", "/"),
        item.path.as_posix(),
        _repo_relative(item.path),
    }


def check_real_corpus_manifest(
    results: list[CheckResult],
    manifest_path: Path,
    ocr_json_paths: Sequence[Path] = (),
) -> None:
    try:
        items = load_real_corpus_manifest(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        _emit_result(results, "real_corpus_manifest", "FAIL", str(exc))
        return

    counts = {category: 0 for category in REQUIRED_REAL_CORPUS_COUNTS}
    unknown_categories: dict[str, int] = {}
    for item in items:
        if item.category in counts:
            counts[item.category] += 1
        else:
            unknown_categories[item.category] = unknown_categories.get(item.category, 0) + 1

    missing_counts = {
        category: required - counts[category]
        for category, required in REQUIRED_REAL_CORPUS_COUNTS.items()
        if counts[category] < required
    }
    count_detail = ", ".join(
        f"{category}={counts[category]}/{required}"
        for category, required in REQUIRED_REAL_CORPUS_COUNTS.items()
    )
    if unknown_categories:
        count_detail += "; unknown=" + ", ".join(
            f"{category}:{count}" for category, count in sorted(unknown_categories.items())
        )
    _emit_result(
        results,
        "real_corpus_manifest_counts",
        "PASS" if not missing_counts and not unknown_categories else "FAIL",
        count_detail,
    )

    missing_files = [item for item in items if not item.path.exists()]
    _emit_result(
        results,
        "real_corpus_manifest_files",
        "PASS" if not missing_files else "FAIL",
        "all files exist"
        if not missing_files
        else "missing: " + ", ".join(_repo_relative(item.path) for item in missing_files[:20]),
    )

    for ocr_json_path in ocr_json_paths:
        try:
            ocr_payload = json.loads(ocr_json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _emit_result(
                results,
                f"real_corpus_json_coverage_{ocr_json_path.stem}",
                "FAIL",
                str(exc),
            )
            continue
        if not isinstance(ocr_payload, dict):
            _emit_result(
                results,
                f"real_corpus_json_coverage_{ocr_json_path.stem}",
                "FAIL",
                "OCR JSON must be an object mapping image path to text",
            )
            continue

        missing_json_items = [
            item for item in items if not (_ocr_key_candidates(item) & set(ocr_payload))
        ]
        _emit_result(
            results,
            f"real_corpus_json_coverage_{ocr_json_path.stem}",
            "PASS" if not missing_json_items else "FAIL",
            f"{len(items)}/{len(items)} manifest images covered"
            if not missing_json_items
            else "missing: "
            + ", ".join(item.raw_path for item in missing_json_items[:20]),
        )


def check_dependency_slimming(results: list[CheckResult]) -> None:
    import tomllib

    if not FORK_ROOT.exists():
        _emit_result(results, "fork_exists", "FAIL", f"missing {_repo_relative(FORK_ROOT)}")
        return

    with (FORK_ROOT / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    dependency_names = {
        dependency.split(";", maxsplit=1)[0]
        .replace("_", "-")
        .lower()
        .split("[", maxsplit=1)[0]
        .split("<", maxsplit=1)[0]
        .split(">", maxsplit=1)[0]
        .split("=", maxsplit=1)[0]
        .strip()
        for dependency in pyproject["project"]["dependencies"]
    }
    for forbidden in ("opencv-python", "opencv-python-headless", "shapely", "six", "tqdm"):
        status = "FAIL" if forbidden in dependency_names else "PASS"
        _emit_result(results, f"metadata_excludes_{forbidden}", status, forbidden)

    for required in ("pyyaml", "pyclipper", "pillow", "scipy", "onnxruntime"):
        status = "PASS" if required in dependency_names else "FAIL"
        _emit_result(results, f"metadata_keeps_{required}", status, required)

    cv2_shapely_offenders = []
    dead_import_offenders = []
    for py_file in PACKAGE_ROOT.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        if "import cv2" in source or "cv2." in source or "shapely" in source:
            cv2_shapely_offenders.append(_repo_relative(py_file))
        if re.search(r"^\s*(?:from\s+(?:six|tqdm)\b|import\s+(?:six|tqdm)\b)", source, re.M):
            dead_import_offenders.append(_repo_relative(py_file))
    _emit_result(
        results,
        "source_excludes_cv2_shapely",
        "PASS" if not cv2_shapely_offenders else "FAIL",
        ", ".join(cv2_shapely_offenders) if cv2_shapely_offenders else "no offenders",
    )
    _emit_result(
        results,
        "source_excludes_tqdm_six_imports",
        "PASS" if not dead_import_offenders else "FAIL",
        ", ".join(dead_import_offenders) if dead_import_offenders else "no offenders",
    )


def check_helper_parity(results: list[CheckResult]) -> None:
    sys.path.insert(0, str(FORK_ROOT))
    from rapidocr_onnxruntime import _pillow_cv as pillow_cv

    cv2_spec = importlib.util.find_spec("cv2")
    shapely_spec = importlib.util.find_spec("shapely")
    if cv2_spec is None or shapely_spec is None:
        missing = [
            name
            for name, spec in (("cv2", cv2_spec), ("shapely", shapely_spec))
            if spec is None
        ]
        _emit_result(
            results,
            "helper_parity",
            "SKIP",
            f"missing optional parity modules: {', '.join(missing)}",
        )
        return

    import cv2
    from shapely.geometry import Polygon

    rng = np.random.default_rng(20260611)
    color = rng.integers(0, 256, size=(17, 23, 3), dtype=np.uint8)
    gray = rng.integers(0, 256, size=(17, 23), dtype=np.uint8)
    mask = rng.integers(0, 2, size=(17, 23), dtype=np.uint8) * 255

    comparisons = {
        "gray_to_bgr": np.array_equal(pillow_cv.gray_to_bgr(gray), cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)),
        "rgb_to_bgr": np.array_equal(pillow_cv.rgb_to_bgr(color), cv2.cvtColor(color, cv2.COLOR_RGB2BGR)),
        "bitwise_not": np.array_equal(pillow_cv.bitwise_not(color), cv2.bitwise_not(color)),
        "bitwise_and": np.array_equal(
            pillow_cv.bitwise_and_with_mask(color, mask),
            cv2.bitwise_and(color, color, mask=mask),
        ),
        "add_uint8": np.array_equal(pillow_cv.add_uint8(color, color), cv2.add(color, color)),
        "copy_make_border": np.array_equal(
            pillow_cv.copy_make_border(color, 2, 3, 4, 5, 0),
            cv2.copyMakeBorder(color, 2, 3, 4, 5, cv2.BORDER_CONSTANT, value=0),
        ),
    }
    for name, ok in comparisons.items():
        _emit_result(results, name, "PASS" if ok else "FAIL", "pixel exact")

    points = np.array([[2.0, 1.0], [8.0, 2.0], [7.0, 6.0], [1.0, 5.0]], dtype=np.float32)
    polygon = Polygon(points)
    _emit_result(
        results,
        "polygon_area",
        "PASS" if abs(pillow_cv.polygon_area(points) - polygon.area) < 1e-9 else "FAIL",
        f"pillow={pillow_cv.polygon_area(points):.9f} shapely={polygon.area:.9f}",
    )
    _emit_result(
        results,
        "polygon_perimeter",
        "PASS" if abs(pillow_cv.polygon_perimeter(points) - polygon.length) < 1e-9 else "FAIL",
        f"pillow={pillow_cv.polygon_perimeter(points):.9f} shapely={polygon.length:.9f}",
    )

    box, min_side = pillow_cv.min_area_box(points)
    cv_rect = cv2.minAreaRect(points)
    cv_box = cv2.boxPoints(cv_rect)
    pillow_area = pillow_cv.polygon_area(box)
    cv_area = cv2.contourArea(cv_box.astype(np.float32))
    cv_min_side = min(cv_rect[1])
    _emit_result(
        results,
        "min_area_box_area",
        "PASS" if abs(pillow_area - cv_area) < 1e-4 else "FAIL",
        f"pillow={pillow_area:.6f} cv2={cv_area:.6f}",
    )
    _emit_result(
        results,
        "min_area_box_min_side",
        "PASS" if abs(min_side - cv_min_side) < 1e-4 else "FAIL",
        f"pillow={min_side:.6f} cv2={cv_min_side:.6f}",
    )

    src = np.array([[1, 1], [22, 3], [20, 15], [2, 13]], dtype=np.float32)
    dst = np.array([[0, 0], [31, 0], [31, 19], [0, 19]], dtype=np.float32)
    matrix = pillow_cv.perspective_transform_matrix(src, dst)
    cv_matrix = cv2.getPerspectiveTransform(src, dst)
    transformed = (np.column_stack((src, np.ones(len(src)))) @ matrix.T)
    transformed = transformed[:, :2] / transformed[:, 2:]
    max_point_diff = float(np.max(np.abs(transformed - dst)))
    _emit_result(
        results,
        "perspective_maps_points",
        "PASS" if max_point_diff < 1e-6 else "FAIL",
        f"max_point_diff={max_point_diff:.9f}",
    )

    resized = pillow_cv.resize(color, (31, 19))
    cv_resized = cv2.resize(color, (31, 19), interpolation=cv2.INTER_LINEAR)
    resize_max = int(np.max(np.abs(resized.astype(np.int16) - cv_resized.astype(np.int16))))
    _emit_result(
        results,
        "resize_close",
        "PASS" if resize_max <= 1 else "FAIL",
        f"maxdiff={resize_max}",
    )

    warped = pillow_cv.warp_perspective(color, matrix, (31, 19))
    inverse = np.linalg.inv(matrix)
    inverse = inverse / inverse[2, 2]
    src_start = inverse @ np.array([0.0, 0.0, 1.0])
    src_end = inverse @ np.array([31.0, 0.0, 1.0])
    src_start = src_start[:2] / src_start[2]
    src_end = src_end[:2] / src_end[2]
    src_delta = src_end - src_start
    angle = abs(np.degrees(np.arctan2(src_delta[1], src_delta[0])))
    cv_interpolation = cv2.INTER_CUBIC if angle >= 8.0 else cv2.INTER_LINEAR
    cv_warped = cv2.warpPerspective(
        color,
        cv_matrix,
        (31, 19),
        flags=cv_interpolation,
        borderMode=cv2.BORDER_REPLICATE,
    )
    warp_abs = np.abs(warped.astype(np.int16) - cv_warped.astype(np.int16))
    warp_max = int(np.max(warp_abs))
    warp_mean = float(np.mean(warp_abs))
    _emit_result(
        results,
        "warp_perspective_bounded",
        "PASS" if warp_max <= 96 and warp_mean <= 25.0 else "FAIL",
        f"maxdiff={warp_max} meandiff={warp_mean:.3f} angle={angle:.2f}",
    )


def _iter_images(image_paths: Sequence[Path]) -> list[Path]:
    images: list[Path] = []
    for image_path in image_paths:
        if image_path.is_dir():
            images.extend(
                sorted(
                    path
                    for path in image_path.rglob("*")
                    if path.suffix.lower() in IMAGE_SUFFIXES
                )
            )
        elif image_path.exists():
            images.append(image_path)
    return images


def run_ocr(
    images: Sequence[Path],
    output_json: Path | None,
    runtime_source: str,
) -> dict[str, str]:
    if runtime_source == "fork":
        sys.path.insert(0, str(FORK_ROOT))
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    results: dict[str, str] = {}
    for image_path in _iter_images(images):
        ocr_result, _ = engine(str(image_path))
        text = "".join(item[1] for item in ocr_result) if ocr_result else ""
        results[_repo_relative(image_path)] = text

    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return results


def compare_ocr_json(baseline_path: Path, candidate_path: Path) -> list[str]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    mismatches = []
    for key in sorted(set(baseline) | set(candidate)):
        if baseline.get(key) != candidate.get(key):
            mismatches.append(key)
    return mismatches


def _default_smoke_dir() -> Path:
    return Path(tempfile.gettempdir()) / "neko_rapidocr_pillow_synthetic"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the rapidocr-pillow backend dependency slimming work.",
    )
    parser.add_argument(
        "--generate-synthetic-corpus",
        type=Path,
        help="Write deterministic Chinese/Japanese/mixed OCR fixture images.",
    )
    parser.add_argument(
        "--helper-parity",
        action="store_true",
        help="Compare helper replacements against cv2/shapely if installed.",
    )
    parser.add_argument(
        "--dependency-slimming",
        action="store_true",
        help="Check fork metadata and source exclude cv2/shapely/tqdm/six.",
    )
    parser.add_argument(
        "--real-corpus-manifest",
        type=Path,
        help=(
            "Validate a real N.E.K.O OCR corpus manifest with zh/ja/mixed/vertical/"
            "tilted category counts."
        ),
    )
    parser.add_argument(
        "--write-real-corpus-manifest",
        type=Path,
        help=(
            "Scan a real corpus root for category subdirectories and write a "
            "manifest. Recognized categories: zh, ja, mixed, vertical, tilted."
        ),
    )
    parser.add_argument(
        "--real-corpus-manifest-output",
        type=Path,
        help="Output path for --write-real-corpus-manifest; defaults to <root>/manifest.json.",
    )
    parser.add_argument(
        "--ocr-images",
        nargs="*",
        type=Path,
        help="Run current rapidocr-pillow OCR over image files or directories.",
    )
    parser.add_argument(
        "--runtime-source",
        choices=("fork", "installed"),
        default="fork",
        help="Import rapidocr_onnxruntime from the local fork or the active environment.",
    )
    parser.add_argument("--ocr-output-json", type=Path, help="Write OCR text results to JSON.")
    parser.add_argument("--compare-baseline-json", type=Path, help="Baseline OCR JSON.")
    parser.add_argument("--compare-candidate-json", type=Path, help="Candidate OCR JSON.")
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run dependency slimming, helper parity, and a small OCR smoke corpus.",
    )

    args = parser.parse_args(argv)
    results: list[CheckResult] = []

    if args.all:
        args.dependency_slimming = True
        args.helper_parity = True
        if args.generate_synthetic_corpus is None:
            args.generate_synthetic_corpus = _default_smoke_dir()
        if not args.ocr_images:
            args.ocr_images = [args.generate_synthetic_corpus]

    if args.generate_synthetic_corpus is not None:
        manifest_path = generate_synthetic_corpus(args.generate_synthetic_corpus)
        _emit_result(
            results,
            "synthetic_corpus",
            "PASS",
            f"wrote {len(_synthetic_cases())} images and {_repo_relative(manifest_path)}",
        )

    if args.dependency_slimming:
        check_dependency_slimming(results)

    if args.write_real_corpus_manifest:
        output_path = args.real_corpus_manifest_output or (
            args.write_real_corpus_manifest / "manifest.json"
        )
        manifest_path = write_real_corpus_manifest(
            args.write_real_corpus_manifest,
            output_path,
        )
        records = load_real_corpus_manifest(manifest_path)
        _emit_result(
            results,
            "real_corpus_manifest_written",
            "PASS" if records else "FAIL",
            f"wrote {len(records)} entries to {_repo_relative(manifest_path)}",
        )
        if args.real_corpus_manifest is None:
            args.real_corpus_manifest = manifest_path

    if args.real_corpus_manifest:
        ocr_json_paths = [
            path
            for path in (args.compare_baseline_json, args.compare_candidate_json)
            if path is not None
        ]
        check_real_corpus_manifest(results, args.real_corpus_manifest, ocr_json_paths)

    if args.helper_parity:
        check_helper_parity(results)

    if args.ocr_images:
        images = _iter_images(args.ocr_images)
        if not images:
            _emit_result(results, "ocr_images", "FAIL", "no input images found")
        else:
            ocr_results = run_ocr(images, args.ocr_output_json, args.runtime_source)
            non_empty = sum(1 for text in ocr_results.values() if text)
            _emit_result(
                results,
                "ocr_smoke",
                "PASS" if non_empty > 0 else "FAIL",
                f"{non_empty}/{len(ocr_results)} images returned OCR text",
            )

    if args.compare_baseline_json or args.compare_candidate_json:
        if not args.compare_baseline_json or not args.compare_candidate_json:
            _emit_result(
                results,
                "ocr_json_compare",
                "FAIL",
                "both baseline and candidate JSON paths are required",
            )
        else:
            mismatches = compare_ocr_json(args.compare_baseline_json, args.compare_candidate_json)
            _emit_result(
                results,
                "ocr_json_compare",
                "PASS" if not mismatches else "FAIL",
                "all OCR texts match" if not mismatches else f"mismatches: {', '.join(mismatches[:20])}",
            )

    if not results:
        parser.print_help()
        return 2

    payload = {
        "results": [asdict(result) for result in results],
        "summary": {
            "pass": sum(1 for result in results if result.status == "PASS"),
            "skip": sum(1 for result in results if result.status == "SKIP"),
            "fail": len(_failures(results)),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if _failures(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
