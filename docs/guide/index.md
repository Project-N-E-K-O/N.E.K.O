# Introduction

**Project N.E.K.O.** (**N**etworked **E**mpathetic **K**nowledging **O**rganism) is an open-source AI companion platform that combines real-time voice/text interaction, Live2D/VRM model rendering, persistent memory, and agent-based task execution into a cohesive experience.

## What is N.E.K.O.?

N.E.K.O. is a UGC (User-Generated Content) platform for AI companions. Users can create, customize, and share AI characters with unique personalities, voices, and visual models. The system supports:

- **Real-time voice conversation** via WebSocket with Realtime API providers (Qwen, OpenAI, Gemini, Step, GLM)
- **Live2D and VRM model rendering** with emotion-mapped animations
- **Persistent memory** across sessions with semantic recall and time-indexed history
- **Background agent execution** via MCP, Computer Use, and Browser Use adapters
- **Voice cloning** with custom TTS voices
- **Steam Workshop integration** for content sharing
- **Plugin system** for developer extensions

## Who is this for?

This documentation is written for **developers** who want to:

- Contribute to the N.E.K.O. core codebase
- Build plugins that extend N.E.K.O.'s capabilities
- Integrate with N.E.K.O.'s REST and WebSocket APIs
- Deploy N.E.K.O. in custom environments
- Understand the system architecture for debugging or extending

## Quick Links

| Goal | Start here |
|------|-----------|
| Set up a dev environment | [Development Setup](./dev-setup) |
| Understand the architecture | [Architecture Overview](/architecture/) |
| Build a plugin | [Plugin Quick Start](/plugins/quick-start) |
| Integrate via API | [API Reference](/api/) |
| Deploy with Docker | [Docker Deployment](/deployment/docker) |
| Configure the system | [Configuration Reference](/config/) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend framework | FastAPI + Uvicorn |
| Realtime communication | WebSocket (native + Alibaba DashScope) |
| Inter-service messaging | ZeroMQ (PUB/SUB + PUSH/PULL) |
| LLM integration | LangChain + OpenAI-compatible APIs |
| TTS | DashScope CosyVoice, GPT-SoVITS |
| Frontend | Vanilla JS, Pixi.js (Live2D), Three.js (VRM) |
| Memory storage | SQLite + text embeddings |
| Package management | uv (Python 3.11) |
| Containerization | Docker (multi-arch) |
