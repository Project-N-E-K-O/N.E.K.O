import { defineConfig } from 'vitepress'
import { readdirSync } from 'node:fs'
import { dirname, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const DOCS_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const SRC_EXCLUDE = new Set([
  'README_en.md',
  'README_ja.md',
  'README_ru.md',
  'zh-CN/guide/openclaw_guide.md',
  'zh-CN/guide/openclaw_guide.en.md',
  'zh-CN/guide/openclaw_guide.ja.md',
  'zh-CN/guide/openclaw_guide.ko.md',
  'zh-CN/guide/openclaw_guide.ru.md',
  'zh-CN/guide/openclaw_guide.zh-TW.md',
])
const SOURCE_DIR_EXCLUDE = new Set(['.vitepress', 'node_modules', 'public'])

function collectPageRoutes(directory = DOCS_ROOT): string[] {
  const routes: string[] = []

  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.isDirectory() && SOURCE_DIR_EXCLUDE.has(entry.name)) continue

    const absolutePath = resolve(directory, entry.name)
    if (entry.isDirectory()) {
      routes.push(...collectPageRoutes(absolutePath))
      continue
    }
    if (!entry.isFile() || !entry.name.endsWith('.md')) continue

    const sourcePath = relative(DOCS_ROOT, absolutePath).replaceAll('\\', '/')
    if (SRC_EXCLUDE.has(sourcePath)) continue

    const route = `/${sourcePath}`
      .replace(/(^|\/)index\.md$/, '$1')
      .replace(/\.md$/, '')
    routes.push(route)
  }

  return routes.sort()
}

const availablePageRoutes = collectPageRoutes()

/* ------------------------------------------------------------------ */
/*  Shared sidebar definitions (reused across locales)                */
/* ------------------------------------------------------------------ */

function guideSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Getting Started',
      intro: 'Introduction', prereq: 'Prerequisites', dev: 'Development Setup',
      quick: 'Quick Start', struct: 'Project Structure', linux: 'Linux Desktop Runtime',
    },
    'zh-CN': {
      group: '快速上手',
      intro: '简介', prereq: '前置条件', dev: '开发环境搭建',
      quick: '快速开始', struct: '项目结构', linux: 'Linux 桌面运行时',
    },
    ja: {
      group: 'はじめに',
      intro: 'はじめに', prereq: '前提条件', dev: '開発環境の構築',
      quick: 'クイックスタート', struct: 'プロジェクト構造', linux: 'Linux デスクトップランタイム',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  const linuxDesktopItems = [{ text: t.linux, link: `${p}/guide/linux-desktop-runtime` }]
  return [
    {
      text: t.group,
      items: [
        { text: t.intro, link: `${p}/guide/` },
        { text: t.prereq, link: `${p}/guide/prerequisites` },
        { text: t.dev, link: `${p}/guide/dev-setup` },
        { text: t.quick, link: `${p}/guide/quick-start` },
        ...linuxDesktopItems,
        { text: t.struct, link: `${p}/guide/project-structure` },
      ],
    },
  ]
}

function architectureSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Architecture',
      overview: 'Overview', three: 'Three-Server Design', data: 'Data Flow',
      session: 'Session Management', memory: 'Memory System', agent: 'Agent System',
      tts: 'TTS Pipeline', taskHud: 'Task HUD System',
    },
    'zh-CN': {
      group: '架构设计',
      overview: '概览', three: '三服务器架构', data: '数据流',
      session: '会话管理', memory: '记忆系统', agent: 'Agent 系统',
      tts: 'TTS 流水线', taskHud: '任务 HUD 系统',
    },
    ja: {
      group: 'アーキテクチャ',
      overview: '概要', three: '3サーバー設計', data: 'データフロー',
      session: 'セッション管理', memory: 'メモリシステム', agent: 'エージェントシステム',
      tts: 'TTS パイプライン', taskHud: 'タスク HUD システム',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  const implementationRecords = lang === 'en'
    ? [{ text: t.taskHud, link: '/architecture/task-hud-system' }]
    : []
  const zhCNOnlyItems = lang === 'zh-CN'
    ? [{ text: 'Neko x QwenPaw 接入规范', link: `${p}/architecture/neko-qwenpaw-integration` }]
    : []
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/architecture/` },
        { text: t.three, link: `${p}/architecture/three-servers` },
        { text: t.data, link: `${p}/architecture/data-flow` },
        { text: t.session, link: `${p}/architecture/session-management` },
        { text: t.memory, link: `${p}/architecture/memory-system` },
        { text: t.agent, link: `${p}/architecture/agent-system` },
        { text: t.tts, link: `${p}/architecture/tts-pipeline` },
        ...implementationRecords,
        ...zhCNOnlyItems,
      ],
    },
  ]
}

function apiSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      ref: 'API Reference', overview: 'Overview',
      rest: 'REST Endpoints', config: 'Config', chars: 'Characters', pages: 'Web Pages',
      live2d: 'Live2D Models', vrm: 'VRM Models', mmd: 'MMD Models', pngtuber: 'PNGTuber Models', mem: 'Memory',
      agent: 'Agent', workshop: 'Steam Workshop', cloudsave: 'Cloud Save', tools: 'Runtime Tools', capture: 'Capture Bridge', sys: 'System',
      music: 'Music', jukebox: 'Jukebox', game: 'Minigames', galgame: 'GalGame', icebreaker: 'Icebreaker', proactive: 'Proactive Chat',
      ws: 'WebSocket', proto: 'Protocol', msg: 'Message Types', audio: 'Audio Streaming',
      internal: 'Internal APIs', memSrv: 'Memory Server', agentSrv: 'Agent Server',
    },
    'zh-CN': {
      ref: 'API 参考', overview: '概览',
      rest: 'REST 接口', config: '配置', chars: '角色', pages: 'Web 页面',
      live2d: 'Live2D 模型', vrm: 'VRM 模型', mmd: 'MMD 模型', pngtuber: 'PNGTuber 模型', mem: '记忆',
      agent: 'Agent', workshop: 'Steam 创意工坊', cloudsave: '云存档', tools: '运行时工具', capture: '截图桥', sys: '系统',
      music: '音乐', jukebox: '点歌台', game: '小游戏', galgame: 'GalGame', icebreaker: '破冰', proactive: '主动搭话',
      ws: 'WebSocket', proto: '协议', msg: '消息类型', audio: '音频流',
      internal: '内部 API', memSrv: '记忆服务器', agentSrv: 'Agent 服务器',
    },
    ja: {
      ref: 'API リファレンス', overview: '概要',
      rest: 'REST エンドポイント', config: '設定', chars: 'キャラクター', pages: 'Web ページ',
      live2d: 'Live2D モデル', vrm: 'VRM モデル', mmd: 'MMD モデル', pngtuber: 'PNGTuber モデル', mem: 'メモリ',
      agent: 'エージェント', workshop: 'Steam Workshop', cloudsave: 'クラウドセーブ', tools: 'ランタイムツール', capture: 'キャプチャブリッジ', sys: 'システム',
      music: '音楽', jukebox: 'ジュークボックス', game: 'ミニゲーム', galgame: 'ギャルゲー', icebreaker: 'アイスブレイク', proactive: 'プロアクティブチャット',
      ws: 'WebSocket', proto: 'プロトコル', msg: 'メッセージ型', audio: 'オーディオストリーミング',
      internal: '内部 API', memSrv: 'メモリサーバー', agentSrv: 'エージェントサーバー',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.ref,
      items: [{ text: t.overview, link: `${p}/api/` }],
    },
    {
      text: t.rest,
      collapsed: false,
      items: [
        { text: t.config, link: `${p}/api/rest/config` },
        { text: t.chars, link: `${p}/api/rest/characters` },
        { text: t.pages, link: `${p}/api/rest/pages` },
        { text: t.live2d, link: `${p}/api/rest/live2d` },
        { text: t.vrm, link: `${p}/api/rest/vrm` },
        { text: t.mmd, link: `${p}/api/rest/mmd` },
        { text: t.pngtuber, link: `${p}/api/rest/pngtuber` },
        { text: t.mem, link: `${p}/api/rest/memory` },
        { text: t.agent, link: `${p}/api/rest/agent` },
        { text: t.workshop, link: `${p}/api/rest/workshop` },
        { text: t.cloudsave, link: `${p}/api/rest/cloudsave` },
        { text: t.tools, link: `${p}/api/rest/tools` },
        { text: t.capture, link: `${p}/api/rest/capture` },
        { text: t.music, link: `${p}/api/rest/music` },
        { text: t.jukebox, link: `${p}/api/rest/jukebox` },
        { text: t.game, link: `${p}/api/rest/game` },
        { text: t.galgame, link: `${p}/api/rest/galgame` },
        { text: t.icebreaker, link: `${p}/api/rest/icebreaker` },
        { text: t.proactive, link: `${p}/api/rest/proactive` },
        { text: t.sys, link: `${p}/api/rest/system` },
      ],
    },
    {
      text: t.ws,
      collapsed: false,
      items: [
        { text: t.proto, link: `${p}/api/websocket/protocol` },
        { text: t.msg, link: `${p}/api/websocket/message-types` },
        { text: t.audio, link: `${p}/api/websocket/audio-streaming` },
      ],
    },
    {
      text: t.internal,
      collapsed: true,
      items: [
        { text: t.memSrv, link: `${p}/api/memory-server` },
        { text: t.agentSrv, link: `${p}/api/agent-server` },
      ],
    },
  ]
}

function modulesSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Core Modules', overview: 'Overview', core: 'LLMSessionManager',
      rt: 'Realtime Client', off: 'Offline Client', tts: 'TTS Client', cfg: 'Config Manager',
    },
    'zh-CN': {
      group: '核心模块', overview: '概览', core: 'LLMSessionManager',
      rt: '实时客户端', off: '离线客户端', tts: 'TTS 客户端', cfg: '配置管理器',
    },
    ja: {
      group: 'コアモジュール', overview: '概要', core: 'LLMSessionManager',
      rt: 'リアルタイムクライアント', off: 'オフラインクライアント', tts: 'TTS クライアント', cfg: '設定マネージャー',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/modules/` },
        { text: t.core, link: `${p}/modules/core` },
        { text: t.rt, link: `${p}/modules/omni-realtime` },
        { text: t.off, link: `${p}/modules/omni-offline` },
        { text: t.tts, link: `${p}/modules/tts-client` },
        { text: t.cfg, link: `${p}/modules/config-manager` },
      ],
    },
  ]
}

function pluginsSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Plugin Development', overview: 'Overview',
      journey: 'Getting Started', quick: 'Quick Start', base: 'Plugin Capabilities',
      toml: 'Plugin Config (plugin.toml)',
      entries: 'Entries & Parameters', router: 'Router (Code Splitting)', lifecycleCfg: 'Lifecycle',
      sdk: 'SDK Reference', migration: 'v0.9 Migration', dec: 'Decorators', ex: 'Examples', adv: 'Advanced Topics',
      hosted: 'Hosted UI', tool: 'LLM Tool Calling', claw: 'Agent Automation & QwenPaw', best: 'Best Practices',
      upgrade: 'Rollback-safe Local Upgrades',
    },
    'zh-CN': {
      group: '插件开发', overview: '概览',
      journey: '旅程的起点', quick: '快速开始', base: '插件能力',
      toml: '插件配置 (plugin.toml)',
      entries: '入口与参数', router: 'Router（代码拆分）', lifecycleCfg: '生命周期',
      sdk: 'SDK 参考', migration: 'v0.9 迁移', dec: '装饰器', ex: '示例', adv: '进阶话题',
      hosted: 'Hosted UI', tool: 'LLM Tool Calling', claw: 'Agent 自动化与 QwenPaw', best: '最佳实践',
      upgrade: '可回滚的本地插件升级',
    },
    ja: {
      group: 'プラグイン開発', overview: '概要',
      journey: 'はじめの一歩', quick: 'クイックスタート', base: 'プラグイン機能',
      toml: 'プラグイン設定 (plugin.toml)',
      entries: 'エントリーとパラメータ', router: 'Router（コード分割）', lifecycleCfg: 'ライフサイクル',
      sdk: 'SDK リファレンス', migration: 'v0.9 移行', dec: 'デコレーター', ex: 'サンプル', adv: '高度なトピック',
      hosted: 'Hosted UI', tool: 'LLM ツール呼び出し', claw: 'Agent Automation & QwenPaw', best: 'ベストプラクティス',
      upgrade: 'ロールバック可能なローカル更新',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/plugins/` },
        {
          text: t.journey,
          collapsed: false,
          items: [
            { text: t.quick, link: `${p}/plugins/quick-start` },
            { text: t.toml, link: `${p}/plugins/plugin-toml` },
            { text: t.entries, link: `${p}/plugins/entries` },
            { text: t.router, link: `${p}/plugins/router` },
            { text: t.lifecycleCfg, link: `${p}/plugins/lifecycle-config` },
            { text: t.base, link: `${p}/plugins/plugin-base` },
          ],
        },
        { text: t.migration, link: `${p}/plugins/migration-v0.9` },
        { text: t.upgrade, link: `${p}/plugins/safe-local-upgrades` },
        { text: t.sdk, link: `${p}/plugins/sdk-reference` },
        { text: t.dec, link: `${p}/plugins/decorators` },
        { text: t.tool, link: `${p}/plugins/tool-calling` },
        ...(lang === 'ja' ? [] : [{ text: t.claw, link: `${p}/plugins/use-claw` }]),
        ...(lang === 'ja' ? [] : [{ text: t.hosted, link: `${p}/plugins/hosted-ui` }]),
        { text: t.ex, link: `${p}/plugins/examples` },
        { text: t.adv, link: `${p}/plugins/advanced` },
        { text: t.best, link: `${p}/plugins/best-practices` },
      ],
    },
  ]
}

function configSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Configuration', overview: 'Overview', env: 'Environment Variables',
      files: 'Config Files', api: 'API Providers', model: 'Model Configuration',
      prio: 'Config Priority', fields: 'Provider Field Reference',
    },
    'zh-CN': {
      group: '配置', overview: '概览', env: '环境变量',
      files: '配置文件', api: 'API 供应商', model: '模型配置',
      prio: '配置优先级', fields: 'Provider 字段参考',
    },
    ja: {
      group: '設定', overview: '概要', env: '環境変数',
      files: '設定ファイル', api: 'API プロバイダー', model: 'モデル設定',
      prio: '設定の優先順位', fields: 'Provider フィールドリファレンス',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  const fieldReference = lang === 'en'
    ? [{ text: t.fields, link: '/api_providers_fields' }]
    : []
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/config/` },
        { text: t.env, link: `${p}/config/environment-vars` },
        { text: t.files, link: `${p}/config/config-files` },
        { text: t.api, link: `${p}/config/api-providers` },
        { text: t.model, link: `${p}/config/model-config` },
        { text: t.prio, link: `${p}/config/config-priority` },
        ...fieldReference,
      ],
    },
  ]
}

function frontendSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Frontend', overview: 'Overview', live2d: 'Live2D Integration',
      vrm: 'VRM Models', mmd: 'MMD Models', pngtuber: 'PNGTuber Models',
      i18n: 'Internationalization', pages: 'Pages & Templates',
    },
    'zh-CN': {
      group: '前端', overview: '概览', live2d: 'Live2D 集成',
      vrm: 'VRM 模型', mmd: 'MMD 模型', pngtuber: 'PNGTuber 模型',
      i18n: '国际化', pages: '页面与模板',
    },
    ja: {
      group: 'フロントエンド', overview: '概要', live2d: 'Live2D 統合',
      vrm: 'VRM モデル', mmd: 'MMD モデル', pngtuber: 'PNGTuber モデル',
      i18n: '国際化', pages: 'ページとテンプレート',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/frontend/` },
        { text: t.live2d, link: `${p}/frontend/live2d` },
        { text: t.vrm, link: `${p}/frontend/vrm` },
        { text: t.mmd, link: `${p}/frontend/mmd` },
        { text: t.pngtuber, link: `${p}/frontend/pngtuber` },
        { text: t.i18n, link: `${p}/frontend/i18n` },
        { text: t.pages, link: `${p}/frontend/pages` },
      ],
    },
  ]
}

function deploymentSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Deployment', overview: 'Overview', docker: 'Docker',
      manual: 'Manual Setup', win: 'Windows Executable', embeddings: 'Local Embedding Assets',
    },
    'zh-CN': {
      group: '部署', overview: '概览', docker: 'Docker',
      manual: '手动部署', win: 'Windows 可执行文件', embeddings: '本地嵌入模型资源',
    },
    ja: {
      group: 'デプロイ', overview: '概要', docker: 'Docker',
      manual: '手動セットアップ', win: 'Windows 実行ファイル', embeddings: 'ローカル埋め込みアセット',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/deployment/` },
        { text: t.docker, link: `${p}/deployment/docker` },
        { text: t.manual, link: `${p}/deployment/manual` },
        { text: t.win, link: `${p}/deployment/windows-exe` },
        { text: t.embeddings, link: `${p}/deployment/embedding-models` },
      ],
    },
  ]
}

function contributingSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Contributing', overview: 'Overview', dev: 'Developer Notes',
      test: 'Testing', code: 'Code Style', road: 'Roadmap', ai: 'AI-Assisted Dev',
      nuitka: 'Nuitka Packaging', docs: 'Documentation Maintenance', miner: 'Natural-Expression Miner',
    },
    'zh-CN': {
      group: '贡献指南', overview: '概览', dev: '开发者须知',
      test: '测试', code: '代码风格', road: '路线图', ai: 'AI 辅助开发',
      nuitka: 'Nuitka 打包注意事项', docs: '文档维护规范', miner: '自然表达候选挖掘器',
    },
    ja: {
      group: 'コントリビュート', overview: '概要', dev: '開発者ノート',
      test: 'テスト', code: 'コードスタイル', road: 'ロードマップ', ai: 'AI支援開発',
      nuitka: 'Nuitka パッケージング', docs: 'ドキュメント保守', miner: '自然表現候補マイナー',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  const maintainerTools = lang === 'en'
    ? [{ text: t.miner, link: '/contributing/natural-expression-candidate-miner' }]
    : []
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/contributing/` },
        { text: t.dev, link: `${p}/contributing/developer-notes` },
        { text: t.ai, link: `${p}/contributing/ai-assisted-dev` },
        { text: t.test, link: `${p}/contributing/testing` },
        { text: t.code, link: `${p}/contributing/code-style` },
        { text: t.docs, link: `${p}/contributing/documentation` },
        { text: t.nuitka, link: `${p}/contributing/nuitka-packaging` },
        ...maintainerTools,
        { text: t.road, link: `${p}/contributing/roadmap` },
      ],
    },
  ]
}

function recordsSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Project Records', overview: 'Overview', design: 'Design Records',
      benchmarks: 'Benchmarks', changelog: 'Plugin SDK Changes',
    },
    'zh-CN': {
      group: '项目记录', overview: '概览', design: '设计记录',
      benchmarks: '基准记录', changelog: '插件 SDK 变更',
    },
    ja: {
      group: 'プロジェクト記録', overview: '概要', design: '設計記録',
      benchmarks: 'ベンチマーク', changelog: 'Plugin SDK 変更',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/records/` },
        { text: t.design, link: '/design/' },
        { text: t.benchmarks, link: '/benchmarks/' },
        { text: t.changelog, link: '/changelog/' },
      ],
    },
  ]
}

/* ------------------------------------------------------------------ */
/*  Per-locale sidebar builder                                        */
/* ------------------------------------------------------------------ */

function buildSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const p = lang === 'en' ? '' : `/${lang}`
  return {
    [`${p}/guide/`]: guideSidebar(lang),
    [`${p}/architecture/`]: architectureSidebar(lang),
    [`${p}/api/`]: apiSidebar(lang),
    [`${p}/modules/`]: modulesSidebar(lang),
    [`${p}/plugins/`]: pluginsSidebar(lang),
    [`${p}/config/`]: configSidebar(lang),
    [`${p}/frontend/`]: frontendSidebar(lang),
    [`${p}/deployment/`]: deploymentSidebar(lang),
    [`${p}/contributing/`]: contributingSidebar(lang),
    [`${p}/records/`]: recordsSidebar(lang),
  }
}

/* ------------------------------------------------------------------ */
/*  Per-locale nav builder                                            */
/* ------------------------------------------------------------------ */

function buildNav(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      guide: 'Guide', arch: 'Architecture', api: 'API', plugins: 'Plugins',
      config: 'Config', more: 'More', modules: 'Core Modules', frontend: 'Frontend',
      deploy: 'Deployment', contrib: 'Contributing', records: 'Project Records',
    },
    'zh-CN': {
      guide: '指南', arch: '架构', api: 'API', plugins: '插件',
      config: '配置', more: '更多', modules: '核心模块', frontend: '前端',
      deploy: '部署', contrib: '贡献', records: '项目记录',
    },
    ja: {
      guide: 'ガイド', arch: 'アーキテクチャ', api: 'API', plugins: 'プラグイン',
      config: '設定', more: 'その他', modules: 'コアモジュール', frontend: 'フロントエンド',
      deploy: 'デプロイ', contrib: 'コントリビュート', records: 'プロジェクト記録',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    { text: t.guide, link: `${p}/guide/`, activeMatch: `${p}/guide/` },
    { text: t.arch, link: `${p}/architecture/`, activeMatch: `${p}/architecture/` },
    { text: t.api, link: `${p}/api/`, activeMatch: `${p}/api/` },
    { text: t.plugins, link: `${p}/plugins/`, activeMatch: `${p}/plugins/` },
    { text: t.config, link: `${p}/config/`, activeMatch: `${p}/config/` },
    {
      text: t.more,
      items: [
        { text: t.modules, link: `${p}/modules/` },
        { text: t.frontend, link: `${p}/frontend/` },
        { text: t.deploy, link: `${p}/deployment/` },
        { text: t.contrib, link: `${p}/contributing/` },
        { text: t.records, link: `${p}/records/` },
      ],
    },
  ]
}

/* ------------------------------------------------------------------ */
/*  Main config                                                       */
/* ------------------------------------------------------------------ */

export default defineConfig({
  title: 'Project N.E.K.O.',
  description: 'Code-backed developer documentation for Project N.E.K.O.',

  head: [
    ['link', { rel: 'icon', href: '/favicon.ico' }],
  ],

  // Custom domain: project-neko.online → base must be '/'
  // (was '/N.E.K.O/' for github.io subdirectory, but custom domain serves at root)
  base: '/',

  lastUpdated: true,
  cleanUrls: true,

  // Keep this list in sync with SRC_EXCLUDE in
  // scripts/check_docs_no_relative_paths.py.
  srcExclude: [...SRC_EXCLUDE],

  /* ---- i18n ---- */
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
    },
    'zh-CN': {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh-CN/',
      themeConfig: {
        nav: buildNav('zh-CN'),
        sidebar: buildSidebar('zh-CN'),
        editLink: {
          pattern: 'https://github.com/Project-N-E-K-O/N.E.K.O/edit/main/docs/:path',
          text: '在 GitHub 上编辑此页',
        },
        lastUpdated: {
          text: '最后更新于',
        },
        docFooter: {
          prev: '上一页',
          next: '下一页',
        },
        outline: {
          label: '页面导航',
        },
        returnToTopLabel: '回到顶部',
        sidebarMenuLabel: '菜单',
        darkModeSwitchLabel: '深色模式',
        footer: {
          message: '基于 Apache License 2.0 发布。',
          copyright: 'Copyright 2025-present Project N.E.K.O. Contributors',
        },
      },
    },
    ja: {
      label: '日本語',
      lang: 'ja',
      link: '/ja/',
      themeConfig: {
        nav: buildNav('ja'),
        sidebar: buildSidebar('ja'),
        editLink: {
          pattern: 'https://github.com/Project-N-E-K-O/N.E.K.O/edit/main/docs/:path',
          text: 'GitHub でこのページを編集する',
        },
        lastUpdated: {
          text: '最終更新日',
        },
        docFooter: {
          prev: '前のページ',
          next: '次のページ',
        },
        outline: {
          label: 'ページナビ',
        },
        returnToTopLabel: 'トップに戻る',
        sidebarMenuLabel: 'メニュー',
        darkModeSwitchLabel: 'ダークモード',
        footer: {
          message: 'Apache License 2.0 の下で公開。',
          copyright: 'Copyright 2025-present Project N.E.K.O. Contributors',
        },
      },
    },
  },

  /* ---- Default (English) theme ---- */
  themeConfig: {
    // The stock VitePress locale switcher assumes every page has a mirror.
    // Keep its hidden fallback links safe; the custom theme uses this route
    // manifest to preserve corresponding-page switches where a mirror exists.
    i18nRouting: false,
    availablePageRoutes,
    logo: '/logo.jpg',
    siteTitle: 'N.E.K.O. Docs',

    nav: buildNav('en'),
    sidebar: buildSidebar('en'),

    socialLinks: [
      { icon: 'github', link: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
      { icon: 'discord', link: 'https://discord.gg/5kgHfepNJr' },
    ],

    editLink: {
      pattern: 'https://github.com/Project-N-E-K-O/N.E.K.O/edit/main/docs/:path',
      text: 'Edit this page on GitHub',
    },

    search: {
      provider: 'local',
    },

    footer: {
      message: 'Released under the Apache License 2.0.',
      copyright: 'Copyright 2025-present Project N.E.K.O. Contributors',
    },
  },
})
