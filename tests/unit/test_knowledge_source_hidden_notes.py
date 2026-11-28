"""Keep DOCX note references subject to the same visibility as their runs."""

import io
import zipfile

import pytest

from services.knowledge.importing.documents import SourceLimits, parse_source
from utils.document_parser import parse_document


pytestmark = pytest.mark.unit_fast


def _notes_document(note, properties, *, also_visible=False):
    """Use real OOXML bytes with one public note and one controlled reference."""
    namespace = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    controlled = '<w:r>' + (f'<w:rPr>{properties}</w:rPr>' if properties else '')
    controlled += f'<w:t>CONTROLLED_RUN_TEXT</w:t><w:{note}Reference w:id="3"/></w:r>'
    visible = f'<w:r><w:{note}Reference w:id="3"/></w:r>' if also_visible else ''
    document = f'<w:document xmlns:w="{namespace}"><w:body><w:p>'
    document += f'<w:r><w:t>Visible document body</w:t><w:{note}Reference w:id="2"/></w:r>'
    document += controlled + visible + '</w:p></w:body></w:document>'
    # Put the controlled note first: filtering references must not invent new
    # paragraph locations for the remaining public note.
    notes = f'<w:{note}s xmlns:w="{namespace}"><w:{note} w:id="3">'
    notes += '<w:p><w:r><w:t>PRIVATE_NOTE_CANARY</w:t></w:r></w:p>'
    notes += f'</w:{note}><w:{note} w:id="2">'
    notes += f'<w:p><w:r><w:t>PUBLIC_NOTE</w:t></w:r></w:p></w:{note}></w:{note}s>'
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr(f"word/{note}s.xml", notes)
    return output.getvalue()


def _extract(raw, consumer):
    if consumer == "knowledge":
        return parse_source("notes.docx", raw, SourceLimits())["content"]
    return parse_document("notes.docx", "", raw)["content"]


@pytest.mark.parametrize("consumer", ["knowledge", "attachment"])
@pytest.mark.parametrize("note", ["footnote", "endnote"])
@pytest.mark.parametrize("properties,visible", [
    ("<w:vanish/>", False), ("<w:vanish w:val=\"true\"/>", False),
    ("<w:vanish w:val=\"1\"/>", False), ("", True),
    ("<w:vanish w:val=\"false\"/>", True), ("<w:vanish w:val=\"0\"/>", True),
    ("<w:vanish w:val=\"off\"/>", True), ("<w:vanish w:val=\"no\"/>", True),
])
def test_hidden_note_references_match_existing_run_visibility(consumer, note, properties, visible):
    text = _extract(_notes_document(note, properties), consumer)
    assert "Visible document body" in text and "PUBLIC_NOTE" in text
    assert ("CONTROLLED_RUN_TEXT" in text) is visible
    assert ("PRIVATE_NOTE_CANARY" in text) is visible


@pytest.mark.parametrize("consumer", ["knowledge", "attachment"])
@pytest.mark.parametrize("note", ["footnote", "endnote"])
def test_a_visible_reference_to_the_same_note_still_authorizes_its_text(consumer, note):
    text = _extract(_notes_document(note, "<w:vanish/>", also_visible=True), consumer)
    assert "PRIVATE_NOTE_CANARY" in text
    assert "CONTROLLED_RUN_TEXT" not in text
