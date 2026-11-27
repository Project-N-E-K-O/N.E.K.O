"""Preserve portable source coverage while bounding dense substring searches."""

from collections import OrderedDict
import itertools
import random

import pytest

from tests.knowledge_archive_fixtures import make_archive
from tests.unit.test_knowledge_package_validation_contracts import _replace_entries
from utils import knowledge_package as package


pytestmark = pytest.mark.unit_fast


class _CountingSource(str):
    """Fail on repeated native searches, independently of machine wall time."""

    def __new__(cls, value, maximum):
        instance = super().__new__(cls, value)
        instance.calls = 0
        instance.maximum = maximum
        return instance

    def find(self, *args):
        self.calls += 1
        assert self.calls <= self.maximum, "dense matches repeatedly rescan long substrings"
        return super().find(*args)


def _reference_coverage(content, bodies, remaining):
    """Use exhaustive starts and reachable cursor sets as an independent oracle."""
    cache = OrderedDict()
    cursors = {0}
    for body in bodies:
        if not body:
            return False
        if body not in cache:
            starts = []
            for start in range(len(content) + 1):
                if content.startswith(body, start):
                    remaining[0] -= 1
                    if remaining[0] < 0:
                        raise package.PackageError("package_capacity_exceeded")
                    starts.append(start)
            cache[body] = starts
            if len(cache) > 128:
                cache.popitem(last=False)
        cache.move_to_end(body)
        cursors = {max(cursor, start + len(body)) for cursor in cursors for start in cache[body]
                   if max(0, cursor - 120) <= start <= cursor}
        if not cursors:
            return False
    return len(content) in cursors


def _outcome(function, content, bodies, budget):
    """Compare exact remaining work even when validation refuses a package."""
    remaining = [budget]
    try:
        result = function(content, bodies, remaining)
    except package.PackageError as error:
        result = error.reason
    return result, remaining[0]


def test_legal_repetitive_plan_does_not_repeat_millions_of_long_searches():
    bodies = ["a" * width for width in range(1900, 1942)]
    content = _CountingSource("a" * 80000, maximum=64 * len(bodies))
    remaining = [4_000_000]

    assert package._source_is_covered(content, bodies, remaining_matches=remaining)
    # Keep the full occurrence budget, even though enumeration is now cheap.
    assert remaining == [720619]


def test_occurrence_order_matches_exhaustive_overlapping_unicode_starts():
    randomizer = random.Random(3765)
    pairs = [("a" * 300, "a" * 100), ("猫🙂" * 120, "猫🙂" * 40),
             ("é\u0301" * 80, "é\u0301" * 20), ("abababababa", "ababa")]
    for length in range(7):
        for chars in itertools.product("ab", repeat=length):
            for width in range(1, 5):
                pairs.extend(("".join(chars), "".join(body)) for body in itertools.product("ab", repeat=width))
    for _ in range(300):
        content = "".join(randomizer.choice("aab猫🙂é\u0301\0") for _ in range(randomizer.randrange(151)))
        start = randomizer.randrange(len(content) + 1)
        body = content[start:start + randomizer.randrange(1, 100)] or "missing"
        pairs.append((content, body))

    for content, body in pairs:
        expected = [start for start in range(len(content) + 1) if content.startswith(body, start)]
        assert list(package._source_match_starts(content, body)) == expected


def test_ordinary_excerpt_keeps_native_search_without_python_source_scanning():
    class NativeSource(str):
        def __getitem__(self, index):
            raise AssertionError("ordinary excerpts must retain the native search fast path")

    assert list(package._source_match_starts(NativeSource("ordinary reference text"), "reference")) == [9]


def test_coverage_verdict_and_budget_match_independent_small_random_oracle():
    randomizer = random.Random(3765)

    def actual(content, bodies, remaining):
        return package._source_is_covered(content, bodies, remaining_matches=remaining)

    for _ in range(160):
        content = "".join(randomizer.choice("aab猫🙂é\u0301") for _ in range(randomizer.randrange(45)))
        bodies = []
        for _ in range(randomizer.randrange(6)):
            start = randomizer.randrange(len(content) + 1)
            body = content[start:start + randomizer.randrange(20)]
            bodies.append(body if randomizer.randrange(8) else body + "foreign")
        for budget in (-1, 0, 1, 8, 33, 1000):
            assert _outcome(actual, content, bodies, budget) == _outcome(
                _reference_coverage, content, bodies, budget)


@pytest.mark.parametrize("distinct", [128, 129])
def test_occurrence_cache_keeps_its_existing_128_body_budget(distinct):
    # Revisiting body 1 costs nothing at 128, but costs all 300 starts once
    # it was evicted by body 129. An unbounded cache changes package admission.
    bodies = ["a" * width for width in range(1, distinct + 1)] + ["a"]
    matches = sum(301 - width for width in range(1, distinct + 1)) + (300 if distinct > 128 else 0)
    remaining = [matches]
    assert package._source_is_covered("a" * 300, bodies, remaining_matches=remaining)
    assert remaining == [0]
    with pytest.raises(package.PackageError, match="package_capacity_exceeded"):
        package._source_is_covered("a" * 300, bodies, remaining_matches=[matches - 1])


def test_dense_matching_retains_one_shared_budget_across_entries(tmp_path, monkeypatch):
    def mutate(manifest, members):
        entries = [{"title": "First", "content": "a" * 300}, {"title": "Second", "content": "a" * 300}]
        _replace_entries(manifest, members, entries, [["a" * 100] * 3] * 2)

    path = make_archive(tmp_path / "shared-budget.zip", mutate=mutate)
    # Each entry enumerates 201 starts once; identical bodies within that
    # entry are cached. Crossing entries must still spend the remaining work.
    monkeypatch.setattr(package, "_MAX_COVERAGE_MATCHES", 402)
    assert len(package.validate_package(path).chunks) == 6
    monkeypatch.setattr(package, "_MAX_COVERAGE_MATCHES", 401)
    with pytest.raises(package.PackageError, match="package_capacity_exceeded"):
        package.validate_package(path)


def test_long_repetitive_plan_remains_a_valid_portable_archive(tmp_path):
    def mutate(manifest, members):
        _replace_entries(manifest, members, [{"title": "Example", "content": "a" * 80000}],
                         [["a" * width for width in range(1900, 1942)]])

    checked = package.validate_package(make_archive(tmp_path / "complete-repeated.zip", mutate=mutate))
    assert len(checked.chunks) == 42
