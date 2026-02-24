# N.E.K.O. Automated Test Suite

This directory contains the automated test suite for Project N.E.K.O.

## Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (Fast Python package installer and resolver)

## Setup

1.  **Install dependencies**:
    ```bash
    uv sync
    ```

2.  **Install Playwright browsers** (for frontend and e2e tests):
    ```bash
    uv run playwright install
    ```

3.  **Prepare API Keys**:
    -   Copy `tests/api_keys.json.template` to `tests/api_keys.json`.
    -   Fill in your API keys (OpenAI, Qwen, etc.).

4.  **Prepare Test Data** (optional, for vision tests):
    -   Place a screenshot at `tests/test_inputs/screenshot.png`.
    -   See `tests/test_inputs/script.md` for recording scripts.

## Running Tests

> **IMPORTANT**: All commands must be run using `uv run` to ensure the correct environment.

### Run All Tests (excluding e2e)
```bash
uv run pytest tests/ -s
```

### Run Unit Tests Only
Tests for `OmniOfflineClient`, `OmniRealtimeClient`, and provider API connectivity.
```bash
uv run pytest tests/unit -s
```

### Run Frontend Integration Tests
Tests for Settings, Character Manager, Memory Browser, Voice Clone, and Emotion Manager pages.
```bash
uv run pytest tests/frontend -s
```

### Run End-to-End (E2E) Tests
Full user journey tests (requires `--run-e2e` flag):
```bash
uv run pytest tests/e2e --run-e2e -s
```

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures (server, page, data dir)
├── api_keys.json            # API keys (gitignored)
├── unit/
│   ├── test_providers.py    # Multi-provider API test (Text/TTS/Voice/Video)
│   ├── test_text_chat.py    # OmniOfflineClient text + vision chat
│   ├── test_voice_session.py# OmniRealtimeClient WebSocket session
│   └── test_video_session.py# OmniRealtimeClient video/screen streaming
├── frontend/
│   ├── test_api_settings.py # API key settings page
│   ├── test_chara_settings.py # Character management page
│   ├── test_memory_browser.py # Memory browser page
│   ├── test_voice_clone.py  # Voice clone page
│   └── test_emotion.py      # Live2D + VRM emotion manager pages
├── e2e/
│   └── test_e2e_full_flow.py# Full app journey (8 stages)
├── utils/
│   └── llm_judger.py        # LLM-based response quality evaluator
└── test_inputs/
    ├── script.md            # Recording scripts for audio tests
    └── screenshot.png       # Test screenshot for vision tests
```

## Key Fixtures

| Fixture | Scope | Description |
|---|---|---|
| `loaded_api_keys` | session | Loads API keys from `api_keys.json` |
| `clean_user_data_dir` | session | Temp directory for isolated config/memory |
| `running_server` | session | Starts the FastAPI backend on a random port |
| `mock_page` | function | Playwright page with error monitoring |
| `llm_judger` | session | LLM-based test output evaluator |

## Markers

| Marker | Flag |Description |
|---|---|---|
| `unit` | (none) | Unit tests, run by default |
| `frontend` | (none) | Frontend integration tests, run by default |
| `e2e` | `--run-e2e` | End-to-end tests, skipped unless flagged |
| `manual` | `--run-manual` | Manual tests, skipped unless flagged |
## LLM Judger & Reports

The test suite includes an **LLM Judger** (`tests/utils/llm_judger.py`) that evaluates the quality and correctness of AI responses using various LLM providers (OpenAI, SiliconFlow, Qwen, GLM) with automatic fallback.

### Evaluation Modes

| Mode | Method | Description |
|---|---|---|
| Single-check | `judge()` | Evaluates one input→output pair against criteria (YES/NO) |
| Conversation | `judge_conversation()` | Evaluates a full multi-turn conversation holistically with 5-dimension scoring |

**Conversation Quality Dimensions** (each scored 1-10):
- Coherence, Context Retention, Character Consistency, Response Quality, Engagement

### Report Generation
When tests involving the `llm_judger` are run, results are collected and a **narrative report** is generated at the end of the test session.

- **Storage Location**: `tests/reports/`
- **File Format**: 
  - `test_report_YYYYMMDD_HHMMSS.json`: Machine-readable results with full input/output/verdicts/scores.
  - `test_report_YYYYMMDD_HHMMSS.md`: **LLM-generated narrative report** with executive summary, detailed analysis, and recommendations. Falls back to table format if LLM is unavailable.

### LLM Provider Configuration
The judger uses keys defined in `tests/api_keys.json`. It will attempt to use providers in order of preference and skip those with missing or placeholder keys (e.g., `sk-...`).

---
*(Note: Reports are gitignored and will not be committed to the repository)*
