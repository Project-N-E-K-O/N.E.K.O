# Copyright 2025-2026 Project N.E.K.O. Team
# SPDX-License-Identifier: Apache-2.0
"""Information delivery between trusted, registered host adapters (RFC #3859).

The plugin owner implements the other end: authenticating local principals,
labelling reads/derived outputs and enforcing delivery before exposing any
result, callback, attachment or lookup handle. Legacy /runs has no such
contract and is deliberately not registered. Never accept adapter identities,
labels or these Python objects from a tool's JSON arguments.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from contextlib import ExitStack, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from nekoclaw.host import call_audit
from nekoclaw.host.ledger import Ledger
from nekoclaw.host.permissions import GrantBinding, GrantScope, PermissionGate


class DeliveryDenied(PermissionError):
    """Fixed host errors may cross the boundary; foreign exception bodies cannot."""


class ScopeTooLarge(DeliveryDenied):
    def __init__(self) -> None:
        super().__init__("The full scope must fit in the permission request. Request fewer resources at a time.")


class ResultWithheld(DeliveryDenied):
    def __init__(self) -> None:
        super().__init__("The target was invoked. Its result was withheld.")


class DeliveryAuditFull(RuntimeError):
    """The request reached its target, but the host could not audit the remaining work."""

    def __init__(self) -> None:
        super().__init__("The target was invoked. Its result could not be delivered because the audit is full.")


@dataclass(frozen=True)
class Parcel:
    """An adapter must explicitly classify each payload, including public data (empty set)."""

    data: Any
    scopes: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.scopes, frozenset) or any(
            not isinstance(scope, str) or not scope or not scope.isprintable()
            for scope in self.scopes
        ):
            raise ValueError("expected an explicit set of trusted resource identifiers")

    def snapshot(self, inherited: frozenset[str]) -> Parcel:
        """Detach the JSON payload before any asynchronous permission or audit work."""
        data = json.loads(json.dumps(self.data, allow_nan=False, ensure_ascii=False))
        return Parcel(data, self.scopes | inherited)


@dataclass(frozen=True)
class Answer:
    value: Parcel | None = None
    content: Parcel | None = None


@dataclass(frozen=True)
class Invocation:
    commission_id: str
    trace_id: str
    call_id: str


class DeliveryAdapter(Protocol):
    identity: str

    def permits(self, sender: str, *, one_way: bool) -> bool:
        """Apply the target's native invocation policy and supported delivery modes."""
        ...

    def accepts(self, scopes: frozenset[str]) -> bool:
        """Apply current, locally verified read rights to the entire set."""
        ...

    async def trigger(self, invocation: Invocation, arguments: Parcel) -> None:
        """Accept without a business reply. Deferred transfers belong to the invoking run.

        Directly await nested transfers in this same task. A queued worker can use
        deferred=True after scheduling; trigger must return for it to be accepted.
        Work that must survive that run requires a separately registered commission.
        """
        ...

    async def call(self, invocation: Invocation, arguments: Parcel) -> Answer: ...


@dataclass
class _Processing:
    endpoint: DeliveryAdapter
    commission_id: str
    scopes: frozenset[str]
    grants: dict[tuple[str, GrantScope], str]
    binding: GrantBinding
    accepted: asyncio.Future[bool] | None
    run_active: Callable[[], bool]
    task: asyncio.Task | None

    def settle(self, ok: bool) -> None:
        if self.accepted is not None and not self.accepted.done():
            self.accepted.set_result(ok)


@dataclass(frozen=True)
class Entry:
    """Description of an exact registered target for the requested input scopes."""
    identity: str
    can_trigger: bool
    can_call: bool


class ManagedDelivery:
    """Host-only interface. Each endpoint is bound by object identity at registration."""

    def __init__(self, ledger: Ledger, permissions: PermissionGate) -> None:
        self._ledger = ledger
        self._permissions = permissions
        self._adapters: dict[str, DeliveryAdapter] = {}
        self._bindings: dict[str, GrantBinding] = {}
        self._processing: ContextVar[_Processing | None] = ContextVar(
            "managed_delivery_processing", default=None
        )

    def register(self, adapter: DeliveryAdapter) -> None:
        identity = adapter.identity
        if not re.fullmatch(
            r"(?:neko|dsh|session|builtin):[a-zA-Z0-9_.:-]{1,120}", identity
        ):
            raise ValueError("invalid managed endpoint identity")
        if identity in self._adapters:
            raise ValueError("managed endpoint already registered")
        self._adapters[identity] = adapter
        self._bindings[identity] = GrantBinding()

    def discover(self, sender: DeliveryAdapter, scopes: frozenset[str]) -> tuple[Entry, ...]:
        """List native candidates only. Discovery neither borrows a grant nor runs an endpoint."""
        parent = self._processing.get()
        if parent is not None:
            if parent.endpoint is not sender or not parent.binding.active or not parent.run_active():
                return ()
            scopes = scopes | parent.scopes
        if self._adapters.get(sender.identity) is not sender:
            return ()
        try:
            if not sender.accepts(scopes):
                return ()
        except Exception:
            return ()
        entries = []
        for identity, recipient in sorted(self._adapters.items()):
            try:
                if not recipient.accepts(scopes):
                    continue
                trigger = recipient.permits(sender.identity, one_way=True)
                call = recipient.permits(sender.identity, one_way=False)
            except Exception:
                continue  # A native rule's error may contain data unavailable to this sender.
            if (trigger or call) and self._connected(sender, recipient):
                entries.append(Entry(identity, trigger, call))
        return tuple(entries)

    def unregister(self, adapter: DeliveryAdapter) -> None:
        if self._adapters.get(adapter.identity) is adapter:
            self._bindings.pop(adapter.identity).active = False
            del self._adapters[adapter.identity]

    def _connected(self, sender: DeliveryAdapter, recipient: DeliveryAdapter) -> bool:
        return (
            self._adapters.get(sender.identity) is sender
            and self._adapters.get(recipient.identity) is recipient
        )

    async def invoke(
        self,
        commission_id: str,
        sender: DeliveryAdapter,
        target: str,
        arguments: Parcel,
        *,
        one_way: bool,
        deferred: bool = False,
    ) -> Answer | None:
        """Queued trigger workers may pass deferred=True to wait for audited acceptance."""
        # Strip both text and exception context. An adapter's native checks and
        # driver errors can contain data the sender must never receive.
        try:
            return await self._invoke(
                commission_id, sender, target, arguments, one_way=one_way, deferred=deferred
            )
        except asyncio.CancelledError:
            failure = asyncio.CancelledError()
        except call_audit.CommissionEnded:
            failure = call_audit.CommissionEnded("this commission is no longer under way")
        except call_audit.AuditFull:
            failure = call_audit.AuditFull()
        except DeliveryAuditFull:
            failure = DeliveryAuditFull()
        except ResultWithheld:
            failure = ResultWithheld()
        except ScopeTooLarge:
            failure = ScopeTooLarge()
        except Exception:
            failure = DeliveryDenied("managed delivery refused or unavailable")
        raise failure

    async def _invoke(
        self,
        commission_id: str,
        sender: DeliveryAdapter,
        target: str,
        arguments: Parcel,
        *,
        one_way: bool,
        deferred: bool,
    ) -> Answer | None:
        recipient = self._adapters.get(target)
        if recipient is None or not self._connected(sender, recipient):
            raise DeliveryDenied("managed delivery is unavailable")
        registrations = (self._bindings[sender.identity], self._bindings[target])
        parent = self._processing.get()
        if deferred and (parent is None or parent.accepted is None or parent.task is asyncio.current_task()):
            raise DeliveryDenied("only a queued trigger worker may await acceptance")
        continuation = parent is not None and parent.accepted is not None and parent.task is not asyncio.current_task()
        if continuation and parent.task is not None and not deferred:
            # A child awaited by trigger() would deadlock if we waited implicitly.
            raise DeliveryDenied("an early trigger worker must request deferred acceptance")
        if parent is not None and (parent.endpoint is not sender or parent.commission_id != commission_id):
            raise DeliveryDenied("invocation does not belong to this adapter")
        # Freeze mutable data before awaiting acceptance. A trigger may acquire
        # additional restricted information before releasing its worker.
        arguments = arguments.snapshot(parent.scopes if parent else frozenset())
        if continuation:
            if not await asyncio.shield(parent.accepted):
                raise DeliveryDenied("trigger was not accepted")
            arguments = Parcel(arguments.data, arguments.scopes | parent.scopes)
        if parent is not None and (not parent.binding.active or not parent.run_active()):
            raise DeliveryDenied("registration or run has ended")
        run_active = parent.run_active if parent is not None else self._permissions.current_run(commission_id)
        # Keep once-only loans until the actual handoff, including across audit
        # writes. Roll back every temporary handle on all exit paths.
        with ExitStack() as cleanup:
            grants = parent.grants.copy() if parent and not continuation else {}
            answer = None
            invoked = False
            return_scopes: frozenset[str] = frozenset()
            async with call_audit.call(
                self._ledger, commission_id, "trigger" if one_way else "invoke", target
            ) as op:
                try:
                    if not run_active() or not recipient.permits(sender.identity, one_way=one_way):
                        raise DeliveryDenied("invocation refused")
                    invocation = Invocation(commission_id, op.trace_id, op.call_id)
                    start_scope = GrantScope(
                        provider=sender.identity,
                        resource="trigger" if one_way else "call",
                        recipient=target,
                        bindings=registrations,
                    )
                    category = "plugin_entry" if target.startswith("neko:") else "other"
                    await self._borrow(
                        commission_id,
                        category,
                        start_scope,
                        target,
                        op,
                        cleanup,
                        grants,
                    )
                    await self._authorize_transfer(
                        commission_id,
                        sender,
                        recipient,
                        arguments.scopes,
                        "send_arguments",
                        cleanup,
                        grants,
                    )
                    self._require_live(sender, recipient, arguments.scopes, grants)
                    if not run_active() or not recipient.permits(sender.identity, one_way=one_way):
                        raise DeliveryDenied("invocation refused")
                    frame = _Processing(
                        recipient, commission_id, arguments.scopes, grants.copy(), registrations[1],
                        asyncio.get_running_loop().create_future() if one_way else None,
                        run_active, asyncio.current_task(),
                    )
                    if one_way:
                        cleanup.callback(frame.settle, False)
                    token = self._processing.set(frame)
                    try:
                        invoked = True
                        if one_way:
                            await recipient.trigger(invocation, arguments)
                        else:
                            answer = await recipient.call(invocation, arguments)
                    finally:
                        frame.task = None
                        self._processing.reset(token)
                    if not one_way:
                        self._require_live(sender, recipient, arguments.scopes, grants)
                        answer, return_scopes = self._select_answer(
                            sender, answer, frame.scopes
                        )
                        for channel, parcel in (("value", answer.value), ("content", answer.content)):
                            if parcel is not None:
                                await self._authorize_transfer(
                                    commission_id, recipient, sender, parcel.scopes,
                                    "deliver_" + channel, cleanup, grants,
                                )
                        if answer.value is None and answer.content is None:
                            await self._authorize_transfer(
                                commission_id, recipient, sender, frozenset(), "deliver_empty", cleanup, grants
                            )
                except call_audit.AuditFull:
                    if invoked:
                        op.reason = "audit_full_after_invocation"
                        raise DeliveryAuditFull() from None
                    raise
                except DeliveryDenied:
                    op.status = "denied"
                    if invoked and not one_way:
                        op.reason = "result_withheld"
                        raise ResultWithheld() from None
                    op.reason = "managed_delivery_denied"
                    raise
                except Exception:
                    if invoked and not one_way:
                        op.reason = "result_unavailable_after_invocation"
                        raise ResultWithheld() from None
                    raise
            # Closing a call record awaited SQLite. Check again, with no await
            # between this test and the caller receiving the actual payload.
            if not op.closed:
                raise ResultWithheld()
            if one_way:
                # A queued independent job keeps the classification and endpoint binding,
                # but must obtain its own execution and delivery approvals.
                with suppress(Exception):
                    self._require_live(sender, recipient, arguments.scopes, grants)
                    frame.settle(run_active())
            else:
                try:
                    self._require_live(recipient, sender, return_scopes, grants)
                except Exception:
                    # The target ran, but the caller never received the result.
                    # Keep the outcome unambiguous even if the final audit update fails.
                    with suppress(Exception):
                        await self._ledger.run(
                            lambda db: db.execute(
                                "UPDATE host_calls SET status='denied',reason='delivery_revoked' WHERE call_id=?",
                                (op.call_id,),
                            )
                        )
                    raise ResultWithheld() from None
                if parent is not None:
                    parent.scopes |= return_scopes
            return answer

    @staticmethod
    def _select_answer(
        receiver: DeliveryAdapter, answer: Answer | None, inherited: frozenset[str]
    ) -> tuple[Answer, frozenset[str]]:
        if not isinstance(answer, Answer):
            raise DeliveryDenied("unclassified response")

        def allowed(parcel: Parcel | None) -> Parcel | None:
            if parcel is None:
                return None
            if not isinstance(parcel, Parcel):
                raise DeliveryDenied("unclassified response")
            scopes = inherited | parcel.scopes
            return parcel.snapshot(inherited) if receiver.accepts(scopes) else None

        value, content = allowed(answer.value), allowed(answer.content)
        if (answer.value or answer.content) and not (value or content):
            raise DeliveryDenied("recipient cannot receive the result")
        scopes = frozenset().union(
            *(p.scopes for p in (value, content) if p is not None)
        )
        return Answer(value, content), scopes

    def _require_live(
        self,
        sender: DeliveryAdapter,
        receiver: DeliveryAdapter,
        scopes: frozenset[str],
        grants: dict[tuple[str, GrantScope], str],
    ) -> None:
        if (
            not self._connected(sender, receiver)
            or not sender.accepts(scopes)
            or not receiver.accepts(scopes)
            or not all(self._permissions.grant_active(ref) for ref in grants.values())
        ):
            raise DeliveryDenied("delivery authorization expired")

    async def _borrow(
        self,
        commission_id: str,
        category: str,
        scope: GrantScope,
        title: str,
        record: call_audit.Call,
        cleanup: ExitStack,
        grants: dict[tuple[str, GrantScope], str],
        direction: str | None = None,
    ) -> None:
        inherited = grants.get((category, scope))
        if inherited:
            if not await self._permissions.valid_grant(commission_id, inherited, category, scope=scope):
                raise DeliveryDenied("authorization expired")
            record.grant_id = self._permissions.authorization_ref(inherited)
            return
        decision = await self._permissions.check(
            commission_id,
            category,
            scope=scope,
            detail={"title": title, **({"direction": direction} if direction else {})},
            hold_once=True,
        )
        grant = decision.grant_id
        record.grant_id = self._permissions.authorization_ref(grant)
        if not decision.allow or not grant:
            raise DeliveryDenied("permission refused")
        cleanup.callback(self._permissions.release_grant, grant)
        if not await self._permissions.valid_grant(
            commission_id, grant, category, scope=scope
        ):
            raise DeliveryDenied("authorization expired")
        grants[(category, scope)] = grant

    async def _authorize_transfer(
        self,
        commission_id: str,
        sender: DeliveryAdapter,
        receiver: DeliveryAdapter,
        scopes: frozenset[str],
        kind: str,
        cleanup: ExitStack,
        grants: dict[tuple[str, GrantScope], str],
    ) -> None:
        async with call_audit.call(
            self._ledger, commission_id, kind, receiver.identity
        ) as record:
            try:
                self._require_live(sender, receiver, scopes, grants)
                if scopes:
                    from config.nekoclaw_settings import PERMISSION_SUBJECT_MAX_TOKENS
                    from utils.tokenize import count_tokens

                    resources = json.dumps(
                        sorted(scopes), ensure_ascii=False, separators=(",", ":")
                    )
                    record.scope_ref = hashlib.sha256(resources.encode()).hexdigest()
                    direction = kind.split("_", 1)[1]
                    scope = GrantScope(
                        provider=sender.identity,
                        resource=f"{direction}:{record.scope_ref}",
                        recipient=receiver.identity,
                        bindings=(self._bindings[sender.identity], self._bindings[receiver.identity]),
                    )
                    title = f"{sender.identity} → {receiver.identity} {resources}"
                    if count_tokens(title) > PERMISSION_SUBJECT_MAX_TOKENS:
                        raise ScopeTooLarge()
                    await self._borrow(
                        commission_id,
                        "managed_delivery",
                        scope,
                        title,
                        record,
                        cleanup,
                        grants,
                        direction=direction,
                    )
            except DeliveryDenied:
                record.status = "denied"
                record.reason = "managed_delivery_denied"
                raise
        if not record.closed:
            raise DeliveryDenied("the transfer could not be audited")
