"""Sample-exact delivery sidecars; no speaker or provider implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from main_logic.voice_turn.interception_events import InterceptionDeliveryStage


def _add_range(ranges: list[tuple[int, int]], start: int, end: int) -> None:
    merged = []
    for left, right in sorted([*ranges, (start, end)]):
        if merged and left <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    ranges[:] = merged


@dataclass(eq=False, slots=True)
class AudioDeliveryTag:
    """Own one original audio interval across trim, resample and chunking.

    All ranges are relative to this tag, never inferred from cumulative wire
    byte counters. Only complete interval evidence produces WRITTEN/NOT_SENT.
    A partly written and partly discarded interval remains UNKNOWN.
    """

    sample_count: int
    on_stage: Callable[[InterceptionDeliveryStage], bool | None]
    _written: list[tuple[int, int]] = field(default_factory=list, init=False)
    _dropped: list[tuple[int, int]] = field(default_factory=list, init=False)
    _queued: list[tuple[int, int]] = field(default_factory=list, init=False)
    _owned: list[tuple[int, int]] = field(default_factory=list, init=False)
    _reported: set[InterceptionDeliveryStage] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if type(self.sample_count) is not int or self.sample_count <= 0:
            raise ValueError("audio delivery tag needs a positive sample count")
        if not callable(self.on_stage):
            raise TypeError("audio delivery tag requires an observer")

    @property
    def settled(self) -> bool:
        return bool(self._reported & {
            InterceptionDeliveryStage.TRANSPORT_OWNED,
            InterceptionDeliveryStage.NOT_SENT,
        })

    def observe(self, start_sample: int, end_sample: int, stage: InterceptionDeliveryStage) -> None:
        if not (0 <= start_sample < end_sample <= self.sample_count):
            raise ValueError("audio delivery subrange lies outside its tag")
        if type(stage) is not InterceptionDeliveryStage:
            raise TypeError("audio delivery stage required")
        if stage is InterceptionDeliveryStage.PROVIDER_CONFIRMED:
            raise ValueError("audio writes cannot infer provider confirmation")
        if (InterceptionDeliveryStage.UNKNOWN in self._reported and stage in {
            InterceptionDeliveryStage.TRANSPORT_WRITTEN, InterceptionDeliveryStage.TRANSPORT_OWNED,
        }):
            # A cancelled/failed attempt is terminally uncertain. Its late
            # local completion cannot resurrect or transfer the old output;
            # only an explicit provider receipt can settle the original debt.
            return
        if stage in {InterceptionDeliveryStage.TRANSPORT_WRITTEN, InterceptionDeliveryStage.TRANSPORT_OWNED} and any(
            left < end_sample and right > start_sample for left, right in self._dropped
        ):
            raise ValueError("discarded audio cannot acquire transport write evidence")
        if (self._written == [(0, self.sample_count)]
                and stage in {InterceptionDeliveryStage.NOT_SENT, InterceptionDeliveryStage.UNKNOWN}):
            return
        if stage is InterceptionDeliveryStage.TRANSPORT_OWNED and not any(
            left <= start_sample and right >= end_sample for left, right in self._written
        ):
            raise ValueError("transport ownership requires exact preceding write evidence")
        if stage in {
            InterceptionDeliveryStage.TRANSPORT_WRITTEN,
            InterceptionDeliveryStage.TRANSPORT_OWNED,
            InterceptionDeliveryStage.NOT_SENT,
            InterceptionDeliveryStage.QUEUED,
        }:
            target = {
                InterceptionDeliveryStage.TRANSPORT_WRITTEN: self._written,
                InterceptionDeliveryStage.NOT_SENT: self._dropped,
                InterceptionDeliveryStage.QUEUED: self._queued,
                InterceptionDeliveryStage.TRANSPORT_OWNED: self._owned,
            }[stage]
            _add_range(target, start_sample, end_sample)
            complete = target == [(0, self.sample_count)]
            if not complete:
                if self._written and self._dropped:
                    self._report(InterceptionDeliveryStage.UNKNOWN)
                return
            if stage is InterceptionDeliveryStage.NOT_SENT and self._written:
                self._report(InterceptionDeliveryStage.UNKNOWN)
                return
        if (stage is InterceptionDeliveryStage.UNKNOWN
                and InterceptionDeliveryStage.TRANSPORT_WRITTEN in self._reported):
            return
        self._report(stage)

    def _report(self, stage: InterceptionDeliveryStage) -> None:
        if stage not in self._reported:
            if self.on_stage(stage) is False:
                raise RuntimeError("audio delivery owner rejected its receipt")
            self._reported.add(stage)


@dataclass(frozen=True, slots=True)
class AudioDeliverySpan:
    tag: AudioDeliveryTag
    start_sample: int
    end_sample: int

    def __post_init__(self) -> None:
        if type(self.tag) is not AudioDeliveryTag:
            raise TypeError("audio delivery span requires its original tag")
        if not 0 <= self.start_sample < self.end_sample <= self.tag.sample_count:
            raise ValueError("invalid audio delivery span")

    @property
    def samples(self) -> int:
        return self.end_sample - self.start_sample

    def observe(self, stage: InterceptionDeliveryStage) -> None:
        self.tag.observe(self.start_sample, self.end_sample, stage)


def slice_delivery_spans(
    spans: tuple[AudioDeliverySpan, ...], offset: int, samples: int,
) -> tuple[AudioDeliverySpan, ...]:
    """Slice exact payload positions while retaining original tag coordinates."""
    if offset < 0 or samples < 0:
        raise ValueError("invalid audio delivery slice")
    result = []
    cursor = 0
    for span in spans:
        left, right = max(offset, cursor), min(offset + samples, cursor + span.samples)
        if left < right:
            result.append(AudioDeliverySpan(
                span.tag, span.start_sample + left - cursor,
                span.start_sample + right - cursor,
            ))
        cursor += span.samples
    if spans and sum(span.samples for span in result) != samples:
        raise ValueError("audio delivery metadata does not cover its payload")
    return tuple(result)


__all__ = ["AudioDeliveryTag", "AudioDeliverySpan", "slice_delivery_spans"]
