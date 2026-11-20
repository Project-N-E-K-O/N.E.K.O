"""Regressions for reviewed source extraction and immutable history boundaries."""

import io
import json
import logging
import zipfile

from pathlib import Path

import pytest

from knowledge.chunking import derive_chunks, _fit_windows
from knowledge.importing import documents
from knowledge.importing.projection import project_pack
from knowledge.importing.sources import SourceStore
from knowledge.models import KnowledgeEntry
from utils import document_parser as office

pytestmark = pytest.mark.unit_fast


def _docx(body, **members):
    output = io.BytesIO()
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', f'<w:document {ns}><w:body>{body}</w:body></w:document>')
        for name, content in members.items():
            archive.writestr(name, f'<w:footnotes {ns}>{content}</w:footnotes>')
    return output.getvalue()


def _paragraph(text):
    return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'


def test_long_source_name_keeps_its_format_suffix():
    parsed = documents.parse_source('a' * 160 + '.txt', b'complete body', documents.SourceLimits())
    assert parsed['filename'].endswith('.txt') and len(parsed['filename']) <= 160


def test_text_budget_is_checked_before_allocating_paragraph_records(monkeypatch):
    monkeypatch.setattr(documents, '_paragraphs', lambda *_: pytest.fail('oversized text reached paragraph allocation'))
    with pytest.raises(documents.SourceError) as error:
        documents.parse_source('large.txt', b'a\n\n' * 10, documents.SourceLimits(max_document_chars=20))
    assert error.value.reason == 'document_text_too_large'


def test_located_docx_skips_fallback_and_distinguishes_note_positions():
    body = '<mc:AlternateContent><mc:Choice>' + _paragraph('Choice') + '</mc:Choice><mc:Fallback>' + _paragraph('Fallback') + '</mc:Fallback></mc:AlternateContent>'
    body += '<w:p><w:r><w:footnoteReference w:id="2"/><w:footnoteReference w:id="3"/></w:r></w:p>'
    notes = '<w:footnote w:id="2">' + _paragraph('First note') + '</w:footnote><w:footnote w:id="3">' + _paragraph('Second note') + '</w:footnote>'
    parsed = documents.parse_source('notes.docx', _docx(body, **{'word/footnotes.xml': notes}), documents.SourceLimits())
    assert parsed['content'] == 'Choice\n\nFirst note\n\nSecond note'
    assert [s['location']['paragraph'] for s in parsed['sections'][-2:]] == [1, 2]


def test_located_docx_skips_text_boxes_inside_hidden_runs():
    def text_box(text, hidden):
        properties = '<w:rPr><w:vanish/></w:rPr>' if hidden else ''
        return (f'<w:p><w:r>{properties}<w:pict><w:txbxContent>{_paragraph(text)}'
                '</w:txbxContent></w:pict></w:r></w:p>')

    body = _paragraph('Body') + text_box('Hidden box', True) + text_box('Visible box', False)
    parsed = documents.parse_source('boxes.docx', _docx(body), documents.SourceLimits())
    assert parsed['content'] == 'Body\n\nVisible box'


def test_docx_rejects_oversize_during_extraction(monkeypatch):
    original = office._extract_word_paragraph_text
    calls = []

    def extract(node):
        calls.append(True)
        return original(node)

    monkeypatch.setattr(office, '_extract_word_paragraph_text', extract)
    with pytest.raises(documents.SourceError) as error:
        documents.parse_source('many.docx', _docx(_paragraph('abc') * 30), documents.SourceLimits(max_document_chars=5))
    assert error.value.reason == 'document_text_too_large'
    assert len(calls) == 2


def test_failed_admission_does_not_hide_retained_history_and_preview_is_idempotent(tmp_path):
    store = SourceStore(tmp_path)
    preview = store.preview('notes.txt', b'complete body')
    first = store.retain_preview(preview['preview_id'], preview['sha256'])
    store.set_retained(first['pack_id'], True)
    repeated = store.retain_preview(preview['preview_id'], preview['sha256'])
    assert repeated['version'] == first['version']
    assert store.retained_packs()[0]['pack_id'] == first['pack_id']
    assert len(store.versions(first['pack_id'], first['doc_id'])) == 1


def test_malformed_retained_names_do_not_break_other_groups(tmp_path):
    store = SourceStore(tmp_path)
    path = store.directory('imp.invalid') / 'manifest.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'pack_id':'imp.invalid','retained':True,'name':[]}))
    assert store.retained_packs() == []


def test_projection_preserves_words_and_skips_blank_windows():
    content = 'a ' * 1998 + 'lighthouse' + ' ' * 8000 + 'B'
    pack = project_pack('imp.words', 'words', [{'content':content,'name':'words','doc_id':'a'*32,'version':1}])
    assert any('lighthouse' in entry.content for entry in pack.entries)
    assert all(entry.content.strip() for entry in pack.entries)
    assert ''.join(entry.content for entry in pack.entries).replace(' ', '') == content.replace(' ', '')


def test_legacy_projection_is_available_only_for_existing_source_verification():
    content = 'a ' * 1998 + 'lighthouse'
    document = {'content':content,'name':'words','doc_id':'a'*32,'version':1}
    old = project_pack('imp.words','words',[document],legacy_windows=True)
    assert old.entries[0].content == content[:4000]
    assert old.entries[1].content == content[4000:]
    assert old.entries != project_pack('imp.words','words',[document]).entries


@pytest.mark.parametrize('bad_sidecar', ['[]','{bad-json'])
def test_hiding_pack_does_not_require_readable_sidecar_but_retaining_does(tmp_path,bad_sidecar):
    store = SourceStore(tmp_path)
    path = store.directory('imp.damaged')/'manifest.json'
    path.parent.mkdir(parents=True)
    path.write_text(bad_sidecar)
    store.set_retained('imp.damaged',False)
    assert path.read_text() == bad_sidecar
    with pytest.raises((ValueError,documents.SourceError)):
        store.set_retained('imp.damaged',True)


def test_hiding_valid_sidecar_does_not_swallow_write_failure(tmp_path,monkeypatch):
    from knowledge.importing import sources
    store = SourceStore(tmp_path)
    preview = store.preview('notes.txt',b'complete body')
    store.retain_preview(preview['preview_id'],preview['sha256'])
    store.set_retained(preview['pack_id'],True)

    def fail(*_):
        raise OSError('synthetic write failure')

    monkeypatch.setattr(sources,'atomic_write_json',fail)
    with pytest.raises(OSError):
        store.set_retained(preview['pack_id'],False)


def test_retry_repairs_manifest_after_frozen_version_was_written(tmp_path,monkeypatch):
    from knowledge.importing import sources
    store = SourceStore(tmp_path)
    preview = store.preview('notes.txt',b'complete body')
    write = sources.atomic_write_json

    def fail(*_):
        raise OSError('synthetic manifest write failure')

    monkeypatch.setattr(sources,'atomic_write_json',fail)
    with pytest.raises(OSError):
        store.retain_preview(preview['preview_id'],preview['sha256'])
    monkeypatch.setattr(sources,'atomic_write_json',write)
    assert store.retain_preview(preview['preview_id'],preview['sha256'])['version'] == 1
    assert store.has_history(preview['pack_id'])


def test_nonmonotone_fit_can_find_valid_prefixes():
    assert _fit_windows('abcdef', lambda text:text in {'ab','cd','ef'}) == ['ab','cd','ef']


def test_fitted_chunks_do_not_publish_blank_bodies():
    chunks = derive_chunks(KnowledgeEntry(title='T', summary='', content='A   B'), fit=lambda text:len(text) <= 4)
    assert chunks and all(chunk.embed_text.split('\n', 1)[1].strip() for chunk in chunks)
    assert ''.join(chunk.embed_text.split('\n',1)[1] for chunk in chunks).replace(' ','') == 'AB'


def test_source_spool_keeps_the_exact_original_bytes(tmp_path):
    spool = tmp_path / 'request-spool'
    raw = b'complete\r\noriginal\r\n'
    spool.write_bytes(raw)
    store = SourceStore(tmp_path / 'library')
    preview = store.preview('spooled.txt', spool)
    retained = store.retain_preview(preview['preview_id'],preview['sha256'])
    assert 'complete' in retained['content'] and 'original' in retained['content']
    stored = store.directory(retained['pack_id']) / 'originals' / retained['doc_id'] / 'v1' / 'original'
    assert stored.read_bytes() == raw


def test_source_spool_read_is_bounded_by_the_document_limit(tmp_path, monkeypatch):
    from knowledge.importing import sources

    monkeypatch.setattr(sources, 'SourceLimits', lambda: documents.SourceLimits(max_document_bytes=16))
    spool = tmp_path / 'request-spool'
    spool.write_bytes(b'x' * 64)
    reads = []
    original_open = Path.open

    def tracking_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        if self == spool:
            read = stream.read
            stream.read = lambda size=-1: reads.append(size) or read(size)
        return stream

    monkeypatch.setattr(Path, 'open', tracking_open)
    with pytest.raises(documents.SourceError) as error:
        SourceStore(tmp_path / 'library').preview('spooled.txt', spool)
    assert error.value.reason == 'document_too_large'
    assert reads == [17]


def test_many_located_paragraphs_have_readable_preview_and_version_records(tmp_path):
    store = SourceStore(tmp_path)
    preview = store.preview('many.docx', _docx(_paragraph('a') * 80_000))
    assert preview['chars'] == 239_998
    assert store.find_preview(preview['preview_id'])['content'] == preview['content']
    retained = store.retain_preview(preview['preview_id'],preview['sha256'])
    assert len(store.version(retained['pack_id'],retained['doc_id'],1)['sections']) == 80_000


def test_unreadable_sidecar_size_is_rejected_before_original_publication(tmp_path, monkeypatch):
    from knowledge.importing import sources

    monkeypatch.setattr(sources, 'SIDECAR_MAX_BYTES', 10)
    store = SourceStore(tmp_path)
    with pytest.raises(documents.SourceError) as error:
        store.preview('notes.txt',b'complete body')
    assert error.value.reason == 'document_text_too_large'
    assert not list(tmp_path.rglob('*.original')) and not list(tmp_path.rglob('*.json'))


def test_private_pdf_redacts_child_emitter_diagnostics(monkeypatch, caplog):
    factory = logging.getLogRecordFactory()

    def parse(*_,**__):
        logging.getLogger('pypdf.generic._data_structures').warning('Private token: %s','PRIVATE_PDF_CANARY')
        return [{'text':'readable','location':{'page':1}}]

    monkeypatch.setattr(office,'parse_document_sections',parse)
    assert documents.parse_source('notes.pdf',b'%PDF-synthetic',documents.SourceLimits())['content'] == 'readable'
    assert 'PRIVATE_PDF_CANARY' not in caplog.text and 'knowledge_pdf_parser_warning' in caplog.text
    assert logging.getLogRecordFactory() is factory


def test_inline_image_page_without_xobject_is_not_silently_skipped():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=50,height=50)
    stream = DecodedStreamObject()
    stream.set_data(b'q BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x00\x00 EI Q')
    page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(documents.SourceError) as error:
        documents.parse_source('scan.pdf',output.getvalue(),documents.SourceLimits())
    assert error.value.reason == 'scan_unsupported'


def test_notes_referenced_only_from_hidden_text_stay_hidden():
    body = ('<w:p><w:r><w:t>Body</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r>'
            '<w:r><w:rPr><w:vanish/></w:rPr><w:footnoteReference w:id="3"/></w:r></w:p>')
    notes = '<w:footnote w:id="2">' + _paragraph('Visible note') + '</w:footnote><w:footnote w:id="3">' + _paragraph('Hidden note') + '</w:footnote>'
    parsed = documents.parse_source('notes.docx', _docx(body, **{'word/footnotes.xml': notes}), documents.SourceLimits())
    assert parsed['content'] == 'Body\n\nVisible note'
    attachment = office.parse_document('notes.docx', '', _docx(body, **{'word/footnotes.xml': notes}))
    assert 'Visible note' in attachment['content'] and 'Hidden note' not in attachment['content']


def test_word_traversals_do_not_depend_on_the_python_recursion_limit():
    import sys
    import xml.etree.ElementTree as ET

    depth = sys.getrecursionlimit() + 500
    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    inner = '<w:p><w:r><w:t>Deep</w:t><w:footnoteReference w:id="7"/></w:r></w:p>'
    root = ET.fromstring(f'<w:document {ns}>' + '<w:sdt>' * depth + inner + '</w:sdt>' * depth + '</w:document>')
    assert office._collect_word_note_reference_ids(root, office._WORD_NS + 'footnoteReference') == {'7'}
    assert [office._extract_word_paragraph_text(p) for p in office._iter_word_paragraphs(root)] == ['Deep']


def test_word_paragraph_traversal_keeps_document_order():
    import xml.etree.ElementTree as ET

    ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    body = _paragraph('One') + '<w:tbl><w:tr><w:tc>' + _paragraph('Two') + _paragraph('Three') + '</w:tc></w:tr></w:tbl>' + _paragraph('Four')
    root = ET.fromstring(f'<w:document {ns}><w:body>{body}</w:body></w:document>')
    assert [office._extract_word_paragraph_text(p) for p in office._iter_word_paragraphs(root)] == ['One', 'Two', 'Three', 'Four']
