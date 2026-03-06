# 🤖 Bot Telegram com IA

Um bot pessoal para Telegram com integração com IA via Groq, capaz de conversar, analisar arquivos, interpretar imagens e executar comandos de gerenciamento de arquivos.

---

## 📌 Sobre o projeto

Este projeto foi criado para funcionar como um assistente pessoal no Telegram, com foco em produtividade, análise de arquivos e automação de tarefas.

O bot é capaz de:

- conversar com IA de forma natural;
- criar arquivos automaticamente a partir de respostas;
- ler arquivos de texto;
- analisar arquivos `.csv` e planilhas;
- receber documentos enviados pelo Telegram;
- analisar automaticamente arquivos compatíveis após o envio;
- reanalisar o último arquivo recebido;
- interpretar imagens enviadas no chat;
- rodar em plataformas como o Render.

---

## 🚀 Funcionalidades

### Conversa com IA
O bot responde mensagens utilizando a API da Groq.

### Criação automática de arquivos
Se a IA identificar que deve gerar um arquivo, o bot salva esse conteúdo automaticamente no ambiente onde está rodando e pode enviá-lo de volta pelo Telegram.

### Comandos de arquivos
O bot permite navegar e manipular arquivos e pastas com comandos no Telegram.

### Análise de CSV e planilhas
Suporte para análise de:

- `.csv`
- `.xls`
- `.xlsx`
- `.xlsm`
- `.xlsb`
- `.ods`
- `.odt`

### Análise automática de documentos enviados
Ao enviar um arquivo compatível no Telegram, o bot salva o documento e já executa a análise automaticamente.

### Reanálise do último arquivo
Com o comando `/analisar_ultimo`, o bot repete a análise do último arquivo compatível recebido.

### Análise de imagens
O bot também consegue interpretar imagens enviadas no chat usando modelo com visão.

---

## 📂 Comandos disponíveis

```text
/dir - Lista arquivos e pastas
/cd - Entra em uma pasta
/mkdir - Cria uma pasta
/ler - Lê um arquivo
/del - Exclui um arquivo ou pasta vazia
/analisar - Analisa CSV e planilhas
/analisar_ultimo - Analisa novamente o último arquivo recebido
/help - Mostra os comandos disponíveis
/start - Inicia o bot