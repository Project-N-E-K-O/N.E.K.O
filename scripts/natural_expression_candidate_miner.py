#!/usr/bin/env python3
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0

"""Compatibility CLI for deterministic natural-expression candidate mining."""

from __future__ import annotations

import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from utils.natural_expression_candidates import (
    CandidateMinerError,
    MiningConfig,
    SourceMessage,
    build_parser,
    build_report,
    main,
    normalize_language,
    read_jsonl,
    serialize_report,
    write_report,
)

__all__ = [
    "CandidateMinerError",
    "MiningConfig",
    "SourceMessage",
    "build_parser",
    "build_report",
    "main",
    "normalize_language",
    "read_jsonl",
    "serialize_report",
    "write_report",
]


if __name__ == "__main__":
    raise SystemExit(main())
