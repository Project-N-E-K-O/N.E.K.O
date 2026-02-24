import { defineConfig } from 'vitepress'

/* ------------------------------------------------------------------ */
/*  Shared sidebar definitions (reused across locales)                */
/* ------------------------------------------------------------------ */

function guideSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Getting Started',
      intro: 'Introduction', prereq: 'Prerequisites', dev: 'Development Setup',
      quick: 'Quick Start', struct: 'Project Structure',
    },
    'zh-CN': {
      group: '快速上手',
      intro: '简介', prereq: '前置条件', dev: '开发环境搭建',
      quick: '快速开始', struct: '项目结构',
    },
    ja: {
      group: 'はじめに',
      intro: 'はじめに', prereq: '前提条件', dev: '開発環境の構築',
      quick: 'クイックスタート', struct: 'プロジェクト構造',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.intro, link: `${p}/guide/` },
        { text: t.prereq, link: `${p}/guide/prerequisites` },
        { text: t.dev, link: `${p}/guide/dev-setup` },
        { text: t.quick, link: `${p}/guide/quick-start` },
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
      tts: 'TTS Pipeline',
    },
    'zh-CN': {
      group: '架构设计',
      overview: '概览', three: '三服务器架构', data: '数据流',
      session: '会话管理', memory: '记忆系统', agent: 'Agent 系统',
      tts: 'TTS 流水线',
    },
    ja: {
      group: 'アーキテクチャ',
      overview: '概要', three: '3サーバー設計', data: 'データフロー',
      session: 'セッション管理', memory: 'メモリシステム', agent: 'エージェントシステム',
      tts: 'TTS パイプライン',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
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
      ],
    },
  ]
}

function apiSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      ref: 'API Reference', overview: 'Overview',
      rest: 'REST Endpoints', config: 'Config', chars: 'Characters',
      live2d: 'Live2D Models', vrm: 'VRM Models', mem: 'Memory',
      agent: 'Agent', workshop: 'Steam Workshop', sys: 'System',
      ws: 'WebSocket', proto: 'Protocol', msg: 'Message Types', audio: 'Audio Streaming',
      internal: 'Internal APIs', memSrv: 'Memory Server', agentSrv: 'Agent Server',
    },
    'zh-CN': {
      ref: 'API 参考', overview: '概览',
      rest: 'REST 接口', config: '配置', chars: '角色',
      live2d: 'Live2D 模型', vrm: 'VRM 模型', mem: '记忆',
      agent: 'Agent', workshop: 'Steam 创意工坊', sys: '系统',
      ws: 'WebSocket', proto: '协议', msg: '消息类型', audio: '音频流',
      internal: '内部 API', memSrv: '记忆服务器', agentSrv: 'Agent 服务器',
    },
    ja: {
      ref: 'API リファレンス', overview: '概要',
      rest: 'REST エンドポイント', config: '設定', chars: 'キャラクター',
      live2d: 'Live2D モデル', vrm: 'VRM モデル', mem: 'メモリ',
      agent: 'エージェント', workshop: 'Steam Workshop', sys: 'システム',
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
        { text: t.live2d, link: `${p}/api/rest/live2d` },
        { text: t.vrm, link: `${p}/api/rest/vrm` },
        { text: t.mem, link: `${p}/api/rest/memory` },
        { text: t.agent, link: `${p}/api/rest/agent` },
        { text: t.workshop, link: `${p}/api/rest/workshop` },
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
      group: 'Plugin Development', overview: 'Overview', quick: 'Quick Start',
      sdk: 'SDK Reference', dec: 'Decorators', ex: 'Examples', adv: 'Advanced Topics',
      best: 'Best Practices',
    },
    'zh-CN': {
      group: '插件开发', overview: '概览', quick: '快速开始',
      sdk: 'SDK 参考', dec: '装饰器', ex: '示例', adv: '进阶话题',
      best: '最佳实践',
    },
    ja: {
      group: 'プラグイン開発', overview: '概要', quick: 'クイックスタート',
      sdk: 'SDK リファレンス', dec: 'デコレーター', ex: 'サンプル', adv: '高度なトピック',
      best: 'ベストプラクティス',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/plugins/` },
        { text: t.quick, link: `${p}/plugins/quick-start` },
        { text: t.sdk, link: `${p}/plugins/sdk-reference` },
        { text: t.dec, link: `${p}/plugins/decorators` },
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
      prio: 'Config Priority',
    },
    'zh-CN': {
      group: '配置', overview: '概览', env: '环境变量',
      files: '配置文件', api: 'API 供应商', model: '模型配置',
      prio: '配置优先级',
    },
    ja: {
      group: '設定', overview: '概要', env: '環境変数',
      files: '設定ファイル', api: 'API プロバイダー', model: 'モデル設定',
      prio: '設定の優先順位',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
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
      ],
    },
  ]
}

function frontendSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Frontend', overview: 'Overview', live2d: 'Live2D Integration',
      vrm: 'VRM Models', i18n: 'Internationalization', pages: 'Pages & Templates',
    },
    'zh-CN': {
      group: '前端', overview: '概览', live2d: 'Live2D 集成',
      vrm: 'VRM 模型', i18n: '国际化', pages: '页面与模板',
    },
    ja: {
      group: 'フロントエンド', overview: '概要', live2d: 'Live2D 統合',
      vrm: 'VRM モデル', i18n: '国際化', pages: 'ページとテンプレート',
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
      manual: 'Manual Setup', win: 'Windows Executable',
    },
    'zh-CN': {
      group: '部署', overview: '概览', docker: 'Docker',
      manual: '手动部署', win: 'Windows 可执行文件',
    },
    ja: {
      group: 'デプロイ', overview: '概要', docker: 'Docker',
      manual: '手動セットアップ', win: 'Windows 実行ファイル',
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
      ],
    },
  ]
}

function contributingSidebar(lang: 'en' | 'zh-CN' | 'ja') {
  const t = {
    en: {
      group: 'Contributing', overview: 'Overview', dev: 'Developer Notes',
      test: 'Testing', code: 'Code Style', road: 'Roadmap',
    },
    'zh-CN': {
      group: '贡献指南', overview: '概览', dev: '开发者须知',
      test: '测试', code: '代码风格', road: '路线图',
    },
    ja: {
      group: 'コントリビュート', overview: '概要', dev: '開発者ノート',
      test: 'テスト', code: 'コードスタイル', road: 'ロードマップ',
    },
  }[lang]
  const p = lang === 'en' ? '' : `/${lang}`
  return [
    {
      text: t.group,
      items: [
        { text: t.overview, link: `${p}/contributing/` },
        { text: t.dev, link: `${p}/contributing/developer-notes` },
        { text: t.test, link: `${p}/contributing/testing` },
        { text: t.code, link: `${p}/contributing/code-style` },
        { text: t.road, link: `${p}/contributing/roadmap` },
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
      deploy: 'Deployment', contrib: 'Contributing',
    },
    'zh-CN': {
      guide: '指南', arch: '架构', api: 'API', plugins: '插件',
      config: '配置', more: '更多', modules: '核心模块', frontend: '前端',
      deploy: '部署', contrib: '贡献',
    },
    ja: {
      guide: 'ガイド', arch: 'アーキテクチャ', api: 'API', plugins: 'プラグイン',
      config: '設定', more: 'その他', modules: 'コアモジュール', frontend: 'フロントエンド',
      deploy: 'デプロイ', contrib: 'コントリビュート',
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
      ],
    },
  ]
}

/* ------------------------------------------------------------------ */
/*  Main config                                                       */
/* ------------------------------------------------------------------ */

export default defineConfig({
  title: 'Project N.E.K.O.',
  description: 'Developer documentation for the AI companion metaverse platform',

  head: [
    ['link', { rel: 'icon', href: '/favicon.ico' }],
  ],

  // Custom domain: project-neko.online → base must be '/'
  // (was '/N.E.K.O/' for github.io subdirectory, but custom domain serves at root)
  base: '/',

  lastUpdated: true,
  cleanUrls: true,

  // Exclude project README translations from the doc build
  srcExclude: ['README_en.md', 'README_ja.md'],

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
          message: '基于 MIT 许可发布。',
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
          message: 'MIT ライセンスの下で公開。',
          copyright: 'Copyright 2025-present Project N.E.K.O. Contributors',
        },
      },
    },
  },

  /* ---- Default (English) theme ---- */
  themeConfig: {
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
      message: 'Released under the MIT License.',
      copyright: 'Copyright 2025-present Project N.E.K.O. Contributors',
    },
  },
})
