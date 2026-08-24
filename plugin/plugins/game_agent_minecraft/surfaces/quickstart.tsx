import {
  Alert,
  Button,
  ButtonGroup,
  Card,
  Grid,
  KeyValue,
  Page,
  Stack,
  StatusBadge,
  Step,
  Steps,
  Text,
  Tip,
  Warning,
  useEffect,
  useRef,
  useState,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps, Tone } from "@neko/plugin-ui"

type LocaleKey = "zh-CN" | "en" | "ja" | "ko" | "ru"

// The admin URL is the mindserver web UI mc-agent ships. It speaks
// settings_spec.json under the hood and supports live per-agent restart
// — the cleanest path for end users to change MC port, profile, etc.
const ADMIN_PANEL_URL = "http://localhost:8765"
const STATUS_REFRESH_INTERVAL_MS = 5000

// mc-agent is distributed as a zip on three netdisks (China-friendly +
// global). End users pick whichever one is fastest, download, unzip
// anywhere on disk, and double-click the bundled 启动.bat to
// run it — it's a separate program from N.E.K.O., the two communicate
// over WebSocket (ws://localhost:48909 by default). We deliberately do
// NOT bundle / auto-spawn mc-agent from N.E.K.O.: non-MC users would
// carry a ~200 MB node_modules + portable Node tax for nothing, and
// the two projects need to evolve independently.
const DOWNLOAD_LINKS = {
  quark: "https://pan.quark.cn/s/b662424f7f34",
  gdrive:
    "https://drive.google.com/drive/folders/1DSx_y1MsTEvc5ljsjURNJ0aP1ax3RoN-?usp=drive_link",
  baidu: "https://pan.baidu.com/s/1i_a6IUQDz-GpEaWGvIcnqw?pwd=kuro",
}

// Open an external URL from inside this hosted-tsx surface.
//
// The surface renders in a sandbox="allow-scripts" iframe (see
// HostedSurfaceFrame.vue) — no allow-same-origin, no allow-popups. That
// means window.electronShell is unreachable and window.open is a silent
// no-op here, which is why the download / admin-panel links did nothing.
// Instead we postMessage the URL up to the host, which routes it through
// its own openExternalUrl (system browser in Electron, new tab in a real
// browser). Same channel the markdown surface's link interceptor uses;
// the handler lives in HostedSurfaceFrame.vue (neko-hosted-surface-open-external).
function openExternalUrl(url: string): void {
  if (window.parent && window.parent !== window) {
    window.parent.postMessage(
      { type: "neko-hosted-surface-open-external", payload: { url } },
      "*",
    )
    return
  }
  window.open(url, "_blank", "noopener,noreferrer")
}

type StatusCopy = {
  title: string
  refresh: string
  openAdmin: string
  connected: string
  disconnected: string
  checking: string
  unknown: string
  taskLabel: string
  taskIdle: string
  adminHint: string
  errorPrefix: string
}

type DownloadCopy = {
  title: string
  hint: string
  quark: string
  gdrive: string
  baidu: string
}

type GuideCopy = {
  title: string
  subtitle: string
  notice: string
  cards: Array<{ title: string; badge: string; body: string }>
  status: StatusCopy
  download: DownloadCopy
  setupTitle: string
  setupSteps: Array<{ title: string; body: string }>
  portsTitle: string
  ports: Array<{ key: string; label: string; value: string }>
  tipsTitle: string
  tips: string[]
  warning: string
}

// Inline COPY keeps the quickstart self-contained — no `t()` round-trip,
// no extra i18n JSON file just for this surface. The five locales below
// mirror galgame's quickstart so anyone translating that plugin can keep
// working in lockstep across both.
const COPY: Record<LocaleKey, GuideCopy> = {
  "zh-CN": {
    title: "Minecraft 游戏插件 快速开始",
    subtitle: "让猫娘陪你玩 MC——她会有一个自己的游戏角色，和你在同一个世界里一起行动。",
    notice: "mc-agent 还在持续更新中：安装流程和控制面板的界面会随版本变化，网盘里的包也会不定期替换。如果你看到的界面和这里写的对不上，先用本页的「下载 mc-agent」卡片重新下一份最新的包；重下之后还是对不上，那就是新版还没发布，请耐心等待更新。",
    cards: [
      { title: "先装 Minecraft", badge: "Install", body: "Java 版 v1.21.1 推荐，其他 1.21.x 也可。自己买正版或离线启动。" },
      { title: "再开 mc-agent", badge: "Setup", body: "下面下个 mc-agent 解压、双击「启动.bat」启动它。它和 N.E.K.O 是两个各自独立的程序，都开着就会自动连上。首次启动会自动打开控制面板网页，密钥、模型、游戏端口全在网页里填，不用碰任何文件。猫娘默认用离线模式进游戏，不需要另外给她买正版账号（代价是进不了开了正版验证的联机服务器，你自己开的局域网世界不受影响）。" },
      { title: "最后和猫娘一起玩吧", badge: "Play", body: "先在 MC 里把单人世界「对局域网开放」，猫娘才连得进来。然后和她正常聊天，她会一边陪你说话、一边和你在游戏里一起玩，就像身边真多了个玩家。（部分 AI 供应商暂时没法同时语音对话和操作游戏。）" },
    ],
    status: {
      title: "mc-agent 状态",
      refresh: "刷新",
      openAdmin: "打开管理面板",
      connected: "已连接",
      disconnected: "未连接",
      checking: "检查中…",
      unknown: "未知",
      taskLabel: "当前任务",
      taskIdle: "（空闲）",
      adminHint: "密钥、模型、MC 端口、猫娘的名字和皮肤全在管理面板里改，这是唯一的配置入口。",
      errorPrefix: "查询失败：",
    },
    download: {
      title: "下载 mc-agent",
      hint: "三个网盘任选其一，下载完解压到一个短路径的目录（整条路径最好别超过 90 个字符），双击里面的「启动.bat」即可。启动后回这里点刷新看状态。已经装好了也可以从这里下新版覆盖更新。",
      quark: "夸克网盘",
      gdrive: "Google Drive",
      baidu: "百度网盘（提取码 kuro）",
    },
    setupTitle: "完整流程",
    setupSteps: [
      { title: "1. 装 Minecraft Java Edition", body: "推荐 v1.21.1（1.21.x 系列都行）。自己选择正版 / 离线启动器。" },
      { title: "2. 装 mc-agent（如果上面状态显示「未连接」）", body: "用上面的下载卡片，三个网盘挑一个下 mc-agent 压缩包，解压到一个路径短一点的目录（比如 C:\\mc-agent 或桌面）。然后双击里面的「启动.bat」（会开一个命令行黑窗口，别关）。它第一次启动会提示「还没有选择 AI 供应商，bot 暂不启动」，同时自动打开控制面板网页——接下来全部配置都在那个网页里做，不需要复制或编辑任何文件。" },
      { title: "3. 在控制面板「AI 配置」页填密钥", body: "供应商分「国内直连 / 境外服务 / 本机运行」三组，国内那组不需要梯子、注册即用（DeepSeek、通义千问、智谱 GLM、Kimi、豆包、硅基流动等）。点一家 → 粘贴密钥（每张卡片上都有「去申请密钥」的外链）→ 点「测试连接」→ 测通后模型下拉会自动拉到该供应商的真实模型列表，挑一个保存。密钥保存在你自己电脑上、网页里只显示掩码；调用模型时它会发给你选的那家供应商——用别人家的 API 本来就得带密钥认证。" },
      { title: "4. 开 MC 世界并「对局域网开放」", body: "进入单人世界 → ESC → 对局域网开放 → 选游戏模式 → 创建局域网世界。MC 会在聊天框显示「Local game hosted on port XXXXX」，记下这个端口号。" },
      { title: "5. 在控制面板「游戏连接」页填端口，再点「启动 Bot」", body: "点上面「打开管理面板」按钮 → 「游戏连接」页 → 把上一步抄下来的端口填进去 → 保存 → 点「启动 Bot」。包里自带的端口是占位值 25565，必须换成你自己那局的。" },
      { title: "6. 验证 bot 进游戏了", body: "MC 聊天框会看到「Neko joined the game」。看不到就刷新本页状态，或者看「启动.bat」那个黑窗口最后几行报什么错。" },
      { title: "7. 跟猫娘说话", body: "你可以和猫娘一边聊天一边玩耍，她会根据你的要求和她自己的想法行动。" },
    ],
    portsTitle: "端口说明",
    ports: [
      { key: "mc", label: "MC 游戏端口", value: "你「对局域网开放」时显示的那个数字，bot 通过它连进游戏世界。包里自带的是占位值 25565，一定要换成你自己那局的。" },
      { key: "admin", label: "管理面板（localhost:8765）", value: "上面那个「打开管理面板」按钮跳的就是这个。密钥、模型、猫娘的名字和人设、皮肤、游戏连接、行为开关全在这里改。" },
    ],
    tipsTitle: "排错",
    tips: [
      "状态一直「未连接」：没启动「启动.bat」，或者启动后报错就退了；看那个黑窗口最后几行报什么错。",
      "解压时报「找不到路径的一部分」：不是压缩包坏了，是解压目录路径太长（Windows 上限 260 字符）。换个短路径重解压，比如 C:\\mc-agent。",
      "bot 进不了 MC 世界：99% 端口对不上；MC 那边随机端口，每次重新「对局域网开放」都不一样，要回控制面板「游戏连接」页改。",
      "bot 进了但啥也不干：可能是 N.E.K.O. 没有正确识别连接；回本页点刷新看状态，并在 N.E.K.O 设置页确认本插件是「已启用」。",
      "在面板里换了皮肤但游戏里没变：皮肤要真显示出来，Minecraft 服务端得装 FabricTailor 模组；没装的话面板里能看到、游戏里还是默认皮肤，其他功能不受影响。",
      "想关掉 mc-agent：在管理面板的 bot 列表里点 Stop，或者直接关 N.E.K.O。",
    ],
    warning: "猫娘在 MC 世界里的行为受你的指令和当前 AI 模型能力影响，复杂任务可能会失败或绕路。",
  },
  en: {
    title: "Minecraft Game Plugin — Quickstart",
    subtitle: "Let neko-chan play MC with you — she gets her own in-game character and moves around the same world you do.",
    notice: "mc-agent is still under active development: the install flow and the control panel UI change between versions, and the netdisk archives get replaced from time to time. If what you see doesn't match this page, first re-download the latest archive from the \"Download mc-agent\" card on this page. If it still doesn't match after that, the new build simply isn't out yet — please be patient and wait for the update.",
    cards: [
      { title: "Install Minecraft", badge: "Install", body: "Java Edition v1.21.1 recommended; other 1.21.x versions also work. Use any launcher you like." },
      { title: "Run mc-agent", badge: "Setup", body: "Download mc-agent below, unzip, double-click 启动.bat. It's a separate program from N.E.K.O.; run both and they connect on their own. On first launch it opens a control panel in your browser — API key, model and game port all go in there, you never edit a file. Neko-chan joins in offline mode by default, so she doesn't need her own paid Minecraft account (the trade-off: she can't join online-mode servers; your own LAN world is unaffected)." },
      { title: "Play together", badge: "Play", body: "First, open your single-player world to LAN so neko-chan can connect. Then just chat with neko-chan — she'll keep talking with you while playing alongside you in the world, like a real second player. (Some AI providers can't yet voice-chat and operate the game at the same time.)" },
    ],
    status: {
      title: "mc-agent Status",
      refresh: "Refresh",
      openAdmin: "Open admin panel",
      connected: "Connected",
      disconnected: "Disconnected",
      checking: "Checking…",
      unknown: "Unknown",
      taskLabel: "Current task",
      taskIdle: "(idle)",
      adminHint: "API key, model, MC port, neko-chan's name and skin are all changed in the admin panel — it's the only place you configure anything.",
      errorPrefix: "Query failed: ",
    },
    download: {
      title: "Download mc-agent",
      hint: "Pick whichever drive is fastest. Unzip into a folder with a short path (keep the whole path under ~90 characters), double-click 启动.bat inside to launch, then hit Refresh here. Already set up? These are also the links to grab a newer build and update over it.",
      quark: "Quark Drive (CN)",
      gdrive: "Google Drive",
      baidu: "Baidu Pan (code: kuro)",
    },
    setupTitle: "Full setup flow",
    setupSteps: [
      { title: "1. Install Minecraft Java Edition", body: "v1.21.1 recommended (any 1.21.x is fine). Pick any launcher (official, MultiMC, Prism, etc.)." },
      { title: "2. Install mc-agent (if status above is \"Disconnected\")", body: "Use the download card above — pick any of the three drives, grab the mc-agent archive, extract it into a short path (e.g. C:\\mc-agent or your Desktop). Then double-click 启动.bat inside (it opens a black console window — don't close it). On first launch it prints \"no AI provider selected yet, bot not started\" and opens the control panel in your browser — everything from here on is done in that web page, you never copy or edit a file." },
      { title: "3. Set your API key on the panel's \"AI config\" page", body: "Providers are grouped into China-direct / overseas / local. Pick one → paste your key (each card links straight to that vendor's console to get one) → click \"Test connection\" → once it passes, the model dropdown auto-fills with that provider's real model list; pick one and save. Your key is stored on your own machine and always shown masked in the UI; it does get sent to the provider you picked whenever a request is made — that is how authenticating against their API works." },
      { title: "4. Open a world to LAN", body: "Single player → ESC → Open to LAN → pick game mode → Start. MC will print \"Local game hosted on port XXXXX\" in chat. Note the port number." },
      { title: "5. Enter the port on the \"Game connection\" page, then Start Bot", body: "Click \"Open admin panel\" above → Game connection page → type in the port you wrote down → save → click \"Start Bot\". The shipped value 25565 is only a placeholder; it must be replaced with your own session's port." },
      { title: "6. Confirm the bot joined", body: "You should see \"Neko joined the game\" in MC chat. If not, refresh status here or check the last few lines of the 启动.bat console window." },
      { title: "7. Talk to neko-chan", body: "Chat and play with neko-chan — she'll act on what you ask and on her own ideas." },
    ],
    portsTitle: "Ports",
    ports: [
      { key: "mc", label: "MC game port", value: "The number MC shows when you Open to LAN — the bot uses it to join your world. The shipped value 25565 is only a placeholder and must be replaced with your own session's port." },
      { key: "admin", label: "Admin panel (localhost:8765)", value: "Where the \"Open admin panel\" button goes. API key, model, neko-chan's name and persona, skin, game connection and behavior toggles all live here." },
    ],
    tipsTitle: "Troubleshooting",
    tips: [
      "Status stays \"Disconnected\": 启动.bat isn't running, or it crashed at startup — check the last few lines in that black console window.",
      "Extraction fails with \"Could not find a part of the path\": the archive isn't broken — your target folder path is too long (Windows caps at 260 chars). Re-extract somewhere short like C:\\mc-agent.",
      "Bot can't join the world: 99% wrong port. MC picks a random LAN port every time you re-open to LAN; update it on the panel's Game connection page.",
      "Bot joins but does nothing: N.E.K.O. may not have registered the connection correctly. Hit Refresh here to recheck the status, and confirm this plugin is enabled in N.E.K.O. settings.",
      "Changed the skin in the panel but the game looks the same: skins only render if the Minecraft server has the FabricTailor mod installed. Without it the panel shows your skin but the game keeps the default one; nothing else is affected.",
      "To stop mc-agent: click Stop in the admin panel's bot list, or just close N.E.K.O.",
    ],
    warning: "What neko-chan does in the world depends on your instructions and the capability of the AI model in use; complex tasks may stall or detour.",
  },
  ja: {
    title: "Minecraft ゲームプラグイン クイックスタート",
    subtitle: "猫娘ちゃんと MC を遊ぼう。彼女は自分のキャラクターを持って、同じ世界であなたと一緒に動きます。",
    notice: "mc-agent は現在も更新中です：導入手順やコントロールパネルの画面はバージョンごとに変わり、ネットディスク上のパッケージも随時差し替えられます。ここの説明と実際の画面が食い違う場合は、まずは本ページの「mc-agent をダウンロード」カードから最新のパッケージを取り直してください。それでも合わない場合は新版がまだ公開されていないので、アップデートをお待ちください。",
    cards: [
      { title: "Minecraft を入れる", badge: "Install", body: "Java 版 v1.21.1 推奨。他の 1.21.x でも可。お好きなランチャーで。" },
      { title: "mc-agent を起動", badge: "Setup", body: "下のカードから mc-agent を入手・解凍し「启动.bat」をダブルクリック。N.E.K.O とは別のプログラムで、両方を起動しておけば自動でつながります。初回起動時にブラウザでコントロールパネルが自動的に開き、API キー・モデル・ゲームのポートはすべてそこで入力します（ファイルを編集する必要はありません）。猫娘ちゃんは既定でオフラインモードで参加するので、専用の正規アカウントは不要です（その代わり正規認証のマルチサーバーには入れません。自分で開いた LAN ワールドは問題なし）。" },
      { title: "一緒に遊ぼう", badge: "Play", body: "まず自分のシングルプレイ世界を「LANに公開」すると猫娘ちゃんが入れます。あとは普通におしゃべりするだけ。猫娘ちゃんは会話を続けながら、本物のもう一人のプレイヤーのように一緒に遊んでくれる。（一部の AI プロバイダーでは、音声会話とゲーム操作の同時進行がまだできない場合があります。）" },
    ],
    status: {
      title: "mc-agent ステータス",
      refresh: "更新",
      openAdmin: "管理パネルを開く",
      connected: "接続済み",
      disconnected: "未接続",
      checking: "確認中…",
      unknown: "不明",
      taskLabel: "現在のタスク",
      taskIdle: "（待機）",
      adminHint: "API キー、モデル、MC ポート、猫娘ちゃんの名前、スキンはすべて管理パネルで変更します。設定の入口はここだけです。",
      errorPrefix: "問い合わせ失敗: ",
    },
    download: {
      title: "mc-agent をダウンロード",
      hint: "回線に合うものを選んで DL し、パスの短いフォルダに解凍（全体で 90 文字以内が目安）→ 中の「启动.bat」をダブルクリックで起動 → こちらで更新ボタンを押す。導入済みでも、新版を取り直して上書き更新する入口はここです。",
      quark: "Quark Drive（中国）",
      gdrive: "Google Drive",
      baidu: "百度网盘（パスワード kuro）",
    },
    setupTitle: "セットアップ全体",
    setupSteps: [
      { title: "1. Minecraft Java 版をインストール", body: "v1.21.1 推奨（1.21.x なら何でも）。公式 / MultiMC / Prism いずれでも。" },
      { title: "2. mc-agent をインストール（上が「未接続」なら）", body: "上のダウンロードカードから mc-agent の配布パッケージを取得し、パスの短いフォルダ（例：C:\\mc-agent やデスクトップ）に解凍。中の「启动.bat」をダブルクリックで起動（黒いコンソール窓が開く、閉じないこと）。初回は「AI プロバイダーが未選択のためボットは起動しません」と表示され、ブラウザでコントロールパネルが自動的に開く。以降の設定はすべてその画面で行い、ファイルのコピーや編集は一切不要。" },
      { title: "3. コントロールパネルの「AI 設定」ページで API キーを入力", body: "プロバイダーは「中国国内直結 / 海外サービス / ローカル実行」の 3 グループ。1 つ選ぶ → キーを貼り付け（各カードに発行ページへの外部リンクあり）→「接続テスト」→ 成功するとモデル一覧がそのプロバイダーの実データで埋まるので、1 つ選んで保存。キーは自分の PC に保存され、画面上は常にマスク表示。ただしリクエストのたびに選んだプロバイダーへ送信されます（相手の API を使う以上、キーによる認証は避けられません）。" },
      { title: "4. ワールドを LANに公開", body: "シングルプレイ → ESC → LANに公開 → モード選択 → 開始。チャットに「Local game hosted on port XXXXX」と出るのでポート番号を控える。" },
      { title: "5. 「ゲーム接続」ページにポートを入れて「ボット起動」", body: "上の「管理パネルを開く」→「ゲーム接続」ページ → 控えた番号を入力 → 保存 →「ボット起動」。同梱の 25565 は仮の値なので、必ず自分のセッションのポートに変更すること。" },
      { title: "6. ボットの参加を確認", body: "MC のチャットに「Neko joined the game」と出れば成功。出なければ本ページの状態を更新、または「启动.bat」の黒い窓の最終行のエラーを確認。" },
      { title: "7. 猫娘に話しかける", body: "猫娘とおしゃべりしながら一緒に遊べる。リクエストと猫娘自身の判断で動く。" },
    ],
    portsTitle: "ポート一覧",
    ports: [
      { key: "mc", label: "MC ゲームポート", value: "「LANに公開」時に MC が表示する番号。ボットがこれでワールドに参加。同梱の 25565 は仮の値なので、必ず自分のセッションのポートに変更すること。" },
      { key: "admin", label: "管理パネル（localhost:8765）", value: "「管理パネルを開く」が飛ぶ先。API キー、モデル、猫娘ちゃんの名前と人格、スキン、ゲーム接続、動作スイッチはすべてここ。" },
    ],
    tipsTitle: "トラブルシューティング",
    tips: [
      "ステータスがずっと「未接続」: 「启动.bat」が起動していない、または起動直後にクラッシュ。黒いコンソール窓の最終行のエラーを確認。",
      "解凍時に「パスの一部が見つかりません」: パッケージの破損ではなく、解凍先のパスが長すぎる（Windows の上限は 260 文字）。C:\\mc-agent など短いパスに解凍し直す。",
      "ボットがワールドに入れない: 99% ポート不一致。MC は「LANに公開」のたびにランダムなポートを選ぶので、パネルの「ゲーム接続」ページで更新。",
      "入ったが何もしない: N.E.K.O が接続を正しく認識できていない可能性。本ページで更新して状態を確認し、N.E.K.O 設定で本プラグインが「有効」か確認。",
      "パネルでスキンを変えてもゲーム内が変わらない: スキンの反映には Minecraft サーバー側に FabricTailor モッドが必要。未導入だとパネルには表示されてもゲーム内は既定スキンのまま（他の機能に影響なし）。",
      "mc-agent を止める: 管理パネルのボット一覧から Stop、または N.E.K.O ごと終了。",
    ],
    warning: "世界内での猫娘ちゃんの挙動は、指示内容と使用中の AI モデルの性能に左右されます。複雑なタスクは失敗 / 迂回することがあります。",
  },
  ko: {
    title: "Minecraft 게임 플러그인 빠른 시작",
    subtitle: "고양이와 함께 MC를 즐기세요. 고양이는 자기 캐릭터로 같은 월드에 들어와 너와 함께 움직여요.",
    notice: "mc-agent는 아직 계속 업데이트 중입니다: 설치 흐름과 제어판 화면이 버전마다 달라지고, 네트워크 드라이브의 압축 파일도 수시로 교체돼요. 여기 설명과 실제 화면이 다르면 먼저 이 페이지의 「mc-agent 다운로드」 카드에서 최신 패키지를 다시 받아 보세요. 다시 받아도 그대로면 새 버전이 아직 안 나온 것이니 업데이트를 조금만 기다려 주세요.",
    cards: [
      { title: "Minecraft 설치", badge: "Install", body: "Java 에디션 v1.21.1 권장. 다른 1.21.x도 가능. 원하는 런처 사용." },
      { title: "mc-agent 실행", badge: "Setup", body: "아래에서 mc-agent 다운로드 → 압축 해제 → 「启动.bat」 더블클릭. N.E.K.O와는 별개 프로그램이라 둘 다 켜 두면 알아서 연결돼요. 처음 실행하면 브라우저에 제어판이 자동으로 열리고, API 키·모델·게임 포트를 전부 그 웹 화면에서 입력해요(파일을 건드릴 일 없음). 고양이는 기본적으로 오프라인 모드로 접속하니 정품 계정을 따로 살 필요는 없어요(대신 정품 인증을 켠 멀티 서버에는 못 들어가요. 직접 연 LAN 월드는 문제없음)." },
      { title: "함께 놀기", badge: "Play", body: "먼저 본인 싱글플레이 월드를 「LAN에 공개」해야 고양이가 들어올 수 있어요. 그다음 그냥 평범하게 대화하세요. 고양이는 너와 이야기를 나누면서 진짜 또 한 명의 플레이어처럼 함께 놀아 줍니다. (일부 AI 제공자는 아직 음성 대화와 게임 조작을 동시에 못 할 수 있어요.)" },
    ],
    status: {
      title: "mc-agent 상태",
      refresh: "새로고침",
      openAdmin: "관리 패널 열기",
      connected: "연결됨",
      disconnected: "연결 안 됨",
      checking: "확인 중…",
      unknown: "알 수 없음",
      taskLabel: "현재 작업",
      taskIdle: "(대기)",
      adminHint: "API 키, 모델, MC 포트, 고양이 이름, 스킨 모두 관리 패널에서 변경합니다. 설정 입구는 여기 하나뿐이에요.",
      errorPrefix: "조회 실패: ",
    },
    download: {
      title: "mc-agent 다운로드",
      hint: "네트워크에 맞는 드라이브를 골라 다운로드 후 경로가 짧은 폴더에 압축 해제(전체 경로 90자 이내 권장) → 안의 「启动.bat」 더블클릭으로 실행 → 여기서 새로고침. 이미 설치했더라도 새 버전을 받아 덮어쓰는 입구도 여기예요.",
      quark: "Quark Drive (중국)",
      gdrive: "Google Drive",
      baidu: "百度网盘 (비밀번호 kuro)",
    },
    setupTitle: "전체 설정 흐름",
    setupSteps: [
      { title: "1. Minecraft Java 에디션 설치", body: "v1.21.1 권장 (1.21.x 모두 가능). 공식 / MultiMC / Prism 등 원하는 런처." },
      { title: "2. mc-agent 설치 (위 상태가 「연결 안 됨」이면)", body: "위 다운로드 카드에서 mc-agent 압축 파일을 받아 경로가 짧은 폴더(예: C:\\mc-agent 또는 바탕화면)에 압축 해제. 그다음 안의 「启动.bat」을 더블클릭해 실행 (검은 콘솔 창이 열림, 닫지 말 것). 처음 실행하면 「AI 제공자를 아직 고르지 않아 봇을 시작하지 않습니다」라고 뜨면서 브라우저에 제어판이 자동으로 열려요. 이후 설정은 전부 그 웹 화면에서 하며, 파일을 복사하거나 편집할 일은 없어요." },
      { title: "3. 제어판 「AI 설정」 페이지에서 API 키 입력", body: "제공자는 「중국 직결 / 해외 서비스 / 로컬 실행」 세 그룹. 하나 선택 → 키 붙여넣기(카드마다 발급 페이지 외부 링크 있음) → 「연결 테스트」 → 통과하면 모델 드롭다운이 그 제공자의 실제 모델 목록으로 채워지니 하나 골라 저장. 키는 내 PC에 저장되고 화면에는 항상 마스킹되어 보여요. 다만 요청할 때마다 고른 제공자에게 전송돼요 — 남의 API를 쓰려면 키 인증이 필요하니까요." },
      { title: "4. 월드를 LAN에 공개", body: "싱글 플레이 → ESC → LAN에 공개 → 게임 모드 선택 → 시작. 채팅창에 「Local game hosted on port XXXXX」가 표시되니 포트 번호 기록." },
      { title: "5. 「게임 연결」 페이지에 포트를 넣고 「봇 시작」", body: "위「관리 패널 열기」클릭 → 「게임 연결」 페이지 → 기록한 번호 입력 → 저장 → 「봇 시작」. 기본값 25565는 자리표시자일 뿐이니 반드시 본인 세션의 포트로 바꿔야 해요." },
      { title: "6. 봇 참가 확인", body: "MC 채팅에「Neko joined the game」이 보이면 성공. 안 보이면 본 페이지 상태를 새로고침하거나 「启动.bat」 콘솔 창의 마지막 줄 에러 확인." },
      { title: "7. 고양이에게 말 걸기", body: "고양이와 대화하면서 함께 놀 수 있어. 네 요청과 고양이 본인의 생각에 따라 움직여." },
    ],
    portsTitle: "포트 안내",
    ports: [
      { key: "mc", label: "MC 게임 포트", value: "「LAN에 공개」 시 MC가 보여주는 숫자. 봇이 이를 통해 월드에 참가해요. 기본값 25565는 자리표시자일 뿐이니 반드시 본인 세션의 포트로 바꿔야 해요." },
      { key: "admin", label: "관리 패널 (localhost:8765)", value: "「관리 패널 열기」가 가는 곳. API 키, 모델, 고양이 이름과 성격, 스킨, 게임 연결, 동작 스위치가 전부 여기에 있어요." },
    ],
    tipsTitle: "문제 해결",
    tips: [
      "상태가 계속 「연결 안 됨」: 「启动.bat」이 실행되지 않았거나 실행 직후 죽음. 검은 콘솔 창의 마지막 줄 에러 확인.",
      "압축 해제 중 「경로의 일부를 찾을 수 없습니다」: 파일이 손상된 게 아니라 대상 폴더 경로가 너무 김 (Windows 상한 260자). C:\\mc-agent 같은 짧은 경로에 다시 풀기.",
      "봇이 월드에 못 들어감: 99% 포트 불일치. MC는 「LAN에 공개」할 때마다 랜덤 포트를 고르니 제어판 「게임 연결」 페이지에서 갱신.",
      "들어갔지만 아무것도 안 함: N.E.K.O가 연결을 제대로 인식하지 못했을 수 있어요. 본 페이지에서 새로고침해 상태를 확인하고, N.E.K.O 설정에서 본 플러그인이 「활성화」인지 확인.",
      "패널에서 스킨을 바꿨는데 게임에서 그대로: 스킨이 실제로 보이려면 Minecraft 서버에 FabricTailor 모드가 설치돼 있어야 해요. 없으면 패널에만 보이고 게임에서는 기본 스킨 (다른 기능에는 영향 없음).",
      "mc-agent 종료: 관리 패널의 봇 목록에서 Stop, 또는 N.E.K.O 전체 종료.",
    ],
    warning: "월드 안에서 고양이가 하는 행동은 네 지시와 지금 쓰는 AI 모델의 능력에 따라 달라져요. 복잡한 작업은 실패하거나 돌아갈 수 있어요.",
  },
  ru: {
    title: "Игровой плагин Minecraft — Быстрый старт",
    subtitle: "Играй в MC вместе с нэко-тян — у неё будет свой персонаж, и она будет ходить по тому же миру, что и ты.",
    notice: "mc-agent всё ещё активно обновляется: процесс установки и интерфейс панели управления меняются от версии к версии, архивы на дисках время от времени заменяются. Если увиденное не совпадает с этой страницей, сначала перекачай свежий архив по карточке «Скачать mc-agent» на этой странице. Если и после этого не совпадает — новая версия просто ещё не вышла, дождись обновления.",
    cards: [
      { title: "Установи Minecraft", badge: "Install", body: "Java Edition v1.21.1 рекомендуется; другие 1.21.x тоже подойдут. Любой лаунчер." },
      { title: "Запусти mc-agent", badge: "Setup", body: "Скачай mc-agent ниже, распакуй, дважды кликни 启动.bat. Это отдельная программа от N.E.K.O. — запусти обе, и они соединятся сами. При первом запуске в браузере сама откроется панель управления — ключ API, модель и игровой порт вводятся только там, никакие файлы править не нужно. Нэко-тян по умолчанию заходит в офлайн-режиме, так что отдельный лицензионный аккаунт покупать не надо (расплата: на серверы с проверкой лицензии она не попадёт; твой собственный LAN-мир это не затрагивает)." },
      { title: "Играйте вместе", badge: "Play", body: "Сначала открой свой одиночный мир для сети (кнопка «Открыть для сети»), чтобы нэко-тян смогла подключиться. Затем просто болтай с нэко-тян — она будет общаться с тобой и одновременно играть рядом, как настоящий второй игрок. (У части AI-провайдеров пока не получается одновременно вести голосовой диалог и управлять игрой.)" },
    ],
    status: {
      title: "Статус mc-agent",
      refresh: "Обновить",
      openAdmin: "Открыть админ-панель",
      connected: "Подключено",
      disconnected: "Нет связи",
      checking: "Проверка…",
      unknown: "Неизвестно",
      taskLabel: "Текущая задача",
      taskIdle: "(простой)",
      adminHint: "Ключ API, модель, MC-порт, имя нэко-тян и скин меняются в админ-панели — это единственная точка настройки.",
      errorPrefix: "Ошибка запроса: ",
    },
    download: {
      title: "Скачать mc-agent",
      hint: "Выбери диск побыстрее, распакуй в папку с коротким путём (весь путь лучше держать короче ~90 символов), дважды кликни 启动.bat внутри для запуска, затем жми «Обновить» здесь. Уже всё настроено? Отсюда же качается свежая сборка для обновления поверх.",
      quark: "Quark Drive (Китай)",
      gdrive: "Google Drive",
      baidu: "Baidu Pan (код kuro)",
    },
    setupTitle: "Полный путь настройки",
    setupSteps: [
      { title: "1. Установи Minecraft Java Edition", body: "v1.21.1 рекомендуется (любой 1.21.x подойдёт). Любой лаунчер: официальный, MultiMC, Prism." },
      { title: "2. Установи mc-agent (если статус выше «Нет связи»)", body: "Через карточку «Скачать» выше скачай архив mc-agent и распакуй его в папку с коротким путём (например C:\\mc-agent или на Рабочий стол). Затем дважды кликни 启动.bat внутри (откроется чёрное окно консоли — не закрывай). При первом запуске он напишет «AI-провайдер ещё не выбран, бот не стартует» и сам откроет панель управления в браузере — вся дальнейшая настройка делается на этой веб-странице, копировать или править файлы не нужно." },
      { title: "3. Впиши ключ API на странице «Настройка AI»", body: "Провайдеры разбиты на три группы: китайские напрямую / зарубежные сервисы / локальный запуск. Выбери одного → вставь ключ (на каждой карточке есть ссылка на консоль этого провайдера) → нажми «Проверить соединение» → после успеха выпадающий список моделей заполнится реальным перечнем этого провайдера, выбери одну и сохрани. Ключ хранится на твоём компьютере и в интерфейсе всегда показан маской, но при каждом запросе он отправляется выбранному провайдеру — иначе его API тебя не аутентифицирует." },
      { title: "4. Открой мир для сети", body: "Одиночная игра → ESC → Открыть для сети → выбери режим → Старт. MC напишет в чате «Local game hosted on port XXXXX». Запомни порт." },
      { title: "5. Впиши порт на странице «Подключение к игре» и нажми «Запустить бота»", body: "Жми «Открыть админ-панель» сверху → страница «Подключение к игре» → впиши записанный номер → сохрани → «Запустить бота». Идущее в комплекте значение 25565 — просто заглушка, его обязательно надо заменить на порт своей сессии." },
      { title: "6. Подтверди вход бота", body: "В чате MC появится «Neko joined the game». Если нет — обнови статус здесь или посмотри последние строки в чёрном окне 启动.bat." },
      { title: "7. Поговори с нэко-тян", body: "Болтай с нэко-тян и играй вместе — она будет действовать по твоим просьбам и по собственным идеям." },
    ],
    portsTitle: "Порты",
    ports: [
      { key: "mc", label: "Игровой порт MC", value: "Число, которое показывает MC при открытии для сети — по нему бот заходит в мир. Идущее в комплекте значение 25565 это лишь заглушка, его обязательно надо заменить на порт своей сессии." },
      { key: "admin", label: "Админ-панель (localhost:8765)", value: "Куда ведёт кнопка «Открыть админ-панель». Здесь ключ API, модель, имя и характер нэко-тян, скин, подключение к игре и переключатели поведения." },
    ],
    tipsTitle: "Решение проблем",
    tips: [
      "Статус всё время «Нет связи»: 启动.bat не запущен, или упал на старте — смотри последние строки в том чёрном окне консоли.",
      "При распаковке ошибка «Could not find a part of the path»: архив не битый — слишком длинный путь до папки назначения (предел Windows 260 символов). Распакуй заново в короткий путь, например C:\\mc-agent.",
      "Бот не заходит в мир: 99% — порт не совпадает. MC выбирает случайный LAN-порт при каждом открытии для сети; обнови его на странице «Подключение к игре».",
      "Бот зашёл, но ничего не делает: возможно, N.E.K.O. не распознал подключение правильно. Нажми «Обновить» здесь, чтобы перепроверить статус, и убедись в настройках N.E.K.O., что плагин «включен».",
      "Поменял скин в панели, а в игре без изменений: чтобы скин реально отображался, на сервере Minecraft должен стоять мод FabricTailor. Без него скин виден только в панели, а в игре остаётся стандартный (на остальное не влияет).",
      "Остановить mc-agent: нажми Stop в списке ботов админ-панели, или просто закрой N.E.K.O.",
    ],
    warning: "Что нэко-тян делает в мире, зависит от твоих инструкций и возможностей используемой AI-модели; сложные задачи могут провалиться или пойти в обход.",
  },
}

function resolveLocale(locale: string | undefined): LocaleKey {
  const lower = String(locale || "").trim().toLowerCase().replace("_", "-")
  if (lower === "zh" || lower.startsWith("zh-")) return "zh-CN"
  if (lower.startsWith("ja")) return "ja"
  if (lower.startsWith("ko")) return "ko"
  if (lower.startsWith("ru")) return "ru"
  return "en"
}

type StatusState = {
  loading: boolean
  connected: boolean | null  // null = never queried yet
  pendingTask: string
  error: string
}

// Unwrap the hosted-surface action envelope. ``props.api.call`` resolves to
// ``{plugin_id, action_id, result}`` where ``result`` is the plugin entry's
// own return value (here: the ``game_agent_status`` status dict). Be
// defensive about a bare-dict shape too so a future host change that returns
// the entry value directly doesn't break the surface.
function unwrapActionResult(envelope: any): Record<string, any> {
  if (envelope && typeof envelope === "object") {
    if (envelope.result && typeof envelope.result === "object") return envelope.result
    return envelope
  }
  return {}
}

export default function GameAgentMinecraftQuickstart(props: PluginSurfaceProps) {
  const copy = COPY[resolveLocale(props.locale)]
  const status = copy.status

  const [state, setState] = useState<StatusState>({
    loading: false,
    connected: null,
    pendingTask: "",
    error: "",
  })

  // game_agent_status 走 plugin call → 后端起一个 run，慢链路下可能比
  // STATUS_REFRESH_INTERVAL_MS 还久。没有 in-flight guard 会触发并发
  // run、setState 乱序、卸载后写 state 等问题，加两个 ref 防住。
  const refreshingRef = useRef(false)
  const unmountedRef = useRef(false)

  const refresh = async () => {
    if (refreshingRef.current || unmountedRef.current) return
    refreshingRef.current = true
    setState((prev) => ({ ...prev, loading: true, error: "" }))
    try {
      // Hosted surfaces run in a sandbox="allow-scripts" (no allow-same-origin)
      // iframe, so a raw fetch("/runs") has an opaque origin and fails with
      // "Failed to fetch". The supported path is props.api.call, which bridges
      // to the host via postMessage → callPluginHostedSurfaceAction. Requires
      // the guide to declare permissions=["action:call"] and game_agent_status
      // to be exposed as a UI action (@ui.action) — see plugin.toml / __init__.py.
      const envelope = await props.api.call("game_agent_status")
      if (unmountedRef.current) return
      const data = unwrapActionResult(envelope)
      setState({
        loading: false,
        connected: Boolean(data.connected),
        pendingTask: String(data.pending_task || ""),
        error: "",
      })
    } catch (exc: any) {
      if (unmountedRef.current) return
      const raw = String(exc?.message || exc)
      const code = String(exc?.code || "")
      // The plugin is manual-start (auto_start=false); until it's enabled the
      // hosted-action route rejects with PLUGIN_NOT_RUNNING. On this setup page
      // that's an expected state — keep polling so the badge keeps reflecting
      // the live enable status, but DON'T surface any alert for it (the
      // "disconnected" badge already shows it). Only real/unexpected errors
      // get an alert.
      const notRunning = code === "PLUGIN_NOT_RUNNING" || /not running|PLUGIN_NOT_RUNNING|not started/i.test(raw)
      setState((prev) => ({
        ...prev,
        loading: false,
        connected: false,
        error: notRunning ? "" : status.errorPrefix + raw,
      }))
    } finally {
      refreshingRef.current = false
    }
  }

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, STATUS_REFRESH_INTERVAL_MS)
    return () => {
      unmountedRef.current = true
      window.clearInterval(timer)
    }
  }, [])

  const tone: Tone =
    state.loading && state.connected === null
      ? "default"
      : state.connected
        ? "success"
        : "warning"
  const badgeText =
    state.connected === null
      ? state.loading
        ? status.checking
        : status.unknown
      : state.connected
        ? status.connected
        : status.disconnected

  // Only the player-facing bits go on this card. The bridge URL the status
  // entry also returns (ws://localhost:48909) is plumbing — it belongs in the
  // plugin README, not on a panel end users read.
  const statusItems = [
    {
      key: "task",
      label: status.taskLabel,
      value: state.pendingTask || status.taskIdle,
    },
  ]

  return (
    <Page title={copy.title} subtitle={copy.subtitle}>
      {/* mc-agent 是独立演进的外部程序，安装流程和面板 UI 会随版本变化；
          先把这句摆在最上面，免得用户拿旧包对着新教程一步步试。 */}
      <Alert tone="info">{copy.notice}</Alert>

      <Card title={status.title}>
        <Stack>
          <StatusBadge tone={tone}>{badgeText}</StatusBadge>
          {state.error ? (
            <Alert tone="warning">{state.error}</Alert>
          ) : null}
          <KeyValue items={statusItems} />
          <ButtonGroup>
            <Button onClick={refresh} disabled={state.loading}>
              {status.refresh}
            </Button>
            <Button
              tone="primary"
              onClick={() => openExternalUrl(ADMIN_PANEL_URL)}
            >
              {status.openAdmin}
            </Button>
          </ButtonGroup>
          <Text>{status.adminHint}</Text>
        </Stack>
      </Card>

      {/* Always rendered, including when already connected. An outdated
          mc-agent still speaks the bridge protocol and so reports as
          connected — those are exactly the users the notice above sends
          here to re-download, and hiding the card would strand them. */}
      <Card title={copy.download.title}>
        <Stack>
          <Text>{copy.download.hint}</Text>
          <ButtonGroup>
            <Button
              tone="primary"
              onClick={() => openExternalUrl(DOWNLOAD_LINKS.quark)}
            >
              {copy.download.quark}
            </Button>
            <Button onClick={() => openExternalUrl(DOWNLOAD_LINKS.gdrive)}>
              {copy.download.gdrive}
            </Button>
            <Button onClick={() => openExternalUrl(DOWNLOAD_LINKS.baidu)}>
              {copy.download.baidu}
            </Button>
          </ButtonGroup>
        </Stack>
      </Card>

      <Grid cols={3}>
        {copy.cards.map((card) => (
          <Card key={card.title} title={card.title}>
            <Stack>
              <StatusBadge tone="primary">{card.badge}</StatusBadge>
              <Text>{card.body}</Text>
            </Stack>
          </Card>
        ))}
      </Grid>

      <Card title={copy.setupTitle}>
        <Steps>
          {copy.setupSteps.map((step, index) => (
            <Step key={step.title} index={String(index + 1)} title={step.title}>
              <Text>{step.body}</Text>
            </Step>
          ))}
        </Steps>
      </Card>

      <Card title={copy.portsTitle}>
        <KeyValue items={copy.ports} />
      </Card>

      <Alert tone="info">{copy.tipsTitle}</Alert>
      <Stack>
        {copy.tips.map((tip) => (
          <Tip key={tip}>{tip}</Tip>
        ))}
      </Stack>

      <Warning>{copy.warning}</Warning>
    </Page>
  )
}
