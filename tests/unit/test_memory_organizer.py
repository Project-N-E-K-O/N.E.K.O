"""One device organizes a character's learned memory; the others extract and wait."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from memory import organizer
from memory.organizer import CLAIM, NONE, RELEASE, RENEW, RENEW_INTERVAL_MS, TAKEOVER_AFTER_MS
from tests.unit.test_evidence_promote_merge import _install
from tests.unit.test_long_term_memory_sync import (  # noqa: F401
    UID, PublishingServer, isolated_chat_sync_writer, local_state, native_fact, owner,
)
from utils.file_utils import atomic_write_json

DESKTOP, PHONE = '1' * 32, '2' * 32
NAME = '小天'


@pytest.fixture
def gate():
    """Install an answer to "does this device organize?" and always remove it again."""
    answers = {}
    organizer.set_gate(lambda name: answers.get(name, False))
    try:
        yield answers
    finally:
        organizer.set_gate(None)


def held(device, renewed=1000, rank=1):
    return {'deviceId': device, 'renewedAt': renewed, 'rank': rank}


def test_nobody_holding_is_claimed_and_an_unable_device_never_claims():
    assert organizer.decide(None, DESKTOP, 4, None, 5000) == CLAIM
    assert organizer.decide(None, DESKTOP, 0, None, 5000) == NONE
    assert organizer.decide(held(PHONE), DESKTOP, 0, None, 5000) == NONE
    # A holder that can no longer organize gives the role up instead of sitting on it.
    assert organizer.decide(held(DESKTOP, rank=4), DESKTOP, 0, None, 5000) == RELEASE


def test_a_higher_rank_takes_the_role_back_and_an_equal_one_waits():
    seen = organizer.observe(held(PHONE, rank=1), None, 5000)
    assert organizer.decide(held(PHONE, rank=1), DESKTOP, 2, seen, 5000) == CLAIM
    assert organizer.decide(held(PHONE, rank=3), DESKTOP, 4, seen, 5000) == CLAIM
    assert organizer.decide(held(PHONE, rank=2), DESKTOP, 2, seen, 5000) == NONE
    assert organizer.decide(held(PHONE, rank=3), DESKTOP, 2, seen, 5000) == NONE


@pytest.mark.parametrize('renewed', [1, 5000, 9_007_199_254_740_991])
def test_takeover_waits_on_this_devices_own_clock_whatever_the_holder_wrote(renewed):
    """The holder's renewedAt may be years off in either direction; only its changing matters."""
    holder = held(PHONE, renewed, rank=4)
    seen = organizer.observe(holder, None, 50_000)
    assert seen == {'deviceId': PHONE, 'renewedAt': renewed, 'seenAt': 50_000}
    assert organizer.decide(holder, DESKTOP, 2, seen, 50_000 + TAKEOVER_AFTER_MS - 1) == NONE
    assert organizer.decide(holder, DESKTOP, 2, organizer.observe(holder, seen, 50_000 + TAKEOVER_AFTER_MS),
                            50_000 + TAKEOVER_AFTER_MS) == CLAIM
    # A renewal restarts the wait, however its new value compares with the old one.
    renewal = held(PHONE, renewed - 1 if renewed > 1 else 7, rank=4)
    later = 50_000 + TAKEOVER_AFTER_MS
    refreshed = organizer.observe(renewal, seen, later)
    assert refreshed['seenAt'] == later
    assert organizer.decide(renewal, DESKTOP, 2, refreshed, later + TAKEOVER_AFTER_MS - 1) == NONE


def test_a_clock_moved_backwards_restarts_the_wait():
    holder = held(PHONE)
    seen = organizer.observe(holder, None, 9_000_000)
    again = organizer.observe(holder, seen, 1_000)
    assert again['seenAt'] == 1_000
    assert organizer.decide(holder, DESKTOP, 1, again, 1_000) == NONE
    assert organizer.observe(None, seen, 1_000) is None


def test_the_holder_renews_hourly_and_when_its_rank_changes():
    mine = held(DESKTOP, rank=4)
    seen = organizer.observe(mine, None, 1_000)
    assert organizer.decide(mine, DESKTOP, 4, seen, 1_000 + RENEW_INTERVAL_MS - 1) == NONE
    assert organizer.decide(mine, DESKTOP, 4, seen, 1_000 + RENEW_INTERVAL_MS) == RENEW
    assert organizer.decide(mine, DESKTOP, 2, seen, 1_000) == RENEW


def test_without_a_gate_or_for_a_partition_everything_is_allowed(gate):
    assert organizer.allowed(NAME) is False
    gate[NAME] = True
    assert organizer.allowed(NAME) is True
    assert organizer.allowed('other', subject={'scope': 'group'}) is True
    organizer.set_gate(None)
    assert organizer.allowed('other') is True


@pytest.mark.asyncio
async def test_a_device_that_does_not_organize_leaves_private_promotion_to_the_organizer(tmp_path, gate):
    _, _, _, engine, _ = _install(str(tmp_path))
    with patch.object(engine, '_aauto_promote_stale_locked', new=AsyncMock(return_value=3)) as stale, \
            patch.object(engine, '_aauto_promote_score_driven', new=AsyncMock(return_value=1)) as scored, \
            patch.object(engine, 'aauto_promote_time_driven', new=AsyncMock(return_value=0)) as timed:
        assert await engine.aauto_promote_stale(NAME) == 0
        # Group and member partitions never sync, so their lifecycle still runs here.
        assert not stale.called and not scored.called
        timed.assert_awaited_once_with(NAME, scoped_only=True)
        gate[NAME] = True
        assert await engine.aauto_promote_stale(NAME) == 3
        assert stale.called and scored.called


@pytest.mark.asyncio
async def test_time_driven_promotion_keeps_only_partitions_on_a_device_that_does_not_organize(tmp_path, gate):
    from datetime import datetime, timedelta
    _, _, _, engine, _ = _install(str(tmp_path))
    old = (datetime.now() - timedelta(days=400)).isoformat()
    reflection = {'id': 'ref_private', 'entity': 'master', 'text': '主人喜欢安静的晚上', 'status': 'pending',
                  'created_at': old, 'source_fact_ids': []}
    await engine.asave_reflections(NAME, [dict(reflection)])
    assert await engine.aauto_promote_time_driven(NAME) == 0
    assert (await engine.aload_reflections(NAME))[0]['status'] == 'pending'
    gate[NAME] = True
    await engine.aauto_promote_time_driven(NAME)
    assert (await engine.aload_reflections(NAME))[0]['status'] != 'pending'


@pytest.mark.asyncio
async def test_synthesis_and_legacy_rechecks_never_reach_a_model_on_a_device_that_does_not_organize(tmp_path, gate):
    _, facts, _, engine, _ = _install(str(tmp_path))
    with patch.object(facts, 'aload_facts', new=AsyncMock(side_effect=AssertionError('facts read'))), \
            patch.object(engine, 'aload_reflections', new=AsyncMock(side_effect=AssertionError('reflections read'))):
        assert await engine.synthesize_reflections(NAME) == []
        assert await engine.reflect(NAME) is None
        assert await engine.arecheck_one_legacy_reflection(NAME) is False
        assert await facts.arecheck_one_legacy_fact(NAME) is False
        gate[NAME] = True
        with pytest.raises(AssertionError):
            await facts.arecheck_one_legacy_fact(NAME)
        with pytest.raises(AssertionError):
            await engine.arecheck_one_legacy_reflection(NAME)


@pytest.mark.asyncio
async def test_extraction_leaves_facts_unjudged_unless_this_device_organizes(tmp_path):
    from tests.unit.test_evidence_extraction import _install_factstore
    from tests.unit.test_extraction_progress import message
    fs, _ = _install_factstore(str(tmp_path))
    extracted = [{'text': '主人喜欢绿茶', 'importance': 6, 'entity': 'master', 'source_indices': [1]}]
    with patch.object(fs, '_allm_extract_facts', new=AsyncMock(return_value=extracted)), \
            patch.object(fs, 'adetect_pending_signals', new=AsyncMock(return_value=([{'signal': 1}], ['f']))) as judge:
        persisted, signals, batch = await fs.aextract_facts_and_detect_signals(NAME, [message('我喜欢绿茶', 'a' * 32)], judge=False)
        assert len(persisted) == 1 and signals == [] and batch == [] and not judge.called
        assert (await fs.aload_facts(NAME))[0]['signal_processed'] is False
        _, signals, batch = await fs.aextract_facts_and_detect_signals(NAME, [message('我也喜欢红茶', 'b' * 32)])
        assert signals == [{'signal': 1}] and batch == ['f'] and judge.called


@pytest.mark.asyncio
async def test_the_organizer_judges_waiting_facts_without_a_conversation_of_its_own(monkeypatch, gate):
    """Facts a phone extracted arrive unjudged; the desktop judges them on an idle tick."""
    from types import SimpleNamespace
    from app.memory_server import signal_extraction
    calls = []
    store = SimpleNamespace(
        adetect_pending_signals=AsyncMock(return_value=([{'target': 'r1'}], ['from-phone'])),
        amark_signal_processed=AsyncMock(side_effect=lambda name, ids: calls.append(('mark', ids))))
    engine = SimpleNamespace(aauto_promote_stale=AsyncMock(return_value=0))
    monkeypatch.setattr(signal_extraction.runtime, 'fact_store', store)
    monkeypatch.setattr(signal_extraction.runtime, 'reflection_engine', engine)
    monkeypatch.setattr(signal_extraction.runtime, 'persona_manager', object())
    dispatched = AsyncMock(side_effect=lambda name, signals, source: calls.append(('dispatch', signals)) or True)
    monkeypatch.setattr(signal_extraction, '_adispatch_evidence_signals', dispatched)
    monkeypatch.setattr(signal_extraction, '_publish_extraction', lambda name: calls.append(('publish', name)))

    await signal_extraction._judge_pending(NAME)
    assert calls == [] and not store.adetect_pending_signals.called
    gate[NAME] = True
    await signal_extraction._judge_pending(NAME)
    # Scores are applied before the facts are marked judged, and both leave in one snapshot.
    assert calls == [('dispatch', [{'target': 'r1'}]), ('mark', ['from-phone']), ('publish', NAME)]
    calls.clear()
    dispatched.side_effect = lambda name, signals, source: False
    await signal_extraction._judge_pending(NAME)
    assert calls == []


def _synced(owner_fixture):  # noqa: F811
    """Give the fixture character a device identity and facts, and a confirmed first upload."""
    atomic_write_json(owner_fixture.directory / 'facts.json', [native_fact('mine', 'only on this device')])


@pytest.mark.asyncio
async def test_a_character_that_does_not_sync_is_organized_here_as_before(owner, monkeypatch):  # noqa: F811
    assert owner.native.organizes('Neko') is False
    assert owner.native.organizes('Unknown') is True
    owner.runtime.state.pause(UID)
    assert owner.native.organizes('Neko') is True
    monkeypatch.setattr(owner.native, '_authority', lambda: None)
    assert owner.native.organizes('Neko') is True


@pytest.mark.asyncio
async def test_the_role_is_held_only_once_the_server_confirmed_the_claim(owner):  # noqa: F811
    _synced(owner)
    device = owner.runtime.state.settings()['device_id']
    server = PublishingServer()
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is True
    staged = json.loads((owner.directory / 'long_term_portable.json').read_text(encoding='utf-8'))['organizer']
    assert staged['deviceId'] == device and staged['rank'] == organizer.RANK_DESKTOP
    # Staged but not confirmed: checking again changes nothing and the role is not held yet.
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is True
    assert json.loads((owner.directory / 'long_term_portable.json').read_text(encoding='utf-8'))['organizer'] == staged
    assert owner.native.organizes('Neko') is False
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.document['organizer'] == staged and owner.native.organizes('Neko') is True
    # Confirmed and fresh: nothing more to publish.
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is False
    # Turning the evidence pipeline on changes the rank, which is published as a renewal.
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP_EVIDENCE) is True
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.document['organizer']['rank'] == organizer.RANK_DESKTOP_EVIDENCE
    assert server.document['organizer']['deviceId'] == device


@pytest.mark.asyncio
async def test_a_device_yields_to_a_higher_claim_and_takes_over_a_silent_one(owner, monkeypatch):  # noqa: F811
    _synced(owner)
    server = PublishingServer()
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    phone = {'deviceId': PHONE, 'renewedAt': 9_007_199_254_740_000, 'rank': organizer.RANK_PHONE_EVIDENCE}
    server.publish({**server.document, 'completedAt': server.document['completedAt'] + 10, 'organizer': phone})
    await server.exchange(owner)
    assert await owner.native.consume(UID)
    assert owner.native.organizes('Neko') is False
    clock = [1_000_000_000_000]
    monkeypatch.setattr(owner.native, '_now_ms', lambda: clock[0])
    # Lower rank and a fresh claim: this device does not organize and does not try to.
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is False
    clock[0] += TAKEOVER_AFTER_MS - 1
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is False
    clock[0] += 1
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is True
    staged = json.loads((owner.directory / 'long_term_portable.json').read_text(encoding='utf-8'))['organizer']
    assert staged['deviceId'] == owner.runtime.state.settings()['device_id']


@pytest.mark.asyncio
async def test_only_one_of_two_simultaneous_claims_survives_the_merge(owner):  # noqa: F811
    """Both devices claim from the same base; the one the server took first keeps the role."""
    _synced(owner)
    server = PublishingServer()
    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is True
    assert owner.native._capture('Neko', False)
    phone = {'deviceId': PHONE, 'renewedAt': 5, 'rank': organizer.RANK_DESKTOP}
    server.publish({**server.document, 'completedAt': server.document['completedAt'] + 10, 'organizer': phone})
    await server.exchange(owner)
    assert await owner.native.consume(UID)
    assert local_state(owner)['base']['document']['organizer'] == phone
    assert json.loads((owner.directory / 'long_term_portable.json').read_text(encoding='utf-8'))['organizer'] == phone
    assert owner.native.organizes('Neko') is False
    # The loser does not try again while the winner's claim is fresh.
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is False


@pytest.mark.asyncio
async def test_only_the_organizer_trims_evidence_and_only_what_the_server_already_holds(owner, monkeypatch):  # noqa: F811
    from memory import long_term_merge
    monkeypatch.setattr(long_term_merge, 'EVIDENCE_PRUNE_ABOVE', 2)
    monkeypatch.setattr(long_term_merge, 'EVIDENCE_PRUNE_TO', 1)
    state_path = owner.directory / 'long_term_portable.json'
    retired = [{'id': f'old-{index}', 'subject': 'master', 'sourceMessageIds': [f'source-{index}']} for index in range(3)]
    atomic_write_json(state_path, {'evidence': {'retirements': retired, 'confirmations': []}})
    _synced(owner)
    server = PublishingServer()

    def stored():
        return json.loads(state_path.read_text(encoding='utf-8'))['evidence']['retirements']

    assert owner.native._capture('Neko', True)
    await server.exchange(owner)
    # Published, but this device does not organize: nothing is trimmed.
    assert not owner.native._capture('Neko', False) and stored() == retired
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is True
    # A retirement the server has not seen yet keeps everything in place.
    atomic_write_json(state_path, {**json.loads(state_path.read_text(encoding='utf-8')), 'evidence': {
        'retirements': [*retired, {'id': 'fresh', 'subject': 'master', 'sourceMessageIds': ['source-new']}], 'confirmations': []}})
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert owner.native.organizes('Neko') and len(server.document['evidence']['retirements']) == 4
    # Now the organizer, with everything published: the oldest records go.
    assert owner.native._capture('Neko', False)
    assert [row['id'] for row in stored()] == ['fresh']
    await server.exchange(owner)
    assert [row['id'] for row in server.document['evidence']['retirements']] == ['fresh']
    assert not owner.native._capture('Neko', False)



@pytest.mark.asyncio
@pytest.mark.parametrize('organizes', [False, True])
async def test_weak_memory_never_promotes_group_reflections_whoever_organizes(tmp_path, gate, organizes):
    """Not organizing must not switch the weak-memory caller into the mode that writes group persona."""
    from datetime import datetime, timedelta
    _, _, persona, engine, _ = _install(str(tmp_path))
    gate[NAME] = organizes
    old = (datetime.now() - timedelta(days=400)).isoformat()
    scoped = {'id': 'ref_group', 'entity': 'group_chat', 'text': '群里常聊游戏', 'status': 'confirmed', 'created_at': old,
              'confirmed_at': old, 'source_fact_ids': [], 'subject_kind': 'group_chat', 'subject_id': 'g1', 'scope': 'group:g1',
              'observed_days': [(datetime.now() - timedelta(days=offset)).date().isoformat() for offset in (400, 300, 200, 100)]}
    private = {'id': 'ref_private', 'entity': 'master', 'text': '主人喜欢安静的晚上', 'status': 'pending',
               'created_at': old, 'source_fact_ids': []}
    await engine.asave_reflections(NAME, [dict(scoped), dict(private)])
    await engine.aauto_promote_time_driven(NAME)
    rows = {row['id']: row for row in await engine.aload_reflections(NAME)}
    assert rows['ref_group']['status'] == 'confirmed'
    assert (rows['ref_private']['status'] == 'pending') is (not organizes)


@pytest.mark.asyncio
async def test_a_private_review_this_device_may_not_run_does_not_hide_her_other_reviews(monkeypatch, gate):
    from tests.unit import test_behavior_notes as behavior
    from app.memory_server import refine_loops
    monkeypatch.setattr(refine_loops, '_introspection_due', lambda name, now: True)
    monkeypatch.setattr(refine_loops, '_behavior_review_due', lambda name, now: True)
    monkeypatch.setattr(refine_loops, '_abehavior_review_enabled', AsyncMock(return_value=True))
    runs = behavior._tick_runtime(monkeypatch)
    name = next(iter(refine_loops.runtime._config_manager.aload_characters.return_value['猫娘']))
    await refine_loops._run_introspection_tick()
    assert runs.behavior.await_count == 1 and runs.private.await_count == 0
    gate[name] = True
    await refine_loops._run_introspection_tick()
    assert runs.private.await_count == 1


@pytest.mark.asyncio
async def test_a_snapshot_the_holder_publishes_anyway_renews_its_claim(owner, monkeypatch):  # noqa: F811
    _synced(owner)
    server = PublishingServer()
    assert owner.native._tend_organizer_state('Neko', organizer.RANK_DESKTOP) is True
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    claimed = server.document['organizer']
    # Nothing new to say: no snapshot is made just to renew before the hour is up.
    assert not owner.native._capture('Neko', False)
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device'),
                                                      native_fact('more', 'learned a moment later')])
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    renewed = server.document['organizer']
    assert renewed['deviceId'] == claimed['deviceId'] and renewed['rank'] == claimed['rank']
    assert renewed['renewedAt'] > claimed['renewedAt']
    # The renewal is part of this device's memory, so the next capture has nothing left to upload.
    assert json.loads((owner.directory / 'long_term_portable.json').read_text(encoding='utf-8'))['organizer'] == renewed
    assert not owner.native._capture('Neko', False)
    # A device that does not hold the role publishes the holder's claim untouched.
    phone = {'deviceId': PHONE, 'renewedAt': 7, 'rank': organizer.RANK_PHONE_EVIDENCE}
    server.publish({**server.document, 'completedAt': server.document['completedAt'] + 10, 'organizer': phone})
    await server.exchange(owner)
    assert await owner.native.consume(UID)
    atomic_write_json(owner.directory / 'facts.json', [native_fact('mine', 'only on this device'),
                                                      native_fact('more', 'learned a moment later'),
                                                      native_fact('third', 'and one more')])
    assert owner.native._capture('Neko', False)
    await server.exchange(owner)
    assert server.document['organizer'] == phone
