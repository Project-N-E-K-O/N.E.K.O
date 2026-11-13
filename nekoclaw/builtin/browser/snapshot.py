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
#
# ---------------------------------------------------------------------------
# The logic of this module is taken from two projects, both distributed under
# the Apache License, Version 2.0:
#
# * Puppeteer, https://github.com/puppeteer/puppeteer,
#   packages/puppeteer-core/src/cdp/Accessibility.ts (Copyright 2018 Google
#   Inc.): which nodes of the accessibility tree are "interesting", which are
#   leaves and controls, and which of a node's properties are kept.
# * Chrome DevTools MCP, https://github.com/ChromeDevTools/chrome-devtools-mcp,
#   commit ec6fd2cf7a84434f42efe3e886ce2c6ee7b8d83b, src/TextSnapshot.ts and
#   src/formatters/SnapshotFormatter.ts (Copyright 2025 Google LLC): how uids
#   are given and kept from one snapshot to the next, and the text format.
#
# It is a port to Python on top of raw protocol answers; nothing of either is
# included as it is.
# ---------------------------------------------------------------------------

"""A page as text: its accessibility tree, every element with a uid.

The browser's ``Accessibility.getFullAXTree`` returns every node it knows.
Most of them mean nothing to a reader (wrappers, layout boxes), so unless a
verbose snapshot is asked for only the interesting ones are kept, the way a
screen reader would see the page: landmarks, controls, focusable elements,
and leaves that have a name.

Each kept node that stands for a DOM node gets a uid ``<snapshot>_<number>``.
A node that was in the snapshot before keeps the uid it had, so a uid stays
valid while its element stays on the page; an element of a document that has
been replaced (a navigation) does not come back, and its uid is refused.

One line per node, indented by depth::

    uid=3_0 RootWebArea "Order form" url="http://127.0.0.1/order"
      uid=3_4 textbox "Your name" focusable
      uid=3_9 combobox "Product" expandable haspopup="menu" value="-- choose --"
        uid=3_10 option "Brass Hinge"

This module is pure: it turns protocol answers into the tree and the text.
Asking the browser is done by ``session.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_USER_STRING_PROPERTIES = ("name", "value", "description", "keyshortcuts", "roledescription", "valuetext", "url")
_BOOLEAN_PROPERTIES = (
    "disabled",
    "expanded",
    "focused",
    "modal",
    "multiline",
    "multiselectable",
    "readonly",
    "required",
    "selected",
    "busy",
    "atomic",
)
_TRISTATE_PROPERTIES = ("checked", "pressed")
_NUMERICAL_PROPERTIES = ("level", "valuemax", "valuemin")
_TOKEN_PROPERTIES = ("autocomplete", "haspopup", "invalid", "orientation", "live", "relevant", "errormessage", "details")

_LEAF_ROLES = frozenset(
    {"doc-cover", "graphics-symbol", "img", "image", "Meter", "scrollbar", "slider", "separator", "progressbar"}
)
_CONTROL_ROLES = frozenset(
    {
        "button",
        "checkbox",
        "ColorWell",
        "combobox",
        "DisclosureTriangle",
        "listbox",
        "menu",
        "menubar",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "radio",
        "scrollbar",
        "searchbox",
        "slider",
        "spinbutton",
        "switch",
        "tab",
        "textbox",
        "tree",
        "treeitem",
    }
)
_LANDMARK_ROLES = frozenset({"banner", "complementary", "contentinfo", "form", "main", "navigation", "region", "search"})
_TEXT_ONLY_ROLES = frozenset({"LineBreak", "text", "InlineTextBox", "StaticText"})

# A boolean property that is present says the element can be in that state;
# the state itself is listed only when it is true.
_BOOLEAN_CAPABILITY = {"disabled": "disableable", "expanded": "expandable", "focused": "focusable", "selected": "selectable"}

_DATA_URL_SHOWN_CHARACTERS = 60


def _value(wrapper: Any) -> Any:
    return wrapper.get("value") if isinstance(wrapper, dict) else None


class AXNode:
    """One node of the browser's accessibility tree, as the protocol gave it."""

    __slots__ = (
        "payload",
        "children",
        "frame",
        "role",
        "name",
        "description",
        "ignored",
        "_properties",
        "_editable",
        "_richly_editable",
        "_focusable",
        "_hidden",
        "_busy",
        "_modal",
        "_live",
        "_roledescription",
        "_has_errormessage",
        "_has_details",
        "_has_focusable_child",
    )

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.children: list[AXNode] = []
        # The snapshot of the document inside, for an ``Iframe`` node.
        self.frame: SnapshotNode | None = None
        role = _value(payload.get("role"))
        self.role = str(role) if role else "Unknown"
        self.name = str(_value(payload.get("name")) or "")
        description = _value(payload.get("description"))
        self.description = str(description) if description else ""
        self.ignored = bool(payload.get("ignored"))
        self._properties: dict[str, Any] = {}
        self._editable = False
        self._richly_editable = False
        self._focusable = False
        self._hidden = False
        self._busy = False
        self._modal = False
        self._live: Any = None
        self._roledescription: Any = None
        self._has_errormessage = False
        self._has_details = False
        self._has_focusable_child: bool | None = None
        for item in payload.get("properties") or ():
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            value = _value(item.get("value"))
            self._properties[name.lower()] = value
            if name == "editable":
                self._richly_editable = value == "richtext"
                self._editable = True
            elif name == "focusable":
                self._focusable = bool(value)
            elif name == "hidden":
                self._hidden = bool(value)
            elif name == "busy":
                self._busy = bool(value)
            elif name == "live":
                self._live = value
            elif name == "modal":
                self._modal = bool(value)
            elif name == "roledescription":
                self._roledescription = value
            elif name == "errormessage":
                self._has_errormessage = True
            elif name == "details":
                self._has_details = True

    @property
    def backend_node_id(self) -> int | None:
        value = self.payload.get("backendDOMNodeId")
        return int(value) if isinstance(value, int) else None

    def _plain_text_field(self) -> bool:
        if self._richly_editable:
            return False
        if self._editable:
            return True
        return self.role in ("textbox", "searchbox")

    def has_focusable_child(self) -> bool:
        if self._has_focusable_child is None:
            self._has_focusable_child = any(child._focusable or child.has_focusable_child() for child in self.children)
        return self._has_focusable_child

    def is_leaf(self) -> bool:
        if not self.children:
            return True
        if self._plain_text_field() or self.role in _TEXT_ONLY_ROLES:
            return True
        if self.role in _LEAF_ROLES:
            return True
        if self.has_focusable_child():
            return False
        return self.role == "heading" and bool(self.name)

    def is_control(self) -> bool:
        return self.role in _CONTROL_ROLES

    def is_interesting(self, inside_control: bool) -> bool:
        if self.role == "Ignored" or self._hidden or self.ignored:
            return False
        if self.role in _LANDMARK_ROLES:
            return True
        if (
            self._focusable
            or self._richly_editable
            or self._busy
            or (self._live and self._live != "off")
            or self._modal
            or self._has_errormessage
            or self._has_details
            or self._roledescription
        ):
            return True
        if self.is_control():
            return True
        if inside_control:
            return False
        return self.is_leaf() and bool(self.name or self.description)

    def attributes(self) -> dict[str, Any]:
        """Return the properties that are kept, by name."""
        properties = dict(self._properties)
        for key in ("name", "value", "description"):
            wrapper = self.payload.get(key)
            if isinstance(wrapper, dict) and "value" in wrapper:
                properties[key] = wrapper["value"]
        kept: dict[str, Any] = {}
        for key in _USER_STRING_PROPERTIES:
            if key in properties:
                kept[key] = properties[key]
        for key in _BOOLEAN_PROPERTIES:
            if key == "focused" and self.role == "RootWebArea":
                continue
            if key in properties:
                kept[key] = bool(properties[key])
        for key in _TRISTATE_PROPERTIES:
            if key in properties:
                value = properties[key]
                kept[key] = "mixed" if value == "mixed" else value in (True, "true")
        for key in _NUMERICAL_PROPERTIES:
            if key in properties:
                kept[key] = properties[key]
        for key in _TOKEN_PROPERTIES:
            value = properties.get(key)
            if value and value != "false":
                kept[key] = value
        return kept


def build_ax_tree(payloads: list[dict[str, Any]]) -> AXNode | None:
    """Return the root of the tree the protocol's flat node list describes."""
    by_id: dict[str, AXNode] = {}
    for payload in payloads:
        if isinstance(payload, dict) and "nodeId" in payload:
            by_id[str(payload["nodeId"])] = AXNode(payload)
    for node in by_id.values():
        for child_id in node.payload.get("childIds") or ():
            child = by_id.get(str(child_id))
            if child is not None:
                node.children.append(child)
    return next(iter(by_id.values()), None)


def iframe_nodes(root: AXNode) -> list[AXNode]:
    """Return the nodes that stand for an embedded document."""
    found: list[AXNode] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.role == "Iframe" and node.backend_node_id is not None:
            found.append(node)
        stack.extend(node.children)
    return found


@dataclass(slots=True)
class SnapshotNode:
    """One node of a snapshot."""

    role: str
    attributes: dict[str, Any]
    backend_node_id: int | None
    # The document the node belongs to. A uid is only good for that document.
    loader_id: str
    frame_id: str
    children: list["SnapshotNode"] = field(default_factory=list)
    uid: str = ""

    @property
    def name(self) -> str:
        value = self.attributes.get("name")
        return str(value) if value else ""


def _collect_interesting(collection: set[int], node: AXNode, inside_control: bool) -> None:
    if node.is_interesting(inside_control) or node.frame is not None:
        collection.add(id(node))
    if node.is_leaf():
        return
    inside_control = inside_control or node.is_control()
    for child in node.children:
        _collect_interesting(collection, child, inside_control)


def _serialize(node: AXNode, interesting: set[int] | None, loader_id: str, frame_id: str) -> list[SnapshotNode]:
    children: list[SnapshotNode] = []
    for child in node.children:
        # Line fragments of a text node: they repeat the text and stand for no element.
        if child.role == "InlineTextBox":
            continue
        children.extend(_serialize(child, interesting, loader_id, frame_id))
    if interesting is not None and id(node) not in interesting:
        return children
    attributes = node.attributes()
    if node.role == "option" and attributes.get("name"):
        # The node of an option does not carry its value; its text stands for it.
        attributes["value"] = str(attributes["name"])
    serialized = SnapshotNode(
        role=node.role,
        attributes=attributes,
        backend_node_id=node.backend_node_id,
        loader_id=loader_id,
        frame_id=frame_id,
        children=children,
    )
    if node.frame is not None:
        serialized.children.append(node.frame)
    return [serialized]


def serialize_tree(root: AXNode, *, verbose: bool, loader_id: str, frame_id: str) -> SnapshotNode | None:
    """Return the tree as snapshot nodes: every node when ``verbose``, else the interesting ones."""
    interesting: set[int] | None = None
    if not verbose:
        interesting = set()
        _collect_interesting(interesting, root, False)
    serialized = _serialize(root, interesting, loader_id, frame_id)
    return serialized[0] if serialized else None


@dataclass(slots=True)
class Snapshot:
    """One snapshot of a page: the tree and its nodes by uid."""

    snapshot_id: int
    root: SnapshotNode
    verbose: bool
    by_uid: dict[str, SnapshotNode]


def assign_uids(root: SnapshotNode, snapshot_id: int, known: dict[str, str]) -> dict[str, SnapshotNode]:
    """Give every node that stands for a DOM node its uid and return the nodes by uid.

    ``known`` maps a node (``<loader id>_<backend node id>``) to the uid it
    was given before; it is kept up to date, and nodes that are no longer in
    the tree are forgotten.
    """
    by_uid: dict[str, SnapshotNode] = {}
    seen: set[str] = set()
    counter = 0
    stack = [root]
    # Depth first in document order, so the numbers read top to bottom.
    while stack:
        node = stack.pop()
        if node.backend_node_id is not None:
            key = f"{node.loader_id}_{node.backend_node_id}"
            uid = known.get(key)
            if uid is None or uid in by_uid:
                uid = f"{snapshot_id}_{counter}"
                counter += 1
                known[key] = uid
            seen.add(key)
            node.uid = uid
            by_uid[uid] = node
        stack.extend(reversed(node.children))
    for key in list(known):
        if key not in seen:
            del known[key]
    return by_uid


def _strip_whitespace(text: str) -> str:
    return "".join(text.split())


def _redundant_text_children(node: SnapshotNode, verbose: bool) -> bool:
    """Whether the children only repeat the node's own name as text."""
    if verbose or not node.name or not node.children:
        return False
    text = ""
    for child in node.children:
        if child.role != "StaticText" or child.children:
            return False
        text += child.name
    return _strip_whitespace(text) == _strip_whitespace(node.name)


def _line(node: SnapshotNode) -> str:
    parts: list[str] = []
    if node.uid:
        parts.append(f"uid={node.uid}")
    if node.role and node.role != "StaticText":
        parts.append("ignored" if node.role == "none" else node.role)
    if node.name:
        parts.append(f'"{node.name}"')
    is_option = node.role == "option"
    for key in sorted(node.attributes):
        if key == "name":
            continue
        value = node.attributes[key]
        capability = _BOOLEAN_CAPABILITY.get(key)
        if capability and isinstance(value, bool) and not (is_option and capability == "selectable"):
            parts.append(capability)
        if is_option and key == "value" and value == node.name:
            continue
        if value is True:
            parts.append(key)
        elif isinstance(value, bool):
            continue
        elif isinstance(value, (str, int, float)):
            if key == "url" and isinstance(value, str) and value.startswith("data:"):
                # Other addresses are kept whole: the snapshot is their only full copy.
                value = value[:_DATA_URL_SHOWN_CHARACTERS] + "..." if len(value) > _DATA_URL_SHOWN_CHARACTERS else value
            parts.append(f'{key}="{value}"')
    return " ".join(parts)


def format_lines(root: SnapshotNode, *, verbose: bool) -> list[str]:
    """Return the snapshot as lines of text, one per node, indented by depth."""
    lines: list[str] = []
    stack: list[tuple[SnapshotNode, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        lines.append("  " * depth + _line(node))
        if not _redundant_text_children(node, verbose):
            stack.extend((child, depth + 1) for child in reversed(node.children))
    return lines


def find_by_uid(root: SnapshotNode, uid: str) -> SnapshotNode | None:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.uid == uid:
            return node
        stack.extend(node.children)
    return None
