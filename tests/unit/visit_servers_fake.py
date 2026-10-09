# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Fake N.E.K.O. Servers for the visit data endpoints (``httpx.MockTransport`` handler, §4.7)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx

BASE = "https://servers.test"


class FakeServers:
    """``transcripts`` / ``reports`` / ``history`` / ``details`` of Servers (§4.7)."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.limit = 1024 * 1024
        self.transcript_mode = "ok"         # ok | 503 | 429 | budget | parts | not_participant | unknown_visit | not_started | not_started_final
        self.report_mode = "ok"             # ok | 503 | 429 | network | 404
        self.fail_parts: set[int] = set()   # 这些块回 503（模拟中途断）
        self.groups: dict[tuple[str, str], dict] = {}
        self.complete: dict[tuple[str, str], list] = {}
        self.reports: list[dict] = []
        self.transcript_seen_at_report: list[bool] = []
        self.history_items: list[dict] = []
        self.history_mode = "ok"            # ok | 401 | 503
        self.details_mode = "ok"            # ok | 401 | 403 | 404 | 503
        self.details_lines: list[dict] = []
        self.details_pages_extra = 0        # 在真实页之后再多给几页 next_cursor（模拟永远翻不完）
        self.details_fail_page: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/visit/transcripts":
            return self._transcripts(request)
        if request.url.path == "/api/visit/reports":
            return self._reports(request)
        if request.url.path == "/api/visit/history":
            return self._history(request)
        if request.url.path.startswith("/api/visit/details/"):
            return self._details(request)
        return httpx.Response(404, json={"code": "nope"})

    def _history(self, request: httpx.Request) -> httpx.Response:
        if self.history_mode == "401":
            return httpx.Response(401, json={"code": "unauthenticated"})
        if self.history_mode == "503":
            return httpx.Response(503)
        out: dict = {"items": self.history_items}
        if not parse_qs(request.url.query.decode()).get("cursor"):
            out["next_cursor"] = "page-2"
        return httpx.Response(200, json=out)

    def _details(self, request: httpx.Request) -> httpx.Response:
        visit_id = request.url.path.rsplit("/", 1)[-1]
        mode = self.details_mode
        if mode == "401":
            return httpx.Response(401, json={"code": "unauthenticated"})
        if mode == "403":
            return httpx.Response(403, json={"code": "not_participant"})
        if mode == "404":
            return httpx.Response(404, json={"code": "unknown_visit"})
        if mode == "503":
            return httpx.Response(503)
        query = parse_qs(request.url.query.decode())
        limit = int(query.get("limit", ["500"])[0])
        cursor = query.get("cursor", [""])[0]
        page = int(cursor[1:]) if cursor else 0
        if self.details_fail_page is not None and page == self.details_fail_page:
            return httpx.Response(503)
        rows = self.details_lines[page * limit:(page + 1) * limit]
        out = {"visit_id": visit_id, "transport": "trtc", "started_at": 1.0, "ended_at": 2.0, "duration_s": 1,
               "free_minutes_deducted": 50, "usage": None, "lines": rows,
               "uploaded": {"host": True, "guest": True}, "requester_role": "host"}
        total_pages = -(-len(self.details_lines) // limit) + self.details_pages_extra
        if page + 1 < total_pages:
            out["next_cursor"] = f"p{page + 1}"
        return httpx.Response(200, json=out)

    def _transcripts(self, request: httpx.Request) -> httpx.Response:
        mode = self.transcript_mode
        if mode == "bogus204":
            return httpx.Response(204)
        if mode == "html200":
            return httpx.Response(200, text="<html>proxy</html>")
        if mode == "503":
            return httpx.Response(503)
        if mode == "429":
            return httpx.Response(429, json={"code": "rate_limited", "retry_after_s": 77})
        if mode == "401":
            return httpx.Response(401, json={"code": "unauthorized"})
        if mode == "budget":
            return httpx.Response(413, json={"code": "transcript_budget_exceeded"})
        if mode == "parts":
            return httpx.Response(400, json={"code": "parts_out_of_range"})
        if mode == "not_participant":
            return httpx.Response(403, json={"code": "not_participant"})
        if mode == "unknown_visit":
            return httpx.Response(404, json={"code": "unknown_visit"})
        if mode in ("not_started", "not_started_final"):
            return httpx.Response(409, json={"code": "visit_not_started", "final": mode == "not_started_final"})
        if len(request.content) > self.limit:
            return httpx.Response(413, json={"code": "too_large"})
        body = json.loads(request.content)
        key = (body["visit_id"], body["role"])
        if key in self.complete:
            return httpx.Response(200, json={"ok": True, "duplicate": True, "complete": True,
                                             "accepted_parts": list(range(body.get("parts", 1)))})
        parts, part = body.get("parts", 1), body.get("part", 0)
        if part in self.fail_parts:
            self.fail_parts.discard(part)
            return httpx.Response(503)
        group = self.groups.get(key)
        if group is None or group["parts"] != parts:
            group = {"parts": parts, "chunks": {}}
            self.groups[key] = group
        duplicate = part in group["chunks"]
        group["chunks"][part] = body["lines"]
        done = len(group["chunks"]) == parts and mode != "never_complete"
        if done:
            self.complete[key] = [line for k in range(parts) for line in group["chunks"][k]]
        out = {"ok": True, "accepted_parts": sorted(group["chunks"]), "complete": done}
        if mode in ("no_parts", "complete_no_parts"):
            del out["accepted_parts"]
            out["complete"] = mode == "complete_no_parts"
        if duplicate:
            out["duplicate"] = True
        return httpx.Response(200 if duplicate else 201, json=out)

    def _reports(self, request: httpx.Request) -> httpx.Response:
        mode = self.report_mode
        if mode == "bogus200":
            return httpx.Response(200, json={"ok": True})
        if mode == "network":
            raise httpx.ConnectError("down")
        if mode == "503":
            return httpx.Response(503)
        if mode == "429":
            return httpx.Response(429, json={"code": "rate_limited", "retry_after_s": 5})
        if mode == "404":
            return httpx.Response(404, json={"code": "unknown_visit"})
        body = json.loads(request.content)
        self.reports.append(body)
        self.transcript_seen_at_report.append(any(k[0] == body["visit_id"] for k in self.complete))
        return httpx.Response(201, json={"report_id": f"r{len(self.reports)}"})

    def count(self, path: str) -> int:
        return sum(1 for r in self.requests if r.url.path == path)
