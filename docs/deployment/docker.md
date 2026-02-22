# Docker Deployment

## Quick start

```bash
# Clone the repository
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O/docker

# Configure environment
cp env.template .env
# Edit .env with your API keys

# Start
docker-compose up -d
```

Access the Web UI at `http://localhost:48911`.

## docker-compose.yml

```yaml
version: '3.8'

services:
  neko-main:
    image: ghcr.io/project-n-e-k-o/n.e.k.o:latest
    container_name: neko
    restart: unless-stopped
    ports:
      - "48911:80"
    environment:
      - NEKO_CORE_API_KEY=${NEKO_CORE_API_KEY}
      - NEKO_CORE_API=${NEKO_CORE_API:-qwen}
      - NEKO_ASSIST_API=${NEKO_ASSIST_API:-qwen}
      - NEKO_ASSIST_API_KEY_QWEN=${NEKO_ASSIST_API_KEY_QWEN:-}
      - NEKO_ASSIST_API_KEY_OPENAI=${NEKO_ASSIST_API_KEY_OPENAI:-}
      - NEKO_ASSIST_API_KEY_GLM=${NEKO_ASSIST_API_KEY_GLM:-}
      - NEKO_ASSIST_API_KEY_STEP=${NEKO_ASSIST_API_KEY_STEP:-}
      - NEKO_ASSIST_API_KEY_SILICON=${NEKO_ASSIST_API_KEY_SILICON:-}
      - NEKO_MCP_TOKEN=${NEKO_MCP_TOKEN:-}
    volumes:
      - ./N.E.K.O:/root/Documents/N.E.K.O
      - ./logs:/app/logs
    networks:
      - neko-network

networks:
  neko-network:
    driver: bridge
```

## Environment variables

Create a `.env` file from the template:

```bash
# Required
NEKO_CORE_API_KEY=sk-your-key-here
NEKO_CORE_API=qwen

# Optional
NEKO_ASSIST_API=qwen
NEKO_ASSIST_API_KEY_QWEN=sk-your-assist-key
```

See [Environment Variables](/config/environment-vars) for the full reference.

## Nginx proxy

The Docker container includes Nginx as a reverse proxy:

- Proxies to the main server on the internal port
- WebSocket support for real-time chat
- Static file caching (30-day expiry)
- Health check at `/health`

## Data persistence

| Mount | Container path | Purpose |
|-------|----------------|---------|
| `./N.E.K.O` | `/root/Documents/N.E.K.O` | Config, characters, memories |
| `./logs` | `/app/logs` | Application logs |

## Provider quick start

**Qwen (recommended):**
```bash
NEKO_CORE_API_KEY=sk-xxxxx
NEKO_CORE_API=qwen
```

**Free (no key needed):**
```bash
NEKO_CORE_API_KEY=free-access
NEKO_CORE_API=free
```

**OpenAI:**
```bash
NEKO_CORE_API_KEY=sk-xxxxx
NEKO_CORE_API=openai
```

## Troubleshooting

```bash
# View logs
docker logs neko

# Enter container
docker exec -it neko bash

# Check config
docker exec neko cat /root/Documents/N.E.K.O/core_config.json

# Check environment
docker exec neko env | grep NEKO_
```
