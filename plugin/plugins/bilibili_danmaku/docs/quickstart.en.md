# Bilibili Danmaku Listener - Quick Start

A few steps to set up real-time Bilibili live room danmaku monitoring, paired with AI replies, a background LLM, and Bilibili read/write tools.

---

## 1. Connection Status

The top card shows the plugin's runtime status in real time:

| Metric | Meaning |
|--------|---------|
| Status light | Gray = disconnected, green = connected, yellow = connecting, red = error |
| Received | Cumulative count of danmaku / gifts / SC received |
| Filtered | Number of messages blocked by filter rules |
| Buffer | Danmaku waiting to be aggregated and pushed |
| Popularity | Current popularity value of the live room |

---

## 2. Sign In to Your Bilibili Account

Click the "Bilibili Account" section to open the sign-in panel:

- **QR code sign-in** (recommended): Click "QR Sign-in" → scan the QR code with the Bilibili App → wait for automatic confirmation
- **Check credentials**: View current sign-in status, username, UID, and expiration
- **Reload credentials**: Manually refresh the sign-in state
- **Clear sign-in**: Delete locally encrypted credentials and sign out

> 💡 Manual Cookie entry has been removed to prevent leaking sensitive information. Guest mode can receive danmaku but cannot send danmaku or use advanced filtering.

---

## 3. Live Room Settings

In the "Live Room Settings" area:

1. **Enter the room ID** — Get it from the live room URL, for example `22925943` in `https://live.bilibili.com/22925943`
2. **Click "Switch Room"** — Apply the new room ID
3. **Click "▶ Start Listening"** — Connect to the danmaku server and start receiving danmaku

**Sending live danmaku**:

- "Let NEKO speak" off: Send the input content directly to the live room
- "Let NEKO speak" on: Pass the input plus live room context to NEKO, which generates a reply in character before sending

---

## 4. AI Push Settings

Controls how danmaku are pushed to the AI for processing:

| Setting | Description |
|---------|-------------|
| Push interval (seconds) | Time interval at which aggregated danmaku are pushed to the AI. 10–30 seconds recommended. Too short means the AI reacts too often; too long means high reply latency |
| Max danmaku length | Bilibili limits danmaku to 20 characters; the AI's reply is automatically truncated beyond that. Keeping it at 20 is recommended |
| Target AI name | Specifies which AI receives the danmaku push. Leave empty to push to the default AI |
| Owner's Bilibili UID / username | Once an owner account is set, NEKO will treat the owner's messages specially (priority replies, different tone, etc.) |

---

## 5. Real-time Danmaku Stream

Displays received danmaku, gifts, and SC in real time:

| Type | Left border color | Description |
|------|-------------------|-------------|
| Danmaku | Pink | Regular user danmaku, shows username, level, fan badge |
| Gift | Gold | Records of users sending gifts |
| SC (Super Chat) | Green | Paid highlighted message |

**Control buttons**:

- **Auto-scroll**: When enabled, new danmaku automatically scroll into view
- **⏸ Pause / ▶ Resume**: Pause or resume the danmaku stream display
- **🗑 Clear**: Clear the current danmaku display history

---

## 6. Bilibili Read Tools

Read public Bilibili data without write permissions — safe to call. Fill in the "keyword / BV / UID / favorites ID" fields above, then click the matching button:

| Tool | Purpose | Required parameters |
|------|---------|---------------------|
| Search videos | Search videos by keyword | Keyword |
| Trending videos | Site-wide trending video list | - |
| Hot search | Bilibili real-time hot search ranking | - |
| Weekly must-watch | Weekly must-watch picks | - |
| Ranking | Ranking for a specific category | Order/category (e.g. `all`/`game`/`dance`) |
| Video info | Get video details | BV |
| Video comments | Get the video's comment list | BV |
| Video subtitles | Get AI-generated subtitles | BV |
| Historical danmaku | Get a video's historical danmaku | BV |
| User info | Get user profile | UID |
| User uploads | Get a user's uploaded video list | UID |
| Favorites list | Get a user's favorites list | UID |
| Favorites contents | Get videos inside a favorites folder | Favorites media_id |

Call results are uniformly shown in the "Bilibili Tool Results" area.

---

## 7. Bilibili Write Tools

Perform write operations on Bilibili — **these affect your account**, so use with care:

| Tool | Purpose | Required parameters |
|------|---------|---------------------|
| Post comment / reply | Post a comment under a video or reply to a comment | BV + comment content; replies also require the comment rpid |
| Post moment | Publish a new moment | Moment text content (images optional) |
| Send DM | Send a direct message to a user | Target UID + message content |

- **Let NEKO speak**: When enabled, comments / moments / DMs are first generated in character by NEKO before being sent
- Write-tool buttons are red. Before calling, confirm: account is signed in, content is correct, and the target is right

---

## 8. Background LLM Settings

Once enabled, danmaku are aggregated and sent to a designated LLM that generates guidance prompts, so NEKO responds more naturally to the live room atmosphere.

**Basic configuration**:

| Setting | Description |
|---------|-------------|
| Enable switch | Turn the background LLM feature on/off |
| API endpoint | OpenAI-compatible endpoint, e.g. `https://api.openai.com/v1/chat/completions` |
| Model name | E.g. `gpt-4o-mini`, `deepseek-chat` |
| API Key | API key (hidden by default; click 👁 to reveal) |
| Aggregation window | Number of danmaku to collect before triggering an LLM summary; 10–20 recommended |
| Max sample size | Maximum capacity of the danmaku sample pool; older danmaku are evicted by time when exceeded |

**Advanced settings** (click "Advanced settings" to expand):

| Setting | Description |
|---------|-------------|
| Catgirl name | Auto-substituted for the `{name}` placeholder in prompts |
| Knowledge base context | Persona, catchphrases, common memes — supports the `{name}` placeholder |
| User profile summary | Basic profile of the streamer/users for LLM reference |
| Prompt template | Custom System Prompt; supports `{name}` and `{knowledge_context}` placeholders. Leave empty to use the default template |

> 💡 After configuring, click "Save Configuration" then turn on the enable switch. Click "🔍 Test" to verify API connectivity.

---

## FAQ

| Question | Solution |
|----------|----------|
| QR sign-in fails | Make sure the App is signed in; the QR code is valid for 2 minutes — refresh and scan again |
| Danmaku listener has no response | Check the room ID, network, and whether the account is signed in |
| AI does not reply to danmaku | Confirm the push interval is set, the background LLM is enabled, and API settings are correct |
| Sending danmaku fails | Make sure you're signed in and have permission to send danmaku in this live room (some rooms restrict by account level) |
| API call errors | Check the API endpoint, model name, and API Key; click "🔍 Test" to troubleshoot |
