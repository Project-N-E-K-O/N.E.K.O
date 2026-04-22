# -*- coding: utf-8 -*-
"""
Unit tests for memory.evidence pure functions.

Covers memory-evidence-rfc §8 success criteria S1, S1b, S2, S3, S18:
- read-time decay math (independent rein/disp clocks)
- derived status tier mapping
- maybe_mark_sub_zero累计 / 防抖 / protected 豁免
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest


@pytest.fixture
def fixed_now():
    return datetime(2026, 4, 22, 12, 0, 0)


# ── effective_reinforcement / effective_disputation (S1) ─────────────


def test_effective_reinforcement_no_signal_returns_zero(fixed_now):
    from memory.evidence import effective_reinforcement
    entry = {'reinforcement': 0.0, 'rein_last_signal_at': None}
    assert effective_reinforcement(entry, fixed_now) == 0.0


def test_effective_reinforcement_fresh_signal_no_decay(fixed_now):
    from memory.evidence import effective_reinforcement
    entry = {
        'reinforcement': 2.0,
        'rein_last_signal_at': fixed_now.isoformat(),
    }
    assert effective_reinforcement(entry, fixed_now) == pytest.approx(2.0)


def test_effective_reinforcement_half_life_decay(fixed_now):
    from config import EVIDENCE_REIN_HALF_LIFE_DAYS
    from memory.evidence import effective_reinforcement
    past = fixed_now - timedelta(days=EVIDENCE_REIN_HALF_LIFE_DAYS)
    entry = {
        'reinforcement': 2.0,
        'rein_last_signal_at': past.isoformat(),
    }
    # One half-life → value halved
    assert effective_reinforcement(entry, fixed_now) == pytest.approx(1.0, abs=1e-6)


def test_effective_disputation_half_life_slower_than_rein(fixed_now):
    from config import (
        EVIDENCE_DISP_HALF_LIFE_DAYS,
        EVIDENCE_REIN_HALF_LIFE_DAYS,
    )
    from memory.evidence import effective_disputation, effective_reinforcement

    age_days = EVIDENCE_REIN_HALF_LIFE_DAYS  # One rein half-life
    past = fixed_now - timedelta(days=age_days)
    rein_entry = {'reinforcement': 1.0, 'rein_last_signal_at': past.isoformat()}
    disp_entry = {'disputation': 1.0, 'disp_last_signal_at': past.isoformat()}
    rein_val = effective_reinforcement(rein_entry, fixed_now)
    disp_val = effective_disputation(disp_entry, fixed_now)
    # With same age, disp decays slower (larger half-life)
    assert disp_val > rein_val
    # Sanity: both less than original
    assert rein_val < 1.0 and disp_val < 1.0
    # disp is about 2 ** (30/180) = 2 ** 0.1667 fraction of original
    expected_disp = 0.5 ** (age_days / EVIDENCE_DISP_HALF_LIFE_DAYS)
    assert disp_val == pytest.approx(expected_disp, rel=1e-6)


# ── evidence_score (S2) ──────────────────────────────────────────────


def test_evidence_score_protected_returns_inf(fixed_now):
    from memory.evidence import evidence_score
    entry = {'protected': True, 'reinforcement': 0.0, 'disputation': 10.0}
    assert evidence_score(entry, fixed_now) == float('inf')


def test_evidence_score_fresh_signals(fixed_now):
    from memory.evidence import evidence_score
    entry = {
        'reinforcement': 2.0, 'disputation': 0.5,
        'rein_last_signal_at': fixed_now.isoformat(),
        'disp_last_signal_at': fixed_now.isoformat(),
    }
    assert evidence_score(entry, fixed_now) == pytest.approx(1.5)


# ── S1b: independent rein/disp clocks ────────────────────────────────


def test_independent_clocks_disp_signal_does_not_reset_rein_clock(fixed_now):
    """Given rein=3 aged 30d (one rein half-life), applying a disp signal
    today must NOT reset rein_last_signal_at; rein stays at ~1.5."""
    from config import EVIDENCE_REIN_HALF_LIFE_DAYS
    from memory.evidence import effective_disputation, effective_reinforcement, evidence_score

    past = fixed_now - timedelta(days=EVIDENCE_REIN_HALF_LIFE_DAYS)
    entry = {
        'reinforcement': 3.0,
        'rein_last_signal_at': past.isoformat(),
        'disputation': 1.0,
        'disp_last_signal_at': fixed_now.isoformat(),  # fresh disp
    }
    assert effective_reinforcement(entry, fixed_now) == pytest.approx(1.5, abs=1e-6)
    assert effective_disputation(entry, fixed_now) == pytest.approx(1.0, abs=1e-6)
    assert evidence_score(entry, fixed_now) == pytest.approx(0.5, abs=1e-6)


def test_independent_clocks_rein_signal_does_not_reset_disp_clock(fixed_now):
    """Symmetric: disp aged, then fresh rein — disp should continue decaying."""
    from config import EVIDENCE_DISP_HALF_LIFE_DAYS
    from memory.evidence import effective_disputation, effective_reinforcement

    past = fixed_now - timedelta(days=EVIDENCE_DISP_HALF_LIFE_DAYS)
    entry = {
        'reinforcement': 1.0, 'rein_last_signal_at': fixed_now.isoformat(),
        'disputation': 2.0, 'disp_last_signal_at': past.isoformat(),
    }
    assert effective_reinforcement(entry, fixed_now) == pytest.approx(1.0)
    # disp decayed to half
    assert effective_disputation(entry, fixed_now) == pytest.approx(1.0, abs=1e-6)


# ── derive_status (S2) ───────────────────────────────────────────────


def test_derive_status_tier_mapping(fixed_now):
    from memory.evidence import derive_status

    def _seed(score: float) -> dict:
        # reinforcement - disputation = score, with fresh timestamps so decay
        # is a no-op for this test
        if score >= 0:
            return {
                'reinforcement': score, 'disputation': 0.0,
                'rein_last_signal_at': fixed_now.isoformat(),
                'disp_last_signal_at': None,
            }
        return {
            'reinforcement': 0.0, 'disputation': -score,
            'rein_last_signal_at': None,
            'disp_last_signal_at': fixed_now.isoformat(),
        }

    assert derive_status(_seed(0.0), fixed_now) == 'pending'
    assert derive_status(_seed(0.5), fixed_now) == 'pending'
    assert derive_status(_seed(1.0), fixed_now) == 'confirmed'
    assert derive_status(_seed(1.99), fixed_now) == 'confirmed'
    assert derive_status(_seed(2.0), fixed_now) == 'promoted'
    assert derive_status(_seed(-1.99), fixed_now) == 'pending'
    assert derive_status(_seed(-2.0), fixed_now) == 'archive_candidate'
    assert derive_status(_seed(-5.0), fixed_now) == 'archive_candidate'


# ── S3: migration seed arithmetic (values per §5.1 table) ────────────


def test_migration_seed_scores_land_on_tier_boundaries(fixed_now):
    """Each legacy status should seed (rein, disp) to a score exactly on the
    corresponding new-tier boundary: §5.1 seed table."""
    from memory.evidence import derive_status

    # Simulate §5.1 seeds via {rein, disp, timestamps}.
    cases = {
        'pending':   (0.0, 0.0, 'pending'),
        'confirmed': (1.0, 0.0, 'confirmed'),
        'promoted':  (2.0, 0.0, 'promoted'),
        'denied':    (0.0, 2.0, 'archive_candidate'),
    }
    for legacy_status, (rein, disp, expected_tier) in cases.items():
        entry = {
            'reinforcement': rein,
            'disputation': disp,
            'rein_last_signal_at': fixed_now.isoformat() if rein else None,
            'disp_last_signal_at': fixed_now.isoformat() if disp else None,
        }
        assert derive_status(entry, fixed_now) == expected_tier, (
            f"legacy {legacy_status!r} should seed to {expected_tier!r}"
        )


# ── S18: maybe_mark_sub_zero accumulation semantics ─────────────────


def test_sub_zero_counter_increments_once_per_day_and_does_not_reset(fixed_now):
    from memory.evidence import maybe_mark_sub_zero
    entry = {
        'reinforcement': 0.0, 'disputation': 2.0,
        'rein_last_signal_at': None,
        'disp_last_signal_at': fixed_now.isoformat(),
        'sub_zero_days': 0,
    }
    # Day 0 increment
    assert maybe_mark_sub_zero(entry, fixed_now) is True
    assert entry['sub_zero_days'] == 1

    # Same day — double increment防抖
    assert maybe_mark_sub_zero(entry, fixed_now) is False
    assert entry['sub_zero_days'] == 1

    # Day 1
    day1 = fixed_now + timedelta(days=1)
    entry['disp_last_signal_at'] = day1.isoformat()  # keep fresh so score still <0
    assert maybe_mark_sub_zero(entry, day1) is True
    assert entry['sub_zero_days'] == 2


def test_sub_zero_counter_does_not_reset_when_score_goes_positive(fixed_now):
    """'归档更积极' — sub_zero_days stays even if score recovers to >= 0."""
    from memory.evidence import maybe_mark_sub_zero
    entry = {
        'reinforcement': 0.0, 'disputation': 2.0,
        'rein_last_signal_at': None,
        'disp_last_signal_at': fixed_now.isoformat(),
        'sub_zero_days': 3,
        'sub_zero_last_increment_date': (fixed_now - timedelta(days=1)).date().isoformat(),
    }
    # Flip the entry positive
    entry['reinforcement'] = 5.0
    entry['rein_last_signal_at'] = fixed_now.isoformat()
    # Score is now +3; counter should NOT be incremented AND NOT reset
    assert maybe_mark_sub_zero(entry, fixed_now) is False
    assert entry['sub_zero_days'] == 3


def test_sub_zero_protected_exempt(fixed_now):
    from memory.evidence import maybe_mark_sub_zero
    entry = {
        'protected': True,
        'reinforcement': 0.0, 'disputation': 100.0,
        'disp_last_signal_at': fixed_now.isoformat(),
        'sub_zero_days': 0,
    }
    assert maybe_mark_sub_zero(entry, fixed_now) is False
    assert entry['sub_zero_days'] == 0


# ── importance → initial rein seed (PR-1 round-8 addition) ──────────


def test_initial_reinforcement_from_importance_curve():
    from memory.evidence import initial_reinforcement_from_importance
    assert initial_reinforcement_from_importance(10) == pytest.approx(0.8)
    assert initial_reinforcement_from_importance(9) == pytest.approx(0.6)
    assert initial_reinforcement_from_importance(8) == pytest.approx(0.4)
    assert initial_reinforcement_from_importance(7) == pytest.approx(0.2)
    assert initial_reinforcement_from_importance(6) == 0.0
    assert initial_reinforcement_from_importance(5) == 0.0
    assert initial_reinforcement_from_importance(1) == 0.0
    # Dirty values gracefully default to 0
    assert initial_reinforcement_from_importance(None) == 0.0
    assert initial_reinforcement_from_importance("hi") == 0.0
    # Above 10: still clamp to the top tier
    assert initial_reinforcement_from_importance(20) == pytest.approx(0.8)
