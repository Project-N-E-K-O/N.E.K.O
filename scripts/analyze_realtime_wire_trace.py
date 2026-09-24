#!/usr/bin/env python3
"""Offline analyzer for NEKO_REALTIME_WIRE_TRACE logs.

Reads one or more N.E.K.O_Main_*.log files, extracts the "[wire-trace] " and
"[arbiter-trace] " JSON records, segments them by connection, and reports on
three hypotheses about the lanlan_app_gemini proxy:

  H_a  function_call_arguments.* carry a top-level response_id that differs
       from the response.id of the response.done that terminates them.
  H_b  the proxy sends no response.done that resolves our owner while our
       response.create is pending (unrelated dones may still pass by).
  H_c  the proxy answers conversation.item.create before our response.create.

Arbiter records stamped with cid/gen are keyed to their connection exactly;
older records without cid fall back to "most recently active connection".

Stdlib only; imports nothing from the project.

Usage:
  python analyze_wire_trace.py LOG [LOG ...] [--output REPORT]
                               [--timeline-limit N] [--no-timeline]
"""

import argparse
import datetime
import json
import re
import sys

WIRE_PREFIX = "[wire-trace] "
ARBITER_PREFIX = "[arbiter-trace] "
STAMP_RE = re.compile(
    "([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2})"
)
PAIR_WINDOW_S = 3.0
DONE_WINDOW_S = 60.0
ARBITER_ATTRIBUTION_WINDOW_S = 5.0
NL = chr(10)

# Arbiter terminal outcomes that credit the terminal to the pending owner.
RESOLVING_TERMINAL_OUTCOMES = frozenset(
    {"resolved", "claimed_never_announced", "cancelled_adoption"}
)
# Arbiter timeouts that mean the owner waited for a start or a terminal.
OWNER_WAIT_TIMEOUT_KINDS = frozenset({"response_started", "response_done"})
H_A_TOTAL_KEYS = {
    "match": "a_match",
    "mismatch": "a_mismatch",
    "no_done_within_60s": "a_no_done",
    "fc_missing_response_id": "a_fc_missing_id",
    "done_missing_id": "a_done_missing_id",
}


class Record:
    __slots__ = ("kind", "t", "stamp", "data", "path", "line_no", "seq")

    def __init__(self, kind, t, stamp, data, path, line_no, seq):
        self.kind = kind
        self.t = t
        self.stamp = stamp
        self.data = data
        self.path = path
        self.line_no = line_no
        self.seq = seq

    @property
    def is_send(self):
        return self.kind == "wire" and self.data.get("dir") == "send"

    @property
    def is_recv(self):
        return self.kind == "wire" and self.data.get("dir") == "recv"

    @property
    def type(self):
        value = self.data.get("type")
        return value if isinstance(value, str) else ""


def stamp_to_epoch(stamp):
    if not stamp:
        return None
    try:
        parsed = datetime.datetime.strptime(
            stamp.replace("T", " "), "%Y-%m-%d %H:%M:%S"
        )
    except ValueError:
        return None
    return parsed.timestamp()


def parse_files(paths):
    decoder = json.JSONDecoder()
    records = []
    seq = 0
    stats = {"lines": 0, "wire": 0, "arbiter": 0, "unparseable": 0}
    for path in paths:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                stats["lines"] += 1
                for kind, prefix in (("wire", WIRE_PREFIX), ("arbiter", ARBITER_PREFIX)):
                    index = line.find(prefix)
                    if index < 0:
                        continue
                    payload = line[index + len(prefix):].strip()
                    try:
                        data, _end = decoder.raw_decode(payload)
                    except ValueError:
                        stats["unparseable"] += 1
                        break
                    if not isinstance(data, dict):
                        stats["unparseable"] += 1
                        break
                    match = STAMP_RE.search(line[:index])
                    stamp = match.group(1) if match else None
                    t = data.get("t")
                    if not isinstance(t, (int, float)) or isinstance(t, bool):
                        t = stamp_to_epoch(stamp)
                    if t is None:
                        stats["unparseable"] += 1
                        break
                    records.append(
                        Record(kind, float(t), stamp, data, path, line_no, seq)
                    )
                    stats[kind] += 1
                    seq += 1
                    break
    records.sort(key=lambda record: (record.t, record.seq))
    return records, stats


class Segment:
    def __init__(self, key):
        self.key = key
        self.records = []
        self.last_wire_t = None
        self.ambiguous_arbiter = 0
        self.exact_arbiter = 0

    @property
    def first_t(self):
        return self.records[0].t if self.records else 0.0

    def label(self):
        if self.key[0] == "anon":
            return "connection #%s (no gen/cid; split on session.update)" % self.key[1]
        if self.key[0] == "arbiter-only":
            return "arbiter records with no wire records"
        return "cid=%s gen=%s" % (self.key[0], self.key[1])


def segment_records(records):
    segments = []
    by_key = {}
    anon_index = 0
    pending_arbiter = []

    def open_segment(key):
        segment = Segment(key)
        segments.append(segment)
        by_key[key] = segment
        return segment

    for record in records:
        data = record.data
        if record.kind == "wire":
            cid = data.get("cid")
            gen = data.get("gen")
            if cid is None and gen is None:
                key = ("anon", anon_index)
                current = by_key.get(key)
                if (
                    current is not None
                    and record.is_send
                    and record.type == "session.update"
                    and any(existing.is_recv for existing in current.records)
                ):
                    anon_index += 1
                    key = ("anon", anon_index)
            else:
                key = (cid, gen)
            segment = by_key.get(key) or open_segment(key)
            if pending_arbiter:
                segment.records.extend(pending_arbiter)
                pending_arbiter = []
            segment.records.append(record)
            segment.last_wire_t = record.t
            continue
        cid = data.get("cid")
        if cid is not None:
            # Arbiter records stamped with the wire trace's client tag key to
            # their connection exactly; no guessing across live clients.
            gen = data.get("gen")
            if gen is not None:
                key = (cid, gen)
                segment = by_key.get(key) or open_segment(key)
            else:
                same_client = [
                    segment for segment in segments if segment.key[0] == cid
                ]
                segment = same_client[-1] if same_client else open_segment((cid, None))
            segment.records.append(record)
            segment.exact_arbiter += 1
            continue
        live = [
            segment
            for segment in segments
            if segment.last_wire_t is not None
        ]
        if not live:
            pending_arbiter.append(record)
            continue
        target = max(live, key=lambda segment: segment.last_wire_t)
        rivals = [
            segment
            for segment in live
            if segment is not target
            and target.last_wire_t - segment.last_wire_t <= ARBITER_ATTRIBUTION_WINDOW_S
            and segment.key[0] != target.key[0]
        ]
        if rivals:
            target.ambiguous_arbiter += 1
        target.records.append(record)
    if pending_arbiter:
        segment = open_segment(("arbiter-only",))
        segment.records.extend(pending_arbiter)
    for segment in segments:
        segment.records.sort(key=lambda record: (record.t, record.seq))
    return segments


def compact(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def describe(record):
    data = record.data
    if record.kind == "wire":
        head = ("SEND " if record.is_send else "RECV ") + record.type
        skip = {"dir", "t", "type", "gen", "cid"}
    else:
        head = "ARB  " + str(data.get("decision"))
        skip = {"t", "decision"}
    parts = [
        key + "=" + compact(value)
        for key, value in data.items()
        if key not in skip and value is not None
    ]
    return head + (" " + " ".join(parts) if parts else "")


def ms(delta_s):
    return "%+d ms" % round(delta_s * 1000)


def response_id_of(record):
    data = record.data
    for key in ("response_id", "response.id"):
        value = data.get(key)
        if value is not None:
            return value
    return None


# ---------------------------------------------------------------- H_a


def analyze_h_a(segment):
    calls = {}
    dones = []
    for record in segment.records:
        if not record.is_recv:
            continue
        if record.type.startswith("response.function_call_arguments."):
            call_id = record.data.get("call_id")
            if call_id is None:
                call_id = "<no call_id: response_id=%s output_index=%s>" % (
                    record.data.get("response_id"),
                    record.data.get("output_index"),
                )
            entry = calls.get(call_id)
            if entry is None:
                entry = calls[call_id] = {
                    "first": record,
                    "last": record,
                    "response_ids": [],
                    "name": record.data.get("name"),
                }
            entry["last"] = record
            response_id = record.data.get("response_id")
            if response_id is not None and response_id not in entry["response_ids"]:
                entry["response_ids"].append(response_id)
            if entry["name"] is None:
                entry["name"] = record.data.get("name")
        elif record.type == "response.done":
            dones.append(record)
    results = []
    for call_id, entry in calls.items():
        fc_response_id = entry["response_ids"][0] if entry["response_ids"] else None
        listed = [
            done
            for done in dones
            if any(
                isinstance(item, dict) and item.get("call_id") == call_id
                for item in (done.data.get("output") or [])
            )
        ]
        done = None
        association = None
        if listed:
            done = listed[0]
            association = "listed_in_output"
        else:
            later = [
                candidate
                for candidate in dones
                if candidate.t >= entry["last"].t
                and candidate.t - entry["last"].t <= DONE_WINDOW_S
            ]
            if later:
                done = later[0]
                association = "next_done"
        done_id = done.data.get("response.id") if done is not None else None
        # A missing id on either side is a different failure from a mismatch
        # (the arbiter never adopts id-less content), so it gets its own
        # category and stays out of the match/mismatch totals.
        if fc_response_id is None:
            verdict = "fc_missing_response_id"
        elif done is None:
            verdict = "no_done_within_60s"
        elif done_id is None:
            verdict = "done_missing_id"
        elif done_id == fc_response_id:
            verdict = "match"
        else:
            verdict = "mismatch"
        fc_id_terminated = fc_response_id is not None and any(
            candidate.data.get("response.id") == fc_response_id for candidate in dones
        )
        results.append(
            {
                "call_id": call_id,
                "name": entry["name"],
                "first": entry["first"],
                "fc_response_ids": entry["response_ids"],
                "done": done,
                "done_id": done_id,
                "association": association,
                "verdict": verdict,
                "fc_id_ever_terminated": fc_id_terminated,
            }
        )
    return results


# ---------------------------------------------------------------- H_b


def analyze_h_b(segment):
    arbiter = [record for record in segment.records if record.kind == "arbiter"]
    has_arbiter = bool(arbiter)
    results = []
    creates = [
        record
        for record in segment.records
        if record.is_send and record.type == "response.create"
    ]
    for create in creates:
        event_id = create.data.get("event_id")
        source = None
        for record in arbiter:
            data = record.data
            if (
                data.get("decision") == "dispatch"
                and data.get("phase") == "response_create_sent"
                and data.get("create_event_id") == event_id
                and abs(record.t - create.t) <= 2.0
            ):
                source = data.get("source")
                break
        end = None
        end_reason = None
        if has_arbiter:
            for record in arbiter:
                if record.t < create.t:
                    continue
                data = record.data
                decision = data.get("decision")
                if (
                    decision == "dispatch"
                    and data.get("phase") in ("completed", "failed")
                    and (source is None or data.get("source") == source)
                ):
                    end = record.t
                    end_reason = "dispatch_" + str(data.get("phase"))
                    break
                if decision == "connection":
                    end = record.t
                    end_reason = "connection_" + str(data.get("outcome"))
                    break
        if end is None:
            later_dones = [
                record
                for record in segment.records
                if record.is_recv
                and record.type == "response.done"
                and record.t > create.t
            ]
            if later_dones and later_dones[0].t - create.t <= DONE_WINDOW_S:
                end = later_dones[0].t
                end_reason = "first_response_done (no arbiter data)"
            else:
                end = create.t + DONE_WINDOW_S
                end_reason = "60s bound (no terminal observed)"
        window = [
            record for record in segment.records if create.t < record.t <= end
        ]
        dones = [
            record
            for record in window
            if record.is_recv and record.type == "response.done"
        ]
        terminal_outcomes = {}
        orphan_while_waiting = 0
        timeouts = []
        started_by = None
        resolved_by = None
        first_owner_timeout_t = None
        for record in window:
            if record.kind != "arbiter":
                continue
            data = record.data
            decision = data.get("decision")
            ours = source is None or data.get("owner_source") == source
            if decision == "terminal":
                outcome = str(data.get("outcome"))
                terminal_outcomes[outcome] = terminal_outcomes.get(outcome, 0) + 1
                if outcome == "orphan_mismatch" and ours:
                    orphan_while_waiting += 1
                if (
                    resolved_by is None
                    and ours
                    and outcome in RESOLVING_TERMINAL_OUTCOMES
                ):
                    resolved_by = record
            elif decision == "timeout" and (
                source is None or data.get("source") == source
            ):
                kind = str(data.get("kind"))
                timeouts.append(kind)
                if kind in OWNER_WAIT_TIMEOUT_KINDS and first_owner_timeout_t is None:
                    first_owner_timeout_t = record.t
            if started_by is None and ours:
                if decision == "content" and data.get("outcome") == "accepted":
                    started_by = "content %s response_id=%s" % (
                        data.get("event_type"),
                        data.get("response_id"),
                    )
                elif decision == "created" and data.get("outcome") == "owner_claimed":
                    started_by = "response.created response.id=%s" % data.get(
                        "response.id"
                    )
        # Classified by how the arbiter credited terminals, not by whether any
        # response.done passed by: an orphan_mismatch or server_response done
        # arriving while our owner starves is exactly the H_b failure.
        if has_arbiter:
            if resolved_by is not None:
                classification = "refuted"
            elif first_owner_timeout_t is not None:
                classification = "supported"
            else:
                classification = "inconclusive"
        else:
            classification = "refuted" if dones else "inconclusive"
        results.append(
            {
                "create": create,
                "source": source,
                "end_reason": end_reason,
                "window_ms": round((end - create.t) * 1000),
                "done_ids": [record.data.get("response.id") for record in dones],
                "terminal_outcomes": terminal_outcomes,
                "orphan_while_waiting": orphan_while_waiting,
                "timeouts": timeouts,
                "started_by": started_by,
                "has_arbiter": has_arbiter,
                "resolved_by": resolved_by,
                "resolved_after_timeout": resolved_by is not None
                and first_owner_timeout_t is not None
                and first_owner_timeout_t < resolved_by.t,
                "classification": classification,
                "done_not_attributed": has_arbiter
                and bool(dones)
                and resolved_by is None,
            }
        )
    return results


# ---------------------------------------------------------------- H_c


CONTENT_TYPES = ("response.created", "response.done", "response.output_item.added")


def is_content_event(record):
    if not record.is_recv:
        return False
    event_type = record.type
    if event_type.startswith("response.function_call_arguments."):
        return True
    if event_type.endswith(".delta") and event_type.startswith("response."):
        return True
    return event_type in CONTENT_TYPES


def analyze_h_c(segment):
    results = []
    records = segment.records
    for index, item in enumerate(records):
        if not (item.is_send and item.type == "conversation.item.create"):
            continue
        if not (
            item.data.get("item.role") == "user"
            or item.data.get("item.type") == "message"
        ):
            continue
        create = None
        for later in records[index + 1:]:
            if later.t - item.t > PAIR_WINDOW_S:
                break
            if later.is_send and later.type == "response.create":
                create = later
                break
        if create is None:
            continue
        seen_before = set()
        for earlier in records[:index]:
            if earlier.kind == "wire":
                response_id = response_id_of(earlier)
                if response_id is not None:
                    seen_before.add(response_id)
        between = []
        arbiter_between = []
        for record in records[index + 1:]:
            if record.t >= create.t:
                break
            if is_content_event(record):
                response_id = response_id_of(record)
                between.append(
                    {
                        "record": record,
                        "offset_ms": round((record.t - item.t) * 1000),
                        "response_id": response_id,
                        "new_response": response_id is not None
                        and response_id not in seen_before,
                    }
                )
            elif record.kind == "arbiter" and record.data.get("decision") in (
                "content",
                "created",
                "terminal",
                "item_created",
            ):
                arbiter_between.append(record)
        results.append(
            {
                "item": item,
                "create": create,
                "gap_ms": round((create.t - item.t) * 1000),
                "between": between,
                "arbiter_between": arbiter_between,
            }
        )
    return results


# ---------------------------------------------------------------- report


def verdict(supported, refuted, label_supported, label_refuted, total):
    if total == 0:
        return "insufficient data (0 cases)"
    if supported and not refuted:
        return "supported (%d of %d %s)" % (supported, total, label_supported)
    if refuted and not supported:
        return "refuted (%d of %d %s)" % (refuted, total, label_refuted)
    if supported and refuted:
        return "mixed: supported in %d, refuted in %d (of %d)" % (
            supported,
            refuted,
            total,
        )
    return "insufficient data (%d cases, none conclusive)" % total


def build_report(paths, records, stats, segments, timeline_limit, show_timeline):
    out = []
    add = out.append
    add("Realtime wire-trace report")
    add("=" * 26)
    add("inputs: " + ", ".join(paths))
    add(
        "lines read=%d  wire records=%d  arbiter records=%d  unparseable=%d"
        % (stats["lines"], stats["wire"], stats["arbiter"], stats["unparseable"])
    )
    add("connections: %d" % len(segments))
    add("")

    totals = {
        "a_match": 0,
        "a_mismatch": 0,
        "a_no_done": 0,
        "a_fc_missing_id": 0,
        "a_done_missing_id": 0,
        "b_windows": 0,
        "b_supported": 0,
        "b_refuted": 0,
        "b_inconclusive": 0,
        "b_done_not_attributed": 0,
        "b_orphans": 0,
        "b_no_arbiter": 0,
        "c_pairs": 0,
        "c_new_content": 0,
        "c_no_content": 0,
    }

    for number, segment in enumerate(segments, 1):
        base = segment.first_t
        add("-" * 78)
        add("Connection %d: %s" % (number, segment.label()))
        first = segment.records[0] if segment.records else None
        if first is not None:
            add(
                "  starts %s (t=%.3f), %d records (%d arbiter records keyed by cid)"
                % (
                    first.stamp or "?",
                    first.t,
                    len(segment.records),
                    segment.exact_arbiter,
                )
            )
        if segment.ambiguous_arbiter:
            add(
                "  NOTE: %d arbiter records were attributed here while another "
                "client was active within %.0fs; attribution is approximate."
                % (segment.ambiguous_arbiter, ARBITER_ATTRIBUTION_WINDOW_S)
            )
        add("")

        add("  H_a: function-call response_id vs terminating response.done")
        h_a = analyze_h_a(segment)
        if not h_a:
            add("    (no function_call_arguments.* events)")
        for result in h_a:
            first_record = result["first"]
            done = result["done"]
            add(
                "    call_id=%s name=%s fc.response_id=%s at %s"
                % (
                    result["call_id"],
                    result["name"],
                    ",".join(str(value) for value in result["fc_response_ids"]) or None,
                    ms(first_record.t - base),
                )
            )
            if done is None:
                add("      -> no response.done within 60s: %s" % result["verdict"])
            else:
                add(
                    "      -> response.done response.id=%s at %s (%s): %s%s"
                    % (
                        result["done_id"],
                        ms(done.t - base),
                        result["association"],
                        result["verdict"].upper(),
                        ""
                        if result["fc_id_ever_terminated"]
                        or not result["fc_response_ids"]
                        else "; fc.response_id never appears on any response.done",
                    )
                )
            totals[H_A_TOTAL_KEYS[result["verdict"]]] += 1
        add("")

        add("  H_b: response.done while our response.create is pending")
        h_b = analyze_h_b(segment)
        if not h_b:
            add("    (no response.create sends)")
        for result in h_b:
            create = result["create"]
            totals["b_windows"] += 1
            if not result["has_arbiter"]:
                totals["b_no_arbiter"] += 1
            add(
                "    response.create event_id=%s source=%s at %s; pending %d ms until %s"
                % (
                    create.data.get("event_id"),
                    result["source"],
                    ms(create.t - base),
                    result["window_ms"],
                    result["end_reason"],
                )
            )
            add(
                "      response.done received while pending: %d %s"
                % (len(result["done_ids"]), result["done_ids"] or "")
            )
            if result["terminal_outcomes"]:
                add("      arbiter terminal outcomes: %s" % compact(result["terminal_outcomes"]))
            if result["orphan_while_waiting"]:
                add(
                    "      orphan_mismatch while this owner waited: %d"
                    % result["orphan_while_waiting"]
                )
                totals["b_orphans"] += result["orphan_while_waiting"]
            if result["started_by"]:
                add("      owner start confirmed by: %s" % result["started_by"])
            if result["timeouts"]:
                add("      timeouts: %s" % ", ".join(result["timeouts"]))
            if result["done_not_attributed"]:
                totals["b_done_not_attributed"] += 1
                add("      response.done arrived, but no terminal resolved this owner")
            classification = result["classification"]
            resolved = result["resolved_by"]
            if classification == "refuted" and resolved is not None:
                detail = "terminal outcome=%s response.id=%s credited to this owner at %s%s" % (
                    resolved.data.get("outcome"),
                    resolved.data.get("response.id"),
                    ms(resolved.t - base),
                    " (only after a started/done timeout)"
                    if result["resolved_after_timeout"]
                    else "",
                )
            elif classification == "refuted":
                detail = "a response.done arrived; no arbiter data to attribute it"
            elif classification == "supported":
                detail = "no terminal resolved this owner and a started/done timeout fired"
            elif result["has_arbiter"]:
                detail = "no terminal resolved this owner, but no started/done timeout fired"
            else:
                detail = "no response.done and no arbiter data"
            totals["b_" + classification] += 1
            add("      -> %s: %s" % (classification.upper(), detail))
        add("")

        add("  H_c: content between conversation.item.create and response.create")
        h_c = analyze_h_c(segment)
        if not h_c:
            add("    (no user/message item followed by response.create within 3s)")
        for result in h_c:
            item = result["item"]
            totals["c_pairs"] += 1
            add(
                "    item.create item.id=%s role=%s type=%s at %s -> response.create "
                "event_id=%s after %d ms"
                % (
                    item.data.get("item.id"),
                    item.data.get("item.role"),
                    item.data.get("item.type"),
                    ms(item.t - base),
                    result["create"].data.get("event_id"),
                    result["gap_ms"],
                )
            )
            new_count = sum(1 for entry in result["between"] if entry["new_response"])
            if not result["between"]:
                add("      no provider content in between")
            # Wire content and arbiter decisions interleaved in time order.
            listing = [
                (
                    entry["record"].t,
                    entry["record"].seq,
                    "      %+6d ms  RECV %s response_id=%s%s"
                    % (
                        entry["offset_ms"],
                        entry["record"].type,
                        entry["response_id"],
                        "  (new response)" if entry["new_response"] else "",
                    ),
                )
                for entry in result["between"]
            ] + [
                (
                    record.t,
                    record.seq,
                    "      %+6d ms  %s"
                    % (round((record.t - item.t) * 1000), describe(record)),
                )
                for record in result["arbiter_between"]
            ]
            for _t, _seq, text in sorted(listing):
                add(text)
            if new_count:
                totals["c_new_content"] += 1
            else:
                totals["c_no_content"] += 1
        add("")

        if show_timeline:
            add("  Timeline (offsets from connection start)")
            shown = segment.records
            if timeline_limit and len(shown) > timeline_limit:
                add(
                    "    (showing first %d of %d records; use --timeline-limit 0 for all)"
                    % (timeline_limit, len(shown))
                )
                shown = shown[:timeline_limit]
            for record in shown:
                add("    %10s  %s" % (ms(record.t - base), describe(record)))
            add("")

    add("=" * 78)
    add("Verdicts")
    add("")
    associated = totals["a_match"] + totals["a_mismatch"]
    add(
        "H_a (function-call response_id != response.done response.id): "
        + verdict(
            totals["a_mismatch"],
            totals["a_match"],
            "associated calls mismatched",
            "associated calls matched",
            associated,
        )
    )
    add(
        "     counts: match=%d mismatch=%d no_done_within_60s=%d "
        "fc_missing_response_id=%d done_missing_id=%d"
        % (
            totals["a_match"],
            totals["a_mismatch"],
            totals["a_no_done"],
            totals["a_fc_missing_id"],
            totals["a_done_missing_id"],
        )
    )
    add(
        "H_b (no response.done resolves our owner while its response.create is pending): "
        + verdict(
            totals["b_supported"],
            totals["b_refuted"],
            "pending creates timed out with no terminal resolving the owner",
            "pending creates were resolved by a terminal",
            totals["b_windows"],
        )
    )
    add(
        "     counts: pending_windows=%d supported=%d refuted=%d inconclusive=%d "
        "done_arrived_not_attributed_to_owner=%d orphan_mismatch_while_waiting=%d "
        "windows_without_arbiter_data=%d"
        % (
            totals["b_windows"],
            totals["b_supported"],
            totals["b_refuted"],
            totals["b_inconclusive"],
            totals["b_done_not_attributed"],
            totals["b_orphans"],
            totals["b_no_arbiter"],
        )
    )
    add(
        "H_c (provider answers the item before response.create): "
        + verdict(
            totals["c_new_content"],
            totals["c_no_content"],
            "item/create pairs had new-response content in between",
            "item/create pairs had none",
            totals["c_pairs"],
        )
    )
    add(
        "     counts: pairs=%d with_new_response_content=%d without=%d"
        % (totals["c_pairs"], totals["c_new_content"], totals["c_no_content"])
    )
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split(NL)[0])
    parser.add_argument("logs", nargs="+", help="N.E.K.O_Main_*.log files")
    parser.add_argument("--output", "-o", help="write the report to this file")
    parser.add_argument(
        "--timeline-limit",
        type=int,
        default=400,
        help="max timeline records per connection (0 = unlimited)",
    )
    parser.add_argument(
        "--no-timeline", action="store_true", help="omit the per-connection timeline"
    )
    args = parser.parse_args(argv)
    records, stats = parse_files(args.logs)
    segments = segment_records(records)
    lines = build_report(
        args.logs,
        records,
        stats,
        segments,
        args.timeline_limit,
        not args.no_timeline,
    )
    text = NL.join(lines) + NL
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
