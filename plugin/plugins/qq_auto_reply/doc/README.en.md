# QQ Auto-Reply Plugin

Connects QQ through the OneBot protocol and provides permission-based intelligent auto replies. Supports both private chats and group messages, with AI conversation integration.

## Startup & Onboarding

1. Download a OneBot implementation (NapCat is recommended). The examples below use NapCat.
   Open:
   ```text 
   https://github.com/NapNeko/NapCatQQ/releases
   ```
   Pick a package to download (the recommended one is `NapCat.Shell.zip`).
2. Start NapCat and open the NapCat configuration page.
3. In the left toolbar, choose **Network Configuration**.
4. Add a **WS Server**, enable it, and save.
5. Start the plugin.
6. Go back once.
7. Open this plugin panel again.
8. Follow the guide in order.

Current flow notes:
- The plugin `startup` automatically creates the business config file if it does not exist yet.
- NapCat starts from the configured execution directory first; if that is empty, it falls back to the default directory.
- After NapCat generates the login QR code, it is copied into the plugin static directory; click **Refresh QR Code** in the UI to sync and display the latest image.
- The frontend UI can directly manage:
  - OneBot address / Token / PATH
  - NapCat foreground/background launch
  - Trusted users / trusted groups
  - Auto-reply start/stop

## Features

- OneBot protocol support via NapCat
- Multi-level permission management for users: `admin`, `trusted`, `normal`
- Group permission control: `trusted`, `open`, `normal`
- AI conversation integration through OmniOfflineClient
- Memory sync for admin private chats
- Relay mode for normal users with configurable probability
- Open-group mode for proactive replies without @ mention
- Nickname management for trusted users
- Automatic reconnect with exponential backoff after WebSocket disconnects

## Configuration

It is recommended to configure everything through the plugin UI under **QQ OneBot Service Settings**. The business config file is created automatically on first startup. Common fields include:

- `onebot_url`: OneBot WebSocket address, default `ws://127.0.0.1:3001`
- `token`: OneBot access token
- `napcat_directory`: NapCat execution directory
- `show_napcat_window`: `true` for foreground launch with a visible console, `false` for background launch
- `trusted_users`: trusted user list
- `trusted_groups`: trusted group list
- `normal_relay_probability`: probability of relaying normal messages to the owner
- `truth_reply_probability`: probability of proactive replies in open groups

### Configuration Items

| Key | Type | Description |
|-----|------|-------------|
| `onebot_url` | string | WebSocket address of the OneBot service |
| `token` | string | Access token for the OneBot service (if required) |
| `trusted_users` | array | Trusted user list with QQ number, permission level, and nickname |
| `trusted_groups` | array | Trusted group list with group number and permission level |
| `normal_relay_probability` | float | Probability that a normal user/group message is relayed to the owner |
| `truth_reply_probability` | float | Probability that an `open` group triggers a direct reply without @ mention |

### Probability fields inside trusted groups

Each item in `trusted_groups` can include two optional probability fields in addition to `group_id` and `level`:

- `normal_relay_probability`: only applies when `level = normal`. When a group message enters the “relay to the owner” path, this value overrides the global default relay probability for that group.
- `open_reply_probability`: only applies when `level = open`. In open groups, when nobody @mentions the bot, this value overrides the global default proactive reply probability for that group.

Notes:
- Both values must be decimals between `0` and `1`.
- If left empty, the plugin falls back to the global default setting.
- `trusted` groups do not use either field, because `trusted` groups only enter the normal reply flow after the bot is explicitly @mentioned.

## Permission Levels

### User Permissions

| Level | Meaning | Behavior |
|------|---------|----------|
| `admin` | Administrator | Replies directly in private chat, syncs dialogue to memory, addressed as "Master" |
| `trusted` | Trusted user | Replies directly in private chat, no memory sync, nickname supported |
| `normal` | Normal user | No direct reply, may be relayed to the administrator based on probability |
| `none` | Unauthorized | Message ignored |

### Group Permissions

| Level | Meaning | Behavior |
|------|---------|----------|
| `trusted` | Trusted group | Only replies when the bot is @ mentioned |
| `open` | Open group | Can reply directly without @ mention; uses temporary session memory and character card context; does not write to memory storage |
| `normal` | Normal group | Does not reply directly, may relay to the administrator |
| `none` | Unauthorized | Message ignored |

## Extra Notes

### 1. Proactive Sending

You can call dedicated panel entries to let the bot generate AI-styled content first and then actively send it to the target user or group.

Notes:
- `message` means a prompt for the AI, not the final text to be sent unchanged.
- Existing character persona and model configuration are reused.
- Proactive private sending may read memory context for generation, but this action itself is not written back into memory.
- Proactive group sending is not written into memory.
- The private entry uses `target`: it can be either a numeric QQ ID or a nickname already configured in the trusted-user list.
- `group_id` must be a numeric string.
- `message` cannot be empty.
- Auto-reply must already be started and OneBot must be connected; otherwise the entry fails immediately.

### 2. Stop the plugin

Stopping the plugin will:
- Disconnect the WebSocket
- Clean up runtime resources

## FAQ

### 1. Cannot connect to OneBot

**Problem**: The log shows `Failed to connect to OneBot`

**Fixes**:
- Check whether NapCat is running properly (port 3001)
- Confirm that `onebot_url` is correct
- Verify the `token`

### 2. The bot does not reply

**Problem**: A message is sent but no reply appears

**Fixes**:
- Check whether the sender is in `trusted_users`
- Confirm the permission level (`normal` users do not receive direct replies)
- Check logs to confirm that the message was received
- In `trusted` groups, make sure the bot was @ mentioned
- In `open` groups, no @ mention is required if everything is configured correctly

### 3. Memory sync failed

**Problem**: The log shows memory sync failure

**Fixes**:
- Confirm that Memory Server is running
- Only admin private chats are synced into memory
- Group chats (including `open`) only keep temporary context and do not write into memory storage

### 4. Relay mode is not working

**Problem**: Normal-user messages are not relayed to the administrator

**Fixes**:
- Check whether an administrator (`level = "admin"`) is configured
- Confirm `normal_relay_probability` (default 0.1 = 10%)
- Check the logs to see whether a relay was triggered

## Contact

If you run into any issues, please open an issue or send an email to zhaijiunknown@outlook.com.

## License

This plugin follows the N.E.K.O project license.
