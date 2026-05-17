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
// anywhere on disk, and double-click the bundled 启动mc-agent.bat to
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

// Route external links through the system browser when running inside
// Electron — the embedded Chromium webview spawned by window.open has
// no close affordance and traps users. Falls back to plain window.open
// when running in a real browser (e.g. plugin dev preview). The
// `electronShell` global is exposed by the host preload script (same
// contract used in static/app-proactive.js for url-card / meme links).
//
// Promise.resolve + .catch normalizes both ipcRenderer.invoke (returns
// Promise<void>) and ipcRenderer.send (returns void) preload shapes
// and swallows the unhandled rejection that would otherwise fire on
// IPC failure. We deliberately do NOT fall back to window.open on
// Electron-path rejection: in Electron, window.open IS the trapped
// inner webview behavior this helper exists to avoid. Same shape as
// frontend/react-neko-chat/src/openExternal.ts and
// frontend/plugin-manager/src/utils/openExternal.ts.
function openExternalUrl(url: string): void {
  const shell = (window as any).electronShell
  if (shell && typeof shell.openExternal === "function") {
    Promise.resolve(shell.openExternal(url)).catch((err: unknown) => {
      // eslint-disable-next-line no-console
      console.warn("[openExternalUrl] electronShell.openExternal failed:", err)
    })
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
  wsLabel: string
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
    title: "Minecraft 游戏代理 快速开始",
    subtitle: "让猫娘陪你玩 MC——通过 mc-agent 桥接 mineflayer bot 控制游戏内化身。",
    cards: [
      { title: "先装 Minecraft", badge: "Install", body: "Java 版 v1.21.1 推荐，其他 1.21.x 也可。自己买正版或离线启动。" },
      { title: "再开 mc-agent", badge: "Bridge", body: "下面下个 mc-agent 解压、双击「启动mc-agent.bat」启动它。它和 N.E.K.O 是两个独立程序，靠 WebSocket 联通。" },
      { title: "最后给猫娘任务", badge: "Play", body: "正常聊天，让她「帮我挖石头」「合成镐子」，她会自动调 minecraft_task 派给 bot。" },
    ],
    status: {
      title: "mc-agent 状态",
      refresh: "刷新",
      openAdmin: "打开管理面板",
      connected: "已连接",
      disconnected: "未连接",
      checking: "检查中…",
      unknown: "未知",
      wsLabel: "WebSocket",
      taskLabel: "当前任务",
      taskIdle: "（空闲）",
      adminHint: "改 MC 端口、bot 名字、profile 都去管理面板（mindserver UI）。",
      errorPrefix: "查询失败：",
    },
    download: {
      title: "下载 mc-agent",
      hint: "三个网盘任选其一，下载完解压到任意目录，双击里面的「启动mc-agent.bat」即可。启动后回这里点刷新看状态。",
      quark: "夸克网盘",
      gdrive: "Google Drive",
      baidu: "百度网盘（提取码 kuro）",
    },
    setupTitle: "完整流程",
    setupSteps: [
      { title: "1. 装 Minecraft Java Edition", body: "推荐 v1.21.1（1.21.x 系列都行）。自己选择正版 / 离线启动器。" },
      { title: "2. 装 mc-agent（如果上面状态显示「未连接」）", body: "用上面的下载卡片，三个网盘挑一个下 mc-agent.zip，解压到任意目录。双击里面的「启动mc-agent.bat」启动它（会开一个命令行黑窗口，别关）。N.E.K.O 这边会自动连上。" },
      { title: "3. 开 MC 世界并 Open to LAN", body: "进入单人世界 → ESC → Open to LAN → 选游戏模式 → 开放。MC 会在聊天框显示「Local game hosted on port XXXXX」，记下这个端口号。" },
      { title: "4. 在管理面板里把 MC 端口改成你抄下的那个", body: "点上面「打开管理面板」按钮 → 找到 bot 配置 → 修改 port 字段 → 保存。bot 会自动重启用新端口连进 MC 世界。" },
      { title: "5. 验证 bot 进游戏了", body: "MC 聊天框会看到「Kuro joined the game」。看不到就刷新本页状态，或者看「启动mc-agent.bat」那个黑窗口报什么错。" },
      { title: "6. 跟猫娘说话", body: "你可以和猫娘一边聊天一边玩耍，她会根据你的要求和她自己的想法行动。" },
    ],
    portsTitle: "端口说明",
    ports: [
      { key: "mc", label: "MC 游戏端口（默认 55916）", value: "你 Open to LAN 时显示的那个数字。bot 通过它连进游戏世界。" },
      { key: "mindserver", label: "mindserver 管理端口（默认 8765）", value: "上面那个「打开管理面板」按钮跳的就是这个。改 bot 配置都在这里。" },
      { key: "plugin", label: "plugin 桥接端口（默认 48909）", value: "插件和 mc-agent 之间的内部通信。一般不用动；想动改 NEKO_PLUGIN_WS_PORT 环境变量。" },
    ],
    tipsTitle: "排错",
    tips: [
      "状态一直「未连接」：没启动「启动mc-agent.bat」，或者 bat 启动后报错就退了；看那个黑窗口最后几行报什么错。",
      "bot 进不了 MC 世界：99% 端口对不上；MC 那边随机端口，每次重开都不一样，要在管理面板里改。",
      "bot 进了但啥也不干：可能是 LLM 没拿到 minecraft_task 工具；在 N.E.K.O 设置页确认本插件是「已启用」。",
      "想关掉 mc-agent：在管理面板的 bot 列表里点 Stop，或者直接关 N.E.K.O。",
    ],
    warning: "本插件只控制 bot；它在 MC 世界里的行为受你的指令和当前 LLM 模型能力影响，复杂任务可能会失败或绕路。",
  },
  en: {
    title: "Minecraft Game Agent — Quickstart",
    subtitle: "Let neko-chan play MC with you. mc-agent bridges a mineflayer bot to control an in-game avatar.",
    cards: [
      { title: "Install Minecraft", badge: "Install", body: "Java Edition v1.21.1 recommended; other 1.21.x versions also work. Use any launcher you like." },
      { title: "Run mc-agent", badge: "Bridge", body: "Download mc-agent below, unzip, double-click 启动mc-agent.bat. It's a separate program from N.E.K.O., they talk over WebSocket." },
      { title: "Give a task", badge: "Play", body: "Chat normally and ask neko-chan to \"mine stone\" or \"craft a pickaxe\" — she'll dispatch via minecraft_task." },
    ],
    status: {
      title: "mc-agent Status",
      refresh: "Refresh",
      openAdmin: "Open admin panel",
      connected: "Connected",
      disconnected: "Disconnected",
      checking: "Checking…",
      unknown: "Unknown",
      wsLabel: "WebSocket",
      taskLabel: "Current task",
      taskIdle: "(idle)",
      adminHint: "Change MC port, bot name, or profile via the admin panel (mindserver UI).",
      errorPrefix: "Query failed: ",
    },
    download: {
      title: "Download mc-agent",
      hint: "Pick whichever drive is fastest. Unzip anywhere, double-click 启动mc-agent.bat inside to launch, then hit Refresh here.",
      quark: "Quark Drive (CN)",
      gdrive: "Google Drive",
      baidu: "Baidu Pan (code: kuro)",
    },
    setupTitle: "Full setup flow",
    setupSteps: [
      { title: "1. Install Minecraft Java Edition", body: "v1.21.1 recommended (any 1.21.x is fine). Pick any launcher (official, MultiMC, Prism, etc.)." },
      { title: "2. Install mc-agent (if status above is \"Disconnected\")", body: "Use the download card above — pick any of the three drives, grab mc-agent.zip, extract anywhere. Double-click 启动mc-agent.bat inside (it opens a black console window — don't close it). N.E.K.O. will auto-connect." },
      { title: "3. Open a world to LAN", body: "Single player → ESC → Open to LAN → pick game mode → Start. MC will print \"Local game hosted on port XXXXX\" in chat. Note the port number." },
      { title: "4. Change MC port via admin panel", body: "Click \"Open admin panel\" above → find your bot config → change the port field to the number you wrote down → save. The bot will restart and join your world." },
      { title: "5. Confirm the bot joined", body: "You should see \"Kuro joined the game\" in MC chat. If not, refresh status here or check the 启动mc-agent.bat console window for errors." },
      { title: "6. Talk to neko-chan", body: "Chat and play with neko-chan — she'll act on what you ask and on her own ideas." },
    ],
    portsTitle: "Ports",
    ports: [
      { key: "mc", label: "MC game port (default 55916)", value: "The number MC shows when you Open to LAN. The bot uses this to join your world." },
      { key: "mindserver", label: "mindserver admin port (default 8765)", value: "Where the \"Open admin panel\" button goes. Change bot config here." },
      { key: "plugin", label: "plugin bridge port (default 48909)", value: "Internal channel between this plugin and mc-agent. Don't touch unless port is in use — override with NEKO_PLUGIN_WS_PORT env var." },
    ],
    tipsTitle: "Troubleshooting",
    tips: [
      "Status stays \"Disconnected\": 启动mc-agent.bat isn't running, or it crashed at startup — check the last few lines in that black console window.",
      "Bot can't join the world: 99% wrong port. MC picks a random LAN port each time; update it in the admin panel.",
      "Bot joins but does nothing: the LLM probably didn't pick up the minecraft_task tool. Check this plugin is enabled in N.E.K.O. settings.",
      "To stop mc-agent: click Stop in the admin panel's bot list, or just close N.E.K.O.",
    ],
    warning: "This plugin only controls the bot. In-world behavior depends on your prompts and the current LLM's capability; complex tasks may stall or detour.",
  },
  ja: {
    title: "Minecraft ゲームエージェント クイックスタート",
    subtitle: "猫娘ちゃんと MC を遊ぼう。mc-agent が mineflayer ボットを橋渡しして、ゲーム内アバターを操作します。",
    cards: [
      { title: "Minecraft を入れる", badge: "Install", body: "Java 版 v1.21.1 推奨。他の 1.21.x でも可。お好きなランチャーで。" },
      { title: "mc-agent を起動", badge: "Bridge", body: "下のカードから mc-agent を入手・解凍し「启动mc-agent.bat」をダブルクリック。N.E.K.O とは別プログラムで WebSocket 経由で連携。" },
      { title: "猫娘に指示", badge: "Play", body: "普通に会話して「石を掘って」「ツルハシを作って」と頼めば minecraft_task で自動派遣。" },
    ],
    status: {
      title: "mc-agent ステータス",
      refresh: "更新",
      openAdmin: "管理パネルを開く",
      connected: "接続済み",
      disconnected: "未接続",
      checking: "確認中…",
      unknown: "不明",
      wsLabel: "WebSocket",
      taskLabel: "現在のタスク",
      taskIdle: "（待機）",
      adminHint: "MC ポート、ボット名、プロファイルは管理パネル（mindserver UI）で変更。",
      errorPrefix: "問い合わせ失敗: ",
    },
    download: {
      title: "mc-agent をダウンロード",
      hint: "回線に合うものを選んで DL し、任意の場所に解凍 → 中の「启动mc-agent.bat」をダブルクリックで起動 → こちらで更新ボタンを押す。",
      quark: "Quark Drive（中国）",
      gdrive: "Google Drive",
      baidu: "百度网盘（パスワード kuro）",
    },
    setupTitle: "セットアップ全体",
    setupSteps: [
      { title: "1. Minecraft Java 版をインストール", body: "v1.21.1 推奨（1.21.x なら何でも）。公式 / MultiMC / Prism いずれでも。" },
      { title: "2. mc-agent をインストール（上が「未接続」なら）", body: "上のダウンロードカードから mc-agent.zip を取得し、任意の場所に解凍。中の「启动mc-agent.bat」をダブルクリックして起動（黒いコンソール窓が開く、閉じないこと）。N.E.K.O が自動で接続。" },
      { title: "3. ワールドを LAN 公開", body: "シングルプレイ → ESC → LAN 公開 → モード選択 → 開始。チャットに「Local game hosted on port XXXXX」と出るのでポート番号を控える。" },
      { title: "4. 管理パネルで MC ポートを書き換え", body: "上の「管理パネルを開く」→ ボット設定 → port を控えた番号に変更 → 保存。ボットが再起動して新ポートでワールドに参加。" },
      { title: "5. ボットの参加を確認", body: "MC のチャットに「Kuro joined the game」と出れば成功。出なければ本ページの状態を更新、または「启动mc-agent.bat」の黒い窓のエラーを確認。" },
      { title: "6. 猫娘に話しかける", body: "猫娘とおしゃべりしながら一緒に遊べる。リクエストと猫娘自身の判断で動く。" },
    ],
    portsTitle: "ポート一覧",
    ports: [
      { key: "mc", label: "MC ゲームポート（既定 55916）", value: "LAN 公開時に MC が表示する番号。ボットがこれでワールドに参加。" },
      { key: "mindserver", label: "mindserver 管理ポート（既定 8765）", value: "「管理パネルを開く」が飛ぶ先。ボット設定はここで変更。" },
      { key: "plugin", label: "plugin ブリッジポート（既定 48909）", value: "プラグインと mc-agent の内部通信。基本いじらない。変えるなら NEKO_PLUGIN_WS_PORT 環境変数で。" },
    ],
    tipsTitle: "トラブルシューティング",
    tips: [
      "ステータスがずっと「未接続」: 「启动mc-agent.bat」が起動していない、または起動直後にクラッシュ。黒いコンソール窓の最終行のエラーを確認。",
      "ボットがワールドに入れない: 99% ポート不一致。MC は毎回ランダムポート、管理パネルで更新。",
      "入ったが何もしない: LLM が minecraft_task ツールを認識していない可能性。N.E.K.O 設定で本プラグインが「有効」か確認。",
      "mc-agent を止める: 管理パネルのボット一覧から Stop、または N.E.K.O ごと終了。",
    ],
    warning: "本プラグインはボット操作のみ。世界内での挙動は指示内容と現在の LLM 性能に依存し、複雑なタスクは失敗 / 迂回することがあります。",
  },
  ko: {
    title: "Minecraft 게임 에이전트 빠른 시작",
    subtitle: "고양이 캐릭터와 함께 MC를 즐기세요. mc-agent가 mineflayer 봇을 게임 내 아바타로 다리 놓아 줍니다.",
    cards: [
      { title: "Minecraft 설치", badge: "Install", body: "Java 에디션 v1.21.1 권장. 다른 1.21.x도 가능. 원하는 런처 사용." },
      { title: "mc-agent 실행", badge: "Bridge", body: "아래에서 mc-agent 다운로드 → 압축 해제 → 「启动mc-agent.bat」 더블클릭. N.E.K.O와는 별개 프로그램으로 WebSocket으로 연동." },
      { title: "고양이에게 작업 지시", badge: "Play", body: "평범하게 채팅하며 「돌 캐줘」 「곡괭이 만들어줘」 요청 → minecraft_task로 자동 파견." },
    ],
    status: {
      title: "mc-agent 상태",
      refresh: "새로고침",
      openAdmin: "관리 패널 열기",
      connected: "연결됨",
      disconnected: "연결 안 됨",
      checking: "확인 중…",
      unknown: "알 수 없음",
      wsLabel: "WebSocket",
      taskLabel: "현재 작업",
      taskIdle: "(대기)",
      adminHint: "MC 포트, 봇 이름, 프로필은 관리 패널(mindserver UI)에서 변경.",
      errorPrefix: "조회 실패: ",
    },
    download: {
      title: "mc-agent 다운로드",
      hint: "네트워크에 맞는 드라이브를 골라 다운로드 후 임의 폴더에 압축 해제 → 안의 「启动mc-agent.bat」 더블클릭으로 실행 → 여기서 새로고침.",
      quark: "Quark Drive (중국)",
      gdrive: "Google Drive",
      baidu: "百度网盘 (비밀번호 kuro)",
    },
    setupTitle: "전체 설정 흐름",
    setupSteps: [
      { title: "1. Minecraft Java 에디션 설치", body: "v1.21.1 권장 (1.21.x 모두 가능). 공식 / MultiMC / Prism 등 원하는 런처." },
      { title: "2. mc-agent 설치 (위 상태가 「연결 안 됨」이면)", body: "위 다운로드 카드에서 mc-agent.zip을 받아 임의 폴더에 압축 해제. 안의 「启动mc-agent.bat」을 더블클릭해 실행 (검은 콘솔 창이 열림, 닫지 말 것). N.E.K.O가 자동 연결." },
      { title: "3. 월드를 LAN 공개", body: "싱글 플레이 → ESC → LAN 공개 → 게임 모드 선택 → 시작. 채팅창에 「Local game hosted on port XXXXX」가 표시되니 포트 번호 기록." },
      { title: "4. 관리 패널에서 MC 포트 변경", body: "위「관리 패널 열기」클릭 → 봇 설정 → port 필드를 기록한 번호로 변경 → 저장. 봇이 재시작되어 새 포트로 월드에 참가." },
      { title: "5. 봇 참가 확인", body: "MC 채팅에「Kuro joined the game」이 보이면 성공. 안 보이면 본 페이지 상태를 새로고침하거나 「启动mc-agent.bat」 콘솔 창의 에러 확인." },
      { title: "6. 고양이에게 말 걸기", body: "고양이와 대화하면서 함께 놀 수 있어. 네 요청과 고양이 본인의 생각에 따라 움직여." },
    ],
    portsTitle: "포트 안내",
    ports: [
      { key: "mc", label: "MC 게임 포트 (기본 55916)", value: "LAN 공개 시 MC가 보여주는 숫자. 봇이 이를 통해 월드 참가." },
      { key: "mindserver", label: "mindserver 관리 포트 (기본 8765)", value: "「관리 패널 열기」가 가는 곳. 봇 설정 변경." },
      { key: "plugin", label: "plugin 브릿지 포트 (기본 48909)", value: "플러그인과 mc-agent 사이 내부 통신. 보통 손대지 않음. 바꾸려면 NEKO_PLUGIN_WS_PORT 환경변수." },
    ],
    tipsTitle: "문제 해결",
    tips: [
      "상태가 계속 「연결 안 됨」: 「启动mc-agent.bat」이 실행되지 않았거나 실행 직후 죽음. 검은 콘솔 창의 마지막 줄 에러 확인.",
      "봇이 월드에 못 들어감: 99% 포트 불일치. MC는 매번 랜덤 포트이므로 관리 패널에서 갱신.",
      "들어갔지만 아무것도 안 함: LLM이 minecraft_task 도구를 인식 못 함. N.E.K.O 설정에서 본 플러그인이 「활성화」인지 확인.",
      "mc-agent 종료: 관리 패널의 봇 목록에서 Stop, 또는 N.E.K.O 전체 종료.",
    ],
    warning: "본 플러그인은 봇 제어만 담당. 월드 내 행동은 지시 내용과 현재 LLM 능력에 따라 달라지며 복잡한 작업은 실패 / 우회할 수 있음.",
  },
  ru: {
    title: "Minecraft Game Agent — Быстрый старт",
    subtitle: "Играй в MC вместе с нэко-тян. mc-agent связывает mineflayer-бота с аватаром в игре.",
    cards: [
      { title: "Установи Minecraft", badge: "Install", body: "Java Edition v1.21.1 рекомендуется; другие 1.21.x тоже подойдут. Любой лаунчер." },
      { title: "Запусти mc-agent", badge: "Bridge", body: "Скачай mc-agent ниже, распакуй, дважды кликни 启动mc-agent.bat. Это отдельная программа от N.E.K.O., связь по WebSocket." },
      { title: "Дай задачу", badge: "Play", body: "Общайся обычно: «накопай камня», «скрафти кирку» — нэко-тян диспатчит через minecraft_task." },
    ],
    status: {
      title: "Статус mc-agent",
      refresh: "Обновить",
      openAdmin: "Открыть админ-панель",
      connected: "Подключено",
      disconnected: "Нет связи",
      checking: "Проверка…",
      unknown: "Неизвестно",
      wsLabel: "WebSocket",
      taskLabel: "Текущая задача",
      taskIdle: "(простой)",
      adminHint: "Меняй MC-порт, имя бота, профиль через админ-панель (mindserver UI).",
      errorPrefix: "Ошибка запроса: ",
    },
    download: {
      title: "Скачать mc-agent",
      hint: "Выбери диск побыстрее, распакуй в любую папку, дважды кликни 启动mc-agent.bat внутри для запуска, затем жми «Обновить» здесь.",
      quark: "Quark Drive (Китай)",
      gdrive: "Google Drive",
      baidu: "Baidu Pan (код kuro)",
    },
    setupTitle: "Полный путь настройки",
    setupSteps: [
      { title: "1. Установи Minecraft Java Edition", body: "v1.21.1 рекомендуется (любой 1.21.x подойдёт). Любой лаунчер: официальный, MultiMC, Prism." },
      { title: "2. Установи mc-agent (если статус выше «Нет связи»)", body: "Через карточку «Скачать» выше скачай mc-agent.zip, распакуй в любую папку. Дважды кликни 启动mc-agent.bat внутри (откроется чёрное окно консоли — не закрывай). N.E.K.O автоматически подключится." },
      { title: "3. Открой мир в LAN", body: "Одиночная игра → ESC → Открыть для сети → выбери режим → Старт. MC напишет в чате «Local game hosted on port XXXXX». Запомни порт." },
      { title: "4. Поменяй MC-порт в админ-панели", body: "Жми «Открыть админ-панель» сверху → найди конфиг бота → измени port на записанный номер → сохрани. Бот перезапустится и зайдёт в твой мир." },
      { title: "5. Подтверди вход бота", body: "В чате MC появится «Kuro joined the game». Если нет — обнови статус здесь или посмотри ошибки в чёрном окне 启动mc-agent.bat." },
      { title: "6. Поговори с нэко-тян", body: "Болтай с нэко-тян и играй вместе — она будет действовать по твоим просьбам и по собственным идеям." },
    ],
    portsTitle: "Порты",
    ports: [
      { key: "mc", label: "Игровой порт MC (по умолч. 55916)", value: "Число, которое показывает MC при открытии LAN. Бот использует его для входа в мир." },
      { key: "mindserver", label: "Админ-порт mindserver (по умолч. 8765)", value: "Куда ведёт кнопка «Открыть админ-панель». Меняй настройки бота здесь." },
      { key: "plugin", label: "Мост-порт plugin (по умолч. 48909)", value: "Внутренняя связь между плагином и mc-agent. Обычно не трогай. Меняй переменной окружения NEKO_PLUGIN_WS_PORT." },
    ],
    tipsTitle: "Решение проблем",
    tips: [
      "Статус всё время «Нет связи»: 启动mc-agent.bat не запущен, или упал на старте — смотри последние строки в том чёрном окне консоли.",
      "Бот не заходит в мир: 99% — порт не совпадает. MC выбирает случайный LAN-порт каждый раз; обнови в админ-панели.",
      "Бот зашёл, но ничего не делает: LLM, видимо, не подцепил инструмент minecraft_task. Проверь в настройках N.E.K.O., что плагин «включен».",
      "Остановить mc-agent: нажми Stop в списке ботов админ-панели, или просто закрой N.E.K.O.",
    ],
    warning: "Плагин управляет только ботом. Поведение в мире зависит от твоих инструкций и текущей модели LLM; сложные задачи могут провалиться или пойти в обход.",
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
  wsUrl: string
  pendingTask: string
  error: string
}

async function readJsonResponse(response: Response, label: string) {
  if (!response.ok) {
    throw new Error(`${label} HTTP ${response.status}`)
  }
  return await response.json()
}

// Calls a plugin entry via the run API and waits for the JSON result.
// Polling shape matches the convention in plugin/plugins/study_companion/
// surfaces/knowledge_map.tsx — it's the canonical pattern for surfaces
// that need to read backend state.
async function callPlugin(entryId: string, args: Record<string, unknown> = {}) {
  const createResp = await fetch("/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plugin_id: "game_agent_minecraft", entry_id: entryId, args }),
  })
  const created = await readJsonResponse(createResp, "Run create")
  const runId = created.run_id || created.id
  if (!runId) {
    throw new Error("Run id missing")
  }
  for (let attempt = 0; attempt < 30; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 200))
    const run = await readJsonResponse(await fetch(`/runs/${runId}`), "Run poll")
    if (run.status === "succeeded") {
      const exported = await readJsonResponse(await fetch(`/runs/${runId}/export`), "Run export")
      const item = (exported.items || []).find((c: any) => c.type === "json" && c.json)
      if (!item) {
        throw new Error("Run export missing JSON result")
      }
      if (item.json.success === false || item.json.error) {
        throw new Error(item.json.error?.message || item.json.message || "Plugin call failed")
      }
      return item.json.data || {}
    }
    if (["failed", "canceled", "timeout"].includes(run.status)) {
      throw new Error(run.error?.message || run.message || run.status)
    }
  }
  throw new Error("Plugin call timed out")
}

export default function GameAgentMinecraftQuickstart(props: PluginSurfaceProps) {
  const copy = COPY[resolveLocale(props.locale)]
  const status = copy.status

  const [state, setState] = useState<StatusState>({
    loading: false,
    connected: null,
    wsUrl: "",
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
      const data = await callPlugin("game_agent_status")
      if (unmountedRef.current) return
      setState({
        loading: false,
        connected: Boolean(data.connected),
        wsUrl: String(data.ws_url || ""),
        pendingTask: String(data.pending_task || ""),
        error: "",
      })
    } catch (exc: any) {
      if (unmountedRef.current) return
      setState((prev) => ({
        ...prev,
        loading: false,
        connected: false,
        error: String(exc?.message || exc),
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

  const statusItems = [
    { key: "ws", label: status.wsLabel, value: state.wsUrl || "—" },
    {
      key: "task",
      label: status.taskLabel,
      value: state.pendingTask || status.taskIdle,
    },
  ]

  return (
    <Page title={copy.title} subtitle={copy.subtitle}>
      <Card title={status.title}>
        <Stack>
          <StatusBadge tone={tone}>{badgeText}</StatusBadge>
          {state.error ? (
            <Alert tone="warning">{status.errorPrefix + state.error}</Alert>
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

      {state.connected !== true ? (
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
      ) : null}

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
