# Plugin Mijia · Guia rápido de uso (v1.1)

> **Dica essencial**: este plugin foi pensado para o **controle por linguagem natural**. Na maioria dos casos não é preciso procurar o ID do dispositivo nem parâmetros técnicos: basta dar um comando em linguagem coloquial para controlar seus dispositivos.

---

## 1. Tabela rápida de comandos principais

Basta dizer ao AI Agent um dos seguintes tipos de comando e o plugin fará a análise e execução automaticamente:

| Tipo de ação | Comando de exemplo (copie e use) | Observações |
| :--- | :--- | :--- |
| **💡 Ligar/desligar dispositivo** | `acenda a luz do quarto` <br> `desligue a tomada da sala` | Aceita verbos como «acenda / apague / ligue / desligue» |
| **🏠 Controle por área** | `acenda a luz da sala` <br> `desligue o ar-condicionado do quarto principal` | **(Destaque da nova versão)** Aceita «nome do cômodo + nome do dispositivo» |
| **🌡️ Ajustar temperatura** | `ajuste o ar para 26 graus` <br> `coloque o ar em 24 graus` | Reconhece números e unidades automaticamente |
| **☀️ Ajustar brilho** | `coloque a luz em 50%` <br> `brilho da luminária 30` | Aceita porcentagens ou números inteiros |
| **🔄 Trocar de modo** | `coloque o ar em modo refrigerar` <br> `coloque o ventilador em automático` | Aceita as palavras de modo mais comuns |
| **🎬 Executar cena** | `executar a cena de chegar em casa` <br> `acionar a cena de sair de casa` | As cenas precisam estar configuradas previamente no Mijia App |

---

## 2. Dica avançada: resolvendo o problema de «muitos dispositivos, não acha o certo»

Se você tiver vários dispositivos do mesmo tipo em casa (por exemplo, várias «luzes»), dizer apenas `acenda a luz` pode acionar uma mensagem de desambiguação.

### 1. Exemplo de mensagem de desambiguação
Quando o comando é ambíguo, o plugin retorna uma mensagem semelhante à seguinte; siga a instrução e **acrescente o nome do cômodo**:

> Foram encontrados 3 dispositivos correspondentes a «luz»:
> 1. 🟢 Sala — plafom de teto
> 2. 🔴 Quarto — plafom de teto
> Especifique com precisão usando «nome do cômodo + nome do dispositivo», por exemplo «luz do quarto»

### 2. Formato de comando preciso recomendado
Para evitar confusão, recomendamos criar o hábito de usar **`[nome do cômodo] + [nome do dispositivo]`**:

*   ❌ Comando vago: `acenda a luz`
*   ✅ Comando preciso: `acenda a luz do quarto`
*   ✅ Comando preciso: `acenda o plafom de teto da sala`

---

## 3. Pontos de entrada de API mais usados (para desenvolvedores / usuários avançados)

Se precisar chamar diretamente as interfaces do plugin, estes são os pontos de entrada principais:

| ID de entrada | Função | Exemplo de parâmetros |
| :--- | :--- | :--- |
| `smart_control` | **Controle unificado por linguagem natural (recomendado)** | `{ "command": "acenda a luz do quarto" }` |
| `find_device_by_name` | Procurar um dispositivo | `{ "name": "luz do quarto" }` |
| `query_device_state` | Consultar o estado de um dispositivo | `{ "name": "ar-condicionado" }` |
| `execute_scene` | Executar uma cena | `{ "scene_id": "12345" }` |

---

## 4. Perguntas frequentes (FAQ)

**Q: Por que aparece a mensagem «Não está logado»?**
A: Abra primeiro o plugin Mijia no painel de plugins do NEKO, toque em «Entrar com QR Code» e conclua a autorização.

**Q: Por que o dispositivo está offline, mas o plugin o mostra como online?**
A: O plugin exibe o estado na nuvem, que pode ter algum atraso. Verifique se o dispositivo está realmente ligado à energia e conectado ao Mijia App.

**Q: Quais dispositivos são suportados?**
A: Em tese, todos os dispositivos da plataforma IoT do Mijia. Qualquer dispositivo que possa ser controlado pelo Mijia App também é suportado por este plugin.

---
*Versão do documento: 2026-04-30*
