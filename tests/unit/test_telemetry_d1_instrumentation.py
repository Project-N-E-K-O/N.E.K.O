"""D1 churn diagnostic instrumentation: user_message_sent counter + session_turn_count histogram.

Verify the wiring for token_tracker.note_user_message (each message) and _atexit_save's
session_turn_count (including sessions with no messages). The instrumentation SDK itself
is covered by tests/test_instrument.py; this verifies only token_tracker wiring.
"""
import types
from pathlib import Path

_SRV_DIR = Path(__file__).resolve().parents[2] / "local_server" / "telemetry_server"


def _make_tracker(tmp_path, monkeypatch):
    """Create an independent TokenTracker (patch config_manager to a temporary directory; do not touch the real disk)."""
    import utils.token_tracker as tk
    fake_cm = types.SimpleNamespace(config_dir=tmp_path)
    monkeypatch.setattr(tk, "get_config_manager", lambda: fake_cm)
    tt = tk.TokenTracker()
    monkeypatch.setattr(tt, "save", lambda *a, **k: None)  # _atexit_save does not make network calls
    return tt


def _snapshot():
    import utils.instrument as inst
    return inst.snapshot()


def test_user_message_counter_split_by_input_type(tmp_path, monkeypatch):
    import utils.instrument as inst
    tt = _make_tracker(tmp_path, monkeypatch)
    inst.snapshot()  # Leftovers from before drain

    tt.record_app_start("main_server")  # Reset _session_msg_count = 0
    tt.note_user_message("text")
    tt.note_user_message("voice")
    tt.note_user_message("text")

    assert tt._session_msg_count == 3
    counters = _snapshot().get("counters", {})
    # Split by input_type: 2 text turns and 1 voice turn. Sum = 3 total turns.
    assert counters.get("user_message_sent|input_type=text") == 2
    assert counters.get("user_message_sent|input_type=voice") == 1


def test_session_turn_count_emitted_on_session_end(tmp_path, monkeypatch):
    import utils.instrument as inst
    tt = _make_tracker(tmp_path, monkeypatch)
    inst.snapshot()

    tt.record_app_start("main_server")
    tt.note_user_message("text")
    tt.note_user_message("text")
    tt._atexit_save()  # session_end → emit session_turn_count (value 2)

    hists = _snapshot().get("histograms", {})
    stc = [v for k, v in hists.items() if k.startswith("session_turn_count")]
    assert stc, "session_end 未 emit session_turn_count"
    assert stc[0]["count"] == 1  # One event per session_end
    assert stc[0]["sum"] == 2    # 2 turns in this session


def test_zero_message_session_still_emits_turn_count(tmp_path, monkeypatch):
    """A session with no messages (app opened and closed without chatting) must still emit session_turn_count=0.

    This is the most direct D1 churn signal; do not skip it just because there were \"no messages.\"
    """
    import utils.instrument as inst
    tt = _make_tracker(tmp_path, monkeypatch)
    inst.snapshot()

    tt.record_app_start("main_server")
    # Send no messages
    tt._atexit_save()

    snap = _snapshot()
    hists = snap.get("histograms", {})
    stc = [v for k, v in hists.items() if k.startswith("session_turn_count")]
    assert stc and stc[0]["count"] == 1, "零消息会话漏 emit session_turn_count"
    assert stc[0]["sum"] == 0  # 0 turns
    # No user_message_sent events
    assert not any(k.startswith("user_message_sent") for k in snap.get("counters", {}))


def test_session_turn_count_resets_between_sessions(tmp_path, monkeypatch):
    """record_app_start must reset the turn count to prevent accumulation across sessions."""
    tt = _make_tracker(tmp_path, monkeypatch)
    tt.record_app_start("main_server")
    tt.note_user_message("text")
    tt.note_user_message("text")
    assert tt._session_msg_count == 2
    # Simulate a new session (same process; record_app_start has a one-time lock, so set the flag directly to test reset semantics)
    tt._has_recorded_app_start = False
    tt.record_app_start("main_server")
    assert tt._session_msg_count == 0


def test_has_completed_core_loop(tmp_path, monkeypatch):
    """has_completed_core_loop provides the before_first_loop dimension for error instrumentation."""
    tt = _make_tracker(tmp_path, monkeypatch)
    assert tt.has_completed_core_loop() is False
    tt._first_user_message_recorded = True  # core_loop precondition: the user has already spoken
    tt.note_core_loop_completed()
    assert tt.has_completed_core_loop() is True


def test_device_hw_format_low_cardinality():
    """device_hw is a four-part low-cardinality enum string (os|arch|ram|cpu); it must never contain raw values."""
    import utils.token_tracker as tk
    hw = tk._get_device_hw()
    parts = hw.split("|")
    assert len(parts) == 4, f"device_hw 应为 4 段 os|arch|ram|cpu，实得 {hw!r}"
    os_tag, arch, ram, cpu = parts
    assert os_tag in ("win", "mac", "linux", "other")
    assert arch in ("x86_64", "arm64", "other")
    assert ram in ("lt8", "8to16", "16to32", "ge32", "unknown")
    assert cpu in ("le4", "5to8", "9to16", "gt16", "unknown")


def _make_storage(tmp_path):
    import sys
    srv = _SRV_DIR
    if str(srv) not in sys.path:
        sys.path.insert(0, str(srv))
    from storage import TelemetryStorage
    return TelemetryStorage(tmp_path / "t.db")


def test_normalize_device_hw_whitelist(tmp_path):
    """Server-boundary allowlist: accept valid four-part values; map forged, out-of-range, or wrong-length values to ''."""
    import sys
    if str(_SRV_DIR) not in sys.path:
        sys.path.insert(0, str(_SRV_DIR))
    from storage import normalize_device_hw
    # Valid
    assert normalize_device_hw("win|x86_64|16to32|9to16") == "win|x86_64|16to32|9to16"
    assert normalize_device_hw("mac|arm64|ge32|le4") == "mac|arm64|ge32|le4"
    # Invalid: wrong number of segments (5-part legacy format) / out-of-range tag / arbitrary string / non-str / empty
    assert normalize_device_hw("win|x86_64|16to32|avx2|9to16") == ""   # 5 segments
    assert normalize_device_hw("win|x86_64|99GB|9to16") == ""          # ram out of range
    assert normalize_device_hw("evil; DROP TABLE; or PII username") == ""
    assert normalize_device_hw("a" * 64) == ""
    assert normalize_device_hw(None) == ""
    assert normalize_device_hw("") == ""


# device_hw server preserve-known: an empty string must not overwrite a previously known value
def test_device_hw_preserve_known(tmp_path):
    import json
    st = _make_storage(tmp_path)
    dev = "deviceHWXXXXXXXXX"
    st.store_event(dev, "1.0", json.dumps({"device_id": dev}), {}, device_hw="win|x86_64|16to32|9to16")
    # A later report with empty device_hw (for example, detection failure) must not overwrite a known value
    st.store_event(dev, "1.0", json.dumps({"device_id": dev}), {}, device_hw="")
    row = st._get_conn().execute(
        "SELECT device_hw FROM devices WHERE device_id=?", (dev,)
    ).fetchone()
    assert row["device_hw"] == "win|x86_64|16to32|9to16"
    # A new non-empty value overwrites normally
    st.store_event(dev, "1.0", json.dumps({"device_id": dev}), {}, device_hw="mac|arm64|ge32|5to8")
    row = st._get_conn().execute(
        "SELECT device_hw FROM devices WHERE device_id=?", (dev,)
    ).fetchone()
    assert row["device_hw"] == "mac|arm64|ge32|5to8"


def test_device_hw_degraded_does_not_overwrite(tmp_path):
    """Hardware profile is static: a degraded profile with more unknown segments must not overwrite a more complete known profile."""
    import json
    st = _make_storage(tmp_path)
    dev = "deviceHWDEGXXXXXX"
    # Known complete profile (0 unknowns)
    st.store_event(dev, "1.0", json.dumps({"device_id": dev}), {}, device_hw="win|x86_64|16to32|9to16")
    # Degraded profile (2 unknowns due to temporary detection failure) must not overwrite it
    st.store_event(dev, "1.0", json.dumps({"device_id": dev}), {}, device_hw="win|x86_64|unknown|unknown")
    row = st._get_conn().execute("SELECT device_hw FROM devices WHERE device_id=?", (dev,)).fetchone()
    assert row["device_hw"] == "win|x86_64|16to32|9to16", "退化 profile 覆盖了已知完整 profile"
    # Detection recovers (fewer unknowns): overwrite normally
    st.store_event(dev, "1.0", json.dumps({"device_id": dev}), {}, device_hw="win|x86_64|ge32|gt16")
    row = st._get_conn().execute("SELECT device_hw FROM devices WHERE device_id=?", (dev,)).fetchone()
    assert row["device_hw"] == "win|x86_64|ge32|gt16"
