import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Project N.E.K.O.',
  description: 'Developer documentation for the AI companion metaverse platform',

  head: [
    ['link', { rel: 'icon', href: '/favicon.ico' }],
  ],

  // Deploy to GitHub Pages at https://<org>.github.io/N.E.K.O/
  // Change this if using a custom domain
  base: '/N.E.K.O/',

  lastUpdated: true,
  cleanUrls: true,

  // Exclude project README translations from the doc build
  srcExclude: ['README_en.md', 'README_ja.md'],

  themeConfig: {
    logo: '/logo.jpg',
    siteTitle: 'N.E.K.O. Docs',

    nav: [
      { text: 'Guide', link: '/guide/', activeMatch: '/guide/' },
      { text: 'Architecture', link: '/architecture/', activeMatch: '/architecture/' },
      { text: 'API', link: '/api/', activeMatch: '/api/' },
      { text: 'Plugins', link: '/plugins/', activeMatch: '/plugins/' },
      { text: 'Config', link: '/config/', activeMatch: '/config/' },
      {
        text: 'More',
        items: [
          { text: 'Core Modules', link: '/modules/' },
          { text: 'Frontend', link: '/frontend/' },
          { text: 'Deployment', link: '/deployment/' },
          { text: 'Contributing', link: '/contributing/' },
        ],
      },
    ],

    sidebar: {
      '/guide/': [
        {
          text: 'Getting Started',
          items: [
            { text: 'Introduction', link: '/guide/' },
            { text: 'Prerequisites', link: '/guide/prerequisites' },
            { text: 'Development Setup', link: '/guide/dev-setup' },
            { text: 'Quick Start', link: '/guide/quick-start' },
            { text: 'Project Structure', link: '/guide/project-structure' },
          ],
        },
      ],

      '/architecture/': [
        {
          text: 'Architecture',
          items: [
            { text: 'Overview', link: '/architecture/' },
            { text: 'Three-Server Design', link: '/architecture/three-servers' },
            { text: 'Data Flow', link: '/architecture/data-flow' },
            { text: 'Session Management', link: '/architecture/session-management' },
            { text: 'Memory System', link: '/architecture/memory-system' },
            { text: 'Agent System', link: '/architecture/agent-system' },
            { text: 'TTS Pipeline', link: '/architecture/tts-pipeline' },
          ],
        },
      ],

      '/api/': [
        {
          text: 'API Reference',
          items: [
            { text: 'Overview', link: '/api/' },
          ],
        },
        {
          text: 'REST Endpoints',
          collapsed: false,
          items: [
            { text: 'Config', link: '/api/rest/config' },
            { text: 'Characters', link: '/api/rest/characters' },
            { text: 'Live2D Models', link: '/api/rest/live2d' },
            { text: 'VRM Models', link: '/api/rest/vrm' },
            { text: 'Memory', link: '/api/rest/memory' },
            { text: 'Agent', link: '/api/rest/agent' },
            { text: 'Steam Workshop', link: '/api/rest/workshop' },
            { text: 'System', link: '/api/rest/system' },
          ],
        },
        {
          text: 'WebSocket',
          collapsed: false,
          items: [
            { text: 'Protocol', link: '/api/websocket/protocol' },
            { text: 'Message Types', link: '/api/websocket/message-types' },
            { text: 'Audio Streaming', link: '/api/websocket/audio-streaming' },
          ],
        },
        {
          text: 'Internal APIs',
          collapsed: true,
          items: [
            { text: 'Memory Server', link: '/api/memory-server' },
            { text: 'Agent Server', link: '/api/agent-server' },
          ],
        },
      ],

      '/modules/': [
        {
          text: 'Core Modules',
          items: [
            { text: 'Overview', link: '/modules/' },
            { text: 'LLMSessionManager', link: '/modules/core' },
            { text: 'Realtime Client', link: '/modules/omni-realtime' },
            { text: 'Offline Client', link: '/modules/omni-offline' },
            { text: 'TTS Client', link: '/modules/tts-client' },
            { text: 'Config Manager', link: '/modules/config-manager' },
          ],
        },
      ],

      '/plugins/': [
        {
          text: 'Plugin Development',
          items: [
            { text: 'Overview', link: '/plugins/' },
            { text: 'Quick Start', link: '/plugins/quick-start' },
            { text: 'SDK Reference', link: '/plugins/sdk-reference' },
            { text: 'Decorators', link: '/plugins/decorators' },
            { text: 'Examples', link: '/plugins/examples' },
            { text: 'Advanced Topics', link: '/plugins/advanced' },
            { text: 'Best Practices', link: '/plugins/best-practices' },
          ],
        },
      ],

      '/config/': [
        {
          text: 'Configuration',
          items: [
            { text: 'Overview', link: '/config/' },
            { text: 'Environment Variables', link: '/config/environment-vars' },
            { text: 'Config Files', link: '/config/config-files' },
            { text: 'API Providers', link: '/config/api-providers' },
            { text: 'Model Configuration', link: '/config/model-config' },
            { text: 'Config Priority', link: '/config/config-priority' },
          ],
        },
      ],

      '/frontend/': [
        {
          text: 'Frontend',
          items: [
            { text: 'Overview', link: '/frontend/' },
            { text: 'Live2D Integration', link: '/frontend/live2d' },
            { text: 'VRM Models', link: '/frontend/vrm' },
            { text: 'Internationalization', link: '/frontend/i18n' },
            { text: 'Pages & Templates', link: '/frontend/pages' },
          ],
        },
      ],

      '/deployment/': [
        {
          text: 'Deployment',
          items: [
            { text: 'Overview', link: '/deployment/' },
            { text: 'Docker', link: '/deployment/docker' },
            { text: 'Manual Setup', link: '/deployment/manual' },
            { text: 'Windows Executable', link: '/deployment/windows-exe' },
          ],
        },
      ],

      '/contributing/': [
        {
          text: 'Contributing',
          items: [
            { text: 'Overview', link: '/contributing/' },
            { text: 'Developer Notes', link: '/contributing/developer-notes' },
            { text: 'Testing', link: '/contributing/testing' },
            { text: 'Code Style', link: '/contributing/code-style' },
            { text: 'Roadmap', link: '/contributing/roadmap' },
          ],
        },
      ],
    },

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
