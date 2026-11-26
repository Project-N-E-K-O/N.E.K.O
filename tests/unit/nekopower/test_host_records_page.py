"""A session's record read a page at a time for showing it to the user (``records.read_page``).

From the end backwards, by the place a line begins at; one entry whole
when the user opens it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nekopower.host import records


def _message(role: str, content: Any, **more: Any) -> dict[str, Any]:
    return {"type": "message", "id": more.pop("id", "e"), "message": {"role": role, "content": content, **more}}


def _write(path: Path, entries: list[Any], tail: bytes = b"") -> list[int]:
    """Write a record; return the place each line begins at."""
    places, data = [], b""
    for entry in entries:
        places.append(len(data))
        line = entry if isinstance(entry, bytes) else json.dumps(entry, ensure_ascii=False).encode("utf-8")
        data += line + b"\n"
    path.write_bytes(data + tail)
    return places


def _page(path: Path, before: int | None = None, limit: int = 50, **more: Any) -> dict[str, Any]:
    more.setdefault("entry_tokens", 200)
    more.setdefault("scan_bytes", 1 << 20)
    return records.read_page(path, before, limit, **more)


def test_a_page_is_the_last_entries_in_the_order_they_were_said_and_says_where_the_one_before_begins(tmp_path):
    path = tmp_path / "transcript.jsonl"
    places = _write(path, [_message("user", f"turn {n}", timestamp=1_700_000_000_000 + n) for n in range(7)])

    last = _page(path, limit=3)
    assert [(entry["at"], entry["text"]) for entry in last["entries"]] == [
        (places[4], "turn 4"), (places[5], "turn 5"), (places[6], "turn 6"),
    ]
    assert last["before"] == places[4]
    # The page before it ends where this one began: nothing twice, nothing passed over.
    middle = _page(path, last["before"], limit=3)
    assert [entry["text"] for entry in middle["entries"]] == ["turn 1", "turn 2", "turn 3"]
    first = _page(path, middle["before"], limit=3)
    assert [entry["text"] for entry in first["entries"]] == ["turn 0"]
    # At the beginning of the record there is no page before.
    assert first["before"] is None
    # A message says when it was said in milliseconds: told in seconds.
    assert last["entries"][0]["time"] == 1_700_000_000.004


def test_an_entry_is_timed_by_when_it_was_written_which_the_record_gives_in_seconds(tmp_path):
    path = tmp_path / "transcript.jsonl"
    _write(
        path,
        [
            # As the runtime writes them: the entry in seconds, the message inside it in milliseconds.
            {**_message("user", "first", timestamp=1_795_443_848_162), "timestamp": 1_795_443_848.16},
            {"type": "custom_message", "custom_type": "notice:usage", "content": "half used", "timestamp": 1_795_443_850.5},
            {"type": "compaction", "summary": "It began.", "timestamp": 1_795_443_851},
            _message("user", "no time at all"),
            {**_message("user", "no time that is one"), "timestamp": "soon"},
            {**_message("user", "nor a yes"), "timestamp": True},
        ],
    )
    assert [entry["time"] for entry in _page(path)["entries"]] == [
        1_795_443_848.16, 1_795_443_850.5, 1_795_443_851.0, None, None, None,
    ]


def test_an_entry_says_who_spoke_what_was_called_and_what_a_tool_gave_back(tmp_path):
    path = tmp_path / "transcript.jsonl"
    _write(
        path,
        [
            {"type": "session", "id": "head"},
            _message("user", [{"type": "text", "text": "sort the photos"}, {"type": "image", "data": "AAAA"}]),
            _message(
                "assistant",
                [
                    {"type": "thinking", "thinking": "which folder?"},
                    {"type": "text", "text": "Looking."},
                    {"type": "toolCall", "id": "c1", "name": "glob", "arguments": {"pattern": "*.jpg"}},
                ],
            ),
            _message("toolResult", [{"type": "text", "text": "a.jpg\nb.jpg"}], toolName="glob", isError=False),
            _message("toolResult", "no such file", tool_name="read", is_error=True),
            {"type": "custom_message", "customType": "steer", "content": "by month, please"},
            {"type": "compaction", "summary": "It sorted two photos."},
            _message("assistant", [{"type": "toolCall", "id": "c2", "name": "report", "arguments": {}}]),
        ],
    )
    shown = _page(path)["entries"]
    assert [entry["role"] for entry in shown] == [
        "user", "assistant", "tool_result", "tool_result", "notice", "summary", "assistant",
    ]
    asked, looked, gave, failed, steered, summed, reported = shown
    # A picture is counted and not passed on; what the model thought to itself is not shown.
    assert (asked["text"], asked["pictures"]) == ("sort the photos", 1)
    assert "AAAA" not in json.dumps(shown) and "which folder" not in json.dumps(shown)
    assert looked["text"] == "Looking."
    assert looked["calls"] == [{"name": "glob", "arguments": '{"pattern":"*.jpg"}', "cut": False}]
    assert (gave["tool"], gave["failed"], gave["text"]) == ("glob", False, "a.jpg\nb.jpg")
    assert (failed["tool"], failed["failed"]) == ("read", True)
    assert (steered["tool"], steered["text"]) == ("steer", "by month, please")
    assert summed["text"] == "It sorted two photos."
    # A message that only calls a tool is shown by the call.
    assert (reported["text"], [item["name"] for item in reported["calls"]]) == ("", ["report"])


def test_a_long_entry_is_cut_in_a_page_and_whole_when_it_is_opened(tmp_path):
    path = tmp_path / "transcript.jsonl"
    long_text = " ".join(f"word{n}" for n in range(600))
    places = _write(
        path,
        [
            _message("toolResult", long_text, toolName="read"),
            _message("assistant", [{"type": "toolCall", "id": "c", "name": "write", "arguments": {"content": long_text}}]),
        ],
    )
    result, wrote = _page(path, entry_tokens=40)["entries"]
    assert result["cut"] and len(result["text"]) < len(long_text) / 4 and result["text"].startswith("word0 word1")
    assert wrote["calls"][0]["cut"] and len(wrote["calls"][0]["arguments"]) < len(long_text) / 4

    whole = records.read_whole_entry(path, places[0], entry_tokens=100_000, max_bytes=1 << 20)
    assert (whole["at"], whole["text"], whole["cut"]) == (places[0], long_text, False)
    with pytest.raises(records.EntryTooLarge):
        records.read_whole_entry(path, places[0], entry_tokens=100_000, max_bytes=100)


def test_a_line_that_is_being_written_or_is_no_entry_is_passed_over(tmp_path):
    path = tmp_path / "transcript.jsonl"
    places = _write(
        path,
        [_message("user", "first"), b"{not json", ["a", "list"], _message("user", "   "), _message("user", "second")],
        tail=b'{"type": "message", "message": {"role": "user", "content": "half a li',
    )
    page = _page(path)
    assert [entry["text"] for entry in page["entries"]] == ["first", "second"]
    assert page["before"] is None
    # The line that is not whole yet is no place to read an entry at.
    with pytest.raises(records.NoSuchPlace):
        records.read_whole_entry(path, path.stat().st_size - 10, entry_tokens=100, max_bytes=1 << 20)
    # Nor is a line with nothing to show.
    with pytest.raises(records.NoSuchPlace):
        records.read_whole_entry(path, places[1], entry_tokens=100, max_bytes=1 << 20)


@pytest.mark.parametrize("place", [-1, 3, 10**9])
def test_a_place_where_no_line_begins_is_refused(tmp_path, place):
    path = tmp_path / "transcript.jsonl"
    _write(path, [_message("user", "first"), _message("user", "second")])
    with pytest.raises(records.NoSuchPlace):
        _page(path, place)
    with pytest.raises(records.NoSuchPlace):
        records.read_whole_entry(path, place, entry_tokens=100, max_bytes=1 << 20)


def test_lines_longer_than_a_block_and_a_record_longer_than_a_page_reads_are_read_to_the_end(tmp_path, monkeypatch):
    # Blocks far shorter than the lines: every line is put together from several.
    monkeypatch.setattr(records, "_BACKWARD_CHUNK_BYTES", 16)
    path = tmp_path / "transcript.jsonl"
    texts = [f"entry {n}." + "x" * (n * 13) for n in range(12)]
    places = _write(path, [_message("user", text) for text in texts])
    seen: list[str] = []
    before: int | None = None
    pages = 0
    while True:
        page = _page(path, before, limit=5)
        seen = [entry["text"] for entry in page["entries"]] + seen
        pages += 1
        # A page that gets nowhere would go round here for ever.
        assert pages < 100 and page["before"] != before
        before = page["before"]
        if before is None:
            break
    assert (seen, pages) == (texts, 3)

    # A page that may read only so much ends there and says where to go on; nothing is lost.
    seen, before, pages = [], None, 0
    while True:
        page = _page(path, before, limit=50, scan_bytes=200)
        seen = [entry["text"] for entry in page["entries"]] + seen
        pages += 1
        # A page that gets nowhere would go round here for ever.
        assert pages < 100 and page["before"] != before
        before = page["before"]
        if before is None:
            break
    assert seen == texts and pages > 3
    assert [entry["at"] for entry in _page(path, limit=50)["entries"]] == places


def test_a_last_line_that_has_no_end_yet_is_not_read_however_whole_it_looks(tmp_path, monkeypatch):
    # Written, its newline not yet: nothing tells it from a line that is half way, so it waits.
    monkeypatch.setattr(records, "_BACKWARD_CHUNK_BYTES", 16)
    path = tmp_path / "transcript.jsonl"
    tail = json.dumps(_message("user", "not ended yet")).encode("utf-8")
    places = _write(path, [_message("user", "first"), _message("user", "second")], tail=tail)
    page = _page(path)
    assert [entry["text"] for entry in page["entries"]] == ["first", "second"]
    # It begins where a line begins, and is no entry to open all the same.
    begins = path.stat().st_size - len(tail)
    assert begins > places[1]
    with pytest.raises(records.NoSuchPlace):
        records.read_whole_entry(path, begins, entry_tokens=100, max_bytes=1 << 20)
    # Once its newline is there it is read.
    with path.open("ab") as handle:
        handle.write(b"\n")
    assert [entry["text"] for entry in _page(path)["entries"]] == ["first", "second", "not ended yet"]
    assert records.read_whole_entry(path, begins, entry_tokens=100, max_bytes=1 << 20)["text"] == "not ended yet"


def test_a_record_that_is_not_there_says_so(tmp_path):
    with pytest.raises(records.RecordError):
        _page(tmp_path / "nothing.jsonl")
    with pytest.raises(records.RecordError):
        records.read_whole_entry(tmp_path / "nothing.jsonl", 0, entry_tokens=100, max_bytes=100)
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert _page(empty) == {"entries": [], "before": None}


def test_a_record_that_is_there_and_cannot_be_opened_is_not_one_nothing_was_said_in(tmp_path):
    # Nothing there: no record, as when nothing was said or it was removed.
    with pytest.raises(records.RecordError) as gone:
        records.read_page(tmp_path / "missing.jsonl", None, 5, entry_tokens=100, scan_bytes=10_000)
    assert not isinstance(gone.value, records.RecordUnreadable)
    with pytest.raises(records.RecordError) as gone:
        records.read_whole_entry(tmp_path / "missing.jsonl", 0, entry_tokens=100, max_bytes=10_000)
    assert not isinstance(gone.value, records.RecordUnreadable)
    # There, and not to be opened as a file (a directory stands for one that is held or not permitted).
    held = tmp_path / "held.jsonl"
    held.mkdir()
    with pytest.raises(records.RecordUnreadable):
        records.read_page(held, None, 5, entry_tokens=100, scan_bytes=10_000)
    with pytest.raises(records.RecordUnreadable):
        records.read_whole_entry(held, 0, entry_tokens=100, max_bytes=10_000)


def test_a_record_that_fails_while_it_is_read_is_not_one_nothing_was_said_in(tmp_path, monkeypatch):
    path = tmp_path / "record.jsonl"
    path.write_text('{"type": "message", "message": {"role": "user", "content": "hello"}}\n', encoding="utf-8")

    def fails(*args, **kwargs):
        raise OSError("the disk went away")

    # Opened, and then it cannot be read on (a share that dropped, a disk that fails).
    monkeypatch.setattr(records, "_whole_end", fails)
    with pytest.raises(records.RecordUnreadable):
        records.read_page(path, None, 5, entry_tokens=100, scan_bytes=10_000)
    monkeypatch.setattr(records, "_at_line_start", fails)
    with pytest.raises(records.RecordUnreadable):
        records.read_whole_entry(path, 0, entry_tokens=100, max_bytes=10_000)


def test_the_lines_before_a_place_are_the_same_whatever_the_size_of_the_blocks_they_are_read_in(tmp_path, monkeypatch):
    # Empty lines, short ones, and ones that reach over many blocks, as a picture kept in a record does.
    lines = [b"", b"a", b"x" * 37, b"", b"hello world", b"y" * 5, b"z" * 64, b"last"]
    path = tmp_path / "record.jsonl"
    path.write_bytes(b"".join(line + b"\n" for line in lines))
    places, at = [], 0
    for line in lines:
        places.append((at, line))
        at += len(line) + 1
    ends = [place for place, _line in places] + [at]
    for block in (1, 2, 3, 7, 8, 16, 1000):
        monkeypatch.setattr(records, "_BACKWARD_CHUNK_BYTES", block)
        with path.open("rb") as handle:
            for count, end in enumerate(ends):
                found = list(records._lines_before(handle, end, 10**9))
                assert found == places[:count][::-1], (block, end)
