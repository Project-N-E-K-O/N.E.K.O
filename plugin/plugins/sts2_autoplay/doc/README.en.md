# Quick Start

`sts2_autoplay` connects the local *Slay the Spire 2* state exposed by `STS2 AI Agent` into N.E.K.O, so you can inspect the current run, review the next suggested move, let it handle short stretches of play, and adjust the strategy in natural language while the run is in progress. In the current version, the interaction surface has been narrowed down to a few core abilities: check status, inspect the current run, control autoplay, toggle companion mode, adjust strategy from one sentence, preview the next move, and take the suggested step.

## Tutorial

### Get the Mod

Using Git:
```text
https://github.com/CharTyr/STS2-Agent/releases
```

### Install the Game Mod

In Steam, right-click *Slay the Spire 2*, then choose Manage -> Browse local files.

The default Steam game directory is usually similar to:

```text
...\Steam\steamapps\common\Slay the Spire 2
```

Copy the `STS2 AI Agent` mod into the `mods/` directory under the game folder.

If there is no `mods` folder under the *Slay the Spire 2* directory, create it yourself.

```text
Using mods may cause save loss. Please back up your saves, or use the console to compensate yourself (press the "~" key in the main menu, enter "unlock all", and all characters and difficulties will be unlocked).
```

After installation, the directory should look like:

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### Set the Mod Port

The mod listens on port `8080` by default. This is a commonly used port and may be occupied by other software.

You can open Environment Variables and create a new system/global variable named `STS2_API_PORT`. Choose a less common port such as `18080` or `38080`.

Example: variable name `STS2_API_PORT`, variable value `18080`.

Tip: you can open Environment Variables quickly this way:

- Press Win + R to open the Run dialog.

- Enter the following command and press Enter:

```text
rundll32 sysdm.cpl,EditEnvironmentVariables
```

### Launch the Game and Confirm the Interface

Start the game normally first so the Mod loads with the game.

The first time you switch to mod mode, the game may crash once. This is normal; just start the game again.

After the mod is loaded, in N.E.K.O enable Cat Paw, enable the plugin, enter the plugin panel, and manually start the Slay the Spire plugin.

If you happen to be operating inside *Slay the Spire 2* at the same time that you enable or initialize the plugin, the first reply may be one beat slower than usual. This is normal. After the current board state finishes syncing, later responses should return to normal.

### Available Commands

【Play a card】【Auto-play for me】【Clear a floor】【How was that play】【Stop】
【Play one card】【Play a specific card】【Recommend one card】... and similar phrases.

## Contact

If you have any problems, please send the game runtime logs and the N.E.K.O runtime logs by email to zhaijiunknown@outlook.com.

Game runtime logs:
```text
%AppData%\SlayTheSpire2\logs
```

N.E.K.O runtime logs:
```text
Your user folder\AppData\Local\N.E.K.O\logs
```

## Feature Overview

- Connects to the local `STS2 AI Agent` HTTP service and reads the current run state.
- Supports a one-shot run inspection flow that refreshes once and returns the snapshot, situation summary, and neko sync packet together.
- Supports background autoplay control: start, pause, resume, stop, and taking the currently suggested next step.
- Supports companion mode, which can watch the run with you and push observations, commentary, and reminders without hard-interrupting the main flow.
- Supports natural-language strategy adjustment: one user sentence can be turned into event-level or enemy-level preference overrides for the current scene.
- Supports checking the next suggested move before deciding whether to actually execute it.
- Supports safety protections such as low-HP pause, dangerous-attack slowdown, speed restoration after danger passes, and auto-resume when conditions are safe again.
- Supports passive frontend pushes for state sync, observations, companion hints, and control feedback.

## Plugin Configuration

Config file: `plugin.toml`

### Basic Configuration

| Config Item | Default | Description |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Address of the local STS2 Agent. |
| `connect_timeout_seconds` | `5` | Connection timeout in seconds. |
| `request_timeout_seconds` | `15` | Request timeout in seconds. |
| `poll_interval_idle_seconds` | `3` | Polling interval while idle. |
| `poll_interval_active_seconds` | `1` | Polling interval while autoplay is running. |
| `action_interval_seconds` | `1.5` | Extra delay between actions. |
| `post_action_delay_seconds` | `0.5` | Delay after an action to wait for the board to stabilize. |
| `autoplay_on_start` | `false` | Whether to automatically start autoplay after the plugin starts. |
| `character_strategy` | `defect` | Default strategy name. The runtime will map the current run into the matching strategy context. |
| `max_consecutive_errors` | `3` | Maximum consecutive error count before the connection is considered unhealthy. |

### Frontend Pushes and Companion Observation

| Config Item | Default | Description |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Whether autoplay action/error updates may be pushed to the frontend. |
| `llm_frontend_output_probability` | `1.0` | Push probability for ordinary action messages. Errors and critical control feedback may still be forced through. |
| `autoplay_push_probability` | `0.5` | Probability for ordinary run-sync pushes when companion mode is not active. |
| `companion_push_probability` | `0.7` | Probability for ordinary run-sync pushes while companion mode is active. |
| `neko_reporting_enabled` | `true` | Whether companion observation behavior is enabled. |
| `neko_report_interval_steps` | `1` | How often observation content is refreshed, measured in autoplay steps. |
| `neko_report_hud_enabled` | `true` | Whether prepared observation content is actually pushed through the frontend HUD / message channel. |
| `neko_commentary_enabled` | `true` | Whether companion commentary and reminders may be generated. |
| `neko_commentary_probability` | `0.65` | Trigger probability for ordinary low-priority commentary. |
| `neko_commentary_min_interval_seconds` | `4` | Minimum interval before repeating similar commentary, used to reduce spam. |
| `neko_critical_commentary_always` | `true` | Whether high-priority reminders should always be broadcast. |
| `neko_guidance_max_queue` | `50` | Internal queue limit for guidance / preference-related context. |

### Automatic Protection and Tempo Control

| Config Item | Default | Description |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | When HP ratio falls below this value, autoplay will prefer to pause. |
| `neko_auto_safe_hp_threshold` | `0.5` | When HP recovers to this value, the run may be considered safe again. |
| `neko_auto_dangerous_attack_threshold` | `20` | High-damage enemy intents at or above this threshold may trigger slowdown protection. |
| `neko_auto_resume_after_low_hp` | `true` | Whether autoplay may resume automatically after a low-HP pause once conditions are safe again. |
| `neko_desperate_enabled` | `true` | Whether the desperate low-HP survival posture is enabled. |
| `neko_desperate_hp_threshold` | `0.2` | HP ratio that triggers the desperate survival posture. |
| `neko_maximize_enabled` | `true` | Whether a more value-maximizing decision posture is enabled. |

## Recommended Phrasing for Regular Users

Regular users do not need to remember low-level parameters. Prefer routing the user's original wording into the currently retained high-level abilities and let the plugin decide whether the request is about inspecting the run, adjusting strategy, or taking the next suggested step.

Recommended interpretation:

| What the user means | Best matching ability |
| --- | --- |
| `what's going on right now` | `sts2_get_status` |
| `show me the current run` | `sts2_read_state` |
| `let her keep playing` / `pause autoplay for now` / `let her continue playing` / `stop letting her play` | autoplay control entries |
| `adjust the strategy from this note: prefer the lower-cost route in this event` | `sts2_apply_user_override` |
| `show me what she wants to do next` | `sts2_get_planned_operation` |
| `take the suggested step` | `sts2_execute_planned_operation` |
| `turn on companion mode` / `turn off companion mode` | companion mode control entries |

Recommended interaction order:
1. Inspect the current run first.
2. Then check what she wants to do next.
3. If you disagree or want a preference change, adjust the strategy in one sentence.
4. Finally decide whether to take the suggested step or let autoplay continue.

## Plugin Entries

These are the main public-facing abilities still exposed by the current plugin script. Their display names are phrased in more natural language now, but the underlying entry ids remain stable so host integrations do not need to change.

### `sts2_health_check`

Checks whether the local STS2 Agent service is reachable. Use it first during startup checks, integration testing, or error investigation.

### `sts2_get_status`

Shows the overall runtime state: whether the connection is healthy, which screen is active, whether autoplay is running, whether standby is enabled, and what the current mode/error context looks like.

### `sts2_read_state`

Refreshes the current run once and returns three layers together:
- the current snapshot
- the current situation summary
- the current neko sync packet

Use this when you want one complete readout before deciding the next move.

### `sts2_set_standby`

Toggles standby mode. In standby mode, the plugin stops executing actions but still keeps state organization and sync preparation available.

### `sts2_start_autoplay`

Lets her keep playing. This starts background autoplay and allows the current run to continue progressing on its own.

### `sts2_pause_autoplay`

Pauses autoplay for now. Useful when you want to take over manually or change strategy before the next action.

### `sts2_resume_autoplay`

Lets her continue playing from the paused state.

### `sts2_stop_autoplay`

Stops letting her play. This fully stops background autoplay and hands control back to you.

### `sts2_enable_companion_mode`

Turns on companion mode. With it enabled, the plugin becomes more proactive about organizing the run state, pushing observations, and offering commentary or reminders.

### `sts2_disable_companion_mode`

Turns off companion mode. This stops the companion commentary layer while keeping the basic state-reading and autoplay controls available.

### `sts2_apply_user_override`

Adjusts strategy from a single user note. It interprets the sentence in the current scene and turns it into the matching event-level or enemy-level override.

This entry also has an extra safety rule:
- if autoplay is currently running, it **pauses autoplay first**
- after the strategy update, it tells you to **resume autoplay manually if you want to continue**
- it will not continue the run on its own before you explicitly choose to resume

### `sts2_get_planned_operation`

Shows what she wants to do next. Use this when you want to preview the next move instead of executing it immediately.

### `sts2_execute_planned_operation`

Takes the suggested step directly.

## Typical Usage

### Check Connection

1. Launch *Slay the Spire 2*.
2. Confirm `http://127.0.0.1:8080/health` is accessible.
3. Call `sts2_health_check` in N.E.K.O.

### Manually Review and Execute the Next Step

First inspect the current suggestion:

```text
sts2_get_planned_operation
```

If the suggestion looks good, then execute it:

```text
sts2_execute_planned_operation
```

This matches the current public entry model better than calling a removed one-step action directly.

### Check the Current Run Before Acting

A common safe flow is:

1. Check the full current run state with:

```text
sts2_read_state
```

2. See what she wants to do next with:

```text
sts2_get_planned_operation
```

3. If needed, adjust the strategy in one sentence.

4. Finally decide whether to:
   - take the suggested step with `sts2_execute_planned_operation`, or
   - let autoplay continue with `sts2_start_autoplay`.

### Let the Catgirl Help Clear a Floor

The user can say:

```text
clear this floor for me
```

The host should call:

```text
sts2_start_autoplay
```

Once autoplay starts, use the pause / resume / stop entries to control it. Observation pushes are only progress feedback and should not be treated as task completion signals by themselves.

### Adjust Strategy Mid-run

If the user wants to change the direction while the run is in progress, they can say something like:

```text
defend first, don't take too much damage
```

The host should call:

```text
sts2_apply_user_override
```

Recommended parameters:

```json
{
  "instruction": "defend first, don't take too much damage",
  "source": "user"
}
```

If autoplay is currently running, this entry will pause autoplay first, apply the strategy update, and then ask the user to resume autoplay manually if they want to continue.

## Frontend Push Events

The plugin sends several categories of passive information through the host message channel, mainly grouped into three buckets:

1. **State and run sync**
   - current run summary
   - current suggestion summary
   - current companion-mode sync payloads

2. **Autoplay control feedback**
   - autoplay started
   - paused / resumed / stopped
   - strategy-updated messages that require manual resume

3. **Companion and protection notices**
   - companion commentary
   - risk reminders
   - low-HP pause
   - dangerous-attack slowdown
   - speed restoration or autoplay recovery after danger passes

These pushes all use passive delivery semantics by default and should not hard-interrupt the main conversation. Their frequency is further affected by:
- `autoplay_push_probability`
- `companion_push_probability`
- `neko_commentary_probability`
- `neko_report_hud_enabled`
and related settings.

## Common Troubleshooting

### Connection failure when calling plugin entries

First check:

- Whether the game has already been launched.
- Whether the `STS2 AI Agent` Mod has been correctly placed into the game's `mods/` directory.
- Whether `http://127.0.0.1:8080/health` is accessible.
- Whether `base_url` in `plugin.toml` is correct.

### `http://127.0.0.1:8080/health` cannot be opened

Check in this order:

1. Whether the game has really been launched.
2. Whether `STS2AIAgent.dll`, `STS2AIAgent.pck`, and `mod_id.json` have all been copied into the game's `mods/` directory.
3. Whether the filenames were renamed by the system, duplicated, or placed in the wrong directory.
4. Whether you are operating in the Steam game directory rather than the upstream repository directory.
5. Whether a firewall or security software is blocking the local port.

### Auto-play runs, but the frontend receives no messages

Check:

- Whether `llm_frontend_output_enabled` is `true`.
- Whether `llm_frontend_output_probability` is set too low.
- Whether `neko_reporting_enabled` is `true`.
- During integration testing, you can first set `llm_frontend_output_probability` to `1`.
- Whether the host frontend is actually receiving plugin push messages.

### Catgirl mid-run strategy changes have no obvious effect

Check:

- Whether the plugin is currently in standby.
- Whether `sts2_apply_user_override` returned `ok`.
- Whether the instruction is specific enough, such as "prioritize defense", "hit the lowest-HP enemy first", or "save the potion".
- Whether the current legal actions can actually satisfy the requested adjustment.

Remember that `sts2_apply_user_override` updates strategy preferences. It does not instantly force a specific card to be played on the current frame.

### Auto-play keeps running in a direction you no longer want

Use `sts2_pause_autoplay` first, then call `sts2_apply_user_override` to adjust the strategy, and finally decide whether to resume with `sts2_resume_autoplay`.

### Stuck in events, popups, or transitional states

The current version already handles events, popups, and transitional states. Priority actions include:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

If it is still stuck, first use `sts2_read_state` to inspect the current `screen` and `available_actions`.

### Auto-play suddenly pauses or slows down

This may have triggered safety protections:

- It pauses when HP ratio falls below `neko_auto_low_hp_threshold`.
- It slows down during Boss fights or dangerous attacks.
- If `neko_auto_resume_after_low_hp` is `true`, it may auto-resume after HP recovers to `neko_auto_safe_hp_threshold`.

You can call `sts2_get_status` to inspect the state, or call `sts2_resume_autoplay` / `sts2_stop_autoplay` to handle it.
