# Configuration Overview

N.E.K.O. has three separate configuration surfaces. They do not share one universal precedence chain.

| Surface | Recommended editor | Runtime source |
| --- | --- | --- |
| User settings, characters, API keys | Web UI | JSON under the selected N.E.K.O. data root |
| Provider definitions and model-role defaults | Maintainer-edited repository data | `config/api_providers.json` plus code fallbacks |
| Ports and selected runtime switches | Environment or desktop port settings | `config/network.py`, `config/memory_settings.py`, and `port_config.json` |

For a normal installation, start N.E.K.O. and use the Web UI. Do not edit bundled files merely to change a personal API key.

## References

- [Config files](./config-files): writable files and storage roots
- [API providers](./api-providers): provider-schema contract
- [Model configuration](./model-config): role-based model resolution
- [Environment variables](./environment-vars): variables actually read by current code
- [Config priority](./config-priority): precedence by configuration surface

::: warning Secrets
Keep API keys out of Git, screenshots, issue reports, and logs. The Web UI writes them to local user configuration.
:::
