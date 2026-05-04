"""Diagnose why galgame plugin context isn't being pushed to the cat girl.

Usage: .venv/Scripts/python.exe scripts/diagnose_push.py

Reads the shared state from the running plugin process by importing the plugin
module and calling galgame_get_status directly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "plugin"))


def main() -> None:
    import urllib.request
    import urllib.error

    ports_to_try = [48911, 48912, 48915, 48916]
    for port in ports_to_try:
        url = f"http://localhost:{port}/runs"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps({
                    "plugin_id": "galgame_plugin",
                    "entry_id": "galgame_get_status",
                    "args": {},
                }).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
                run_id = result.get("run_id", "")
                if run_id:
                    print(f"Created run on port {port}: {run_id}")
                    import time
                    for _ in range(20):
                        time.sleep(0.5)
                        poll_url = f"http://localhost:{port}/runs/{run_id}"
                        try:
                            with urllib.request.urlopen(poll_url, timeout=5) as pr:
                                run_data = json.loads(pr.read())
                                status = run_data.get("status")
                                if status == "succeeded":
                                    export_url = f"http://localhost:{port}/runs/{run_id}/export"
                                    with urllib.request.urlopen(export_url, timeout=5) as er:
                                        exports = json.loads(er.read())
                                        if exports:
                                            data = json.loads(exports[0].get("data", "{}"))
                                            print_diagnostics(data)
                                        else:
                                            print("No export data")
                                    return
                                elif status in ("failed", "timeout"):
                                    print(f"Run failed: {run_data}")
                                    return
                        except Exception as e:
                            pass
                    print("Timeout waiting for result")
                    return
        except urllib.error.HTTPError as e:
            if e.code == 405:
                continue
            print(f"HTTP error on port {port}: {e.code} {e.reason}")
        except Exception:
            pass

    print("Could not reach N.E.K.O server on any known port.")
    print("Is N.E.K.O running? Check with: netstat -ano | grep LISTEN | grep 4891")


def print_diagnostics(data: dict) -> None:
    print("\n" + "=" * 60)
    print("  GALGAME PUSH DIAGNOSTICS")
    print("=" * 60)

    print("\n--- Settings ---")
    print(f"  push_notifications:  {data.get('push_notifications')}")
    print(f"  push_policy:         {data.get('push_policy')}")
    print(f"  mode:                {data.get('mode')}")
    print(f"  actionable:          {data.get('actionable')}")
    print(f"  status:              {data.get('status')}")
    print(f"  agent_user_status:   {data.get('agent_user_status')}")

    print("\n--- Session ---")
    session_id = str(data.get("session_id", ""))
    print(f"  session_id:          {session_id[:30]}{'...' if len(session_id) > 30 else ''}")
    print(f"  scene_id:            {data.get('scene_id')}")
    print(f"  route_id:            {data.get('route_id')}")
    print(f"  line_id:             {data.get('line_id')}")
    print(f"  input_source:        {data.get('input_source')}")
    print(f"  standby_requested:   {data.get('standby_requested')}")

    print("\n--- Line Counting ---")
    print(f"  interval:            {data.get('scene_summary_line_interval')}")
    print(f"  lines_since_push:    {data.get('scene_summary_lines_since_push')}")
    print(f"  lines_until_push:    {data.get('scene_summary_lines_until_push')}")

    print("\n--- Recent Pushes ---")
    pushes = data.get("recent_pushes", [])
    print(f"  total:               {len(pushes)}")
    if data.get("last_push"):
        lp = data["last_push"]
        print(f"  last_push_kind:      {lp.get('kind')}")
        print(f"  last_push_status:    {lp.get('status')}")
        print(f"  last_push_scene:     {lp.get('scene_id')}")
        print(f"  last_push_ts:        {lp.get('ts')}")
    else:
        print("  last_push:           (none)")

    agent = data.get("agent", {})
    debug = agent.get("debug", {}) if isinstance(agent, dict) else {}
    summary_debug = debug.get("summary", {}) if isinstance(debug, dict) else {}

    print("\n--- Summary Debug ---")
    if summary_debug.get("last_drop"):
        ld = summary_debug["last_drop"]
        print(f"  *** LAST DROP ***")
        print(f"  reason:              {ld.get('reason')}")
        print(f"  scene_id:            {ld.get('scene_id')}")
        print(f"  trigger:             {ld.get('trigger')}")
    else:
        print(f"  last_drop:           (none)")

    print(f"  last_processed_seq:  {summary_debug.get('last_processed_event_seq')}")

    scene_states = summary_debug.get("scene_states", {})
    print(f"\n--- Scene States ({len(scene_states)} scenes) ---")
    if not scene_states:
        print("  (no scenes tracked)")
    else:
        for sid, st in scene_states.items():
            lsp = st.get("lines_since_push", 0)
            seen = st.get("seen_line_keys", [])
            seen_count = len(seen) if isinstance(seen, (list, set)) else 0
            marker = " *** READY TO PUSH" if isinstance(lsp, int) and lsp >= 8 else ""
            print(f"  {sid}:")
            print(f"    lines_since_push:  {lsp}{marker}")
            print(f"    seen_keys:         {seen_count}")
            print(f"    last_seq:          {st.get('last_line_seq')}")

    print("\n" + "=" * 60)
    print("  DIAGNOSIS")
    print("=" * 60)

    issues = []

    if not data.get("push_notifications"):
        issues.append("push_notifications is DISABLED - no pushes will happen")

    policy = data.get("push_policy", "")
    if policy == "disabled":
        mode = data.get("mode", "")
        if mode == "silent":
            issues.append(f"mode is 'silent' - push policy disabled")
        else:
            issues.append(f"push_policy is 'disabled' (mode={mode})")

    if not data.get("actionable"):
        issues.append("agent is NOT actionable (connection_state != 'active' or no snapshot)")

    if data.get("standby_requested"):
        issues.append("agent is in STANDBY (user paused)")

    if data.get("status") == "error":
        issues.append(f"agent is in ERROR state: {data.get('agent_error', 'unknown')}")

    if not session_id:
        issues.append("no active session (game not connected)")

    if scene_states:
        total_lines = sum(
            st.get("lines_since_push", 0)
            for st in scene_states.values()
            if isinstance(st, dict)
        )
        max_scene_lines = max(
            (st.get("lines_since_push", 0) for st in scene_states.values() if isinstance(st, dict)),
            default=0,
        )
        if total_lines >= 8 and max_scene_lines < 8:
            issues.append(
                f"SCENE FRAGMENTATION: {total_lines} total lines across "
                f"{len(scene_states)} scenes, but max per-scene is {max_scene_lines} "
                f"(need 8). Lines are being split across multiple scene_ids."
            )
    elif session_id:
        issues.append("no scene states tracked (no lines counted yet)")

    if summary_debug.get("last_drop"):
        ld = summary_debug["last_drop"]
        issues.append(f"last push was DROPPED: reason={ld.get('reason')}, scene={ld.get('scene_id')}")

    if not issues:
        print("  No obvious issues found.")
        print("  If pushes still aren't working, check:")
        print("  - Is the cat girl (N.E.K.O main) actually receiving push_message?")
        print("  - Is the LLM backend reachable (check API key/URL)?")
    else:
        for i, issue in enumerate(issues, 1):
            print(f"  {i}. {issue}")


if __name__ == "__main__":
    main()
