"""Router reasoning dialects (Opper, OrcaRouter, Requesty floors) at the OpenAI SDK wire boundary."""

import json

import httpx
import openai
import pytest

from config import providers as P
from config.providers import focus_extra_body
from main_logic.omni_offline_client import OmniOfflineClient
from utils.llm_client import create_chat_llm

OPPER = 'https://api.opper.ai/v3/compat'
ORCAROUTER = 'https://api.orcarouter.ai/v1'
OPENROUTER = 'https://openrouter.ai/api/v1'
REQUESTY = 'https://router.requesty.ai/v1'


def _wire_bodies(model, endpoint, override_sets):
    """Serialize ``_params`` through the real SDK and return the JSON bodies."""
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            'id': 'test-completion', 'object': 'chat.completion', 'created': 0,
            'model': model,
            'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'},
                         'finish_reason': 'stop'}],
        })

    client = create_chat_llm(model, endpoint, 'test-key')
    try:
        with openai.OpenAI(
            api_key='test-key', base_url=endpoint,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        ) as sdk:
            messages = [{'role': 'user', 'content': 'Synthetic reasoning test'}]
            for overrides in override_sets:
                sdk.chat.completions.create(**client._params(messages, **overrides))
        return captured, client.extra_body
    finally:
        client.close()


@pytest.mark.unit
@pytest.mark.parametrize('model', sorted(P.OPPER_REASONING_EFFORT))
def test_opper_gemini_regular_and_focus_wire_payloads(model):
    """Bare and gemini/ ids drop the ignored Google knob for the table's top-level effort."""
    regular, focus = P.OPPER_REASONING_EFFORT[model]
    bodies, instance_extra = _wire_bodies(model, OPPER, (
        {}, {'extra_body': focus_extra_body(model, OPPER)},
        {'extra_body': None}, {'reasoning_effort': 'high'},
    ))
    assert bodies[0]['reasoning_effort'] == regular
    assert bodies[1]['reasoning_effort'] == focus
    assert 'reasoning_effort' not in bodies[2]
    assert bodies[3]['reasoning_effort'] == 'high'
    assert all('extra_body' not in body and 'google' not in body for body in bodies)
    assert instance_extra == P.MODELS_EXTRA_BODY_MAP[model.removeprefix('gemini/')]


@pytest.mark.unit
def test_opper_effort_table_matches_measurements_and_floor():
    """Pin the measured choices: bare 2.5-flash ignores none / minimal, 3.x sits on minimal, 3.8 on low."""
    table = P.OPPER_REASONING_EFFORT
    assert table['gemini-2.5-flash'] == ('low', 'low')
    assert table['gemini/gemini-2.5-flash'] == ('minimal', 'low')
    for upstream in ('gemini-3.7-flash', 'gemini-3.8-flash'):
        assert table[upstream] == table['gemini/' + upstream] == ('low', 'low')
    for upstream in ('gemini-3-flash-preview', 'gemini-3.1-flash-lite', 'gemini-3.5-flash',
                     'gemini-3.5-flash-lite', 'gemini-3.6-flash'):
        assert table[upstream] == table['gemini/' + upstream] == ('minimal', 'low')
    for model, efforts in table.items():
        assert P.resolve_router_model(model, OPPER) in P.MODELS_EXTRA_BODY_MAP
        for effort in efforts:
            assert P.clamp_reasoning_effort(model, effort) == effort
    assert P.ROUTER_REASONING['api.opper.ai'].efforts is table


@pytest.mark.unit
@pytest.mark.parametrize('endpoint', [
    'http://localhost:4000/v1', OPENROUTER, ORCAROUTER, 'https://api.opper.ai.example.test/v3/compat',
])
def test_gemini_prefixed_ids_stay_unregistered_off_opper(endpoint):
    """gemini/ ids resolve only on Opper, so other proxies using the same id get no knob."""
    assert 'gemini/gemini-3.8-flash' not in P.MODELS_EXTRA_BODY_MAP
    bodies, instance_extra = _wire_bodies('gemini/gemini-3.8-flash', endpoint, (
        {}, {'extra_body': focus_extra_body('gemini/gemini-3.8-flash', endpoint)},
    ))
    assert not instance_extra
    for body in bodies:
        assert 'reasoning_effort' not in body and 'reasoning' not in body and 'extra_body' not in body


@pytest.mark.unit
@pytest.mark.parametrize('model', ['claude-sonnet-5-5', 'claude-haiku-4-5-20251001'])
def test_opper_leaves_claude_without_reasoning_effort(model):
    """Claude gets no effort on Opper (low turns thinking on for 4.x, 5.x cannot be stopped)."""
    bodies, _ = _wire_bodies(model, OPPER, ({}, {'extra_body': focus_extra_body(model, OPPER)}))
    for body in bodies:
        assert 'reasoning_effort' not in body
        assert body['thinking'] == P.MODELS_EXTRA_BODY_MAP[model]['thinking']


@pytest.mark.unit
@pytest.mark.parametrize('endpoint', [
    'https://generativelanguage.googleapis.com/v1beta/openai',
    'https://api.opper.ai.example.test/v3/compat',
    'https://example.test/api.opper.ai/v3/compat',
])
def test_other_endpoints_keep_google_dialect(endpoint):
    """Scope the Opper rewrite to the exact Opper host."""
    client = create_chat_llm('gemini-3.8-flash', endpoint, 'test-key')
    try:
        params = client._params([{'role': 'user', 'content': 'Synthetic test'}])
        assert params['extra_body'] == P.EXTRA_BODY_GEMINI_3
        assert 'reasoning_effort' not in params
    finally:
        client.close()


@pytest.mark.unit
def test_opper_preserves_explicit_effort_and_unrelated_extras():
    """A caller's own effort wins and unrelated keys survive without mutating inputs."""
    extras = {**P.EXTRA_BODY_GEMINI_3, 'reasoning_effort': 'medium', 'metadata': {'tag': 'synthetic'}}
    snapshot = json.dumps(extras, sort_keys=True)
    client = create_chat_llm('gemini-3.8-flash', OPPER, 'test-key')
    try:
        params = client._params([], extra_body=extras)
        assert params['extra_body'] == {'reasoning_effort': 'medium', 'metadata': {'tag': 'synthetic'}}
        assert 'reasoning_effort' not in params
        params = client._params([], extra_body={**P.EXTRA_BODY_GEMINI_3, 'metadata': {'tag': 'x'}})
        assert params['extra_body'] == {'metadata': {'tag': 'x'}}
        assert params['reasoning_effort'] == 'low'
        assert json.dumps(extras, sort_keys=True) == snapshot
        assert P.EXTRA_BODY_GEMINI_3 == {
            'extra_body': {'google': {'thinking_config': {'thinking_level': 'low', 'include_thoughts': False}}},
        }
    finally:
        client.close()


@pytest.mark.unit
def test_opper_unrecognized_thinking_body_passes_through():
    """A custom thinking body that is neither registered form is not guessed at."""
    custom = {'extra_body': {'google': {'thinking_config': {'thinking_level': 'high'}}}}
    assert P.rewrite_router_reasoning(OPPER, 'gemini-3.8-flash', custom) == (custom, None)
    assert P.rewrite_router_reasoning(OPPER, 'unknown-model', custom) == (custom, None)
    assert P.rewrite_router_reasoning(None, 'gemini-3.8-flash', custom) == (custom, None)


@pytest.mark.unit
@pytest.mark.parametrize('model, native', [
    ('anthropic/claude-sonnet-5.5', 'claude-sonnet-5-5'),
    ('anthropic/claude-haiku-4.5', 'claude-haiku-4-5-20251001'),
    ('anthropic/claude-sonnet-4.6', 'claude-sonnet-4-6'),
    ('anthropic/claude-opus-4.7', 'claude-opus-4-7'),
])
def test_orcarouter_claude_sends_native_thinking_block(model, native):
    """OrcaRouter passes Anthropic's thinking block through, so its ids map onto native Claude."""
    assert P.resolve_router_model(model, ORCAROUTER) == native
    expected = P.MODELS_EXTRA_BODY_MAP[native]
    bodies, instance_extra = _wire_bodies(model, ORCAROUTER, (
        {}, {'extra_body': focus_extra_body(model, ORCAROUTER)},
    ))
    assert instance_extra == expected
    for body in bodies:
        assert body['thinking'] == expected['thinking']
        assert 'reasoning_effort' not in body and 'reasoning' not in body


@pytest.mark.unit
@pytest.mark.parametrize('model, native', [
    ('deepseek/deepseek-v4-flash', 'deepseek-v4-flash'),
    ('deepseek/deepseek-v4-pro', 'deepseek-v4-pro'),
    ('deepseek/deepseek-v4-flash-vision-exp', 'deepseek-v4-flash-vision-exp'),
    ('deepseek/deepseek-v4-flash-free', 'deepseek-v4-flash'),
])
def test_orcarouter_deepseek_sends_native_thinking_block(model, native):
    """OrcaRouter passes DeepSeek's thinking.type through; measured 0 reasoning tokens when disabled."""
    assert P.resolve_router_model(model, ORCAROUTER) == native
    expected = P.MODELS_EXTRA_BODY_MAP[native]
    focus = focus_extra_body(model, ORCAROUTER)
    assert focus == P.MODELS_FOCUS_EXTRA_BODY_MAP[native]
    bodies, instance_extra = _wire_bodies(model, ORCAROUTER, ({}, {'extra_body': focus}))
    assert instance_extra == expected
    assert bodies[0]['thinking'] == expected['thinking']
    assert bodies[1]['thinking'] == focus['thinking']
    for body in bodies:
        assert 'reasoning_effort' not in body and 'reasoning' not in body


@pytest.mark.unit
@pytest.mark.parametrize('model', ['deepseek/deepseek-v4.1-flash', 'deepseek/deepseek-reasoner'])
def test_orcarouter_unregistered_deepseek_sends_nothing(model):
    """DeepSeek ids without a native entry keep sending no thinking block."""
    assert P.resolve_router_model(model, ORCAROUTER) == model
    bodies, instance_extra = _wire_bodies(model, ORCAROUTER, ({},))
    assert not instance_extra
    assert 'thinking' not in bodies[0]


@pytest.mark.unit
@pytest.mark.parametrize('endpoint', [
    OPENROUTER, REQUESTY, OPPER, 'https://api.orcarouter.ai.example.test/v1', 'http://localhost:4000/v1',
])
def test_deepseek_prefixed_ids_untouched_off_orcarouter(endpoint):
    """OpenRouter also names it deepseek/deepseek-v4-flash; only OrcaRouter maps it to native DeepSeek."""
    model = 'deepseek/deepseek-v4-flash'
    assert P.resolve_router_model(model, endpoint) == model
    bodies, instance_extra = _wire_bodies(model, endpoint, (
        {}, {'extra_body': focus_extra_body(model, endpoint)},
    ))
    assert not instance_extra
    for body in bodies:
        assert 'thinking' not in body


@pytest.mark.unit
def test_orcarouter_default_models_resolve():
    """The OrcaRouter profile's default models all reach a registered native entry."""
    from config.api_profiles import DEFAULT_ASSIST_API_PROFILES

    profile = DEFAULT_ASSIST_API_PROFILES['orcarouter']
    models = {v for k, v in profile.items() if k.endswith('_MODEL')}
    assert models
    for model in models:
        assert P.resolve_router_model(model, profile['OPENROUTER_URL']) in P.MODELS_EXTRA_BODY_MAP


@pytest.mark.unit
@pytest.mark.parametrize('model', ['anthropic/claude-opus-5.5', 'anthropic/claude-fable-5.1'])
def test_orcarouter_unregistered_claude_sends_nothing(model):
    """Models that cannot turn thinking off keep the native default (no thinking block)."""
    bodies, instance_extra = _wire_bodies(model, ORCAROUTER, ({},))
    assert not instance_extra
    assert 'thinking' not in bodies[0]


@pytest.mark.unit
@pytest.mark.parametrize('endpoint', [
    OPENROUTER, REQUESTY, OPPER, 'https://api.orcarouter.ai.example.test/v1', 'http://localhost:4000/v1',
])
def test_anthropic_dotted_ids_untouched_off_orcarouter(endpoint):
    """OpenRouter shares the anthropic/ naming but wants reasoning:{...}; never send it a thinking block."""
    model = 'anthropic/claude-sonnet-5.5'
    assert P.resolve_router_model(model, endpoint) == model
    bodies, instance_extra = _wire_bodies(model, endpoint, (
        {}, {'extra_body': focus_extra_body(model, endpoint)},
    ))
    assert not instance_extra
    for body in bodies:
        assert 'thinking' not in body and 'reasoning_effort' not in body


@pytest.mark.unit
@pytest.mark.parametrize('model, effort, clamped', [
    ('claude-sonnet-5-5', 'none', 'low'),
    ('anthropic/claude-haiku-4.5', 'minimal', 'low'),
    ('gemini-3.8-flash', 'minimal', 'low'),
    ('google/gemini-3.8-flash', 'none', 'low'),
    ('gemini-3.7-flash', 'minimal', 'low'),
    ('google/gemini-3.7-flash', 'none', 'low'),
    ('google/gemini-3-pro-preview', 'minimal', 'low'),
    ('gemini/gemini-3.1-pro-preview', 'none', 'low'),
    ('google/gemini-3.1-pro-preview:flex', 'minimal', 'low'),
    ('google/gemini-3.5-flash:free', 'none', 'minimal'),
    ('anthropic/claude-sonnet-5.5:thinking', 'none', 'low'),
    ('google/gemini-3.5-flash', 'none', 'minimal'),
    ('gemini/gemini-3-flash-preview', 'none', 'minimal'),
    ('gemini-3.6-flash', 'minimal', 'minimal'),
    ('gemini-3.5-flash-lite', 'none', 'minimal'),
    ('gemini-2.5-flash', 'none', 'minimal'),
    ('gemini-2.5-flash-lite', 'none', 'none'),
    ('gpt-5-nano', 'none', 'minimal'),
    ('gpt-5.6-luna', 'none', 'none'),
    ('gemini-3.8-flash', 'high', 'high'),
    ('gemini-3.8-flash', 'auto', 'auto'),
])
def test_upstream_floor_clamp(model, effort, clamped):
    assert P.clamp_reasoning_effort(model, effort) == clamped


@pytest.mark.unit
def test_registered_router_efforts_respect_upstream_floor():
    """Every registered effort, regular and Focus, already sits at or above the vendor floor."""
    for registry in (P.MODELS_EXTRA_BODY_MAP, P.MODELS_FOCUS_EXTRA_BODY_MAP):
        for model, body in registry.items():
            reasoning = body.get('reasoning')
            efforts = [body.get('reasoning_effort'), reasoning.get('effort') if isinstance(reasoning, dict) else None]
            for effort in filter(None, efforts):
                assert P.clamp_reasoning_effort(model, effort) == effort, (model, effort)


@pytest.mark.unit
def test_requesty_clamps_effort_below_floor():
    """A caller's below-floor OpenRouter effort is raised before reaching Requesty."""
    client = create_chat_llm('google/gemini-3.8-flash', REQUESTY, 'test-key')
    try:
        params = client._params([], extra_body={'reasoning': {'effort': 'none'}})
        assert params['reasoning_effort'] == 'low'
        assert 'extra_body' not in params
        params = client._params([], extra_body={'reasoning': {'effort': 'minimal'}})
        assert params['reasoning_effort'] == 'low'
    finally:
        client.close()
    client = create_chat_llm('google/gemini-3.5-flash', REQUESTY, 'test-key')
    try:
        assert client._params([])['reasoning_effort'] == 'minimal'
        assert client._params([], extra_body={'reasoning': {'effort': 'none'}})['reasoning_effort'] == 'minimal'
    finally:
        client.close()


@pytest.mark.unit
def test_focus_overrides_follow_router_host():
    """Focus on a router id uses the host-resolved form and only reserves headroom when it differs."""
    overrides = OmniOfflineClient._focus_stream_overrides(
        True, 'gemini/gemini-2.5-flash', base_max_tokens=500, base_url=OPPER,
    )
    assert overrides['extra_body'] == P.EXTRA_BODY_GEMINI_THINKING
    assert overrides['max_completion_tokens'] > 500
    overrides = OmniOfflineClient._focus_stream_overrides(
        True, 'anthropic/claude-sonnet-5.5', base_max_tokens=500, base_url=ORCAROUTER,
    )
    assert overrides == {'extra_body': P.EXTRA_BODY_ANTHROPIC_BETWEEN_TOOLS}
    assert OmniOfflineClient._focus_stream_overrides(
        True, 'gemini/gemini-2.5-flash', base_max_tokens=500,
    ) == {'extra_body': None}
