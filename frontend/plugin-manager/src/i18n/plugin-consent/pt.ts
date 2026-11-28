import labels from '../plugin-permissions/pt'
import { withPermissionLabels } from '../plugin-permissions/shared'
import type { PluginConsentMessages } from './en-US'

const messages: PluginConsentMessages = {
  title: 'Permissões',
  dialogTitle: 'Confirmar capacidades do plugin',
  introduction: 'Este plugin pode usar as capacidades abaixo enquanto estiver em execução. Confirme-as para iniciá-lo.',
  analysisPending: 'A análise de capacidades ainda não terminou. Clique em iniciar novamente para iniciar diretamente: {names}',
  incomplete: 'A análise de capacidades está incompleta. Você ainda pode confirmar e iniciar este plugin.',
  upgraded: 'Adicionada após a atualização',
  pending: 'Aguardando confirmação',
  decision: {
    approved: 'Aprovado',
    denied: 'Negado',
  },
  banner: {
    confirm: '{n} capacidades pendentes de confirmação',
    upgradedConfirm: '{n} capacidades novas após a atualização pendentes de confirmação',
    capabilitiesDesc:
      'O plugin pode ler as suas conversas ou memória, ou enviar conteúdo ao N.E.K.O. Após confirmar, não será perguntado novamente até que uma atualização adicione capacidades novas.',
    incompleteTitle: 'A análise de capacidades não foi concluída por completo',
    incompleteDesc: 'Os resultados podem estar incompletos; mesmo assim pode aprovar e continuar a usar.',
    deniedDesc: 'O arranque automático irá ignorar este plugin; ao iniciá-lo manualmente será perguntado novamente.',
    approvedAt: 'Confirmado em {date}',
    analyzingTitle: 'A analisar as capacidades do plugin…',
    analyzingDesc: 'Os resultados aparecem aqui automaticamente quando a análise terminar.',
  },
  approve: 'Aprovar',
  reject: 'Negar',
  approveAndStart: 'Aprovar e iniciar',
  revoke: 'Revogar confirmação',
  revokeDecision: 'Retirar decisão',
  evidenceToggle: 'Localizações no código ({n})',
  meta: {
    analyzed: 'Analisado em {date}',
    version: 'Versão {version}',
    files: '{n} ficheiros',
  },
  noticesTitle: 'Há capacidades de plugins aguardando confirmação',
  loading: 'Carregando informações de capacidades…',
  loadFailed: 'Não foi possível carregar as informações de capacidades.',
  saveFailed: 'Não foi possível salvar a decisão. Tente novamente.',
  unknown:
    'O uso exato de dados ainda é desconhecido. Os SDKs de plugins podem ler conversas e memória e enviar conteúdo ao N.E.K.O.',
  noneFound: 'Nenhum problema encontrado: a análise do código não encontrou uso de permissões nem código que não pudesse ser confirmado estaticamente.',
  retry: 'Analisar novamente',
  findings: {
    usedTitle: 'Permissões que este plugin usa',
    usedBanner: 'Este plugin usa {n} permissão(ões)',
    declared: 'Declarada pelo autor',
    detected: 'Encontrada no código',
    blindSpotsTitle: '{n} ponto(s) não puderam ser confirmados pela análise estática',
    blindSpotsDesc: 'Esses pontos carregam código dinamicamente, usam reflexão ou executam código gerado, ou não foram analisados. O que fazem não pode ser determinado sem executar o plugin.',
    unlocated: 'Parte do plugin (sem um arquivo específico)',
    partialTag: 'Parcialmente não confirmado',
    partialTagHint: 'Parte do código não pôde ser analisada estaticamente. Veja a página de detalhes.',
  },
  errors: {
    sourceUnknown: 'Não foi possível determinar a origem de instalação atual do plugin, então a substituição foi interrompida para proteger as permissões que você concedeu. O plugin não foi alterado. Desinstale o plugin e instale-o novamente para recuperar.',
    readFailed: 'Não é possível ler os registros de permissões de plugins agora. Nada foi alterado. Tente novamente em instantes.',
    corrupt: 'O arquivo de registros de permissões de plugins (plugin_permissions.json) está danificado e não pode ser lido, então a operação foi interrompida. Mova esse arquivo para fora da pasta de configuração do N.E.K.O e reinicie o N.E.K.O; os plugins podem pedir suas permissões novamente.',
    unsupportedVersion: 'Os registros de permissões de plugins foram salvos por uma versão mais recente do N.E.K.O e não podem ser lidos por esta versão, então a operação foi interrompida. Atualize o N.E.K.O para a versão mais recente e tente novamente.',
    writeFailed: 'Não foi possível salvar os registros de permissões de plugins. Nada foi alterado. Verifique o espaço livre em disco e a permissão de gravação na pasta de configuração do N.E.K.O e tente novamente.',
  },
  warnings: {
    budget_exceeded: 'A análise parou ao atingir seu limite de trabalho.',
    source_too_large: 'Um arquivo de origem excedeu o limite de tamanho da análise.',
    source_unreadable: 'Não foi possível analisar um arquivo de origem.',
    analysis_failed: 'Não foi possível concluir a análise em segundo plano.',
  },
  privacy: {
    title: "Privacidade",
  },
  capabilities: {
    conversations_read: {
      title: 'Ler conversas',
      description: 'Pode ler mensagens recentes e registros de conversas, ou observar novas conversas e respostas concluídas.',
    },
    memory_read: {
      title: 'Ler memória',
      description: 'Pode pesquisar a memória de longo prazo ou ler a memória do personagem e informações de persona.',
    },
    messages_push: {
      title: 'Enviar conteúdo ao N.E.K.O',
      description: 'Pode enviar mensagens, exibir cartões ou outro conteúdo, ou anexar conteúdo às respostas.',
    },
  },
}

export default { ...messages, capabilities: withPermissionLabels(messages.capabilities, labels) }
