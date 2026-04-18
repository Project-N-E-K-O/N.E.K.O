# Use NEKO to Connect to QwenPaw

## QwenPaw Installation Guide

### Step 1: Install

You do not need to configure Python manually. One command installs `uv`, creates a virtual environment, and installs QwenPaw with its dependencies. Note: this may not work in some network environments or under enterprise permission restrictions.

macOS / Linux:

```bash
curl -fsSL https://qwenpaw.agentscope.io/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://qwenpaw.agentscope.io/install.ps1 | iex
```

### Step 2: Initialize

After installation finishes, open a new terminal and run:

```bash
qwenpaw init --defaults
```

During initialization, QwenPaw shows a security warning explaining that the assistant runs in your local environment and that multiple users of the same instance would share access to files, commands, and secrets. Read it and type `yes` to continue.

![QwenPaw initialization security prompt](assets/openclaw_guide/image1.png)

### Step 3: Start

```bash
qwenpaw app
```

If startup succeeds, the last line in the terminal will usually be:

```text
INFO:     Uvicorn running on http://127.0.0.1:8088 (Press CTRL+C to quit)
```

After the service starts, visit `http://127.0.0.1:8088` to open the QwenPaw console.

### Step 4: Replace Persona Files (Optional)

After initialization, QwenPaw creates its configuration directory automatically:

- Windows default: `C:\Users\YourUsername\.qwenpaw`
- macOS default: `~/.qwenpaw`

Because `.qwenpaw` is hidden:

- On Windows, enable hidden items in File Explorer
- On macOS, press `Command + Shift + .` in Finder

If you want QwenPaw to behave like a clean backend executor for N.E.K.O, download this package:

- [Replacement Files.zip](assets/openclaw_guide/替换文件.zip)

Copy `SOUL.md`, `AGENTS.md`, and `PROFILE.md` from the archive into `.qwenpaw/workspaces/default`, overwrite the existing files, and delete `BOOTSTRAP.md` from that directory.

Then stop QwenPaw with `CTRL+C` and restart it:

```bash
qwenpaw app
```

## Basic Setup: Model Configuration

Open the QwenPaw console, go to the Model page, and choose the provider you want to use. `DashScope` is a common beginner choice, but you can select another provider if your API key is for a different service.

Open the provider settings, enter your API key, and save.

![QwenPaw model configuration page](assets/openclaw_guide/image2.png)

After saving, return to the chat page and select the configured model.

## Enable OpenClaw in N.E.K.O

N.E.K.O still uses the internal name `openclaw`, so the `OpenClaw` toggle in the UI actually means QwenPaw.

Follow this order:

1. Open the Agent panel in N.E.K.O
2. Turn on the main `Agent` switch
3. Make sure `openclawUrl` points to `http://127.0.0.1:8088`
4. Turn on the `OpenClaw` sub-switch
5. Wait for the availability check to pass

N.E.K.O will try the QwenPaw compatible endpoint first and automatically fall back to the main `process` endpoint when needed. No custom channel setup is required for the main integration path.
