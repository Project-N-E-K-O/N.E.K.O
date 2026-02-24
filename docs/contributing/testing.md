# Testing

N.E.K.O. has a comprehensive test suite covering unit tests, frontend integration tests, and end-to-end flows.

## Setup

```bash
# Install dependencies
uv sync

# Install Playwright browsers (for frontend & e2e tests)
uv run playwright install
```

### API keys for tests

```bash
cp tests/api_keys.json.template tests/api_keys.json
# Edit tests/api_keys.json with your API keys
```

This file is gitignored and will not be committed.

## Running tests

::: warning
All test commands must use `uv run` to ensure the correct Python environment.
:::

```bash
# All tests (excluding e2e)
uv run pytest tests/ -s

# Unit tests only
uv run pytest tests/unit -s

# Frontend integration tests
uv run pytest tests/frontend -s

# End-to-end tests (requires explicit flag)
uv run pytest tests/e2e --run-e2e -s
```

## Test structure

```
tests/
├── conftest.py                # Shared fixtures (server lifecycle, page, data dirs)
├── api_keys.json              # API keys (gitignored)
├── unit/
│   ├── test_providers.py      # Multi-provider API connectivity
│   ├── test_text_chat.py      # OmniOfflineClient text + vision chat
│   ├── test_voice_session.py  # OmniRealtimeClient WebSocket sessions
│   └── test_video_session.py  # OmniRealtimeClient video/screen streaming
├── frontend/
│   ├── test_api_settings.py   # API key settings page
│   ├── test_chara_settings.py # Character management page
│   ├── test_memory_browser.py # Memory browser page
│   ├── test_voice_clone.py    # Voice clone page
│   └── test_emotion.py        # Live2D + VRM emotion manager pages
├── e2e/
│   └── test_e2e_full_flow.py  # Full app journey (8 stages)
├── utils/
│   ├── llm_judger.py          # LLM-based response quality evaluator
│   └── audio_streamer.py      # Audio streaming test utility
└── test_inputs/
    ├── script.md              # Recording scripts for audio tests
    └── screenshot.png         # Test screenshot for vision tests
```

## Test categories

### Unit tests (`tests/unit/`)

Test core backend components in isolation:

- **Provider connectivity**: Verify API connections to all supported providers
- **Text chat**: Test `OmniOfflineClient` with text and vision inputs
- **Voice sessions**: Test `OmniRealtimeClient` WebSocket connections
- **Video sessions**: Test screen sharing and video streaming

### Frontend tests (`tests/frontend/`)

Use Playwright to test Web UI pages:

- **API settings**: Key input, provider switching, save/load
- **Character settings**: CRUD operations, personality editing
- **Memory browser**: Memory file listing, editing, saving
- **Voice clone**: Upload interface, voice preview
- **Emotion manager**: Both Live2D and VRM emotion mapping

### E2E tests (`tests/e2e/`)

Full user journey tests that exercise the complete system. These require the `--run-e2e` flag because they:

- Start real server processes
- Make actual API calls
- Take longer to run

## Test utilities

### LLM Judger (`tests/utils/llm_judger.py`)

An LLM-based evaluator that assesses response quality. Used in e2e tests to verify that character responses are contextually appropriate, in-character, and factually reasonable.

### Playwright patterns

Frontend tests follow a **reconnaissance-then-action** pattern:

1. Navigate to the page
2. Wait for `networkidle` (critical for JS-rendered content)
3. Inspect the rendered DOM
4. Execute actions using discovered selectors

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('http://localhost:48911')
    page.wait_for_load_state('networkidle')
    # Now safe to interact with the page
```

::: tip
Always launch Chromium in headless mode for CI compatibility. Wait for `networkidle` before inspecting any dynamic content.
:::

## Writing new tests

1. Place test files in the appropriate subdirectory (`unit/`, `frontend/`, `e2e/`)
2. Use pytest markers: `@pytest.mark.unit`, `@pytest.mark.frontend`, `@pytest.mark.e2e`
3. Use shared fixtures from `conftest.py` for server lifecycle and page setup
4. Follow existing naming convention: `test_<module>_<feature>.py`
