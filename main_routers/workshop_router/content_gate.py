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

"""Non-blocking ownership for Workshop content folders.

Steam consumes the whole folder from ``SetItemContent`` until the upload
finishes. Local file writes and folder cleanup must therefore be excluded for
that entire span, not just while the preflight reads the manifest.

Claims are bookkeeping, never wait queues. Both claim and release belong to
the worker-thread unit that performs the blocking work so cancellation of its
awaiting coroutine cannot release a folder while that worker is still using it.
"""

import os
import threading
from contextlib import contextmanager
from typing import Iterator


class ContentFolderBusy(RuntimeError):
    """The requested content folder is already owned by another operation."""


PUBLISH_PURPOSE = '发布'
CLEANUP_PURPOSE = '清理'

_CLAIM_GUARD = threading.Lock()
_EXCLUSIVE: dict[str, str] = {}
_PARTIAL_WRITERS: dict[str, int] = {}


def _claim_key(content_folder: str) -> str:
    """Collapse aliases, symlinks and Windows junctions to one ownership key."""
    return os.path.normcase(os.path.realpath(content_folder))


def _claim_keys_overlap(first: str, second: str) -> bool:
    """Whether either claimed folder contains the other."""
    try:
        common = os.path.commonpath((first, second))
    except ValueError:
        return False
    return common == first or common == second


def _exclusive_holder_for(key: str) -> str | None:
    """Return the purpose of an overlapping exclusive claim, if any."""
    return next((
        purpose
        for claimed_key, purpose in _EXCLUSIVE.items()
        if _claim_keys_overlap(key, claimed_key)
    ), None)


def _has_partial_writer_for(key: str) -> bool:
    """Whether an overlapping folder has an active partial writer."""
    return any(
        count and _claim_keys_overlap(key, claimed_key)
        for claimed_key, count in _PARTIAL_WRITERS.items()
    )


@contextmanager
def claim_content_folder(content_folder: str, *, purpose: str) -> Iterator[None]:
    """Take a non-blocking exclusive claim for publish or folder deletion."""
    key = _claim_key(content_folder)
    with _CLAIM_GUARD:
        holder = _exclusive_holder_for(key)
        if holder is not None:
            raise ContentFolderBusy(f'该内容目录正在{holder}，请等这次操作结束后再试')
        if _has_partial_writer_for(key):
            raise ContentFolderBusy('该内容目录有局部文件正在写入，请稍后再试')
        _EXCLUSIVE[key] = purpose
    try:
        yield
    finally:
        with _CLAIM_GUARD:
            _EXCLUSIVE.pop(key, None)


@contextmanager
def claim_partial_writer(
    content_folder: str,
    *,
    purpose: str = '修改参考语音',
) -> Iterator[None]:
    """Take a shared local-write claim excluded by whole-folder operations."""
    key = _claim_key(content_folder)
    with _CLAIM_GUARD:
        holder = _exclusive_holder_for(key)
        if holder is not None:
            raise ContentFolderBusy(f'该物品正在{holder}，等这次操作结束后再{purpose}')
        _PARTIAL_WRITERS[key] = _PARTIAL_WRITERS.get(key, 0) + 1
    try:
        yield
    finally:
        with _CLAIM_GUARD:
            remaining = _PARTIAL_WRITERS.get(key, 0) - 1
            if remaining > 0:
                _PARTIAL_WRITERS[key] = remaining
            else:
                _PARTIAL_WRITERS.pop(key, None)


claim_reference_pair = claim_partial_writer
