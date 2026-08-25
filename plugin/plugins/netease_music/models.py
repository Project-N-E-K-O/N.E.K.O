"""Data contracts for the anonymous NetEase music provider."""

from __future__ import annotations

from dataclasses import dataclass

from plugin.sdk.plugin import tr
from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlayRequest(BaseModel):
    """Public plugin-entry request.  The model never accepts delivery details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(
        min_length=1,
        max_length=100,
        description=tr(
            "entry.play.param.query",
            default="歌曲名，可附带歌手或版本",
        ),
    )

    @field_validator("query", mode="before")
    @classmethod
    def _strip_query(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


@dataclass(frozen=True, slots=True)
class SongCandidate:
    """A sanitized search candidate returned by NetEase."""

    song_id: int
    name: str
    artist: str
    album: str = ""
    fee: int | None = None
    artist_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedMedia:
    """A probed HTTPS media URL and its exact allow-list hostname."""

    url: str
    hostname: str
