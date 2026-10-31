"""Independent Live2D bindings. Resource installation precedes renderer-confirmed selection."""

from __future__ import annotations

import asyncio
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from services.chat_sync import host
from services.chat_sync.model_protocol import canonical, encode, web_url
from services.chat_sync.model_package import install_model, package_model
from services.chat_sync.writer import store_connection


class _PrivateModelLogFilter(logging.Filter):
    def filter(self, record):
        return "x-oss-signature=" not in record.getMessage()


class _ModelSelectionChanged(Exception):
    pass


class ModelStore:
    def __init__(self, path):
        self.path = Path(path)
        with closing(store_connection(path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS model_state(domain TEXT PRIMARY KEY,state TEXT NOT NULL)")

    def get(self, domain):
        with closing(store_connection(self.path)) as conn:
            row = conn.execute("SELECT state FROM model_state WHERE domain=?", (domain,)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, domain, state):
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute("INSERT OR REPLACE INTO model_state VALUES (?,?)", (domain, canonical(state).decode()))


class DesktopModelAdapter:
    def __init__(self, config):
        self.config = config

    async def read(self, uid):
        from utils.config_manager import get_reserved
        from utils.config_manager.reserved_schema import get_character_uid
        data = await self.config.aload_characters()
        for name, raw in data.get("猫娘", {}).items():
            if isinstance(raw, dict) and get_character_uid(raw) == uid:
                kind = get_reserved(raw, "avatar", "model_type", default="live2d")
                path = get_reserved(raw, "avatar", "live2d", "model_path", default="") if kind == "live2d" else ""
                selection = canonical([kind, path]).decode()
                return {"name": name, "kind": kind, "path": path, "selection": selection}
        raise ValueError("chat_sync_character_not_found")

    def source(self, path):
        return host.resolve_model_file(path, self.config)

    async def apply(self, uid, expected, path, commit):
        from utils.character_memory import character_config_mutation_lock
        from utils.config_manager import set_reserved
        from utils.config_manager.reserved_schema import get_character_uid
        async with character_config_mutation_lock:
            current = await self.read(uid)
            if current["selection"] != expected:
                return False
            data = await self.config.aload_characters()
            raw = data.get("猫娘", {}).get(current["name"])
            if not isinstance(raw, dict) or get_character_uid(raw) != uid:
                return False
            set_reserved(raw, "avatar", "model_type", "live2d")
            set_reserved(raw, "avatar", "live2d", "model_path", path)
            set_reserved(raw, "avatar", "asset_source", "local_imported")
            set_reserved(raw, "avatar", "asset_source_id", "")
            await commit(self.config.save_characters, data)
        await host.get_init_one_catgirl()(current["name"], is_new=False)
        return True


class DesktopModelSync:
    def __init__(self, runtime, path):
        self.runtime, self.store, self.adapter = runtime, ModelStore(path), None
        self.lock = asyncio.Lock()
        logger = logging.getLogger("httpx")
        if not any(isinstance(item, _PrivateModelLogFilter) for item in logger.filters):
            logger.addFilter(_PrivateModelLogFilter())

    async def _head(self, domain, request):
        value = await request("GET", f"/v1/chat-sync/characters/{domain.character_uid}/model-binding", None)
        if (not isinstance(value, dict) or value.get("ownerId") != domain.owner_id
                or value.get("characterSyncId") != domain.character_uid or type(value.get("revision")) is not int or value["revision"] < 0):
            raise ValueError("invalid_model_response")
        record = value.get("record")
        if record is None:
            if value["revision"] != 0 or value.get("recordHash") is not None:
                raise ValueError("invalid_model_response")
        elif value["revision"] <= 0 or hashlib.sha256(encode(record)).hexdigest() != value.get("recordHash"):
            raise ValueError("invalid_model_response")
        return value

    async def _supersede_pending(self, domain, request, commit, state):
        pending = state["pending"]
        latest = await self._head(domain, request)
        if latest["revision"] < max(state["revision"], state.get("seen_revision", 0)):
            raise ValueError("invalid_model_response")
        state["seen_revision"] = latest["revision"]
        if latest["revision"] > pending["baseRevision"]:
            # A lost acknowledgement may already have committed this pending record.
            # Any other cloud advance retains the existing explicit conflict policy.
            if (latest["revision"] == pending["baseRevision"] + 1
                    and latest["recordHash"] == hashlib.sha256(encode(pending["record"])).hexdigest()):
                state.update(revision=latest["revision"], selection=pending["selection"], remote=latest["record"])
            else:
                state["conflict"] = latest
        state.pop("pending")
        await commit(self.store.put, domain.key(), state)
        if not state.get("conflict"):
            await commit(self.runtime.scheduler.retry_model, domain.key())

    async def step(self, uid, *, manual=False):
        try:
            await self._step(uid, manual=manual)
        except Exception:
            # Persist a safe retry affordance without exposing signed URLs or provider errors.
            async with self.lock:
                try:
                    domain, _, commit = await self.runtime._bound_transport(uid)
                    state = await asyncio.to_thread(self.store.get, domain.key())
                    if state:
                        state["error"] = True
                        await commit(self.store.put, domain.key(), state)
                except Exception:
                    pass
            raise

    async def _step(self, uid, *, manual=False):
        if self.adapter is None:
            return
        async with self.lock:
            runtime, adapter = self.runtime, self.adapter
            authenticated = await runtime._session()
            domain, request, commit = await runtime._bound_transport(uid, authenticated=authenticated)
            binding = await asyncio.to_thread(runtime.state.binding, uid)
            async def guard():
                await runtime._guard(binding, authenticated[1])
            caps = await request("GET", "/v1/chat-sync/models/capabilities", None)
            if caps.get("schemaVersion") != 1 or caps.get("modelBinding") != 1:
                return
            current = await adapter.read(uid)
            head = await self._head(domain, request)
            key = domain.key()
            state = await asyncio.to_thread(self.store.get, key)
            had_state = state is not None
            if state is None:
                state = {"revision": 0, "selection": current["selection"] if head["record"] else ""}
                await commit(self.store.put, key, state)
            state.pop("error", None)
            if head["revision"] < max(state["revision"], state.get("seen_revision", 0)):
                raise ValueError("invalid_model_response")
            state["seen_revision"] = head["revision"]
            if state.get("conflict"):
                return
            candidate = state.get("candidate")
            if candidate and candidate["revision"] == head["revision"]:
                if encode(candidate["record"]) != encode(head["record"]):
                    raise ValueError("invalid_model_response")
                if current["selection"] == canonical(["live2d", candidate["path"]]).decode():
                    state.update(revision=head["revision"], selection=current["selection"], remote=head["record"])
                    state.pop("candidate", None); state.pop("failed", None)
                    await commit(self.store.put, key, state)
                    return
            if candidate and candidate["revision"] == head["revision"] and candidate["expected"] == current["selection"]:
                return
            dirty = state["selection"] != current["selection"]
            if head["revision"] > state["revision"] and dirty and (state["revision"] > 0 or had_state) and not state.get("pending"):
                state["conflict"] = head
                await commit(self.store.put, key, state)
                return
            if dirty and not state.get("pending") and not (head["revision"] > state["revision"] == 0):
                if current["kind"] != "live2d":
                    state["selection"] = current["selection"]
                    await commit(self.store.put, key, state)
                    return
                if current["path"]:
                    if not caps.get("resources"):
                        return
                    source = await asyncio.to_thread(adapter.source, current["path"])
                    if source is None:
                        raise ValueError("cloud_model_source_unavailable")
                    record, file = await asyncio.to_thread(package_model, source, self.store.path.parent / "model-packages")
                else:
                    record, file = {"schemaVersion": 1, "model": None}, None
                if (await adapter.read(uid))["selection"] != current["selection"]:
                    return
                state["pending"] = {"operationId": uuid4().hex, "baseRevision": state["revision"], "record": record,
                                    "selection": current["selection"], "file": file}
                state.pop("candidate", None)
                await commit(self.store.put, key, state)
            pending = state.get("pending")
            if pending:
                async def pending_guard():
                    await guard()
                    if (await adapter.read(uid))["selection"] != pending["selection"]:
                        raise _ModelSelectionChanged()
                try:
                    await pending_guard()
                    if pending["file"] and not pending.get("uploaded"):
                        await self.upload_file(request, f"/v1/chat-sync/characters/{uid}", pending["file"], guard=pending_guard)
                        pending["uploaded"] = True
                        await commit(self.store.put, key, state)
                    envelope = {field: pending[field] for field in ("operationId", "baseRevision", "record")}
                    await pending_guard()
                    receipt = await request("PUT", f"/v1/chat-sync/characters/{uid}/model-binding", canonical(envelope))
                except _ModelSelectionChanged:
                    await self._supersede_pending(domain, request, commit, state)
                    return
                except ValueError as exc:
                    if getattr(exc, 'media_resource_missing', False) and pending.get('file'):
                        pending['uploaded'] = False
                        await commit(self.store.put, key, state)
                        return
                    if getattr(exc, "status_code", None) == 409:
                        latest = await self._head(domain, request)
                        if latest["revision"] > pending["baseRevision"]:
                            state["conflict"] = latest
                            state["seen_revision"] = max(state.get("seen_revision", 0), latest["revision"])
                            await commit(self.store.put, key, state)
                            return
                    raise
                if (receipt.get("ownerId") != domain.owner_id or receipt.get("characterSyncId") != uid
                        or receipt.get("operationId") != pending["operationId"] or receipt.get("revision") != pending["baseRevision"] + 1
                        or receipt.get("recordHash") != hashlib.sha256(encode(pending["record"])).hexdigest()):
                    raise ValueError("invalid_model_response")
                state.update(revision=receipt["revision"], selection=pending["selection"], remote=pending["record"])
                state.pop("pending")
                await commit(self.store.put, key, state)
                if (await adapter.read(uid))["selection"] != pending["selection"]:
                    await commit(runtime.scheduler.retry_model, key)
                return
            if head["revision"] > state["revision"] and head["record"] and state.get("failed") != head["revision"]:
                record = head["record"]
                if record["model"] is None:
                    from config.character_defaults import DEFAULT_LIVE2D_MODEL_PATH
                    path = "/static/" + DEFAULT_LIVE2D_MODEL_PATH
                else:
                    model = record["model"]
                    archive = await self.download(domain, request, model["package"], guard)
                    destination = Path(adapter.config.live2d_dir) / "cloud-sync"
                    installed = await asyncio.to_thread(install_model, archive, record, destination)
                    readable = getattr(adapter.config, "readable_live2d_dir", None)
                    prefix = "/user_live2d_local" if readable and Path(readable).resolve() != Path(adapter.config.live2d_dir).resolve() else "/user_live2d"
                    path = prefix + "/cloud-sync/" + installed.relative_to(destination).as_posix()
                if (await self._head(domain, request))["revision"] != head["revision"]:
                    return
                state["candidate"] = {"token": uuid4().hex, "revision": head["revision"], "record": record,
                                      "expected": current["selection"], "path": path}
                await commit(self.store.put, key, state)
                if runtime.history_changed:
                    await runtime.history_changed(uid)

    async def download(self, domain, request, package, guard):
        import httpx
        target = self.store.path.parent / "model-packages" / (package["sha256"] + ".zip")
        if target.is_file() and target.stat().st_size == package["size"]:
            with target.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == package["sha256"]:
                    return target
        resource = package["resource"]
        if "url" in resource:
            url = web_url(resource["url"])
        else:
            response = await request("GET", f"/v1/chat-sync/characters/{domain.character_uid}/resources/{resource['resourceId']}/download", None)
            if (response.get("resourceId") != resource["resourceId"] or response.get("size") != package["size"]
                    or response.get("sha256") != package["sha256"] or response.get("contentType") != "application/zip"):
                raise ValueError("invalid_model_response")
            url = web_url(response["download"]["url"])
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix("." + uuid4().hex + ".part")
        digest, count = hashlib.sha256(), 0
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
                async with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise OSError("cloud_model_download_unavailable")
                    with part.open("xb") as output:
                        async for chunk in response.aiter_raw():
                            await guard()
                            count += len(chunk)
                            if count > package["size"]:
                                raise ValueError("invalid_cloud_model_hash")
                            digest.update(chunk)
                            output.write(chunk)
            if count != package["size"] or digest.hexdigest() != package["sha256"]:
                raise ValueError("invalid_cloud_model_hash")
            await guard()
            part.replace(target)
            return target
        finally:
            part.unlink(missing_ok=True)

    async def status(self, uid):
        runtime = self.runtime
        binding = await asyncio.to_thread(runtime.state.binding, uid)
        if not binding or not binding["enabled"] or self.adapter is None:
            return {}
        from services.chat_sync.ledger import ChatDomain
        state = await asyncio.to_thread(self.store.get, ChatDomain(binding["owner_id"], uid).key())
        if not state:
            return {}
        current = await self.adapter.read(uid)
        candidate = state.get("candidate")
        if candidate and candidate["expected"] == current["selection"]:
            return {"candidate": {"token": candidate["token"], "path": candidate["path"], "name": current["name"]}}
        return {"conflict": bool(state.get("conflict")), "failed": bool(state.get("failed") or state.get("error"))}

    async def finish(self, uid, token, loaded):
        async with self.lock:
            domain, request, commit = await self.runtime._bound_transport(uid)
            state = await asyncio.to_thread(self.store.get, domain.key())
            candidate = state.get("candidate") if state else None
            if not candidate or candidate["token"] != token or self.adapter is None:
                return False
            if not loaded:
                state["failed"] = candidate["revision"]
                state.pop("candidate")
                await commit(self.store.put, domain.key(), state)
                return False
            if (await self._head(domain, request))["revision"] != candidate["revision"]:
                return False
            if not await self.adapter.apply(uid, candidate["expected"], candidate["path"], commit):
                return False
            state.update(revision=candidate["revision"], selection=canonical(["live2d", candidate["path"]]).decode(), remote=candidate["record"])
            state.pop("candidate"); state.pop("failed", None)
            await commit(self.store.put, domain.key(), state)
            return True

    async def resolve(self, uid, action):
        async with self.lock:
            domain, _, commit = await self.runtime._bound_transport(uid)
            state = await asyncio.to_thread(self.store.get, domain.key())
            if not state or self.adapter is None:
                return
            if action == "retry":
                state.pop("failed", None)
                state.pop("error", None)
            elif state.get("conflict"):
                remote = state.pop("conflict")
                state.pop("pending", None); state.pop("candidate", None)
                state["revision"] = remote["revision"] if action == "local" else remote["revision"] - 1
                state["selection"] = "" if action == "local" else (await self.adapter.read(uid))["selection"]
            await commit(self.store.put, domain.key(), state)
            await commit(self.runtime.scheduler.retry_model, domain.key())

    async def upload_file(self, request, base, file, guard=None):
        import httpx
        metadata = {key: file[key] for key in ("resourceId", "purpose", "size", "sha256", "contentType")}
        if metadata["purpose"] != "model" or metadata["contentType"] != "application/zip":
            raise ValueError("invalid_model_resource")
        if guard:
            await guard()
        result = await request("POST", base + "/resources", canonical(metadata))
        if result.get("resourceId") != file["resourceId"] or type(result.get("ready")) is not bool:
            raise ValueError("invalid_cloud_model_response")
        if guard:
            await guard()
        if result["ready"]:
            return
        signed = result["upload"]
        if not web_url(signed["url"]).startswith("https://") or signed["method"] != "PUT" or any(
                key.lower() not in {"content-type", "content-length"} for key in signed["headers"]):
            raise ValueError("invalid_cloud_model_response")
        raw = await asyncio.to_thread(Path(file["path"]).read_bytes)
        if len(raw) != file["size"] or hashlib.sha256(raw).hexdigest() != file["sha256"]:
            raise ValueError("invalid_cloud_model_resource")
        async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
            async def chunks():
                for offset in range(0, len(raw), 65536):
                    if guard:
                        await guard()
                    yield raw[offset:offset + 65536]
            try:
                response = await client.put(signed["url"], headers=signed["headers"], content=chunks())
            except httpx.HTTPError:
                raise OSError("cloud_model_upload_unavailable") from None
            if response.status_code not in {200, 201, 204}:
                raise OSError("cloud_model_upload_unavailable")
        if guard:
            await guard()
        receipt = await request("POST", base + "/resources/" + file["resourceId"] + "/complete", None)
        if receipt.get("resourceId") != file["resourceId"] or receipt.get("ready") is not True:
            raise ValueError("invalid_cloud_model_response")
        if guard:
            await guard()
