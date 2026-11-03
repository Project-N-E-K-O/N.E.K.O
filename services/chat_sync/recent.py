"""Bound, retryable recent-memory exchange using the existing sync authority."""

from contextlib import closing
import json
from uuid import uuid4

from services.chat_sync.recent_protocol import canonical, changes, snapshot
from services.chat_sync.writer import store_connection


class RecentMemorySync:
    def __init__(self, path, adapter):
        self.path, self.adapter = path, adapter
        with closing(store_connection(path)) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS recent_memory_state "
                         "(domain TEXT PRIMARY KEY, base TEXT NOT NULL, pending TEXT, revision INTEGER NOT NULL DEFAULT 0)")
            conn.execute("CREATE TABLE IF NOT EXISTS recent_memory_preferences "
                         "(domain TEXT PRIMARY KEY, excluded TEXT NOT NULL DEFAULT '[]', preview TEXT)")

    def state(self, domain):
        with closing(store_connection(self.path)) as conn:
            row = conn.execute("SELECT base,pending,revision FROM recent_memory_state WHERE domain=?", (domain,)).fetchone()
            preference = conn.execute("SELECT excluded,preview FROM recent_memory_preferences WHERE domain=?", (domain,)).fetchone()
        return {"base": json.loads(row[0]) if row else [], "pending": json.loads(row[1]) if row and row[1] else None,
                "revision": row[2] if row else 0, "excluded": json.loads(preference[0]) if preference else [],
                "preview": json.loads(preference[1]) if preference and preference[1] else None}

    def exclude(self, domain, keys):
        shared = {row['id'] for row in self.state(domain)['base']}
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute("INSERT INTO recent_memory_preferences(domain,excluded) VALUES(?,?) ON CONFLICT(domain) "
                         "DO UPDATE SET excluded=EXCLUDED.excluded", (domain, canonical(sorted(set(keys) - shared)).decode()))

    def preview(self, domain, value):
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute("INSERT INTO recent_memory_preferences(domain,preview) VALUES(?,?) ON CONFLICT(domain) "
                         "DO UPDATE SET preview=EXCLUDED.preview", (domain, canonical(value).decode()))

    def visible(self, domain, local):
        excluded = set(self.state(domain)['excluded'])
        return [row for row in local if row['id'] not in excluded]

    def prepare(self, domain, change, local):
        pending = {"body": {"operationId": uuid4().hex, **change}, "local": local}
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute("INSERT INTO recent_memory_state(domain,base,pending) VALUES(?, '[]', ?) ON CONFLICT(domain) "
                         "DO UPDATE SET pending=EXCLUDED.pending", (domain, canonical(pending).decode()))
        return pending

    def acknowledge(self, domain, pending):
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute("UPDATE recent_memory_state SET base=?,pending=NULL WHERE domain=?",
                         (canonical(pending["local"]).decode(), domain))

    def apply(self, domain, name, remote, fingerprint, private, excluded_ids, can_apply):
        if not (can_apply() if callable(can_apply) else can_apply):
            return False
        if self.state(domain)['excluded'] != excluded_ids:
            return False
        excluded = set(excluded_ids)
        combined = [row for row in remote['entries'] if row['id'] not in excluded]
        for index, row in private:
            combined.insert(min(index, len(combined)), row)
        if not self.adapter.apply(name, combined, fingerprint):
            return False
        with closing(store_connection(self.path)) as conn, conn:
            conn.execute("INSERT INTO recent_memory_state VALUES(?,?,NULL,?) ON CONFLICT(domain) "
                         "DO UPDATE SET base=EXCLUDED.base,revision=EXCLUDED.revision",
                         (domain, canonical([row for row in remote['entries'] if row['id'] not in excluded]).decode(), remote["revision"]))
        return True

    async def sync(self, domain, name, request, commit, *, can_apply=True):
        """Freeze retries before HTTP and fence file application against local edits."""
        key = domain.key()
        path = f"/v1/chat-sync/characters/{domain.character_uid}/recent-memory"
        state = await commit(self.state, key)
        uploaded = removed = 0

        async def send(pending):
            receipt = await request("PUT", path, canonical(pending["body"]))
            if (not isinstance(receipt, dict) or set(receipt) != {"ownerId", "characterSyncId", "operationId", "revision"}
                    or receipt["ownerId"] != domain.owner_id or receipt["characterSyncId"] != domain.character_uid
                    or receipt["operationId"] != pending["body"]["operationId"]
                    or type(receipt["revision"]) is not int or not 1 <= receipt["revision"] <= 9_007_199_254_740_991):
                raise ValueError("invalid_recent_memory_receipt")
            await commit(self.acknowledge, key, pending)

        if state["pending"]:
            await send(state["pending"])
            state = await commit(self.state, key)
        local = await commit(self.adapter.read, name)
        projected = await commit(self.visible, key, local['entries'])
        change = changes(state["base"], projected)
        if change["upserts"] or change["removedIds"]:
            pending = await commit(self.prepare, key, change, projected)
            await send(pending)
            uploaded, removed = len(change["upserts"]), len(change["removedIds"])
        remote = snapshot(await request("GET", path, None), domain.owner_id, domain.character_uid)
        private = [(index, row) for index, row in enumerate(local['entries']) if row['id'] in state['excluded']]
        applied = await commit(self.apply, key, name, remote, local["fingerprint"], private, state['excluded'], can_apply)
        return {"uploaded": uploaded, "deletions": removed, "pullPages": 1, "hasMore": not applied,
                "changed": bool(applied and projected != [row for row in remote['entries'] if row['id'] not in state['excluded']])}
