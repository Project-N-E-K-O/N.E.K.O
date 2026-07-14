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
"""OpenAI-compatible embeddings adapter."""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

class OpenAIEmbeddings:
    """Lightweight OpenAI embeddings client."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str = "",
        api_key: str | None = None,
        **_kwargs: Any,
    ):
        from openai import AsyncOpenAI, OpenAI

        self.model = model
        _api_key = api_key or "sk-placeholder"
        self._client = OpenAI(base_url=base_url, api_key=_api_key)
        self._aclient = AsyncOpenAI(base_url=base_url, api_key=_api_key)

    def embed_query(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in resp.data]

    async def aembed_query(self, text: str) -> list[float]:
        resp = await self._aclient.embeddings.create(model=self.model, input=text)
        return resp.data[0].embedding
