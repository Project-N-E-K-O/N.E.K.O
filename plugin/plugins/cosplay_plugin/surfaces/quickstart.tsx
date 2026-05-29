import {
  Alert,
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
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

type LocaleKey = "zh-CN" | "en" | "ja" | "ko" | "ru"

type GuideCopy = {
  title: string
  subtitle: string
  cards: Array<{ title: string; badge: string; body: string }>
  setupTitle: string
  setupSteps: Array<{ title: string; body: string }>
  modesTitle: string
  modes: Array<{ key: string; label: string; value: string }>
  panelsTitle: string
  panels: Array<{ key: string; label: string; value: string }>
  tipsTitle: string
  tips: string[]
  warning: string
}

const COPY: Record<LocaleKey, GuideCopy> = {
  "zh-CN": {
    title: "Galgame 游玩助手快速开始",
    subtitle: "按现在的插件面板完成依赖检查、窗口绑定、OCR / Memory Reader 读取和 Agent 辅助。",
    cards: [
      { title: "先看依赖", badge: "Install", body: "RapidOCR 与 DXcam 默认随程序提供；日文、韩文或其他 OCR 模型缺失时，面板会显示下载入口。" },
      { title: "再绑定画面", badge: "Capture", body: "可以选择 OCR 窗口，也可以用 Textractor / Memory Reader 锁定进程，适配不同游戏引擎。" },
      { title: "最后开 Agent", badge: "Agent", body: "陪伴、选项建议、解释台词、总结场景都在状态面板和 Agent 面板里查看。" },
    ],
    setupTitle: "推荐配置流程",
    setupSteps: [
      { title: "1. 打开插件 UI", body: "进入 cosplay_plugin 详情页的“界面”标签，先查看顶部依赖与运行状态横幅。" },
      { title: "2. 补齐 OCR 资源", body: "RapidOCR 可直接使用内置中文模型；如果切到日文、韩文或英文模型，按横幅提示下载缺失模型。" },
      { title: "3. 打开游戏并刷新目标", body: "让游戏停在有文字的画面，点击刷新窗口；自动匹配失败时手动选择识别窗口。" },
      { title: "4. 校准截图区域", body: "在 OCR 窗口区域应用推荐校准或自动重新校准，对话框、字幕区和宽屏布局都可以单独保存 profile。" },
      { title: "5. 必要时启用 Memory Reader", body: "截图黑屏或 OCR 很差时，安装 Textractor，选择候选进程并锁定 Memory Reader 目标。" },
      { title: "6. 选择工作模式", body: "silent 只记录，companion 会让 Agent 陪读，choice_advisor 会更关注选项建议；自动推进需要单独确认安全策略。" },
      { title: "7. 观察状态面板", body: "插件状态页会集中显示运行状态、OCR 运行时、快照、最近台词、最近选项、事件和 Agent 响应。" },
      { title: "8. 用入口做深度操作", body: "插件入口可解释当前台词、总结场景、建议选项、训练或验证 OCR 屏幕感知模型。" },
    ],
    modesTitle: "模式怎么选",
    modes: [
      { key: "silent", label: "Silent", value: "只识别和记录，不主动打扰。" },
      { key: "companion", label: "Companion", value: "识别新台词后让 Agent 生成陪伴回应。" },
      { key: "choice", label: "Choice advisor", value: "出现选项时给出排序和理由。" },
    ],
    panelsTitle: "当前面板对应关系",
    panels: [
      { key: "install", label: "安装与依赖", value: "RapidOCR 模型、Textractor 的检测和安装任务。" },
      { key: "capture", label: "OCR 与窗口", value: "截图后端、窗口选择、截图校准、屏幕感知和视觉辅助。" },
      { key: "memory", label: "Memory Reader", value: "Textractor 候选进程、手动锁定、引擎 hook 兜底。" },
      { key: "status", label: "插件状态", value: "运行状态、快照、历史台词、选项、事件和 Agent 推送记录。" },
    ],
    tipsTitle: "排查提示",
    tips: [
      "窗口列表为空时，确认游戏没有最小化，并先停在有文字的画面。",
      "DXcam 黑屏时切到 MSS / PyAutoGUI / PrintWindow，或改用 Memory Reader。",
      "识别语言不对时，先改 RapidOCR lang_type / OCR languages，再按提示下载对应模型。",
      "Agent 不回应时，检查插件是否运行、模式是否为 companion / choice_advisor，以及目标 AI 是否已配置。",
    ],
    warning: "本教程只说明路径，不会自动安装依赖、切换模式或推进游戏。",
  },
  en: {
    title: "Galgame Play Assistant Quickstart",
    subtitle: "Set up dependency checks, target binding, OCR / Memory Reader input, and Agent assistance from the current panel.",
    cards: [
      { title: "Check dependencies", badge: "Install", body: "RapidOCR and DXcam are bundled by default. The panel offers downloads when extra OCR language models are missing." },
      { title: "Bind the game", badge: "Capture", body: "Pick an OCR window, or lock a Textractor / Memory Reader process for engines where screenshot OCR is unreliable." },
      { title: "Use the Agent", badge: "Agent", body: "Companion replies, choice advice, line explanations, and scene summaries are surfaced in the status and Agent panels." },
    ],
    setupTitle: "Recommended Flow",
    setupSteps: [
      { title: "1. Open the plugin UI", body: "Open the cosplay_plugin detail page and switch to the UI tab, then check the dependency and runtime banners at the top." },
      { title: "2. Prepare OCR resources", body: "RapidOCR can use the bundled Chinese model immediately. For Japanese, Korean, or English models, follow the banner to download missing files." },
      { title: "3. Launch the game and refresh targets", body: "Keep the game on a text screen, refresh windows, and manually select the OCR target if auto matching misses it." },
      { title: "4. Calibrate capture", body: "Apply the recommended crop or run auto recalibration. Dialogue boxes, subtitle areas, and wide layouts can each keep their own profile." },
      { title: "5. Enable Memory Reader when needed", body: "If screenshots are black or OCR quality is poor, install Textractor, choose a candidate process, and lock the Memory Reader target." },
      { title: "6. Choose a mode", body: "silent records only, companion replies to new lines, and choice_advisor focuses on visible choices. Auto advance requires separate safety confirmation." },
      { title: "7. Watch the status panel", body: "The status page combines runtime state, OCR runtime, snapshots, recent lines, choices, events, and Agent replies." },
      { title: "8. Use entries for deeper actions", body: "Plugin entries can explain the current line, summarize scenes, suggest choices, and train or validate OCR screen awareness models." },
    ],
    modesTitle: "Mode Guide",
    modes: [
      { key: "silent", label: "Silent", value: "Recognize and record without proactive replies." },
      { key: "companion", label: "Companion", value: "Generate Agent companion replies for new lines." },
      { key: "choice", label: "Choice advisor", value: "Rank visible choices and explain the reasons." },
    ],
    panelsTitle: "Panel Map",
    panels: [
      { key: "install", label: "Install & Dependencies", value: "RapidOCR models, Textractor checks, and install tasks." },
      { key: "capture", label: "OCR & Window", value: "Capture backend, window selection, calibration, screen awareness, and vision assist." },
      { key: "memory", label: "Memory Reader", value: "Textractor candidates, manual process lock, and engine hook fallback." },
      { key: "status", label: "Plugin Status", value: "Runtime state, snapshots, line history, choices, events, and Agent pushes." },
    ],
    tipsTitle: "Troubleshooting",
    tips: [
      "If the window list is empty, make sure the game is not minimized and is showing text.",
      "If DXcam captures a black frame, switch to MSS / PyAutoGUI / PrintWindow or use Memory Reader.",
      "If OCR uses the wrong language, update RapidOCR lang_type / OCR languages first, then download the prompted model.",
      "If the Agent does not reply, check that the plugin is running, the mode is companion / choice_advisor, and the target AI is configured.",
    ],
    warning: "This guide only explains the path. It will not install dependencies, switch modes, or advance the game automatically.",
  },
  ja: {
    title: "Galgame プレイアシスタント クイックスタート",
    subtitle: "現在のパネルで依存関係、対象ウィンドウ、OCR / Memory Reader、Agent 補助を設定します。",
    cards: [
      { title: "依存関係を確認", badge: "Install", body: "RapidOCR と DXcam は標準同梱です。追加 OCR 言語モデルが必要な場合はパネルにダウンロード入口が出ます。" },
      { title: "ゲームをバインド", badge: "Capture", body: "OCR ウィンドウを選択するか、Textractor / Memory Reader のプロセスを固定します。" },
      { title: "Agent を使う", badge: "Agent", body: "陪読、選択肢提案、台詞解説、シーン要約は状態パネルと Agent パネルで確認できます。" },
    ],
    setupTitle: "推奨設定手順",
    setupSteps: [
      { title: "1. プラグイン UI を開く", body: "cosplay_plugin 詳細ページの「UI」タブを開き、上部の依存関係と実行状態を確認します。" },
      { title: "2. OCR リソースを準備", body: "内蔵中国語モデルはそのまま使えます。日本語、韓国語、英語モデルはバナーの案内から不足分を取得します。" },
      { title: "3. ゲームを起動して更新", body: "文字が表示された画面でウィンドウを更新し、自動選択されない場合は手動で対象を選びます。" },
      { title: "4. キャプチャ範囲を調整", body: "推奨校正または自動再校正を使い、ダイアログ領域やワイド画面ごとに profile を保存します。" },
      { title: "5. 必要なら Memory Reader", body: "黒画面や OCR 品質が悪い場合は Textractor を導入し、候補プロセスを固定します。" },
      { title: "6. モードを選ぶ", body: "silent は記録のみ、companion は陪読返信、choice_advisor は選択肢提案を重視します。" },
      { title: "7. 状態パネルを見る", body: "実行状態、OCR、スナップショット、最近の台詞、選択肢、イベント、Agent 応答をまとめて確認できます。" },
      { title: "8. エントリを使う", body: "現在の台詞解説、シーン要約、選択肢提案、OCR 画面認識モデルの訓練と検証ができます。" },
    ],
    modesTitle: "モード選択",
    modes: [
      { key: "silent", label: "Silent", value: "認識と記録のみ行います。" },
      { key: "companion", label: "Companion", value: "新しい台詞に Agent が陪読返信します。" },
      { key: "choice", label: "Choice advisor", value: "表示された選択肢を順位付けします。" },
    ],
    panelsTitle: "パネル対応",
    panels: [
      { key: "install", label: "インストールと依存", value: "RapidOCR モデル、Textractor の確認とインストール。" },
      { key: "capture", label: "OCR とウィンドウ", value: "キャプチャ backend、ウィンドウ選択、校正、画面認識、Vision 補助。" },
      { key: "memory", label: "Memory Reader", value: "Textractor 候補、手動固定、エンジン hook のフォールバック。" },
      { key: "status", label: "プラグイン状態", value: "実行状態、履歴、選択肢、イベント、Agent 通知。" },
    ],
    tipsTitle: "トラブルシュート",
    tips: [
      "ウィンドウ一覧が空なら、ゲームを最小化せず文字画面で待機してください。",
      "DXcam が黒画面なら MSS / PyAutoGUI / PrintWindow、または Memory Reader を試してください。",
      "認識言語が違う場合は RapidOCR lang_type / OCR languages を変更し、必要なモデルを取得します。",
      "Agent が返信しない場合は、プラグインの起動、モード、対象 AI 設定を確認します。",
    ],
    warning: "このガイドは操作説明のみです。依存関係のインストール、モード変更、ゲーム進行は自動実行しません。",
  },
  ko: {
    title: "Galgame 플레이 어시스턴트 빠른 시작",
    subtitle: "현재 패널에서 의존성, 대상 창, OCR / Memory Reader, Agent 보조 기능을 설정합니다.",
    cards: [
      { title: "의존성 확인", badge: "Install", body: "RapidOCR와 DXcam은 기본 포함입니다. 추가 OCR 언어 모델이 없으면 패널에 다운로드 안내가 표시됩니다." },
      { title: "게임 연결", badge: "Capture", body: "OCR 창을 선택하거나 Textractor / Memory Reader 프로세스를 고정해 엔진별 문제를 우회합니다." },
      { title: "Agent 사용", badge: "Agent", body: "동행 응답, 선택지 조언, 대사 설명, 장면 요약을 상태 및 Agent 패널에서 확인합니다." },
    ],
    setupTitle: "권장 설정 흐름",
    setupSteps: [
      { title: "1. 플러그인 UI 열기", body: "cosplay_plugin 상세 페이지의 UI 탭에서 상단 의존성 및 실행 상태 배너를 확인합니다." },
      { title: "2. OCR 리소스 준비", body: "내장 중국어 모델은 바로 사용할 수 있습니다. 일본어, 한국어, 영어 모델은 배너 안내에 따라 다운로드합니다." },
      { title: "3. 게임 실행 후 대상 새로고침", body: "텍스트가 보이는 화면에서 창 목록을 새로고침하고 자동 선택이 실패하면 직접 선택합니다." },
      { title: "4. 캡처 영역 보정", body: "추천 보정이나 자동 재보정을 적용해 대화창, 자막 영역, 와이드 레이아웃별 profile을 저장합니다." },
      { title: "5. 필요하면 Memory Reader 사용", body: "검은 화면이나 낮은 OCR 품질이 발생하면 Textractor를 설치하고 후보 프로세스를 고정합니다." },
      { title: "6. 모드 선택", body: "silent는 기록만, companion은 동행 응답, choice_advisor는 선택지 조언에 집중합니다." },
      { title: "7. 상태 패널 확인", body: "실행 상태, OCR 런타임, 스냅샷, 최근 대사, 선택지, 이벤트, Agent 응답을 한곳에서 봅니다." },
      { title: "8. 엔트리 활용", body: "현재 대사 설명, 장면 요약, 선택지 추천, OCR 화면 인식 모델 학습과 검증을 실행할 수 있습니다." },
    ],
    modesTitle: "모드 선택",
    modes: [
      { key: "silent", label: "Silent", value: "인식과 기록만 수행합니다." },
      { key: "companion", label: "Companion", value: "새 대사에 Agent 동행 응답을 생성합니다." },
      { key: "choice", label: "Choice advisor", value: "보이는 선택지를 순위화하고 이유를 설명합니다." },
    ],
    panelsTitle: "패널 안내",
    panels: [
      { key: "install", label: "설치와 의존성", value: "RapidOCR 모델, Textractor 검사와 설치 작업." },
      { key: "capture", label: "OCR과 창", value: "캡처 backend, 창 선택, 보정, 화면 인식, Vision 보조." },
      { key: "memory", label: "Memory Reader", value: "Textractor 후보, 수동 프로세스 고정, 엔진 hook 대체 경로." },
      { key: "status", label: "플러그인 상태", value: "실행 상태, 기록, 선택지, 이벤트, Agent 푸시." },
    ],
    tipsTitle: "문제 해결",
    tips: [
      "창 목록이 비어 있으면 게임이 최소화되지 않았고 텍스트 화면인지 확인하세요.",
      "DXcam이 검은 화면이면 MSS / PyAutoGUI / PrintWindow 또는 Memory Reader를 사용하세요.",
      "인식 언어가 다르면 RapidOCR lang_type / OCR languages를 조정하고 필요한 모델을 다운로드하세요.",
      "Agent가 응답하지 않으면 플러그인 실행 상태, 모드, 대상 AI 설정을 확인하세요.",
    ],
    warning: "이 가이드는 경로만 설명합니다. 의존성 설치, 모드 변경, 게임 진행을 자동 실행하지 않습니다.",
  },
  ru: {
    title: "Быстрый старт Galgame Play Assistant",
    subtitle: "Настройте зависимости, привязку окна, OCR / Memory Reader и помощь Agent в текущей панели.",
    cards: [
      { title: "Проверьте зависимости", badge: "Install", body: "RapidOCR и DXcam поставляются по умолчанию. Если не хватает OCR-модели языка, панель покажет загрузку." },
      { title: "Привяжите игру", badge: "Capture", body: "Выберите OCR-окно или закрепите процесс Textractor / Memory Reader для игр, где скриншотный OCR работает плохо." },
      { title: "Используйте Agent", badge: "Agent", body: "Ответы компаньона, советы по выборам, объяснения строк и сводки сцен доступны в панелях статуса и Agent." },
    ],
    setupTitle: "Рекомендуемый порядок",
    setupSteps: [
      { title: "1. Откройте UI плагина", body: "На странице cosplay_plugin откройте вкладку UI и проверьте верхние баннеры зависимостей и состояния." },
      { title: "2. Подготовьте OCR", body: "Встроенную китайскую модель RapidOCR можно использовать сразу. Для японского, корейского или английского следуйте подсказке загрузки." },
      { title: "3. Запустите игру и обновите цели", body: "Оставьте игру на экране с текстом, обновите окна и вручную выберите цель, если автоматика ошиблась." },
      { title: "4. Откалибруйте захват", body: "Примените рекомендуемую обрезку или автокалибровку; профили можно хранить для разных областей диалога." },
      { title: "5. Включите Memory Reader при необходимости", body: "Если кадр черный или OCR плохой, установите Textractor и закрепите подходящий процесс." },
      { title: "6. Выберите режим", body: "silent только пишет историю, companion отвечает на новые строки, choice_advisor фокусируется на вариантах выбора." },
      { title: "7. Следите за статусом", body: "Страница статуса объединяет runtime, OCR, снимки, последние строки, варианты, события и ответы Agent." },
      { title: "8. Используйте entry-действия", body: "Можно объяснять текущую строку, суммировать сцену, советовать выборы и обучать или проверять OCR screen awareness." },
    ],
    modesTitle: "Режимы",
    modes: [
      { key: "silent", label: "Silent", value: "Только распознавание и запись истории." },
      { key: "companion", label: "Companion", value: "Agent отвечает на новые строки как компаньон." },
      { key: "choice", label: "Choice advisor", value: "Ранжирует видимые варианты и объясняет причины." },
    ],
    panelsTitle: "Карта панели",
    panels: [
      { key: "install", label: "Установка и зависимости", value: "Модели RapidOCR, Textractor и задачи установки." },
      { key: "capture", label: "OCR и окно", value: "Backend захвата, выбор окна, калибровка, screen awareness и Vision." },
      { key: "memory", label: "Memory Reader", value: "Кандидаты Textractor, ручная привязка процесса и engine hooks." },
      { key: "status", label: "Статус плагина", value: "Runtime, история строк, варианты, события и push-записи Agent." },
    ],
    tipsTitle: "Диагностика",
    tips: [
      "Если список окон пуст, убедитесь, что игра не свернута и показывает текст.",
      "Если DXcam дает черный кадр, переключитесь на MSS / PyAutoGUI / PrintWindow или Memory Reader.",
      "Если язык OCR неверный, настройте RapidOCR lang_type / OCR languages и загрузите нужную модель.",
      "Если Agent не отвечает, проверьте запуск плагина, режим companion / choice_advisor и настройку целевого AI.",
    ],
    warning: "Это только руководство. Оно не устанавливает зависимости, не меняет режим и не продвигает игру автоматически.",
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

export default function GalgameQuickstartGuide(props: PluginSurfaceProps) {
  const copy = COPY[resolveLocale(props.locale)]

  return (
    <Page title={copy.title} subtitle={copy.subtitle}>
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

      <Grid cols={2}>
        <Card title={copy.modesTitle}>
          <KeyValue items={copy.modes} />
        </Card>

        <Card title={copy.panelsTitle}>
          <KeyValue items={copy.panels} />
        </Card>
      </Grid>

      <Alert tone="info">
        {copy.tipsTitle}
      </Alert>
      <Stack>
        {copy.tips.map((tip) => (
          <Tip key={tip}>{tip}</Tip>
        ))}
      </Stack>

      <Warning>{copy.warning}</Warning>
    </Page>
  )
}
