"""read / write / edit: output format, four-level matching, encoding and newlines."""

from __future__ import annotations

import codecs
from pathlib import Path

import pytest

from nekoclaw.runtime.tools import seek, textio
from nekoclaw.runtime.tools.base import ToolError, validate_arguments
from nekoclaw.runtime.tools.fs import (
    apply_edit,
    build_read_window,
    edit_file,
    format_read_output,
    read_file,
    write_file,
)
from nekoclaw.runtime.tools import tool_schemas

EN_DASH = chr(0x2013)
LEFT_QUOTE = chr(0x201C)
RIGHT_QUOTE = chr(0x201D)
NBSP = chr(0x00A0)


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------


def test_read_numbers_lines_and_reports_the_end(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"one\ntwo\nthree\n")
    assert read_file(path, offset=1, limit=2000) == (
        f"<path>{path}</path>\n<type>file</type>\n<content>\n"
        "1: one\n2: two\n3: three\n\n(End of file - total 3 lines)\n</content>"
    )


def test_read_window_footer_points_at_the_next_offset(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("\n".join(f"line {n}" for n in range(1, 11)), encoding="utf-8")
    text = read_file(path, offset=3, limit=4)
    assert "3: line 3\n4: line 4\n5: line 5\n6: line 6\n\n" in text
    assert "(Showing lines 3-6 of 10. Use offset=7 to continue.)" in text
    assert "7: line 7" not in text


def test_read_caps_long_lines_and_total_bytes() -> None:
    window = build_read_window(["x" * 2500, "short"], offset=1, limit=2000, display="f")
    assert window.lines[0][1] == "x" * 2000 + "... (line truncated to 2000 chars)"

    lines = ["y" * 1000 for _ in range(200)]
    window = build_read_window(lines, offset=1, limit=2000, display="f")
    assert window.truncated_by_bytes
    shown = len(window.lines)
    assert shown < 200
    assert format_read_output("f", window).endswith(
        f"(Output capped. Showing lines 1-{shown}. Use offset={shown + 1} to continue.)\n</content>"
    )


def test_read_errors_use_the_stable_wording(tmp_path: Path) -> None:
    missing = tmp_path / "missing.txt"
    with pytest.raises(ToolError, match=r'cannot read ".*missing.txt": not found'):
        read_file(missing, offset=1, limit=10)
    with pytest.raises(ToolError, match="not a regular file"):
        read_file(tmp_path, offset=1, limit=10)
    binary = tmp_path / "b.bin"
    binary.write_bytes(bytes([1, 2, 0, 3]))
    with pytest.raises(ToolError, match="binary file"):
        read_file(binary, offset=1, limit=10)
    short = tmp_path / "s.txt"
    short.write_text("a\nb\n", encoding="utf-8")
    with pytest.raises(ToolError, match=r"offset 9 is out of range for .* \(2 lines\)"):
        read_file(short, offset=9, limit=10)
    empty = tmp_path / "e.txt"
    empty.write_bytes(b"")
    assert "(End of file - total 0 lines)" in read_file(empty, offset=1, limit=10)


# ---------------------------------------------------------------------------
# edit: literal and relaxed matching
# ---------------------------------------------------------------------------


def test_edit_replaces_a_unique_literal_match() -> None:
    outcome = apply_edit("a = 1\nb = 2\n", "b = 2", "b = 3", replace_all=False, display="f")
    assert outcome.content == "a = 1\nb = 3\n"
    assert (outcome.replacements, outcome.relaxed_level) == (1, None)


def test_edit_rejects_an_ambiguous_match_and_names_the_count() -> None:
    with pytest.raises(ToolError) as error:
        apply_edit("x\nx\nx\n", "x", "y", replace_all=False, display="f.py")
    assert str(error.value) == (
        'old_string matched 3 times in "f.py"; provide a more specific old_string or set '
        "replace_all to true"
    )
    outcome = apply_edit("x\nx\nx\n", "x", "y", replace_all=True, display="f.py")
    assert outcome.content == "y\ny\ny\n" and outcome.replacements == 3


def test_edit_reports_text_that_is_not_there() -> None:
    with pytest.raises(ToolError, match='old_string was not found in "f"'):
        apply_edit("alpha\n", "omega", "x", replace_all=False, display="f")


@pytest.mark.parametrize(
    ("content", "old", "level"),
    [
        # Level 2: the file has trailing whitespace the model did not write.
        ("def f():   \n    return 1\t\n", "def f():\n    return 1\n", "trailing_whitespace"),
        # Level 3: the model's indentation differs from the file's.
        ("    if x:\n        go()\n", "if x:\n    go()\n", "surrounding_whitespace"),
        # Level 4: typographic punctuation in the file, ASCII from the model.
        (
            f"title = {LEFT_QUOTE}a {EN_DASH} b{RIGHT_QUOTE}\n",
            'title = "a - b"\n',
            "unicode_punctuation",
        ),
        (f"x{NBSP}={NBSP}1\n", "x = 1\n", "unicode_punctuation"),
    ],
)
def test_edit_falls_back_through_the_relaxed_levels(content: str, old: str, level: str) -> None:
    outcome = apply_edit(content, old, "REPLACED\n", replace_all=False, display="f")
    assert outcome.relaxed_level == level
    assert outcome.content == "REPLACED\n"
    assert outcome.replacements == 1


def test_relaxed_edit_replaces_only_the_matched_lines() -> None:
    content = "keep 1\n\ttarget()   \nkeep 2\n"
    outcome = apply_edit(content, "    target()", "changed()", replace_all=False, display="f")
    assert outcome.relaxed_level == "surrounding_whitespace"
    # No trailing newline in old_string: the line's own newline stays.
    assert outcome.content == "keep 1\nchanged()\nkeep 2\n"


def test_relaxed_edit_counts_matches_and_honours_replace_all() -> None:
    content = "  a()  \nmid\n\ta()  \n"
    with pytest.raises(ToolError, match="old_string matched 2 times"):
        apply_edit(content, "a()\n", "b()\n", replace_all=False, display="f")
    outcome = apply_edit(content, "a()\n", "b()\n", replace_all=True, display="f")
    assert outcome.content == "b()\nmid\nb()\n"
    assert outcome.replacements == 2


def test_stricter_level_wins_over_a_looser_one() -> None:
    # Line 1 matches after rstrip, line 3 only after strip: only line 1 counts.
    lines = ["value  ", "other", "   value"]
    assert seek.find_block(lines, ["value"]) == ("trailing_whitespace", [0])
    assert seek.find_block(["value", "value"], ["value"]) == ("exact", [0, 1])
    assert seek.find_block(["a"], ["a", "b"]) is None
    assert seek.find_block(["a"], []) is None


def test_edit_result_says_when_a_relaxed_match_was_used(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_bytes(b"def f():\n    return 1   \n")
    exact = edit_file(path, "def f():", "def g():", replace_all=False)
    assert exact == f"The file {path} has been updated successfully."
    relaxed = edit_file(path, "return 1\n", "return 2\n", replace_all=False)
    assert relaxed.startswith(f"The file {path} has been updated successfully.")
    assert "did not match exactly" in relaxed
    assert "ignoring leading and trailing whitespace" in relaxed
    assert path.read_bytes() == b"def g():\nreturn 2\n"


# ---------------------------------------------------------------------------
# Encoding and newlines survive a round trip
# ---------------------------------------------------------------------------


def test_edit_keeps_crlf_line_endings(tmp_path: Path) -> None:
    path = tmp_path / "win.txt"
    path.write_bytes(b"first\r\nsecond\r\nthird\r\n")
    # The model writes LF; the file keeps CRLF.
    edit_file(path, "second\n", "2nd\nextra\n", replace_all=False)
    assert path.read_bytes() == b"first\r\n2nd\r\nextra\r\nthird\r\n"


def test_edit_keeps_a_utf8_bom_and_lf(tmp_path: Path) -> None:
    path = tmp_path / "bom.txt"
    path.write_bytes(codecs.BOM_UTF8 + "héllo\nworld\n".encode("utf-8"))
    edit_file(path, "world", "wörld", replace_all=False)
    assert path.read_bytes() == codecs.BOM_UTF8 + "héllo\nwörld\n".encode("utf-8")


def test_edit_keeps_utf16_with_bom(tmp_path: Path) -> None:
    path = tmp_path / "u16.txt"
    path.write_bytes(codecs.BOM_UTF16_LE + "alpha\r\nbeta\r\n".encode("utf-16-le"))
    assert "1: alpha\n2: beta" in read_file(path, offset=1, limit=10)
    edit_file(path, "beta", "gamma", replace_all=False)
    assert path.read_bytes() == codecs.BOM_UTF16_LE + "alpha\r\ngamma\r\n".encode("utf-16-le")


def test_gbk_file_is_read_and_written_back_as_gbk(tmp_path: Path) -> None:
    text = "配置说明：这是一个使用国标编码保存的文本文件。\n第二行：包含更多的中文内容，用来让编码检测有足够的依据。\n"
    path = tmp_path / "gbk.txt"
    path.write_bytes(text.encode("gbk"))
    decoded, fmt = textio.decode_text(path.read_bytes(), verb="read", display="gbk.txt")
    assert decoded == text
    assert codecs.lookup(fmt.encoding).name in {"gbk", "gb18030", "gb2312", "cp936"}
    edit_file(path, "第二行", "第2行", replace_all=False)
    assert path.read_bytes().decode("gbk") == text.replace("第二行", "第2行")


def test_text_that_cannot_be_encoded_in_the_files_encoding_is_refused(tmp_path: Path) -> None:
    fmt = textio.TextFormat(encoding="gbk")
    with pytest.raises(ToolError, match="cannot represent"):
        textio.encode_text("snowman " + chr(0x2603) + chr(0x1F600), fmt, display="f")


def test_write_creates_then_updates_and_keeps_the_existing_format(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "new.txt"
    created = write_file(path, "a\nb\n")
    assert created == f"<path>{path}</path>\n<type>file</type>\n<content>\nCreated file\n</content>"
    assert path.read_bytes() == b"a\nb\n"

    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(codecs.BOM_UTF8 + b"old\r\ntext\r\n")
    updated = write_file(crlf, "new\ntext\n")
    assert "Updated file" in updated
    assert crlf.read_bytes() == codecs.BOM_UTF8 + b"new\r\ntext\r\n"

    with pytest.raises(ToolError, match="not a regular file"):
        write_file(tmp_path, "x")


def test_newline_detection_follows_the_majority() -> None:
    assert textio.detect_newline("a\r\nb\r\nc\n") == "\r\n"
    assert textio.detect_newline("a\nb\nc\r\n") == "\n"
    assert textio.split_lines("a\nb\n") == ["a", "b"]
    assert textio.split_lines("") == []


# ---------------------------------------------------------------------------
# Argument validation
# ---------------------------------------------------------------------------


def test_arguments_are_checked_against_the_schema() -> None:
    read = tool_schemas()["read"]["parameters"]
    assert validate_arguments(read, {"file_path": "a", "offset": None}) == {"file_path": "a"}
    with pytest.raises(ToolError, match="missing required file_path"):
        validate_arguments(read, {"offset": 1})
    with pytest.raises(ToolError, match="offset must be a number"):
        validate_arguments(read, {"file_path": "a", "offset": "3"})
    todo = tool_schemas()["todo_write"]["parameters"]
    with pytest.raises(ToolError, match=r"todos\[0\].status must be one of"):
        validate_arguments(todo, {"todos": [{"content": "x", "status": "doing"}]})
    with pytest.raises(ToolError, match="were not valid JSON"):
        validate_arguments(read, {"__invalid_json_arguments__": '{"file_path": '})
