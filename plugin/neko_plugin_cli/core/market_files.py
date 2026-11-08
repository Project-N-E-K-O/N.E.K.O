"""File restrictions shared by Market release checks and dependency inspection."""
from pathlib import PurePosixPath

# File starts the scan treats as a binary whatever the file is called.
MARKET_BINARY_SIGNATURES = (
    b"MZ", b"\x7fELF", b"PK\x03\x04", b"PK\x05\x06", b"\x1f\x8b",
    b"7z\xbc\xaf\x27\x1c", b"Rar!", b"\x28\xb5\x2f\xfd", b"\x00asm", b"BZh", b"\xfd7zXZ\x00",
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
)
MARKET_BINARY_SUFFIXES = frozenset(
    {
        ".exe", ".dll", ".pyd", ".so", ".dylib", ".wasm", ".zip", ".whl", ".pyc",
        ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".neko-plugin", ".neko-bundle",
    }
)
MARKET_MAX_FILE_BYTES = 2 * 1024 * 1024
# The Market streams these media/model files, exempting them from its per-file limit.
MARKET_ASSET_SUFFIXES = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp3", ".mp4", ".wav",
        ".woff", ".woff2", ".ttf", ".onnx", ".pt", ".safetensors", ".gguf",
    }
)


def market_file_exceeds_size_limit(name: str, size: int) -> bool:
    """Apply the source and release archive's existing per-file size policy."""
    return (size > MARKET_MAX_FILE_BYTES
            and PurePosixPath(name).suffix.lower() not in MARKET_BINARY_SUFFIXES | MARKET_ASSET_SUFFIXES)

def market_rejects_file(name: str, prefix: bytes) -> bool:
    """Apply the existing Market suffix and signature rules to one file."""
    return (PurePosixPath(name).suffix.lower() in MARKET_BINARY_SUFFIXES
            or prefix.startswith(MARKET_BINARY_SIGNATURES)
            or prefix[257:262] == b"ustar")
