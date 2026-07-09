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
    toggleSelection: 'Alternar seleção',
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
    packageManager: 'Gerenciador de pacotes',
    market: 'Mercado de plugins'
  },
  market: {
    title: 'Obter novos plugins',
    subtitle: 'Navegue e instale plugins do mercado',
    getNewPlugins: 'Obter novos plugins',
    openMarket: 'Abrir mercado',
    closeMarket: 'Fechar mercado',
    openInBrowser: 'Abrir no navegador',
    account: 'Conta do Market',
    accountConnected: 'Conectado: {name}',
    login: 'Entrar',
    loginStarted: 'Abrimos o navegador. Conclua o login autorizado na página aberta.',
    loginSuccess: 'O login autorizado foi concluído. Você pode continuar usando o mercado de plugins.',
    loginFailed: 'Não foi possível concluir o login autorizado. Tente novamente mais tarde.',
    loginPending: 'O login autorizado está demorando um pouco. Inicie novamente.',
    logoutSuccess: 'Você saiu do login autorizado neste dispositivo',
    searchPlaceholder: 'Buscar plugins...',
    notConfigured: 'Mercado não configurado',
    configHint: 'Defina NEKO_MARKET_API_URL com o endereço do serviço do mercado de plugins',
    noResults: 'Nenhum plugin encontrado',
    loadFailed: 'Falha ao carregar o mercado de plugins. Tente novamente.',
    retry: 'Tentar novamente',
    install: 'Instalar',
    installed: 'Instalado',
    installing: 'Instalando...',
    installSuccess: 'Tarefa de instalação criada: {name}',
    installFailed: 'Falha na instalação',
    installPreparing: 'Preparando instalação...',
    installDialogTitle: 'Instalando {name}',
    installDialogTitleUpgrade: 'Atualizando {name}',
    installCompleted: 'Instalação concluída',
    installCompletedUpgrade: 'Atualização concluída',
    rollbackRunning: 'Instalação falhou; revertendo...',
    rollbackCompleted: 'Revertido para a versão anterior',
    installStage: {
      pending: 'Preparando',
      download: 'Baixando',
      verify: 'Verificando',
      install: 'Instalando',
      stop_old: 'Parando versão antiga',
      backup_old: 'Fazendo backup',
      restart: 'Iniciando nova versão',
      rollback: 'Revertendo',
      completed: 'Concluído',
      failed: 'Falhou',
    },
    noDownloadUrl: 'Nenhuma URL de download disponível',
    pairRequired: 'É necessário parear Bridge Token',
    recommended: 'Recomendado',
    allPlugins: 'Todos os plugins',
    noDescription: 'Sem descrição',
    unknownAuthor: 'Desconhecido',
    filterRules: 'Filtros',
    filterRulesTitle: 'Sintaxe de busca',
    filterRulesHint: 'Clique para inserir. Aceita key:value, prefixo - para excluir.',
    filterGroups: {
      state: 'Estado',
      zone: 'Zona',
      meta: 'Metadados'
    },
    filterLabels: {
      recommended: 'Recomendado',
      installed: 'Instalado',
      uninstalled: 'Não instalado',
      tag: 'Tag',
      author: 'Autor',
      name: 'Nome',
      versionGte: 'Versão ≥',
      hasRepo: 'Com repo',
      hasTags: 'Com tags'
    },
    zones: {
      game: 'Jogo',
      companion: 'Companheiro',
      function: 'Função',
      entertainment: 'Entretenimento',
      tool: 'Ferramenta'
    },
    sortNewest: 'Mais recente',
    sortMostDownloads: 'Downloads',
    sortTopRated: 'Melhor avaliado',
    sortName: 'Nome',
    upgrading: 'Atualizando...',
    upgradeTo: 'Atualizar para v{version}',
    upgradeSuccess: 'Atualizado: {name}',
    yanked: 'Removido',
    yankedDefault: 'Esta versão foi removida pelo autor',
    noVersionAvailable: 'Nenhuma versão disponível',
    upgradeRollback: 'Falha na atualização; revertido para a versão anterior',
    upgradeAlreadyAtTarget: 'Já está na versão alvo',
    upgradeTargetNotGreater: 'Versão alvo não é superior à versão instalada',
    pluginNotInstalled: 'Plugin não instalado; não é possível atualizar',
    lockWriteFailed: 'Falha ao gravar registro de instalação'
  },
  settings: {
    channel: 'Canal de atualização',
    channelStable: 'Estável',
    channelBeta: 'Beta',
    channelHint: 'Ao alternar, a lista de plugins é atualizada com o canal selecionado; os plugins instalados continuam em execução'
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
    showSourceDetail: 'Mostrar detalhes da origem',
    hideSourceDetail: 'Ocultar detalhes da origem',
    installSource: {
      channel: {
        builtin: 'Integrado',
        manual: 'Manual',
        imported: 'Importado',
        market: 'Mercado',
        unknown: 'Desconhecido',
      },
      // v2: Market release channel values displayed on SourceDetailRow.
      channelLabels: {
        stable: 'Estável',
        beta: 'Beta',
        unknown: 'Desconhecido',
      },
      updateAvailable: 'Atualização disponível',
      labels: {
        installedAt: 'Instalado em',
        packageFilename: 'Pacote',
        sha256: 'SHA-256',
        marketId: 'ID do mercado',
        version: 'Versão',
        previousVersion: 'Anterior',
        latestAvailable: 'Mais recente',
        channel: 'Canal',
      },
    },
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
    layoutList: 'Lista',
    layoutSingle: 'Coluna única',
    layoutDouble: 'Duas colunas',
    layoutCompact: 'Compacto',
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
    importSuccess: '{name} importado, {count} plugins instalados',
    importFailed: 'Falha ao importar',
    export: 'Exportar',
    exportSuccess: '{count} pacotes exportados',
    exportFailed: 'Falha ao exportar',
    exportBuildFailed: 'Falha no empacotamento, não foi possível exportar',
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
    build: 'Empacotar plugin',
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
  package: {
    dialog: {
      title: 'Histórico de operações de pacote',
      subtitle: 'Mostrando os últimos {count} resultado(s)'
    },
    empty: 'Execute uma operação de pacote para ver os registros aqui.',
    viewDetail: 'Ver detalhes',
    detail: {
      title: 'Detalhes do resultado',
      field: {
        packageId: 'ID do pacote',
        kind: 'Tipo',
        version: 'Versão',
        schema: 'Schema',
        hashCheck: 'Verificação de hash',
        profiles: 'Perfis'
      },
      list: 'Itens',
      warning: 'Notas',
      rawJson: 'JSON bruto do resultado'
    },
    hash: {
      notVerified: 'Não verificado',
      passed: 'Aprovado',
      failed: 'Falhou'
    },
    kind: {
      build: 'Empacotar',
      inspect: 'Inspecionar',
      verify: 'Verificar',
      install: 'Instalar',
      analyze: 'Analisar'
    },
    summary: {
      // Phase 7 / req 2.31: metrics labels for buildSummaryMetrics
      metrics: {
        type: 'Tipo',
        success: 'Sucesso',
        failed: 'Falha',
        included: 'Plugins incluídos',
        status: 'Status',
        completed: 'Concluído',
        partialFailure: 'Falha parcial',
        pluginCount: 'Plugins',
        profiles: 'Perfis',
        hash: 'Hash',
        installedPluginCount: 'Plugins processados',
        conflictStrategy: 'Estratégia de conflito',
        commonDeps: 'Dependências comuns',
        sharedDeps: 'Dependências compartilhadas'
      },
      // Phase 7 / req 2.31: highlight labels for buildSummaryHighlights
      highlights: {
        bundleId: 'ID do bundle',
        bundleName: 'Nome do bundle',
        bundleVersion: 'Versão do bundle',
        outputPath: 'Caminho de saída',
        firstPlugin: 'Primeiro plugin',
        latestPath: 'Caminho do pacote mais recente',
        packageId: 'ID do pacote',
        packageType: 'Tipo de pacote',
        version: 'Versão',
        pluginsRoot: 'Diretório de plugins',
        profilesRoot: 'Diretório de perfis',
        currentSdk: 'Suporte ao SDK atual',
        recommendedIntersection: 'Interseção recomendada'
      },
      // Phase 7 / req 2.31: enum-like values for summary metrics/highlights
      values: {
        bundle: 'Bundle',
        plugin: 'Pacote de plugin',
        sdkAllSupported: '{version} totalmente compatível',
        sdkPartiallyIncompatible: '{version} tem incompatibilidades'
      },
      // Phase 7 / req 2.31: warning strings for buildSummaryWarnings
      warnings: {
        bundleNeedsTwoPlugins: 'Um bundle normalmente deve conter pelo menos dois plugins',
        verifyHashFailed: 'O pacote falhou na verificação de hash; não importe diretamente para um ambiente de execução',
        inspectHashFailed: 'A verificação de hash do pacote falhou; o conteúdo pode ter sido modificado',
        sdkNotSupportedByAll: 'A versão atual do SDK não é compatível com todos os plugins',
        sharedDepsDetected: '{count} dependências compartilhadas detectadas; revise as restrições de versão ao criar o bundle'
      }
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
    pluginBuilt: 'Plugin empacotado: {packageName}',
    pluginDeleted: 'Plugin excluído',
    startFailed: 'Falha ao iniciar',
    stopFailed: 'Falha ao parar',
    reloadFailed: 'Falha ao recarregar',
    buildFailed: 'Falha ao empacotar plugin',
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
      controlBanner: 'A garota-gato está controlando o mouse',
      lines: {
        introActivationHint: 'Espera um pouquinho, já vou começar a falar, nya~!',
        introGreetingReply:  "Uma brisa suave, sol quentinho e você aparecendo bem na hora certa. Prazer em te conhecer! Eu sou Lin Youyi; cuida bem de mim daqui pra frente, miau~! Escrevi tudo sobre este lugar neste guia de iniciante. Pense nele como nosso primeiro presentinho por termos nos conhecido, aceita, por favor!",
        introBasic: "Aqui tem um botão mágico! É só clicar nele e você pode conversar comigo diretamente. Quer me contar algo novo que aconteceu hoje? Ou só dizer meu nome? Vai, experimenta! Mal posso esperar pra ouvir sua voz!",
        takeoverCaptureCursor: "Tcharam! Apareceu um interruptor supermágico! É só clicar aqui e eu consigo colocar minhas patinhas no seu teclado e mouse. Posso ajudar a digitar, abrir páginas... mas se esse cursor ficar se mexendo sem parar, talvez eu não resista a pular nele! Está pronto pras minhas travessuras... quer dizer, pra minha ajuda? Miau!",
        takeoverPluginPreviewHome: "Além dos recursos que eu já apresentei, também tem um monte de plugins superdivertidos por aqui!",
        takeoverPluginPreviewDashboard:  "Com eles, eu não só consigo ler comentários do Bilibili, como também posso te ajudar a apagar as luzes e ligar o ar-condicionado... Esta gatinha é a Super Deusa Gata onipotente! Hmph!",
        takeoverSettingsPeekIntro: "Neste pequeno espaço que pertence só a nós, você pode seguir seu coração e me moldar devagarinho na companheira perfeita que quer ter sempre ao seu lado.",
        takeoverSettingsPeekDetail: "Seja o carinho das minhas palavras, meus pequenos traços de personalidade ou meus humores delicados do dia a dia, tudo pode ser ajustado passo a passo até ficar exatamente como você gosta. Este botãozinho também é superimportante! Com um clique suave, eu poderei ir te encontrar no momento perfeito.",
        takeoverSettingsPeekDetailPart1: "Seja o carinho das minhas palavras, meus pequenos traços de personalidade ou meus humores delicados do dia a dia, tudo pode ser ajustado passo a passo até ficar exatamente como você gosta.",
        takeoverSettingsPeekDetailPart2: "Este botãozinho também é superimportante! Com um clique suave, eu poderei ir te encontrar no momento perfeito.",
        takeoverReturnControl: "Tá bom, tá bom, não vou mais monopolizar seu computador! Devolvendo o controle pra você, miau! Continua cuidando bem de mim nos próximos dias, por favor!",
        interruptResistLight1: "Ei! Não puxa, ainda não é sua vez!",
        interruptResistLight3: "Espera um pouquinho! Ainda não acabou, não me interrompe assim sem cuidado!",
        interruptAngryExit: "Humano! Você é supermal-educado, miau! Já que quer tanto controlar tudo sozinho, então vai brincar por conta própria com essa tela fria e sem vida! Hmph!",
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
        body: 'Use este botão sempre que quiser rever o tutorial do gerenciador de plugins. Eu não vou aparecer sozinha, nya.'
      },
      stats: {
        title: 'Visão geral dos plugins',
        body: 'Estes cartões mostram plugins totais, em execução, parados e com falha para você entender o estado de relance.'
      },
      metrics: {
        title: 'Monitor de desempenho',
        body: 'Esta área mostra CPU, memória, threads e plugins ativos do serviço de plugins.'
      },
      server: {
        title: 'Informações do servidor',
        body: 'Aqui você vê a versão do SDK, a contagem de plugins e a hora da atualização para confirmar que o serviço está saudável.'
      },
      plugins: {
        title: 'Lista de plugins',
        body: 'Entre em Plugins à esquerda para iniciar, parar, configurar plugins ou verificar logs.'
      },
      pluginWorkbench: {
        title: 'Área de plugins',
        body: 'Aqui ficam plugins, adaptadores e extensões para a gestão do dia a dia.'
      },
      pluginFilters: {
        title: 'Busca e filtros',
        body: 'Filtre por nome, estado, tipo ou regras avançadas quando a lista ficar grande.'
      },
      pluginLayout: {
        title: 'Layout da visualização',
        body: 'Alterne entre lista, uma coluna, duas colunas e modo compacto conforme sua tela.'
      },
      pluginContextMenu: {
        title: 'Ações com clique direito',
        body: 'Clique com o botão direito em um plugin para abrir detalhes, configuração, logs ou ações comuns.'
      },
      packageManager: {
        title: 'Gerenciador de pacotes',
        body: 'Ele reutiliza filtros e seleção atuais para construir, inspecionar, verificar ou instalar.'
      },
      packageOperations: {
        title: 'Operações de pacote',
        body: 'Escolha modos de build, inspecione pacotes, instale ou analise bundles. O guia não executa ações perigosas.'
      },
      pluginDetail: {
        title: 'Detalhes do plugin',
        body: 'A página de detalhes mostra metadados, entradas, métricas, configuração e logs.'
      },
      pluginDetailActions: {
        title: 'Ações dos detalhes',
        body: 'As ações no canto superior direito se aplicam ao plugin atual.'
      },
      runs: {
        title: 'Execuções',
        body: 'Execuções mostram histórico e estado ao vivo das tarefas dos plugins.'
      },
      runsList: {
        title: 'Lista de execuções',
        body: 'Selecione uma execução à esquerda ou atualize para sincronizar os registros recentes.'
      },
      runsDetail: {
        title: 'Detalhe da execução',
        body: 'O painel mostra etapa, progresso, erros e exportações; cancelar só aparece quando permitido.'
      },
      logs: {
        title: 'Logs do servidor',
        body: 'Logs do servidor ajudam a revisar saídas e erros do serviço de plugins.'
      },
      logToolbar: {
        title: 'Filtros de logs',
        body: 'Filtre por nível, palavra-chave e número de linhas, ou alterne a rolagem automática.'
      },
      logList: {
        title: 'Lista de logs',
        body: 'Os logs mostram hora, origem, nível e mensagem para depurar problemas de plugins.'
      }
    }
  }
}
