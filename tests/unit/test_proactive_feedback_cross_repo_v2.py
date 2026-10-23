"""Opt-in real desktop Store/runtime -> loopback synthetic server integration.

Set NEKO_FEEDBACK_V2_SERVER_ROOT to a checkout containing the v2 peer fixture.
NEKO_FEEDBACK_V2_SERVER_ENV selects an already prepared server uv environment.
No real credentials, user data, or production services are involved.
"""
import asyncio
import copy
from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import time
import uuid

import httpx
import pytest

from main_logic.recommendation_feedback import RecommendationStore
from services.proactive_feedback import host
from services.proactive_feedback.runtime_v2 import ProactiveFeedbackRuntimeV2


@pytest.mark.asyncio
@pytest.mark.parametrize("tail_kind", ["playing", "paused", "ended", "stopped", "failed"])
async def test_actual_store_runtime_signed_backend_statistics_roundtrip(tmp_path, tail_kind):
    configured = os.environ.get("NEKO_FEEDBACK_V2_SERVER_ROOT")
    if not configured:
        pytest.skip("optional companion server checkout is not configured")
    server_root = Path(configured).resolve(strict=True)
    fixture = server_root / "tests/fixtures/proactive_feedback_v2_peer.py"
    assert fixture.is_file()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    user_id = str(uuid.uuid4())
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(server_root)
    if environment.get("NEKO_FEEDBACK_V2_SERVER_ENV"):
        environment["UV_PROJECT_ENVIRONMENT"] = environment["NEKO_FEEDBACK_V2_SERVER_ENV"]
    snapshot = {"base_url": origin, "local_user_id": user_id, "access_token": "synthetic-integration-token"}
    owner = host.owner_from_snapshot(snapshot)

    @contextmanager
    def attribution():
        yield owner

    async def resolve():
        return {"logged_in": True, "snapshot": dict(snapshot)}

    store = RecommendationStore(tmp_path / "desktop.sqlite3", protocol_version=2)
    async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
        runtime = ProactiveFeedbackRuntimeV2(store, snapshot_provider=lambda: dict(snapshot),
            session_provider=resolve, client=client, attribution_context=attribution,
            fenced_snapshot_provider=lambda: dict(snapshot), trusted_origin=origin)
        log = (tmp_path / "synthetic-server.log").open("wb")
        process = await asyncio.to_thread(subprocess.Popen, ["uv", "run", "--project", str(server_root), "--no-sync", "--offline",
            "--python", "3.12", "python", str(fixture), "--synthetic-root", str(tmp_path / "server"),
            "--user", user_id, "--port", str(port)], cwd=server_root, env=environment,
            stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    pytest.fail("synthetic peer exited: " + (tmp_path / "synthetic-server.log").read_text())
                try:
                    response = await client.get(origin + "/_fixture_ready")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert time.monotonic() < deadline, "synthetic peer startup timed out"
                await asyncio.sleep(0.1)
            assert (await runtime.sync_once())["state"] == "device_activation_required"
            await runtime.activate_device()
            context = runtime.control_snapshot(owner)
            assert context is not None
            card = store.create(owner, "Fixture", "fixture-turn", {
                "name": "Fixture", "artist": "Fixture", "url": "https://media.example/fixture"}, {}, control=context)
            event = {"event_id": str(uuid.uuid4()), "kind": "shown", "occurred_at": time.time(),
                     "context": runtime.control_snapshot(owner), "offer_context": card["offer_context"]}
            assert store.record(owner, card["id"], event, control=context)["telemetry"]["status"] == "durable"
            await asyncio.sleep(0.12)  # Permit the test uncertainty interval to precede server receipt.
            result = await runtime.sync_once()
            assert result["state"] == "synced", result
            local = store.sync_status(owner)
            assert local["pending"] == 0 and local["held"] == 0, local
            headers = {"Authorization": "Bearer synthetic-integration-token"}
            response = await client.get(origin + "/api/proactive/feedback/v2/statistics", headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["categories"] == [dict(category="music", metric="counts_v2",
                recommended=1, accepted=0, skipped=0, playback_ms=0)]
            # A lost local reply / repeated handoff does not add a cloud contribution.
            assert not store.record(owner, card["id"], event, control=context)["applied"]
            assert (await runtime.sync_once())["state"] == "idle"
            again = await client.get(origin + "/api/proactive/feedback/v2/statistics", headers=headers)
            assert again.json()["categories"] == response.json()["categories"]
            accepted = {**event, "event_id": str(uuid.uuid4()), "kind": "accepted",
                        "context": runtime.control_snapshot(owner)}
            assert store.record(owner, card["id"], accepted, control=context)["playback_grant"]
            epoch_context = copy.deepcopy(runtime.control_snapshot(owner))
            epoch_id = str(uuid.uuid4())
            for start, end in ((0, 20), (20, 40)):
                await asyncio.sleep(0.04)
                measurement = {**event, "event_id": str(uuid.uuid4()), "kind": "measurement",
                    "context": runtime.control_snapshot(owner), "playback_id": "synthetic-playback",
                    "measurement": {"version": "playback_elapsed_observed_v2", "epoch_id": epoch_id,
                        "range_start_ms": start, "range_end_ms": end, "origin_context": epoch_context}}
                assert store.record(owner, card["id"], measurement, control=context)["telemetry"]["status"] == "durable"
            await asyncio.sleep(0.12)
            assert (await runtime.sync_once())["state"] == "synced"
            response = await client.get(origin + "/api/proactive/feedback/v2/statistics", headers=headers)
            assert response.json()["categories"] == [
                dict(category="music", metric="counts_v2", recommended=1, accepted=1, skipped=0, playback_ms=0),
                dict(category="music", metric="playback_elapsed_observed_v2", recommended=0, accepted=0, skipped=0, playback_ms=40)]
            assert store.sync_status(owner)["held"] == 0
            # Real player callbacks attach the last short range to the normal
            # playing/pause/terminal event, rather than a checkpoint-only fact.
            await asyncio.sleep(0.04)
            tail = {**event, "event_id": str(uuid.uuid4()), "kind": tail_kind,
                    "context": runtime.control_snapshot(owner), "playback_id": "synthetic-playback",
                    "measurement": {"version": "playback_elapsed_observed_v2", "epoch_id": epoch_id,
                        "range_start_ms": 40, "range_end_ms": 60, "origin_context": epoch_context}}
            assert store.record(owner, card["id"], tail, control=context)["telemetry"]["status"] == "durable"
            await asyncio.sleep(0.12)
            result = await runtime.sync_once()
            assert result["state"] == "synced", result
            response = await client.get(origin + "/api/proactive/feedback/v2/statistics", headers=headers)
            metrics = {row["metric"]: row for row in response.json()["categories"]}
            assert metrics["playback_elapsed_observed_v2"]["playback_ms"] == 60
            assert store.sync_status(owner)["pending"] == 0
        finally:
            try:
                await client.post(origin + "/_fixture_stop", headers={"Authorization": "Bearer synthetic-integration-token"})
            except httpx.HTTPError:
                pass
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                process.terminate()
                await asyncio.to_thread(process.wait, 5)
            log.close()
