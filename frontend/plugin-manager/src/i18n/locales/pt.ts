/**
 * Pacote de idioma português
 */
export default {
  common: {
    loading: 'Carregando...',
    refresh: 'Atualizar',
    search: 'Pesquisar',
    filter: 'Filtrar',
    reset: 'Redefinir',
    confirm: 'Confirmar',
    cancel: 'Cancelar',
    save: 'Salvar',
    delete: 'Excluir',
    edit: 'Editar',
    add: 'Adicionar',
    back: 'Voltar',
    submit: 'Enviar',
    close: 'Fechar',
    minimize: 'Minimizar',
    maximize: 'Maximizar',
    restore: 'Restaurar',
    success: 'Sucesso',
    error: 'Erro',
    warning: 'Aviso',
    info: 'Informação',
    noData: 'Sem dados',
    unknown: 'Desconhecido',
    nA: 'N/D',
    darkMode: 'Modo escuro',
    lightMode: 'Modo claro',
    logoutConfirmTitle: 'Aviso',
    disconnected: 'Servidor desconectado',
    languageAuto: 'Automático'
  },
  nav: {
    dashboard: 'Painel',
    plugins: 'Plugins',
    metrics: 'Métricas',
    logs: 'Registros',
    runs: 'Execuções',
    serverLogs: 'Registros do servidor',
    adapters: 'Adaptadores',
    adapterUI: 'UI do adaptador',
    packageManager: 'Gerenciador de pacotes'
  },
  auth: {
    unauthorized: 'Acesso não autorizado',
    forbidden: 'Acesso negado'
  },
  plugin: {
    addProfile: {
      prompt: 'Digite um novo nome de perfil',
      title: 'Adicionar perfil',
      inputError: 'O nome não pode ficar vazio nem conter apenas espaços'
    },
    removeProfile: {
      confirm: 'Tem certeza que deseja excluir o perfil "{name}"?',
      title: 'Excluir perfil'
    }
  },
  dashboard: {
    title: 'Painel',
    pluginOverview: 'Visão geral dos plugins',
    totalPlugins: 'Total de plugins',
    running: 'Em execução',
    stopped: 'Parados',
    crashed: 'Com falhas',
    globalMetrics: 'Monitoramento global de desempenho',
    totalCpuUsage: 'Uso total de CPU',
    totalMemoryUsage: 'Uso total de memória',
    totalThreads: 'Total de threads',
    activePlugins: 'Plugins ativos',
    serverInfo: 'Informações do servidor',
    sdkVersion: 'Versão do SDK',
    updateTime: 'Hora da atualização',
    noMetricsData: 'Sem dados de desempenho',
    failedToLoadServerInfo: 'Falha ao carregar informações do servidor',
    startTutorial: 'Guia tutorial',
    tutorialHint: 'Primeira vez no gerenciador de plugins? Toque aqui que eu te mostro rapidinho.'
  },
  plugins: {
    title: 'Plugins',
    name: 'Nome do plugin',
    id: 'ID do plugin',
    version: 'Versão',
    description: 'Descrição',
    status: 'Status',
    sdkVersion: 'Versão do SDK',
    actions: 'Ações',
    start: 'Iniciar',
    stop: 'Parar',
    reload: 'Recarregar',
    reloadAll: 'Recarregar tudo',
    reloadAllConfirm: 'Tem certeza que deseja recarregar os {count} plugins em execução?',
    reloadAllSuccess: '{count} plugins recarregados com sucesso',
    reloadAllPartial: 'Recarga concluída: {success} com sucesso, {fail} com falha',
    viewDetails: 'Ver detalhes',
    noPlugins: 'Sem plugins',
    adapterNotFound: 'Adaptador não encontrado',
    pluginNotFound: 'Plugin não encontrado',
    pluginDetail: 'Detalhes do plugin',
    basicInfo: 'Informações básicas',
    entries: 'Pontos de entrada',
    performance: 'Desempenho',
    config: 'Configuração',
    logs: 'Registros',
    entryPoint: 'Ponto de entrada',
    entryName: 'Nome',
    entryId: 'ID',
    entryDescription: 'Descrição',
    trigger: 'Acionar',
    triggerSuccess: 'Acionamento bem-sucedido',
    triggerFailed: 'Falha ao acionar',
    noEntries: 'Sem pontos de entrada',
    showMetrics: 'Mostrar métricas',
    hideMetrics: 'Ocultar métricas',
    filterPlaceholder: 'Filtrar plugins por texto, pinyin e regras is:/type:/has:',
    filterRules: 'Regras',
    filterRulesTitle: 'Regras de filtro',
    filterRulesHint: 'Clique em uma regra para inseri-la na consulta e combiná-la com texto normal.',
    filterWhitelist: 'Lista branca',
    filterBlacklist: 'Lista negra',
    invalidRegex: 'Expressão regular inválida',
    hoverToShowFilter: 'Passe o cursor para mostrar o filtro',
    configPath: 'Arquivo de configuração',
    lastModified: 'Última modificação',
    configEditorPlaceholder: 'Digite a configuração do plugin em formato TOML',
    configInvalidToml: 'Formato TOML inválido. Corrija antes de salvar.',
    configLoadFailed: 'Falha ao carregar a configuração do plugin',
    configSaveFailed: 'Falha ao salvar a configuração do plugin',
    configReloadTitle: 'Recarga necessária',
    configReloadPrompt: 'Configuração atualizada. Recarregar o plugin agora para aplicar as alterações?',
    configApplyTitle: 'Aplicar configuração',
    configHotUpdatePrompt: 'Configuração salva. Aplicar ao plugin em execução agora? (A atualização a quente não exige reinício)',
    hotUpdate: 'Atualização a quente',
    reloadPlugin: 'Reiniciar plugin',
    hotUpdateSuccess: 'Configuração atualizada a quente com sucesso',
    hotUpdatePartial: 'Configuração salva, mas o plugin não está em execução. Terá efeito após iniciar.',
    hotUpdateFailed: 'Falha na atualização a quente',
    formMode: 'Formulário',
    sourceMode: 'Fonte',
    formModeHint: 'Este modo renderiza um formulário a partir do objeto de configuração analisado pelo servidor. Use o modo fonte para recursos TOML avançados (comentários/formatação).',
    addField: 'Adicionar campo',
    addItem: 'Adicionar item',
    fieldName: 'Nome do campo',
    fieldNameRequired: 'O nome do campo é obrigatório',
    invalidFieldKey: 'Nome de campo inválido',
    fieldType: 'Tipo do campo',
    duplicateFieldKey: 'O nome do campo já existe. Escolha outro.',
    profiles: 'Perfis',
    active: 'Ativo',
    diffPreview: 'Pré-visualização das diferenças',
    unsavedChangesWarning: 'Há alterações não salvas. Ao trocar de plugin elas serão descartadas. Continuar?',
    enabled: 'Habilitado',
    disabled: 'Desabilitado',
    autoStart: 'Início automático',
    manualStart: 'Início manual',
    fetchFailed: 'Falha ao obter os plugins',
    extension: 'Extensão',
    pluginType: 'Tipo',
    pluginTypeNormal: 'Plugin',
    hostPlugin: 'Plugin hospedeiro',
    boundExtensions: 'Extensões vinculadas',
    pluginsSection: 'Plugins',
    adaptersSection: 'Adaptadores',
    extensionsSection: 'Extensões',
    typePlugin: 'Plugin',
    typeAdapter: 'Adaptador',
    typeExtension: 'Extensão',
    openPackageManager: 'Gerenciador de pacotes',
    closePackageManager: 'Ocultar gerenciador de pacotes',
    packageManagerOpened: 'Gerenciador de pacotes aberto',
    packageManagerSyncHint: 'Os filtros e plugins selecionados são sincronizados diretamente com o painel do gerenciador de pacotes.',
    multiSelect: 'Seleção múltipla',
    exitMultiSelect: 'Sair da seleção múltipla',
    selectedCount: '{count} selecionados',
    selectAllVisible: 'Selecionar visíveis',
    invertVisibleSelection: 'Inverter visíveis',
    clearSelection: 'Limpar seleção',
    batchStartConfirm: 'Iniciar os {count} plugins selecionados?',
    batchStopConfirm: 'Parar os {count} plugins em execução?',
    batchReloadConfirm: 'Recarregar os {count} plugins em execução?',
    batchDeleteConfirm: 'Excluir os {count} plugins selecionados? Esta ação não pode ser desfeita.',
    batchStartSuccess: '{count} plugins iniciados com sucesso',
    batchStopSuccess: '{count} plugins parados com sucesso',
    batchReloadSuccess: '{count} plugins recarregados com sucesso',
    batchDeleteSuccess: '{count} plugins excluídos com sucesso',
    batchPartial: 'Concluído: {success} com sucesso, {fail} com falha',
    batchNoStartable: 'Nenhum plugin iniciável na seleção',
    batchNoStoppable: 'Nenhum plugin em execução na seleção',
    batchNoReloadable: 'Nenhum plugin em execução na seleção',
    import: 'Importar',
    importing: 'Importando…',
    importSuccess: '{name} importado, {count} plugins desempacotados',
    importFailed: 'Falha ao importar',
    export: 'Exportar',
    exportSuccess: '{count} pacotes exportados',
    exportFailed: 'Falha ao exportar',
    exportPackFailed: 'Falha no empacotamento, não foi possível exportar',
    filterRuleGroups: {
      state: 'Estado',
      type: 'Tipo',
      meta: 'Metadados'
    },
    filterRuleLabels: {
      running: 'Em execução',
      stopped: 'Parados',
      disabled: 'Desabilitado',
      selected: 'Selecionados',
      manual: 'Início manual',
      auto: 'Início automático',
      plugin: 'Plugin',
      adapter: 'Adaptador',
      extension: 'Extensão',
      ui: 'Com UI',
      entries: 'Com pontos de entrada',
      host: 'Com hospedeiro',
      name: 'Por nome',
      id: 'Por ID',
      hostTarget: 'Por hospedeiro',
      version: 'Por versão',
      entry: 'Por ponto de entrada',
      author: 'Por autor'
    },
    contextSections: {
      navigation: 'Navegar',
      runtime: 'Tempo de execução',
      plugin: 'Extras do plugin'
    },
    pack: 'Empacotar plugin',
    delete: 'Excluir plugin',
    disableExtension: 'Desabilitar extensão',
    enableExtension: 'Habilitar extensão',
    dangerDialog: {
      title: 'Confirmar ação destrutiva',
      warningTitle: 'Esta ação não pode ser desfeita',
      deleteMessage: 'Excluir "{pluginName}" removerá o diretório do plugin e a lista será atualizada imediatamente.',
      hint: 'Para evitar cliques acidentais, mantenha o botão abaixo pressionado para continuar.',
      holdIdle: 'Mantenha pressionado para excluir',
      holdActive: 'Continue pressionando para confirmar…',
      loading: 'Excluindo plugin...'
    },
    ui: {
      open: 'Abrir UI',
      title: 'UI',
      panel: 'Painel',
      guide: 'Tutorial',
      loading: 'Carregando UI do plugin...',
      loadError: 'Falha ao carregar a UI do plugin',
      noUI: 'Este plugin não possui UI personalizada',
      hostedTsxPending: 'Renderização Hosted TSX em breve',
      markdownPending: 'Renderização de tutorial Markdown em breve',
      autoPending: 'Painéis gerados automaticamente em breve',
      surfaceUnavailable: 'Surface indisponível',
      surfaceEntryMissing: 'O arquivo de entrada declarado por esta Surface não existe. Verifique o caminho entry no plugin.toml.',
      surfaceWarnings: 'A declaração de UI do plugin precisa de atenção',
      controlError: 'Erro de controle da UI do plugin',
      hostedRuntimePending: 'O contêiner Vue reconheceu esta Surface. Renderizadores TSX, Markdown e Auto serão conectados em uma fase posterior.'
    }
  },
  metrics: {
    title: 'Métricas',
    pluginMetrics: 'Métricas de desempenho do plugin',
    cpuUsage: 'Uso de CPU',
    memoryUsage: 'Uso de memória',
    threads: 'Threads',
    pid: 'ID do processo',
    noMetrics: 'Sem dados de desempenho',
    refreshInterval: 'Intervalo de atualização',
    seconds: 'segundos',
    cpu: 'Uso de CPU',
    memory: 'Memória',
    memoryPercent: '% de memória',
    pendingRequests: 'Solicitações pendentes',
    totalExecutions: 'Execuções totais',
    noData: 'Sem dados'
  },
  logs: {
    title: 'Registros',
    pluginLogs: 'Registros do plugin',
    serverLogs: 'Registros do servidor',
    level: 'Nível',
    time: 'Hora',
    source: 'Origem',
    file: 'Arquivo',
    message: 'Mensagem',
    allLevels: 'Todos os níveis',
    noLogs: 'Sem registros',
    autoScroll: 'Rolagem automática',
    scrollToBottom: 'Rolar até o final',
    logFiles: 'Arquivos de registro',
    selectFile: 'Selecionar arquivo',
    search: 'Buscar nos registros...',
    lines: 'Linhas',
    totalLogs: 'Total de {count} registros',
    loadError: 'Falha ao carregar registros: {error}',
    emptyFile: 'O arquivo de registro está vazio ou não existe',
    noMatches: 'Nenhum registro correspondente',
    logFile: 'Arquivo de registro',
    totalLines: 'Linhas totais',
    returnedLines: 'Linhas retornadas',
    connected: 'Conectado',
    disconnected: 'Desconectado',
    connectionFailed: 'Falha de conexão do fluxo de registros'
  },
  runs: {
    title: 'Execuções',
    detail: 'Detalhes da execução',
    wsDisconnected: 'Conexão em tempo real não estabelecida. Verifique o status do servidor.',
    noRuns: 'Sem execuções',
    selectRun: 'Selecione uma execução para ver detalhes',
    runId: 'ID da execução',
    status: 'Status',
    pluginId: 'ID do plugin',
    entryId: 'Ponto de entrada',
    updatedAt: 'Atualizado em',
    createdAt: 'Criado em',
    stage: 'Etapa',
    message: 'Mensagem',
    progress: 'Progresso',
    error: 'Erro',
    export: 'Exportar',
    exportType: 'Tipo',
    exportContent: 'Conteúdo',
    noExport: 'Sem itens para exportar',
    cancel: 'Cancelar execução',
    cancelConfirmTitle: 'Cancelar esta execução?',
    cancelConfirmMessage: 'ID da execução: {runId}',
    cancelSuccess: 'Cancelamento solicitado'
  },
  packageManager: {
    resultDialog: {
      title: 'Registro de resultados de pacotes',
      subtitle: 'Mantém os últimos {count} resultados de execução',
      empty: 'Os resultados das operações de pacote aparecerão aqui',
      viewDetails: 'Ver detalhes',
      detailTitle: 'Detalhes do resultado',
      summaryTitle: 'Detalhes',
      notesTitle: 'Notas',
      rawJsonTitle: 'JSON bruto do resultado',
      kinds: {
        pack: 'Empacotar',
        inspect: 'Inspecionar',
        verify: 'Verificar',
        unpack: 'Descompactar',
        analyze: 'Analisar',
      },
      inspect: {
        packageId: 'ID do pacote',
        packageType: 'Tipo',
        version: 'Versão',
        schemaVersion: 'Schema',
        hashCheck: 'Verificação Hash',
        profiles: 'Profiles',
        packageTypes: {
          bundle: 'Bundle',
          plugin: 'Pacote de plugin',
        },
        hashStatus: {
          notChecked: 'Não verificado',
          passed: 'Aprovado',
          failed: 'Falhou',
        },
      },
      metrics: {
        pack: {
          type: 'Tipo',
          succeeded: 'Sucesso',
          failed: 'Falha',
          containsPlugins: 'Contém plugins',
          status: 'Estado',
          complete: 'Concluído',
          partialFailed: 'Falha parcial',
        },
        inspect: {
          pluginCount: 'Contagem de plugins',
          profileCount: 'Profiles',
          hash: 'Hash',
        },
        unpack: {
          processedPlugins: 'Plugins processados',
          conflictStrategy: 'Estratégia de conflito',
          hash: 'Hash',
        },
        analyze: {
          pluginCount: 'Contagem de plugins',
          commonDependencies: 'Dependências comuns',
          sharedDependencies: 'Dependências compartilhadas',
        },
      },
      highlights: {
        pack: {
          bundlePluginId: 'ID do bundle',
          bundleName: 'Nome do bundle',
          bundleVersion: 'Versão do bundle',
          outputPath: 'Caminho de saída',
          firstPlugin: 'Primeiro plugin',
          latestPackagePath: 'Caminho do pacote mais recente',
        },
        inspect: {
          packageId: 'ID do pacote',
          packageType: 'Tipo de pacote',
          version: 'Versão',
        },
        unpack: {
          packageId: 'ID do pacote',
          pluginsRoot: 'Diretório de plugins',
          profilesRoot: 'Diretório de Profiles',
        },
        analyze: {
          currentSdk: 'Suporte do SDK atual',
          supported: 'compatível',
          unsupported: 'não totalmente compatível',
          matchingVersions: 'Combinações recomendadas',
        },
      },
      list: {
        pluginPrefix: 'plugin:',
        profilePrefix: 'profile:',
        renamedSuffix: '(renomeado)',
        arrow: '->',
      },
      warnings: {
        bundleNeedsTwoPlugins: 'Um bundle geralmente deve conter pelo menos dois plugins',
        verifyFailed: 'O pacote não passou na verificação hash. Não importe diretamente para um ambiente de execução.',
        inspectHashFailed: 'A verificação hash do pacote atual falhou e o conteúdo pode ter sido modificado.',
        analyzeSdkMismatch: 'A versão atual do SDK não é compatível com todos os plugins em conjunto.',
        analyzeSharedDependencies: '{count} dependências compartilhadas detectadas. Verifique bem as restrições de versão ao criar bundles.',
      },
    },
  },
  status: {
    running: 'Em execução',
    stopped: 'Parado',
    crashed: 'Com falhas',
    loadFailed: 'Falha no carregamento',
    loading: 'Carregando',
    disabled: 'Desabilitado',
    injected: 'Injetado',
    pending: 'Hospedeiro pendente'
  },
  logLevel: {
    DEBUG: 'Depuração',
    INFO: 'Informação',
    WARNING: 'Aviso',
    ERROR: 'Erro',
    CRITICAL: 'Crítico',
    UNKNOWN: 'Desconhecido'
  },
  messages: {
    fetchFailed: 'Falha ao obter dados',
    operationSuccess: 'Operação bem-sucedida',
    operationFailed: 'Falha na operação',
    confirmDelete: 'Confirmar exclusão?',
    confirmStop: 'Confirmar parar plugin?',
    confirmStart: 'Confirmar iniciar plugin?',
    confirmReload: 'Confirmar recarregar plugin?',
    pluginStarted: 'Plugin iniciado com sucesso',
    pluginStopped: 'Plugin parado',
    pluginReloaded: 'Plugin recarregado com sucesso',
    pluginPacked: 'Plugin empacotado: {packageName}',
    pluginDeleted: 'Plugin excluído',
    startFailed: 'Falha ao iniciar',
    stopFailed: 'Falha ao parar',
    reloadFailed: 'Falha ao recarregar',
    packFailed: 'Falha ao empacotar plugin',
    deleteFailed: 'Falha ao excluir plugin',
    pluginLoadFailed: 'O plugin falhou ao carregar e não pode ser iniciado.',
    confirmDisableExt: 'Desabilitar esta extensão? Sua funcionalidade será descarregada do plugin hospedeiro.',
    extensionDisabled: 'Extensão desabilitada',
    extensionEnabled: 'Extensão habilitada',
    disableExtFailed: 'Falha ao desabilitar a extensão',
    enableExtFailed: 'Falha ao habilitar a extensão',
    requestFailed: 'Falha na solicitação',
    requestFailedWithStatus: 'Falha na solicitação ({status})',
    badRequest: 'Parâmetros de solicitação inválidos',
    resourceNotFound: 'Recurso solicitado não encontrado',
    internalServerError: 'Erro interno do servidor',
    serviceUnavailable: 'Serviço indisponível',
    networkError: 'Erro de rede. Verifique sua conexão.'
  },
  welcome: {
    about: {
      title: 'Sobre o N.E.K.O.',
      description: 'N.E.K.O. (Networked Emotional Knowing Organism) é um metaverso de companheiros de IA "vivos", construído juntos por você e eu. É uma plataforma UGC orientada a código aberto e com propósito solidário, dedicada a construir um metaverso AI-nativo intimamente conectado ao mundo real.'
    },
    pluginManagement: {
      title: 'Gerenciamento de plugins',
      description: 'Acesse a lista de plugins pela barra de navegação à esquerda. Você pode visualizar, iniciar, parar e recarregar plugins. Cada plugin conta com monitoramento de desempenho e visualização de registros independentes para ajudá-lo a gerenciar e depurar melhor o sistema de plugins.'
    },
    mcpServer: {
      title: 'Servidor MCP',
      description: 'O N.E.K.O. suporta servidores Model Context Protocol (MCP), permitindo que plugins interajam com outros sistemas e serviços de IA por meio de protocolos padronizados. Você pode ver e gerenciar as conexões MCP na página de detalhes do plugin.'
    },
    documentation: {
      title: 'Documentação e recursos',
      description: 'Consulte a documentação do projeto para mais informações:',
      links: [
        { text: 'Repositório do GitHub', url: 'https://github.com/Project-N-E-K-O/N.E.K.O' },
        { text: 'Página da Steam', url: 'https://store.steampowered.com/app/4099310/__NEKO/' },
        { text: 'Comunidade do Discord', url: 'https://discord.gg/5kgHfepNJr' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ' e ',
      readme: 'Arquivo README.md:',
      openFailed: 'Falha ao abrir o README.md no editor',
      openTimeout: 'Tempo de solicitação esgotado ao abrir o arquivo README.md',
      openError: 'Ocorreu um erro ao abrir o arquivo README.md'
    },
    community: {
      title: 'Comunidade e suporte',
      description: 'Junte-se à nossa comunidade para se conectar com outros desenvolvedores e usuários:',
      links: [
        { text: 'Servidor do Discord', url: 'https://discord.gg/5kgHfepNJr' },
        { text: 'Grupo QQ', url: 'https://qm.qq.com/q/hN82yFONJQ' },
        { text: 'Issues do GitHub', url: 'https://github.com/Project-N-E-K-O/N.E.K.O/issues' }
      ],
      linkSeparator: ', ',
      linkLastSeparator: ' e '
    }
  },
  app: {
    titleSuffix: 'Gerenciador de plugins N.E.K.O'
  },
  tutorial: {
    yuiGuide: {
      buttons: {
        skipChat: 'Agora não',
        sayHello: 'Olá',
      },
      lines: {
        introActivationHint: 'Clica aqui pra eu poder começar a falar, nya~!',
        introGreetingReply: 'Brisa suave, sol, e você aparecendo exatamente na hora certa. Prazer em te conhecer, eu sou Lin Youyi; cuide bem de mim daqui em diante, miau! Escrevi tudo sobre este lugar no guia para iniciantes. Considere isso o primeiro presentinho do nosso encontro; dá uma olhada!',
        introBasic: 'Ooh, olha este botaozinho brilhante! Dá um clique nele e a gente pode começar a conversar na hora! Quer me contar as novidades de hoje? Ou só chamar meu nome? Vamos, experimenta, mal posso esperar para ouvir sua voz! Miau!',
        takeoverCaptureCursor: 'Ta-dá! Apareceu o interruptor mágico definitivo! É só tocar aqui e eu posso esticar minhas patinhas até o seu teclado e o seu mouse! Vou te ajudar a digitar, ajudar a abrir páginas web... Mas, se esse ponteiro do mouse ficar se mexendo por aí, talvez eu não consiga resistir a pular nele! Você está pronto para minhas travessuras... ah, não, para minha ajuda? Miau!',
        takeoverPluginPreviewHome: 'Ainda não acabou! Olha, olha, tem muuuitos plugins divertidos aqui!',
        takeoverPluginPreviewDashboard: 'Com eles, eu não só consigo ler o danmaku do Bilibili, mas também ajudar você a apagar as luzes e ligar o ar-condicionado... Eu sou a super deusa gata onipotente! Hehe!',
        takeoverSettingsPeekIntro: 'Claro, se você quiser que eu converse mais com você, não é impossível. Só prepare mais peixinhos secos pra mim, hehe. Tá bom, vou parar de te provocar; todas as configurações estão neste ícone de engrenagem.',
        takeoverSettingsPeekDetail: 'Olha, aqui você pode colocar roupas novas em mim, me dar uma voz bonita... Trocar por outra catgirl ou modificar memórias? Espera um pouco! O que você está fazendo? Não vai me substituir, vai? Aaaah, de jeito nenhum! Fecha, fecha rápido!',
        takeoverSettingsPeekDetailPart1: 'Olha, aqui você pode colocar roupas novas em mim, me dar uma voz bonita... Trocar por outra catgirl ou modificar memórias?',
        takeoverSettingsPeekDetailPart2: 'Espera um pouco! O que você está fazendo? Não vai me substituir, vai? Aaaah, de jeito nenhum! Fecha, fecha rápido!',
        takeoverReturnControl: 'Tá bom, tá bom, não vou mais tomar conta do seu computador! O controle voltou pra você! Mas nada de clicar em configurações estranhas quando eu não estiver olhando! Conte comigo, e cuide de mim daqui em diante!',
        interruptResistLight1: 'Ei! Não me arrasta, ainda não é a sua vez!',
        interruptResistLight3: 'Espera um pouco! Ainda não acabou, não me interrompa assim!',
        interruptAngryExit: 'Humano! Você é muito sem educação, miau! Já que você quer tanto operar tudo sozinho, então vai brincar com essa tela fria sozinho! Hmph!',
        introPractice: 'Agora, tenta falar comigo e vê se a gente está sincronizadinho, nya~!',
      },
    }
  },
  yuiTutorial: {
    title: 'Meow~ Bem-vindo ao Gerenciador de Plugins!',
    welcome: 'É aqui que você gerencia todos os seus plugins, nya~ Pode navegar, executar e ajustar pra me deixar ainda mais poderosa!',
    hint: 'Vá com calma, dê uma olhadinha, e toca no botão abaixo quando terminar~',
    complete: 'Tudo pronto, meow~',
    dismiss: 'Talvez depois~',
    keyboardSkipHint: 'Pressione Enter ou Espaço para avançar. Isso fica ativo 0,5 segundo após o início de cada etapa.',
    steps: {
      start: {
        title: 'Comece aqui',
        body: 'Use este botão para reproduzir a tour do gerenciador de plugins quando quiser. Se você trocar o idioma durante a reprodução, a tour acompanha o novo idioma.'
      },
      stats: {
        title: 'Visão geral dos plugins',
        body: 'Esses cartões resumem o total, os em execução, os parados e os que travaram, para avaliar primeiro o estado do serviço de plugins.'
      },
      metrics: {
        title: 'Monitor de desempenho',
        body: 'Esta área mostra CPU, memória, threads e a contagem de plugins ativos. Confira primeiro quando o galgame OCR ou o Agent estiverem lentos.'
      },
      server: {
        title: 'Informações do servidor',
        body: 'Aqui você pode verificar a versão do SDK, a contagem de plugins e a hora da atualização para confirmar que o serviço de plugins do backend está disponível.'
      },
      plugins: {
        title: 'Lista de plugins',
        body: 'Abra o Gerenciamento de Plugins para iniciar, parar, recarregar, configurar plugins ou abrir a UI e o guia do galgame_plugin.'
      },
      pluginWorkbench: {
        title: 'Área de plugins',
        body: 'Este espaço reúne plugins normais, adaptadores e extensões. galgame_plugin, Danmaku, MCP e outros plugins ficam aqui.'
      },
      pluginFilters: {
        title: 'Busca e filtros',
        body: 'Filtre por nome, estado, tipo ou regras avançadas. Para encontrar o galgame_plugin rapidamente, pesquise por galgame.'
      },
      pluginLayout: {
        title: 'Layout da visualização',
        body: 'Alterne entre lista, uma coluna, duas colunas e modo compacto. Duas colunas ou o modo compacto reduzem a rolagem quando há muitos plugins.'
      },
      pluginContextMenu: {
        title: 'Ações com clique direito',
        body: 'Clique com o botão direito em um plugin para abrir detalhes, configuração, logs, UI ou guia, e para executar iniciar, parar e recarregar.'
      },
      packageManager: {
        title: 'Gerenciador de pacotes',
        body: 'O gerenciador de pacotes reutiliza os filtros atuais e a seleção múltipla para criar pacotes de plugin único ou bundles, e também para lidar com pacotes locais.'
      },
      packageOperations: {
        title: 'Operações de pacote',
        body: 'Empacote plugins selecionados, individuais ou todos; crie bundles; inspecione e verifique pacotes; descompacte pacotes; ou analise dependências de bundles aqui.'
      },
      pluginDetail: {
        title: 'Detalhes do plugin',
        body: 'A página de detalhes mostra a UI, o guia, as informações básicas, entradas, métricas, configuração e logs. O painel principal do galgame_plugin é a guia UI.'
      },
      pluginDetailActions: {
        title: 'Ações dos detalhes',
        body: 'As ações no canto superior direito se aplicam ao plugin atual. Ao depurar o galgame_plugin, confirme que ele está em execução antes de abrir a UI ou os logs.'
      },
      runs: {
        title: 'Execuções',
        body: 'As execuções mostram o histórico e o estado ao vivo das tarefas de entrada do plugin, como instalar dependências de OCR, explicar falas ou resumir cenas.'
      },
      runsList: {
        title: 'Lista de execuções',
        body: 'Selecione uma execução de tarefa à esquerda. Depois que instalações, análises ou entradas de Agent terminarem, use esta lista para rever os resultados.'
      },
      runsDetail: {
        title: 'Detalhe da execução',
        body: 'O painel de detalhes mostra etapa, progresso, erros e exportações. Cancelar aparece apenas em tarefas longas que podem ser interrompidas.'
      },
      logs: {
        title: 'Logs do servidor',
        body: 'Os logs do servidor mostram a saída de todo o serviço de plugins. Logs específicos do galgame_plugin também ficam disponíveis na página de detalhes.'
      },
      logToolbar: {
        title: 'Filtros de logs',
        body: 'Filtre por nível, palavra-chave e número de linhas, ou alterne a rolagem automática. Use o ID do plugin como palavra-chave ao depurar.'
      },
      logList: {
        title: 'Lista de logs',
        body: 'Os logs mostram hora, origem, nível e mensagem. Erros de OCR, Memory Reader, Agent e do gerenciador de pacotes costumam aparecer primeiro aqui.'
      }
    }
  }
}
