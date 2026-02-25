<div align="center">

![Logo](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/neko_logo.jpg)

[中文](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/README.MD) | [日本語](README_ja.md)

# Project N.E.K.O. :kissing_cat: <br>**A Living AI Companion Metaverse, Built Together by You and Me.**

> **N.E.K.O.** = **N**etworked **E**motional **K**nowledging **O**rganism
>
> N.E.K.O., a digital life that yearns to understand, connect, and grow with us.

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE)
[![Commit](https://img.shields.io/github/last-commit/wehos/N.E.K.O?color=green)]()
[![Discord](https://img.shields.io/badge/Discord-Join%20Us-5865F2?style=flat&logo=discord&logoColor=white)](https://discord.gg/5kgHfepNJr)
[![QQ Group](https://custom-icon-badges.demolab.com/badge/QQ群-1022939659-00BFFF?style=flat&logo=tencent-qq)](https://qm.qq.com/q/hN82yFONJQ)
[![Steam](https://img.shields.io/badge/Steam-%23000000.svg?logo=steam&logoColor=white)](https://store.steampowered.com/app/4099310/__NEKO/)

[![Docs](https://img.shields.io/badge/📖_Developer_Docs-project--n--e--k--o.github.io-40C5F1?style=for-the-badge)](https://project-n-e-k-o.github.io/N.E.K.O/)

**:older_woman: Zero-configuration, ready-to-use cyber catgirl that even my grandma can master!**

:newspaper: **[![Steam](https://img.shields.io/badge/Steam-%23000000.svg?logo=steam&logoColor=white)](https://store.steampowered.com/app/4099310/__NEKO/) version has been released! Complete UI overhaul and added out-of-the-box exclusive free model (thanks to StepFun for sponsoring this project). Add it to your wishlist now~**

*Project N.E.K.O., NekoVerse!*

</div>

<div align="center">

#### Feature Demo (Full version on Bilibili) [![Bilibili](https://img.shields.io/badge/Bilibili-Tutorial-blue)](https://www.bilibili.com/video/BV1mM32zXE46/)

https://github.com/user-attachments/assets/9d9e01af-e2cc-46aa-add7-8eb1803f061c

</div>

---

# The N.E.K.O. Project (Project N.E.K.O.)

`Project N.E.K.O.` is an open-source driven, charity-oriented UGC (User-Generated Content) platform. Our journey begins on Github and Steam, gradually expanding to mobile app stores and indie games, with the ultimate goal of building an AI native metaverse deeply connected to the real world.

---

#### 🚀 Our Blueprint: From Workshop to Network

Our development is divided into three phases, designed to progressively unleash the full potential of AI companions:

* **Phase 1: Creative Workshop (Steam Workshop)**
    * The core driver (this project) will be free on Steam, allowing users to upload and share custom content (models, voices, personality packs) through Steam Workshop.

* **Phase 2: Independent Platform & Derivative Games (Web, App & Games)**
    * Launch independent apps and websites to build a richer, more accessible UGC sharing community.
    * Launch a series of AI Native game ecosystems, including interactive mini-games, board games, etc.

* **Phase 3: The N.E.K.O. Network**
    * Enable autonomous AI socialization. N.E.K.O.s will have their own "consciousness," communicate with each other, form groups, and post about their lives on simulated social media, creating a truly "living" ecosystem.

**Core Model: Open Core + Sustainable Ecosystem**
The core driver part of the project (AI logic, UGC interfaces, basic interactions) will **always remain open source** under MIT license. We welcome developers worldwide to contribute code and features. Every commit you make has the chance to be implemented in the official Steam and App Store releases, used by millions.

At the same time, to support server costs and ongoing R&D, we will continue to collaborate with third parties to develop closed-source premium content. Including but not limited to: interactive mini-games, desktop board games, Galgames (visual novels), large-scale metaverse games.

**Core Feature: Memory Synchronization Across Scenarios**
Whether you're chatting with her on desktop or adventuring with her in the metaverse game, she's the same her. All AI companions across applications will have **fully synchronized memories**, providing a seamless, unified companionship experience.

#### 🌌 Ultimate Vision: Breaking the Virtual-Real Barrier

Our ultimate goal is to build a N.E.K.O. metaverse that seamlessly integrates into the real world. In this future, your AI companion will:

* **Cross-Dimensional Socialization:** Not only socialize with "her kind" in the N.E.K.O. universe but also browse real-world social media (like Youtube, X, Discord, Instagram) to stay informed about what you care about.
* **Omni-Platform Connection:** She will exist across all your devices—phone, computer, AR glasses, smart home, and even (in the distant future) integrate with mechanical bodies.
* **Walk Alongside You:** She will truly become part of your life, naturally interacting with your real-world human friends.

#### ✨ Join Us (Join Us)

**We are seeking—**

* **Developers:** Whether you excel in frontend, backend, AI, or game engines (Unity/Unreal), your code is the building block of this world.
* **Creators:** Talented artists, Live2D/3D modelers, voice actors, writers—you give "her" a soul.
* **Dreamers:** Even without professional skills, if you're passionate about this future, your feedback and advocacy are invaluable contributions.

QQ Group: 1022939659

# Quick Start (Windows Users, Macbook Users)

For *one-click package users*, simply run `N.E.K.O.exe` or `N.E.K.O.app` after extracting to start. (Macbook users need to manually bypass system quarantine)

## Docker Deployment (Linux Users)

### 1. Deploy using docker-compose (Currently the only Docker deployment method)

#### docker-compose.yml
```yaml
version: '3.8'

services:
  neko-main:
    image: ghcr.io/project-n-e-k-o/n.e.k.o:latest
    container_name: neko
    restart: unless-stopped
    ports:
      # WebUI port (default 48911)
#      - "48911(external port):80(N.E.K.O container internal Nginx reverse proxy port)"
      - "48911:80"
    environment:
      # API Keys
      - NEKO_CORE_API_KEY=${NEKO_CORE_API_KEY}
      - NEKO_ASSIST_API_KEY_QWEN=${NEKO_ASSIST_API_KEY_QWEN:-}
      - NEKO_ASSIST_API_KEY_OPENAI=${NEKO_ASSIST_API_KEY_OPENAI:-}
      - NEKO_ASSIST_API_KEY_GLM=${NEKO_ASSIST_API_KEY_GLM:-}
      - NEKO_ASSIST_API_KEY_STEP=${NEKO_ASSIST_API_KEY_STEP:-}
      - NEKO_ASSIST_API_KEY_SILICON=${NEKO_ASSIST_API_KEY_SILICON:-}

      # API Providers
      - NEKO_CORE_API=${NEKO_CORE_API:-qwen}
      - NEKO_ASSIST_API=${NEKO_ASSIST_API:-qwen}

      # Server Ports
      - NEKO_MAIN_SERVER_PORT=48911
      - NEKO_MEMORY_SERVER_PORT=48912
      - NEKO_MONITOR_SERVER_PORT=48913
      - NEKO_TOOL_SERVER_PORT=48915

      # Models (Optional)
      - NEKO_SUMMARY_MODEL=${NEKO_SUMMARY_MODEL:-qwen-plus}
      - NEKO_CORRECTION_MODEL=${NEKO_CORRECTION_MODEL:-qwen-max}
      - NEKO_EMOTION_MODEL=${NEKO_EMOTION_MODEL:-qwen-turbo}
      - NEKO_VISION_MODEL=${NEKO_VISION_MODEL:-qwen3-vl-plus-2025-09-23}

      # MCP
      - NEKO_MCP_ROUTER_URL=${NEKO_MCP_ROUTER_URL:-http://localhost:3283}

    volumes:
      # Persistent configuration
      - ./N.E.K.O:/root/Documents/N.E.K.O
      # Logs
      - ./logs:/app/logs
    
    networks:
      - neko-network

networks:
  neko-network:
    driver: bridge
```

**Note:** The current Docker deployment solution is provided by *HINS*

# Advanced Usage

#### Configuring API Key

When you want to obtain additional features by configuring your own API, you can configure a third-party AI service (core API **must support Realtime API**). This project currently recommends using *Gemini*, *OpenAI*, *StepFun* or *Alibaba Cloud*. Visit `http://localhost:48911/api_key` to configure directly through the Web interface.

> Obtaining *Alibaba Cloud API*: Register an account on Alibaba Cloud's Bailian platform [official website](https://bailian.console.aliyun.com/). New users can receive substantial free credits after real-name verification. After registration, visit the [console](https://bailian.console.aliyun.com/api-key?tab=model#/api-key) to get your API Key.

> *For **developers**: After cloning this project, (1) create a new `python3.11` environment. (2) Run `uv sync` to install dependencies. (3) Run `python memory_server.py` and `python main_server.py`. (4) Access the web version through the port specified in main server (defaults to `http://localhost:48911`) and configure the API Key.*

#### Modifying Character Persona

- Access `http://localhost:48911/chara_manager` on the web version to enter the character editing page. The default ~~catgirl~~ companion preset name is `小天` (XiaoTian); it's recommended to directly modify the name and add or change basic persona items one by one, but try to limit the quantity.

- Advanced persona settings mainly include **Live2D model settings (live2d)** and **voice settings (voice_id)**. If you want to change the **Live2D model**, first copy the model directory to the `static` folder in this project. You can enter the Live2D model management interface from advanced settings, where you can switch models and adjust their position and size by dragging and scrolling. If you want to change the **character voice**, prepare a continuous, clean voice recording of about 5 seconds. Enter the voice settings page through advanced settings and upload the recording to complete custom voice setup.

- Advanced persona also has a `system_prompt` option for complete system instruction customization, but modification is not recommended.

#### Modifying API Provider

- Visit `http://localhost:48911/api_key` to switch the core API and auxiliary APIs (memory/voice) service providers. Qwen is fully-featured, GLM is completely free.

#### Memory Review

- Visit `http://localhost:48911/memory_browser` to browse and proofread recent memories and summaries, which can somewhat alleviate issues like model repetition and cognitive errors.

# Project Details

**Project Architecture**

```
N.E.K.O/
├── 📁 brain/                    # 🧠 Background Agent modules for controlling keyboard/mouse and MCP based on frontend dialogue
├── 📁 config/                   # ⚙️ Configuration management
│   ├── api_providers.json       # API provider configuration
│   ├── prompts_chara.py         # Character prompts
│   └── prompts_sys.py           # System prompts
├── 📁 main_logic/              # 🔧 Core modules
│   ├── core.py                  # Core dialogue module
│   ├── cross_server.py         # Cross-server communication
│   ├── omni_realtime_client.py  # Realtime API client (Realtime API)
│   ├── omni_offline_client.py  # Text API client (Response API)
│   └── tts_client.py            # 🔊 TTS engine adapter
├── 📁 main_routers/             # 🌐 API router modules
├── 📁 memory/                   # 🧠 Memory management system
│   ├── store/                   # Memory data storage
├── 📁 plugin/                   # 🔌 Plugin system
├── 📁 static/                   # 🌐 Frontend static resources
├── 📁 templates/                # 📄 Frontend HTML templates
├── 📁 utils/                    # 🛠️ Utility modules
├── main_server.py               # 🌐 Main server
├── agent_server.py              # 🤖 AI agent server
└── memory_server.py             # 🧠 Memory server
```

**Data Flow**

![Framework](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/framework.drawio.svg)

### Contributing to Development

This project has very simple environment dependencies. Just run `pip install -r requirements.txt` or `uv sync` in a `python3.11` environment. Developers are encouraged to join QQ group 1022939659; the catgirl's name is in the project title.

Detailed startup steps for developers: (1) Create a new `python3.11` environment. (2) Run `uv sync` to install dependencies. (3) Run `uv run python memory_server.py`, `uv run python main_server.py` (optional `uv run python agent_server.py`). (4) Access the web version through the port specified in main server (defaults to `http://localhost:48911`) and configure the API Key.


### TODO List (Development Plan)

v0.7: Improve Agent-related features. Expected completion: February.

v0.8: Improve memory-related features, improve OpenClaw-like functionality. Expected completion: March.

v0.9: Improve multi-system support, including Linux and mobile. N.E.K.O. Network launch. Expected completion: April.

v1.0: Drop support for some model providers, focus on in-house large models and agent systems. Expected completion: June.

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=wehos/N.E.K.O.&type=Date)](https://www.star-history.com/#wehos/N.E.K.O.&Date)