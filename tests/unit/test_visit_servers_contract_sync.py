# -*- coding: utf-8 -*-
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

"""Pin the Servers contract document to section 4.7 of the visit design record.

``docs/design/visit-servers-contract.md`` is the authoritative Servers
contract and is generated from ``docs/design/visit-infrastructure.md``
section 4.7. Every endpoint carries a machine-readable field-set line
(a bullet whose label is the field-set marker, then ``key `a, b?, c.d```
segments joined by a middle dot). These tests parse both documents and
require, per endpoint:

* the endpoint body (direction / surface / fields / field set / limits) is
  identical in both documents;
* the parsed field sets are equal, and every listed name really occurs in the
  prose field line of that endpoint (the summary cannot drift from the prose);
* ``POST /api/visit/reports`` is exactly
  ``{visit_id, reason, note?, include_transcript, anomalies, app_version}``
  with no ``transcript`` field;
* the old fragment ``transcript, reason}`` appears in neither document.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DESIGN = REPO_ROOT / "docs" / "design" / "visit-infrastructure.md"
CONTRACT = REPO_ROOT / "docs" / "design" / "visit-servers-contract.md"

FIELDSET_LABEL = "- 字段集:"  # Field set
FIELD_LABEL = "- 字段:"  # Fields
DESIGN_S47 = "### 4.7 Servers"
DESIGN_S48 = "### 4.8 "
CONTRACT_ENDPOINTS = "## 端点"  # Endpoints
CONTRACT_SERVERS_LIST = "## Servers 实现清单"  # Servers implementation checklist
DESIGN_SERVERS_LIST = "### N.E.K.O. Servers"

ENDPOINTS = (
    "POST /api/visit/credentials",
    "身份票 identity_ticket（claims 全表）",  # Identity ticket identity_ticket (complete claims table)
    "GET /api/visit/pubkeys",
    "GET /api/visit/invites/{invite_code}/preview",
    "POST /api/visit/reports",
    "POST /api/visit/transcripts",
    "GET /api/visit/history",
    "GET /api/visit/details/{visit_id}",
    "POST /admin/visit/bans",
)
CHECKLIST = "Servers 侧需要实现的清单（供排期）"  # Servers-side implementation checklist (for scheduling)

REPORTS_REQ = {"visit_id", "reason", "note?", "include_transcript", "anomalies", "app_version"}
OLD_REPORTS_FRAGMENT = "transcript, reason}"

_HEADING_RE = re.compile(r"^(#{1,6}) (.+?)\s*$")
_SEGMENT_RE = re.compile(r"^(\S+)\s+`([^`]*)`$")
_REPORTS_INLINE_RE = re.compile(r"/api/visit/reports\s*\{([^}]*)\}")


def _read(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _region(lines: list[str], start_prefix: str, stop) -> list[str]:
    starts = [i for i, ln in enumerate(lines) if ln.startswith(start_prefix)]
    assert len(starts) == 1, f"expected exactly one heading starting with {start_prefix!r}, got {len(starts)}"
    begin = starts[0] + 1
    end = next((i for i in range(begin, len(lines)) if stop(lines[i])), len(lines))
    return lines[begin:end]


def _design_s47() -> list[str]:
    return _region(_read(DESIGN), DESIGN_S47, lambda ln: ln.startswith(DESIGN_S48))


def _contract_endpoints() -> list[str]:
    return _region(_read(CONTRACT), CONTRACT_ENDPOINTS, lambda ln: ln.startswith("## "))


def _sections(region: list[str]) -> dict[str, list[str]]:
    """Map heading title -> body lines (until the next heading), trailing blanks dropped."""
    out: dict[str, list[str]] = {}
    current: str | None = None
    for line in region:
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) >= 3:
            current = match.group(2)
            assert current not in out, f"duplicate endpoint heading {current!r}"
            out[current] = []
            continue
        if current is not None:
            out[current].append(line.rstrip())
    for body in out.values():
        while body and not body[-1]:
            body.pop()
    return out


def _single_line(body: list[str], label: str, where: str) -> str:
    hits = [ln for ln in body if ln.startswith(label)]
    assert len(hits) == 1, f"{where}: expected one {label!r} line, got {len(hits)}"
    return hits[0]


def parse_fieldset(line: str) -> dict[str, frozenset[str]]:
    """Parse one field-set line into ``{segment key: frozenset(names)}``."""
    assert line.startswith(FIELDSET_LABEL), line
    out: dict[str, frozenset[str]] = {}
    for raw in line[len(FIELDSET_LABEL):].split(" · "):
        segment = raw.strip()
        match = _SEGMENT_RE.match(segment)
        assert match, f"unparseable field-set segment {segment!r}"
        key = match.group(1)
        names = [n.strip() for n in match.group(2).split(",") if n.strip()]
        assert len(names) == len(set(names)), f"duplicate names in segment {key!r}: {names}"
        assert key not in out, f"duplicate segment key {key!r}"
        out[key] = frozenset(names)
    return out


def _fieldsets(sections: dict[str, list[str]], where: str) -> dict[str, dict[str, frozenset[str]]]:
    return {
        ep: parse_fieldset(_single_line(sections[ep], FIELDSET_LABEL, f"{where} {ep}"))
        for ep in ENDPOINTS
    }


def _word_in(name: str, text: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text) is not None


@pytest.fixture(scope="module")
def design_sections() -> dict[str, list[str]]:
    return _sections(_design_s47())


@pytest.fixture(scope="module")
def contract_sections() -> dict[str, list[str]]:
    return _sections(_contract_endpoints())


def test_both_documents_list_the_same_endpoints(design_sections, contract_sections):
    expected = set(ENDPOINTS) | {CHECKLIST}
    assert set(design_sections) == expected
    assert set(contract_sections) == expected


@pytest.mark.parametrize("endpoint", (*ENDPOINTS, CHECKLIST))
def test_endpoint_body_is_identical(endpoint, design_sections, contract_sections):
    assert contract_sections[endpoint] == design_sections[endpoint], (
        f"{endpoint}: contract body differs from design section 4.7; edit both together"
    )


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_endpoint_field_sets_are_equal(endpoint, design_sections, contract_sections):
    design_fs = _fieldsets(design_sections, "design")[endpoint]
    contract_fs = _fieldsets(contract_sections, "contract")[endpoint]
    assert contract_fs == design_fs


@pytest.mark.parametrize("which", ("design", "contract"))
@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_field_set_names_occur_in_prose(endpoint, which, design_sections, contract_sections):
    sections = design_sections if which == "design" else contract_sections
    body = sections[endpoint]
    prose = _single_line(body, FIELD_LABEL, f"{which} {endpoint}")
    if endpoint.startswith("身份票"):
        # Claim types and meanings are in the table below; tables also count as body text
        prose = "\n".join([prose, *[ln for ln in body if ln.startswith("|")]])
    for key, names in parse_fieldset(_single_line(body, FIELDSET_LABEL, which)).items():
        for name in names:
            if key == "错误":  # Errors
                for token in name.split():
                    assert _word_in(token, prose), f"{which} {endpoint}: error {name!r} not in prose"
                continue
            leaf = name.rstrip("?").rsplit(".", 1)[-1]
            assert _word_in(leaf, prose), f"{which} {endpoint}: {key} field {name!r} not in prose"


@pytest.mark.parametrize("which", ("design", "contract"))
def test_reports_request_is_exact(which, design_sections, contract_sections):
    sections = design_sections if which == "design" else contract_sections
    fs = _fieldsets(sections, which)["POST /api/visit/reports"]
    assert set(fs["req"]) == REPORTS_REQ
    for key, names in fs.items():
        assert not any(n.rstrip("?") == "transcript" for n in names), f"{which}: reports {key} has transcript"
    prose = _single_line(sections["POST /api/visit/reports"], FIELD_LABEL, which)
    assert not _word_in("transcript", prose), f"{which}: reports field line carries a bare transcript field"


@pytest.mark.parametrize("which", ("design", "contract"))
def test_claims_set_matches_claims_table(which, design_sections, contract_sections):
    sections = design_sections if which == "design" else contract_sections
    endpoint = ENDPOINTS[1]
    claims = {n.rstrip("?") for n in _fieldsets(sections, which)[endpoint]["claims"]}
    rows = [ln for ln in sections[endpoint] if ln.startswith("| `")]
    table = {re.match(r"^\| `([^`]+)`", ln).group(1) for ln in rows}
    assert claims == table


@pytest.mark.parametrize("path", (DESIGN, CONTRACT), ids=("design", "contract"))
def test_no_old_reports_fragment(path):
    text = path.read_text(encoding="utf-8")
    # Design §5 PR-16 quotes the old fragment once as a prohibited literal when describing this test; exempt only this notation
    text = text.replace(f"旧格式片段 `{OLD_REPORTS_FRAGMENT}`", "")  # Old-format fragment `...`
    assert OLD_REPORTS_FRAGMENT not in text


@pytest.mark.parametrize("path", (DESIGN, CONTRACT), ids=("design", "contract"))
def test_every_inline_reports_body_is_the_canonical_one(path):
    text = path.read_text(encoding="utf-8")
    bodies = _REPORTS_INLINE_RE.findall(text)
    assert bodies, f"{path.name}: no inline reports body found"
    for body in bodies:
        names = {n.strip() for n in body.split(",")}
        assert names == REPORTS_REQ, f"{path.name}: stale reports body {{{body}}}"


def test_servers_checklist_matches_design_section_5():
    def numbered(lines: list[str]) -> list[str]:
        return [ln.rstrip() for ln in lines if re.match(r"^\d+\. ", ln)]

    design = numbered(_region(_read(DESIGN), DESIGN_SERVERS_LIST, lambda ln: ln.startswith("### ")))
    contract = numbered(_region(_read(CONTRACT), CONTRACT_SERVERS_LIST, lambda ln: ln.startswith("## ")))
    assert design, "design section 5 Servers list not found"
    assert contract == design
