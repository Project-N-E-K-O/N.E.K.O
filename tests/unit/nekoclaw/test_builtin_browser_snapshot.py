# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A page as text: which nodes of the accessibility tree are kept, their uids, the lines.

The node lists are written by hand in the shape ``Accessibility.getFullAXTree``
answers with. The expectations marked "as upstream" are those of the tests of
Chrome DevTools MCP's ``SnapshotFormatter`` (the format) and of Puppeteer's
``Accessibility`` (what is interesting), which ``snapshot.py`` is a port of.
"""

from __future__ import annotations

from typing import Any

import pytest

from nekoclaw.builtin.browser.snapshot import (
    AXNode,
    SnapshotNode,
    assign_uids,
    build_ax_tree,
    find_by_uid,
    format_lines,
    iframe_nodes,
    serialize_tree,
)

# ---------------------------------------------------------------------------
# Building protocol-shaped node lists
# ---------------------------------------------------------------------------


def ax(
    role: str,
    name: str | None = None,
    *children: dict[str, Any],
    dom: int | None = None,
    ignored: bool = False,
    value: Any = None,
    description: str | None = None,
    **properties: Any,
) -> dict[str, Any]:
    """Return one node as a nested description; ``flat`` turns a tree of them into the protocol's list."""
    return {
        "role": role,
        "name": name,
        "children": list(children),
        "dom": dom,
        "ignored": ignored,
        "value": value,
        "description": description,
        "properties": properties,
    }


def flat(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the nodes of a nested description as the protocol lists them: flat, the root first."""
    payloads: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> str:
        node_id = str(len(payloads) + 1)
        payload: dict[str, Any] = {
            "nodeId": node_id,
            "ignored": node["ignored"],
            "role": {"type": "role", "value": node["role"]},
            "properties": [
                {"name": key, "value": {"type": "computed", "value": item}} for key, item in node["properties"].items()
            ],
        }
        if node["name"] is not None:
            payload["name"] = {"type": "computedString", "value": node["name"]}
        if node["value"] is not None:
            payload["value"] = {"type": "string", "value": node["value"]}
        if node["description"] is not None:
            payload["description"] = {"type": "computedString", "value": node["description"]}
        if node["dom"] is not None:
            payload["backendDOMNodeId"] = node["dom"]
        payloads.append(payload)
        payload["childIds"] = [walk(child) for child in node["children"]]
        return node_id

    walk(tree)
    return payloads


def snapshot_of(
    tree: dict[str, Any],
    *,
    snapshot_id: int = 1,
    known: dict[str, str] | None = None,
    verbose: bool = False,
    loader: str = "L1",
    frame: str = "F1",
) -> tuple[list[str], SnapshotNode, dict[str, SnapshotNode]]:
    root = build_ax_tree(flat(tree))
    assert root is not None
    serialized = serialize_tree(root, verbose=verbose, loader_id=loader, frame_id=frame)
    assert serialized is not None
    by_uid = assign_uids(serialized, snapshot_id, {} if known is None else known)
    return format_lines(serialized, verbose=verbose), serialized, by_uid


def page(*children: dict[str, Any], name: str = "Shop") -> dict[str, Any]:
    # The browser reports the root of a document as focusable, which is what keeps it.
    return ax("RootWebArea", name, *children, dom=1, focusable=True)


def node(role: str, name: str = "", uid: str = "", children: list[SnapshotNode] | None = None, **attributes: Any) -> SnapshotNode:
    """Return a snapshot node written by hand, for the format alone."""
    if name:
        attributes["name"] = name
    return SnapshotNode(
        role=role,
        attributes=attributes,
        backend_node_id=1 if uid else None,
        loader_id="L1",
        frame_id="F1",
        children=children or [],
        uid=uid,
    )


# ---------------------------------------------------------------------------
# The tree the protocol's list describes
# ---------------------------------------------------------------------------


def test_the_first_node_of_the_list_is_the_root_and_children_follow_their_ids() -> None:
    root = build_ax_tree(flat(page(ax("main", None, ax("button", "Buy", dom=3), dom=2))))
    assert root is not None
    assert root.role == "RootWebArea" and root.name == "Shop"
    assert [child.role for child in root.children] == ["main"]
    assert [child.name for child in root.children[0].children] == ["Buy"]
    assert root.children[0].children[0].backend_node_id == 3


def test_a_child_id_that_names_no_node_and_an_entry_without_an_id_are_skipped() -> None:
    payloads = flat(page(ax("button", "Buy", dom=2)))
    payloads[0]["childIds"].append("missing")
    payloads.append({"role": {"value": "button"}})
    payloads.append("not a node")  # type: ignore[arg-type]
    root = build_ax_tree(payloads)
    assert root is not None
    assert [child.name for child in root.children] == ["Buy"]


def test_an_empty_list_has_no_tree() -> None:
    assert build_ax_tree([]) is None


def test_a_node_without_a_role_or_a_name_reads_as_unknown_and_unnamed() -> None:
    bare = AXNode({"nodeId": "1"})
    assert bare.role == "Unknown"
    assert bare.name == ""
    assert bare.description == ""
    assert bare.ignored is False
    assert bare.backend_node_id is None


# ---------------------------------------------------------------------------
# What is interesting
# ---------------------------------------------------------------------------


def test_landmarks_controls_focusable_elements_and_named_leaves_are_kept_and_wrappers_are_not() -> None:
    lines, _tree, _by_uid = snapshot_of(
        page(
            ax(
                "generic",
                None,
                ax("banner", None, ax("link", "Home", ax("StaticText", "Home", dom=5), dom=4, focusable=True), dom=3),
                ax(
                    "main",
                    None,
                    ax("heading", "Welcome", ax("StaticText", "Welcome", dom=8), dom=7, level=1),
                    ax("paragraph", None, ax("StaticText", "Read this first.", dom=10), dom=9),
                    ax("button", "Buy", ax("StaticText", "Buy", dom=12), dom=11),
                    ax("img", "Logo", dom=13),
                    ax("generic", None, dom=14),
                    dom=6,
                ),
                dom=2,
            )
        )
    )
    # The link's own text (uid 1_3) is kept in the tree and folded in the text: it repeats the name.
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        "  uid=1_1 banner",
        '    uid=1_2 link "Home"',
        "  uid=1_4 main",
        '    uid=1_5 heading "Welcome" level="1"',
        '    uid=1_6 "Read this first."',
        '    uid=1_7 button "Buy"',
        '    uid=1_8 img "Logo"',
    ]


@pytest.mark.parametrize("role", ["banner", "complementary", "contentinfo", "form", "main", "navigation", "region", "search"])
def test_every_landmark_is_kept_even_without_a_name(role: str) -> None:
    lines, _tree, _by_uid = snapshot_of(page(ax(role, None, dom=2)))
    assert lines == ['uid=1_0 RootWebArea "Shop"', f"  uid=1_1 {role}"]


@pytest.mark.parametrize(
    "role",
    ["button", "checkbox", "combobox", "listbox", "menu", "menuitem", "radio", "searchbox", "slider", "spinbutton", "switch", "tab", "textbox", "tree", "treeitem"],
)
def test_a_control_is_kept_even_when_it_cannot_take_the_focus_and_has_no_name(role: str) -> None:
    lines, _tree, _by_uid = snapshot_of(page(ax(role, None, dom=2)))
    assert lines == ['uid=1_0 RootWebArea "Shop"', f"  uid=1_1 {role}"]


@pytest.mark.parametrize(
    "properties",
    [
        {"focusable": True},
        {"editable": "richtext"},
        {"busy": True},
        {"live": "polite"},
        {"modal": True},
        {"errormessage": "e1"},
        {"details": "d1"},
        {"roledescription": "slide"},
    ],
)
def test_a_wrapper_is_kept_for_what_makes_it_matter_to_a_reader(properties: dict[str, Any]) -> None:
    _lines, tree, _by_uid = snapshot_of(page(ax("generic", None, ax("button", "Buy", dom=3), dom=2, **properties)))
    assert [child.role for child in tree.children] == ["generic"]
    assert [child.role for child in tree.children[0].children] == ["button"]


@pytest.mark.parametrize("properties", [{}, {"live": "off"}, {"focusable": False}, {"busy": False}, {"modal": False}])
def test_a_wrapper_with_nothing_of_that_is_dropped_and_its_children_move_up(properties: dict[str, Any]) -> None:
    _lines, tree, _by_uid = snapshot_of(page(ax("generic", None, ax("button", "Buy", dom=3), dom=2, **properties)))
    assert [child.role for child in tree.children] == ["button"]


def test_a_leaf_needs_a_name_or_a_description_to_be_kept() -> None:
    lines, _tree, _by_uid = snapshot_of(
        page(
            ax("img", None, dom=2),
            ax("img", "Logo", dom=3),
            ax("img", None, dom=4, description="A drawing of a cat"),
        )
    )
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        '  uid=1_1 img "Logo"',
        '  uid=1_2 img description="A drawing of a cat"',
    ]


def test_hidden_and_ignored_nodes_are_left_out_whatever_else_they_are() -> None:
    lines, _tree, _by_uid = snapshot_of(
        page(
            ax("button", "Hidden", dom=2, hidden=True, focusable=True),
            ax("button", "Ignored", dom=3, ignored=True, focusable=True),
            ax("Ignored", "Role says so", dom=4, focusable=True),
            ax("navigation", "Hidden landmark", dom=5, hidden=True),
            ax("button", "Shown", dom=6),
        )
    )
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 button "Shown"']


def test_what_an_ignored_node_holds_is_still_looked_at() -> None:
    lines, _tree, _by_uid = snapshot_of(page(ax("generic", None, ax("button", "Inside", dom=3), dom=2, ignored=True)))
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 button "Inside"']


def test_text_inside_a_control_is_not_listed_but_a_focusable_element_inside_it_is() -> None:
    lines, _tree, _by_uid = snapshot_of(
        page(
            ax(
                "button",
                "Buy now",
                ax("StaticText", "Buy", dom=3),
                ax("img", "Cart", dom=4),
                ax("link", "Terms", dom=5, focusable=True),
                dom=2,
            )
        )
    )
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 button "Buy now"', '    uid=1_2 link "Terms"']


def test_a_plain_text_field_is_a_leaf_and_what_it_is_made_of_is_not_listed() -> None:
    lines, _tree, _by_uid = snapshot_of(
        page(
            ax("textbox", "Your name", ax("generic", None, ax("StaticText", "Ada", dom=4), dom=3), dom=2, focusable=True),
            ax("searchbox", "Search", ax("StaticText", "hinge", dom=6), dom=5),
            ax("generic", "Notes", ax("StaticText", "typed", dom=8), dom=7, editable="plaintext"),
        )
    )
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        '  uid=1_1 textbox "Your name"',
        '  uid=1_2 searchbox "Search"',
        '  uid=1_3 generic "Notes"',
    ]


def test_a_rich_text_field_is_not_a_leaf_so_its_text_is_listed() -> None:
    lines, _tree, _by_uid = snapshot_of(
        page(ax("generic", "Editor", ax("paragraph", None, ax("StaticText", "Dear all", dom=4), dom=3), dom=2, editable="richtext"))
    )
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 generic "Editor"', '    uid=1_2 "Dear all"']


@pytest.mark.parametrize("role", ["img", "image", "Meter", "scrollbar", "slider", "separator", "progressbar", "graphics-symbol", "doc-cover"])
def test_roles_whose_children_are_presentation_only_are_leaves(role: str) -> None:
    built = build_ax_tree(flat(ax(role, "x", ax("button", "inside", dom=2, focusable=True), dom=1)))
    assert built is not None
    assert built.is_leaf()
    lines, _tree, _by_uid = snapshot_of(page(ax(role, "Named", ax("button", "inside", dom=3, focusable=True), dom=2)))
    assert lines == ['uid=1_0 RootWebArea "Shop"', f'  uid=1_1 {role} "Named"']


def test_a_named_heading_is_a_leaf_unless_something_inside_it_takes_the_focus() -> None:
    plain = build_ax_tree(flat(ax("heading", "News", ax("StaticText", "News", dom=2), dom=1)))
    linked = build_ax_tree(
        flat(ax("heading", "News", ax("generic", None, ax("link", "News", dom=3, focusable=True), dom=2), dom=1))
    )
    unnamed = build_ax_tree(flat(ax("heading", None, ax("StaticText", "News", dom=2), dom=1)))
    assert plain is not None and linked is not None and unnamed is not None
    assert plain.is_leaf()
    assert not linked.is_leaf()
    assert not unnamed.is_leaf()
    lines, _tree, _by_uid = snapshot_of(page(ax("heading", "News", ax("StaticText", "News", dom=3), dom=2, level=2)))
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 heading "News" level="2"']
    # A heading that wraps a link is not a leaf, so only the link is listed (as upstream).
    lines, _tree, _by_uid = snapshot_of(
        page(ax("heading", "News", ax("link", "News", ax("StaticText", "News", dom=4), dom=3, focusable=True), dom=2, level=2))
    )
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 link "News"']


def test_a_node_without_children_is_a_leaf_and_a_plain_container_is_not() -> None:
    empty = build_ax_tree(flat(ax("generic", None, dom=1)))
    holder = build_ax_tree(flat(ax("generic", None, ax("StaticText", "x", dom=2), dom=1)))
    assert empty is not None and holder is not None
    assert empty.is_leaf()
    assert not holder.is_leaf()


def test_line_fragments_of_text_are_dropped_in_both_kinds_of_snapshot() -> None:
    tree = page(ax("paragraph", None, ax("StaticText", "alpha", ax("InlineTextBox", "alpha"), dom=3), dom=2))
    lines, _tree, _by_uid = snapshot_of(tree)
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 "alpha"']
    verbose, _tree, _by_uid = snapshot_of(tree, verbose=True)
    assert verbose == ['uid=1_0 RootWebArea "Shop"', "  uid=1_1 paragraph", '    uid=1_2 "alpha"']
    assert all("InlineTextBox" not in line for line in verbose)


def test_a_verbose_snapshot_keeps_every_node() -> None:
    tree = page(
        ax(
            "generic",
            None,
            ax("none", None, ax("StaticText", "Only text", dom=4), dom=3, ignored=True),
            ax("button", "Hidden", dom=5, hidden=True),
            ax("button", "Buy", ax("StaticText", "Buy", dom=7), dom=6),
            dom=2,
        )
    )
    lines, _tree, _by_uid = snapshot_of(tree, verbose=True)
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        "  uid=1_1 generic",
        "    uid=1_2 ignored",
        '      uid=1_3 "Only text"',
        '    uid=1_4 button "Hidden"',
        '    uid=1_5 button "Buy"',
        '      uid=1_6 "Buy"',
    ]
    brief, _tree, _by_uid = snapshot_of(tree)
    assert brief == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 "Only text"', '  uid=1_2 button "Buy"']


# ---------------------------------------------------------------------------
# The properties that are kept
# ---------------------------------------------------------------------------


def test_only_the_known_properties_are_kept_and_their_names_are_lower_cased() -> None:
    built = AXNode(
        flat(
            ax(
                "textbox",
                "Email",
                dom=1,
                value="a@b.c",
                description="Work address",
                focusable=True,
                editable="plaintext",
                settable=True,
                multiline=False,
                readonly=False,
                required=True,
                invalid="false",
                autocomplete="list",
                hasPopup="menu",
                keyshortcuts="Alt+E",
                roledescription="mail field",
                valuetext="a at b",
                url="http://127.0.0.1/",
                level=2,
                valuemin=0,
                valuemax=10,
            )
        )[0]
    )
    assert built.attributes() == {
        "name": "Email",
        "value": "a@b.c",
        "description": "Work address",
        "keyshortcuts": "Alt+E",
        "roledescription": "mail field",
        "valuetext": "a at b",
        "url": "http://127.0.0.1/",
        "multiline": False,
        "readonly": False,
        "required": True,
        "level": 2,
        "valuemax": 10,
        "valuemin": 0,
        "autocomplete": "list",
        "haspopup": "menu",
    }


def test_the_root_of_a_document_does_not_report_focus() -> None:
    root = AXNode(flat(ax("RootWebArea", "Shop", dom=1, focusable=True, focused=True))[0])
    assert "focused" not in root.attributes()
    field = AXNode(flat(ax("textbox", "Name", dom=1, focusable=True, focused=True))[0])
    assert field.attributes()["focused"] is True


@pytest.mark.parametrize(
    ("given", "kept"),
    [("true", True), ("false", False), ("mixed", "mixed"), (True, True), (False, False)],
)
def test_checked_and_pressed_are_true_false_or_mixed(given: Any, kept: Any) -> None:
    box = AXNode(flat(ax("checkbox", "Agree", dom=1, checked=given, pressed=given))[0])
    assert box.attributes()["checked"] == kept
    assert box.attributes()["pressed"] == kept
    assert type(box.attributes()["checked"]) is type(kept)


@pytest.mark.parametrize("token", ["autocomplete", "haspopup", "invalid", "orientation", "live", "relevant", "errormessage", "details"])
def test_a_token_property_that_is_false_or_empty_is_left_out(token: str) -> None:
    for nothing in ("false", "", None, False):
        assert token not in AXNode(flat(ax("textbox", "x", dom=1, **{token: nothing}))[0]).attributes()
    assert AXNode(flat(ax("textbox", "x", dom=1, **{token: "yes"}))[0]).attributes()[token] == "yes"


def test_an_option_takes_its_text_as_its_value() -> None:
    _lines, tree, _by_uid = snapshot_of(
        page(
            ax(
                "combobox",
                "Product",
                ax("option", "Brass Hinge", dom=3, focusable=True, selected=True),
                ax("option", None, dom=4, focusable=True, selected=False),
                dom=2,
                focusable=True,
                value="Brass Hinge",
            )
        )
    )
    named, unnamed = tree.children[0].children
    assert named.attributes["value"] == "Brass Hinge"
    assert "value" not in unnamed.attributes


# ---------------------------------------------------------------------------
# The line format
# ---------------------------------------------------------------------------


def test_a_line_is_uid_role_quoted_name_then_the_properties_in_alphabetical_order() -> None:
    line = format_lines(
        node(
            "textbox",
            "Email",
            "4_2",
            value="a@b.c",
            required=True,
            multiline=False,
            autocomplete="list",
            description="Work address",
            level=3,
        ),
        verbose=False,
    )
    assert line == ['uid=4_2 textbox "Email" autocomplete="list" description="Work address" level="3" required value="a@b.c"']


def test_a_boolean_that_is_present_says_what_the_element_can_be_and_only_true_says_what_it_is() -> None:
    can_be = node("button", "Menu", "1_1", disabled=False, expanded=False, focused=False, selected=False)
    assert format_lines(can_be, verbose=False) == ['uid=1_1 button "Menu" disableable expandable focusable selectable']
    is_now = node("button", "Menu", "1_1", disabled=True, expanded=True, focused=True, selected=True)
    assert format_lines(is_now, verbose=False) == [
        'uid=1_1 button "Menu" disableable disabled expandable expanded focusable focused selectable selected'
    ]


def test_other_booleans_are_listed_only_when_true() -> None:
    line = format_lines(
        node("textbox", "Notes", "1_1", multiline=True, readonly=False, required=False, modal=True, busy=False, atomic=True),
        verbose=False,
    )
    assert line == ['uid=1_1 textbox "Notes" atomic modal multiline']


def test_checked_is_a_word_when_true_absent_when_false_and_spelled_out_when_mixed() -> None:
    assert format_lines(node("checkbox", "Agree", "1_1", checked=True), verbose=False) == ['uid=1_1 checkbox "Agree" checked']
    assert format_lines(node("checkbox", "Agree", "1_1", checked=False), verbose=False) == ['uid=1_1 checkbox "Agree"']
    assert format_lines(node("checkbox", "Agree", "1_1", checked="mixed"), verbose=False) == [
        'uid=1_1 checkbox "Agree" checked="mixed"'
    ]
    assert format_lines(node("button", "Bold", "1_1", pressed=True), verbose=False) == ['uid=1_1 button "Bold" pressed']
    assert format_lines(node("button", "Bold", "1_1", pressed="mixed"), verbose=False) == ['uid=1_1 button "Bold" pressed="mixed"']


def test_numbers_and_strings_are_written_as_key_and_quoted_value() -> None:
    line = format_lines(node("slider", "Volume", "1_4", valuemin=0, valuemax=100, value="35", orientation="horizontal"), verbose=False)
    assert line == ['uid=1_4 slider "Volume" orientation="horizontal" value="35" valuemax="100" valuemin="0"']


def test_static_text_has_no_role_word_and_a_node_without_a_dom_node_has_no_uid() -> None:
    assert format_lines(node("StaticText", "Total: 12", "2_7"), verbose=False) == ['uid=2_7 "Total: 12"']
    assert format_lines(node("InlineTextBox", "alpha"), verbose=False) == ['InlineTextBox "alpha"']
    assert format_lines(node("generic"), verbose=False) == ["generic"]


def test_the_role_none_is_printed_as_ignored() -> None:
    # As upstream: "formats a node with role none as ignored".
    root = node("none", "", "1_1", [node("statictext", "text", "1_2")])
    assert format_lines(root, verbose=False) == ["uid=1_1 ignored", '  uid=1_2 statictext "text"']


def test_an_option_does_not_repeat_its_text_as_its_value_and_is_never_called_selectable() -> None:
    # As upstream: "omits redundant option attributes in text output".
    root = node(
        "combobox",
        "Language",
        "1_1",
        [
            node("option", "English", "1_2", value="English", selected=False),
            node("option", "Deutsch", "1_3", value="de", selected=True),
        ],
    )
    assert format_lines(root, verbose=False) == [
        'uid=1_1 combobox "Language"',
        '  uid=1_2 option "English"',
        '  uid=1_3 option "Deutsch" selected value="de"',
    ]


def test_an_option_can_still_be_disableable_and_focusable() -> None:
    line = format_lines(node("option", "English", "1_2", value="English", disabled=True, focused=False), verbose=False)
    assert line == ['uid=1_2 option "English" disableable disabled focusable']


def test_a_long_address_is_kept_whole_and_a_data_url_is_shortened() -> None:
    address = "https://example.com/?q=" + "a" * 500
    assert format_lines(node("link", "Next", "1_1", url=address), verbose=False) == [f'uid=1_1 link "Next" url="{address}"']
    data = "data:image/png;base64," + "A" * 5000
    line = format_lines(node("image", "logo", "1_1", url=data), verbose=False)[0]
    assert line == f'uid=1_1 image "logo" url="{data[:60]}..."'
    short = "data:text/plain,hello"
    assert format_lines(node("link", "Tiny", "1_1", url=short), verbose=False) == [f'uid=1_1 link "Tiny" url="{short}"']


def test_only_the_url_is_shortened_not_another_property_that_holds_a_data_url() -> None:
    data = "data:image/png;base64," + "A" * 200
    assert format_lines(node("textbox", "Paste", "1_1", value=data), verbose=False) == [f'uid=1_1 textbox "Paste" value="{data}"']


def test_lines_are_indented_two_spaces_for_each_level() -> None:
    root = node(
        "RootWebArea",
        "Deep",
        "1_0",
        [
            node("main", "", "1_1", [node("form", "", "1_2", [node("button", "Send", "1_3")]), node("link", "Help", "1_4")]),
            node("contentinfo", "", "1_5"),
        ],
    )
    assert format_lines(root, verbose=False) == [
        'uid=1_0 RootWebArea "Deep"',
        "  uid=1_1 main",
        "    uid=1_2 form",
        '      uid=1_3 button "Send"',
        '    uid=1_4 link "Help"',
        "  uid=1_5 contentinfo",
    ]


class TestTextChildrenThatOnlyRepeatTheName:
    """As upstream's formatter tests of the same names."""

    def test_the_only_text_child_that_repeats_the_name_is_folded(self) -> None:
        root = node("link", "Privacy Policy", "1_1", [node("StaticText", "Privacy Policy", "1_2")])
        assert format_lines(root, verbose=False) == ['uid=1_1 link "Privacy Policy"']

    def test_it_is_kept_in_a_verbose_snapshot(self) -> None:
        root = node("link", "Privacy Policy", "1_1", [node("StaticText", "Privacy Policy", "1_2")])
        assert format_lines(root, verbose=True) == ['uid=1_1 link "Privacy Policy"', '  uid=1_2 "Privacy Policy"']

    def test_it_is_kept_when_it_has_a_sibling_that_is_not_text(self) -> None:
        root = node("link", "Privacy Policy", "1_1", [node("StaticText", "Privacy Policy", "1_2"), node("image", "icon", "1_3")])
        assert format_lines(root, verbose=False) == [
            'uid=1_1 link "Privacy Policy"',
            '  uid=1_2 "Privacy Policy"',
            '  uid=1_3 image "icon"',
        ]

    def test_several_text_children_that_add_up_to_the_name_are_folded(self) -> None:
        root = node(
            "link",
            "English 7,245,000+ articles",
            "1_1",
            [node("StaticText", "English", "1_2"), node("StaticText", "7,245,000+ ", "1_3"), node("StaticText", "articles", "1_4")],
        )
        assert format_lines(root, verbose=False) == ['uid=1_1 link "English 7,245,000+ articles"']

    def test_text_children_that_say_something_else_are_kept(self) -> None:
        root = node("link", "English", "1_1", [node("StaticText", "English", "1_2"), node("StaticText", "articles", "1_3")])
        assert format_lines(root, verbose=False) == ['uid=1_1 link "English"', '  uid=1_2 "English"', '  uid=1_3 "articles"']

    def test_a_text_child_of_a_node_without_a_name_is_kept(self) -> None:
        root = node("paragraph", "", "1_1", [node("StaticText", "Hello", "1_2")])
        assert format_lines(root, verbose=False) == ["uid=1_1 paragraph", '  uid=1_2 "Hello"']

    def test_a_text_child_with_children_of_its_own_is_kept(self) -> None:
        root = node("link", "Hello", "1_1", [node("StaticText", "Hello", "1_2", [node("InlineTextBox", "Hello")])])
        assert format_lines(root, verbose=False) == ['uid=1_1 link "Hello"', '  uid=1_2 "Hello"', '    InlineTextBox "Hello"']


class TestUpstreamFormatterCases:
    """The remaining cases of upstream's formatter tests, with their expected text."""

    def test_the_static_text_role_is_omitted(self) -> None:
        root = node("link", "Privacy", "1_1", [node("StaticText", "Privacy Policy", "1_2")])
        assert format_lines(root, verbose=False) == ['uid=1_1 link "Privacy"', '  uid=1_2 "Privacy Policy"']

    def test_value_properties(self) -> None:
        root = node(
            "textbox",
            "textbox",
            "1_1",
            [node("statictext", "text", "1_2")],
            value="value",
            live="polite",
            relevant="additions",
            errormessage="error-id",
            details="details-id",
        )
        assert format_lines(root, verbose=False) == [
            'uid=1_1 textbox "textbox" details="details-id" errormessage="error-id" live="polite" relevant="additions" value="value"',
            '  uid=1_2 statictext "text"',
        ]

    def test_boolean_properties(self) -> None:
        root = node("button", "button", "1_1", [node("statictext", "text", "1_2")], disabled=True, busy=True, atomic=True)
        assert format_lines(root, verbose=False) == [
            'uid=1_1 button "button" atomic busy disableable disabled',
            '  uid=1_2 statictext "text"',
        ]

    def test_checked_properties(self) -> None:
        root = node("checkbox", "checkbox", "1_1", [node("statictext", "text", "1_2")], checked=True)
        assert format_lines(root, verbose=False) == ['uid=1_1 checkbox "checkbox" checked', '  uid=1_2 statictext "text"']

    def test_several_kinds_of_properties_together(self) -> None:
        root = node(
            "root",
            "root",
            "1_1",
            [
                node("button", "button", "1_2", focused=True, disabled=True),
                node("textbox", "textbox", "1_3", value="value"),
                node("slider", "volume", "1_4", valuemin=0, valuemax=100),
            ],
        )
        assert format_lines(root, verbose=False) == [
            'uid=1_1 root "root"',
            '  uid=1_2 button "button" disableable disabled focusable focused',
            '  uid=1_3 textbox "textbox" value="value"',
            '  uid=1_4 slider "volume" valuemax="100" valuemin="0"',
        ]


def test_the_example_of_the_module_reads_as_documented() -> None:
    lines, _tree, _by_uid = snapshot_of(
        ax(
            "RootWebArea",
            "Order form",
            ax("textbox", "Your name", dom=2, focusable=True, focused=False, editable="plaintext", multiline=False),
            ax(
                "combobox",
                "Product",
                ax("MenuListPopup", None, ax("option", "Brass Hinge", dom=5, focusable=True, selected=False), dom=4),
                dom=3,
                focusable=True,
                expanded=False,
                hasPopup="menu",
                value="-- choose --",
            ),
            dom=1,
            focusable=True,
            url="http://127.0.0.1/order",
        ),
        snapshot_id=3,
    )
    assert lines == [
        'uid=3_0 RootWebArea "Order form" url="http://127.0.0.1/order"',
        '  uid=3_1 textbox "Your name" focusable',
        '  uid=3_2 combobox "Product" expandable haspopup="menu" value="-- choose --"',
        '    uid=3_3 option "Brass Hinge"',
    ]


# ---------------------------------------------------------------------------
# uids
# ---------------------------------------------------------------------------


def _three_buttons(*names_and_ids: tuple[str, int]) -> dict[str, Any]:
    return page(ax("main", None, *(ax("button", name, dom=dom) for name, dom in names_and_ids), dom=2))


def test_uids_are_the_snapshot_number_and_a_count_in_document_order() -> None:
    lines, tree, by_uid = snapshot_of(
        page(
            ax("navigation", None, ax("link", "One", dom=11, focusable=True), ax("link", "Two", dom=12, focusable=True), dom=10),
            ax("main", None, ax("button", "Three", dom=21), dom=20),
        ),
        snapshot_id=7,
    )
    assert lines == [
        'uid=7_0 RootWebArea "Shop"',
        "  uid=7_1 navigation",
        '    uid=7_2 link "One"',
        '    uid=7_3 link "Two"',
        "  uid=7_4 main",
        '    uid=7_5 button "Three"',
    ]
    assert list(by_uid) == ["7_0", "7_1", "7_2", "7_3", "7_4", "7_5"]
    assert by_uid["7_3"].name == "Two" and by_uid["7_3"].backend_node_id == 12
    assert by_uid["7_0"] is tree


def test_a_node_keeps_its_uid_in_the_next_snapshot_and_a_new_node_gets_a_new_one() -> None:
    known: dict[str, str] = {}
    first, _tree, _by_uid = snapshot_of(_three_buttons(("A", 3), ("B", 4)), snapshot_id=1, known=known)
    assert first == ['uid=1_0 RootWebArea "Shop"', "  uid=1_1 main", '    uid=1_2 button "A"', '    uid=1_3 button "B"']
    second, _tree, by_uid = snapshot_of(_three_buttons(("New", 9), ("A", 3), ("B", 4)), snapshot_id=2, known=known)
    assert second == [
        'uid=1_0 RootWebArea "Shop"',
        "  uid=1_1 main",
        '    uid=2_0 button "New"',
        '    uid=1_2 button "A"',
        '    uid=1_3 button "B"',
    ]
    assert by_uid["1_2"].backend_node_id == 3
    assert by_uid["2_0"].backend_node_id == 9
    assert known == {"L1_1": "1_0", "L1_2": "1_1", "L1_9": "2_0", "L1_3": "1_2", "L1_4": "1_3"}


def test_a_node_that_disappeared_is_forgotten_and_gets_a_new_uid_when_it_comes_back() -> None:
    known: dict[str, str] = {}
    snapshot_of(_three_buttons(("A", 3), ("B", 4)), snapshot_id=1, known=known)
    without, _tree, by_uid = snapshot_of(_three_buttons(("A", 3)), snapshot_id=2, known=known)
    assert without == ['uid=1_0 RootWebArea "Shop"', "  uid=1_1 main", '    uid=1_2 button "A"']
    assert "1_3" not in by_uid
    assert "L1_4" not in known
    back, _tree, by_uid = snapshot_of(_three_buttons(("A", 3), ("B", 4)), snapshot_id=3, known=known)
    assert back[-1] == '    uid=3_0 button "B"'
    assert "1_3" not in by_uid


def test_the_same_dom_node_numbers_in_another_document_get_new_uids() -> None:
    known: dict[str, str] = {}
    snapshot_of(_three_buttons(("A", 3)), snapshot_id=1, known=known, loader="L1")
    lines, _tree, by_uid = snapshot_of(_three_buttons(("A", 3)), snapshot_id=2, known=known, loader="L2")
    assert lines == ['uid=2_0 RootWebArea "Shop"', "  uid=2_1 main", '    uid=2_2 button "A"']
    assert set(by_uid) == {"2_0", "2_1", "2_2"}
    assert known == {"L2_1": "2_0", "L2_2": "2_1", "L2_3": "2_2"}
    assert {item.loader_id for item in by_uid.values()} == {"L2"}


def test_a_node_without_a_dom_node_gets_no_uid_and_uses_up_no_number() -> None:
    lines, tree, by_uid = snapshot_of(
        page(ax("button", "First", dom=2), ax("button", "Made up by the browser"), ax("button", "Last", dom=3))
    )
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        '  uid=1_1 button "First"',
        '  button "Made up by the browser"',
        '  uid=1_2 button "Last"',
    ]
    assert tree.children[1].uid == ""
    assert set(by_uid) == {"1_0", "1_1", "1_2"}


def test_two_nodes_that_stand_for_one_dom_node_get_a_uid_each() -> None:
    lines, _tree, by_uid = snapshot_of(page(ax("button", "Twice", dom=5), ax("img", "Twice too", dom=5)))
    assert lines == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 button "Twice"', '  uid=1_2 img "Twice too"']
    assert by_uid["1_1"].backend_node_id == by_uid["1_2"].backend_node_id == 5


def test_every_node_of_a_snapshot_carries_the_document_and_the_frame_it_was_read_from() -> None:
    _lines, tree, by_uid = snapshot_of(_three_buttons(("A", 3)), loader="LOADER-9", frame="FRAME-9")
    assert {(item.loader_id, item.frame_id) for item in by_uid.values()} == {("LOADER-9", "FRAME-9")}
    assert tree.loader_id == "LOADER-9"


def test_find_by_uid_walks_the_tree() -> None:
    _lines, tree, by_uid = snapshot_of(_three_buttons(("A", 3), ("B", 4)))
    assert find_by_uid(tree, "1_3") is by_uid["1_3"]
    assert find_by_uid(tree, "1_0") is tree
    assert find_by_uid(tree, "9_9") is None


# ---------------------------------------------------------------------------
# Embedded documents
# ---------------------------------------------------------------------------


def _with_frame(inner: dict[str, Any], *, verbose: bool = False, known: dict[str, str] | None = None, snapshot_id: int = 1) -> tuple[list[str], dict[str, SnapshotNode]]:
    outer = build_ax_tree(
        flat(
            page(
                ax("button", "Before", dom=2),
                ax("generic", None, ax("Iframe", None, dom=4), dom=3),
                ax("button", "After", dom=5),
            )
        )
    )
    assert outer is not None
    frames = iframe_nodes(outer)
    assert [item.backend_node_id for item in frames] == [4]
    inner_root = build_ax_tree(flat(inner))
    assert inner_root is not None
    frames[0].frame = serialize_tree(inner_root, verbose=verbose, loader_id="INNER", frame_id="FRAME-IN")
    tree = serialize_tree(outer, verbose=verbose, loader_id="OUTER", frame_id="FRAME-OUT")
    assert tree is not None
    by_uid = assign_uids(tree, snapshot_id, {} if known is None else known)
    return format_lines(tree, verbose=verbose), by_uid


def test_only_iframe_nodes_that_stand_for_a_dom_node_are_embedded_documents() -> None:
    root = build_ax_tree(
        flat(page(ax("Iframe", None, dom=2), ax("Iframe", None), ax("generic", None, ax("Iframe", "Ad", dom=4), dom=3)))
    )
    assert root is not None
    assert sorted(item.backend_node_id for item in iframe_nodes(root)) == [2, 4]


def test_the_tree_of_an_embedded_document_hangs_below_its_iframe_with_uids_of_its_own_document() -> None:
    inner = ax("RootWebArea", "Pay", ax("textbox", "Card number", dom=2, focusable=True), dom=1, focusable=True)
    known: dict[str, str] = {}
    lines, by_uid = _with_frame(inner, known=known)
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        '  uid=1_1 button "Before"',
        "  uid=1_2 Iframe",
        '    uid=1_3 RootWebArea "Pay"',
        '      uid=1_4 textbox "Card number"',
        '  uid=1_5 button "After"',
    ]
    card = by_uid["1_4"]
    assert (card.loader_id, card.frame_id, card.backend_node_id) == ("INNER", "FRAME-IN", 2)
    before = by_uid["1_1"]
    assert (before.loader_id, before.frame_id, before.backend_node_id) == ("OUTER", "FRAME-OUT", 2)
    # The two documents number their nodes independently: 2 is a different element in each.
    assert known["OUTER_2"] == "1_1" and known["INNER_2"] == "1_4"


def test_an_iframe_without_a_document_that_could_be_read_is_not_listed() -> None:
    outer = build_ax_tree(flat(page(ax("button", "Before", dom=2), ax("Iframe", None, dom=4))))
    assert outer is not None
    tree = serialize_tree(outer, verbose=False, loader_id="OUTER", frame_id="FRAME-OUT")
    assert tree is not None
    assign_uids(tree, 1, {})
    assert format_lines(tree, verbose=False) == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 button "Before"']


def test_the_elements_of_an_embedded_document_keep_their_uids_while_the_outer_document_is_replaced() -> None:
    inner = ax("RootWebArea", "Pay", ax("textbox", "Card number", dom=2, focusable=True), dom=1, focusable=True)
    known: dict[str, str] = {}
    _lines, first = _with_frame(inner, known=known, snapshot_id=1)
    lines, second = _with_frame(inner, known=known, snapshot_id=2)
    assert set(first) == set(second)
    assert lines[4] == '      uid=1_4 textbox "Card number"'


def test_text_with_line_breaks_is_one_line_of_the_snapshot() -> None:
    code = "def main():\n    return 1\r\n\u2028done"
    lines, _tree, _by_uid = snapshot_of(
        page(ax("textbox", "Notes", dom=2, focusable=True, value="first line\nsecond line"), ax("StaticText", code, dom=3))
    )
    assert lines == [
        'uid=1_0 RootWebArea "Shop"',
        '  uid=1_1 textbox "Notes" value="first line\\nsecond line"',
        '  uid=1_2 "def main():\\n    return 1\\n\\ndone"',
    ]
    assert all("\n" not in line and "\r" not in line for line in lines)


@pytest.mark.parametrize("verbose", [False, True])
def test_a_page_nested_deeper_than_python_calls_itself_is_listed_all_the_same(verbose: bool) -> None:
    import sys

    depth = sys.getrecursionlimit() * 3
    payloads: list[dict[str, Any]] = []
    for number in range(1, depth + 1):
        payloads.append(
            {
                "nodeId": str(number),
                "ignored": False,
                "role": {"type": "role", "value": "generic" if 1 < number < depth else ("RootWebArea" if number == 1 else "button")},
                "name": {"type": "computedString", "value": "Deep" if number in (1, depth) else ""},
                "properties": [{"name": "focusable", "value": {"type": "boolean", "value": True}}] if number in (1, depth) else [],
                "backendDOMNodeId": number,
                "childIds": [str(number + 1)] if number < depth else [],
            }
        )
    root = build_ax_tree(payloads)
    assert root is not None
    tree = serialize_tree(root, verbose=verbose, loader_id="L1", frame_id="F1")
    assert tree is not None
    by_uid = assign_uids(tree, 1, {})
    lines = format_lines(tree, verbose=verbose)
    if verbose:
        assert len(lines) == depth and len(by_uid) == depth
    else:
        # The wrappers in between are dropped; the button at the bottom is found.
        assert lines == ['uid=1_0 RootWebArea "Deep"', '  uid=1_1 button "Deep"']


def test_a_list_that_names_a_node_as_its_own_ancestor_does_not_go_round_for_ever() -> None:
    payloads = flat(page(ax("button", "Go", dom=2, focusable=True)))
    # Malformed: the button names the root as its child.
    payloads[1]["childIds"] = ["1"]
    root = build_ax_tree(payloads)
    assert root is not None
    tree = serialize_tree(root, verbose=False, loader_id="L1", frame_id="F1")
    assert tree is not None
    assign_uids(tree, 1, {})
    assert format_lines(tree, verbose=False) == ['uid=1_0 RootWebArea "Shop"', '  uid=1_1 button "Go"']
