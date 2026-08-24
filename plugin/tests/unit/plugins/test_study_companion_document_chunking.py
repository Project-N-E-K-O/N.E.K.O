from __future__ import annotations

import pytest

from plugin.plugins.study_companion.document_chunking import (
    DocumentChunkingError,
    split_document,
)


pytestmark = pytest.mark.unit


def _chars(value: str) -> int:
    return len(value)


def test_markdown_uses_heading_tree_and_ignores_fenced_pseudo_headings() -> None:
    source = (
        "preface\n\n# Book\nintro\n\n"
        "```markdown\n# Not a chapter\n## Also not a section\n```\n\n"
        "## Real section\ndetails\n"
    )

    chunks = split_document(
        source,
        "text/markdown",
        token_counter=_chars,
        target_tokens=45,
        max_tokens=70,
        min_preferred_tokens=1,
    )

    paths = [path for chunk in chunks for path in chunk.heading_paths]
    assert ("Book",) in paths
    assert ("Book", "Real section") in paths
    assert all("Not a chapter" not in path for path in paths)
    assert all("Also not a section" not in path for path in paths)
    assert "".join(chunk.text for chunk in chunks) == source


def test_txt_recognizes_chinese_and_english_chapters_but_not_body_mentions() -> None:
    source = (
        "前置信息\n\n第一章：开始\n\n正文。\n"
        "我们会在第一章讨论背景，但这一行不是标题。\n\n"
        "第十二回 风波再起\n内容。\n\nChapter 3 - Result\n\nEnding."
    )

    chunks = split_document(
        source,
        "text/plain",
        token_counter=_chars,
        target_tokens=45,
        max_tokens=75,
        min_preferred_tokens=1,
    )

    paths = [path for chunk in chunks for path in chunk.heading_paths]
    assert ("第一章：开始",) in paths
    assert ("第十二回 风波再起",) in paths
    assert ("Chapter 3 - Result",) in paths
    assert all("我们会在第一章" not in " / ".join(path) for path in paths)
    assert "".join(chunk.text for chunk in chunks) == source


@pytest.mark.parametrize(
    "document_type",
    [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ],
)
def test_extracted_pdf_and_docx_use_plain_text_boundaries(document_type: str) -> None:
    source = "第一章：开始\n\n正文。\n\nChapter 2 - Review\n\nSummary."

    chunks = split_document(
        source,
        document_type,
        token_counter=_chars,
        target_tokens=20,
        max_tokens=30,
        min_preferred_tokens=1,
    )

    paths = [path for chunk in chunks for path in chunk.heading_paths]
    assert ("第一章：开始",) in paths
    assert ("Chapter 2 - Review",) in paths
    assert "".join(chunk.text for chunk in chunks) == source


def test_unstructured_text_falls_back_to_paragraphs_then_sentences() -> None:
    source = "第一句内容。第二句内容。第三句内容。第四句内容。\n\n末尾段落。"

    chunks = split_document(
        source,
        "text/plain",
        token_counter=_chars,
        target_tokens=14,
        max_tokens=18,
        min_preferred_tokens=1,
    )

    assert len(chunks) > 1
    assert all(chunk.tokens <= 18 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == source
    assert [(chunk.start_char, chunk.end_char) for chunk in chunks] == [
        (sum(len(item.text) for item in chunks[:index]), sum(len(item.text) for item in chunks[: index + 1]))
        for index, chunk in enumerate(chunks)
    ]


def test_short_neighbor_is_merged_when_hard_limit_allows_it() -> None:
    source = "A sentence that fills space. Tiny."

    chunks = split_document(
        source,
        "text/plain",
        token_counter=_chars,
        target_tokens=28,
        max_tokens=len(source),
        min_preferred_tokens=10,
    )

    assert len(chunks) == 1
    assert chunks[0].text == source


def test_indivisible_oversized_sentence_is_rejected_without_truncation() -> None:
    with pytest.raises(DocumentChunkingError) as raised:
        split_document(
            "x" * 21,
            "text/plain",
            token_counter=_chars,
            target_tokens=10,
            max_tokens=20,
            min_preferred_tokens=1,
        )

    assert raised.value.diagnostic == "document_split_failed"


def test_more_than_maximum_chunks_is_rejected_instead_of_omitting_text() -> None:
    source = "".join(f"Sentence {index:02d}. " for index in range(12))

    with pytest.raises(DocumentChunkingError) as raised:
        split_document(
            source,
            "text/plain",
            token_counter=_chars,
            target_tokens=13,
            max_tokens=13,
            min_preferred_tokens=1,
            max_chunks=4,
        )

    assert raised.value.diagnostic == "document_split_failed"


def test_token_counter_is_injected_and_controls_reported_budget() -> None:
    calls: list[str] = []

    def counter(value: str) -> int:
        calls.append(value)
        return len(value.split())

    source = "one two three. four five six. seven eight."
    chunks = split_document(
        source,
        "text/plain",
        token_counter=counter,
        target_tokens=4,
        max_tokens=6,
        min_preferred_tokens=1,
    )

    assert calls
    assert all(chunk.tokens == len(chunk.text.split()) for chunk in chunks)
    assert all(chunk.tokens <= 6 for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == source


@pytest.mark.parametrize("document_type", ["text/plain", "text/markdown"])
def test_chunk_order_and_source_are_stable(document_type: str) -> None:
    source = "# A\n\nAlpha. Beta.\n\n# B\n\nGamma. Delta.\n"

    first = split_document(
        source,
        document_type,
        token_counter=_chars,
        target_tokens=16,
        max_tokens=24,
        min_preferred_tokens=1,
    )
    second = split_document(
        source,
        document_type,
        token_counter=_chars,
        target_tokens=16,
        max_tokens=24,
        min_preferred_tokens=1,
    )

    assert first == second
    assert "".join(chunk.text for chunk in first) == source
    assert [chunk.index for chunk in first] == list(range(len(first)))
