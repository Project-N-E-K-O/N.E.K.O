# Plugin de resposta automática do QQ

Este plugin conecta o QQ por meio do protocolo OneBot e fornece respostas automáticas inteligentes de acordo com o nível de permissões. Dá suporte a chats privados e grupos, com integração de conversa por IA.

## Inicialização e guia

1. Baixe uma implementação de OneBot (recomenda-se NapCat). Os exemplos abaixo usam NapCat.
   Abra:
   ```text 
   https://github.com/NapNeko/NapCatQQ/releases
   ```
   Escolha qualquer pacote para baixar (o recomendado é `NapCat.Shell.zip`).
2. Inicie o NapCat e abra a página de configuração do NapCat.
3. Na barra lateral esquerda, selecione **Configuração de rede**.
4. Adicione um **Servidor WS**, ative-o e salve.
5. Inicie o plugin.
6. Volte uma vez.
7. Abra novamente o painel deste plugin.
8. Siga a guia passo a passo.

Observações sobre o fluxo atual:
- Durante `startup`, o plugin cria automaticamente o arquivo de configuração de negócio se ele ainda não existir.
- O NapCat é iniciado primeiro a partir do diretório de execução configurado; se estiver vazio, usa o diretório padrão.
- Quando o NapCat gera o QR code de login, ele é copiado para o diretório estático do plugin; na interface, clique em **Atualizar QR code** para sincronizar e mostrar a imagem mais recente.
- A interface frontend permite gerenciar diretamente:
  - Endereço OneBot / Token / PATH
  - Início do NapCat em primeiro plano ou em segundo plano
  - Usuários confiáveis / grupos confiáveis
  - Início / parada da resposta automática

## Recursos

- Suporte ao protocolo OneBot via NapCat
- Gestão multinível de permissões de usuário: `admin`, `trusted`, `normal`
- Controle multinível de permissões de grupo: `trusted`, `open`, `normal`
- Integração com conversas de IA via OmniOfflineClient
- Sincronização de memória para conversas privadas do administrador
- Encaminhamento probabilístico de mensagens normais para o administrador
- Modo de grupo aberto com resposta sem `@`
- Gestão de apelidos para usuários confiáveis
- Reconexão automática de WebSocket com backoff exponencial

## Configuração

Recomenda-se configurar tudo pela interface do plugin em **Configuração do serviço QQ OneBot**. O arquivo de configuração de negócio é criado automaticamente na primeira inicialização. Os principais campos são:

- `onebot_url`: endereço WebSocket do OneBot, padrão `ws://127.0.0.1:3001`
- `token`: token de acesso do OneBot
- `napcat_directory`: diretório de execução do NapCat
- `show_napcat_window`: `true` para início em primeiro plano com console visível; `false` para início em segundo plano
- `trusted_users`: lista de usuários confiáveis
- `trusted_groups`: lista de grupos confiáveis
- `normal_relay_probability`: probabilidade de encaminhar mensagens normais ao dono
- `truth_reply_probability`: probabilidade de responder proativamente em grupos open

### Tabela de configuração

| Chave | Tipo | Descrição |
|------|------|-----------|
| `onebot_url` | string | Endereço WebSocket do serviço OneBot |
| `token` | string | Token de acesso do serviço OneBot (se exigido) |
| `trusted_users` | array | Lista de usuários confiáveis com número QQ, nível de permissão e apelido |
| `trusted_groups` | array | Lista de grupos confiáveis com número do grupo e nível de permissão |
| `normal_relay_probability` | float | Probabilidade de encaminhar mensagens privadas/de grupo normais ao dono |
| `truth_reply_probability` | float | Probabilidade de resposta direta em grupos `open` sem `@` |

### Campos de probabilidade dentro dos grupos confiáveis

Cada item em `trusted_groups` pode incluir dois campos opcionais de probabilidade além de `group_id` e `level`:

- `normal_relay_probability`: só se aplica quando `level = normal`. Quando uma mensagem do grupo entra na rota de “encaminhar ao mestre”, esse valor substitui a probabilidade global padrão de encaminhamento para aquele grupo.
- `open_reply_probability`: só se aplica quando `level = open`. Em grupos abertos, quando ninguém menciona o bot com `@`, esse valor substitui a probabilidade global padrão de resposta proativa para aquele grupo.

Observações:
- Ambos os valores devem ser decimais entre `0` e `1`.
- Se ficarem em branco, o plugin usa a configuração global padrão.
- Grupos `trusted` não usam nenhum desses dois campos, porque só entram no fluxo normal de resposta quando o bot recebe um `@` explícito.

## Níveis de permissão

### Permissões de usuário

| Nível | Significado | Comportamento |
|------|-------------|---------------|
| `admin` | Administrador | Resposta direta no privado, sincronização com memória, tratado como "mestre" |
| `trusted` | Usuário confiável | Resposta direta no privado, sem sincronização de memória, apelido permitido |
| `normal` | Usuário normal | Não responde diretamente, pode encaminhar ao administrador por probabilidade |
| `none` | Não autorizado | Mensagem ignorada |

### Permissões de grupo

| Nível | Significado | Comportamento |
|------|-------------|---------------|
| `trusted` | Grupo confiável | Responde apenas quando o bot recebe `@` |
| `open` | Grupo aberto | Pode responder sem `@`, reutiliza contexto temporário e ficha do personagem, mas não grava em memória |
| `normal` | Grupo normal | Não responde diretamente, pode encaminhar ao administrador |
| `none` | Não autorizado | Mensagem ignorada |

## Informações adicionais

### 1. Envio proativo de mensagens

Você pode chamar entradas do painel para fazer o bot gerar primeiro um texto no estilo da IA e depois enviá-lo proativamente ao usuário ou grupo escolhido.

Observações:
- `message` agora representa uma instrução para a IA, não o texto final enviado sem alterações.
- A personalidade do personagem e a configuração do modelo existentes são reutilizadas.
- Em envios proativos privados, o contexto de memória pode ser lido, mas esse envio não é escrito de volta na memória.
- Envios proativos para grupos não são escritos na memória.
- A entrada privada usa `target`: ele pode ser um ID numérico do QQ ou um apelido já configurado na lista de usuários confiáveis.
- `group_id` deve ser uma cadeia numérica.
- `message` não pode ser vazio.
- A resposta automática deve estar iniciada e o OneBot conectado; caso contrário, a entrada falha imediatamente.

### 2. Parar o plugin

Ao parar o plugin, acontece o seguinte:
- Encerramento da conexão WebSocket
- Limpeza dos recursos em execução

## Perguntas frequentes

### 1. Não é possível conectar ao OneBot

**Problema**: o log mostra `Failed to connect to OneBot`

**Soluções**:
- Verifique se o NapCat está funcionando corretamente (porta 3001)
- Confirme se `onebot_url` está correto
- Verifique se o `token` é válido

### 2. O bot não responde

**Problema**: uma mensagem é enviada mas não há resposta

**Soluções**:
- Verifique se o remetente está em `trusted_users`
- Revise o nível de permissão (`normal` não recebe resposta direta)
- Veja no log se a mensagem foi recebida
- Em grupos `trusted`, confirme se o bot recebeu `@`
- Em grupos `open`, não é necessário `@` se tudo estiver configurado corretamente

### 3. Falha na sincronização de memória

**Problema**: o log mostra erro de sincronização de memória

**Soluções**:
- Confirme que o Memory Server está em execução
- Apenas conversas privadas do administrador são sincronizadas com a memória
- Conversas em grupo (incluindo `open`) usam apenas contexto temporário e não gravam no armazenamento de memória

### 4. O encaminhamento não funciona

**Problema**: mensagens de usuários normais não são encaminhadas ao administrador

**Soluções**:
- Verifique se há um administrador configurado (`level = "admin"`)
- Confirme o valor de `normal_relay_probability` (padrão 0.1 = 10%)
- Confira no log se o encaminhamento realmente foi acionado

## Contato

Se tiver qualquer problema, abra um issue ou envie um e-mail para zhaijiunknown@outlook.com.

## Licença

Este plugin segue a licença do projeto N.E.K.O.
