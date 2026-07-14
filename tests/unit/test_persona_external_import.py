from __future__ import annotations

import asyncio

import pytest

from memory.persona.facts import FactsMixin


class _ExternalImportHarness(FactsMixin):
    def __init__(self, persona: dict):
        self.persona = persona
        self.lock = asyncio.Lock()
        self.save_count = 0

    def _get_alock(self, _name: str) -> asyncio.Lock:
        return self.lock

    async def _aensure_persona_locked(self, _name: str) -> dict:
        return self.persona

    async def asave_persona(self, _name: str, _persona: dict) -> None:
        self.save_count += 1


@pytest.mark.asyncio
async def test_external_import_deduplicates_legacy_string_facts_only_as_strings():
    importer = _ExternalImportHarness({
        'master': {
            'facts': [
                '  Existing   FACT  ',
                {'text': 'Dictionary Fact'},
                {'text': None},
                {'text': 42},
            ],
        },
    })

    result = await importer.aimport_external_facts('Neko', [
        {'entity': 'master', 'text': 'existing fact'},
        {'entity': 'master', 'text': ' dictionary   fact '},
        {'entity': 'master', 'text': '42'},
    ])

    assert result == {'added': 1, 'skipped': 2}
    assert importer.persona['master']['facts'][-1]['text'] == '42'
    assert importer.save_count == 1
