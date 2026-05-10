# Início rápido

`sts2_autoplay` é usado para conectar ao N.E.K.O o estado local de *Slay the Spire 2* exposto pelo `STS2 AI Agent`. O plugin pode ler a situação, executar ações legais, jogar automaticamente conforme a estratégia, deixar a gatinha escolher uma única carta, enviar observações ao frontend e permitir que a gatinha envie orientações suaves a partir de tarefas em segundo plano para influenciar a próxima rodada de decisões.

## Tutorial de uso

### Obter o MOD

Use Git Clone:
```text
git clone https://github.com/CharTyr/STS2-Agent.git
```

### Instalar o Mod do jogo

No Steam, clique com o botão direito em *Slay the Spire 2* e selecione "Gerenciar -> Procurar arquivos locais".

O diretório padrão do jogo no Steam costuma ser parecido com:

```text
...\Steam\steamapps\common\Slay the Spire 2
```

Copie o mod `STS2 AI Agent` para a pasta `mods/` no diretório de Slay the Spire 2.

Se não houver pasta `mods` no diretório de Slay the Spire 2, crie-a manualmente.

```text
Usar mods pode causar perda de saves. Faça backup ou recupere via console (no menu principal de Slay the Spire pressione a tecla "~" e digite "unlock all" para desbloquear todos os personagens e dificuldades).
```

Após a instalação, o diretório deve parecer:

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### Iniciar o jogo e confirmar a interface

Primeiro inicie o jogo normalmente, para que o Mod seja carregado junto com o jogo.

Após o mod carregar, no NEKO ative a pata de gato, ligue o plugin, entre no painel de plugins e inicie manualmente o plugin de Slay the Spire.

### Comandos disponíveis

`jogue carta` / `jogo automático` / `complete a run` / `como estou indo?` / `pare`
`jogue uma carta` / `jogue esta carta` / `recomende uma carta` ... e assim por diante...

## Visão geral das funcionalidades

- Conecta ao serviço HTTP local do `STS2 AI Agent` e lê o estado do jogo.
- Suporta executar manualmente um passo, jogo semi-automático em segundo plano, pausar, retomar e parar.
- Suporta três modos de decisão: `full-program`, `half-program`, `full-model`.
- Suporta carregar documentos de estratégia por personagem; arquivos de estratégia ficam em `strategies/`.
- Suporta seleção única de carta pela gatinha: escolhe apenas uma carta dentre as ações `play_card` disponíveis, primeiro envia o motivo e depois executa.
- Suporta orientação suave da gatinha: o usuário ou a gatinha podem enviar orientação em linguagem natural, que será considerada na próxima rodada de decisão do LLM.
- Suporta relatórios de observação em segundo plano: envia ao frontend o andar atual, o combate, a mão, as intenções dos inimigos, os raciocínios do LLM, etc.
- Suporta proteção de segurança: pausa em vida baixa, redução de velocidade em chefes/ataques perigosos, retomada automática após recuperação de vida, estratégia de sobrevivência desesperada, maximização de benefício e pontuação de sinergia.

## Dependências

Este plugin depende do serviço HTTP local fornecido pelo Mod upstream `STS2 AI Agent`:

- Mod no jogo: `STS2AIAgent`
- Endereço de interface local padrão: `http://127.0.0.1:8080`
- Endereço de verificação de saúde: `http://127.0.0.1:8080/health`

Ou seja, este plugin funciona sob as seguintes premissas:

1. O Mod `STS2 AI Agent` está instalado em *Slay the Spire 2*.
2. Após o jogo ser iniciado, `http://127.0.0.1:8080/health` está acessível.
3. O plugin `sts2_autoplay` está habilitado no N.E.K.O.

## Configuração do plugin

Arquivo de configuração: `plugin.toml`

### Configuração básica

| Opção | Padrão | Descrição |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Endereço do Agent local de Spire. |
| `connect_timeout_seconds` | `5` | Tempo limite de conexão em segundos. |
| `request_timeout_seconds` | `15` | Tempo limite de requisição em segundos. |
| `poll_interval_idle_seconds` | `3` | Intervalo de polling em estado ocioso. |
| `poll_interval_active_seconds` | `1` | Intervalo de polling durante o jogo automático. |
| `action_interval_seconds` | `1.5` | Intervalo extra entre cada ação. |
| `post_action_delay_seconds` | `0.5` | Intervalo de espera após uma ação para a situação estabilizar. |
| `autoplay_on_start` | `false` | Se deve começar a jogar automaticamente após iniciar o plugin. |
| `semi_auto_autoplay` | `true` | Se deve criar contexto de tarefa semi-automática ao iniciar o jogo automático. |
| `mode` | `half-program` | Modo atual de jogo automático. |
| `character_strategy` | `defect` | Nome da estratégia do personagem, corresponde a `strategies/<name>.md`. |
| `max_consecutive_errors` | `3` | Número máximo de erros consecutivos; se exceder, é considerado desconexão. |
| `push_notifications` | `true` | Campo histórico reservado. |
| `event_stream_enabled` | `false` | Campo reservado, atualmente não ativado. |

### Modos de decisão

`mode` aceita os seguintes valores, bem como os aliases em chinês correspondentes:

| Modo | Alias chinês | Descrição |
| --- | --- | --- |
| `full-program` | `全程序` | Heurística puramente programática, sem chamadas ao modelo. |
| `half-program` | `半程序` | Primeiro pré-checagens programáticas, depois uma chamada ao modelo para decisão, com validação/fallback de legalidade. |
| `full-model` | `全模型` | Duas chamadas ao modelo: primeiro reasoning, depois final action; checagens programáticas no meio e validação final de legalidade. |

### Estratégias de personagem

`character_strategy` busca documentos de estratégia em `strategies/<name>.md`. Estratégias atualmente embutidas:

- `defect`
- `ironclad`
- `silent_hunter`
- `necrobinder`
- `regent`

Você pode adicionar arquivos Markdown em `strategies/` para estender estratégias. Por exemplo:

```text
strategies/my_strategy.md
```

E configure ou defina o parâmetro de entrada como:

```text
my_strategy
```

### Envio para o frontend e observação da gatinha

| Opção | Padrão | Descrição |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Se deve enviar ativamente as ações/erros do jogo automático ao frontend. |
| `llm_frontend_output_probability` | `0.15` | Probabilidade de envio para ações comuns; o intervalo é limitado a `0.0 ~ 1.0`. Erros são enviados forçosamente. |
| `neko_reporting_enabled` | `true` | Se deve enviar relatórios de observação da gatinha. |
| `neko_report_interval_steps` | `1` | A cada quantos passos do jogo automático enviar um relatório de observação; mínimo `1`. |
| `neko_commentary_enabled` | `true` | Se deve gerar narração ao vivo da gatinha em relatórios de observação. Quando desativado, relatórios estruturados ainda são enviados, mas `live_commentary.text` permanece vazio. |
| `neko_commentary_probability` | `0.65` | Probabilidade de gatilho de narração comum de baixa prioridade; o intervalo é limitado a `0.0 ~ 1.0`; cenários de alta prioridade como vida baixa, finalização ou ataque alto podem ignorar a probabilidade. |
| `neko_commentary_min_interval_seconds` | `4` | Intervalo mínimo em segundos para repetir a narração no mesmo cenário de baixa prioridade, para reduzir spam e repetições. |
| `neko_critical_commentary_always` | `true` | Se as narrações de urgência `critical` / `high` sempre são reproduzidas, por exemplo: agonia, finalização, ataque alto inimigo. |
| `neko_guidance_max_queue` | `50` | Tamanho máximo da fila de orientação suave da gatinha. |

Os relatórios de observação da gatinha trazem `report`, `neko_context`, `live_commentary`, `task` e outros metadata simplificados, ajudando o frontend ou a lógica de diálogo a reconhecer isso como uma "observação de processo" e não uma notificação de conclusão de tarefa. Para economizar tokens do usuário, o conteúdo enviado retém apenas a ação atual, vida, mão, inimigos, resumo tático, orientação consumida e resumo da tarefa.

`live_commentary` fornece ao frontend/TTS campos curtos de narração: `text`, `scene`, `mood`, `urgency`, `priority`, `tts`, `interrupt`, `tone`, `character_strategy`. As narrações são escolhidas aleatoriamente de um pool de templates por cena para reduzir repetições; também ajustam tendências conforme a estratégia do personagem, por exemplo: `defect` tende ao racional, `ironclad` ao estável. Atualmente cobre agonia, vida baixa, finalização, ataques inimigos chegando, defesa, combate normal, recompensas, loja, locais de descanso, eventos, mapa, bem como narrações em nível de evento como fim de combate, relíquias-chave e conclusão de escolha de rota.

### Proteção de segurança e ações autônomas

| Opção | Padrão | Descrição |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | Quando a proporção de vida atual cai abaixo deste valor, o jogo automático em segundo plano pausa autonomamente. |
| `neko_auto_safe_hp_threshold` | `0.5` | Após a vida recuperar a esta proporção, é permitido retomar automaticamente. |
| `neko_auto_dangerous_attack_threshold` | `20` | Redução automática de velocidade quando o dano recebido do inimigo atinge este valor e quebraria a defesa. |
| `neko_auto_resume_after_low_hp` | `true` | Se permite retomada automática após pausa por vida baixa, quando a vida se recupera. |
| `neko_desperate_enabled` | `true` | Se ativa a estratégia de sobrevivência desesperada. |
| `neko_desperate_hp_threshold` | `0.2` | Proporção de vida que dispara a estratégia de sobrevivência desesperada. |
| `neko_maximize_enabled` | `true` | Se ativa a seleção de cartas com maximização de benefício. |
| `neko_synergy_enabled` | `true` | Se ativa a pontuação de sinergia/combinação. |

As ações autônomas atuais incluem:

- `pause`: pausa em vida baixa, aguardando comandos do usuário ou da gatinha.
- `slow_down`: reduz temporariamente o intervalo de ações durante lutas de chefe ou ataques perigosos.
- `resume`: retoma após cumprir a condição de vida segura.

## Frases recomendadas para usuários comuns

Usuários comuns não precisam memorizar as entradas de baixo nível abaixo. Prefira passar as palavras originais do usuário para `sts2_neko_command`, e o plugin decide internamente se deve consultar status, dar conselhos, jogar uma carta de fato, executar um passo, iniciar o jogo automático, pausar, retomar, parar, revisar a jogada recente, responder dúvidas sobre o jogo automático, ou usar a frase como orientação suave durante o jogo automático.

Regras de interação recomendadas:

| Frase do usuário | Comportamento do plugin |
| --- | --- |
| `a spire conectou` / `qual a situação agora` | Apenas verificar conexão, status ou snapshot; não operar o jogo. |
| `como jogar este turno` / `qual carta é melhor jogar` | Apenas recomendar uma carta jogável e explicar o motivo; não jogar automaticamente. |
| `jogue uma carta para mim` / `escolha uma carta e jogue` | Após autorização explícita, escolher apenas uma das ações `play_card` e jogá-la. |
| `dê um passo por mim` / `execute um passo` | Após autorização explícita, executar uma ação legal, podendo incluir terminar o turno, escolher recompensa ou se mover no mapa. |
| `passe este andar para mim` / `jogue automaticamente um pouco` | Iniciar jogo semi-automático; condição de parada padrão: completar o andar atual. |
| `defenda primeiro` / `não seja ganancioso com dano` | Enquanto o jogo automático está rodando, vira orientação suave para a próxima rodada de decisões; quando não está rodando, pedir esclarecimento de forma conservadora, sem agir. |
| `como joguei agora` / `revise aquela carta` | Dar avaliação de jogada com base no snapshot leve mais recente; não operar o jogo. |
| `por que joga assim` / `o que está fazendo` | Enquanto o jogo automático está rodando, responder sobre a estratégia atual e o raciocínio da situação; sem operações extras. |
| `pause um pouco` / `continue` / `pare aí` | Pausar, retomar ou parar o jogo automático respectivamente. |

Padrão seguro: consulta não opera, frases vagas não executam ações perigosas; somente quando o usuário diz explicitamente "jogue por mim", "execute", "jogue automaticamente" ou "assuma" é que ações reais são feitas.

## Entradas do plugin

As entradas a seguir são expostas ao host e podem ser chamadas diretamente no N.E.K.O. Para cenários de usuários comuns, recomenda-se chamar primeiro `sts2_neko_command`; as outras entradas são principalmente interfaces de controle preciso para desenvolvedores.

### `sts2_neko_command`

Entrada-mestre de linguagem natural para Slay the Spire. Quando o usuário não especifica explicitamente uma ferramenta de baixo nível, prefira chamá-la.

Parâmetros:

- `command`: obrigatório, palavras originais do usuário. Exemplos: `como jogar este turno`, `jogue uma carta para mim`, `defenda primeiro`, `pause um pouco`.
- `scope`: opcional, padrão `auto`. Valores possíveis: `auto`, `status`, `advice`, `one_card`, `one_action`, `autoplay`, `control`, `guidance`, `review`, `question`, `chat`.
- `confirm`: opcional, padrão `false`. Usado para confirmar operações de alto risco como assumir o controle continuamente.

O retorno inclui `intent`, `action`, `executed`, `needs_confirmation`, `summary` e o `result` subjacente.

### `sts2_health_check`

Verifica se o serviço local de Spire Agent está disponível.

### `sts2_refresh_state`

Força uma atualização do estado atual de Spire.

### `sts2_get_status`

Obtém informações sobre estado de conexão, estado do jogo automático, modo atual, estratégia do personagem, tarefa semi-automática, erros recentes, ações recentes, etc.

### `sts2_get_snapshot`

Obtém o snapshot do jogo mais recentemente cacheado e as ações atualmente executáveis.

### `sts2_step_once`

Executa um passo conforme a estratégia atual.

### `sts2_play_one_card_by_neko`

Permite que a gatinha escolha e jogue uma carta.

Parâmetros:

- `objective`: opcional, objetivo de autorização do usuário. Exemplo: `escolha uma carta e jogue por mim`.

Comportamento:

1. Lê o jogador atual, a mão, os inimigos e as ações legais.
2. Mantém apenas as ações `play_card`.
3. Permite que o modo/estratégia atual escolha uma carta.
4. Primeiro envia ao frontend "qual carta planeja jogar e por quê".
5. Revalida que a ação ainda é legal.
6. Joga a carta e envia a observação de conclusão.

Se atualmente não houver cartas jogáveis, retorna `idle` e envia o motivo da falha.

### `sts2_start_autoplay`

Inicia o loop de jogo semi-automático em segundo plano.

Parâmetros:

- `objective`: opcional, objetivo de autorização do usuário. Exemplo: `passe este andar para mim`.
- `stop_condition`: condição de parada, padrão `current_floor`.

`stop_condition` aceita:

- `current_floor`: termina ao completar o andar atual ou entrar no próximo.
- `current_combat` / `combat`: termina quando, durante a tarefa, tiver entrado em combate e depois saído.
- `manual` / `none`: não termina automaticamente, requer parada manual.

Após iniciar, o plugin cria um contexto de tarefa semi-automática e envia um evento de início de tarefa ao frontend. Ao concluir a tarefa, é enviado `semi_auto_task_completed`.

### `sts2_pause_autoplay`

Pausa o jogo automático.

### `sts2_resume_autoplay`

Retoma um jogo automático pausado cuja tarefa em segundo plano ainda existe. Se a tarefa em segundo plano não existe mais, retorna `idle` com segurança e não reinicia implicitamente o jogo automático.

### `sts2_stop_autoplay`

Para o jogo automático e limpa o contexto da tarefa semi-automática.

### `sts2_get_history`

Obtém o histórico recente de ações e estados.

Parâmetros:

- `limit`: número de entradas a retornar, padrão `20`, intervalo limitado a `1 ~ 100`.

### `sts2_send_neko_guidance`

Envia orientação suave da gatinha para o jogo automático em segundo plano. A orientação entra na fila e é injetada no contexto na próxima rodada de decisão do LLM.

Parâmetros:

- `content`: obrigatório, conteúdo de orientação em linguagem natural. Exemplo: `defenda primeiro, sem pressa para causar dano`.
- `step`: opcional, número do passo correspondente.
- `type`: opcional, padrão `soft_guidance`.

### `sts2_set_mode`

Define o modo de jogo automático.

Parâmetros:

- `mode`: aceita `full-program` / `全程序`, `half-program` / `半程序`, `full-model` / `全模型`.

### `sts2_set_character_strategy`

Define o nome da estratégia do personagem.

Parâmetros:

- `character_strategy`: após normalização do nome, é correspondido com `strategies/<name>.md`. Por exemplo, `defect` corresponde a `strategies/defect.md`.

### `sts2_set_speed`

Define parâmetros de velocidade e os escreve de volta no `plugin.toml` local.

Parâmetros:

- `action_interval_seconds`
- `post_action_delay_seconds`
- `poll_interval_active_seconds`

## Modos de uso típicos

### Verificar conexão

1. Inicie *Slay the Spire 2*.
2. Confirme que `http://127.0.0.1:8080/health` está acessível.
3. No N.E.K.O, chame `sts2_health_check`.

### Executar manualmente um passo

Chame:

```text
sts2_step_once
```

O plugin escolherá e executará uma ação legal com base no `mode` e `character_strategy` atuais.

### Deixar a gatinha jogar uma carta

O usuário pode dizer à gatinha algo como:

```text
escolha uma carta e jogue por mim
```

O host deve chamar:

```text
sts2_play_one_card_by_neko
```

O plugin escolhe apenas dentre as cartas atualmente jogáveis, sem escolher fim de turno, mapa, recompensa ou outras ações.

### Deixar a gatinha ajudar a passar de andar

O usuário pode dizer:

```text
passe este andar para mim
```

O host deve chamar:

```text
sts2_start_autoplay
```

Parâmetros recomendados:

```json
{
  "objective": "passe este andar para mim",
  "stop_condition": "current_floor"
}
```

Durante a execução da tarefa, eventos de observação são apenas relatórios de progresso e não representam conclusão. Somente ao receber o evento de conclusão da tarefa semi-automática é que se deve dizer ao usuário que este andar foi concluído.

### Orientação durante o jogo

Durante o jogo automático, o usuário ou a gatinha podem enviar orientação:

```text
defenda primeiro, não tome dano demais
```

Deve-se chamar:

```text
sts2_send_neko_guidance
```

Parâmetros recomendados:

```json
{
  "content": "defenda primeiro, não tome dano demais",
  "type": "soft_guidance"
}
```

A orientação será considerada na próxima rodada de decisão do LLM. O modo `full-program` não depende do modelo, então o impacto da orientação suave é limitado.

## Eventos enviados ao frontend

O plugin envia as seguintes categorias de eventos pelo canal de mensagens do host. Exceto início/conclusão de tarefa, erros e prévias de carta única, observações comuns tentam usar texto curto e metadata simplificada para reduzir consumo de tokens do usuário.

| Tipo de evento | Descrição |
| --- | --- |
| `action` | Observação comum de ação do jogo automático, controlada por probabilidade. |
| `error` | Erro do jogo automático, envio forçado. |
| `neko_report` | Relatório completo de observação da gatinha, incluindo situação atual, mão, inimigos, resumo tático e raciocínio do modelo. |
| `neko_card_task_planned` | A tarefa de carta única da gatinha planeja jogar uma carta específica. |
| `neko_card_task_completed` | Tarefa de carta única da gatinha executada. |
| `neko_card_task_failed` | A tarefa de carta única da gatinha não pôde ser executada. |
| `semi_auto_task_started` | Tarefa semi-automática iniciada. |
| `semi_auto_task_completed` | Tarefa semi-automática concluída. |
| `neko_autonomous_action` | O sistema pausou, reduziu velocidade ou retomou autonomamente. |

Observação: `neko_report` é uma observação de processo, não uma notificação de conclusão de tarefa. O frontend ou a lógica de diálogo não devem descrever uma ação de passo único, jogar carta, fim de turno ou atualização de estado como "tarefa concluída", "chefe derrotado", "combate encerrado" ou "run completada". Se a gatinha quiser influenciar a próxima rodada de decisões, deve-se chamar `sts2_send_neko_guidance`; se quiser controlar o fluxo de forma rígida, deve-se chamar as entradas de pausa, retomada ou parada.

## Solução de problemas comuns

### Falha de conexão ao chamar uma entrada do plugin

Verifique primeiro:

- Se o jogo foi iniciado.
- Se o Mod `STS2 AI Agent` foi colocado corretamente em `mods/` do jogo.
- Se `http://127.0.0.1:8080/health` está acessível.
- Se o `base_url` em `plugin.toml` está correto.

### `http://127.0.0.1:8080/health` não abre

Verifique prioritariamente:

1. Se o jogo realmente foi iniciado.
2. Se `STS2AIAgent.dll`, `STS2AIAgent.pck` e `mod_id.json` foram todos copiados para o diretório `mods/` do jogo.
3. Se os nomes dos arquivos foram renomeados pelo sistema, duplicados ou colocados no diretório errado.
4. Se você está operando no diretório de jogo do Steam e não no diretório do repositório upstream.
5. Se algum firewall ou software de segurança está bloqueando a porta local.

### O jogo automático funciona, mas o frontend não recebe mensagens

Verifique:

- Se `llm_frontend_output_enabled` está `true`.
- Se `llm_frontend_output_probability` está muito baixo.
- Se `neko_reporting_enabled` está `true`.
- Durante depuração, defina `llm_frontend_output_probability` para `1` primeiro.
- Se o frontend do host está recebendo as mensagens enviadas pelo plugin.

### A orientação da gatinha durante o jogo não tem efeito evidente

Verifique:

- Se o modo atual é `half-program` ou `full-model`.
- Se `sts2_send_neko_guidance` retorna `ok`.
- Se o conteúdo da orientação é específico o suficiente, por exemplo: "priorize defesa", "ataque primeiro o inimigo de menor vida", "guarde a poção".
- Se as ações legais atuais realmente conseguem cumprir a orientação.

### A tarefa semi-automática demora a concluir

Verifique `stop_condition`:

- Se for `manual` / `none`, a tarefa não conclui automaticamente, é preciso chamar `sts2_stop_autoplay`.
- Se for `current_combat`, a tarefa conclui quando, durante a tarefa, tiver entrado em combate e depois saído.
- Se for `current_floor`, geralmente conclui ao completar o andar atual ou entrar no próximo.

Você pode chamar `sts2_get_status` para ver `autoplay.task`.

### Travadas em salas de evento, pop-ups ou estados de transição

A versão atual já trata eventos, pop-ups e estados de transição; ações prioritárias incluem:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

Se ainda assim travar, primeiro use `sts2_get_snapshot` para ver o `screen` atual e `available_actions`.

### O jogo automático pausa de repente ou fica mais lento

Pode ter sido acionada a proteção de segurança:

- Quando a proporção de vida cai abaixo de `neko_auto_low_hp_threshold`, pausa.
- Em lutas de chefe ou ataques perigosos, reduz velocidade.
- Se `neko_auto_resume_after_low_hp` for `true`, ao recuperar vida até `neko_auto_safe_hp_threshold` pode retomar automaticamente.

Você pode chamar `sts2_get_status` para verificar o estado, ou chamar `sts2_resume_autoplay` / `sts2_stop_autoplay` para tratar.
