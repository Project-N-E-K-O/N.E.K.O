"""Reject unreadable painted PDF pages while keeping true blank-page semantics."""

import io

import pytest

from knowledge.importing.documents import SourceError, SourceLimits, parse_source
from tests.unit.test_knowledge_importing_documents import _pdf_with_text
from utils import document_parser


pytestmark = pytest.mark.unit_fast


def _mixed_pdf(operations):
    """Keep real readable text on page one to expose silent omission of page two."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(_pdf_with_text())).pages[0])
    page = writer.add_blank_page(width=50, height=50)
    stream = DecodedStreamObject()
    stream.set_data(operations)
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("paint", [b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"])
def test_unreadable_vector_page_cannot_be_omitted_from_a_successful_import(paint):
    with pytest.raises(SourceError) as error:
        parse_source("outlined.pdf", _mixed_pdf(b"0 0 20 20 re " + paint), SourceLimits())
    assert error.value.reason == "scan_unsupported"


@pytest.mark.parametrize("operations", [b"q Q", b"q 1 0 0 1 0 0 cm 1 w Q", b"0 0 10 10 re W n",
                                         b"0 0 m 10 10 l n", b"0 0 m 10 10 l", b"S", b"0 0 m S"])
def test_state_clipping_and_unpainted_paths_remain_blank_pages(operations):
    assert parse_source("blank.pdf", _mixed_pdf(operations), SourceLimits())["content"] == "A complete private reference."


def _stream(operations, *, subtype=None, resources=None):
    from pypdf.generic import DecodedStreamObject, NameObject

    stream = DecodedStreamObject()
    stream.set_data(operations)
    if subtype:
        stream[NameObject("/Subtype")] = NameObject(subtype)
    if resources is not None:
        stream[NameObject("/Resources")] = resources
    return stream


def _resources(**objects):
    from pypdf.generic import DictionaryObject, NameObject

    return DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/" + key): value for key, value in objects.items()})})


@pytest.mark.parametrize("leaf,painted", [(b"q Q", False), (b"0 0 20 20 re S", True),
                                        (b"BI /W 1 /H 1 /BPC 8 /CS /RGB ID \xff\x00\x00 EI", True)])
def test_form_chain_traversal_does_not_depend_on_the_python_recursion_limit(leaf, painted):
    child = _stream(leaf, subtype="/Form")
    # Construct in memory to isolate our traversal from pypdf's independent
    # text extractor and writer recursion limits.
    for _ in range(1200):
        child = _stream(b"/Next Do", subtype="/Form", resources=_resources(Next=child))
    assert document_parser._pdf_has_unreadable_content(_stream(b"/Next Do"), _resources(Next=child), None) is painted


def test_only_invoked_form_content_counts_and_parent_operations_resume():
    form = _stream(b"q Q", subtype="/Form")
    unused = _stream(b"0 0 20 20 re S", subtype="/Form")
    assert document_parser._pdf_has_unreadable_content(_stream(b"/Form Do 0 0 10 10 re S"), _resources(Form=form, Unused=unused), None)
    assert not document_parser._pdf_has_unreadable_content(_stream(b"/Form Do"), _resources(Form=form, Unused=unused), None)


def test_nested_forms_preserve_inherited_resources_and_stop_cycles():
    from pypdf.generic import NameObject

    image = _stream(b"pixels", subtype="/Image")
    form = _stream(b"/Again Do /Image Do", subtype="/Form")
    resources = _resources(Again=form, Image=image)
    assert document_parser._pdf_has_unreadable_content(_stream(b"/Again Do"), resources, None)
    # Removing the actual draw leaves a cycle, not painted content.
    resources[NameObject("/XObject")].pop(NameObject("/Image"))
    assert not document_parser._pdf_has_unreadable_content(_stream(b"/Again Do"), resources, None)


def test_reused_resource_less_form_is_checked_in_each_inherited_scope():
    shared = _stream(b"/Pick Do", subtype="/Form")
    blank = _stream(b"q Q", subtype="/Form")
    image = _stream(b"pixels", subtype="/Image")
    first = _stream(b"/Shared Do", subtype="/Form", resources=_resources(Shared=shared, Pick=blank))
    second = _stream(b"/Shared Do", subtype="/Form", resources=_resources(Shared=shared, Pick=image))
    assert document_parser._pdf_has_unreadable_content(_stream(b"/First Do /Second Do"),
                                                      _resources(First=first, Second=second), None)


@pytest.mark.parametrize("operations,painted", [(b"/Shade sh", True), (b"BT (glyph) Tj ET", True),
                                                (b"BT [100 <01>] TJ ET", True), (b"BT (glyph) ' ET", True),
                                                (b'BT 0 0 (glyph) " ET', True), (b"BT () Tj ET", False),
                                                (b"BT [100 -20] TJ ET", False), (b"BT [] TJ ET", False)])
def test_shading_and_glyph_painting_require_nonempty_drawn_content(operations, painted):
    assert document_parser._pdf_has_unreadable_content(_stream(operations), {}, None) is painted


def _font_resources(subtype):
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject

    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject(subtype),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    if subtype == "/Type0":
        # A real composite font needs its descendant and encoding before text
        # extraction; this CMap also keeps the existing one-byte space valid.
        base_font = NameObject("/HeiseiMin-W3")
        descriptor = DictionaryObject({
            NameObject("/Type"): NameObject("/FontDescriptor"), NameObject("/FontName"): base_font,
            NameObject("/FontBBox"): ArrayObject([NumberObject(value) for value in (-123, -257, 1001, 910)]),
            **{NameObject("/" + key): NumberObject(value) for key, value in {
                "Flags": 6, "ItalicAngle": 0, "Ascent": 723, "Descent": -241, "CapHeight": 709, "StemV": 69,
            }.items()},
        })
        descendant = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): base_font, NameObject("/FontDescriptor"): descriptor,
            NameObject("/CIDSystemInfo"): DictionaryObject({
                NameObject("/Registry"): TextStringObject("Adobe"), NameObject("/Ordering"): TextStringObject("Japan1"),
                NameObject("/Supplement"): NumberObject(2),
            }),
        })
        font.update({NameObject("/BaseFont"): base_font, NameObject("/Encoding"): NameObject("/90ms-RKSJ-H"),
                     NameObject("/DescendantFonts"): ArrayObject([descendant])})
    return DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})


def _mixed_pdf_with_font(operations, subtype):
    from pypdf import PdfReader
    from pypdf.generic import NameObject

    reader = PdfReader(io.BytesIO(_mixed_pdf(operations)))
    from pypdf import PdfWriter

    writer = PdfWriter(clone_from=reader)
    writer.pages[1][NameObject("/Resources")] = _font_resources(subtype)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("operations", [b"BT /F1 12 Tf ( ) Tj ET", b"BT /F1 12 Tf [(  ) -250 ( )] TJ ET",
                                         b"BT /F1 12 Tf ( ) ' ET"])
def test_space_only_page_in_a_simple_font_stays_a_blank_page(operations):
    parsed = parse_source("spaces.pdf", _mixed_pdf_with_font(operations, "/Type1"), SourceLimits())
    assert parsed["content"] == "A complete private reference."


@pytest.mark.parametrize("subtype", ["/Type0", "/Type3"])
def test_spaces_in_composite_or_type3_fonts_still_reject(subtype):
    with pytest.raises(SourceError) as error:
        parse_source("glyphs.pdf", _mixed_pdf_with_font(b"BT /F1 12 Tf ( ) Tj ET", subtype), SourceLimits())
    assert error.value.reason == "scan_unsupported"


@pytest.mark.parametrize("operations,painted", [(b"BT /F1 12 Tf ( ) Tj ET", False),
                                                (b"BT /F1 12 Tf [( ) -250 <20>] TJ ET", False),
                                                (b"BT /F2 12 Tf ( ) Tj ET", True), (b"BT ( ) Tj ET", True),
                                                (b"BT /F1 12 Tf <01> Tj ET", True),
                                                (b"BT /F1 12 Tf [( ) -250 (a)] TJ ET", True)])
def test_only_spaces_in_a_known_simple_font_draw_nothing(operations, painted):
    assert document_parser._pdf_has_unreadable_content(_stream(operations), _font_resources("/TrueType"), None) is painted


def test_restored_graphics_state_brings_back_the_outer_font():
    from pypdf.generic import DictionaryObject, NameObject

    resources = _font_resources("/Type0")
    resources[NameObject("/Font")][NameObject("/F2")] = DictionaryObject(
        {NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
         NameObject("/BaseFont"): NameObject("/Helvetica")})
    assert document_parser._pdf_has_unreadable_content(
        _stream(b"BT /F1 12 Tf q /F2 12 Tf Q ( ) Tj ET"), resources, None)
    assert not document_parser._pdf_has_unreadable_content(
        _stream(b"BT /F1 12 Tf q /F2 12 Tf ( ) Tj Q ET"), resources, None)


def _simple_font(**entries):
    from pypdf.generic import DictionaryObject, NameObject

    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    font.update({NameObject("/" + key): value for key, value in entries.items() if value is not None})
    if entries.get("BaseFont", "") is None:
        del font[NameObject("/BaseFont")]
    return DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})


def _font_variants():
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

    def differences(*values):
        return DictionaryObject({NameObject("/Differences"): ArrayObject(values)})

    return [
        ({"Encoding": NameObject("/WinAnsiEncoding")}, False),
        ({"Encoding": differences(NumberObject(30), NameObject("/a"), NameObject("/b"), NameObject("/space"))}, False),
        ({"Encoding": differences(NumberObject(32), NameObject("/A"))}, True),
        ({"Encoding": differences(NumberObject(31), NameObject("/a"), NameObject("/A"))}, True),
        ({"Encoding": NameObject("/Custom")}, True),
        ({"Encoding": DictionaryObject({NameObject("/BaseEncoding"): NameObject("/Custom")})}, True),
        ({"FontDescriptor": DictionaryObject({NameObject("/FontFile2"): NumberObject(0)})}, True),
        ({"FontDescriptor": DictionaryObject({NameObject("/FontName"): NameObject("/Helvetica"),
                                              NameObject("/Flags"): NumberObject(32)})}, False),
        ({"FontDescriptor": DictionaryObject({NameObject("/Flags"): NumberObject(4)}),
          "Encoding": NameObject("/WinAnsiEncoding")}, True),
        ({"FontDescriptor": DictionaryObject({NameObject("/Flags"): NumberObject(36)})}, True),
        ({"FontDescriptor": DictionaryObject({NameObject("/FontName"): NameObject("/Helvetica")})}, True),
        # Without an explicit encoding only the standard text fonts prove 32 is a space.
        ({"BaseFont": None}, True),
        ({"BaseFont": NameObject("/Arial")}, True),
        ({"BaseFont": NameObject("/Symbol")}, True),
        ({"BaseFont": NameObject("/Arial"), "Encoding": NameObject("/WinAnsiEncoding")}, False),
        ({"BaseFont": NameObject("/Arial"), "Encoding": differences(NumberObject(65), NameObject("/A"))}, True),
        ({"BaseFont": NameObject("/Times-Roman")}, False),
    ]


@pytest.mark.parametrize("index", range(17))
def test_space_exemption_requires_a_standard_space_in_a_non_embedded_font(index):
    entries, painted = _font_variants()[index]
    assert document_parser._pdf_has_unreadable_content(
        _stream(b"BT /F1 12 Tf ( ) Tj ET"), _simple_font(**entries), None) is painted


def test_forms_inherit_the_caller_font_per_scope():
    from pypdf.generic import NameObject

    resources = _font_resources("/Type1")
    form = _stream(b"BT ( ) Tj ET", subtype="/Form")
    resources[NameObject("/XObject")] = _resources(Spaces=form)[NameObject("/XObject")]
    assert not document_parser._pdf_has_unreadable_content(_stream(b"BT /F1 12 Tf ET /Spaces Do"), resources, None)
    resources[NameObject("/Font")][NameObject("/F2")] = _font_resources("/Type0")[NameObject("/Font")][NameObject("/F1")]
    assert document_parser._pdf_has_unreadable_content(
        _stream(b"BT /F1 12 Tf ET /Spaces Do BT /F2 12 Tf ET /Spaces Do"), resources, None)


@pytest.mark.parametrize("operations,fonts", [(b"BT Tf ( ) Tj ET", None), (b"BT 12 Tf ( ) Tj ET", None),
                                              (b"BT /F1 12 Tf ( ) Tj ET", 7),
                                              # A malformed Tf after a valid one resets the font.
                                              (b"BT /F1 12 Tf Tf ( ) Tj ET", None),
                                              (b"BT /F1 12 Tf /F1 Tf ( ) Tj ET", None),
                                              (b"BT /F1 12 Tf 12 /F1 Tf ( ) Tj ET", None)])
def test_malformed_font_state_is_treated_as_painting(operations, fonts):
    from pypdf.generic import DictionaryObject, NameObject, NumberObject

    resources = _font_resources("/Type1") if fonts is None else DictionaryObject(
        {NameObject("/Font"): NumberObject(fonts)})
    assert document_parser._pdf_has_unreadable_content(_stream(operations), resources, None)


def _with_ext_states(resources, **states):
    from pypdf.generic import DictionaryObject, NameObject

    resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/" + key): value for key, value in states.items()})
    return resources


@pytest.mark.parametrize("operations,painted", [
    (b"BT /F1 12 Tf /Plain gs ( ) Tj ET", False),     # a state without /Font keeps Helvetica
    (b"BT /F1 12 Tf /Glyphs gs ( ) Tj ET", True),     # gs switches to a Type3 font drawing 0x20
    (b"BT /F1 12 Tf /Missing gs ( ) Tj ET", True),    # an unresolvable state leaves the font unknown
    (b"BT /F1 12 Tf /BadFont gs ( ) Tj ET", True),    # a /Font entry without a usable font
])
def test_fonts_selected_through_ext_g_state_are_respected(operations, painted):
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

    type3 = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type3")})
    resources = _with_ext_states(
        _font_resources("/Type1"),
        Plain=DictionaryObject({NameObject("/LW"): NumberObject(1)}),
        Glyphs=DictionaryObject({NameObject("/Font"): ArrayObject([type3, NumberObject(12)])}),
        BadFont=DictionaryObject({NameObject("/Font"): NumberObject(3)}),
    )
    assert document_parser._pdf_has_unreadable_content(_stream(operations), resources, None) is painted


def test_a_page_whose_ext_g_state_switches_to_a_type3_font_is_rejected():
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject

    from pypdf import PdfReader, PdfWriter

    raw = _mixed_pdf_with_font(b"BT /F1 12 Tf /Glyphs gs ( ) Tj ET", "/Type1")
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(raw)))
    type3 = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type3")})
    resources = writer.pages[1]["/Resources"]
    resources[NameObject("/ExtGState")] = DictionaryObject({NameObject("/Glyphs"): DictionaryObject(
        {NameObject("/Font"): ArrayObject([writer._add_object(type3), NumberObject(12)])})})
    output = io.BytesIO()
    writer.write(output)
    with pytest.raises(SourceError) as error:
        parse_source("glyphs.pdf", output.getvalue(), SourceLimits())
    assert error.value.reason == "scan_unsupported"
