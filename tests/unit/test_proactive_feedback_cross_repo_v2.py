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
            if tail_kind == "ended":
                await _assert_actual_device_replacement_cas(runtime, client, origin, headers, tmp_path)
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


async def _assert_actual_device_replacement_cas(runtime, client, origin, headers, root):
    """A second real desktop preserves a viewed version through the real peer."""
    bindings = []
    installations = []
    for _ in range(4):
        installation = str(uuid.uuid4())
        context = (await client.get(origin + "/api/proactive/feedback/v2/devices", headers=headers)).json()["activation_context"]
        response = await client.post(origin + "/api/proactive/feedback/v2/devices/activate",
                                     headers=headers, json={"installation_id": installation, "activation_context": context})
        assert response.status_code == 200, response.text
        bindings.append(response.json()["binding"])
        installations.append(installation)
    second = ProactiveFeedbackRuntimeV2(RecommendationStore(root / "second-desktop.sqlite3", protocol_version=2),
        snapshot_provider=runtime.snapshot_provider, session_provider=runtime.session_provider, client=client,
        attribution_context=runtime.attribution_context, fenced_snapshot_provider=runtime.fenced_snapshot_provider,
        trusted_origin=origin)
    try:
        viewed = await second.list_devices()
        assert sum(row["active"] for row in viewed["devices"]) == 5
        chosen = bindings[0]
        response = await client.post(origin + f"/api/proactive/feedback/v2/devices/{chosen['registration_id']}/remove",
                                     headers=headers, json={"expected_registration_version": 1})
        assert response.status_code == 200, response.text
        context = (await client.get(origin + "/api/proactive/feedback/v2/devices", headers=headers)).json()["activation_context"]
        response = await client.post(origin + "/api/proactive/feedback/v2/devices/activate", headers=headers,
                                     json={"installation_id": installations[0], "activation_context": context})
        assert response.status_code == 200, response.text
        assert response.json()["binding"]["registration_version"] == 3
        from services.proactive_feedback.runtime import SyncFailure
        with pytest.raises(SyncFailure, match="cloud_conflict"):
            await second.activate_device(chosen["registration_id"], expected_owner_key=viewed["owner_key"],
                                         expected_replace_registration_version=1,
                                         activation_context=viewed["activation_context"])
        unchanged = await second.list_devices()
        target = next(row for row in unchanged["devices"] if row["registration_id"] == chosen["registration_id"])
        assert target["active"] and target["registration_version"] == 3
        assert len(unchanged["devices"]) == 5
        first = await second.activate_device(chosen["registration_id"], expected_owner_key=viewed["owner_key"],
                                            expected_replace_registration_version=3,
                                            activation_context=unchanged["activation_context"])
        state = await second.list_devices()
        assert sum(row["active"] for row in state["devices"]) == 5
        assert not next(row for row in state["devices"] if row["registration_id"] == chosen["registration_id"])["active"]
        retry = await second.activate_device(chosen["registration_id"], expected_owner_key=viewed["owner_key"],
                                            expected_replace_registration_version=3,
                                            activation_context=unchanged["activation_context"])
        assert retry == first
        assert (await second.list_devices())["devices"] == state["devices"]
        current = first["binding"]
        await second.remove_device(current["registration_id"], current["registration_version"], viewed["owner_key"])
        with pytest.raises(SyncFailure, match="cloud_conflict"):
            await second.activate_device(chosen["registration_id"], expected_owner_key=viewed["owner_key"],
                                         expected_replace_registration_version=3,
                                         activation_context=unchanged["activation_context"])
        removed = await second.list_devices()
        assert not next(row for row in removed["devices"] if row["is_current"])["active"]
        rejoined = await second.activate_device(expected_owner_key=removed["owner_key"],
                                               activation_context=removed["activation_context"])
        assert rejoined["binding"]["registration_version"] == 3
    finally:
        await second.close()
