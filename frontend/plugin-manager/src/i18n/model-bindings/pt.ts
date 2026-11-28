import bindingErrors from '../model-binding-errors/pt'
import type { ModelBindingsMessages } from './en-US'

const messages: ModelBindingsMessages = {
  bindingErrors,
  title: 'Vínculos de API de modelos',
  manage: 'Gerenciar slots e uso',
  description: 'Escolha um slot de modelo salvo para cada finalidade declarada por este plugin. As alterações são salvas imediatamente.',
  ready: 'Todas as finalidades obrigatórias estão vinculadas',
  notReady: 'Algumas finalidades obrigatórias precisam de um vínculo',
  readinessHint: 'Este status apenas confirma os vínculos obrigatórios. Não verifica a disponibilidade do provedor nem controla a inicialização do plugin.',
  boundSummary: '{bound} de {total} vinculados',
  collapse: 'Recolher',
  expand: 'Expandir',
  required: 'Obrigatório',
  optional: 'Opcional',
  unbound: 'Sem vínculo',
  incompatible: 'Faltam recursos',
  noSlots: 'Ainda não há slots de modelo. Adicione um em Plugin API e atualize esta seção.',
  loadFailed: 'Não foi possível carregar os vínculos. Tente novamente.',
  saveFailed: 'Não foi possível salvar o vínculo. O vínculo anterior continua sendo exibido.',
  capabilities: { text: 'Texto', image_input: 'Entrada de imagens', tool_calling: 'Chamadas de ferramentas', streaming: 'Streaming' },
  status: { bound: 'Vinculado', unbound: 'Nenhum slot selecionado', incompatible: 'O slot vinculado não atende mais aos requisitos desta finalidade' },
}

export default messages
