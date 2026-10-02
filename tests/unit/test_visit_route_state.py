"""Visit route state: a pending placeholder is active, locks are weak per character."""
from __future__ import annotations

import gc

import pytest

from utils import visit_route_state as vrs


@pytest.fixture(autouse=True)
def _clean():
    vrs._reset_for_tests()
    yield
    vrs._reset_for_tests()


def test_pending_placeholder_counts_as_active():
    assert not vrs.is_visit_route_active("A")
    state = vrs.activate_visit_route("A")
    assert state["phase"] == "pending"
    assert vrs.is_visit_route_active("A")
    assert vrs.get_visit_route_state("A") is state
    assert not vrs.is_visit_route_active("B")


def test_finalize_drops_slot_and_flips_stale_reference():
    state = vrs.activate_visit_route("A", phase="active")
    removed = vrs.finalize_visit_route_state("A")
    assert removed is state
    assert state["visit_route_active"] is False
    assert vrs.get_visit_route_state("A") is None
    assert vrs.finalize_visit_route_state("A") is None


def test_inactive_slot_is_not_reported():
    state = vrs.activate_visit_route("A")
    state["visit_route_active"] = False
    assert vrs.get_visit_route_state("A") is None
    assert not vrs.is_visit_route_active("A")


def test_lock_is_shared_while_referenced_and_released_when_idle():
    first = vrs._get_visit_route_lock("A")
    assert vrs._get_visit_route_lock("A") is first
    assert vrs._get_visit_route_lock("B") is not first
    del first
    gc.collect()
    assert "A" not in vrs._visit_route_locks
    # A fresh lock is created on demand after the old one was collected.
    again = vrs._get_visit_route_lock("A")
    assert again is vrs._get_visit_route_lock("A")
