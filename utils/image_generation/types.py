"""Consumer-independent image generation contract."""
from dataclasses import dataclass, field


class ImageGenerationError(Exception):
    """Stable, sanitized error code; never contains prompts, keys or provider bodies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ImageRequest:
    prompt: str = field(repr=False)
    size: str = "1024x1024"


@dataclass(frozen=True)
class GeneratedImage:
    """Exactly one of data/url. URLs expire; consumers own retrieval and storage.

    Provider URLs are untrusted network locations, not authorization to fetch
    internal resources. The generation layer never downloads or displays them.
    """
    data: bytes | None = field(default=None, repr=False)
    url: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class ImageResult:
    provider: str
    model: str
    image: GeneratedImage = field(repr=False)
