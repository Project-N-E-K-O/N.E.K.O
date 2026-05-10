# Quick Start

`sts2_autoplay` is used to connect the local *Slay the Spire 2* state exposed by `STS2 AI Agent` into N.E.K.O. The plugin can read the current board state, execute legal actions, auto-play according to strategy, let the catgirl choose a single card, push observation information to the frontend, and allow the catgirl to send soft guidance in a background task to influence the next decision round.

## Tutorial

### Get the Mod

Using Git:
```text
https://gitclone.com/github.com/CharTyr/STS2-Agent.git
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

### Launch the Game and Confirm the Interface

Start the game normally first so the Mod loads with the game.

The first time you switch to mod mode, the game may crash once. This is normal; just start the game again.

After the mod is loaded, in N.E.K.O enable Cat Paw, enable the plugin, enter the plugin panel, and manually start the Slay the Spire plugin.

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

- Connects to the local `STS2 AI Agent` HTTP service and reads game state.
- Supports manual single-step execution, background semi-auto play, pause, resume, and stop.
- Supports three decision modes: `full-program`, `half-program`, and `full-model`.
- Supports loading strategy documents by character; strategy files are located in `strategies/`.
- Supports catgirl single-card choice: selects only one card from the currently playable `play_card` actions, pushes the reason first, then executes it.
- Supports catgirl soft guidance: the user or the catgirl can send natural-language guidance, which will be referenced by the next LLM decision round.
- Supports background observation reports: pushes current floor, combat, hand, enemy intents, LLM reasoning, and more to the frontend.
- Supports safety protections: pause on low HP, slow down on Boss/dangerous attacks, auto-resume after HP recovery, desperate low-HP survival strategy, value maximization, and synergy scoring.

## Plugin Configuration

Config file: `plugin.toml`

### Basic Configuration

| Config Item | Default | Description |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Address of the local Spire Agent. |
| `connect_timeout_seconds` | `5` | Connection timeout in seconds. |
| `request_timeout_seconds` | `15` | Request timeout in seconds. |
| `poll_interval_idle_seconds` | `3` | Polling interval while idle. |
| `poll_interval_active_seconds` | `1` | Polling interval while auto-play is running. |
| `action_interval_seconds` | `1.5` | Extra delay between actions. |
| `post_action_delay_seconds` | `0.5` | Delay after an action to wait for the board to stabilize. |
| `autoplay_on_start` | `false` | Whether to automatically start playing after the plugin starts. |
| `semi_auto_autoplay` | `true` | Whether to create a semi-auto task context when starting auto-play. |
| `mode` | `half-program` | Current auto-play mode. |
| `character_strategy` | `defect` | Character strategy name, corresponding to `strategies/<name>.md`. |
| `max_consecutive_errors` | `3` | Maximum number of consecutive errors before being considered disconnected. |
| `push_notifications` | `true` | Legacy retained field. |
| `event_stream_enabled` | `false` | Reserved field, not actually used yet. |

### Decision Modes

`mode` supports the following values, as well as their Chinese aliases:

| Mode | Chinese Alias | Description |
| --- | --- | --- |
| `full-program` | `全程序` | Pure programmatic heuristics; no model calls. |
| `half-program` | `半程序` | Run program pre-checks first, then make one model decision, with legality validation/fallback. |
| `full-model` | `全模型` | Two model calls: reasoning first, then final action; program checks in between, and final legality validation at the end. |

### Character Strategies

`character_strategy` looks up a strategy document at `strategies/<name>.md`. The currently built-in strategies are:

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

You can add new Markdown files under `strategies/` to extend strategies. For example:

```text
strategies/my_strategy.md
```

Then set the config or entry parameter to:

```text
my_strategy
```

### Frontend Pushes and Catgirl Observation

| Config Item | Default | Description |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Whether to actively push auto-play actions/errors to the frontend. |
| `llm_frontend_output_probability` | `0.15` | Push probability for ordinary actions; clamped into the range `0.0 ~ 1.0`. Errors are always pushed. |
| `neko_reporting_enabled` | `true` | Whether to push catgirl observation reports. |
| `neko_report_interval_steps` | `1` | Push one observation report every how many auto-play steps; at least `1`. |
| `neko_commentary_enabled` | `true` | Whether to generate live catgirl commentary inside observation reports. If disabled, structured reports are still pushed, but `live_commentary.text` stays empty. |
| `neko_commentary_probability` | `0.65` | Trigger probability for ordinary low-priority commentary; clamped into `0.0 ~ 1.0`. High-priority scenes such as low HP, lethal setups, and heavy incoming attacks can bypass this probability. |
| `neko_commentary_min_interval_seconds` | `4` | Minimum interval in seconds before repeating commentary for the same low-priority scene, used to reduce spam and repeated voice lines. |
| `neko_critical_commentary_always` | `true` | Whether `critical` / `high` urgency commentary should always be broadcast, such as low HP, lethal situations, or heavy enemy attacks. |
| `neko_guidance_max_queue` | `50` | Maximum queue length for catgirl soft guidance. |

Catgirl observation reports carry simplified metadata such as `report`, `neko_context`, `live_commentary`, and `task`, so the frontend or dialogue logic can tell that this is a process observation rather than a task completion notification. To save user tokens, pushed content only keeps the current action, HP, hand, enemies, tactical summary, consumed guidance, and task summary.

`live_commentary` provides short voice-line fields for frontend/TTS: `text`, `scene`, `mood`, `urgency`, `priority`, `tts`, `interrupt`, `tone`, and `character_strategy`. Commentary is randomly selected from a scene-based template pool to reduce repetition; it also adjusts by character strategy, for example `defect` is more rational while `ironclad` is steadier. Current coverage includes near-death, low HP, lethal setups, enemy incoming attacks, defense, normal combat, rewards, shops, rest sites, events, maps, as well as event-level commentary such as combat end, key relic acquisition, and route selection completion.

### Safety Protections and Autonomous Actions

| Config Item | Default | Description |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | When current HP ratio falls below this value, background auto-play will pause autonomously. |
| `neko_auto_safe_hp_threshold` | `0.5` | Auto-play may resume after HP recovers to this ratio. |
| `neko_auto_dangerous_attack_threshold` | `20` | Automatically slow down when incoming enemy attack reaches this value and would break defense. |
| `neko_auto_resume_after_low_hp` | `true` | Whether to allow auto-resume after HP recovery following a low-HP pause. |
| `neko_desperate_enabled` | `true` | Whether to enable the desperate low-HP survival strategy. |
| `neko_desperate_hp_threshold` | `0.2` | HP ratio that triggers the desperate survival strategy. |
| `neko_maximize_enabled` | `true` | Whether to enable value-maximizing card selection. |
| `neko_synergy_enabled` | `true` | Whether to enable synergy/combo scoring. |

Current autonomous actions include:

- `pause`: Pauses on low HP, waiting for user or catgirl instructions.
- `slow_down`: Temporarily slows the action interval during Boss fights or dangerous attacks.
- `resume`: Resumes after the safe HP condition is met.

## Recommended Phrasing for Regular Users

Regular users do not need to remember the low-level entries below. Prefer passing the user's original phrasing to `sts2_neko_command`, and let the plugin internally decide whether to check status, give advice, actually play a card, execute a step, start auto-play, pause, resume, stop, review recent plays, answer auto-play questions, or use the phrasing as soft guidance during auto-play.

Recommended interaction rules:

| User Phrasing | Plugin Behavior |
| --- | --- |
| `is the spire connected` / `what's the situation now` | Only check connection, status, or snapshot; do not operate the game. |
| `how should I play this turn` / `which card is best to play` | Only recommend one playable card and explain the reason; do not auto-play. |
| `play a card for me` / `pick a card and play it` | After explicit authorization, only pick one from `play_card` actions and play it. |
| `play one step for me` / `execute one step` | After explicit authorization, execute one legal action, which may include ending the turn, picking a reward, or moving on the map. |
| `play this run for me` / `auto-play for a bit` | Start semi-auto play; default stop condition is current floor completion. |
| `defend first` / `don't be greedy with damage` | While auto-play is running, this becomes soft guidance for the next decision round; when not running, conservatively ask for clarification rather than acting. |
| `how was that play` / `review the last card I played` | Provide a play-feel review based on the latest lightweight snapshot; do not operate the game. |
| `why play it that way` / `what are you doing` | While auto-play is running, answer the current strategy and board reasoning; do not perform extra actions. |
| `pause for a moment` / `continue` / `let's stop` | Pause, resume, or stop auto-play respectively. |

Safe defaults: consultation does not operate, vague phrasing does not execute dangerous actions; only when the user explicitly says "play for me", "execute", "auto-play", or "take over" will real actions be performed.

## Plugin Entries

The following entries are exposed to the host and can be called directly in N.E.K.O. For regular user scenarios, prefer calling `sts2_neko_command`; other entries are mainly precise control interfaces for developers.

### `sts2_neko_command`

The natural-language master entry for Slay the Spire. Prefer calling it when the user has not explicitly specified a low-level tool.

Parameters:

- `command`: required, the user's original phrasing. Example: `how should I play this turn`, `play a card for me`, `defend first`, `pause for a moment`.
- `scope`: optional, default `auto`. Possible values: `auto`, `status`, `advice`, `one_card`, `one_action`, `autoplay`, `control`, `guidance`, `review`, `question`, `chat`.
- `confirm`: optional, default `false`. Used to confirm high-risk operations such as continuous takeover.

The return includes `intent`, `action`, `executed`, `needs_confirmation`, `summary`, and the underlying `result`.

### `sts2_health_check`

Checks whether the local Spire Agent service is available.

### `sts2_refresh_state`

Forces a refresh of the current Spire state.

### `sts2_get_status`

Gets connection status, auto-play status, current mode, character strategy, semi-auto task, recent errors, recent actions, and other information.

### `sts2_get_snapshot`

Gets the most recently cached game snapshot and currently executable actions.

### `sts2_step_once`

Executes one step under the current strategy.

### `sts2_play_one_card_by_neko`

Lets the catgirl pick and play a card.

Parameters:

- `objective`: optional, user authorization goal. Example: `pick a card and play it for me`.

Behavior:

1. Reads the current player, hand, enemies, and legal actions.
2. Keeps only `play_card` actions.
3. Lets the current mode/strategy pick a card.
4. First pushes "which card is about to be played and why" to the frontend.
5. Re-validates that the action is still legal.
6. Plays the card and pushes the completion observation.

If there is no playable card right now, returns `idle` and pushes the failure reason.

### `sts2_start_autoplay`

Starts the background semi-auto play loop.

Parameters:

- `objective`: optional, user authorization goal. Example: `clear this floor for me`.
- `stop_condition`: stop condition, default `current_floor`.

`stop_condition` supports:

- `current_floor`: ends after the current floor is completed or the next floor is entered.
- `current_combat` / `combat`: ends after combat is entered during the task and then exited.
- `manual` / `none`: does not auto-complete; must be stopped manually.

After starting, the plugin creates a semi-auto task context and pushes a task-start event to the frontend. When the task completes, `semi_auto_task_completed` is pushed.

### `sts2_pause_autoplay`

Pauses auto-play.

### `sts2_resume_autoplay`

Resumes paused auto-play whose background task still exists. If the background task no longer exists, it safely returns `idle` and will not implicitly restart auto-play.

### `sts2_stop_autoplay`

Stops auto-play and clears the semi-auto task context.

### `sts2_get_history`

Gets recent action and state history.

Parameters:

- `limit`: number of entries to return, default `20`, clamped to `1 ~ 100`.

### `sts2_send_neko_guidance`

Sends catgirl soft guidance to the background auto-play. The guidance enters the queue and is injected into the context for the next LLM decision round.

Parameters:

- `content`: required, natural-language guidance content. Example: `defend first, don't rush damage`.
- `step`: optional, the corresponding step number.
- `type`: optional, default `soft_guidance`.

### `sts2_set_mode`

Sets the auto-play mode.

Parameters:

- `mode`: supports `full-program` / `全程序`, `half-program` / `半程序`, `full-model` / `全模型`.

### `sts2_set_character_strategy`

Sets the character strategy name.

Parameters:

- `character_strategy`: matched against `strategies/<name>.md` after name normalization. For example, `defect` matches `strategies/defect.md`.

### `sts2_set_speed`

Sets speed parameters and writes them back into the local `plugin.toml`.

Parameters:

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## Typical Usage

### Check Connection

1. Launch *Slay the Spire 2*.
2. Confirm `http://127.0.0.1:8080/health` is accessible.
3. Call `sts2_health_check` in N.E.K.O.

### Manually Execute One Step

Call:

```text
sts2_step_once
```

The plugin will pick and execute a legal action based on the current `mode` and `character_strategy`.

### Let the Catgirl Play a Card

The user can say something like:

```text
pick a card and play it for me
```

The host should call:

```text
sts2_play_one_card_by_neko
```

The plugin will only pick from currently playable cards and will not pick end-turn, map, reward, or other actions.

### Let the Catgirl Help Clear a Floor

The user can say:

```text
clear this floor for me
```

The host should call:

```text
sts2_start_autoplay
```

Recommended parameters:

```json
{
  "objective": "clear this floor for me",
  "stop_condition": "current_floor"
}
```

While the task is running, observation events are only progress reports and do not represent completion. Only upon receiving the semi-auto task completion event should you tell the user that this floor is done.

### Mid-task Guidance

During auto-play, the user or catgirl can send guidance:

```text
defend first, don't take too much damage
```

Should call:

```text
sts2_send_neko_guidance
```

Recommended parameters:

```json
{
  "content": "defend first, don't take too much damage",
  "type": "soft_guidance"
}
```

The guidance will be referenced in the next LLM decision round. The `full-program` mode does not rely on a model, so soft guidance has limited impact.

## Frontend Push Events

The plugin pushes the following categories of events through the host's message channel. Aside from task start/completion, errors, and single-card previews, normal observations try to use short text and trimmed metadata to reduce user token consumption.

| Event Type | Description |
| --- | --- |
| `action` | Normal auto-play action observation, controlled by probability. |
| `error` | Auto-play error, force-pushed. |
| `neko_report` | Full catgirl observation report, including current board, hand, enemies, tactical summary, and model reasoning. |
| `neko_card_task_planned` | Catgirl single-card task plans to play a card. |
| `neko_card_task_completed` | Catgirl single-card task executed. |
| `neko_card_task_failed` | Catgirl single-card task could not be executed. |
| `semi_auto_task_started` | Semi-auto task started. |
| `semi_auto_task_completed` | Semi-auto task completed. |
| `neko_autonomous_action` | System autonomously paused, slowed, or resumed. |

Note: `neko_report` is a process observation, not a task-completion notification. The frontend or dialogue logic should not describe a single-step action, card play, end-turn, or state refresh as "task complete", "Boss defeated", "combat over", or "run cleared". If the catgirl wants to influence the next decision round, call `sts2_send_neko_guidance`; if she wants to hard-control the flow, call the pause, resume, or stop entries.

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

### Catgirl mid-task guidance has no obvious effect

Check:

- Whether the current mode is `half-program` or `full-model`.
- Whether `sts2_send_neko_guidance` returned `ok`.
- Whether the guidance content is specific enough, such as "prioritize defense", "hit the lowest-HP enemy first", or "save the potion".
- Whether the current legal actions can actually satisfy the guidance.

### Semi-auto task never finishes

Check `stop_condition`:

- If it is `manual` / `none`, the task will not complete automatically; you must call `sts2_stop_autoplay`.
- If it is `current_combat`, the task completes after combat has been entered during the task and then exited.
- If it is `current_floor`, it usually completes after the current floor is cleared or the next floor is entered.

You can call `sts2_get_status` to inspect `autoplay.task`.

### Stuck in events, popups, or transitional states

The current version already handles events, popups, and transitional states. Priority actions include:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

If it is still stuck, first use `sts2_get_snapshot` to inspect the current `screen` and `available_actions`.

### Auto-play suddenly pauses or slows down

This may have triggered safety protections:

- It pauses when HP ratio falls below `neko_auto_low_hp_threshold`.
- It slows down during Boss fights or dangerous attacks.
- If `neko_auto_resume_after_low_hp` is `true`, it may auto-resume after HP recovers to `neko_auto_safe_hp_threshold`.

You can call `sts2_get_status` to inspect the state, or call `sts2_resume_autoplay` / `sts2_stop_autoplay` to handle it.
