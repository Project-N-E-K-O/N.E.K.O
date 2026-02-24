# Deployment Overview

N.E.K.O. can be deployed in several ways depending on your environment and needs.

| Method | Best for | Platform |
|--------|----------|----------|
| [Docker](/deployment/docker) | Production, servers, headless | Linux, macOS |
| [Manual setup](/deployment/manual) | Development, customization | All platforms |
| [Windows executable](/deployment/windows-exe) | End users | Windows |

## Minimum requirements

- **CPU**: 2+ cores
- **RAM**: 4 GB minimum, 8 GB recommended
- **Python**: 3.11 (for manual setup)
- **Network**: Internet access for API calls (unless using local LLM)

## Ports used

| Port | Service | Required |
|------|---------|----------|
| 48911 | Main server (Web UI) | Yes |
| 48912 | Memory server | Yes |
| 48913 | Monitor server | Optional |
| 48915 | Agent server | Optional |
| 48916 | Plugin server | Optional |
