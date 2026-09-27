import re
from pathlib import Path
import pytest

from tests.unit.websocket_static._scenarios import (
    _block_after,
)

APP_SETTINGS_PATH = Path(__file__).resolve().parents[3] / "static" / "app" / "app-settings.js"

pytestmark = pytest.mark.frontend_contract


def test_asr_authority_is_per_key_not_granted_by_unrelated_setting_change():
    # Codex P2. syncSettingsToServer({userInitiated:true}) marks the GLOBAL
    # S.settingsHydrated for every user action, including ones that never touch
    # the ASR key (settings popup toggles, subtitle toggles, the chat-window
    # translate toggle). With a pending or permanently failing boot GET,
    # S.independentAsrEnabled is still the boot default false at that moment, so
    # a global-only gate would let the next start_session stamp false over the
    # backend's persisted true. Authority for that one key must therefore be
    # tracked separately and granted only by explicit ASR edits/cross-window
    # choices or an authoritative server snapshot.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    user_gate = _block_after(settings_source, "if (userInitiated) {")
    assert "S.settingsHydrated = true;" in user_gate
    # The per-key mark inside the userInitiated gate must be conditional on the
    # ASR key actually being dirty — an unconditional mark here is the bug.
    assert (
        "if (_dirtySettingsKeys.has('independentAsrEnabled')) "
        "S.independentAsrAuthoritative = true;" in user_gate
    ), "ASR authority must be granted only when the user change touched that key"

    # (1) A merged server GET grants authority.
    merge_block = settings_source.split(
        "const mergeSettled = loadSettingsFromServer().then(serverResult => {",
        1,
    )[1]
    assert "S.independentAsrAuthoritative = true;" in merge_block.split(
        "startPeriodicSync();", 1
    )[0]

    # (2) A full snapshot from a successful partial POST or 412 grants the same
    # server authority when the boot GET was unavailable.
    snapshot_merge = _block_after(
        settings_source, "function _mergeConversationSettingsSnapshot(data, preservedKeys) {"
    )
    assert "S.independentAsrAuthoritative = true;" in snapshot_merge

    # (3) A cross-window ASR flip grants authority, next to the dirty-key add.
    cross_window = _block_after(
        settings_source, "_dirtySettingsKeys.add('independentAsrEnabled');"
    )
    assert "S.independentAsrAuthoritative = true;" in cross_window

    # No unrelated path grants it: exactly these four assignment sites (the
    # conditional local-user gate plus the three authoritative sources above).
    assert settings_source.count("S.independentAsrAuthoritative = true;") == 4


def test_shared_write_metadata_carries_per_key_asr_authority():
    # Codex P2. meta.hydrated is the GLOBAL hydration bit, which any unrelated
    # user edit flips -- so a window whose boot GET never merged could stamp its
    # pre-merge boot ASR default as trustworthy, and a window that HAD merged
    # the server value would adopt it, mis-stamp its next handshake and POST the
    # wrong value back. The receiver needs the per-key fact instead.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    write_fn = settings_source.split("function _writeSharedSettings(", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "asrAuthoritative: S.independentAsrAuthoritative === true" in write_fn

    read_fn = settings_source.split("function _readSharedWriteMeta(", 1)[1].split(
        "\n    }", 1
    )[0]
    # Fail closed for snapshots written by the previous build.
    assert "asrAuthoritative: meta.asrAuthoritative === true" in read_fn

    # The stale guard consults the writer's per-key authority, not its global
    # hydration bit. The RECEIVER term stays S.settingsHydrated: tightening it
    # to the per-key latch breaks the unhydrated-writer scenario already pinned
    # by test_unrelated_save_from_unhydrated_window_is_not_an_asr_toggle_harness.
    assert (
        "(!asrWriteIsNewer || !asrOutranksLocalChoice\n"
        "                    || (!meta.asrAuthoritative && S.settingsHydrated === true))"
        in settings_source
    )


def test_cross_window_adopted_values_roll_the_dirty_baseline():
    # Without rolling the baseline, a value this window merely RECEIVED looks
    # like a local user edit on the next unrelated save: the key gets marked
    # dirty, that grants S.independentAsrAuthoritative, it rides out in
    # changedKeys as an explicit toggle other windows trust, and the pending
    # settings GET skips it as user-owned. That launders an adopted value into
    # user intent with no clock race at all.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    listener_block = settings_source.split(
        "window.addEventListener('storage', function (event) {", 1
    )[1].split("});", 1)[0]

    apply_index = listener_block.index("const changed = applySharedRuntimeSettings(incoming);")
    roll_index = listener_block.index("_settingsBaseline[key] = S[key];")
    assert apply_index < roll_index, "the baseline roll must observe the applied values"
    # Keys this window really did touch keep their authority.
    assert "if (_dirtySettingsKeys.has(key)) continue;" in listener_block


def test_equal_write_ids_are_broken_by_explicit_asr_intent():
    # Codex P2 follow-up. The applied-id floor in _nextSharedWriteId only rises
    # once this window has APPLIED another window's write, so two windows saving
    # in the same millisecond before either processes the other's storage event
    # still mint the same id. With a strict `>` freshness test the second write
    # reads as superseded and its ASR value is dropped -- and the value dropped
    # is a genuine, explicitly-marked toggle, not an incidental copy. Concurrent
    # writes have no clock order, so the tie is broken on intent instead, which
    # makes both delivery orders converge on the user's choice.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    listener_block = settings_source.split(
        "window.addEventListener('storage', function (event) {", 1
    )[1].split("});", 1)[0]

    assert (
        "|| (meta.writeId === _lastAppliedSharedWriteId && asrMarkedExplicit)"
        in listener_block
    )
    # A strictly OLDER write must still be refused.
    assert "meta.writeId > _lastAppliedSharedWriteId" in listener_block
    # The applied floor must advance only on a strict `>`, so a tie does not
    # consume the id and both tied writes stay eligible.
    assert "if (meta && meta.writeId > _lastAppliedSharedWriteId) {" in listener_block


def test_write_id_doc_does_not_claim_global_uniqueness():
    # The previous round's comments claimed the applied-id floor cured
    # same-millisecond minting across windows. It does not -- that is this
    # finding. A future reader must not be told otherwise by the comment they
    # hit first.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")
    id_fn = settings_source.split("function _nextSharedWriteId() {", 1)[1].split(
        "\n    }", 1
    )[0]
    assert "already OBSERVED" in id_fn
    assert "cannot be broken at mint" in id_fn
    assert "the listener resolves it on explicit intent" in id_fn


def test_asr_decision_tuple_survives_unrelated_saves():
    # Codex P2. _dirtySettingsKeys is monotone, so once a window has toggled ASR
    # every LATER unrelated save still lists independentAsrEnabled in
    # changedKeys -- and used to stamp it with that save's fresh writeId. A then
    # outranks a genuinely newer toggle from B, and the two windows swap. Unlike
    # the same-millisecond tie this follows up, it needs no race at all.
    settings_source = APP_SETTINGS_PATH.read_text(encoding="utf-8")

    # The write carries the id of the decision that produced the value...
    signature = re.search(
        r"function _writeSharedSettings\((?P<params>[^)]*)\)\s*\{",
        settings_source,
    )
    assert signature is not None, "_writeSharedSettings signature is missing"
    parameter_names = {
        parameter.strip() for parameter in signature.group("params").split(",")
    }
    assert {
        "snapshot",
        "explicitKeys",
        "pendingRecovery",
        "serverAuthoritativeKeys",
    } <= parameter_names
    write_fn = _block_after(settings_source, signature.group(0))
    assert "ownMeta.asrDecision = {" in write_fn
    assert "_lastAsrDecision.value === snapshot.independentAsrEnabled" in write_fn

    # ...the reader parses it defensively, falling back to today's behaviour...
    read_fn = _block_after(settings_source, "function _readSharedWriteMeta(settings) {")
    assert "asrDecision:" in read_fn
    assert "_isValidAsrWriteId(" in read_fn
    assert "meta.asrDecision.writeId," in read_fn
    assert "Number.isInteger(meta.serverRevision)" in read_fn

    # ...and both the boot seed and the adopted cross-window flip record the
    # ORIGINAL id, or this window re-inflates the value on its own next save.
    assert "const bootDecision = bootMeta.asrDecision || bootMeta;" in settings_source
    assert "const adopted = meta.asrDecision || meta;" in settings_source
