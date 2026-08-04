"""Pure, provider-neutral Owner voice policy for the beta experiment."""


class OwnerVoicePolicyDecision:
    """Non-authoritative outcomes emitted by the observation-only policy."""

    FORWARD = "FORWARD"
    HYPOTHETICAL_REJECT = "HYPOTHETICAL_REJECT"


class OwnerVoicePolicyResult:
    """Immutable-by-interface record of one beta policy evaluation."""

    __slots__ = (
        "_candidate_generation",
        "_checkpoint_ms",
        "_decision",
        "_policy_version",
        "_profile_generation",
        "_reason",
    )

    def __init__(
        self,
        *,
        decision: str,
        reason: str,
        candidate_generation: int,
        profile_generation: str,
        checkpoint_ms: int,
    ) -> None:
        self._decision = decision
        self._reason = reason
        self._candidate_generation = candidate_generation
        self._profile_generation = profile_generation
        self._checkpoint_ms = checkpoint_ms
        self._policy_version = OwnerVoiceBetaPolicy.VERSION

    @property
    def decision(self) -> str:
        return self._decision

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def candidate_generation(self) -> int:
        return self._candidate_generation

    @property
    def profile_generation(self) -> str:
        return self._profile_generation

    @property
    def checkpoint_ms(self) -> int:
        return self._checkpoint_ms

    @property
    def policy_version(self) -> str:
        return self._policy_version


class OwnerVoiceBetaPolicy:
    """Evaluate two fixed checkpoints and otherwise fail open.

    The policy has no execution authority. ``HYPOTHETICAL_REJECT`` is an
    observation result, not a Provider, transport, or candidate command.
    """

    VERSION = "beta-v1"
    FIRST_CHECKPOINT_MS = 1_500
    SECOND_CHECKPOINT_MS = 3_000
    SIMILARITY_THRESHOLD = 0.40
    DEFAULT_CANDIDATE_CAPACITY = 256

    __slots__ = ("_candidate_capacity", "_first_low")

    def __init__(self, *, candidate_capacity: int = DEFAULT_CANDIDATE_CAPACITY) -> None:
        if type(candidate_capacity) is not int or candidate_capacity <= 0:
            raise ValueError("candidate_capacity must be a positive integer")
        self._candidate_capacity = candidate_capacity
        self._first_low: dict[tuple[int, int, str], bool] = {}

    @property
    def candidate_capacity(self) -> int:
        return self._candidate_capacity

    @property
    def pending_candidate_count(self) -> int:
        return len(self._first_low)

    def observe(
        self,
        *,
        detector_epoch: int,
        candidate_generation: int,
        profile_generation: str,
        active_detector_epoch: int,
        active_candidate_generation: int,
        active_profile_generation: str,
        checkpoint_ms: int,
        similarity: float,
        enabled: bool = True,
    ) -> OwnerVoicePolicyResult:
        """Return a non-authoritative beta decision for one observation."""

        valid_identity = self._valid_identity(
            detector_epoch,
            candidate_generation,
            profile_generation,
            active_detector_epoch,
            active_candidate_generation,
            active_profile_generation,
        )
        if not valid_identity:
            return self._forward(
                candidate_generation,
                profile_generation,
                checkpoint_ms,
                "stale_or_invalid_generation",
            )

        key = (detector_epoch, candidate_generation, profile_generation)
        if enabled is not True:
            self._first_low.pop(key, None)
            return self._forward(
                candidate_generation,
                profile_generation,
                checkpoint_ms,
                "disabled",
            )
        if not self._valid_observation(checkpoint_ms, similarity):
            self._first_low.pop(key, None)
            return self._forward(
                candidate_generation,
                profile_generation,
                checkpoint_ms,
                "invalid_observation",
            )

        clearly_low = float(similarity) < self.SIMILARITY_THRESHOLD
        if checkpoint_ms == self.FIRST_CHECKPOINT_MS:
            self._first_low.pop(key, None)
            if clearly_low:
                self._remember_first_low(key)
                reason = "awaiting_second_low_observation"
            else:
                reason = "owner_or_uncertain"
            return self._forward(
                candidate_generation,
                profile_generation,
                checkpoint_ms,
                reason,
            )

        first_was_low = self._first_low.pop(key, False)
        if first_was_low and clearly_low:
            return OwnerVoicePolicyResult(
                decision=OwnerVoicePolicyDecision.HYPOTHETICAL_REJECT,
                reason="stable_clear_mismatch",
                candidate_generation=candidate_generation,
                profile_generation=profile_generation,
                checkpoint_ms=checkpoint_ms,
            )
        return self._forward(
            candidate_generation,
            profile_generation,
            checkpoint_ms,
            "owner_or_uncertain",
        )

    def forget_candidate(
        self,
        *,
        detector_epoch: int,
        candidate_generation: int,
        profile_generation: str,
    ) -> None:
        """Forget a candidate-local first observation, if present."""

        self._first_low.pop(
            (detector_epoch, candidate_generation, profile_generation),
            None,
        )

    def reset(self) -> None:
        """Drop all bounded candidate-local observation state."""

        self._first_low.clear()

    @staticmethod
    def _valid_identity(
        detector_epoch: object,
        candidate_generation: object,
        profile_generation: object,
        active_detector_epoch: object,
        active_candidate_generation: object,
        active_profile_generation: object,
    ) -> bool:
        return bool(
            type(detector_epoch) is int
            and detector_epoch >= 0
            and type(active_detector_epoch) is int
            and active_detector_epoch >= 0
            and detector_epoch == active_detector_epoch
            and type(candidate_generation) is int
            and candidate_generation >= 0
            and type(active_candidate_generation) is int
            and active_candidate_generation >= 0
            and candidate_generation == active_candidate_generation
            and type(profile_generation) is str
            and bool(profile_generation.strip())
            and type(active_profile_generation) is str
            and bool(active_profile_generation.strip())
            and profile_generation == active_profile_generation
        )

    @classmethod
    def _valid_observation(cls, checkpoint_ms: object, similarity: object) -> bool:
        return bool(
            type(checkpoint_ms) is int
            and checkpoint_ms in {
                cls.FIRST_CHECKPOINT_MS,
                cls.SECOND_CHECKPOINT_MS,
            }
            and type(similarity) in {int, float}
            and similarity == similarity
            and -1.0 <= similarity <= 1.0
        )

    def _remember_first_low(self, key: tuple[int, int, str]) -> None:
        self._first_low[key] = True
        while len(self._first_low) > self._candidate_capacity:
            oldest = next(iter(self._first_low))
            self._first_low.pop(oldest, None)

    @staticmethod
    def _forward(
        candidate_generation: object,
        profile_generation: object,
        checkpoint_ms: object,
        reason: str,
    ) -> OwnerVoicePolicyResult:
        safe_candidate_generation = (
            candidate_generation
            if type(candidate_generation) is int and candidate_generation >= 0
            else 0
        )
        safe_profile_generation = (
            profile_generation
            if type(profile_generation) is str and profile_generation.strip()
            else "invalid"
        )
        safe_checkpoint_ms = (
            checkpoint_ms
            if type(checkpoint_ms) is int and checkpoint_ms >= 0
            else 0
        )
        return OwnerVoicePolicyResult(
            decision=OwnerVoicePolicyDecision.FORWARD,
            reason=reason,
            candidate_generation=safe_candidate_generation,
            profile_generation=safe_profile_generation,
            checkpoint_ms=safe_checkpoint_ms,
        )


__all__ = [
    "OwnerVoiceBetaPolicy",
    "OwnerVoicePolicyDecision",
    "OwnerVoicePolicyResult",
]
