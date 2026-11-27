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

"""Unified dual-mode Decision Model client (native Decisions APIs + Chat Fallback)."""

from .adapters import (
    ChatFallbackDecisionAdapter,
    DecisionAdapter,
    GeminiDecisionsAdapter,
    OpenAIDecisionsAdapter,
    SystemOneAdapter,
    get_decision_adapter,
)
from .client import DecisionClient, clear_native_cooldown, decide
from .rehost import rehost_choice, rehost_choices
from .routing import (
    ROUTE_CHAT,
    ROUTE_NATIVE,
    ROUTE_REHOST,
    chat_logprobs_usable,
    decide_route,
    native_decision_usable,
)
from .schema import (
    ALL_DECISION_PROTOCOLS,
    NATIVE_DECISION_PROTOCOLS,
    BoolQuestion,
    ChoiceQuestion,
    DecisionAnswer,
    DecisionProtocolError,
    DecisionQuestion,
    DecisionResponse,
    ScoreQuestion,
    infer_decision_protocol,
    is_native_decision_protocol,
    resolve_decision_endpoint,
)

__all__ = [
    "ALL_DECISION_PROTOCOLS",
    "NATIVE_DECISION_PROTOCOLS",
    "BoolQuestion",
    "ChoiceQuestion",
    "ScoreQuestion",
    "DecisionQuestion",
    "DecisionAnswer",
    "DecisionResponse",
    "DecisionProtocolError",
    "DecisionAdapter",
    "SystemOneAdapter",
    "OpenAIDecisionsAdapter",
    "GeminiDecisionsAdapter",
    "ChatFallbackDecisionAdapter",
    "get_decision_adapter",
    "DecisionClient",
    "clear_native_cooldown",
    "decide",
    "is_native_decision_protocol",
    "infer_decision_protocol",
    "resolve_decision_endpoint",
    "ROUTE_CHAT",
    "ROUTE_NATIVE",
    "ROUTE_REHOST",
    "chat_logprobs_usable",
    "decide_route",
    "native_decision_usable",
    "rehost_choice",
    "rehost_choices",
]
