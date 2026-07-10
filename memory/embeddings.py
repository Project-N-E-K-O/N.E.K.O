# -*- coding: utf-8 -*-
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

"""
EmbeddingService — Tier 0 of the memory hierarchy: vector embeddings.

Provides ``embed(text)`` / ``embed_batch(texts)`` over the local CPU ONNX
text-retrieval embedding profile. Used by:

  * fact dedup at write time (cosine > threshold → LLM arbitration queue)
  * persona / reflection retrieval (cosine top-K → LLM rerank precandidates)

This module owns the *fallback gate*. The whole feature degrades to
zero-cost if any of the following holds:

  * ``onnxruntime`` cannot be imported
  * the ONNX model file is missing on disk
  * detected RAM < ``VECTORS_MIN_RAM_GB``
  * the user set ``VECTORS_ENABLED = False``
  * ``auto`` quantization when AVX-VNNI is **confirmed absent** (no INT8
    fast-path; default installs omit the large FP32 ONNX bundle — operators
    who need vectors then pin ``int8`` or ship FP32 weights + ``fp32``)
  * loading or any per-call inference raised an exception (sticky disable)

Explicit ``fp32`` loads ``model.onnx`` when present (manual / optional bundle).

When disabled, ``is_available()`` returns False; callers MUST check it
before invoking ``embed()`` / ``embed_batch()`` and fall back to the
pre-vector code path. The disable is process-local and final — once
``DISABLED`` we don't retry within the same process.

Lazy load: the model file is NOT loaded at startup. The
warmup is gated on the first ``request_load()`` call from
memory_server's post-ready hook (after the frontend has finished its
greeting / prominent drain). Until ``READY``, ``embed()`` returns None.

Embedding cache invalidation lives on the entry dict itself:

  * ``embedding``: list[float] | None
  * ``embedding_text_sha256``: str | None
  * ``embedding_model_id``: str | None

A reader treats the cached embedding as valid only when both fingerprints
match the current text + service ``model_id()`` — same pattern as the
``token_count`` cache PR-3 introduced.
"""
from __future__ import annotations

import asyncio
import base64
import enum
import hashlib
import logging
import os
import platform
import re
import sys
from typing import Any

# 走 get_module_logger(..., "Memory") 把本模块日志归到 N.E.K.O.Memory.*，
# 否则 ``logging.getLogger(__name__)`` 产生的 ``memory.embeddings`` logger
# 落在 N.E.K.O 命名空间之外，向上只能传到无 handler 的 root，导致
# "EmbeddingService: ready / vectors disabled (reason)" 这类关键状态行
# 永远不进 Memory 日志文件——线上排"为啥向量召回是空的"时根本看不到原因。
try:
    from utils.logger_config import get_module_logger
    logger = get_module_logger(__name__, "Memory")
except Exception:  # noqa: BLE001 — 极早期/裸测试环境拿不到 config，退回裸 logger
    logger = logging.getLogger(__name__)


# ── on-disk vector encoding ──────────────────────────────────────────
#
# A 256d float vector serialized as JSON ``list[float]`` runs ~5.3 KB
# (each float prints to ~21 chars after Python's repr). We instead
# store ``base64(fp16_bytes)`` — raw little-endian fp16 of the
# L2-normalized vector. Decode is:
#
#     raw = b64decode(s)
#     vec = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
#
# Total bytes = ``2 * dim``; base64 ≈ ``ceil(2 * dim * 4/3)``. At 256d
# that is ~684 chars vs ~5.3 KB before — ~8× smaller, and the decoder
# lands in a contiguous numpy buffer so the recall path can stack
# candidates into a matrix and use ``M @ q.T`` instead of the pure-
# Python cosine loop.
#
# Why fp16 instead of int8: L2-normalized vectors have typical
# per-axis magnitudes ~1/√dim; in that range fp16's mantissa step is
# ~2⁻¹⁴ ≈ 6e-5, giving cosine error ~5e-4 over a 256-dim dot — well
# below LLM-rerank perceptibility. int8 with a per-vector scale would
# only buy 2× more compression but trade in quantization noise (~0.4%
# per dim, ~1% cumulative cosine), an extra fp16 scale prefix, the
# clip/round machinery, and a fresh attack surface around NaN scales.
# At our scale (small thousand-entry corpus) the marginal compression
# is invisible; simpler wire format wins.


# ── Config knobs (mirrored in config/__init__.py for centralised tuning) ──
# These default values are kept in this module so the service stays
# importable in test harnesses that bypass the full app config.

DEFAULT_VECTORS_ENABLED = True
DEFAULT_VECTORS_EMBEDDING_DIM = "auto"            # "auto" | 32 | 64 | 128 | 256 | 512 | 768
DEFAULT_VECTORS_MODEL_PROFILE_ID = "local-text-retrieval-v1"
DEFAULT_VECTORS_QUANTIZATION = "auto"             # "auto" | "int8" | "fp32"
DEFAULT_VECTORS_MIN_RAM_GB = 4.0
DEFAULT_VECTORS_MODEL_DIR_NAME = "embedding_models"
DEFAULT_VECTORS_MAX_LENGTH = 1024

# 推理峰值上限。激活内存近似 ``batch × seq × hidden × layers``;固定
# batch + pad-to-longest 让一条长文本就能把激活顶到上百 GB(实测:
# 用户粘贴一段长 recent 进记忆,凛天上线那个 sweep 把 RSS 从 1.1 GB
# 顶到 12.4 GB)。改成 token 预算 ``batch × max_len ≤ _INFER_BATCH_MAX_TOKENS``,
# 长文本时 batch 自动缩到 1~2 条,峰值有硬上界。
#
# 选 16384:在新 max_length=1024 下,一桶能放下 16 条满长(等同
# worker BATCH_SIZE=16 的原行为),正常路径吞吐零损失;当历史数据 /
# 测试 / 自定义 profile 把 max_length 顶到旧 8192 时也只允许 2 条
# 一桶,峰值仍可控。"""
_INFER_BATCH_MAX_TOKENS = 16384

# Matryoshka discrete steps supported by the default local profile.
_DIM_STEPS = (32, 64, 128, 256, 512, 768)


class EmbeddingState(enum.Enum):
    """Service lifecycle. Transitions are forward-only except DISABLED,
    which is sticky: once we decide vectors are off we never re-enable
    within the same process (otherwise a transient OOM at load could
    flip on/off mid-session and corrupt cache invariants)."""
    INIT = "init"
    LOADING = "loading"
    READY = "ready"
    DISABLED = "disabled"


class _DisableReason(enum.Enum):
    """Why ``is_available()`` is False. Surfaced in the startup log so
    operators can tell apart "user opted out" from "we couldn't load"."""
    NONE = "none"
    USER_DISABLED = "user_disabled_via_config"
    NO_ONNXRUNTIME = "onnxruntime_not_importable"
    # Distinct from NO_ONNXRUNTIME so operators see exactly which dep
    # is missing in the startup log — the two libs ship separately and
    # the install commands diverge.
    NO_TOKENIZERS = "tokenizers_not_importable"
    NO_MODEL_FILE = "model_file_missing"
    # ``enable_truncation`` 失败 = tokenizer 实例存在但截断契约没能建立。
    # 必须当成 load 失败(Codex / CodeRabbit 在 PR #1585 联合指出 P2):
    # ``model_id`` 现在把 max_length 编进 cache id,如果继续 ready,长文本
    # 会被「未截断」地编码、却 stamp 成 ``-mlen1024``,跟未来真的 1024 截
    # 断的 vector 在同一 id 下混存,is_cached_embedding_valid 会判它们「同
    # 一空间」做 cosine,召回质量静默漂移。宁可 disable 也不能错配 cache。
    TRUNCATION_SETUP_FAILED = "tokenizer_truncation_setup_failed"
    # Default bundle is INT8. ``auto`` picks INT8 when *any* usable SIMD int8
    # path exists: AVX-VNNI (fast) OR plain AVX2 (slower but fine for our nano
    # model + small corpus). Only when BOTH are confirmed absent (SSE-only
    # ancient/low-end CPUs) does auto disable — see NO_SIMD_INT8_PATH.
    AVX_VNNI_REQUIRED_FOR_INT8 = "avx_vnni_required_for_int8_bundle"
    # No AVX-VNNI *and* no AVX2 (both confirmed): int8 kernels would fall to
    # SSE, too slow to justify auto-enabling. Distinct from the VNNI reason so
    # the log separates "no fast path at all" from the now-supported "no VNNI
    # but AVX2 present" case (Haswell 2013+ / Zen+).
    NO_SIMD_INT8_PATH = "no_avx2_or_vnni_for_int8"
    # CPU brand string is on _CPU_BRAND_BLOCKLIST: it advertises a usable
    # int8 SIMD path but crashes onnxruntime/MLAS at runtime (illegal
    # instruction). Disabled *before* the session loads so the crash never
    # happens; override per machine with NEKO_VECTORS_FORCE_ENABLE=1.
    CPU_BLOCKLISTED = "cpu_on_known_bad_blocklist"
    LOW_RAM = "ram_below_threshold"
    LOAD_ERROR = "load_raised"
    INFERENCE_ERROR = "inference_raised"


# ── helpers ──────────────────────────────────────────────────────────


def _encode_vector_fp16(vector) -> str:
    """Encode a float vector as ``base64(fp16_bytes)``.

    Accepts list/tuple/numpy. fp16 has dynamic range up to ±65504, so
    L2-normalized vectors (per-axis magnitudes < 1) can never overflow
    on cast — we don't need a per-vector scale prefix the way int8
    quantization would.
    """
    import numpy as np
    arr = np.asarray(vector, dtype=np.float16).ravel()
    return base64.b64encode(arr.tobytes()).decode("ascii")


def _decode_vector_fp16(encoded: str):
    """Inverse of :func:`_encode_vector_fp16`. Returns a numpy fp32 array.

    Returns None on any decoding failure — corrupt cache fields fall
    through to the "no embedding" path rather than raising up into the
    retrieval/dedup loops.

    Strict-validate the base64 payload (``validate=True``): the looser
    setting silently skips non-alphabet bytes, letting a garbage-suffix
    payload decode to plausible-but-wrong values. Reject odd-length
    raw buffers (fp16 must align to 2 bytes — odd length means
    truncation or corruption) and any non-finite element after cast
    (NaN / ±Inf would otherwise propagate through every dot product
    the decoded vector touches).
    """
    import numpy as np
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception:  # noqa: BLE001 — malformed base64 → treat as missing
        return None
    if len(raw) % 2 != 0:
        return None
    decoded = np.frombuffer(raw, dtype=np.float16).astype(np.float32)
    if decoded.size == 0:
        return decoded
    if not np.isfinite(decoded).all():
        return None
    return decoded


def decode_embedding(emb: Any):
    """Public helper: turn a persisted ``embedding`` field into a numpy
    fp32 array, regardless of whether the row carries the new base64
    form, a legacy ``list[float]``, an already-decoded numpy array, or
    None / empty.

    Returns None when the field is missing or unreadable. Used by
    cosine helpers and by recall's batched dot-product path.
    """
    if emb is None:
        return None
    import numpy as np
    if isinstance(emb, np.ndarray):
        if emb.size == 0:
            return None
        return emb.astype(np.float32, copy=False)
    if isinstance(emb, str):
        if not emb:
            return None
        return _decode_vector_fp16(emb)
    if isinstance(emb, (list, tuple)):
        if not emb:
            return None
        try:
            return np.asarray(emb, dtype=np.float32)
        except (TypeError, ValueError):
            return None
    return None


# Anchor on the trailing ``-<dim>d-<quant>`` form emitted by
# :func:`build_model_id` (e.g. ``local-text-retrieval-v1-256d-int8``).
# Anchoring at end-of-string + a known quantization keyword guards
# against profile names that happen to contain their own ``-Nd-``
# segment (e.g. an upstream profile like ``model-384d-v2``); without
# the anchor, ``re.search`` would pick the *first* match (384) rather
# than the actual runtime dim (256), and is_cached_embedding_valid
# would reject every freshly stamped vector forever (size mismatch),
# pinning the worker into an infinite re-embed loop. Codex review
# PR #1147.
#
# ``-mlen<N>`` 后缀(PR #1585):tokenizer 截断长度是 embedding 输入空间的
# 一部分 —— 同一段 2K-token 文本在 max_length=8192 下喂全量、在
# max_length=1024 下只喂前缀,得到的向量在同一模型下也不可比。把 mlen
# 编进 model_id,降 max_length 时旧 cache 自动失效,worker 重新 embed,
# 避免不同 token 输入空间的向量混用做 cosine。后缀可选(``mlen<N>?``)是
# 为了向后兼容已经在用户磁盘上的老 cache id —— 它们解析 dim 仍然成功,
# 在 is_cached_embedding_valid 里会因为 model_id 字符串比较失败被识别
# 为 stale,然后被 worker 重新 stamp 上带 mlen 的新 id。
_MODEL_ID_DIM_RE = re.compile(r"-(\d+)d-(?:int8|fp32)(?:-mlen\d+)?$")


def parse_dim_from_model_id(model_id: str | None) -> int | None:
    """Extract the embedding dimension from a model_id, or None if the
    id can't be parsed.

    ``embedding_model_id`` is built by :func:`build_model_id` and always
    has the shape ``<profile>-<dim>d-<quant>`` where ``quant`` is a
    fixed enum (``int8`` / ``fp32``). The regex anchors on that
    trailing form so a profile name that itself contains ``-Nd-``
    can't shadow the runtime dim segment.
    """
    if not model_id or not isinstance(model_id, str):
        return None
    m = _MODEL_ID_DIM_RE.search(model_id)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (TypeError, ValueError):
        return None


def _embedding_text_sha256(text: str) -> str:
    """Stable fingerprint used for ``embedding_text_sha256`` cache keys.

    Same scheme as ``token_count_text_sha256`` — utf-8 then full sha256.
    Truncation lives at consumer sites only; we keep the full hex so a
    future migration to a longer prefix doesn't require recomputing all
    cached values.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def detect_total_ram_gb() -> float | None:
    """Return total system RAM in GiB or None on detection failure.

    Detection failure is treated as "unknown" upstream — we conservatively
    assume insufficient RAM and disable vectors, since a runaway load on
    a tiny VM is worse than missing a feature on a workstation that
    happens to lack psutil.
    """
    try:
        import psutil
        return psutil.virtual_memory().total / (1024 ** 3)
    except Exception as e:  # noqa: BLE001 — psutil should always be available
        logger.warning("EmbeddingService: psutil RAM detection failed: %s", e)
        return None


# Known-good thresholds for CPU microarchitectures that ship AVX-VNNI.
# Family/model is a stable hardware identifier readable from
# ``HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\Identifier`` on
# Windows and ``/proc/cpuinfo`` on Linux — a plain registry / text read,
# nothing an AV heuristic can mistake for a low-level CPU-probing trick.
#
# Conservative: a "yes" from the table is authoritative (confirmed=True);
# an "I don't know" falls through to numpy CPU features / /proc/cpuinfo so
# brand-new microarchitectures aren't false-negatived just because the
# table predates them.
_INTEL_VNNI_MIN_MODEL_FAMILY_6 = 0x97  # Alder Lake — also covers Raptor,
                                       # Meteor, Arrow, Lunar, Panther
                                       # Lake; Sapphire/Emerald Rapids.

# CPUs that advertise a usable int8 SIMD path through every numeric signal
# we trust (family/model, numpy AVX2/VNNI flags) yet still crash
# onnxruntime at runtime with an illegal-instruction fault. Matched as a
# case-insensitive substring of the CPU *brand string* ("model name" on
# Linux / ``ProcessorNameString`` on Windows), because family/model can not
# single out one SKU — e.g. Haswell-EP E5-2666 v3 shares 06_3FH with the
# entire Haswell-EP cohort, the bulk of which run fine. Brand-string
# matching is the fragile py-cpuinfo pattern this module otherwise avoids,
# so keep this list SHORT and evidence-backed (a real SIGILL in
# onnxruntime's .so, confirmed via ``dmesg``), never a catch-all guess.
#
# A hit disables the WHOLE onnxruntime session — int8 *and* fp32 — not just
# the int8 kernel. The root cause is not yet pinned to a specific kernel, so
# we can not assume the fp32 weights (if an operator shipped them) are safe
# on this silicon; shipping a path we believe still SIGILLs would defeat the
# guard. The fp32 / forced-load recovery path is not removed, only gated:
# ``NEKO_VECTORS_FORCE_ENABLE=1`` overrides the blocklist per machine.
_CPU_BRAND_BLOCKLIST = (
    "e5-2666 v3",  # AWS-custom Haswell-EP; user-reported onnxruntime crash (SIGILL)
)

# AMD Family 0x19 is shared between Zen 3 (no AVX-VNNI) and Zen 4 (yes), so
# family alone is not enough — gate on the documented Zen 4 model ranges
# instead. Zen 5 lives on Family 0x1A and every shipped part has VNNI, so
# Family >= 0x1A is a straight yes; Family < 0x19 is a straight no
# (Zen 1 / Zen 2 at 0x17, older microarchitectures at 0x15 / 0x16).
_AMD_ZEN4_MODEL_RANGES_FAMILY_19 = (
    (0x10, 0x1F),  # Genoa / Bergamo / Storm Peak (Zen 4 server, EPYC 9004,
                   # Threadripper 7000)
    (0x60, 0x6F),  # Raphael / Dragon Range (Zen 4 desktop / mobile HX,
                   # Ryzen 7000 / 7045)
    (0x70, 0x7F),  # Phoenix / Phoenix 2 / Hawk Point (Zen 4 mobile,
                   # Ryzen 7040 / 8040)
    (0xA0, 0xAF),  # Zen 4c derivatives reserved by AMD's CPUID
                   # documentation; included for forward compat so a
                   # future Family-19h Zen-4c part isn't false-negatived.
)

# Zen 3 model ranges on Family 0x19 — definitively no AVX-VNNI. Listed
# explicitly so we can answer ``(False, True)`` (confirmed-no) for them
# rather than ``(False, False)`` (inconclusive), which would let
# ``auto`` quantization optimistically pick int8 on a CPU that genuinely
# can not run it well.
_AMD_ZEN3_MODEL_RANGES_FAMILY_19 = (
    (0x00, 0x0F),  # Milan EPYC / Vermeer (Ryzen 5000 desktop)
    (0x20, 0x2F),  # Cezanne / Lucienne / Barceló (Ryzen 5000G/U APU)
    (0x30, 0x3F),  # Milan-X / Trento (EPYC 7003X)
    (0x40, 0x4F),  # Rembrandt (Ryzen 6000 mobile, Zen 3+)
    (0x50, 0x5F),  # Barceló-R refresh
)


def _read_cpu_family_model() -> tuple[str, int, int] | None:
    """Return ``(vendor, family, model)`` from a plain registry/text read,
    or None when neither the Windows registry nor ``/proc/cpuinfo``
    answered.

    Vendor strings are normalised to the CPUID brand strings
    (``GenuineIntel`` / ``AuthenticAMD``) so the lookup table is indexed
    identically across OSes.
    """
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as k:
                vendor = winreg.QueryValueEx(k, "VendorIdentifier")[0]
                ident = winreg.QueryValueEx(k, "Identifier")[0]
            m = re.search(r"Family (\d+) Model (\d+)", ident)
            if not m:
                return None
            return vendor, int(m.group(1)), int(m.group(2))
        if system == "Linux":
            vendor, family, model = None, None, None
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("vendor_id"):
                        vendor = line.split(":", 1)[1].strip()
                    elif line.startswith("cpu family"):
                        try:
                            family = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            # Malformed numeric field — keep scanning;
                            # the surrounding ``vendor/family/model``
                            # presence check below treats a missing
                            # field as "no answer" and returns None.
                            pass
                    # ``model name`` shares the prefix; check ``model`` first
                    # but skip when the line is actually ``model name``.
                    elif line.startswith("model") and not line.startswith("model name"):
                        try:
                            model = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            # Same fallthrough as above — better to
                            # answer "I don't know" than to crash CPU
                            # detection on an exotic /proc/cpuinfo.
                            pass
                    if vendor and family is not None and model is not None:
                        break
            if vendor is None or family is None or model is None:
                return None
            return vendor, family, model
    except Exception:
        return None
    return None


def _read_cpu_brand_string() -> str | None:
    """Return the CPU brand/marketing string (e.g.
    ``"Intel(R) Xeon(R) CPU E5-2666 v3 @ 2.90GHz"``) from a plain
    registry / text read, or None when neither source answers.

    The sole consumer is :func:`_cpu_is_blocklisted`. Brand-string parsing
    is the fragile py-cpuinfo pattern this module avoids everywhere else
    (numeric family/model is preferred), but it is the *only* signal that
    pins down one SKU — family/model lumps a whole microarchitecture cohort
    together — so it is fenced off to this one narrow use.
    """
    system = platform.system()
    try:
        if system == "Windows":
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0",
            ) as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0]
        if system == "Linux":
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
    except Exception:
        return None
    return None


def _cpu_is_blocklisted() -> bool:
    """True when the CPU brand string matches :data:`_CPU_BRAND_BLOCKLIST`
    and the operator has not set ``NEKO_VECTORS_FORCE_ENABLE``.

    The override is read straight from the environment (not via the
    ``config`` module), so it keeps working after the app is Nuitka-compiled:
    the frozen module is baked into the binary, the process environment is
    not. It follows the documented env convention — ``NEKO_`` prefix, with
    the bare name accepted as a fallback, matching config's ``_read_str_env``
    key order: the FIRST env name that is present and non-empty wins, so an
    explicit ``NEKO_VECTORS_FORCE_ENABLE`` takes precedence over the bare
    name even when its value is falsey (it does not fall through). This is
    the per-machine escape hatch for a false positive (a listed chip that
    actually runs fine, or a later microcode fix) and the way to re-try a
    forced fp32 / int8 load on a blocklisted host.
    """
    for _key in ("NEKO_VECTORS_FORCE_ENABLE", "VECTORS_FORCE_ENABLE"):
        _raw = os.getenv(_key)
        if _raw is None or not _raw.strip():
            continue
        # First present-and-non-empty key decides; a later key (the bare
        # name) never overrides an explicitly-set earlier one, even if that
        # value is falsey — so NEKO_ precedence holds.
        if _raw.strip().lower() in ("1", "true", "yes", "on"):
            return False        # force-enabled → never blocklisted
        break                   # explicit falsey override → fall to brand check
    return _brand_blocklisted()


def _brand_blocklisted() -> bool:
    """Pure brand-string side of :func:`_cpu_is_blocklisted` (no env
    override): True iff the CPU brand matches :data:`_CPU_BRAND_BLOCKLIST`."""
    brand = _read_cpu_brand_string()
    if not brand:
        return False
    low = brand.lower()
    return any(bad in low for bad in _CPU_BRAND_BLOCKLIST)


def _vnni_via_family_model() -> tuple[bool, bool]:
    """Authoritative VNNI answer via CPU family/model lookup.

    Returns ``(has_vnni, confirmed)``. ``confirmed=True`` means the
    family/model fell inside (or strictly below) a known range and the
    answer is final. ``(False, False)`` means the table doesn't know —
    let the caller fall through to numpy CPU features / ``/proc/cpuinfo``.

    Replaces the deleted low-level CPUID probe. Strictly less precise
    in one direction (brand-new microarchitectures that ship before the
    table is updated stay inconclusive instead of authoritative), but
    the consumer's ``auto`` quantization path treats inconclusive as
    "pick int8 optimistically" — the same behaviour the CPUID probe
    produced for sandboxes that refused executable allocation.
    """
    info = _read_cpu_family_model()
    if info is None:
        return False, False
    vendor, family, model = info
    if vendor == "GenuineIntel":
        # All Intel client microarchitectures shipping AVX-VNNI live in
        # Family 6 with model >= Alder Lake's 0x97. Earlier Family-6
        # parts that carry AVX512-VNNI (Ice Lake server, Tiger/Rocket
        # Lake, Sapphire Rapids) are detected by numpy's ``AVX512VNNI``
        # feature flag and don't need enumeration here.
        if family == 6 and model >= _INTEL_VNNI_MIN_MODEL_FAMILY_6:
            return True, True
    elif vendor == "AuthenticAMD":
        # Zen 5 (Family 0x1A, and any later family AMD ships) — every
        # part has AVX-VNNI.
        if family >= 0x1A:
            return True, True
        # Family 0x19 is shared between Zen 3 (no VNNI) and Zen 4 (yes),
        # so we have to look at the model. The Zen-4 ranges below are
        # AMD's documented CPUID model groupings.
        if family == 0x19:
            for lo, hi in _AMD_ZEN4_MODEL_RANGES_FAMILY_19:
                if lo <= model <= hi:
                    return True, True
            for lo, hi in _AMD_ZEN3_MODEL_RANGES_FAMILY_19:
                if lo <= model <= hi:
                    return False, True
            # An unmapped Family-19h model (e.g. a future stepping not
            # yet covered by AMD's published groupings) stays
            # inconclusive so numpy's feature map can have a try
            # instead of silently picking the wrong path.
            return False, False
        # Family < 0x19 covers Zen 1 / Zen 2 (0x17), Excavator (0x15) and
        # earlier — none have AVX-VNNI, so the answer is definitive.
        return False, True
    return False, False


def _numpy_cpu_features() -> dict | None:
    """Read numpy's runtime CPU SIMD feature map, or None if unavailable.

    numpy probes CPU features in its compiled C core at import (for kernel
    dispatch) and exposes the result as ``__cpu_features__`` — a plain
    ``{feature_name: bool}`` dict. We read it instead of calling py-cpuinfo
    because py-cpuinfo probes the CPU by generating a tiny native routine at
    runtime and calling into it from a child process — a low-level trick that
    antivirus heuristics (notably Huorong) match and act on, quarantining
    whichever Python file happens to be on the import stack at the time. The
    launcher starts the memory server as a spawn child that re-imports this
    module, so on Windows that file is ``embeddings.py`` — which is why the AV
    report blamed it (the report's ``--multiprocessing-fork`` line is that
    spawn child, not the probe). numpy's detection is pure compiled C with
    none of that, so the heuristic has nothing to bite, and numpy is already a
    hard dependency we import for inference. See PR #1437 / #1525 and this
    module's git history for the full write-up.

    Returns None on exotic builds where the private attribute is gone, so
    callers fall through to ``/proc/cpuinfo`` (Linux) or stay inconclusive.
    """
    import warnings
    # numpy >= 2.0 renamed the private module ``numpy.core`` → ``numpy._core``
    # and warns on the old path; try the new spelling first, suppress the
    # DeprecationWarning on the legacy one for numpy 1.x.
    for modpath in ("numpy._core._multiarray_umath",
                    "numpy.core._multiarray_umath"):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                mod = __import__(modpath, fromlist=["__cpu_features__"])
            feats = getattr(mod, "__cpu_features__", None)
            if isinstance(feats, dict) and feats:
                return feats
        except Exception:
            continue
    return None


def _np_feature(feats: dict, *needles: str) -> bool:
    """Case-insensitive substring test against numpy's CPU feature map.

    numpy's ``__cpu_features__`` keys are stable upper-case spellings today
    (``AVX2`` / ``AVX512VNNI`` / ``ASIMDDP``), but we case-fold and match a
    substring so a future numpy that re-cases or lightly renames a key (e.g.
    ``AVX512_VNNI``) still resolves — the same forgiving stance the old
    py-cpuinfo ``any("vnni" in flag)`` search had. Returns True when *any*
    needle matches a present key whose value is truthy.

    ``"vnni"`` also matches Knights-Mill ``AVX5124VNNIW`` (a different,
    extinct instruction set), but that part still carries AVX512 so int8
    kernels run fine on it anyway — the resulting "pick int8" decision is the
    right one, so the loose match costs nothing.
    """
    lowered = [n.lower() for n in needles]
    return any(
        v and any(n in k.lower() for n in lowered)
        for k, v in feats.items()
    )


def _detect_int8_fast_path_x86() -> tuple[bool, bool]:
    """x86 INT8 fast path = AVX-VNNI (client) or AVX512-VNNI (server).

    Detection order (plain reads only, no child process):
      1. CPU family/model lookup (:func:`_vnni_via_family_model`) — the
         only path that authoritatively answers for Alder-Lake+ Intel
         *client* CPUs, whose AVX-VNNI flag numpy's feature map does not
         track (it only carries the AVX512 variant).
      2. numpy ``__cpu_features__`` "vnni" match — server VNNI parts
         (Cascade / Ice Lake-SP / Sapphire Rapids, key ``AVX512VNNI``).
         Client AVX-VNNI parts are all Alder-Lake+ and already answered
         authoritatively by (1).
      3. ``/proc/cpuinfo`` on Linux — text parse if numpy's map is gone.

    Returns ``(has_vnni, absence_confirmed)``. ``absence_confirmed=False``
    means no source could read CPU features — the caller stays optimistic
    and picks INT8 in that case (consistent with the ARM branch).
    """
    has_vnni, confirmed = _vnni_via_family_model()
    if confirmed:
        return has_vnni, True

    feats = _numpy_cpu_features()
    if feats is not None:
        return _np_feature(feats, "vnni"), True

    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("flags") and "vnni" in line:
                        return True, True
            return False, True
        except Exception:
            return False, False

    return False, False


def _detect_int8_fast_path_arm() -> tuple[bool, bool]:
    """ARM64 INT8 fast path = ARMv8.2-A NEON sdot/udot (``asimddp`` feature).

    Strategy (plain reads only, no child process):

      * macOS — Apple Silicon (M1+) universally has dotprod; Apple has
        never shipped an ARM Mac without it, so we short-circuit to
        ``(True, True)``.
      * numpy ``__cpu_features__`` "asimddp" — but its *trustworthiness is
        OS-dependent*. numpy only runtime-probes ARM features (HWCAP) on
        Linux/BSD; on Windows it falls back to compile-time baseline macros,
        so a win-arm64 wheel's conservative baseline reports ``ASIMDDP=False``
        even on dotprod-capable Snapdragon X. We therefore trust a numpy
        *positive* on any OS, but a numpy *negative* only on Linux. A Windows
        numpy-negative is NOT confirmed — it falls through to the kernel
        probe below (Codex P1 on PR #1525, restoring PR #1394's intent).
      * Linux fallback — ``/proc/cpuinfo`` ``Features`` line if numpy's map
        is unavailable. The ARM SBC ecosystem still has plenty of
        Cortex-A53 / A57 / A72 cores that predate dotprod (Pi-3 class).
      * Windows — ``IsProcessorFeaturePresent`` kernel32 API (a documented
        feature-query call, nothing low-level). Its 0 return is ambiguous
        (lacks dotprod OR old Win build that returns 0 for unknown feature
        ids), reported as inconclusive so ``auto`` stays optimistic.

    Returns ``(has_dotprod, absence_confirmed)``. Inconclusive cases let
    ``auto`` quantization still pick int8 without claiming a definitive
    answer.
    """
    system = platform.system()
    if system == "Darwin":
        return True, True

    feats = _numpy_cpu_features()
    if feats is not None and _np_feature(feats, "asimddp", "dotprod"):
        # Positive is authoritative on any OS — the flag is only set when the
        # feature is genuinely present.
        return True, True
    if feats is not None and system == "Linux":
        # numpy runtime-probes ARM HWCAP on Linux, so a negative is reliable.
        return False, True

    if system == "Linux":
        try:
            # ARM Linux /proc/cpuinfo uses "Features" (capital F), not "flags".
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("Features") and (
                        "asimddp" in line or "dotprod" in line
                    ):
                        return True, True
            return False, True
        except Exception:
            return False, False

    if system == "Windows":
        try:
            import ctypes
            # PF_ARM_V82_DP_INSTRUCTIONS_AVAILABLE = 43 — the canonical
            # Win32 feature constant for ARMv8.2 dotprod instructions. This
            # is the path numpy's compile-time-baseline negative can't be
            # trusted to replace.
            if ctypes.windll.kernel32.IsProcessorFeaturePresent(43):
                return True, True
            # 0 from this API is ambiguous: the CPU truly lacks dotprod
            # OR the running Windows build predates feature 43 and
            # returns 0 for every unrecognised constant. Stay
            # inconclusive so we don't false-disable embeddings on a
            # capable Snapdragon X running an older Win10 ARM build
            # (Codex P1 review on PR #1394).
            return False, False
        except Exception:
            # ctypes call failed on a non-standard runtime — be
            # inconclusive rather than wrong in either direction.
            return True, False

    # Unknown OS on ARM64 — modern ARM64 almost certainly has dotprod,
    # but we can't confirm.
    return True, False


# Per-process one-shot flag so we log the "no INT8 fast path" outcome
# exactly once at startup, with the vendor/family/model that drove the
# decision. Reset by :func:`reset_embedding_service_for_tests` so unit
# tests can re-trigger the log under monkeypatched detection.
_VNNI_DECISION_LOGGED = False


def _log_int8_fast_path_decision(has_vnni: bool, confirmed: bool) -> None:
    """Emit one warning per process when no INT8 fast path is detected.

    Triaging "why are my vectors disabled?" needs to know whether
    detection was authoritative (the family/model table or a CPU that
    truly lacks VNNI) or just inconclusive (exotic build with no numpy
    feature map, unknown arch). Including the vendor/family/model in the
    log lets us extend the lookup table for future microarchitectures
    based on real reports instead of guesses.

    We stay silent on the positive path — every successful boot would
    just add noise to the log."""
    global _VNNI_DECISION_LOGGED
    if _VNNI_DECISION_LOGGED:
        return
    _VNNI_DECISION_LOGGED = True
    if has_vnni:
        return
    info = _read_cpu_family_model()
    if info is None:
        vendor, family_str, model_str = "?", "?", "?"
    else:
        vendor, family, model = info
        family_str = f"0x{family:X}"
        model_str = f"0x{model:X}"
    # No AVX-VNNI fast path. ``auto`` no longer disables on this alone — it
    # falls back to the slower AVX2 int8 kernel (and only disables when AVX2 is
    # *also* confirmed absent). So the message describes the fallback, not a
    # disable, to avoid the old "vectors are off" misread.
    logger.warning(
        "EmbeddingService: no AVX-VNNI int8 fast path on this CPU "
        "(vendor=%s family=%s model=%s arch=%s vnni_absence_confirmed=%s). "
        "`auto` will fall back to the slower AVX2 int8 kernel if AVX2 is "
        "present (disable only when AVX2 is also absent). Force with "
        "VECTORS_QUANTIZATION=int8 / =fp32 if needed.",
        vendor, family_str, model_str, platform.machine() or "?", confirmed,
    )


def detect_avx_vnni_details() -> tuple[bool, bool]:
    """Return ``(has_int8_fast_path, absence_confirmed)``.

    The name keeps the historical ``vnni`` spelling for backward compat,
    but semantically this answers "does the CPU have a fast INT8 dot
    product?" — what the quantization picker actually needs. The fast
    path is architecture-specific:

      * x86 → AVX-VNNI / AVX512-VNNI
      * ARM64 → ARMv8.2-A NEON sdot/udot (``asimddp`` feature)

    ``absence_confirmed=False`` means detection was inconclusive. For
    ``auto`` quantization, INT8 is still selected in that case — we only
    skip vectors when we are *confident* the CPU lacks the fast path
    (INT8 would be slow and FP32 weights are not shipped).
    """
    if platform.machine().lower() in ("arm64", "aarch64"):
        result = _detect_int8_fast_path_arm()
    else:
        result = _detect_int8_fast_path_x86()
    _log_int8_fast_path_decision(*result)
    return result


def detect_avx_vnni() -> bool:
    """Backward-compatible: whether AVX-VNNI was detected."""
    has_vnni, _confirmed = detect_avx_vnni_details()
    return has_vnni


def detect_avx2_details() -> tuple[bool, bool]:
    """Return ``(has_avx2, absence_confirmed)`` for x86; ARM is treated as
    "has a usable int8 SIMD path" (NEON is universal on ARM64).

    AVX2 is the *slow-but-acceptable* int8 floor: without AVX-VNNI's fused
    dot product, onnxruntime's MLAS still has solid AVX2 int8 kernels (256-bit,
    ~2× SSE throughput) — fine for our nano model + small corpus. Below AVX2
    (SSE-only) int8 would be too slow to auto-enable.

    Source is numpy ``__cpu_features__['AVX2']`` (compiled C probe) rather
    than py-cpuinfo, whose CPU probe runs a generated native routine in a
    child process — the low-level pattern Huorong's heuristic quarantines
    this file over (see :func:`_numpy_cpu_features`).
    ``absence_confirmed=False`` means no source could read CPU features —
    caller stays optimistic (picks int8), matching the VNNI-inconclusive policy.
    """
    if platform.machine().lower() in ("arm64", "aarch64"):
        # On ARM the only tier boundary we act on is dotprod — it plays VNNI's
        # role (fast int8). There is no separate "acceptable 256-bit floor vs
        # disable" split like x86's AVX2-vs-SSE: base NEON is 128-bit, the
        # SSE-equivalent *disable* tier under this PR's own logic. So the int8
        # floor on ARM == the dotprod gate. Returning that (not a blanket
        # (True, True)) keeps prior behavior intact — no-dotprod SBCs
        # (Cortex-A53/A57/A72, Pi-3 class) still disable under auto, exactly as
        # before. The AVX2 broadening is x86-only. (Codex P2 on PR #1482.)
        return _detect_int8_fast_path_arm()
    feats = _numpy_cpu_features()
    if feats is not None:
        return _np_feature(feats, "avx2"), True
    if platform.system() == "Linux":
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("flags") and "avx2" in line.split():
                        return True, True
            return False, True
        except Exception:
            return False, False
    return False, False


def resolve_dim_for_ram(ram_gb: float | None) -> int | None:
    """Pick a Matryoshka dim from detected RAM. None ⇒ disabled.

    The bands match the design contract in the PR description — they're
    not a hard performance cliff, but a conservative budget that leaves
    headroom for the rest of the app (LLM client, websocket pool, TTS
    buffers, frontend renderer if collocated).

    ≥ 16 GB → 256. Higher Matryoshka levels (512/768) are reserved for
    opt-in overrides until we have enough latency data from real installs.
    """
    if ram_gb is None or ram_gb < DEFAULT_VECTORS_MIN_RAM_GB:
        return None
    if ram_gb < 8:
        return 64
    if ram_gb < 16:
        return 128
    return 256


def _coerce_dim(value, ram_gb: float | None) -> int | None:
    """Resolve a config value to an integer dim, or None if disabled.

    "auto" delegates to :func:`resolve_dim_for_ram`. Explicit values must
    be one of the supported Matryoshka steps; an invalid value falls
    back to "auto" with a warning rather than crashing — safer than
    refusing to start because of a typo in settings.
    """
    if value == "auto" or value is None:
        return resolve_dim_for_ram(ram_gb)
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "EmbeddingService: invalid embedding_dim=%r, falling back to auto", value,
        )
        return resolve_dim_for_ram(ram_gb)
    if as_int not in _DIM_STEPS:
        logger.warning(
            "EmbeddingService: dim=%d not in supported %s, falling back to auto",
            as_int, _DIM_STEPS,
        )
        return resolve_dim_for_ram(ram_gb)
    return as_int


def _auto_int8_or_none(
    has_vnni: bool,
    vnni_absence_confirmed: bool,
    has_avx2: bool,
    avx2_absence_confirmed: bool,
) -> str | None:
    """``auto`` policy core: pick ``int8`` when *any* usable int8 SIMD path
    exists, else ``None`` (disable).

    Tiers:
      1. AVX-VNNI present → int8 (fast path).
      2. VNNI inconclusive → int8 (optimistic, unchanged from before).
      3. VNNI confirmed absent, but AVX2 present → int8 (slow-but-fine —
         Haswell 2013+ / Zen+; covers the bulk of pre-2021 desktops).
      4. VNNI confirmed absent, AVX2 inconclusive → int8 (optimistic).
      5. VNNI *and* AVX2 both confirmed absent → None (SSE-only; too slow).
    """
    if has_vnni or not vnni_absence_confirmed:
        return "int8"
    if has_avx2 or not avx2_absence_confirmed:
        return "int8"
    return None


def _resolve_quantization(
    value: str | None,
    has_vnni: bool,
    *,
    vnni_absence_confirmed: bool = True,
    has_avx2: bool = True,
    avx2_absence_confirmed: bool = False,
) -> str | None:
    """Map ``\"auto\"`` / ``\"int8\"`` / ``\"fp32\"`` onto a loadable variant.

    Returns ``\"int8\"``, ``\"fp32\"``, or ``None``. ``None`` means local
    embeddings are off — for ``auto``, only when the CPU has *neither* AVX-VNNI
    nor AVX2 (both confirmed). Explicit ``\"fp32\"`` always loads the FP32 ONNX
    when files exist.

    Explicit ``\"int8\"`` is always honoured (with a warning when no VNNI fast
    path) so operators can force INT8 even on SSE-only CPUs if they accept the
    cost. ``has_avx2`` defaults True so the historical 3-arg call sites keep the
    old (VNNI-only) behaviour until they pass the AVX2 detection through.
    """
    if value == "fp32":
        return "fp32"
    if value == "auto" or value is None or value not in ("int8", "fp32"):
        return _auto_int8_or_none(
            has_vnni, vnni_absence_confirmed, has_avx2, avx2_absence_confirmed,
        )
    # value == "int8" (forced)
    if not has_vnni:
        logger.warning(
            "EmbeddingService: int8 requested but AVX-VNNI not detected "
            "(avx2=%s) — expect slower inference (AVX2 kernel or SSE fallback)",
            has_avx2,
        )
    return "int8"


def build_model_id(
    profile: str, dim: int, quantization: str, max_length: int | None = None,
) -> str:
    """Return the canonical id used in ``embedding_model_id`` cache fields.

    Format: ``<profile>-<dim>d-<quant>`` or ``<profile>-<dim>d-<quant>-mlen<N>``
    (e.g. ``local-text-retrieval-v1-128d-int8-mlen1024``).
    A change to any axis flips the id, which invalidates cached
    embeddings on the next read — same idea as ``tokenizer_identity``.

    ``max_length`` is the tokenizer truncation length — Codex pointed out on PR
    #1585 that the same long text fed into ONNX under max_length=8192 vs
    max_length=1024 yields entirely different token sequences, hence different
    vector spaces; leaving it out of the id would make stale caches "look valid"
    after an upgrade, silently skewing recall quality when cosine-compared against
    new queries. None falls back to the old mlen-less format — only for legacy call
    sites (e.g. old test fixtures that don't pass max_length); real service paths
    always pass it.
    """
    base = f"{profile}-{dim}d-{quantization}"
    if max_length is None:
        return base
    return f"{base}-mlen{max_length}"


def _profile_exists(model_dir: str, profile_id: str) -> bool:
    return os.path.isdir(os.path.join(model_dir, profile_id))


def _is_nonempty_file(path: str) -> bool:
    """File present AND >0 bytes. Zero-byte residue from an interrupted
    download passes plain ``isfile`` but trips the loader downstream — we
    treat it as missing so the bundled fallback still kicks in."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _profile_is_complete(
    model_dir: str, profile_id: str, quantization: str | None = None,
) -> bool:
    """A profile dir is usable only if it has a non-empty tokenizer plus
    a full (model + onnx_data sidecar) variant the runtime can actually
    load.

    ``quantization`` lets callers narrow the variant requirement to the
    one ``_load_session_blocking`` will actually open. With ``None``, only
    the shipped INT8 variant is considered complete — so a legacy fp32-only
    app-data tree does not mask a good bundled int8 profile. Pass ``None``
    only when the runtime quantization is not yet pinned to a single file.

    Why stricter than ``_profile_exists``: a half-downloaded or partially
    deleted app-data profile would otherwise satisfy the existence check,
    short-circuit the bundled fallback, and then trip
    ``NO_MODEL_FILE`` at session load — leaving the user with vectors
    sticky-disabled even though the bundle on disk is fine.
    """
    profile_dir = os.path.join(model_dir, profile_id)
    if not os.path.isdir(profile_dir):
        return False
    if not _is_nonempty_file(os.path.join(profile_dir, "tokenizer.json")):
        return False
    if quantization == "int8":
        stems: tuple[str, ...] = ("model_quantized.onnx",)
    elif quantization == "fp32":
        stems = ("model.onnx",)
    else:
        # Only the INT8 bundle is shipped; fp32 ONNX is optional / omitted.
        stems = ("model_quantized.onnx",)
    for stem in stems:
        model_path = os.path.join(profile_dir, "onnx", stem)
        sidecar_path = model_path + "_data"
        if _is_nonempty_file(model_path) and _is_nonempty_file(sidecar_path):
            return True
    return False


def _bundled_model_dirs() -> list[str]:
    """Candidate roots for build-time packaged embedding assets.

    Developers and CI place model files under
    ``data/embedding_models/<profile_id>/...``. In source runs this is
    relative to the repo root; in PyInstaller/Nuitka builds it lives next
    to the bundled launcher resources.
    """
    roots: list[str] = []
    if hasattr(sys, "_MEIPASS"):
        roots.append(str(sys._MEIPASS))
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    seen: set[str] = set()
    model_dirs: list[str] = []
    for root in roots:
        path = os.path.join(root, "data", DEFAULT_VECTORS_MODEL_DIR_NAME)
        norm = os.path.abspath(path)
        if norm not in seen:
            seen.add(norm)
            model_dirs.append(norm)
    return model_dirs


def _select_model_dir(
    app_docs_model_dir: str,
    profile_id: str,
    quantization: str | None = None,
) -> str:
    """Prefer user-managed app-data models, otherwise use bundled assets.

    A half-downloaded app-data profile, or one that only has the
    *other* quantization variant from what the runtime resolved to, is
    treated as broken (see ``_profile_is_complete``) and we fall back to
    bundled — otherwise the presence-only check would prefer the broken
    dir and sticky-disable vectors at load even though the bundle is
    fine. Callers should pass the resolved ``quantization`` so the
    variant check matches what ``_load_session_blocking`` will open.
    """
    if _profile_is_complete(app_docs_model_dir, profile_id, quantization):
        return app_docs_model_dir
    for bundled_dir in _bundled_model_dirs():
        if _profile_is_complete(bundled_dir, profile_id, quantization):
            return bundled_dir
    return app_docs_model_dir


# ── service ──────────────────────────────────────────────────────────


class EmbeddingService:
    """Process-singleton vector encoder. Acquire via :func:`get_embedding_service`.

    Responsibilities (intentionally narrow — fact / persona / reflection
    subsystems own everything around this class):

      1. Resolve the runtime model id from hardware + config
      2. Lazy-load the ONNX session on first ``request_load()``
      3. Provide ``embed`` / ``embed_batch`` once READY
      4. Be a sticky kill switch: once DISABLED, every method returns
         the safe "no embedding" answer for the rest of the process

    Thread/coroutine safety: ``request_load()`` is idempotent under
    concurrent callers thanks to the asyncio.Lock; embedding calls are
    naturally serialized through ``asyncio.to_thread`` and the
    onnxruntime session itself releases the GIL during inference.
    """

    def __init__(
        self,
        *,
        model_dir: str,
        enabled: bool = DEFAULT_VECTORS_ENABLED,
        embedding_dim_setting=DEFAULT_VECTORS_EMBEDDING_DIM,
        quantization_setting: str = DEFAULT_VECTORS_QUANTIZATION,
        min_ram_gb: float = DEFAULT_VECTORS_MIN_RAM_GB,
        profile_id: str = DEFAULT_VECTORS_MODEL_PROFILE_ID,
        ram_gb: float | None = None,        # injected for tests
        has_vnni: bool | None = None,       # injected for tests
        vnni_absence_confirmed: bool | None = None,  # False = inconclusive detect
        has_avx2: bool | None = None,       # injected for tests
        avx2_absence_confirmed: bool | None = None,  # False = inconclusive detect
    ) -> None:
        self._model_dir = model_dir
        self._enabled = enabled
        self._embedding_dim_setting = embedding_dim_setting
        self._quantization_setting = quantization_setting
        self._min_ram_gb = min_ram_gb
        self._profile_id = profile_id

        # Resolved at construction so ``model_id()`` can return early
        # even before the session loads — callers reading
        # embedding_model_id at write time need a stable id.
        self._ram_gb = ram_gb if ram_gb is not None else detect_total_ram_gb()
        if has_vnni is not None:
            self._has_vnni = has_vnni
            self._vnni_absence_confirmed = (
                True if vnni_absence_confirmed is None else vnni_absence_confirmed
            )
        else:
            detected_vnni, absence_confirmed = detect_avx_vnni_details()
            self._has_vnni = detected_vnni
            self._vnni_absence_confirmed = absence_confirmed
        if has_avx2 is not None:
            self._has_avx2 = has_avx2
            self._avx2_absence_confirmed = (
                True if avx2_absence_confirmed is None else avx2_absence_confirmed
            )
        else:
            detected_avx2, avx2_confirmed = detect_avx2_details()
            self._has_avx2 = detected_avx2
            self._avx2_absence_confirmed = avx2_confirmed
        self._dim = _coerce_dim(embedding_dim_setting, self._ram_gb)
        if quantization_setting not in ("auto", "int8", "fp32"):
            logger.warning(
                "EmbeddingService: invalid quantization=%r, falling back to auto",
                quantization_setting,
            )
            norm_quant = "auto"
        else:
            norm_quant = quantization_setting
        self._quantization = _resolve_quantization(
            norm_quant,
            self._has_vnni,
            vnni_absence_confirmed=self._vnni_absence_confirmed,
            has_avx2=self._has_avx2,
            avx2_absence_confirmed=self._avx2_absence_confirmed,
        )

        self._state = EmbeddingState.INIT
        self._disable_reason = _DisableReason.NONE
        self._session = None
        self._tokenizer = None
        self._load_lock = asyncio.Lock()

        # Decide initial disable conditions (all but model file presence,
        # which we check at load time so a deferred download path can
        # still flip vectors on after first session).
        if _cpu_is_blocklisted():
            # Highest priority: a known-bad CPU SIGILLs onnxruntime no matter
            # what the SIMD detection concluded, so this gate sits above the
            # RAM / quantization checks. Logged (not silent) so the startup
            # line tells operators why vectors are off and how to override.
            self._mark_disabled(_DisableReason.CPU_BLOCKLISTED, log=True)
        elif not self._enabled:
            self._mark_disabled(_DisableReason.USER_DISABLED, log=False)
        elif self._ram_gb is None or self._ram_gb < self._min_ram_gb:
            self._mark_disabled(_DisableReason.LOW_RAM, log=False)
        elif self._dim is None:
            # _coerce_dim returns None when the resolved RAM is too low
            # for any band — defensive double-check; LOW_RAM should have
            # caught it already.
            self._mark_disabled(_DisableReason.LOW_RAM, log=False)
        elif self._quantization is None:
            # auto + neither AVX-VNNI nor AVX2 (both confirmed) → SSE-only,
            # too slow to auto-enable. (Forced int8 never resolves to None.)
            self._mark_disabled(_DisableReason.NO_SIMD_INT8_PATH, log=True)

    # ── public API ────────────────────────────────────────────────────

    def is_available(self) -> bool:
        """True iff a subsequent ``embed()`` call would actually return
        a vector. Callers MUST short-circuit to the pre-vector path
        when this is False."""
        return self._state == EmbeddingState.READY

    def is_disabled(self) -> bool:
        """True iff the service has reached the sticky DISABLED state.
        Distinct from ``not is_available()`` because INIT / LOADING also
        fail ``is_available`` but are not terminal."""
        return self._state == EmbeddingState.DISABLED

    def disable_reason(self) -> str:
        return self._disable_reason.value

    def model_id(self) -> str | None:
        """Canonical id stamped into ``embedding_model_id`` cache fields.
        Returns None when the service is permanently DISABLED — callers
        should not write embedding rows in that case."""
        if (
            self._state == EmbeddingState.DISABLED
            or self._dim is None
            or self._quantization not in ("int8", "fp32")
        ):
            return None
        return build_model_id(
            self._profile_id,
            self._dim,
            self._quantization,
            DEFAULT_VECTORS_MAX_LENGTH,
        )

    def dim(self) -> int | None:
        return self._dim

    def quantization(self) -> str | None:
        return self._quantization

    def ram_gb(self) -> float | None:
        return self._ram_gb

    def has_vnni(self) -> bool:
        return self._has_vnni

    def has_avx2(self) -> bool:
        return self._has_avx2

    async def request_load(self) -> bool:
        """Load the ONNX session if not already loaded. Returns
        ``is_available()`` after the attempt.

        Idempotent: safe to call from multiple coroutines (warmup task
        + first-use fallback). Single-flight via the load lock so we
        don't double-decompress the model file.

        On any failure, transitions to DISABLED and returns False — the
        service stays off for the lifetime of the process.
        """
        if self._state in (EmbeddingState.READY, EmbeddingState.DISABLED):
            return self.is_available()

        async with self._load_lock:
            if self._state in (EmbeddingState.READY, EmbeddingState.DISABLED):
                return self.is_available()
            self._state = EmbeddingState.LOADING
            try:
                await asyncio.to_thread(self._load_session_blocking)
            except _DisabledError as e:
                self._mark_disabled(e.reason)
                return False
            except Exception as e:  # noqa: BLE001 — any load failure → off
                logger.warning(
                    "EmbeddingService: load failed (%s: %s); vectors disabled",
                    type(e).__name__, e,
                )
                self._mark_disabled(_DisableReason.LOAD_ERROR)
                return False
            self._state = EmbeddingState.READY
            logger.info(
                "EmbeddingService: ready (model_id=%s, ram=%.1fGB, vnni=%s, avx2=%s)",
                self.model_id(), self._ram_gb or 0.0, self._has_vnni, self._has_avx2,
            )
            return True

    async def embed(self, text: str) -> list[float] | None:
        """Single-text embedding. Returns None when not READY — caller
        must treat this as a cache miss and skip the vector path for
        this query."""
        if not text:
            return None
        if not self.is_available():
            return None
        try:
            vectors = await asyncio.to_thread(self._infer_blocking, [text])
        except Exception as e:  # noqa: BLE001 — sticky inference failure
            logger.warning(
                "EmbeddingService: inference failed (%s: %s); vectors disabled",
                type(e).__name__, e,
            )
            self._mark_disabled(_DisableReason.INFERENCE_ERROR)
            return None
        return vectors[0] if vectors else None

    async def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """Batch embedding. Empty / None inputs and not-ready service
        both produce a None at the corresponding output index — keeps
        callers' index alignment with the input list intact."""
        if not texts:
            return []
        result: list[list[float] | None] = [None] * len(texts)
        if not self.is_available():
            return result
        # Filter out empty entries before inference but preserve
        # positional alignment in the output via index mapping.
        active_idx: list[int] = []
        active_texts: list[str] = []
        for i, t in enumerate(texts):
            if t:
                active_idx.append(i)
                active_texts.append(t)
        if not active_texts:
            return result
        try:
            vectors = await asyncio.to_thread(self._infer_blocking, active_texts)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "EmbeddingService: batch inference failed (%s: %s); vectors disabled",
                type(e).__name__, e,
            )
            self._mark_disabled(_DisableReason.INFERENCE_ERROR)
            return result
        for slot, vec in zip(active_idx, vectors):
            result[slot] = vec
        return result

    # ── internal: session load / inference ───────────────────────────

    def _model_file_path(self) -> str:
        """Resolve the on-disk ONNX file path for the active quantization.

        Layout mirrors the Hugging Face ONNX export:
        ``onnx/model_quantized.onnx`` (int8) or ``onnx/model.onnx`` (fp32),
        each with a matching ``*_data`` sidecar, plus ``tokenizer.json``.
        """
        filename = (
            "model.onnx"
            if self._quantization == "fp32"
            else "model_quantized.onnx"
        )
        return os.path.join(
            self._model_dir, self._profile_id, "onnx", filename,
        )

    def _tokenizer_file_path(self) -> str:
        return os.path.join(self._model_dir, self._profile_id, "tokenizer.json")

    def _load_session_blocking(self) -> None:
        """Synchronous load — runs under ``asyncio.to_thread``.

        Order of checks: file presence first (cheapest, cleanest disable
        reason), then onnxruntime import (heavyweight import deferred
        until we know the file exists), then session creation. Each
        failure mode raises ``_DisabledError`` with the right reason.
        """
        model_path = self._model_file_path()
        tokenizer_path = self._tokenizer_file_path()
        external_data_path = f"{model_path}_data"
        # Match _profile_is_complete: zero-byte residue from an interrupted
        # download passes os.path.exists but trips ort/tokenizers later. Reject
        # it here as NO_MODEL_FILE so the disable reason is the cleanest one.
        if (
            not _is_nonempty_file(model_path)
            or not _is_nonempty_file(tokenizer_path)
            or not _is_nonempty_file(external_data_path)
        ):
            raise _DisabledError(_DisableReason.NO_MODEL_FILE)
        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as e:
            raise _DisabledError(_DisableReason.NO_ONNXRUNTIME) from e
        try:
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as e:
            # huggingface tokenizers is the only sane way to load the
            # SentencePiece-style tokenizer offline. Distinct
            # disable reason so operators don't chase a phantom
            # onnxruntime install when it's actually tokenizers
            # that's missing.
            raise _DisabledError(_DisableReason.NO_TOKENIZERS) from e

        sess_opts = ort.SessionOptions()
        # intra-op 线程数:backfill 是后台补向量的任务,在小核机上占掉一半核心
        # 已经可感(4 核 → 2 线程 = 50%,与前端渲染 / LLM 客户端 / TTS 抢核)。
        # ≤4 核降到单线程,把余下的核留给交互路径;>4 核维持「一半核心」的老
        # 策略——大核机吞吐优先,头部余量仍然够。
        #
        # 「小核机」阈值按*物理*核判,不用 os.cpu_count():4 核 + 超线程的 CPU
        # (i3/i5 常见)逻辑核报 8,按逻辑核判会漏掉这类机器走 >4 分支给 4 线程
        # ——而这恰是要压占用的目标硬件。psutil 已是本模块依赖(detect_total_ram_gb),
        # 物理核拿不到(平台不支持 / 无 psutil)再退回逻辑核。>4 分支仍按逻辑核
        # 折半,与本改动前的行为逐字一致,不动大核机。
        logical = os.cpu_count() or 2
        try:
            import psutil
            physical = psutil.cpu_count(logical=False)
        except Exception:  # noqa: BLE001 — 无 psutil / 平台不支持 → 退回逻辑核
            physical = None
        cores = physical or logical
        sess_opts.intra_op_num_threads = 1 if cores <= 4 else max(1, logical // 2)
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Arena 默认 True(BFCArena 只涨不还):一次性大分配后 RSS 永久
        # 钉在高水位,把瞬时尖峰变成永久占用。我们的输入有 _INFER_BATCH_MAX_TOKENS
        # 兜底,峰值已可控;关掉 arena 让分配器跟实际需求走,RSS 能跌回,
        # 也避免冷路径偶发长批次永久污染基线。代价:每次 run 重新 malloc,
        # CPU 推理本来就 100ms+ 级,malloc 几 μs 可忽略。
        sess_opts.enable_cpu_mem_arena = False
        # 关掉 intra-op 线程池的 spin-wait。ORT 默认在每次 run 结束后让 worker
        # 线程忙等(busy-spin)一小段以抢下一批活;而 backfill 是「一连串小 run,
        # 中间夹着 Python 侧 tokenize / numpy 预处理」的突发型负载,spin 会在这些
        # 间隙把核心持续顶高。改成阻塞等待后,下次 run 唤醒线程多花几 μs——对
        # 100ms+ 级的 CPU 推理可忽略——却能显著压低 backfill / 召回期间的占用。
        # add_session_config_entry 在旧版本 ORT 上可能缺失,失败只是回到默认
        # spin 行为(功能不受影响),所以吞掉异常不当作 load 失败。
        try:
            sess_opts.add_session_config_entry(
                "session.intra_op.allow_spinning", "0",
            )
        except Exception as e:  # noqa: BLE001 — 老 ORT 无此键 → 保持默认 spin
            logger.debug(
                "EmbeddingService: allow_spinning=0 not applied (%s: %s)",
                type(e).__name__, e,
            )
        self._session = ort.InferenceSession(
            model_path, sess_options=sess_opts, providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        try:
            self._tokenizer.enable_truncation(max_length=DEFAULT_VECTORS_MAX_LENGTH)
        except Exception as e:  # noqa: BLE001
            # 不能继续 ready —— 见 _DisableReason.TRUNCATION_SETUP_FAILED 注释:
            # model_id 把 max_length 编进 cache id,truncation 没生效时长文本
            # 会被未截断地编码、却 stamp 成 mlen1024,污染 cache 语义。让 load
            # 失败走 disable 路径,fallback service 接管,比错配 cache 安全得多。
            logger.warning(
                "EmbeddingService: tokenizer truncation setup failed (max_length=%d): %s",
                DEFAULT_VECTORS_MAX_LENGTH, e,
            )
            raise _DisabledError(_DisableReason.TRUNCATION_SETUP_FAILED) from e

    def _infer_blocking(self, texts: list[str]) -> list[list[float]]:
        """Tokenize + run ONNX session + L2-normalize + Matryoshka-trunc.

        The Matryoshka truncation is the crux of why ``model_id``
        encodes the dim: a 64-d cached vector and a 256-d freshly
        computed vector are NOT comparable, even though they come from
        the same checkpoint, so the cache key MUST contain the dim.

        Sub-batching: ``texts`` arrives already capped by the worker's
        ``BATCH_SIZE`` (currently 16), but pad-to-longest means a single
        long entry can blow up activation memory for the whole batch
        (a long blob pasted into recent → entire 16-batch padded to
        thousands of tokens → multi-GB activations). We re-bucket here
        by token budget ``batch × max_len ≤ _INFER_BATCH_MAX_TOKENS``: short
        rows still pack densely (same throughput as before for the
        normal case), and long rows fall into smaller buckets — capping
        the per-run activation footprint regardless of input shape.
        """
        if self._session is None or self._tokenizer is None:
            raise RuntimeError("session not loaded")
        encoded = self._tokenizer.encode_batch(texts)
        import numpy as np

        input_names = {i.name for i in self._session.get_inputs()}
        # 按长度升序桶装。排序的目的是让相近长度凑一起,避免「一条短一条长」
        # 浪费 padding 预算。每条出桶时记录 original index,run 完按原顺序填回。
        order = sorted(range(len(encoded)), key=lambda i: len(encoded[i].ids))
        out: list[list[float] | None] = [None] * len(texts)

        bucket_idx: list[int] = []
        bucket_max_len = 0
        for orig_i in order:
            n = len(encoded[orig_i].ids)
            new_max = max(bucket_max_len, n)
            # 空桶必接受(哪怕单条 > budget),否则极端 max_length 配置会死锁;
            # 非空桶按预算 flush。
            if bucket_idx and new_max * (len(bucket_idx) + 1) > _INFER_BATCH_MAX_TOKENS:
                self._run_bucket(bucket_idx, encoded, input_names, out, np)
                bucket_idx = [orig_i]
                bucket_max_len = n
            else:
                bucket_idx.append(orig_i)
                bucket_max_len = new_max
        if bucket_idx:
            self._run_bucket(bucket_idx, encoded, input_names, out, np)

        # 桶分配按 range(len(encoded)) 全覆盖 — 任何 None 残留都是 bug
        # 而不是「正常但失败」,所以这里 assert 而不是静默过滤(过滤会改长度,
        # 把 zip(texts, vectors) 错位)。
        assert all(v is not None for v in out), "bucket coverage gap"
        return out  # type: ignore[return-value]

    def _run_bucket(
        self,
        bucket_idx: list[int],
        encoded: list,
        input_names: set,
        out: list,
        np,
    ) -> None:
        """Single-bucket pad → ONNX run → pool → L2-norm → write back into ``out``.

        Split out purely to keep ``_infer_blocking``'s bucketing loop short; all
        state is passed via parameters, no side effects (``out`` is written in
        place by original index).
        """
        ids = [encoded[i].ids for i in bucket_idx]
        mask = [encoded[i].attention_mask for i in bucket_idx]
        max_len = max(len(x) for x in ids)
        ids_arr = np.zeros((len(bucket_idx), max_len), dtype=np.int64)
        mask_arr = np.zeros((len(bucket_idx), max_len), dtype=np.int64)
        for i, (id_row, mask_row) in enumerate(zip(ids, mask)):
            ids_arr[i, : len(id_row)] = id_row
            mask_arr[i, : len(mask_row)] = mask_row
        feeds = {"input_ids": ids_arr}
        if "attention_mask" in input_names:
            feeds["attention_mask"] = mask_arr
        if "token_type_ids" in input_names:
            feeds["token_type_ids"] = np.zeros_like(ids_arr)
        outputs = self._session.run(None, feeds)
        # The default profile uses last-token pooling. Then L2-normalize
        # and Matryoshka-truncate to the active dim.
        token_embeddings = outputs[0]
        last_indices = np.maximum(mask_arr.sum(axis=1) - 1, 0)
        pooled = token_embeddings[np.arange(len(bucket_idx)), last_indices]
        if self._dim is not None and self._dim < pooled.shape[1]:
            pooled = pooled[:, : self._dim]
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        normalized = pooled / norms
        for i, orig_i in enumerate(bucket_idx):
            out[orig_i] = normalized[i].tolist()

    # ── disable bookkeeping ──────────────────────────────────────────

    def _mark_disabled(self, reason: _DisableReason, *, log: bool = True) -> None:
        # Only log the first transition — re-entries from later
        # inference failures shouldn't spam logs.
        if self._state != EmbeddingState.DISABLED and log:
            logger.warning(
                "EmbeddingService: vectors disabled (%s)", reason.value,
            )
        self._state = EmbeddingState.DISABLED
        self._disable_reason = reason
        self._session = None
        self._tokenizer = None


class _DisabledError(Exception):
    """Internal control-flow exception used by the load path to signal
    'no need to log a stack trace, this is a known disable reason'."""

    def __init__(self, reason: _DisableReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


# ── module-level singleton accessor ──────────────────────────────────

_SERVICE: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    """Return the process-wide singleton, lazily constructed.

    Construction reads from ``config`` and the user's app-data dir. The
    service ctor itself is cheap (no model load, no disk IO beyond psutil
    sampling), so we don't bother short-circuiting on the lock outside.
    """
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = _build_default_service()
    return _SERVICE


def reset_embedding_service_for_tests() -> None:
    """Test-only: drop the singleton so the next ``get_embedding_service``
    call rebuilds with whatever monkeypatched config / RAM the test set up.

    Also clears :data:`_VNNI_DECISION_LOGGED` so a test that monkeypatches
    detection can verify the warning is emitted on its synthetic boot.
    """
    global _SERVICE, _VNNI_DECISION_LOGGED
    _SERVICE = None
    _VNNI_DECISION_LOGGED = False


def _build_default_service() -> EmbeddingService:
    """Construct the singleton from app config + app_docs_dir model path."""
    try:
        from utils.config_manager import get_config_manager
        cm = get_config_manager()
        app_docs_model_dir = os.path.join(
            str(cm.app_docs_dir), DEFAULT_VECTORS_MODEL_DIR_NAME,
        )
    except Exception as e:
        # Outside the FastAPI context (e.g. some isolated test that
        # imports this module before bootstrapping config) we still
        # want a service, just one that's permanently disabled. The
        # alternative (raise) would cascade into every memory call site.
        logger.warning(
            "EmbeddingService: config_manager unavailable (%s); using disabled stub",
            e,
        )
        return EmbeddingService(
            model_dir="", enabled=False, ram_gb=0.0, has_vnni=False,
        )

    try:
        from config import (
            VECTORS_ENABLED,
            VECTORS_EMBEDDING_DIM,
            VECTORS_QUANTIZATION,
            VECTORS_MIN_RAM_GB,
            VECTORS_MODEL_PROFILE_ID,
        )
    except ImportError:
        # Config module hasn't been updated yet — fall back to defaults.
        # Lets the embedding module land in one PR before the
        # config-side knobs in another.
        VECTORS_ENABLED = DEFAULT_VECTORS_ENABLED
        VECTORS_EMBEDDING_DIM = DEFAULT_VECTORS_EMBEDDING_DIM
        VECTORS_QUANTIZATION = DEFAULT_VECTORS_QUANTIZATION
        VECTORS_MIN_RAM_GB = DEFAULT_VECTORS_MIN_RAM_GB
        VECTORS_MODEL_PROFILE_ID = DEFAULT_VECTORS_MODEL_PROFILE_ID

    # Resolve quantization here so _select_model_dir can require the exact
    # variant ``_load_session_blocking`` will open. Without this, an app-data
    # profile that only contains the *other* variant would still satisfy the
    # completeness check and short-circuit a complete bundled fallback.
    has_vnni, vnni_absence_confirmed = detect_avx_vnni_details()
    has_avx2, avx2_absence_confirmed = detect_avx2_details()
    norm_q = (
        VECTORS_QUANTIZATION
        if VECTORS_QUANTIZATION in ("auto", "int8", "fp32")
        else "auto"
    )
    resolved_quantization = _resolve_quantization(
        norm_q, has_vnni, vnni_absence_confirmed=vnni_absence_confirmed,
        has_avx2=has_avx2, avx2_absence_confirmed=avx2_absence_confirmed,
    )

    model_dir = (
        app_docs_model_dir
        if resolved_quantization is None
        else _select_model_dir(
            app_docs_model_dir, VECTORS_MODEL_PROFILE_ID, resolved_quantization,
        )
    )

    return EmbeddingService(
        model_dir=model_dir,
        enabled=VECTORS_ENABLED,
        embedding_dim_setting=VECTORS_EMBEDDING_DIM,
        quantization_setting=VECTORS_QUANTIZATION,
        min_ram_gb=VECTORS_MIN_RAM_GB,
        profile_id=VECTORS_MODEL_PROFILE_ID,
        has_vnni=has_vnni,
        vnni_absence_confirmed=vnni_absence_confirmed,
        has_avx2=has_avx2,
        avx2_absence_confirmed=avx2_absence_confirmed,
    )


# ── cosine helpers (numpy-free for callers that only need scoring) ────


def cosine_similarity(a, b) -> float:
    """Cosine similarity between two unit-norm vectors.

    Both ``embed()`` outputs are L2-normalized, so this is a straight
    dot product — no division required. Accepts the canonical base64
    form, legacy ``list[float]``, or an already-decoded numpy array;
    decodes lazily so the per-pair API stays compatible with tests and
    fact-dedup's single-pair callsite. For hot loops over thousands of
    candidates, prefer building a stacked matrix once via
    :func:`decode_embedding` and using ``M @ q`` — the recall path does
    that.

    Out-of-band inputs (None, empty, dim mismatch, malformed base64)
    return 0.0 rather than raising; retrieval and dedup are happier
    ranking around an unrankable candidate than crashing because one
    entry was missing its embedding.
    """
    av = decode_embedding(a)
    bv = decode_embedding(b)
    if av is None or bv is None:
        return 0.0
    if av.size == 0 or bv.size == 0 or av.size != bv.size:
        return 0.0
    import numpy as np
    return float(np.dot(av, bv))


def is_cached_embedding_valid(
    entry: dict, current_text: str, current_model_id: str | None,
) -> bool:
    """Decide whether the persisted embedding on ``entry`` is still good.

    Match contract (mirrors ``token_count`` cache in persona.py):
      * embedding field is a non-empty base64 string (canonical form
        emitted by :func:`stamp_embedding_fields`)
      * the payload actually decodes (corrupt base64 → invalid)
      * decoded length matches the dim encoded in the running
        ``model_id`` — guards against truncated payloads and against
        a wrong-quantization payload sneaking through under the right
        model_id string
      * sha256 of ``current_text`` matches stored ``embedding_text_sha256``
      * ``embedding_model_id`` matches the running service's id

    Legacy ``list[float]`` payloads are intentionally treated as invalid
    so the warmup worker re-stamps them in the new compact form. The
    one-time CPU cost is bounded (small N at migration time) and avoids
    carrying two on-disk shapes forward indefinitely.

    Without the decode + dim check, a corrupt cache row would pass the
    typeof guard, never get re-stamped by the worker (it keeps
    "validating"), and silently fall through to the unembedded pool in
    every recall — a permanent retrieval-quality regression for that
    entry (Codex review on PR #1147).

    Any mismatch → False, callers should clear the embedding fields and
    re-enqueue the entry for the warmup worker.
    """
    if not isinstance(entry, dict):
        return False
    emb = entry.get("embedding")
    if not isinstance(emb, str) or not emb:
        return False
    if current_model_id is None:
        return False
    if entry.get("embedding_model_id") != current_model_id:
        return False
    if entry.get("embedding_text_sha256") != _embedding_text_sha256(current_text):
        return False
    decoded = _decode_vector_fp16(emb)
    if decoded is None or decoded.size == 0:
        return False
    expected_dim = parse_dim_from_model_id(current_model_id)
    if expected_dim is not None and decoded.size != expected_dim:
        return False
    return True


def clear_embedding_fields(entry: dict) -> None:
    """In-place wipe of the embedding cache. Call from any path that
    rewrites ``entry['text']`` so the next render/recall sees a clean
    cache miss instead of a stale vector tied to the old text."""
    if not isinstance(entry, dict):
        return
    entry["embedding"] = None
    entry["embedding_text_sha256"] = None
    entry["embedding_model_id"] = None


def stamp_embedding_fields(
    entry: dict, vector, text: str, model_id: str,
) -> None:
    """In-place write of an embedding triple onto an entry.

    Stamping all three fields together (vector + text-sha + model-id)
    in one helper prevents the half-written state where ``embedding`` is
    set but the fingerprints aren't, which would otherwise look like a
    legacy entry on the next read and trigger a recompute.

    The vector is encoded to the canonical base64 fp16 form before
    storage (see :func:`_encode_vector_fp16`). Callers pass the raw
    fp32 list returned by :meth:`EmbeddingService.embed` — this helper
    owns the on-disk encoding so the rest of the pipeline never sees
    it.
    """
    if not isinstance(entry, dict):
        return
    entry["embedding"] = _encode_vector_fp16(vector)
    entry["embedding_text_sha256"] = _embedding_text_sha256(text)
    entry["embedding_model_id"] = model_id
