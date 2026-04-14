<div align="center">

![Logo](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/neko_logo.jpg)

[中文](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/README.MD) | [日本語](README_ja.md) | [Русский](README_ru.md)

# Project N.E.K.O. :kissing_cat: <br>**A proactive, native omni-modal AI companion featuring 24/7 ambient awareness, agent capability and an embodied emotional engine.**

> **N.E.K.O.** = **N**etworked **E**motional **K**nowledging **O**rganism
>
> N.E.K.O., a digital life that yearns to understand, connect, and grow with us.

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE)
[![Commit](https://img.shields.io/github/last-commit/wehos/N.E.K.O?color=green)](https://github.com/Project-N-E-K-O/N.E.K.O/commits)
[![Discord](https://img.shields.io/badge/Discord-Join%20Us-5865F2?style=flat&logo=discord&logoColor=white)](https://discord.gg/5kgHfepNJr)
[![QQ Group](https://custom-icon-badges.demolab.com/badge/QQ群-1022939659-00BFFF?style=flat&logo=tencent-qq)](https://qm.qq.com/q/hN82yFONJQ)
[![Steam](https://img.shields.io/badge/Steam-%23000000.svg?logo=steam&logoColor=white)](https://store.steampowered.com/app/4099310/__NEKO/)

[![Docs](https://img.shields.io/badge/📖_Developer_Docs-project--neko.online-40C5F1?style=for-the-badge)](https://project-neko.online)

**:older_woman: Zero-configuration, ready-to-use cyber catgirl that even my grandma can master!**

:newspaper: **[![Steam](https://img.shields.io/badge/Steam-%23000000.svg?logo=steam&logoColor=white)](https://store.steampowered.com/app/4099310/__NEKO/) version has been released! Complete UI overhaul and added out-of-the-box exclusive free model (thanks to StepFun for sponsoring this project). Add it to your wishlist now~**

*Project N.E.K.O., NekoVerse!*

</div>

<div align="center">

#### Feature Demo (Full version on Bilibili) [![Bilibili](https://img.shields.io/badge/Bilibili-Tutorial-blue)](https://www.bilibili.com/video/BV1mM32zXE46/)

https://github.com/user-attachments/assets/9d9e01af-e2cc-46aa-add7-8eb1803f061c

</div>

---

## Core Features

<table>
<tr>
<td align="center" width="25%">🎙️<br><b>Omni-Modal Dialogue</b><br>Real-time voice (Realtime API) + text chat (ChatCompletion) with visual understanding</td>
<td align="center" width="25%">🧠<br><b>Three-Tier Memory</b><br>Facts / Reflection / Persona — she truly "remembers" you</td>
<td align="center" width="25%">🤖<br><b>Agent Capabilities</b><br>Browser control (CUA), computer use, OpenClaw A2A calling — she can get work done for you</td>
<td align="center" width="25%">🎭<br><b>Multi-Form Avatar</b><br>Live2D / VRM / MMD with motion capture and full-screen tracking support</td>
</tr>
<tr>
<td align="center">🔌<br><b>Plugin Ecosystem</b><br>Full plugin SDK & marketplace for custom extensions</td>
<td align="center">🌐<br><b>14+ AI Providers</b><br>OpenAI / Gemini / Qwen / DeepSeek and more, with free models out of the box</td>
<td align="center">💬<br><b>Proactive Chat</b><br>24/7 ambient awareness: screen understanding, social media trends, personal feeds, music & memes — she initiates conversations with you</td>
<td align="center">🏪<br><b>UGC Workshop</b><br>Upload and share custom characters, models, and voice packs via Steam Workshop</td>
</tr>
</table>

---

# The N.E.K.O. Project

`Project N.E.K.O.` is an open-source-driven AI companion platform. The core driver is **always open source** under MIT license — every contribution you make has the chance to ship in the official Steam and App Store releases.

---

### 🚀 Current Status & Roadmap

* **✅ Steam Workshop**: Live. Users can upload and share custom characters, models, and voice packs.
* **🚧 [K.U.R.O.](https://github.com/Project-N-E-K-O/K.U.R.O)**: The first AI Native indie game built on the N.E.K.O. ecosystem, in development.
* **🚧 Mobile**: iOS / Android adaptation in progress.
* **🚧 The N.E.K.O. Network**: Autonomous AI socialization — N.E.K.O.s will have their own "consciousness," communicate with each other, form groups, and post about their lives on simulated social media. Coming soon.

**Cross-Scenario Memory Sync**: Whether you're chatting with her on desktop or adventuring with her in a game, she's the same her. All AI companions across applications **fully synchronize memories**.

#### ✨ Join Us

* **Developers:** Frontend, backend, AI, game engines (Unity/Unreal) — your code is the building block of this world.
* **Creators:** Artists, Live2D/3D modelers, voice actors, writers — you give "her" a soul.
* **Dreamers:** Your feedback and advocacy are invaluable contributions.

**Discord**: [Join Us](https://discord.gg/5kgHfepNJr) | **QQ Group**: [1022939659](https://qm.qq.com/q/HxeaMdSkQW)

## Quick Start

### Windows / macOS Users (One-Click Package)

Simply run `N.E.K.O.exe` or `N.E.K.O.app` after extracting to start. (macOS users need to manually bypass system quarantine)

### Docker Deployment (Linux)

<details>
<summary>Click to expand Docker deployment guide</summary>

#### Method 1: Docker Compose (Recommended)

<details>
<summary>Click to view docker-compose.yml</summary>

```yaml
version: '3.8'
services:
  neko-main:
    image: docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:latest
    container_name: neko
    restart: unless-stopped
    ports:
      - "48911:80"   # HTTP port
      - "48912:443"  # HTTPS port
    volumes:
      - ./N.E.K.O:/root/Documents/N.E.K.O
      - ./logs:/app/logs
      - ./ssl:/root/ssl
    networks:
      - neko-network
networks:
  neko-network:
    driver: bridge
```

**Start:**
```bash
docker-compose up -d
```

**Common commands:**
- View logs: `docker-compose logs -f`
- Stop: `docker-compose down`
- Restart: `docker-compose restart`

</details>

#### Method 2: Docker Run

<details>
<summary>Click to view docker run command</summary>

```bash
NEKO_BASE_PATH="/home/neko/neko-data" && \
docker network create --driver bridge neko-network 2>/dev/null || true
docker run -d \
  --name neko \
  --restart unless-stopped \
  -p 48911:80 \
  -p 48912:443 \
  -v "${NEKO_BASE_PATH}/N.E.K.O:/root/Documents/N.E.K.O" \
  -v "${NEKO_BASE_PATH}/logs:/app/logs" \
  -v "${NEKO_BASE_PATH}/ssl:/root/ssl" \
  --network neko-network \
  docker.gh-proxy.org/ghcr.io/project-n-e-k-o/n.e.k.o:latest
```

##### 📁 Directory Structure
After startup, the following directory structure is automatically generated:
```plaintext
current_directory/
├── N.E.K.O/      # Configuration and data
├── logs/         # Application logs
├── ssl/          # SSL certificates
└── docker-compose.yml
```

</details>

#### 🔐 SSL Certificate Configuration

<details>
<summary>Click to view SSL certificate details</summary>

##### Automatic Certificate
On first container startup, a self-signed certificate valid for **1000 years** is automatically generated and saved in the `./ssl/` directory.

##### Custom Certificate
To use your own SSL certificate:

**Method 1: Pre-startup configuration (Recommended)**

```bash
# Create certificate directory
mkdir -p ./ssl

# Place your certificate files (must use specific names)
cp your-cert.crt ./ssl/N.E.K.O.crt
cp your-cert.key ./ssl/N.E.K.O.key
```

**Method 2: Post-startup replacement**

```bash
# 1. Stop container
docker-compose down

# 2. Replace certificate files
cp your-cert.crt ./ssl/N.E.K.O.crt
cp your-cert.key ./ssl/N.E.K.O.key

# 3. Restart
docker-compose up -d
```

##### Certificate Requirements
- ✅ Must be **PEM format**
- ✅ Certificate and private key must match
- ✅ Private key must not be password-protected
- ✅ Certificate must be within validity period
- ❌ Encrypted private keys not supported

##### Certificate Validation
The container automatically validates SSL certificates on startup:
- ✅ **Validation passed**: HTTPS starts normally
- ❌ **Validation failed**: Container fails to start, check logs
- ⚠️ **Skip validation**: Set `DISABLE_SSL=1` to temporarily disable SSL

##### View Certificate Info
```bash
docker exec neko openssl x509 -in /root/ssl/N.E.K.O.crt -noout -text
```
</details>

#### ⚙️ Environment Variables

<details>
<summary>Click to view environment variable configuration</summary>

> **Note**: Some environment variables may not take effect in source code; prefer configuring via Web UI.

```yaml
environment:
  # API Keys
  - NEKO_CORE_API_KEY=${NEKO_CORE_API_KEY}
  - NEKO_ASSIST_API_KEY_QWEN=${NEKO_ASSIST_API_KEY_QWEN}
  - NEKO_ASSIST_API_KEY_OPENAI=${NEKO_ASSIST_API_KEY_OPENAI}
  - NEKO_ASSIST_API_KEY_GLM=${NEKO_ASSIST_API_KEY_GLM}
  - NEKO_ASSIST_API_KEY_STEP=${NEKO_ASSIST_API_KEY_STEP}
  - NEKO_ASSIST_API_KEY_SILICON=${NEKO_ASSIST_API_KEY_SILICON}
  - NEKO_MCP_TOKEN=${NEKO_MCP_TOKEN}

  # API Providers
  - NEKO_CORE_API=${NEKO_CORE_API:-qwen}
  - NEKO_ASSIST_API=${NEKO_ASSIST_API:-qwen}

  # Models
  - NEKO_SUMMARY_MODEL=${NEKO_SUMMARY_MODEL:-qwen-plus}
  - NEKO_CORRECTION_MODEL=${NEKO_CORRECTION_MODEL:-qwen-max}
  - NEKO_EMOTION_MODEL=${NEKO_EMOTION_MODEL:-qwen-turbo}
  - NEKO_VISION_MODEL=${NEKO_VISION_MODEL:-qwen3-vl-plus-2025-09-23}

  # SSL
  - SSL_DOMAIN=${SSL_DOMAIN:-project-neko.online}
  - SSL_DAYS=${SSL_DAYS:-365000}
  - DISABLE_SSL=${DISABLE_SSL:-0}
  - AUTO_REGENERATE_CERT=${AUTO_REGENERATE_CERT:-1}
  - NGINX_AUTO_RELOAD=${NGINX_AUTO_RELOAD:-1}
```

**Quick setup:**

```bash
cat > .env << EOF
NEKO_CORE_API_KEY=your_core_api_key_here
NEKO_ASSIST_API_KEY_QWEN=your_qwen_api_key
NEKO_MCP_TOKEN=your_mcp_token
SSL_DOMAIN=your-domain.com
EOF

docker-compose --env-file .env up -d
```
</details>

#### 🔧 Troubleshooting

<details>
<summary>Click to view common solutions</summary>

##### 1. Port Conflict
```bash
ss -tulpn | grep ':4891[12]'
# Solution: modify port mapping in docker-compose.yml
```

##### 2. Permission Issues
```bash
mkdir -p N.E.K.O logs ssl
chmod 755 N.E.K.O logs ssl
```

##### 3. Container Fails to Start
```bash
docker-compose logs --tail=100
docker logs neko --tail=100
```

##### 4. SSL Certificate Error
```bash
rm -f ssl/N.E.K.O.crt ssl/N.E.K.O.key
docker-compose up -d
```

##### 5. Network Issues
```bash
curl -v http://localhost:48911/health
curl -v -k https://localhost:48912/health
```

##### 6. Container Inaccessible
```bash
docker ps | grep neko
docker logs neko
docker exec -it neko bash
```

##### 7. Disk Space
```bash
docker system prune -f
docker-compose down && docker volume prune -f
```

##### 8. Image Pull Failure
```bash
# Try alternative image source in docker-compose.yml:
# image: ghcr.io/project-n-e-k-o/n.e.k.o:latest
```

</details>

#### 📊 System Monitoring

<details>
<summary>Click to view monitoring commands</summary>

##### Health Check
```bash
curl http://localhost:48911/health
curl -k https://localhost:48912/health
```

##### Resource Monitoring
```bash
docker stats neko
docker top neko
docker inspect neko
```

##### Log Management
```bash
docker-compose logs -f
docker-compose logs --tail=100
docker-compose logs | grep -i error
```

##### Data Backup
```bash
tar -czf neko-backup-$(date +%Y%m%d).tar.gz \
  N.E.K.O/ \
  ssl/ \
  docker-compose.yml
```

##### Version Upgrade
```bash
docker-compose pull
docker-compose up -d
```

</details>

#### 🌐 Access URLs
After container startup:
- **HTTP**: `http://your-server-ip:48911`
- **HTTPS**: `https://your-server-ip:48912`

#### ⏱️ Quick Reference

| Action | Command |
|--------|---------|
| Start | `docker-compose up -d` |
| Stop | `docker-compose down` |
| Logs | `docker-compose logs -f` |
| Restart | `docker-compose restart` |
| Update | `docker-compose pull && docker-compose up -d` |
| Shell | `docker exec -it neko bash` |
| Status | `docker-compose ps` |

---

</details>

### Source Code Development

<details>
<summary>Click to expand developer startup guide</summary>

> Full developer documentation at [project-neko.online](https://project-neko.online)

**Requirements**: Python 3.11 (other versions not supported), [uv](https://docs.astral.sh/uv/) package manager, Node.js (>=20.19)

```bash
# 1. Clone the project
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O

# 2. Install Python dependencies
uv sync

# 3. Build frontend projects (requires Node.js >= 20.19; needed on first run or after frontend changes)
#    Or use the convenience script: build_frontend.bat (Windows) / ./build_frontend.sh (Linux/macOS)
cd frontend/react-neko-chat && npm install && npm run build && cd ../..
cd frontend/plugin-manager && npm install && npm run build && cd ../..

# 4. Start services (main_server and memory_server required at minimum)
uv run python memory_server.py
uv run python main_server.py
# Optional: start Agent service
uv run python agent_server.py

# 5. Visit http://localhost:48911 to configure API Key and start using
```

Developers are encouraged to join QQ group 1022939659.

</details>

## Advanced Usage
<details>
<summary>Click to expand advanced usage</summary>

#### Configuring API Key

Configure third-party AI services for additional features:

- **Core API** (real-time voice conversation): Must support Realtime API. Recommended: *Gemini*, *OpenAI*, *StepFun* or *Alibaba Cloud*.
- **Assist API** (memory/emotion/vision): Supports standard ChatCompletion interface. 14+ providers available.

Visit `http://localhost:48911/api_key` to configure directly through the Web interface.

> Obtaining *Alibaba Cloud API*: Register an account on Alibaba Cloud's Bailian platform [official website](https://bailian.console.aliyun.com/). New users can receive substantial free credits after real-name verification. After registration, visit the [console](https://bailian.console.aliyun.com/api-key?tab=model#/api-key) to get your API Key.

#### Modifying Character Persona

- Access `http://localhost:48911/chara_manager` to enter the character editing page. The default companion preset name is `XiaoTian`; it's recommended to directly modify the name and add or change basic persona items one by one.

- Advanced persona settings include **Live2D/VRM/MMD model settings** and **voice settings**. To change the **Avatar model**, first copy the model directory to the `static` folder. From advanced settings, enter the model management interface to switch models and adjust position/size by dragging and scrolling. To change the **character voice**, prepare a continuous, clean voice recording of about 5 seconds. Enter the voice clone page through advanced settings and upload the recording.

- **Character card export** is supported — export as "definition only" or "full character card" format for sharing and backup.

- Advanced persona also has a `system_prompt` option for complete system instruction customization, but modification is not recommended.

#### Modifying API Provider

- Visit `http://localhost:48911/api_key` to switch core API and assist API service providers.

#### Memory Review

- Visit `http://localhost:48911/memory_browser` to browse and proofread recent memories and summaries, which can alleviate issues like model repetition and cognitive errors.

</details>

## Project Details
<details>
<summary>Click to expand project architecture and roadmap</summary>

**Project Architecture**

```
N.E.K.O/
├── 📁 .agent/                   # 🤖 AI coding assistant rules & skills (Google Antigravity convention)
├── 📁 brain/                    # 🧠 Agent modules
│   ├── computer_use.py          # Computer control
│   ├── browser_use_adapter.py   # Browser automation
│   ├── openclaw_adapter.py      # OpenClaw cloud connection
│   ├── openfang_adapter.py      # OpenFang headless execution backend
│   ├── task_executor.py         # Task execution engine
│   └── 📁 cua/                  # Computer Use Agent subsystem
├── 📁 config/                   # ⚙️ Configuration management
│   ├── api_providers.json       # API provider configuration
│   ├── prompts_chara.py         # Character prompts
│   └── prompts_sys.py           # System prompts
├── 📁 main_logic/               # 🔧 Core modules
│   ├── core.py                  # Core dialogue module
│   ├── cross_server.py          # Cross-server communication
│   ├── omni_realtime_client.py  # Realtime API client
│   ├── omni_offline_client.py   # Text API client (ChatCompletion)
│   └── tts_client.py            # 🔊 TTS engine adapter
├── 📁 main_routers/             # 🌐 API routers (14 routes)
├── 📁 memory/                   # 🧠 Three-tier memory system
│   ├── facts/                   # Fact memory
│   ├── reflection/              # Reflection memory
│   └── persona/                 # Persona memory
├── 📁 frontend/                 # 🖥️ Modern frontend projects
│   ├── react-neko-chat/         # React chat window component
│   └── plugin-manager/          # Vue plugin manager dashboard
├── 📁 plugin/                   # 🔌 Plugin system
│   ├── sdk/                     # Plugin SDK
│   └── server/                  # Plugin server
├── 📁 static/                   # 🌐 Frontend static resources (incl. build artifacts)
├── 📁 templates/                # 📄 Frontend HTML templates (14 pages)
├── 📁 utils/                    # 🛠️ Utility modules
├── main_server.py               # 🌐 Main server
├── agent_server.py              # 🤖 AI agent server
└── memory_server.py             # 🧠 Memory server
```

> **AI-Assisted Development**: The `.agent/` directory follows the Google Antigravity open convention and contains the project's development rules and skill sets. Only Antigravity auto-reads it; all other AI tools (including Claude Code) need to import manually. See the [adaptation guide](https://project-neko.online/contributing/ai-assisted-dev).

**Data Flow**

![Framework](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/framework.drawio.svg)

> Full developer documentation at [project-neko.online](https://project-neko.online)

### Roadmap

v0.7: ✅ Agent-related features. **Completed.**

v0.8: Memory-related features, OpenClaw-like functionality. Expected: March 2026.

v0.9: Multi-system support (Linux, mobile). N.E.K.O. Network launch. Expected: April 2026.

v1.0: Focus on in-house large models and agent systems. Expected: June 2026.

</details>

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=wehos/N.E.K.O.&type=Date)](https://www.star-history.com/#wehos/N.E.K.O.&Date)
