# Início rápido

`sts2_autoplay` é usado para conectar ao N.E.K.O o estado local de *Slay the Spire 2* exposto por `STS2 AI Agent`. O plugin pode ler a situação atual, executar ações legais, jogar automaticamente de acordo com a estratégia, permitir que a gatinha escolha uma única carta, enviar informações de observação ao frontend e permitir que a gatinha envie orientações suaves em tarefas em segundo plano para influenciar a próxima rodada de decisões.

## Tutorial de uso

### Obter o MOD

Usando Git:
```text
https://github.com/CharTyr/STS2-Agent/releases
```

### Instalar o Mod do jogo

No Steam, clique com o botão direito em *Slay the Spire 2* e escolha Gerenciar -> Procurar arquivos locais.

O diretório padrão do jogo no Steam geralmente é parecido com:

```text
...\Steam\steamapps\common\Slay the Spire 2
```

Copie o mod `STS2 AI Agent` para a pasta `mods/` dentro do diretório do jogo.

Se não houver uma pasta `mods` dentro do diretório de *Slay the Spire 2*, crie-a manualmente.

```text
Usar mods pode causar perda de saves. Faça backup ou use o console para se compensar (no menu principal de Slay the Spire, pressione a tecla "~", digite "unlock all" e todos os personagens e dificuldades serão desbloqueados).
```

Após a instalação, a estrutura do diretório deve ficar semelhante a:

```text
Slay the Spire 2/
  mods/
    STS2AIAgent.dll
    STS2AIAgent.pck
    mod_id.json
```

### Configurar a porta do mod

Por padrão, o mod escuta na porta `8080`. Ela é uma porta bastante usada e pode estar ocupada por outro software.

Você pode abrir as Variáveis de ambiente e criar uma nova variável de sistema/global chamada `STS2_API_PORT`. Escolha uma porta menos comum, como `18080` ou `38080`.

Exemplo: nome da variável `STS2_API_PORT`, valor `18080`.

Observação: você pode abrir as Variáveis de ambiente rapidamente assim:

- Pressione Win + R para abrir a caixa Executar.

- Digite o comando abaixo e pressione Enter:

```text
rundll32 sysdm.cpl,EditEnvironmentVariables
```

### Iniciar o jogo e confirmar a interface

Primeiro inicie o jogo normalmente para que o Mod seja carregado junto com o jogo.

Na primeira vez que você alternar para o modo com mod, o jogo pode fechar uma vez. Isso é normal; basta iniciar novamente.

Depois que o mod estiver carregado, no N.E.K.O ative o Cat Paw, ligue o plugin, entre no painel do plugin e inicie manualmente o plugin de Slay the Spire.

### Comandos disponíveis

【Jogar carta】【Autojogar por mim】【Passar um andar】【Como foi a jogada】【Parar】
【Jogar uma carta】【Jogar uma carta específica】【Recomendar uma carta】…… e frases semelhantes.

## Contato

Se houver qualquer problema, envie por e-mail os logs de execução do jogo e do N.E.K.O para zhaijiunknown@outlook.com.

Logs do jogo:
```text
%AppData%\SlayTheSpire2\logs
```

Logs do N.E.K.O:
```text
Sua pasta de usuário\AppData\Local\N.E.K.O\logs
```

## Visão geral dos recursos

- Conecta-se ao serviço HTTP local `STS2 AI Agent` e lê o estado atual da run.
- Permite consultar a situação atual de uma vez só: atualiza o estado uma vez e devolve juntos o snapshot, o resumo da situação e o pacote de sincronização da neko.
- Permite controlar o autoplay em segundo plano: iniciar, pausar, retomar, parar e também executar diretamente o próximo passo sugerido.
- Inclui modo de acompanhamento, que pode observar a partida com você e enviar comentários, lembretes e observações sem interromper o fluxo principal.
- Permite ajustar a estratégia em linguagem natural: uma única frase do usuário pode virar um override de preferência ligado ao evento ou inimigo da cena atual.
- Permite ver o próximo movimento sugerido antes de decidir se deseja realmente executá-lo.
- Inclui proteções de segurança, como pausa com HP baixo, desaceleração diante de ataques perigosos, recuperação da velocidade quando o perigo passa e, quando for seguro, retomada do autoplay.
- Também suporta envios passivos ao frontend: sincronização de estado, observações, pistas de acompanhamento e feedback de controle.

## Configuração deste plugin

Arquivo de configuração: `plugin.toml`

### Configuração básica

| Item de configuração | Padrão | Descrição |
| --- | --- | --- |
| `base_url` | `http://127.0.0.1:8080` | Endereço do Agent local do Spire. |
| `connect_timeout_seconds` | `5` | Tempo limite de conexão, em segundos. |
| `request_timeout_seconds` | `15` | Tempo limite da requisição, em segundos. |
| `poll_interval_idle_seconds` | `3` | Intervalo de polling quando o plugin está ocioso. |
| `poll_interval_active_seconds` | `1` | Intervalo de polling enquanto o autoplay está em execução. |
| `action_interval_seconds` | `1.5` | Pausa extra entre ações. |
| `post_action_delay_seconds` | `0.5` | Espera após cada ação para deixar a situação estabilizar. |
| `autoplay_on_start` | `false` | Se o plugin deve começar a jogar automaticamente ao iniciar. |
| `character_strategy` | `defect` | Estratégia padrão; em execução, ela é associada ao contexto de estratégia que melhor combina com a situação atual. |
| `max_consecutive_errors` | `3` | Quantidade máxima de erros consecutivos antes de considerar a conexão em estado ruim. |

### Envios ao frontend e observação de acompanhamento

| Item de configuração | Padrão | Descrição |
| --- | --- | --- |
| `llm_frontend_output_enabled` | `true` | Se ações e erros do autoplay podem ser enviados ao frontend. |
| `llm_frontend_output_probability` | `1.0` | Probabilidade de envio das mensagens normais de ação. Erros e alguns feedbacks de controle importantes ainda podem ser forçados. |
| `autoplay_push_probability` | `0.5` | Probabilidade de enviar sincronizações normais da partida quando o modo de acompanhamento não está ativo. |
| `companion_push_probability` | `0.7` | Probabilidade de enviar sincronizações normais enquanto o modo de acompanhamento está ativo. |
| `neko_reporting_enabled` | `true` | Se a capacidade de observação da neko fica habilitada. |
| `neko_report_interval_steps` | `1` | A cada quantos passos do autoplay o conteúdo de observação é reorganizado. |
| `neko_report_hud_enabled` | `true` | Se esse conteúdo de observação realmente é enviado ao HUD/canal de mensagens do frontend. |
| `neko_commentary_enabled` | `true` | Se comentários e lembretes de acompanhamento podem ser gerados. |
| `neko_commentary_probability` | `0.65` | Probabilidade de disparo de comentários normais de baixa prioridade. |
| `neko_commentary_min_interval_seconds` | `4` | Intervalo mínimo antes de repetir comentários parecidos; serve para reduzir spam. |
| `neko_critical_commentary_always` | `true` | Se alertas de prioridade alta devem sempre ser emitidos. |
| `neko_guidance_max_queue` | `50` | Limite interno da fila para contexto relacionado a guias e preferências. |

### Proteção automática e controle de ritmo

| Item de configuração | Padrão | Descrição |
| --- | --- | --- |
| `neko_auto_low_hp_threshold` | `0.3` | Se a proporção de HP cair abaixo deste valor, o autoplay tenderá a pausar. |
| `neko_auto_safe_hp_threshold` | `0.5` | Quando o HP volta a esta faixa, a situação pode voltar a ser tratada como segura. |
| `neko_auto_dangerous_attack_threshold` | `20` | Se a intenção de dano inimiga atingir este limiar, a proteção de desaceleração pode entrar em ação. |
| `neko_auto_resume_after_low_hp` | `true` | Se é permitido retomar automaticamente depois de uma pausa por HP baixo quando a situação volta a ser segura. |
| `neko_desperate_enabled` | `true` | Se a postura de sobrevivência desesperada deve ser ativada. |
| `neko_desperate_hp_threshold` | `0.2` | Proporção de HP que dispara essa postura de sobrevivência. |
| `neko_maximize_enabled` | `true` | Se uma tendência mais forte a maximizar valor deve ser ativada. |

## Formas recomendadas para usuários comuns

Usuários comuns não precisam decorar parâmetros de baixo nível. O mais confortável é passar a frase original para as entradas de nível mais alto que continuam ativas e deixar que o plugin decida se o pedido é para consultar a situação, ajustar a estratégia ou executar o próximo passo sugerido.

Interpretação recomendada:

| O que o usuário quer dizer | Capacidade mais adequada |
| --- | --- |
| `o que está acontecendo agora` | `sts2_get_status` |
| `me mostra a situação atual` | `sts2_read_state` |
| `deixa ela continuar jogando` / `pausa o autoplay por agora` / `faz ela continuar` / `não precisa mais jogar sozinha` | entradas de controle de autoplay |
| `ajusta a estratégia com base nisso: neste evento prefiro a rota de menor custo` | `sts2_apply_user_override` |
| `me mostra o que ela quer fazer depois` | `sts2_get_planned_operation` |
| `segue a sugestão` | `sts2_execute_planned_operation` |
| `liga o modo de acompanhamento` / `desliga o modo de acompanhamento` | entradas de controle do modo de acompanhamento |

Fluxo recomendado:
1. Primeiro veja a situação atual.
2. Depois confira o que ela quer fazer em seguida.
3. Se quiser mudar o critério, ajuste a estratégia com uma frase.
4. Por fim, decida se executa esse passo ou se deixa o autoplay continuar.

## Entradas do plugin

Estas são as capacidades públicas que realmente continuam expostas pelo script principal. Os nomes visíveis foram levados para um tom mais natural, mas os `entry id` internos seguem estáveis para não quebrar a integração do host.

### `sts2_health_check`

Verifica se o serviço local do Agent do Spire está realmente acessível. É uma boa primeira checagem ao iniciar, integrar ou investigar erros.

### `sts2_get_status`

Mostra o estado geral do runtime: se a conexão está saudável, em que tela a run está, se o autoplay está rodando, se está em standby e como estão o modo atual e os erros recentes.

### `sts2_read_state`

Atualiza a situação atual uma vez e devolve três camadas juntas:
- o snapshot atual
- o resumo atual da situação
- o pacote atual de sincronização da neko

Serve quando você quer ver tudo de uma vez antes de decidir o próximo movimento.

### `sts2_set_standby`

Liga ou desliga o modo standby. Em standby, as ações deixam de ser executadas, mas a organização do estado e a preparação de sincronização continuam disponíveis.

### `sts2_start_autoplay`

Deixa ela continuar jogando. Inicia o autoplay em segundo plano e faz a situação atual seguir em frente por conta própria.

### `sts2_pause_autoplay`

Pausa o autoplay por enquanto. É útil quando você quer assumir o controle manualmente ou ajustar a estratégia antes do próximo movimento.

### `sts2_resume_autoplay`

Faz ela voltar a jogar a partir do ponto em que estava pausada.

### `sts2_stop_autoplay`

Faz ela parar de jogar sozinha. Encerra completamente o autoplay em segundo plano e devolve o controle para você.

### `sts2_enable_companion_mode`

Liga o modo de acompanhamento. Com ele ativo, o plugin passa a organizar a situação com mais frequência e a enviar observações, comentários e lembretes quando fizer sentido.

### `sts2_disable_companion_mode`

Desliga o modo de acompanhamento. Ele remove apenas a camada de comentários, mas mantém a leitura básica de estado e o controle do autoplay.

### `sts2_apply_user_override`

Ajusta a estratégia a partir de uma única nota do usuário. Ele interpreta sua frase no contexto da cena atual e a converte em um override ligado ao evento ou inimigo correspondente.

Esta entrada também aplica uma proteção extra:
- se o autoplay estiver rodando, **ele o pausa primeiro**
- depois de atualizar a estratégia, ele avisa que **se você quiser continuar, deve retomar o autoplay manualmente**
- ele não continua a run por conta própria até que você decida isso explicitamente

### `sts2_get_planned_operation`

Mostra o que ela quer fazer em seguida. É a opção certa se você quer inspecionar a próxima jogada antes de executá-la.

### `sts2_execute_planned_operation`

Executa diretamente o próximo passo sugerido.

## Eventos enviados ao frontend

O plugin envia vários tipos de informação passiva pelo canal de mensagens do host, organizados principalmente em três blocos:

1. **Sincronização de estado e situação**
   - resumo da situação atual
   - resumo da recomendação atual
   - informações de sincronização enquanto o modo de acompanhamento está ativo

2. **Feedback de controle do autoplay**
   - autoplay iniciado
   - pausado / retomado / parado
   - aviso de que você precisa retomar manualmente após atualizar a estratégia

3. **Avisos de acompanhamento e proteção**
   - comentários de acompanhamento
   - lembretes de risco
   - pausa por vida baixa
   - desaceleração por ataque perigoso
   - recuperação da velocidade ou retomada do autoplay quando o perigo já passou

Esses envios usam semântica passiva por padrão e não devem interromper à força a conversa principal. A frequência deles também depende de ajustes como:
- `autoplay_push_probability`
- `companion_push_probability`
- `neko_commentary_probability`
- `neko_report_hud_enabled`

## Solução de problemas mais comuns

### Falha de conexão ao chamar uma entrada do plugin

Primeiro confira:

- se o jogo já foi iniciado
- se o mod `STS2 AI Agent` foi colocado corretamente em `mods/`
- se `http://127.0.0.1:8080/health` está acessível
- se `base_url` em `plugin.toml` está correto

### Não é possível abrir `http://127.0.0.1:8080/health`

Verifique nesta ordem:

1. se o jogo realmente está em execução
2. se `STS2AIAgent.dll`, `STS2AIAgent.pck` e `mod_id.json` foram todos copiados para `mods/`
3. se os nomes dos arquivos foram alterados, duplicados ou colocados em pasta errada
4. se você está operando na pasta do jogo da Steam, e não no repositório upstream
5. se algum firewall ou software de segurança está bloqueando a porta local

### O autoplay roda, mas o frontend não recebe mensagens

Confira:

- se `llm_frontend_output_enabled` está em `true`
- se `llm_frontend_output_probability` não está baixo demais
- se `neko_reporting_enabled` está em `true`
- durante a integração, você pode subir temporariamente `llm_frontend_output_probability` para `1`
- se o frontend do host está realmente recebendo as mensagens do plugin

### A orientação no meio da partida não parece surtir efeito

Confira:

- se o plugin não está em standby no momento
- se `sts2_send_neko_guidance` devolveu `ok`
- se a orientação é específica o suficiente, por exemplo `priorize defesa`, `ataque primeiro o inimigo com menos vida`, `guarde a poção`
- se as ações legais atuais realmente permitem cumprir essa orientação

### A tarefa semiautomática não termina

Confira `stop_condition`:

- se for `manual` / `none`, a tarefa não termina sozinha e você precisa chamar `sts2_stop_autoplay`
- se for `current_combat`, ela termina depois que durante a tarefa entrou em combate e depois saiu dele
- se for `current_floor`, normalmente termina ao limpar o andar atual ou ao entrar no próximo

Você pode usar `sts2_get_status` para inspecionar `autoplay.task`.

### Fica preso em eventos, pop-ups ou estados de transição

A versão atual já lida com eventos, pop-ups e estados de transição. As ações prioritárias incluem:

- `confirm_modal`
- `dismiss_modal`
- `choose_event_option`
- `proceed`

Se ainda assim travar, primeiro use `sts2_read_state` para revisar o `screen` e `available_actions` atuais.

### O autoplay para ou fica lento de repente

Pode ser que alguma proteção de segurança tenha sido ativada:

- ele pausa se a proporção de HP cair abaixo de `neko_auto_low_hp_threshold`
- ele desacelera em Boss ou diante de ataques perigosos
- se `neko_auto_resume_after_low_hp` estiver em `true`, ele pode retomar quando o HP voltar a `neko_auto_safe_hp_threshold`

Você pode usar `sts2_get_status` para verificar o estado, ou chamar `sts2_resume_autoplay` / `sts2_stop_autoplay` para intervir.
