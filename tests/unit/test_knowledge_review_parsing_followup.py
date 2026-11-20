"""Follow-up review regressions for parser visibility and bounded package work."""

import io

import pytest

from knowledge.chunking import derive_chunks
from knowledge.importing.documents import SourceError, SourceLimits, parse_source
from knowledge.models import KnowledgeEntry
from tests.unit.test_document_parser import _pptx_bytes, _xlsx_bytes
from tests.unit.test_knowledge_archive_v2 import make_archive
from tests.unit.test_knowledge_importing_documents import _pdf_with_text
from tests.unit.test_knowledge_package_review import _replace_entries
from tests.unit.test_knowledge_review_documents import _docx, _paragraph
from utils import document_parser
from utils import knowledge_package as package


pytestmark = pytest.mark.unit_fast


def test_full_token_fit_is_checked_before_nonmonotone_prefix_search():
    chunks = derive_chunks(KnowledgeEntry(title="T", summary="", content="abcdef"),
                           fit=lambda text: text in {"T\na", "T\nabcdef"})
    assert [chunk.embed_text for chunk in chunks] == ["T\nabcdef"]


@pytest.mark.parametrize("note", ["footnote", "endnote"])
def test_note_references_in_docx_fallback_are_ignored(note):
    body = '<mc:AlternateContent><mc:Choice>' + _paragraph("Visible choice")
    body += f'<w:p><w:r><w:{note}Reference w:id="2"/></w:r></w:p></mc:Choice><mc:Fallback>'
    body += f'<w:p><w:r><w:{note}Reference w:id="3"/></w:r></w:p></mc:Fallback></mc:AlternateContent>'
    notes = f'<w:{note} w:id="2">' + _paragraph("Visible note") + f'</w:{note}>'
    notes += f'<w:{note} w:id="3">' + _paragraph("Hidden fallback note") + f'</w:{note}>'
    parsed = parse_source("notes.docx", _docx(body, **{f"word/{note}s.xml": notes}), SourceLimits())
    assert parsed["content"] == "Visible choice\n\nVisible note"


def _pdf_with_artwork(kind):
    """Readable first page plus the exact second-page operations under test."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(_pdf_with_text())).pages[0])
    page = writer.add_blank_page(width=50, height=50)
    stream = DecodedStreamObject()
    operations = b"q Q"
    if kind == "inline":
        operations = b"q BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x00\x00 EI Q"
    if kind in {"image", "unused-image", "blank-form", "image-form"}:
        target = DecodedStreamObject()
        if kind in {"image", "unused-image"}:
            target.update({NameObject("/Subtype"): NameObject("/Image"), NameObject("/Width"): NumberObject(1),
                           NameObject("/Height"): NumberObject(1), NameObject("/BitsPerComponent"): NumberObject(8),
                           NameObject("/ColorSpace"): NameObject("/DeviceRGB")})
            target.set_data(b"\xff\x00\x00")
        else:
            target.update({NameObject("/Subtype"): NameObject("/Form"),
                           NameObject("/BBox"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(50), NumberObject(50)])})
            target.set_data(b"q Q" if kind == "blank-form" else b"BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x00\x00 EI")
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/XObject"): DictionaryObject({NameObject("/Artwork"): writer._add_object(target)})})
        if kind != "unused-image":
            operations = b"q /Artwork Do Q"
    stream.set_data(operations)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("kind,scan", [("state", False), ("unused-image", False), ("blank-form", False),
                                       ("inline", True), ("image", True), ("image-form", True)])
def test_blank_pdf_operators_are_distinguished_from_actual_image_operations(kind, scan):
    if scan:
        with pytest.raises(SourceError) as error:
            parse_source("artwork.pdf", _pdf_with_artwork(kind), SourceLimits())
        assert error.value.reason == "scan_unsupported"
    else:
        assert parse_source("blank-page.pdf", _pdf_with_artwork(kind), SourceLimits())["content"] == "A complete private reference."


# Builders, not bytes: ZIP members carry the current time, so byte parameters
# could differ between xdist workers and break collection.
@pytest.mark.parametrize("filename,build", [("sheets.xlsx", lambda: _xlsx_bytes("A", sheet_count=2)),
                                            ("notes.pptx", lambda: _pptx_bytes("Slide", "Notes"))],
                         ids=["xlsx", "pptx"])
def test_office_character_budget_counts_separator_between_parts(filename, build):
    raw = build()
    full = document_parser.parse_document(filename, "", raw)
    limit = full["chars"] - 2
    bounded = document_parser.parse_document(filename, "", raw,
                                             limits=document_parser.DocumentLimits(max_extracted_chars=limit))
    assert bounded["truncated"] and bounded["chars"] <= limit


def test_one_part_exactly_at_the_character_budget_remains_complete():
    budget = document_parser._TextBudget(limit=4)
    parts = []
    budget.add(parts, "abcd")
    assert parts == ["abcd"] and not budget.truncated


def test_repeated_body_variants_are_not_rescanned_after_sixteen_cache_entries():
    class CountingSource(str):
        calls = 0

        def find(self, *args):
            self.calls += 1
            return super().find(*args)

    content = CountingSource("a" * 800)
    bodies = ["a" * (100 + index % 21) for index in range(1000)]
    assert package._source_is_covered(content, bodies)
    assert content.calls <= 21 * 801


def test_coverage_matching_has_an_independent_work_budget():
    with pytest.raises(package.PackageError) as error:
        package._source_is_covered("a" * 800, ["a" * 100], remaining_matches=[50])
    assert error.value.reason == "package_capacity_exceeded"


def test_coverage_work_budget_is_shared_by_all_package_entries(tmp_path, monkeypatch):
    def mutate(manifest, members):
        entries = [{"title": "First", "content": "body"}, {"title": "Second", "content": "body"}]
        _replace_entries(manifest, members, entries, [["body"], ["body"]])

    monkeypatch.setattr(package, "_MAX_COVERAGE_MATCHES", 1)
    with pytest.raises(package.PackageError) as error:
        package.validate_package(make_archive(tmp_path / "budget.zip", mutate=mutate))
    assert error.value.reason == "package_capacity_exceeded"


@pytest.mark.parametrize("version", ["release/1", "release\\1", "release 1", "版本1", "v1\n", "+1", "v" * 101])
def test_wire_versions_match_subscription_and_catalog_path_identity(tmp_path, version):
    def mutate(manifest, _members):
        manifest["version"] = version

    with pytest.raises(package.PackageError):
        package.validate_package(make_archive(tmp_path / "version.zip", mutate=mutate))


@pytest.mark.parametrize("version", ["1", "v1.0.0+build-rc_2", "auto-" + "a" * 32, "v" * 100])
def test_existing_ascii_wire_versions_remain_valid(tmp_path, version):
    def mutate(manifest, _members):
        manifest["version"] = version

    assert package.validate_package(make_archive(tmp_path / "version.zip", mutate=mutate)).manifest["version"] == version
