# Model Configuration

N.E.K.O. resolves models by **role**, not through one global model string. The selected profile supplies defaults; supported values in `core_config.json` can override individual roles.

| Role | Runtime field |
| --- | --- |
| Core | `CORE_MODEL` |
| Conversation | `CONVERSATION_MODEL` |
| Summary | `SUMMARY_MODEL` |
| Correction | `CORRECTION_MODEL` |
| Emotion | `EMOTION_MODEL` |
| Vision | `VISION_MODEL` |
| Agent | `AGENT_MODEL` |
| Realtime | `REALTIME_MODEL` |
| TTS | `TTS_MODEL` |

Feature code may derive additional role values from these settings.

## Recommended flow

1. Select Core and Assist providers in the Web UI.
2. Enter credentials for the selected providers.
3. Run the UI connectivity checks.
4. Only then configure a supported per-role model, URL, or key.

A saved resolved provider URL is reused only while it remains in the current profile's candidate set.

## Avoid catalog snapshots

Model IDs, endpoints, thinking controls, token limits, and voice catalogs are provider-specific and change over time. Use the Web UI and `config/api_providers.json` from the same revision as the running app. Examples are not compatibility promises.

Adding a role or field requires synchronized loader, config-manager, router/UI, tests, and all eight locale files. Do not silently pass unsupported parameters; model wrappers deliberately omit options incompatible with reasoning/extended-thinking APIs.
