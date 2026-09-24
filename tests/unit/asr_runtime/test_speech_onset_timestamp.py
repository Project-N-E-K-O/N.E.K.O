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

    # ⚠️ 这个守卫的第一版只扫 runtime.py 里的字面量
    # `lifecycle.transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)`，因此完全看不见
    # lifecycle.py 自己的 `self.transition(...)`（begin_pending_turn 里那一处）——
    # 第五个迁移点就是这么漏掉的，还给了"五处都打点了"的假绿。清单式守卫必须自己
    # 证明清单是全的：先跨模块把所有迁移点数出来，再逐个查。
    lifecycle_source = inspect.getsource(asr_lifecycle_module).splitlines()
    lifecycle_sites = [
        index
        for index, line in enumerate(lifecycle_source)
        if "transition(VoiceLifecycleEvent.SPEECH_CONFIRMED)" in line
    ]
    # lifecycle 侧的迁移点没有 runtime 字段可写，只能要求它的**调用方**补打点。
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
        # 赋值必须**紧接**转换那一行开始（注释和空行不算，它们引入不了 await）。
        # 值本身可以是多行表达式：几条路径都要在"暂存的 onset"和"进函数时刻"之间选。
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

    # 每一条路径的 onset 赋值都必须**优先取暂存的 pending onset**，只有它为空时才
    # 用进函数时刻。session 先未就绪、随后又 ready 时，真实开口时刻就是当初记下的
    # 那个值；就地取时钟会把整段重连等待算成「开口之后」，期间拍的帧全被排除。
    #
    # 规则对所有迁移点一视同仁，因此不再需要"哪条是延迟路径"这种启发式识别 ——
    # 之前那版靠往上扫若干行找条件语句，既会跨函数误标，也挡不住直接分支退化。
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

    # detected_at 本身必须在函数里任何 await 之前捕获。
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

    # 暂存的 pending turn onset 也必须用进函数时刻。函数入口已经存了 detected_at
    # （上面那条规则保证它在任何 await 之前），DRAINING 分支再读一次时钟等于把
    # 「进函数 → 走到这一行」之间拍的帧排除在这段发声之外，而这个字段正是后面
    # begin_pending_turn 那处 _asr_turn_onset_at 的来源。
    for index, line in enumerate(source):
        stripped = line.strip()
        if not stripped.startswith("self._asr_pending_turn_onset_at = "):
            continue
        rhs = stripped.split(" = ", 1)[1]
        if rhs == "None":
            continue
        captures_detected_at = False
        for back in range(index, -1, -1):
            # 只在**方法**定义处收边（4 空格缩进）。这些函数里 detected_at 与
            # DRAINING 分支之间隔着 event_is_current / wake_is_current 这类嵌套
            # def，按 "任意 def" 收边会提前停下，规则对这两处直接失效。
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
