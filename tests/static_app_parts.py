from pathlib import Path
from typing import Any


def js_part_paths(directory: Path) -> tuple[Path, ...]:
    part_paths = tuple(sorted(directory.glob("*.js")))
    assert part_paths, f"no JavaScript parts found under {directory}"
    return part_paths


def read_js_parts(
    directory: Path,
    *,
    encoding: str = "utf-8",
    contract_view: bool = True,
) -> str:
    source = "\n".join(path.read_text(encoding=encoding) for path in js_part_paths(directory))
    if contract_view:
        # Parts share promoted bindings through a closure-captured `I` object.
        # Static contracts continue to assert the original identifier-level
        # behavior; runtime harnesses load the real files via add_js_parts().
        return source.replace("I.", "")
    return source


def read_path_or_parts(path: Path, *, encoding: str = "utf-8") -> str:
    if path.is_dir():
        return read_js_parts(path, encoding=encoding)
    return path.read_text(encoding=encoding)


def add_js_parts(page: Any, directory: Path) -> None:
    for part_path in js_part_paths(directory):
        page.add_script_tag(path=str(part_path))
