"""Guards left over from the QQ group ``recall_memory`` tool-call migration.

The tool-call recall channel itself (handler re-pointing, subject isolation,
consent records, revocation gates, tool-row cleanup, pre-tool text) lived in
the qq_auto_reply plugin, and its tests left this repository with the plugin.
What stays here only checks the main program:

- the retired mechanical recall filler phrase does not come back;
- ``omni_offline_client`` grows no "does this route support tools" gate.
"""

from __future__ import annotations

from pathlib import Path



def test_hardcoded_recall_filler_remains_removed():
    """The retired mechanical recall phrase must not return under another hook."""
    repo_root = Path(__file__).resolve().parents[2]
    runtime_paths = [repo_root / "config/prompts/prompts_memory.py"]
    runtime_paths.extend(sorted((repo_root / "main_logic/core").glob("*.py")))
    runtime_sources = "\n".join(
        path.read_text(encoding="utf-8") for path in runtime_paths
    )

    for retired_marker in (
        "RECALL_MEMORY_TOOL_FILLER",
        "_RECALL_FILLER_SID_SUFFIX",
        "::recall-filler",
        "让我回忆一下哦……",
    ):
        assert retired_marker not in runtime_sources


# ---------------------------------------------------------------------------
# Retired: the route capability gate (free proxy forwards tools now)
# ---------------------------------------------------------------------------


def test_offline_client_grows_no_route_capability_gate():
    """No "does this route support tools" predicate may come back.

    One existed while lanlan's free proxy silently dropped ``tools``: QQ
    consulted it and pushed those turns onto a build-time recall. The
    proxy forwards tools now and that fallback is deleted, so a predicate
    answering False would no longer mean "use the other channel" — it
    would mean the turn gets no memory at all, silently.
    """
    import main_logic.omni_offline_client as ooc

    assert [
        name for name in dir(ooc)
        if "supports_tool" in name or "free_route" in name
    ] == []
