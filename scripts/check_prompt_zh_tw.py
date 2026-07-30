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

"""Stop new i18n prompt dicts from landing without a 'zh-TW' key (issue #2500).

A dict under config/prompts/ that has an 'en' key plus 'zh' or 'zh-CN' is a
localized prompt table. Most of them predate Traditional Chinese support, and
`_loc` falls back to 'en' rather than 'zh' on a missing key, so a zh-TW user who
reaches such a dict gets an English prompt.

Backfilling the existing tables is a batched effort tracked in issue #2500. This
gate only stops the hole from growing.

How the ratchet works
=====================
It counts offending dicts at the merge-base and at HEAD. HEAD having more than
the base is what fails the check. That is the whole decision — no source lines,
no per-dict identity, no grouping.

Getting here took three wrong turns, each of which broke a case the simple count
handles for free:

  * **By diff line.** A pre-existing ``{'en': ..., 'ja': ...}`` table that a PR
    turns into a localized one by adding a single ``'zh'`` line never has its own
    definition line in the diff — the gate would miss the very case it exists
    for. And renaming a module with no content change marks every line of the
    new path as added, reporting the whole file's existing backlog.
  * **By whole key set.** Then adding an unrelated locale (a new ``'fr'``
    template) to a pre-existing offender reads as a brand-new table, failing a
    PR that did not grow the backlog.
  * **By Simplified-key scheme** ('zh' vs 'zh-CN' counted separately). Then
    migrating a table from ``'zh'`` to ``'zh-CN'`` shows up as one scheme losing
    a table and the other gaining one, and Counter subtraction only keeps the
    positive side — so it reports growth that did not happen. issue #2500's own
    endgame is exactly that rename across all ~339 tables, i.e. this gate would
    have blocked the migration it exists to serve.

A plain total is invariant under all three: renames, copy edits, added locales,
and scheme migrations all leave it alone, while a table newly subject to the rule
raises it.

The tradeoff: a PR removing one offending table while adding another nets to zero
and passes. That is a deliberate accept — the alternative is matching dicts
across revisions by identity, and every candidate for that identity (position,
key set, scheme) is what the three wrong turns above already tried.

Scope: one expression at a time, plus one cross-statement exemption
==================================================================
A table assembled across statements is not judged::

    T = {"en": "e"}
    T["zh"] = "s"          # T now needs zh-TW, and this gate will not say so

Judging that needs intra-procedural data flow — binding names to mapping state,
then following subscript assignments, ``update()`` calls and ``|=`` through
scopes, aliases, branches and loops. So mutation *payloads* are suppressed as
fragments (alone they say nothing about the assembled table) and the assembled
table goes unjudged. Same family as the "unknowable keys stay silent" rule.

The one cross-statement case that *is* handled is the false-positive direction::

    T = {"en": "e", "zh": "s"}
    T.update({"zh-TW": "t"})    # or T["zh-TW"] = ..., or T |= {...}

That table is compliant at runtime, so reporting it would be a false positive —
and false positives are what get a gate worked around rather than satisfied. The
exemption is deliberately keyed on the mutation *demonstrably supplying zh-TW*:
exempting on any mutation would let an unrelated ``T["other"] = x`` excuse a real
offender, trading one rare false positive for a broad blind spot.

Two accepted blind spots follow from that choice, both on the miss side:

  * The exemption is not ordered against *other* uses of the name, so a copy taken
    before the backfill is not judged::

        T = {"en": "e", "zh": "s"}
        U = dict(T)              # U really does lack zh-TW at runtime
        T["zh-TW"] = "t"         # …but this exempts T's literal, and dict(T)
                                 #    resolves through the name to the same keys

    Ordering the exemption against each use needs statement-level data flow, the
    same analysis the section above declines.
  * ``dict(zip(keys, values))`` is not resolved. Every other static constructor is
    (literal, ``dict()``, ``|``, comprehension, iterable-of-pairs, ``fromkeys``),
    but splitting a localized table into two parallel sequences makes the template
    bodies unreadable, so it is not a shape prompt modules use — there are zero
    occurrences under config/prompts. Chasing constructor forms with no realistic
    use grows ``resolve_keys`` without shrinking the backlog.


Usage:
    python scripts/check_prompt_zh_tw.py [--base origin/main]
    python scripts/check_prompt_zh_tw.py --full     # list the whole backlog
    python scripts/check_prompt_zh_tw.py --count    # backlog size only

Escape hatch: a ``noqa`` comment naming PROMPT_ZH_TW on the dict's opening or
closing line. It may sit in a comma-separated code list in any order, and a bare
``noqa`` suppresses everything — same behaviour as the sibling gates and ruff.
"""
from __future__ import annotations

import argparse
import ast
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path
from typing import Callable, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_SUBDIR = "config/prompts"
PROMPTS_DIR = REPO_ROOT / "config" / "prompts"

CODE = "PROMPT_ZH_TW"

SIMPLIFIED_KEYS = ("zh", "zh-CN")
TRADITIONAL_KEY = "zh-TW"
ANCHOR_KEY = "en"


def _has_noqa(line: str) -> bool:
    """True if `line` carries `# noqa` (bare) or `# noqa: ...,PROMPT_ZH_TW,...`.

    Same implementation as the sibling gates (check_docstring_no_cjk.py,
    check_prompt_hygiene.py, check_llm_budget.py) and therefore the same
    behaviour as ruff/flake8. An earlier version anchored the code immediately
    after ``noqa:``, which silently rejected a bare ``# noqa`` and any list where
    this code was not first — so an author following the convention the other four
    gates use would find their suppression ignored.

    Tolerates a trailing explanatory comment, but it must start with ``#``
    (``# noqa: CODE  # rationale``): the codes block stops only at the next ``#``
    or end-of-line.
    """
    m = re.search(r"#\s*noqa\b(?:\s*:\s*([A-Za-z0-9_,\s]+?))?(?=#|$)", line)
    if not m:
        return False
    raw = m.group(1)
    if raw is None or not raw.strip():
        return True
    return CODE in {c.strip() for c in raw.split(",") if c.strip()}


def resolve_keys(node: ast.AST) -> set[str] | None:
    """Statically resolve a mapping expression's key set, or None if unknowable.

    Merges are resolved *through*, not skipped: ``{"en": ...} | {"zh": ...}``
    resolves to ``{en, zh}``, so the assembled table is judged even though neither
    half is a table on its own. That is what stops a compliant
    ``{"en", "zh"} | {"zh-TW"}`` from being reported as two fragments, and equally
    stops a non-compliant ``{"en"} | {"zh"}`` from slipping through as neither.

    ``None`` means some part is not statically knowable — a spread of a name, a
    non-constant key, ``dict()`` over a variable. The gate stays silent on those
    rather than guessing, because the unknowable part is exactly where a
    ``'zh-TW'`` entry could be hiding, and a gate that cries wolf gets worked
    around rather than satisfied.

    Note the ``dict(...)`` call form cannot express ``zh-CN`` or ``zh-TW`` as
    keywords (neither is an identifier), but it can express ``zh``, so a table
    written that way is still subject to the rule.
    """
    if isinstance(node, ast.Dict):
        keys: set[str] = set()
        for key, value in zip(node.keys, node.values):
            if key is None:
                inner = resolve_keys(value)
                if inner is None:
                    return None
                keys |= inner
            elif isinstance(key, ast.Constant):
                # A *constant* non-string key (a None sentinel, an int) cannot be
                # 'zh-TW', so it hides nothing and is simply not a locale key —
                # skip it and keep reading. Only a non-constant key forces the
                # whole table to be abandoned, since that one could be anything.
                if isinstance(key.value, str):
                    keys.add(key.value)
            else:
                return None
        return keys
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        keys = set()
        for arg in node.args:
            inner = resolve_keys(arg)
            if inner is None:
                # `dict([("en", ...), ("zh", ...)])` — the iterable-of-pairs
                # constructor. Not a mapping, so resolve_keys says nothing about
                # it, but its keys are as statically known as a literal's.
                inner = _pair_sequence_keys(arg)
            if inner is None:
                return None
            keys |= inner
        for kw in node.keywords:
            if kw.arg is None:
                inner = resolve_keys(kw.value)
                if inner is None:
                    return None
                keys |= inner
            else:
                keys.add(kw.arg)
        return keys
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        left = resolve_keys(node.left)
        right = resolve_keys(node.right)
        if left is None or right is None:
            return None
        return left | right
    if isinstance(node, ast.DictComp):
        return _comprehension_keys(node)
    if isinstance(node, ast.Call) and _is_dict_fromkeys(node.func):
        # `dict.fromkeys(("en", "zh"), template)` — one template shared across a
        # literal locale list. func is an Attribute, so the `dict(...)` branch
        # above does not see it, and there is no child mapping node for the walker
        # to fall back on.
        return _literal_string_sequence(node.args[0]) if node.args else None
    return None


def _is_dict_fromkeys(func: ast.AST) -> bool:
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "fromkeys"
        and isinstance(func.value, ast.Name)
        and func.value.id == "dict"
    )


def _literal_string_sequence(node: ast.AST) -> set[str] | None:
    """String constants of a literal Tuple/List/Set, or None if not literal.

    Non-string *constants* are skipped rather than disqualifying the sequence, for
    the same reason as constant non-string dict keys: they cannot be 'zh-TW'.
    """
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return None
    keys: set[str] = set()
    for element in node.elts:
        if not isinstance(element, ast.Constant):
            return None
        if isinstance(element.value, str):
            keys.add(element.value)
    return keys


def _pair_sequence_keys(node: ast.AST) -> set[str] | None:
    """Keys of a literal iterable of ``(key, value)`` pairs, or None.

    Covers the standard ``dict([("en", ...), ("zh", ...)])`` constructor. Every
    element must be a two-item sequence whose first item is a string constant;
    anything else makes the key set unknowable.
    """
    if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return None
    keys: set[str] = set()
    for element in node.elts:
        if not isinstance(element, (ast.Tuple, ast.List)) or len(element.elts) != 2:
            return None
        first = element.elts[0]
        if not isinstance(first, ast.Constant):
            return None
        # Constant non-string keys are skipped, not disqualifying — same rule as
        # dict literals: such a key cannot be 'zh-TW'.
        if isinstance(first.value, str):
            keys.add(first.value)
    return keys


def _comprehension_keys(node: ast.DictComp) -> set[str] | None:
    """Keys of a dict comprehension over an inline literal, or None.

    Resolves the two shapes whose keys are fully determined by the source::

        {loc: build(loc) for loc in ("en", "zh", "ja")}
        {k: v for k, v in (("en", "hello"), ("zh", "ni hao"))}

    The first is a realistic way to write a localized table — one template per
    locale off a literal locale list — so leaving it unresolved would be a real
    blind spot, not a theoretical one.

    Deliberately does NOT follow a name to its definition. `{lang: build(lang)
    for lang in _L10N}` stays unresolved: chasing the symbol would mean judging
    derived structures whose keys are a language list rather than templates, and
    the three comprehensions that exist under config/prompts today are all of
    that kind.
    """
    if len(node.generators) != 1:
        return None
    gen = node.generators[0]
    if gen.ifs or gen.is_async:
        return None
    if not isinstance(node.key, ast.Name):
        return None
    if not isinstance(gen.iter, (ast.Tuple, ast.List, ast.Set)):
        return None

    if isinstance(gen.target, ast.Name):
        if gen.target.id != node.key.id:
            return None
        return _literal_string_sequence(gen.iter)

    if isinstance(gen.target, ast.Tuple):
        names = [e.id for e in gen.target.elts if isinstance(e, ast.Name)]
        if len(names) != len(gen.target.elts) or node.key.id not in names:
            return None
        index = names.index(node.key.id)
        keys = set()
        for element in gen.iter.elts:
            if not isinstance(element, (ast.Tuple, ast.List)):
                return None
            if index >= len(element.elts):
                return None
            item = element.elts[index]
            if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
                return None
            keys.add(item.value)
        return keys

    return None


def _operand_branches(node: ast.AST) -> list[ast.AST]:
    """An operand, or — when it is conditional — the branches it picks between.

    ``{**(A if flag else B), "zh-TW": t}`` merges whichever of A/B runs, so both
    are fragments of the same table. Naming only the ``IfExp`` left traversal free
    to reach A and B and judge each on its own — two false offenders for one
    compliant table.

    The ``IfExp`` itself is not returned: nothing judges one (it is not a mapping
    expression), so listing it would be a line no test can hold to account.
    """
    if isinstance(node, ast.IfExp):
        return [*_operand_branches(node.body), *_operand_branches(node.orelse)]
    return [node]


def _merge_operands(node: ast.AST) -> list[ast.AST]:
    """The sub-mappings a merged construction composes, or ``[]`` if not a merge.

    Conditional operands are expanded into their branches here rather than at each
    caller, so every consumer — fragment suppression, the merge exemption, and
    payload key resolution — agrees on what counts as a fragment.
    """
    return [
        part
        for operand in _direct_merge_operands(node)
        for part in _operand_branches(operand)
    ]


def _direct_merge_operands(node: ast.AST) -> list[ast.AST]:
    """The operands a merge names outright, before conditionals are expanded.

    ``{**a, **b}``, ``dict(BASE, zh=...)`` and ``a | b`` compose their keys out of
    other mappings, so each operand is a *fragment*: the ``'zh-TW'`` entry may
    live in any one of them and none can be judged alone.

    Only the operands themselves. A value keyed normally alongside a spread
    (``{**COMMON, "new": {...}}``) is not a fragment — it is an independent table
    that happens to sit in a merged container.
    """
    if isinstance(node, ast.Dict):
        return [v for k, v in zip(node.keys, node.values) if k is None]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        return list(node.args) + [kw.value for kw in node.keywords if kw.arg is None]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return [node.left, node.right]
    if isinstance(node, ast.AugAssign) and isinstance(node.op, ast.BitOr):
        return [node.value]  # `T |= {...}` — same merge, statement form
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "update"
    ):
        # `T.update({...})` merges into T, so the payload is a fragment for the
        # same reason a spread operand is: whether the assembled table has zh-TW
        # depends on T, which this expression does not show. Suppressing it avoids
        # reporting `T = {"zh-TW": ...}` / `T.update({"en": ..., "zh": ...})` —
        # compliant at runtime — as an offender.
        #
        # Both argument forms count: `update({...})` and `update(**{...})` are the
        # same merge, so the keyword-spread payload is a fragment too.
        return list(node.args) + [kw.value for kw in node.keywords if kw.arg is None]
    return []


def _directly_visible_keys(
    node: ast.AST, resolve_name: Callable[[str], set[str]] | None = None
) -> set[str]:
    """Keys an expression states itself, ignoring what it merges in by name.

    Answers "does this construction demonstrably supply zh-TW?" — the condition for
    treating its named inputs as fragments. ``{**T, "zh-TW": ...}`` does;
    ``dict(T)`` and ``{**T, "ja": ...}`` do not, and their T stays subject to the
    rule.

    Unlike ``resolve_keys`` this never gives up: an unknowable part contributes
    nothing instead of poisoning the whole result.
    """
    if isinstance(node, ast.Dict):
        keys = {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        for key, value in zip(node.keys, node.values):
            if key is None:
                keys |= _directly_visible_keys(value, resolve_name)
        return keys
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
    ):
        keys = {kw.arg for kw in node.keywords if kw.arg is not None}
        for kw in node.keywords:
            if kw.arg is None:
                keys |= _directly_visible_keys(kw.value, resolve_name)
        for arg in node.args:
            keys |= _directly_visible_keys(arg, resolve_name)
            pairs = _pair_sequence_keys(arg)
            if pairs:
                keys |= pairs
        return keys
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return _directly_visible_keys(node.left, resolve_name) | _directly_visible_keys(node.right, resolve_name)
    if isinstance(node, ast.Name) and resolve_name is not None:
        return resolve_name(node.id)
    if isinstance(node, ast.IfExp):
        # Either branch may be the one that carries zh-TW, and a conditional
        # supply still counts here — same call as `if enabled: T["zh-TW"] = t`.
        return _directly_visible_keys(node.body, resolve_name) | _directly_visible_keys(
            node.orelse, resolve_name
        )
    # Anything else resolve_keys understands: `_F | dict.fromkeys(("zh-TW",), t)`,
    # `_F | {loc: tpl for loc in ("zh-TW",)}`. Delegating rather than re-listing
    # the constructors keeps this from lagging behind resolve_keys again — each
    # time it did, the union looked like it supplied nothing and _F got reported.
    return resolve_keys(node) or set()


def _exempt_table_nodes(tree: ast.AST) -> set[int]:
    """ids of tables that must not be judged on their own.

    Covers the one cross-statement shape worth handling::

        T = {"en": "e", "zh": "s"}
        T.update({"zh-TW": "t"})        # or T["zh-TW"] = ..., or T |= {...}

    The table is compliant at runtime, so reporting it is a false positive — and
    false positives are what get a gate worked around rather than satisfied.

    Deliberately narrow in two ways:

    * Only a mutation that *demonstrably supplies zh-TW* exempts its target.
      Exempting on any mutation would let an unrelated ``T["other"] = x`` excuse a
      real offender, trading one rare false positive for a broad blind spot.
    * The exemption lands on the binding **most recently before** the mutation, not
      on every same-named assignment. Otherwise a name reassigned afterwards::

          T = {"en": "e", "zh": "s"}
          T["zh-TW"] = "t"
          T = {"en": "e2", "zh": "s2"}   # a real offender

      would have its later table exempted too.

    No scope analysis: line order is enough here because a mutation and the
    binding it mutates sit in the same scope in practice, and picking the nearest
    preceding binding is right in either nesting direction.
    """
    assignments: dict[str, list[tuple[int, int]]] = {}
    for node in ast.walk(tree):
        name: str | None = None
        if isinstance(node, ast.Assign):
            # Every simple target, so a chained `T = U = {...}` registers both —
            # they name the same object, and a mutation through either completes it.
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments.setdefault(target.id, []).append(
                        (node.lineno, node.value)
                    )
            continue
        if (
            # `T: dict[str, str] = {...}` — an annotated binding is still a
            # binding, and typed prompt constants are ordinary style. Missing them
            # left the table unexempted and reported despite being compliant.
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            name = node.target.id
        if name is None:
            continue
        assignments.setdefault(name, []).append((node.lineno, node.value))
    for bindings in assignments.values():
        bindings.sort(key=lambda item: item[0])

    exempt: set[int] = set()

    def _binding_before(
        name: str, lineno: int, exclude: ast.AST | None = None
    ) -> ast.AST | None:
        """The value bound to `name` most recently at or before `lineno`.

        ``exclude`` skips one candidate: for a self-rebinding merge like
        ``T = T | {...}`` the new assignment registers on the same line as the
        merge, so an inclusive line search would pick the merge's own result
        instead of the binding its right-hand side actually reads.
        """
        for ln, value in reversed(assignments.get(name, [])):
            if ln > lineno:
                continue
            if exclude is not None and value is exclude:
                continue
            return value
        return None

    def _exempt_binding_before(
        name: str, lineno: int, exclude: ast.AST | None = None
    ) -> None:
        value = _binding_before(name, lineno, exclude)
        if value is not None:
            exempt.add(id(value))

    def _mapping_keys(
        node: ast.AST | None, at: int, _seen: frozenset[str] = frozenset()
    ) -> set[str]:
        """Keys a mapping expression supplies, following names to their bindings.

        One resolver for every place a name can stand in for a mapping — a mutation
        payload, a merge operand, an alias of either. Each of those grew its own
        partial version first (``resolve_keys`` only; no iterable-of-pairs; no
        second hop through ``TW2 = dict(TW)``), and every gap read as "no zh-TW
        here", i.e. a table reported despite being compliant at runtime.

        ``_seen`` guards the name hops, so mutual or self-referential bindings
        terminate instead of recursing forever.
        """
        if node is None:
            return set()
        if isinstance(node, ast.Name):
            if node.id in _seen:
                return set()
            return _name_keys(node.id, at, _seen=_seen)
        if isinstance(node, ast.IfExp):
            # `T |= TW if flag else {"zh-TW": t}` — either branch may carry it, and
            # a conditional supply counts, same as in `_directly_visible_keys`.
            return _mapping_keys(node.body, at, _seen) | _mapping_keys(
                node.orelse, at, _seen
            )
        keys = resolve_keys(node)
        if keys is None:
            keys = _pair_sequence_keys(node)
        if keys is None:
            # A construction resolve_keys gave up on because its own parts are
            # names: `dict(TW)`, `{**TW}`, `BASE | TW`.
            operands = _merge_operands(node)
            if operands:
                keys = _directly_visible_keys(node)
                for operand in operands:
                    keys |= _mapping_keys(operand, at, _seen)
        return keys or set()

    def _name_keys(
        name: str,
        at: int,
        exclude: ast.AST | None = None,
        _seen: frozenset[str] = frozenset(),
    ) -> set[str]:
        """Keys of the mapping `name` is bound to just before `at`."""
        return _mapping_keys(
            _binding_before(name, at, exclude), at, _seen | {name}
        )

    for node in ast.walk(tree):
        # `T["zh-TW"] = t`, and its annotated form `T["zh-TW"]: str = t`.
        # AnnAssign carries a single `target` rather than a `targets` list, so it
        # needs its own unpacking even though the shape being matched is identical.
        subscripts: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            subscripts = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            subscripts = [node.target]
        matched = False
        for slot in subscripts:
            if (
                isinstance(slot, ast.Subscript)
                and isinstance(slot.value, ast.Name)
                and isinstance(slot.slice, ast.Constant)
                and slot.slice.value == TRADITIONAL_KEY
            ):
                _exempt_binding_before(slot.value.id, node.lineno)
                matched = True
        if matched:
            continue
        target: str | None = None
        payloads: list[ast.AST] = []
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("update", "setdefault")
            and isinstance(node.func.value, ast.Name)
        ):
            target = node.func.value.id
            if node.func.attr == "setdefault":
                # `T.setdefault("zh-TW", tpl)` names the key directly.
                first = node.args[0] if node.args else None
                if (
                    isinstance(first, ast.Constant)
                    and first.value == TRADITIONAL_KEY
                ):
                    _exempt_binding_before(target, node.lineno)
                continue
            # Every payload, not just the first: `update({...}, **{...})` is legal
            # and so is more than one `**`. Named keywords are deliberately not
            # consulted — 'zh-TW' is not an identifier, so it can never arrive as
            # `update(zh-TW=...)`.
            payloads = list(node.args) + [
                kw.value for kw in node.keywords if kw.arg is None
            ]
        elif (
            isinstance(node, ast.AugAssign)
            and isinstance(node.op, ast.BitOr)
            and isinstance(node.target, ast.Name)
        ):
            target, payloads = node.target.id, [node.value]
        if target is None or not payloads:
            continue

        if any(
            TRADITIONAL_KEY in _mapping_keys(payload, node.lineno)
            for payload in payloads
        ):
            _exempt_binding_before(target, node.lineno)

    # A name merged into a construction that *demonstrably supplies zh-TW* is a
    # fragment of a compliant table: `_F = {"en": .., "zh": ..}` /
    # `T = {**_F, "zh-TW": ..}` — reporting _F there is a false positive.
    #
    # The zh-TW condition is what keeps this from becoming a blind spot. Exempting
    # every named operand meant `U = dict(T)` excused T, so an offender plus a copy
    # of it counted as zero: T exempt as an "operand", U unresolvable because
    # resolve_keys does not follow names.
    for node in ast.walk(tree):
        operands = _merge_operands(node)
        if not operands:
            continue

        # `_TW = {"zh-TW": t}` / `T = {**_F, **_TW}` supplies zh-TW as plainly as an
        # inline literal. Following the name only ever *adds* visible keys, so
        # `U = dict(T)` still sees {en, zh} and leaves T subject to the rule.
        def _named_keys(name: str, _at: int = node.lineno, _self: ast.AST = node) -> set[str]:
            return _name_keys(name, _at, exclude=_self)

        if TRADITIONAL_KEY not in _directly_visible_keys(node, _named_keys):
            continue
        for operand in operands:
            if isinstance(operand, ast.Name):
                _exempt_binding_before(operand.id, node.lineno, exclude=node)

    return exempt


def _table_nodes(tree: ast.AST) -> Iterator[tuple[ast.AST, set[str]]]:
    """Yield ``(node, keys)`` for every mapping expression with knowable keys.

    Three failure modes this threads between, each of which was shipped and then
    reported:

      * Plain ``ast.walk`` judges each operand of a merge on its own, so
        ``{**{"en": ..., "zh": ...}, **{"zh-TW": ...}}`` reports the first half as
        missing zh-TW even though the assembled mapping has it.
      * Pruning a merge's whole subtree instead loses the independent tables
        inside it — ``{**COMMON, "new": {"en": ..., "zh": ...}}`` would never
        check ``"new"``.
      * Suppressing operands without resolving the merge lets the *result* escape:
        ``{"en": ...} | {"zh": ...}`` has both halves suppressed and the enclosing
        BinOp is not a dict node, so nothing was judged at all.

    So: a resolvable expression is judged as a whole, and its merge operands are
    then suppressed as fragments of something already accounted for. An
    unresolvable one is not judged, but traversal continues through it so the
    independent tables it holds are still found.
    """
    suppressed: set[int] = _exempt_table_nodes(tree)
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        if id(node) not in suppressed:
            keys = resolve_keys(node)
            if keys is not None:
                yield node, keys
        # Suppress operands whether or not the merge resolved. An operand is a
        # fragment by definition, so judging it alone is wrong either way: if the
        # merge resolved, the operand's keys are already counted in the result; if
        # it did not (`BASE | {"en": ..., "zh": ...}`), the unknown side is exactly
        # where zh-TW could be. Unconditional also handles nesting — the operands
        # of a suppressed operand are deeper fragments still.
        for operand in _merge_operands(node):
            suppressed.add(id(operand))
        stack.extend(ast.iter_child_nodes(node))


def is_offender(keys: set[str]) -> bool:
    """Whether a resolved key set belongs to a table that needs a zh-TW entry."""
    if ANCHOR_KEY not in keys:
        return False
    if not any(k in keys for k in SIMPLIFIED_KEYS):
        return False
    return TRADITIONAL_KEY not in keys


def _offending_nodes(
    tree: ast.Module, source_lines: list[str]
) -> Iterator[tuple[ast.AST, int, int]]:
    """Yield ``(node, lineno, end_lineno)`` for each offending table, in order.

    Nodes rather than bare line numbers, because two tables can share an opening
    line (``T = {"en": {``) and each needs its own span — see ``locate_touched``.
    """
    found: list[tuple[ast.AST, int, int]] = []
    for node, keys in _table_nodes(tree):
        if not is_offender(keys):
            continue
        lineno = node.lineno
        end = getattr(node, "end_lineno", lineno) or lineno
        # Opening *or* closing line: a suppression comment on the closing brace
        # is a natural place to put it, and for a merge expression the node's own
        # lineno is the left operand's line rather than the line the author would
        # think of as the table's start. (The directive is not spelled out here —
        # ruff would read it as a real one and warn about the bare code name.)
        exempt = any(
            _has_noqa(source_lines[ln - 1])
            for ln in {lineno, end}
            if 1 <= ln <= len(source_lines)
        )
        if exempt:
            continue
        found.append((node, lineno, end))
    # _table_nodes walks depth-first off a stack, so restore source order.
    found.sort(key=lambda item: (item[1], item[2]))
    yield from found


def find_violations(tree: ast.Module, source_lines: list[str]) -> list[int]:
    """Return the line number of every localized dict with no zh-TW key."""
    return [lineno for _node, lineno, _end in _offending_nodes(tree, source_lines)]


def _comment_lines(source: str) -> list[str]:
    """Per-line comment text, indexed like the source's own lines.

    Suppression must be read from comments only. A ``# noqa: PROMPT_ZH_TW``
    appearing *inside* a string literal is template text, not a directive — and a
    multiline template whose first line ends with that text would otherwise exempt
    its own table.

    Line splitting is `re.split` rather than ``str.splitlines()``: the latter also
    breaks on \\x0b \\x0c \\x1c \\x1d \\x1e \\x85 U+2028 U+2029, none of which
    CPython counts as a newline, so one inside a literal shifts every later line
    and a noqa starts matching a neighbour. ``split("\\n")`` is wrong the other
    way, collapsing a lone-CR file into one line.

    On a tokenize failure every line comes back empty — no suppression rather than
    wrong suppression, and ``ast.parse`` will have reported the syntax error.
    """
    blank = [""] * len(re.split(r"\r\n|\r|\n", source))
    # Normalize line endings first: io.StringIO does not treat a lone \r as a
    # newline, so a CR-only module collapses to one line for tokenize while the
    # split above counts them properly. The comment then lands on the wrong index
    # — which suppresses a table that has no noqa and reports the one that does.
    normalized = re.sub(r"\r\n|\r", "\n", source)
    try:
        for token in tokenize.generate_tokens(io.StringIO(normalized).readline):
            if token.type == tokenize.COMMENT:
                row = token.start[0]
                if 1 <= row <= len(blank):
                    blank[row - 1] += token.string
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return [""] * len(blank)
    return blank


def _parse_source(source: str, origin: str) -> tuple[ast.Module | None, list[str]]:
    """Parse source, returning the tree and each line's comment text.

    The second element feeds noqa lookup only, so it carries comments rather than
    raw lines — see ``_comment_lines``.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        sys.stderr.write(f"{origin}: syntax error ({exc})\n")
        return None, []
    return tree, _comment_lines(source)


def count_offenders(sources: dict[str, str]) -> int:
    """How many localized prompt tables in a {path: source} mapping lack zh-TW."""
    total = 0
    for path, source in sorted(sources.items()):
        tree, lines = _parse_source(source, path)
        if tree is None:
            continue
        total += len(find_violations(tree, lines))
    return total


def locate_touched(
    sources: dict[str, str],
    touched: dict[str, set[int]] | None = None,
) -> tuple[list[str], int]:
    """Split offending dicts into (touched by this diff, count of the rest).

    Purely for the error message: the pass/fail decision is the count comparison.
    A total says *that* the backlog grew but not *where*, and 339 pre-existing
    offenders is far too many to print, so the diff's own lines are what make the
    failure actionable.

    Each node carries its own span. Keying nodes by start line instead would let a
    nested table opening on its parent's line (``T = {"en": {``) evict the parent,
    and the parent would then be matched against the child's shorter span — so
    touching a line inside the parent but past the child classified both as
    pre-existing and degraded the message to "run --full".

    Labels are de-duplicated: two offenders sharing an opening line render to the
    same ``path:lineno``, and printing it twice reads as a bug rather than as two
    tables.
    """
    likely: list[str] = []
    other = 0
    for path, source in sorted(sources.items()):
        tree, lines = _parse_source(source, path)
        if tree is None:
            continue
        added = (touched or {}).get(path, set())
        seen: set[str] = set()
        for _node, lineno, end in _offending_nodes(tree, lines):
            if added and any(ln in added for ln in range(lineno, end + 1)):
                label = f"{path}:{lineno}"
                if label not in seen:
                    seen.add(label)
                    likely.append(label)
            else:
                other += 1
    return likely, other


# ---------------------------------------------------------------------------
# git plumbing (mirrors scripts/check_docstring_no_cjk.py)
# ---------------------------------------------------------------------------


def _git(*args: str) -> str:
    """Run git and return stdout as text.

    ``errors="replace"`` because this decodes git's own reporting (diff headers,
    path lists); a malformed byte there should not abort the gate. Source blobs go
    through ``_git_bytes`` instead, so they can honour a PEP 263 declaration.
    """
    return _git_bytes(*args).decode("utf-8", errors="replace")


def _git_bytes(*args: str) -> bytes:
    """Run git and return raw stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
        sys.exit(2)
    return result.stdout


def _decode_source(raw: bytes, origin: str) -> str | None:
    """Decode Python source, honouring a PEP 263 coding declaration.

    A module carrying ``# coding: latin-1`` is valid Python that a plain UTF-8
    read would reject; skipping it would mean silently not checking a real prompt
    module. Only genuinely undecodable bytes are skipped, with a diagnostic.
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    except SyntaxError:
        encoding = "utf-8"
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, LookupError) as exc:
        sys.stderr.write(f"{origin}: cannot decode as {encoding} ({exc})\n")
        return None


def _merge_base(base: str) -> str:
    return _git("merge-base", base, "HEAD").strip()


SYMLINK_MODE = "120000"


def _prompt_files_at(rev: str) -> list[str]:
    """Prompt modules present at `rev`: recursive, subpackages included, no symlinks.

    ``-z`` keeps paths verbatim; without it git wraps non-ASCII paths in quotes and
    octal-escapes the bytes, which would not resolve as a path. Modes are read
    rather than using ``--name-only`` so symlinks can be excluded — see
    ``_sources_on_disk`` for why both sides must agree on that.
    """
    out = _git("ls-tree", "-r", "-z", rev, "--", PROMPTS_SUBDIR)
    paths: list[str] = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _tab, path = entry.partition("\t")
        if not path.endswith(".py"):
            continue
        if meta.split(" ", 1)[0] == SYMLINK_MODE:
            sys.stderr.write(f"{rev}:{path}: symlink, not scanned\n")
            continue
        paths.append(path.replace("\\", "/"))
    return paths


def _sources_at(rev: str) -> dict[str, str]:
    sources: dict[str, str] = {}
    for path in _prompt_files_at(rev):
        text = _decode_source(_git_bytes("show", f"{rev}:{path}"), f"{rev}:{path}")
        if text is not None:
            sources[path] = text
    return sources


_HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _touched_lines(rev: str) -> dict[str, set[int]]:
    """Lines the diff added per prompt file — a hint for error messages only.

    ``-M`` keeps a pure rename from reporting every line of the new path as
    added, which would make the hint point at the whole file.
    """
    # core.quotePath=false: git otherwise C-quotes non-ASCII paths in the `+++`
    # header (`+++ "b/config/prompts/\344\270\255.py"`), which would not match the
    # real path and would drop that file's hints. Same class of problem as the
    # `-z` on ls-tree, different output channel.
    diff = _git(
        "-c", "core.quotePath=false",
        "diff", "-M", "--unified=0", f"{rev}...HEAD", "--", PROMPTS_SUBDIR,
    )
    touched: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current = line[len("+++ b/"):].strip().replace("\\", "/")
            continue
        match = _HUNK_HEADER_RE.match(line)
        if not match or current is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        if count == 0:
            # Deletion-only hunk (`@@ -4 +3,0 @@`): nothing was added, so a plain
            # range() is empty and a table that just *lost* its 'zh-TW' entry —
            # a real way for the total to grow — would have no location at all.
            # Record the lines flanking the deletion point so the enclosing table
            # is still recognisable.
            touched.setdefault(current, set()).update({max(1, start), start + 1})
        else:
            touched.setdefault(current, set()).update(range(start, start + count))
    return touched


def _git_visible_prompt_files() -> set[str] | None:
    """Prompt paths git knows about: tracked plus untracked-not-ignored.

    ``None`` when git cannot answer (no repo, git missing), in which case the disk
    scan is used unfiltered — ``--count`` and ``--full`` are useful outside a
    checkout, and the ratchet itself already needs git for its base side.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
             "--", PROMPTS_SUBDIR],
            cwd=REPO_ROOT, capture_output=True, check=False,
        )
    except OSError:
        # git not on PATH raises FileNotFoundError rather than returning nonzero,
        # and a missing git is precisely one of the cases this fallback is for.
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.decode("utf-8", errors="replace")
    return {
        entry.replace("\\", "/")
        for entry in out.split("\0")
        if entry.endswith(".py")
    }


def _sources_on_disk() -> dict[str, str]:
    """Read every prompt module off disk, decoding per PEP 263.

    Reads bytes and hands them to ``_decode_source``, the same path the base side
    uses, so a module with a coding declaration is checked rather than skipped and
    an unreadable one is reported rather than fatal.
    """
    known = _git_visible_prompt_files()
    sources: dict[str, str] = {}
    for path in sorted(PROMPTS_DIR.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if known is not None and rel not in known:
            # Both sides must draw from the same set of files. The base side comes
            # from `git ls-tree`, so a file git does not know about — a gitignored
            # scratch module, a build artifact — would exist only on this side and
            # count as pure growth, failing the gate over something no PR
            # introduced. Untracked-but-not-ignored files stay in, so a
            # work-in-progress module is still checked locally.
            continue
        if path.is_symlink():
            # Must match _prompt_files_at, which skips mode 120000. Reading here
            # would follow the link and scan its target, while `git show` on the
            # base side yields the link's target *path* as blob content — the two
            # sides would then disagree about the same path forever, and every
            # later PR would fail on a difference no PR introduced.
            sys.stderr.write(f"{rel}: symlink, not scanned\n")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            sys.stderr.write(f"{rel}: cannot read ({exc})\n")
            continue
        text = _decode_source(raw, rel)
        if text is not None:
            sources[rel] = text
    return sources


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Require 'zh-TW' on newly added localized prompt dicts "
            "(offender-count ratchet against --base; --full scans everything)."
        )
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("PROMPT_ZH_TW_BASE", "origin/main"),
        help="Base ref for the ratchet (default: origin/main).",
    )
    parser.add_argument(
        "--full", action="store_true",
        help="List every offending dict, not just newly added ones.",
    )
    parser.add_argument(
        "--count", action="store_true",
        help="Print the backlog size and exit 0.",
    )
    args = parser.parse_args(argv)

    # Force UTF-8 on our own streams. When stdout is a pipe, Python encodes with
    # the locale encoding — cp1252 on the Windows CI runner — so printing a
    # non-ASCII path, or a SyntaxError whose text carries CJK source, would raise
    # UnicodeEncodeError from inside the gate. Callers decode as UTF-8 to match.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")

    disk = _sources_on_disk()

    if args.count:
        print(f"localized prompt dicts missing '{TRADITIONAL_KEY}': "
              f"{count_offenders(disk)}")
        return 0

    if args.full:
        found = 0
        for path, source in sorted(disk.items()):
            tree, lines = _parse_source(source, path)
            if tree is None:
                continue
            for lineno in find_violations(tree, lines):
                print(f"{path}:{lineno}: [{CODE}] missing '{TRADITIONAL_KEY}'")
                found += 1
        if found:
            print(f"\n{found} localized prompt dict(s) missing '{TRADITIONAL_KEY}'.")
            print("This is the issue #2500 backlog; --full is informational.")
        return 1 if found else 0

    merge_base = _merge_base(args.base)
    grew = count_offenders(disk) - count_offenders(_sources_at(merge_base))
    if grew <= 0:
        return 0

    likely, other = locate_touched(disk, _touched_lines(merge_base))
    print(f"[{CODE}] {grew} more localized prompt dict(s) lack "
          f"'{TRADITIONAL_KEY}' than at the merge-base.")
    if likely:
        print(f"        in lines this change touched: {', '.join(likely)}")
    else:
        print("        no touched table is missing it — the new table may have "
              "moved in from elsewhere; run --full to see every offender")
    print(f"        ({other} pre-existing offender(s) are exempt)")
    print(
        "\nA prompt dict with 'en' + 'zh'/'zh-CN' needs 'zh-TW' too: _loc falls "
        "back to 'en', not 'zh', so Traditional Chinese users would get an "
        "English prompt. Add the template, or put '# noqa: PROMPT_ZH_TW' on the "
        "dict's opening or closing line if it genuinely does not need one.\n"
        "The ratchet compares totals rather than source lines, so the locations "
        "above are narrowed to the tables this change touched.\n"
        f"(Set $PROMPT_ZH_TW_BASE or pass --base to override the base ref.)"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
