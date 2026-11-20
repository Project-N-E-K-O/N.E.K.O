"""Read public vocabulary and community metadata independently of the user profile."""

import asyncio
from pathlib import Path
from time import monotonic

import httpx

from utils.proactive_preferences.preference_model import normalize, valid_feature
from utils.file_utils import atomic_write_bytes


_FAILURE_TTL = 30
_PUBLIC_WORDS_REVISION = "525f9b560de45753a5ea01069454e72e9aa541c6"
_PUBLIC_WORDS_URL = (
    "https://raw.githubusercontent.com/hermitdave/FrequencyWords/"
    f"{_PUBLIC_WORDS_REVISION}/content/2018"
)
_PUBLIC_WORDS_LOCALES = {"zh_cn", "zh_tw", "en", "ja", "ko", "ru", "es", "pt"}
_PUBLIC_WORDS_MAX_BYTES = 2_000_000
_word_cache: dict[str, tuple[float, frozenset[str]]] = {}
_word_locks: dict[str, asyncio.Lock] = {}


def _parse_public_words(body: bytes | bytearray) -> frozenset[str]:
    # FrequencyWords content is CC BY-SA 4.0. Read published frequency data
    # independently of the profile. Only complete, common dictionary words
    # can enter the general outbound search vocabulary.
    words = set()
    for line in body.decode("utf-8").splitlines():
        word, _, count = line.rpartition(" ")
        if count.isdecimal() and int(count) >= 20:
            label = normalize(word)
            if label.isalpha() and valid_feature(label):
                words.add(label)
    return frozenset(words)


def _read_cached_words(locale: str) -> tuple[Path | None, frozenset[str]]:
    from utils.config_manager import get_runtime_config_path

    path = None
    try:
        path = Path(
            get_runtime_config_path(
                f"proactive-vocabulary-{_PUBLIC_WORDS_REVISION[:8]}-{locale}.txt"
            )
        )
        with path.open("rb") as stream:
            body = stream.read(_PUBLIC_WORDS_MAX_BYTES + 1)
        if len(body) <= _PUBLIC_WORDS_MAX_BYTES:
            return path, _parse_public_words(body)
    except (OSError, ValueError):
        pass
    return path, frozenset()


async def fetch_public_search_words(language: str) -> frozenset[str]:
    """Load bounded public vocabulary by locale carrying no user information."""
    code = language.lower().replace("_", "-")
    locale = (
        "zh_tw"
        if code in {"zh-tw", "zh-hk", "zh-hant"}
        else ("zh_cn" if code.startswith("zh") else code.split("-", 1)[0])
    )
    if locale not in _PUBLIC_WORDS_LOCALES:
        return frozenset()
    lock = _word_locks.setdefault(locale, asyncio.Lock())
    async with lock:
        now = monotonic()
        if (cached := _word_cache.get(locale)) and now < cached[0]:
            return cached[1]
        cache_path, words = await asyncio.to_thread(_read_cached_words, locale)
        if words:
            _word_cache[locale] = (now + 86_400, words)
            return words
        url = f"{_PUBLIC_WORDS_URL}/{locale}/{locale}_50k.txt"
        try:
            body = bytearray()
            async with asyncio.timeout(8):
                async with httpx.AsyncClient(follow_redirects=False) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > _PUBLIC_WORDS_MAX_BYTES:
                                raise ValueError("oversized_public_vocabulary")
                            body.extend(chunk)
            words = await asyncio.to_thread(_parse_public_words, body)
        except (httpx.HTTPError, TimeoutError, ValueError):
            pass
        if words and cache_path is not None:
            try:
                await asyncio.to_thread(atomic_write_bytes, cache_path, bytes(body))
            except OSError:
                pass
        _word_cache[locale] = (now + (86_400 if words else _FAILURE_TTL), words)
        return words
