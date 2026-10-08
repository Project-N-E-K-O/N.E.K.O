# Remote voice overwrite recovery

Related issue: [#3317](https://github.com/Project-N-E-K-O/N.E.K.O/issues/3317).

## Contract and provider evidence

Prepared recovery is limited to a local operation which has never acquired permission to
submit a provider mutation. Explicit abandonment of an unknown result is a separate
risk-acceptance action, approved by the maintainer on 2026-10-08 in
[PR #3325](https://github.com/Project-N-E-K-O/N.E.K.O/pull/3325#discussion_r4218143397). It preserves the imported voice reference, provider,
account/project/resource scope and character bindings. Closing a dialog or
cancelling an HTTP waiter does not cancel a remote mutation.

The existing implementations use Doubao `/api/v3/tts/voice_clone` and CosyVoice
`/api/v1/services/audio/tts/customization` with `action=update_voice`.

| Evidence needed | Doubao clone interface | CosyVoice update interface |
| --- | --- | --- |
| Client idempotency key and lifetime | Not established | Not established |
| Same-key, same-body replay contract | Not established | Not established |
| Query one specific update operation | Not established; voice status is insufficient | Not established; voice status is insufficient |
| Cancellation with confirmed non-acceptance | Not established | Not established |
| Interrupted upload / lost-response acceptance boundary | Not established | Not established |
| Revision ownership | Voice revision is not a receipt for a particular local operation | Voice revision is not a receipt for a particular local operation |

Checked on 2026-10-07 against the implemented request paths and official
[CosyVoice management reference](https://help.aliyun.com/en/model-studio/cosyvoice-clone-api-reference),
[HTTP reference](https://help.aliyun.com/en/model-studio/voice-clone-design-http-api),
and [Volcengine speech OpenAPI catalogue](https://api.volcengine.com/api-docs/?serviceCode=speech_saas_prod&version=2025-05-21).
These references do not establish the missing guarantees for both implemented
mutation paths. A request/trace ID alone is therefore never used for replay or
unlocking. APIs for other products (for example RTC training) are not substituted
as evidence for the clone endpoint.

## Local protocol

The four existing result states remain unchanged. Submission phase is separate:

| Phase | Meaning | Recovery |
| --- | --- | --- |
| `prepared` | Operation recorded, permission to submit has not been acquired | Explicit conditional recovery permitted |
| `submission_possible` | Permission persisted; transport may have started | Protected until remote reconciliation or explicit unknown abandonment |
| absent (legacy) | Submission history unknown | Never inferred to be prepared; unknown abandonment requires fresh query evidence |

An operation must win an atomic `prepared` to `submission_possible` transition
before calling the provider mutation. The storage transition returns an explicit
applied receipt, separately from the stored winner. Operation ID, record revision,
phase and pending status are checked together. Failed persistence or a conflict
prevents submission.

Recovery checks the active context again and uses the same atomic transition.
It changes the result to `failed` and records `not_submitted_recovered` while
retaining the old operation ID and phase as terminal evidence. Submission cannot
revive that operation. A subsequent overwrite uses a fresh operation ID on the
same local voice reference.

Recovery and submission may race. Recovery winning means the old task cannot
submit. Submission winning means recovery cannot clear its protection. Late
cleanup must still match operation ownership and the observation revision.

## Limits

The interval after `submission_possible` is persisted and before the request is
sent intentionally remains protected, as do historical unknown records. `ready`,
timeouts and unchanged revisions never authorize automatic replay. Explicit unknown
abandonment accepts duplicate-submission risk; no resource migration is included. JSON storage serialization is limited to one
backend process; this protocol does not claim inter-process transactions.

Verification must use precise barriers, isolated provider endpoints and actual
subprocess termination at preparation, permission acquisition, partial upload,
provider acceptance and final local persistence. Restarts must preserve local
identity and compare both persisted state and actual provider submission counts.


## Explicit unknown-result abandonment

Only `overwrite_status=unknown` can expose the `abandon` action. A successful
refresh must observe a ready remote voice whose comparable revision equals
`overwrite_previous_revision`. An advanced revision is reconciled normally;
missing, incomparable, older or still-processing observations grant no action.
The refresh persists `overwrite_abandon_revision` bound to its new local record
revision. Any intervening write invalidates that observation.

The page asks for confirmation that the provider may already have accepted the
update and overwriting again may submit twice. Cancellation sends no request.
`POST /voices/{local_ref}/abandon_overwrite` receives context token, operation ID
and record revision. Under the same per-voice lock as overwrite, it verifies the
stored observation and queries the provider again within a 30-second deadline.
Only a still-equal ready revision permits the atomic context/owner/revision CAS.
This is explicit acceptance of uncertainty, never proof of non-acceptance.

The transition writes `failed` and `overwrite_terminal_reason=user_abandoned_unknown`,
clears the abandonment observation, and retains the operation ID, submission phase,
local reference and character bindings. Its feedback retains `attempt_outcome=unknown`.
It makes no provider mutation and never automatically retries. Old result writes
remain subject to operation/revision CAS; a later overwrite creates a new owner.
Query errors, missing records, new owners, account changes and storage failures
cannot silently unlock the record. Direct overwrite and deletion remain protected
until abandonment has actually persisted.
