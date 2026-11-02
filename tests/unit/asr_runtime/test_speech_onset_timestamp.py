import inspect
import pytest

pytestmark = [pytest.mark.runtime]


@pytest.mark.unit
def test_speech_onset_is_stamped_at_the_transition_not_after_delivery() -> None:
    """The onset stamp must not sit behind an awaited lifecycle notification.

    Two production SPEECH_CONFIRMED paths stamp ``_asr_turn_audio_started_at``
    only after awaiting ``_send_asr_lifecycle_state()``. Visual ownership uses
    the onset as its lower bound, so a stamp taken after that await turns every
    frame captured during delivery into a "not this utterance" frame. The
    invariant is syntactic: the stamp follows the transition with no await in
    between.
    """
    import inspect

    from main_logic.asr_client import lifecycle as asr_lifecycle_module
    from main_logic.asr_client import runtime as asr_runtime_module

    source = inspect.getsource(asr_runtime_module).splitlines()

    # ⚠ This guard initially scanned only runtime.py's literal
    # `lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)`, missing
    # lifecycle.py's own `self.transition(...)` in begin_pending_turn entirely.
    # That omitted the fifth transition and falsely claimed coverage of all five. Inventory guards must prove
    # completeness: enumerate transition sites across modules before checking each one.
    lifecycle_source = inspect.getsource(asr_lifecycle_module).splitlines()
    lifecycle_sites = [
        index
        for index, line in enumerate(lifecycle_source)
        if "transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)" in line
    ]
    # Lifecycle-side sites cannot write runtime fields, so require instrumentation in their **callers**.
    for index in lifecycle_sites:
        owner = None
        for back in range(index, -1, -1):
            stripped = lifecycle_source[back].strip()
            if stripped.startswith("def "):
                owner = stripped[4:].split("(")[0]
                break
        assert owner is not None
        callers = [
            i for i, line in enumerate(source) if f"lifecycle.{owner}()" in line
        ]
        assert callers, (
            f"lifecycle.{owner}() performs a SPEECH_CONFIRMED transition but no "
            f"runtime call site was found to stamp the onset"
        )
        for caller in callers:
            window = chr(10).join(source[caller : caller + 12])
            assert "self._asr_turn_onset_at" in window, (
                f"runtime line {caller + 1}: lifecycle.{owner}() transitions to "
                f"SPEECH_CONFIRMED, so its caller must stamp the onset; got: "
                f"{window!r}"
            )
    transition = "lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)"
    stamp = "self._asr_turn_onset_at ="
    sites = [i for i, line in enumerate(source) if transition in line]

    assert sites, "no SPEECH_CONFIRMED transition found"
    for index in sites:
        # Assignment must begin **immediately after** the transition line (ignore comments/blank lines, which cannot introduce await).
        # Values may span multiple lines: several paths choose between the stored onset and function-entry time.
        first = next(
            offset
            for offset in range(1, 12)
            if source[index + offset].strip()
            and not source[index + offset].strip().startswith("#")
        )
        assert source[index + first].strip().startswith(stamp), (
            f"line {index + 1}: SPEECH_CONFIRMED must start stamping the onset "
            f"before anything else, got: {source[index + first].strip()!r}"
        )

    # Every path must **prefer the stored pending onset**, using function-entry time only
    # when absent. If a session becomes ready after being unready, the true speech start is the original
    # stored value; reading the clock now counts reconnection waits as after speech start, excluding every intervening frame.
    #
    # Apply the same rule to every transition site, eliminating heuristics for delayed paths;
    # the former upward scan for conditionals misclassified across functions and failed to prevent direct-branch degradation.
    for index in sites:
        begin = next(
            offset
            for offset in range(1, 12)
            if source[index + offset].strip()
            and not source[index + offset].strip().startswith("#")
        )
        statement = []
        depth = 0
        for offset in range(begin, begin + 9):
            line = source[index + offset]
            statement.append(line)
            depth += line.count("(") - line.count(")")
            if depth <= 0:
                break
        window = chr(10).join(statement)
        assert "self._asr_pending_speech_onset_at" in window, (
            f"line {index + 1}: the onset assignment must prefer the pending "
            f"onset captured before the reconnect, got: {window!r}"
        )

    # Capture detected_at before any await in the function.
    for index, line in enumerate(source):
        if line.strip() != "detected_at = time.monotonic()":
            continue
        for back in range(index, -1, -1):
            stripped = source[back].strip()
            if stripped.startswith(("async def ", "def ")):
                break
            if stripped.startswith("#"):
                continue
            assert not stripped.startswith("await ") and " await " not in stripped, (
                f"line {index + 1}: detected_at must be captured before any await; "
                f"line {back + 1} is {stripped!r}"
            )

    # Stored pending-turn onset also uses function-entry time. Entry already captured detected_at
    # (the preceding rule ensures before any await); rereading the clock in DRAINING excludes frames captured
    # between entry and this line from the utterance, yet this field later supplies
    # _asr_turn_onset_at in begin_pending_turn.
    for index, line in enumerate(source):
        stripped = line.strip()
        if not stripped.startswith("self._asr_pending_turn_onset_at = "):
            continue
        rhs = stripped.split(" = ", 1)[1]
        if rhs == "None":
            continue
        captures_detected_at = False
        for back in range(index, -1, -1):
            # Stop only at **method** definitions (four-space indentation). Nested defs such as
            # event_is_current / wake_is_current occur between detected_at and DRAINING;
            # stopping at any def ends too soon and disables both checks.
            if source[back].startswith(("    def ", "    async def ")):
                break
            if source[back].strip() == "detected_at = time.monotonic()":
                captures_detected_at = True
                break
        if not captures_detected_at:
            continue
        assert rhs == "detected_at", (
            f"line {index + 1}: the pending turn onset must carry the entry "
            f"timestamp its function already captured, got: {rhs!r}"
        )
