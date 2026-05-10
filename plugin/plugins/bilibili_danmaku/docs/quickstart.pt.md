# Monitor de danmaku do Bilibili - Início rápido

Em poucos passos é possível monitorar em tempo real os danmaku de uma sala ao vivo do Bilibili, combinando-os com respostas de AI, uma LLM em segundo plano e ferramentas de leitura/escrita do Bilibili.

---

## 1. Estado da conexão

O cartão superior mostra em tempo real o estado de execução do plugin:

- **Luz de estado** — Cinza = desconectado, verde = conectado, amarelo = conectando, vermelho = erro
- **Recebidos** — Total acumulado de danmaku / presentes / SC recebidos
- **Filtrados** — Número de mensagens bloqueadas pelas regras de filtro
- **Buffer** — Danmaku aguardando envio agregado
- **Popularidade** — Valor de popularidade atual da sala ao vivo

---

## 2. Entrar com a conta do Bilibili

Clique na seção «Conta do Bilibili» para abrir o painel de login:

- **Login por QR** (recomendado): clique em «Login por QR» → escaneie o QR Code com o Bilibili App → aguarde a confirmação automática
- **Verificar credenciais**: consulta o estado de login, nome de usuário, UID e validade
- **Recarregar credenciais**: atualiza manualmente o estado de login
- **Sair**: remove as credenciais criptografadas locais e encerra a sessão

> A entrada manual de Cookie foi removida para evitar vazamento de informações sensíveis. O modo convidado permite receber danmaku, mas não enviá-los nem usar filtragem avançada.

---

## 3. Configurações da sala ao vivo

Na área «Configurações da sala ao vivo»:

1. **Insira o ID da sala** — obtenha-o da URL da sala, por exemplo `22925943` em `https://live.bilibili.com/22925943`
2. **Clique em «Trocar sala»** — para aplicar o novo ID
3. **Clique em «Iniciar monitoramento»** — conecta-se ao servidor de danmaku e começa a recebê-los

**Enviar danmaku ao vivo**:

- Com «Deixar a NEKO falar» desativado: envia o conteúdo do campo diretamente para a sala
- Com «Deixar a NEKO falar» ativado: envia o conteúdo e o contexto da sala para a NEKO, que gera a resposta conforme seu personagem antes de enviar

---

## 4. Configurações de envio para o AI

Controlam como os danmaku são enviados ao AI para processamento:

- **Intervalo de envio (segundos)** — Intervalo de envio dos danmaku agregados ao AI. Recomenda-se 10–30 segundos. Curto demais: o AI reage com frequência excessiva; longo demais: alta latência de resposta
- **Comprimento máximo do danmaku** — O Bilibili limita a 20 caracteres; o que ultrapassar na resposta do AI será cortado automaticamente. Recomenda-se manter 20
- **Nome do AI alvo** — Define qual AI recebe os danmaku. Se deixado em branco, envia ao AI padrão
- **UID / nome de usuário do dono no Bilibili** — Após configurar a conta do dono, a NEKO trata as mensagens dele de forma especial (resposta prioritária, tom distinto, etc.)

---

## 5. Fluxo de danmaku em tempo real

Mostra em tempo real os danmaku, presentes e SC recebidos:

- **Danmaku (Rosa)** — Danmaku de usuários comuns; mostra nome, nível e selo de fã
- **Presente (Dourado)** — Registro de presentes enviados pelos usuários
- **SC (Super Chat) (Verde)** — Mensagem em destaque paga

**Botões de controle**:

- **Auto-rolagem**: quando ativado, os novos danmaku rolam automaticamente para a área visível
- **Pausar / Continuar**: pausa ou retoma a atualização do fluxo de danmaku
- **Limpar**: limpa o histórico atual de danmaku exibido

---

## 6. Ferramentas de leitura do Bilibili

Leem dados públicos do Bilibili sem permissão de escrita — chamada segura. Preencha acima os campos «palavra-chave / BV / UID / ID dos favoritos» e clique no botão correspondente:

- **Buscar vídeos** — Buscar vídeos por palavra-chave. Obrigatório: Palavra-chave
- **Vídeos populares** — Lista de vídeos populares de todo o site
- **Pesquisas em alta** — Ranking de pesquisas em tempo real do Bilibili
- **Imperdíveis da semana** — Seleção semanal de imperdíveis
- **Ranking** — Ranking de uma categoria específica. Obrigatório: Ordem/categoria (`all`/`game`/`dance`, etc.)
- **Informações do vídeo** — Obter detalhes do vídeo. Obrigatório: BV
- **Comentários do vídeo** — Obter a lista de comentários do vídeo. Obrigatório: BV
- **Legendas do vídeo** — Obter legendas geradas por AI. Obrigatório: BV
- **Histórico de danmaku** — Obter o histórico de danmaku do vídeo. Obrigatório: BV
- **Informações do usuário** — Obter o perfil do usuário. Obrigatório: UID
- **Envios do usuário** — Obter a lista de vídeos enviados pelo usuário. Obrigatório: UID
- **Lista de favoritos** — Obter a lista de pastas de favoritos do usuário. Obrigatório: UID
- **Conteúdo dos favoritos** — Obter os vídeos dentro de uma pasta de favoritos. Obrigatório: media_id da pasta

Os resultados das chamadas aparecem de forma unificada na área «Resultados das ferramentas do Bilibili».

---

## 7. Ferramentas de escrita do Bilibili

Realizam operações de escrita no Bilibili. **Afetam sua conta**, use com cuidado:

- **Publicar comentário/resposta** — Comentar em um vídeo ou responder a um comentário. Obrigatório: BV + conteúdo do comentário; respostas requerem também o rpid do comentário
- **Publicar postagem** — Publicar uma nova postagem (dinâmica). Obrigatório: Texto da postagem (com imagens opcionais)
- **Enviar mensagem direta** — Enviar uma DM a um usuário. Obrigatório: UID do destinatário + conteúdo da mensagem

- **Deixar a NEKO falar**: quando ativado, comentários/postagens/DMs são primeiro gerados pela NEKO conforme seu personagem e só então enviados
- Os botões das ferramentas de escrita são vermelhos. Antes de chamá-los, confirme: conta logada, conteúdo correto e destinatário correto

---

## 8. Configurações da LLM em segundo plano

Quando ativada, os danmaku são agregados e enviados a uma LLM designada que gera prompts de orientação, fazendo a NEKO responder de forma mais natural ao clima da sala ao vivo.

**Configuração básica**:

- **Interruptor de ativação** — Liga/desliga a função de LLM em segundo plano
- **URL da API** — Endpoint compatível com OpenAI, p. ex. `https://api.openai.com/v1/chat/completions`
- **Nome do modelo** — P. ex. `gpt-4o-mini`, `deepseek-chat`
- **API Key** — Chave da API (oculta por padrão ao digitar; clique no ícone para visualizar)
- **Janela de agregação** — Quantos danmaku coletar antes de acionar o resumo da LLM. Recomenda-se 10–20
- **Tamanho máx. da amostra** — Capacidade máxima do pool de amostras de danmaku; ao ultrapassar, os mais antigos são descartados por ordem temporal

**Configurações avançadas** (clique em «Configurações avançadas» para expandir):

- **Nome da catgirl** — Substitui automaticamente o marcador `{name}` nos prompts
- **Contexto da base de conhecimento** — Personalidade, bordões e memes recorrentes do personagem; suporta o marcador `{name}`
- **Resumo do perfil do usuário** — Perfil básico do streamer/usuários como referência para a LLM
- **Modelo de prompt** — System Prompt personalizado; suporta os marcadores `{name}` e `{knowledge_context}`. Se deixado vazio, usa o modelo padrão

> Após configurar, clique em «Salvar configuração» e ative o interruptor. Clique em «Testar» para verificar a conectividade da API.

---

## Perguntas frequentes

**Falha no login por QR?** Confirme que o App está logado; o QR é válido por 2 minutos — atualize e escaneie novamente
**O monitor de danmaku não responde?** Verifique se o ID da sala está correto, se a rede está normal e se a conta está logada
**O AI não responde aos danmaku?** Confirme que o intervalo de envio está definido, a LLM em segundo plano está ativada e a API está corretamente configurada
**Falha ao enviar danmaku?** Confirme que está logado e tem permissão para enviar danmaku na sala (algumas restringem por nível de conta)
**Erros nas chamadas da API?** Verifique URL da API, nome do modelo e API Key; clique em «Testar» para diagnosticar

