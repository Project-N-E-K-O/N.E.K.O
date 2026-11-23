from collections.abc import Mapping
from dataclasses import dataclass
import json
import sys


# Script to check that the `fields.json` file that Claude creates when analyzing PDFs
# does not have overlapping bounding boxes. See forms.md.


@dataclass
class RectAndField:
    rect: list[float]
    rect_type: str
    field: dict


def _is_positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _read_pdf_dimensions(pdf_path: str) -> dict[int, tuple[float, float]]:
    """Return 1-based page_number -> (pdf_width, pdf_height) in PDF points."""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    dims: dict[int, tuple[float, float]] = {}
    for i, page in enumerate(reader.pages):
        box = page.cropbox
        width, height = float(box.width), float(box.height)
        rotation = int(getattr(page, "rotation", 0) or 0) % 360
        if rotation in (90, 270):
            width, height = height, width
        dims[i + 1] = (width, height)
    return dims


def _compute_page_scales(
    fields: dict,
    pdf_dimensions: Mapping[int, tuple[float, float]] | None,
) -> dict[int, float]:
    """Compute image-pixels-per-PDF-point scale factors keyed by 1-based page number."""
    scales: dict[int, float] = {}
    for page in fields.get("pages") or []:
        if not isinstance(page, dict):
            continue
        page_num = page.get("page_number")
        if page_num is None:
            continue
        img_w = page.get("image_width")
        img_h = page.get("image_height")
        dims = pdf_dimensions.get(page_num) if pdf_dimensions else None
        pdf_w = dims[0] if dims else page.get("pdf_width")
        pdf_h = dims[1] if dims else page.get("pdf_height")
        if _is_positive_number(img_h) and _is_positive_number(pdf_h):
            scales[page_num] = float(img_h) / float(pdf_h)
        elif _is_positive_number(img_w) and _is_positive_number(pdf_w):
            scales[page_num] = float(img_w) / float(pdf_w)
    return scales


# Returns a list of messages that are printed to stdout for Claude to read.
def get_bounding_box_messages(
    fields_json_stream,
    pdf_path: str | None = None,
    pdf_dimensions: Mapping[int, tuple[float, float]] | None = None,
) -> list[str]:
    messages = []
    fields = json.load(fields_json_stream)
    messages.append(f"Read {len(fields['form_fields'])} fields")

    if pdf_dimensions is None and pdf_path is not None:
        pdf_dimensions = _read_pdf_dimensions(pdf_path)
    page_scales = _compute_page_scales(fields, pdf_dimensions)

    def rects_intersect(r1, r2):
        disjoint_horizontal = r1[0] >= r2[2] or r1[2] <= r2[0]
        disjoint_vertical = r1[1] >= r2[3] or r1[3] <= r2[1]
        return not (disjoint_horizontal or disjoint_vertical)

    rects_and_fields = []
    for f in fields["form_fields"]:
        rects_and_fields.append(RectAndField(f["label_bounding_box"], "label", f))
        rects_and_fields.append(RectAndField(f["entry_bounding_box"], "entry", f))

    has_error = False
    for i, ri in enumerate(rects_and_fields):
        # This is O(N^2); we can optimize if it becomes a problem.
        for j in range(i + 1, len(rects_and_fields)):
            rj = rects_and_fields[j]
            if ri.field["page_number"] == rj.field["page_number"] and rects_intersect(ri.rect, rj.rect):
                has_error = True
                if ri.field is rj.field:
                    messages.append(f"FAILURE: intersection between label and entry bounding boxes for `{ri.field['description']}` ({ri.rect}, {rj.rect})")
                else:
                    messages.append(f"FAILURE: intersection between {ri.rect_type} bounding box for `{ri.field['description']}` ({ri.rect}) and {rj.rect_type} bounding box for `{rj.field['description']}` ({rj.rect})")
                if len(messages) >= 20:
                    messages.append("Aborting further checks; fix bounding boxes and try again")
                    return messages
        if ri.rect_type == "entry":
            if "entry_text" in ri.field:
                raw_font_size = ri.field["entry_text"].get("font_size", 14)
                font_size = 14 if raw_font_size is None else raw_font_size
                entry_height = ri.rect[3] - ri.rect[1]
                scale = page_scales.get(ri.field["page_number"], 1.0)
                min_height_px = font_size * scale
                if entry_height < min_height_px:
                    has_error = True
                    if scale == 1.0:
                        messages.append(f"FAILURE: entry bounding box height ({entry_height}) for `{ri.field['description']}` is too short for the text content (font size: {font_size}). Increase the box height or decrease the font size.")
                    else:
                        messages.append(f"FAILURE: entry bounding box height ({entry_height}px) for `{ri.field['description']}` is too short for the text content (font size: {font_size}pt, minimum height: {min_height_px:.1f}px). Increase the box height or decrease the font size.")
                    if len(messages) >= 20:
                        messages.append("Aborting further checks; fix bounding boxes and try again")
                        return messages

    if not has_error:
        messages.append("SUCCESS: All bounding boxes are valid")
    return messages

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        print("Usage: check_bounding_boxes.py <fields.json> [input.pdf]")
        sys.exit(1)
    # Input file should be in the `fields.json` format described in forms.md.
    pdf_arg = sys.argv[2] if len(sys.argv) == 3 else None
    with open(sys.argv[1]) as f:
        messages = get_bounding_box_messages(f, pdf_path=pdf_arg)
    for msg in messages:
        print(msg)
