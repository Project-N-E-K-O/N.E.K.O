
# Connect N.E.K.O. to QwenPaw

N.E.K.O. keeps the compatibility name **OpenClaw** for its QwenPaw integration. In this guide, the OpenClaw switch in N.E.K.O. connects to a separately running QwenPaw service.

## 1. Verify the source and install

Use the current instructions from the [official QwenPaw repository](https://github.com/agentscope-ai/QwenPaw). The commands below download and execute a remote installation script. Review the script first if your security policy requires it; restricted networks or managed devices may block the installer.

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

The installer prepares `uv`, an isolated environment, QwenPaw, and its dependencies. Open a new terminal after installation.

## 2. Initialize

```bash
qwenpaw init --defaults
```

Read the security warning shown by QwenPaw before accepting it. One local instance can access the files, commands, and credentials available to its account; do not share it between untrusted users.

![QwenPaw initialization security notice](assets/openclaw_guide/image1.png)

## 3. Start and verify

```bash
qwenpaw app
```

The default console is `http://127.0.0.1:8088/`. Keep the terminal running and open that address in a browser. If it does not load, resolve the QwenPaw startup error before enabling N.E.K.O.

Do not expose the service outside localhost unless you understand and configure its authentication and network boundary.

## 4. Configure a model in QwenPaw

In the QwenPaw console, open the model page, choose a provider, enter the required credential, and save. Then return to chat and select the configured model. Available providers and model names belong to the installed QwenPaw version, so use its current UI rather than a copied list.

![QwenPaw model configuration](assets/openclaw_guide/image2.png)

## 5. Optional: executor persona

The bundled [replacement archive](assets/openclaw_guide/qwenpaw-executor-profile.zip) contains `SOUL.md`, `AGENTS.md`, and `PROFILE.md` for an executor-oriented profile. This step is optional and changes QwenPaw behavior.

Before replacing anything:

1. stop QwenPaw and back up `.qwenpaw/workspaces/default`;
2. inspect the archive and compare it with your current workspace;
3. copy only the files you intend to replace.

The default configuration directory is usually `%USERPROFILE%\.qwenpaw` on Windows or `~/.qwenpaw` on macOS/Linux. Removing `BOOTSTRAP.md` is only part of this optional executor-profile setup; it is not required for N.E.K.O. connectivity. Restart `qwenpaw app` after changes.

## 6. Enable it in N.E.K.O.

1. Start QwenPaw and keep it running.
2. Open N.E.K.O.'s paw/Agent panel.
3. Enable the Agent master switch.
4. Enable the **OpenClaw** child switch.
5. Wait for the availability check.

N.E.K.O. defaults to `http://127.0.0.1:8088`. If QwenPaw uses another address, update `openclawUrl` in N.E.K.O.'s core configuration before retrying.

The current adapter recognizes QwenPaw's v2 console API and legacy agent-compatible APIs. Availability checks probe `/api/version` and `/api/agent/health` as appropriate; requests then use the matching console or agent endpoint. You do not need to create a separate channel file for the default console setup.
