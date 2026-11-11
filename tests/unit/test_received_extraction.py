"""Lines another device said wait their turn, then get their facts extracted here."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from memory import extraction_progress, received_messages
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_recent_memory_sync import DOMAIN, Server, commit, devices, row  # noqa: F401
from utils.llm_client import convert_to_messages

UID = 'a' * 32
# Written out, not read from the module: the wait itself is what these tests pin down.
FIFTEEN_MINUTES = 15 * 60 * 1000
A, B, C = 'a' * 32, 'b' * 32, 'c' * 32


def line(identity, text, role='user', original_time='1000'):
    return {'id': identity, 'role': role, 'text': text, 'originalTime': original_time}


def test_arrivals_keep_only_spoken_lines_in_arrival_order(tmp_path):
    assert received_messages.batches(tmp_path / 'missing') == []
    assert not received_messages.keep_arrivals(tmp_path, [line('0' * 32, 'summary of older talk', role='memo'), line(A, '   ')], 5000)
    assert received_messages.keep_arrivals(tmp_path, [line(B, 'second arrival')], 9000)
    assert received_messages.keep_arrivals(tmp_path, [line(A, 'first arrival'), line(C, 'her reply', role='assistant')], 7000)
    (tmp_path / 'not-a-batch.json').write_text('{}', encoding='utf-8')
    (tmp_path / f'{8000:015d}-{"0" * 12}.json').write_text('{broken', encoding='utf-8')
    pending = received_messages.batches(tmp_path)
    assert [(batch['receivedAt'], [entry['id'] for entry in batch['entries']]) for batch in pending] == [(7000, [A, C]), (9000, [B])]
    received_messages.discard([pending[0]['path']])
    assert [batch['receivedAt'] for batch in received_messages.batches(tmp_path)] == [9000]


def test_arrivals_are_bounded_by_dropping_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(received_messages, 'MAX_BATCHES', 3)
    for index in range(5):
        received_messages.keep_arrivals(tmp_path, [line(f'{index:032x}', f'line {index}')], 1000 + index)
    assert [batch['receivedAt'] for batch in received_messages.batches(tmp_path)] == [1002, 1003, 1004]


def test_an_arrival_is_read_under_the_identity_and_time_every_device_knows_it_by():
    from memory.long_term_sync import trusted_message_source
    messages = convert_to_messages(received_messages.message_dicts([line(A, 'I moved to Kyoto'), line(B, 'noted', 'assistant')]))
    assert [(message.type, message.content) for message in messages] == [('human', 'I moved to Kyoto'), ('ai', 'noted')]
    assert trusted_message_source(messages[0]) == (f'sync:{A}', 1000)
    assert extraction_progress.identities(messages) == [A, B]


@pytest.mark.asyncio
async def test_lines_arriving_from_another_device_are_copied_aside_once(devices, tmp_path):  # noqa: F811
    """This device's own lines, the summary memo and lines already seen are not arrivals."""
    server = Server()
    phone = devices('phone', [row('human', 'said on the phone', recent_memory_id=A),
                              row('ai', 'answered on the phone', recent_memory_id=B)])
    desktop = devices('desktop', [row('human', 'said on the desktop', recent_memory_id=C)])
    desktop[0].received_directory = lambda uid: tmp_path / 'received' / uid
    await desktop[0].sync(DOMAIN, 'Role', server.request, commit)
    assert received_messages.batches(tmp_path / 'received' / UID) == []
    await phone[0].sync(DOMAIN, 'Role', server.request, commit)
    await desktop[0].sync(DOMAIN, 'Role', server.request, commit)
    arrived = received_messages.batches(tmp_path / 'received' / UID)
    assert [[(entry['id'], entry['role'], entry['text']) for entry in batch['entries']] for batch in arrived] == [
        [(A, 'user', 'said on the phone'), (B, 'assistant', 'answered on the phone')]]
    await desktop[0].sync(DOMAIN, 'Role', server.request, commit)
    assert len(received_messages.batches(tmp_path / 'received' / UID)) == 1
    # Without learned-memory sync nothing is kept: the phone was not given a folder.
    assert not (tmp_path / 'phone' / 'received').exists()


@pytest.mark.asyncio
async def test_arrivals_are_copied_aside_before_the_projection_is_applied(devices, tmp_path):  # noqa: F811
    """Once applied the lines look local, so a crash in between must not lose them for extraction."""
    server = Server()
    phone = devices('phone', [row('human', 'said on the phone', recent_memory_id=A)])
    desktop = devices('desktop', [])
    desktop[0].received_directory = lambda uid: tmp_path / 'received' / uid
    await phone[0].sync(DOMAIN, 'Role', server.request, commit)
    # The projection cannot be applied this time; the arrival is kept all the same.
    result = await desktop[0].sync(DOMAIN, 'Role', server.request, commit, can_apply=False)
    assert result['hasMore']
    waiting = received_messages.batches(tmp_path / 'received' / UID)
    assert [[entry['id'] for entry in batch['entries']] for batch in waiting] == [[A]]
    # Offered again on the next sync, it is neither kept twice nor lost.
    await desktop[0].sync(DOMAIN, 'Role', server.request, commit)
    assert [[entry['id'] for entry in batch['entries']] for batch in received_messages.batches(tmp_path / 'received' / UID)] == [[A]]


@pytest.fixture
def extractor(tmp_path, monkeypatch):
    from app.memory_server import received_extraction
    directory = tmp_path / 'received' / UID
    state = SimpleNamespace(now=10_000_000, checked=None, progress={'a': [], 'b': []}, extracted=[], captures=[],
                            both=[], pools=[], known=[], directory=directory, module=received_extraction)

    async def extract_facts(messages, name, *, fail_closed):
        assert fail_closed
        state.extracted.append([message.content for message in messages])
        if isinstance(state.failure, Exception):
            raise state.failure
        # As the real store does: what a pass read is recorded with what it saved.
        state.progress = {**state.progress, 'a': state.progress['a'] + extraction_progress.identities(messages)}
        return []

    async def extract_with_known_pool(name, messages, known_pool, *, conversation_at):
        state.both.append([(message.type, message.content) for message in messages])
        state.pools.append(([fact['id'] for fact in known_pool], conversation_at))
        if state.both_failure is not None:
            if isinstance(state.both_failure, Exception):
                raise state.both_failure
            return None
        state.progress = {**state.progress, 'b': state.progress['b'] + extraction_progress.identities(messages)}
        return []
    state.failure = state.both_failure = None
    store = SimpleNamespace(aextraction_progress=AsyncMock(side_effect=lambda name: state.progress), extract_facts=extract_facts,
                            aextract_facts_with_known_pool=extract_with_known_pool,
                            aload_facts=AsyncMock(side_effect=lambda name: state.known))
    monkeypatch.setattr(received_extraction.runtime, 'fact_store', store)
    monkeypatch.setattr(received_extraction.runtime, '_config_manager', SimpleNamespace(app_docs_dir=tmp_path))
    monkeypatch.setattr(received_extraction, 'DesktopLongTermAdapter',
                        lambda config: SimpleNamespace(received_directory=lambda uid: tmp_path / 'received' / uid))
    monkeypatch.setattr(received_extraction, '_now_ms', lambda: state.now)
    monkeypatch.setattr(received_extraction, '_snapshot_checked_at', lambda uid: state.checked)
    monkeypatch.setattr(received_extraction, '_failures', {})
    from app.memory_server import long_term_sync
    monkeypatch.setattr(long_term_sync, 'queue_capture_after_extraction', state.captures.append)
    return state


@pytest.mark.asyncio
async def test_the_device_that_said_a_line_keeps_the_first_turn(extractor):
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [line(A, 'I moved to Kyoto'), line(B, 'noted', 'assistant')], extractor.now)
    extractor.checked = extractor.now + delay
    extractor.now += delay - 1
    assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and len(received_messages.batches(extractor.directory)) == 1
    extractor.now += 1
    assert await extractor.module.extract_received('Neko', UID) == 1
    # Only what the user said goes through the user pass. Her reply came after the last
    # thing the user said, so the both-sides pass leaves it out too; the batch is then done.
    assert extractor.extracted == [['I moved to Kyoto']] and extractor.both == [[('human', 'I moved to Kyoto')]]
    assert extractor.captures == ['Neko']
    assert received_messages.batches(extractor.directory) == []


@pytest.mark.asyncio
async def test_nothing_is_extracted_until_a_snapshot_check_has_succeeded_since_the_arrival(extractor):
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    arrived = extractor.now
    extractor.now += delay + 60_000
    # A check made while the other device still had its turn proves nothing about
    # progress it published afterwards; only one after the wait counts.
    for checked in (None, arrived - 1, arrived, arrived + delay - 1):
        extractor.checked = checked
        assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and len(received_messages.batches(extractor.directory)) == 1
    extractor.checked = arrived + delay
    assert await extractor.module.extract_received('Neko', UID) == 1


@pytest.mark.asyncio
async def test_lines_the_other_device_already_read_are_not_sent_to_the_model(extractor):
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [line(A, 'I moved to Kyoto'), line(B, 'I have a cat')], extractor.now)
    extractor.now += delay
    extractor.checked = extractor.now
    extractor.progress = {'a': [A], 'b': [A]}
    assert await extractor.module.extract_received('Neko', UID) == 1
    assert extractor.extracted == [['I have a cat']] and extractor.both == [[('human', 'I have a cat')]]
    received_messages.keep_arrivals(extractor.directory, [line(C, 'I like tea')], extractor.now)
    extractor.now += delay
    extractor.checked = extractor.now
    extractor.progress = {'a': [A, B, C], 'b': [A, B, C]}
    assert await extractor.module.extract_received('Neko', UID) == 0
    # Fully read elsewhere: no model call, nothing to publish, and the batch is still cleared.
    assert extractor.extracted == [['I have a cat']] and len(extractor.both) == 1 and extractor.captures == ['Neko']
    assert received_messages.batches(extractor.directory) == []


@pytest.mark.asyncio
async def test_a_backlog_is_worked_off_in_bounded_rounds_and_a_failure_costs_only_its_round(extractor):
    from memory.facts import FactExtractionFailed
    delay = FIFTEEN_MINUTES
    limit = extractor.module.MAX_ENTRIES_PER_RUN
    for batch in range(3):
        received_messages.keep_arrivals(extractor.directory, [
            line(f'{batch:016x}{index:016x}', f'batch {batch} line {index}') for index in range(limit - 10)], extractor.now + batch)
    extractor.now += delay + 10
    extractor.checked = extractor.now
    assert await extractor.module.extract_received('Neko', UID) == limit - 10
    assert [len(texts) for texts in extractor.extracted] == [limit - 10]
    assert extractor.extracted[0][0] == 'batch 0 line 0' and len(received_messages.batches(extractor.directory)) == 2
    # The second round keeps failing: only its own batch is given up, the third is still read.
    extractor.failure = FactExtractionFailed('synthetic')
    for attempt in range(extractor.module.MAX_FAILURES):
        await extractor.module.extract_received('Neko', UID)
    assert len(received_messages.batches(extractor.directory)) == 1
    extractor.failure = None
    assert await extractor.module.extract_received('Neko', UID) == limit - 10
    assert extractor.extracted[-1][0] == 'batch 2 line 0' and received_messages.batches(extractor.directory) == []


@pytest.mark.asyncio
async def test_a_clock_moved_backwards_only_delays_extraction(extractor):
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    extractor.checked = extractor.now
    extractor.now -= 10 * delay
    assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and len(received_messages.batches(extractor.directory)) == 1


@pytest.mark.asyncio
async def test_a_failing_batch_is_retried_and_finally_given_up_without_blocking_later_ones(extractor):
    from memory.facts import FactExtractionFailed
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    extractor.now += delay
    extractor.checked = extractor.now
    extractor.failure = FactExtractionFailed('synthetic')
    for attempt in range(extractor.module.MAX_FAILURES - 1):
        assert await extractor.module.extract_received('Neko', UID) == 0
        # Another line arrives and falls due while the first batch keeps failing:
        # the count still belongs to the oldest batch and is not started over.
        received_messages.keep_arrivals(extractor.directory, [line(f'{attempt:032x}', f'later line {attempt}')],
                                        extractor.now - delay + 1)
        extractor.now += 10
        extractor.checked = extractor.now
        assert len(received_messages.batches(extractor.directory)) == attempt + 2
    await extractor.module.extract_received('Neko', UID)
    assert received_messages.batches(extractor.directory) == [] and extractor.captures == []



def test_the_newest_snapshot_mark_is_refused_while_a_downloaded_revision_waits(tmp_path, monkeypatch):
    from app.memory_server import long_term_sync, received_extraction
    from memory.long_term_sync import DesktopLongTermAdapter
    config = SimpleNamespace(app_docs_dir=tmp_path)
    adapter = DesktopLongTermAdapter(config)
    monkeypatch.setattr(received_extraction.runtime, '_config_manager', config)
    monkeypatch.setattr(long_term_sync, '_authority', object)
    monkeypatch.setattr(long_term_sync, '_binding', lambda uid: {'owner_id': 'owner'})
    assert received_extraction._snapshot_checked_at(UID) is None
    adapter.mark_checked(UID, 7000)
    assert received_extraction._snapshot_checked_at(UID) == 7000
    adapter.incoming_path(UID).write_text('{}', encoding='utf-8')
    assert received_extraction._snapshot_checked_at(UID) is None
    adapter.incoming_path(UID).unlink()
    monkeypatch.setattr(long_term_sync, '_binding', lambda uid: None)
    assert received_extraction._snapshot_checked_at(UID) is None


@pytest.mark.asyncio
async def test_what_she_said_between_two_user_lines_is_read_by_the_both_sides_pass(extractor):
    from datetime import datetime
    delay = FIFTEEN_MINUTES
    said = 1_700_000_000_000
    received_messages.keep_arrivals(extractor.directory, [
        line('1' * 32, 'opening remark nobody answered', 'assistant', str(said - 1000)),
        line(A, 'I moved to Kyoto', original_time=str(said)),
        line(B, 'I once lived in Nara myself', 'assistant', str(said + 1000)),
        line(C, 'really?', original_time=str(said + 2000)),
        line('2' * 32, 'closing remark nobody answered', 'assistant', str(said + 3000))], extractor.now)
    before, after = datetime.fromtimestamp((said - 1000) / 1000), datetime.fromtimestamp((said + 3000) / 1000)
    extractor.known = [
        {'id': 'older', 'created_at': datetime.fromtimestamp(said / 1000 - 3600).isoformat(), 'importance': 9},
        {'id': 'low', 'created_at': before.isoformat(), 'importance': 2},
        {'id': 'high', 'created_at': after.isoformat(), 'importance': 8},
        {'id': 'group', 'created_at': after.isoformat(), 'importance': 9, 'scope': 'group:g1', 'subject_kind': 'group_chat',
         'subject_id': 'g1'}]
    extractor.now += delay
    extractor.checked = extractor.now
    # Three lines went to the model: two through both passes, her middle one through the second only.
    assert await extractor.module.extract_received('Neko', UID) == 3
    assert extractor.extracted == [['I moved to Kyoto', 'really?']]
    assert extractor.both == [[('human', 'I moved to Kyoto'), ('ai', 'I once lived in Nara myself'), ('human', 'really?')]]
    # Known facts: private ones written since the first of these lines was said, most important
    # first; the conversation is dated by its last line, not by when it arrived here.
    assert extractor.pools == [(['high', 'low'], after)]
    assert received_messages.batches(extractor.directory) == [] and extractor.captures == ['Neko']


@pytest.mark.asyncio
async def test_a_both_sides_pass_another_device_already_ran_is_not_run_again(extractor):
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [
        line(A, 'I moved to Kyoto'), line(B, 'noted', 'assistant'), line(C, 'yes')], extractor.now)
    extractor.now += delay
    extractor.checked = extractor.now
    extractor.progress = {'a': [A, C], 'b': [A, B, C]}
    assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and extractor.both == [] and extractor.captures == []
    assert received_messages.batches(extractor.directory) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('unavailable', [False, True])
async def test_a_failing_both_sides_pass_keeps_the_batch_without_repeating_the_user_pass(extractor, unavailable):
    from memory.long_term_sync import FactEvidenceUnavailable
    delay = FIFTEEN_MINUTES
    received_messages.keep_arrivals(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    extractor.now += delay
    extractor.checked = extractor.now
    extractor.both_failure = FactEvidenceUnavailable('synthetic') if unavailable else 'the model call failed'
    assert await extractor.module.extract_received('Neko', UID) == 0
    # What the user pass saved is published; the batch waits for the other pass.
    assert extractor.captures == ['Neko'] and len(received_messages.batches(extractor.directory)) == 1
    for attempt in range(extractor.module.MAX_FAILURES - 2):
        assert await extractor.module.extract_received('Neko', UID) == 0
    assert len(extractor.extracted) == 1 and len(extractor.both) == extractor.module.MAX_FAILURES - 1
    assert len(received_messages.batches(extractor.directory)) == 1
    # Given up after the same number of tries as the user pass, so later arrivals are not blocked.
    await extractor.module.extract_received('Neko', UID)
    assert received_messages.batches(extractor.directory) == [] and extractor.captures == ['Neko']
    received_messages.keep_arrivals(extractor.directory, [line(B, 'I have a cat')], extractor.now)
    extractor.now += delay
    extractor.checked = extractor.now
    extractor.both_failure = None
    assert await extractor.module.extract_received('Neko', UID) == 1
    assert extractor.both[-1] == [('human', 'I have a cat')] and received_messages.batches(extractor.directory) == []


def test_a_line_without_a_usable_time_counts_as_said_when_it_arrived():
    from datetime import datetime
    from app.memory_server.received_extraction import _said_between
    arrived = 1_700_000_500_000
    assert _said_between([line(A, 'x', original_time=None), line(B, 'y', original_time='not a time')], arrived) == (
        datetime.fromtimestamp(arrived / 1000),) * 2
    assert _said_between([line(A, 'x', original_time='1700000000000'), line(B, 'y', original_time=None)], arrived) == (
        datetime.fromtimestamp(1_700_000_000), datetime.fromtimestamp(arrived / 1000))
