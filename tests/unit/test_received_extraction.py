"""Lines another device said wait their turn, then get their facts extracted here."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from memory import extraction_progress, received_messages
from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401
from tests.unit.test_recent_memory_sync import DOMAIN, Server, commit, devices, row  # noqa: F401
from utils.llm_client import convert_to_messages

UID = 'a' * 32
A, B, C = 'a' * 32, 'b' * 32, 'c' * 32


def line(identity, text, role='user', original_time='1000'):
    return {'id': identity, 'role': role, 'text': text, 'originalTime': original_time}


def test_arrivals_keep_only_spoken_lines_in_arrival_order(tmp_path):
    assert received_messages.batches(tmp_path / 'missing') == []
    assert not received_messages.enqueue(tmp_path, [line('0' * 32, 'summary of older talk', role='memo'), line(A, '   ')], 5000)
    assert received_messages.enqueue(tmp_path, [line(B, 'second arrival')], 9000)
    assert received_messages.enqueue(tmp_path, [line(A, 'first arrival'), line(C, 'her reply', role='assistant')], 7000)
    (tmp_path / 'not-a-batch.json').write_text('{}', encoding='utf-8')
    (tmp_path / f'{8000:015d}-{"0" * 12}.json').write_text('{broken', encoding='utf-8')
    pending = received_messages.batches(tmp_path)
    assert [(batch['receivedAt'], [entry['id'] for entry in batch['entries']]) for batch in pending] == [(7000, [A, C]), (9000, [B])]
    received_messages.discard([pending[0]['path']])
    assert [batch['receivedAt'] for batch in received_messages.batches(tmp_path)] == [9000]


def test_arrivals_are_bounded_by_dropping_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr(received_messages, 'MAX_BATCHES', 3)
    for index in range(5):
        received_messages.enqueue(tmp_path, [line(f'{index:032x}', f'line {index}')], 1000 + index)
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


@pytest.fixture
def extractor(tmp_path, monkeypatch):
    from app.memory_server import received_extraction
    directory = tmp_path / 'received' / UID
    state = SimpleNamespace(now=10_000_000, checked=None, progress={'a': [], 'b': []}, extracted=[], captures=[],
                            directory=directory, module=received_extraction)

    async def extract_facts(messages, name, *, fail_closed):
        assert fail_closed
        state.extracted.append([message.content for message in messages])
        if isinstance(state.failure, Exception):
            raise state.failure
        return []
    state.failure = None
    store = SimpleNamespace(aextraction_progress=AsyncMock(side_effect=lambda name: state.progress), extract_facts=extract_facts)
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
    delay = extractor.module.RECEIVED_EXTRACTION_DELAY_MS
    received_messages.enqueue(extractor.directory, [line(A, 'I moved to Kyoto'), line(B, 'noted', 'assistant')], extractor.now)
    extractor.checked = extractor.now + 1
    extractor.now += delay - 1
    assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and len(received_messages.batches(extractor.directory)) == 1
    extractor.now += 1
    assert await extractor.module.extract_received('Neko', UID) == 1
    # Only what the user said goes through the user pass; the batch is then done.
    assert extractor.extracted == [['I moved to Kyoto']] and extractor.captures == ['Neko']
    assert received_messages.batches(extractor.directory) == []


@pytest.mark.asyncio
async def test_nothing_is_extracted_until_a_snapshot_check_has_succeeded_since_the_arrival(extractor):
    delay = extractor.module.RECEIVED_EXTRACTION_DELAY_MS
    received_messages.enqueue(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    arrived = extractor.now
    extractor.now += delay
    for checked in (None, arrived - 1):
        extractor.checked = checked
        assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and len(received_messages.batches(extractor.directory)) == 1
    extractor.checked = arrived
    assert await extractor.module.extract_received('Neko', UID) == 1


@pytest.mark.asyncio
async def test_lines_the_other_device_already_read_are_not_sent_to_the_model(extractor):
    delay = extractor.module.RECEIVED_EXTRACTION_DELAY_MS
    received_messages.enqueue(extractor.directory, [line(A, 'I moved to Kyoto'), line(B, 'I have a cat')], extractor.now)
    extractor.checked = extractor.now
    extractor.now += delay
    extractor.progress = {'a': [A], 'b': []}
    assert await extractor.module.extract_received('Neko', UID) == 1
    assert extractor.extracted == [['I have a cat']]
    received_messages.enqueue(extractor.directory, [line(C, 'I like tea')], extractor.now)
    extractor.checked = extractor.now
    extractor.now += delay
    extractor.progress = {'a': [A, B, C], 'b': []}
    assert await extractor.module.extract_received('Neko', UID) == 0
    # Fully read elsewhere: no model call, nothing to publish, and the batch is still cleared.
    assert extractor.extracted == [['I have a cat']] and extractor.captures == ['Neko']
    assert received_messages.batches(extractor.directory) == []


@pytest.mark.asyncio
async def test_a_clock_moved_backwards_only_delays_extraction(extractor):
    delay = extractor.module.RECEIVED_EXTRACTION_DELAY_MS
    received_messages.enqueue(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    extractor.checked = extractor.now
    extractor.now -= 10 * delay
    assert await extractor.module.extract_received('Neko', UID) == 0
    assert extractor.extracted == [] and len(received_messages.batches(extractor.directory)) == 1


@pytest.mark.asyncio
async def test_a_failing_batch_is_retried_and_finally_given_up_without_blocking_later_ones(extractor):
    from memory.facts import FactExtractionFailed
    delay = extractor.module.RECEIVED_EXTRACTION_DELAY_MS
    received_messages.enqueue(extractor.directory, [line(A, 'I moved to Kyoto')], extractor.now)
    extractor.checked = extractor.now
    extractor.now += delay
    extractor.failure = FactExtractionFailed('synthetic')
    for attempt in range(extractor.module.MAX_FAILURES - 1):
        assert await extractor.module.extract_received('Neko', UID) == 0
        assert len(received_messages.batches(extractor.directory)) == 1
    await extractor.module.extract_received('Neko', UID)
    assert received_messages.batches(extractor.directory) == [] and extractor.captures == []


def test_a_snapshot_check_counts_only_once_it_has_succeeded(tmp_path):
    from services.chat_sync.scheduler import ChatSyncScheduler
    clock = [1_000_000]
    scheduler = ChatSyncScheduler(tmp_path / 'scheduler.sqlite3', clock=lambda: clock[0])
    assert scheduler.succeeded_at('domain', 'long_term') is None
    assert scheduler.claim('domain', 'long_term', 1, 'session')
    # In flight: a reply that may still carry newer progress has not been stored yet.
    assert scheduler.succeeded_at('domain', 'long_term') is None
    scheduler.success('domain', 'long_term')
    assert scheduler.succeeded_at('domain', 'long_term') == 1_000_000
    assert scheduler.succeeded_at('domain', 'pull') is None
    clock[0] += 10_000_000
    assert scheduler.claim('domain', 'long_term', 1, 'session')
    scheduler.failure('domain', 'long_term', 1, 'session', OSError('offline'))
    assert scheduler.succeeded_at('domain', 'long_term') is None
