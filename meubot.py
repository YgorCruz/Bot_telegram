import telebot
from telebot import types
from groq import Groq
import re
import os
import base64
import pandas as pd
import threading
from flask import Flask
import shutil
from datetime import datetime


class FileAnalyzer:
    EXTENSOES_ANALISE = [".csv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt"]

    def limitar_texto(self, texto, limite=12000):
        if len(texto) <= limite:
            return texto
        return texto[:limite] + "\n\n[...conteúdo resumido por limite de tamanho...]"

    def resumir_dataframe(self, df, nome_aba=None, limite_linhas=8):
        partes = []

        if nome_aba:
            partes.append(f"Aba: {nome_aba}")

        partes.append(f"Linhas: {df.shape[0]}")
        partes.append(f"Colunas: {df.shape[1]}")
        partes.append(f"Nome das colunas: {', '.join(map(str, df.columns.tolist()))}")

        nulos = df.isnull().sum()
        nulos_relevantes = nulos[nulos > 0]
        if not nulos_relevantes.empty:
            partes.append("Valores ausentes por coluna:")
            for col, qtd in nulos_relevantes.items():
                partes.append(f"- {col}: {qtd}")

        try:
            duplicadas = int(df.duplicated().sum())
            partes.append(f"Linhas duplicadas: {duplicadas}")
        except Exception:
            pass

        try:
            colunas_numericas = df.select_dtypes(include="number")
            if not colunas_numericas.empty:
                partes.append("Resumo estatístico das colunas numéricas:")
                partes.append(colunas_numericas.describe().to_string())
        except Exception:
            pass

        partes.append("Primeiras linhas:")
        try:
            partes.append(df.head(limite_linhas).fillna("").to_string(index=False))
        except Exception:
            partes.append("Não foi possível renderizar a prévia das primeiras linhas.")

        return "\n".join(partes)

    def ler_texto_generico(self, caminho_arquivo, limite_bytes=4000):
        ultimo_erro = None
        for codificacao in ["utf-8", "latin-1", "cp1252", "mac_roman", "cp850"]:
            try:
                with open(caminho_arquivo, "r", encoding=codificacao) as f:
                    return f.read(limite_bytes)
            except Exception as e:
                ultimo_erro = e
        raise Exception(f"Não consegui ler o arquivo como texto. Último erro: {ultimo_erro}")

    def ler_csv_com_pandas(self, caminho_arquivo):
        ultimo_erro = None
        for encoding in ["utf-8", "latin-1", "cp1252", "cp850"]:
            try:
                df = pd.read_csv(caminho_arquivo, sep=None, engine="python", encoding=encoding)
                return df, encoding
            except Exception as e:
                ultimo_erro = e
        raise Exception(f"Não consegui ler o CSV. Último erro: {ultimo_erro}")

    def ler_planilha_excel(self, caminho_arquivo):
        return pd.read_excel(caminho_arquivo, sheet_name=None)

    def gerar_resumo_arquivo(self, caminho_arquivo):
        extensao = os.path.splitext(caminho_arquivo)[1].lower()

        if not os.path.exists(caminho_arquivo):
            raise FileNotFoundError(f"Arquivo não encontrado: {caminho_arquivo}")

        if extensao == ".csv":
            df, encoding = self.ler_csv_com_pandas(caminho_arquivo)
            resumo = [
                "Tipo detectado: CSV",
                f"Encoding utilizado: {encoding}",
                self.resumir_dataframe(df)
            ]
            return self.limitar_texto("\n\n".join(resumo))

        elif extensao in [".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt"]:
            abas = self.ler_planilha_excel(caminho_arquivo)
            partes = [
                f"Tipo detectado: Planilha {extensao}",
                f"Total de abas: {len(abas)}"
            ]

            for nome_aba, df in abas.items():
                partes.append("\n" + "=" * 60)
                partes.append(self.resumir_dataframe(df, nome_aba=nome_aba))

            return self.limitar_texto("\n\n".join(partes))

        else:
            raise Exception(
                f"Formato não suportado para /analisar: {extensao}\n"
                f"Formatos aceitos: {', '.join(self.EXTENSOES_ANALISE)}"
            )


class TelegramAIBotApp:
    def __init__(self):
        self.CHAVE_GROQ = os.environ.get("GROQ_API_KEY")
        self.TOKEN_TELEGRAM = os.environ.get("TELEGRAM_TOKEN")

        if not self.CHAVE_GROQ or not self.TOKEN_TELEGRAM:
            raise ValueError("As variáveis de ambiente GROQ_API_KEY e TELEGRAM_TOKEN precisam estar configuradas.")

        self.cliente_groq = Groq(api_key=self.CHAVE_GROQ)
        self.bot = telebot.TeleBot(self.TOKEN_TELEGRAM)
        self.app = Flask(__name__)
        self.analyzer = FileAnalyzer()

        self.codigo_arquivo_atual = os.path.abspath(__file__)
        self.diretorio_backups = os.path.join(os.getcwd(), "backups_codigo")
        self.arquivo_log_autoajuste = os.path.join(os.getcwd(), "autoajustes.log")
        self.codigo_proposto_pendente = None
        self.instrucao_autoajuste_pendente = None
        self.ultimo_backup_codigo = None

        os.makedirs(self.diretorio_backups, exist_ok=True)

        self.pessoal = """Você é o assistente pessoal.
Seu objetivo é ajudar na organização de tarefas, rotina, estudos, tecnologia, trabalho e no dia a dia.

Você deve falar de forma humana, natural, clara e útil.
Seja breve quando possível, mas sem perder clareza.
Adapte o tom ao jeito de falar, mantendo linguagem acessível."""

        self.historico_conversa = []
        self.ultimo_arquivo_recebido = None
        self.aguardando_confirmacao_envio = False
        self.arquivo_pendente_envio = None

        self.configurar_rotas_flask()
        self.configurar_comandos_telegram()
        self.registrar_handlers()

    def configurar_rotas_flask(self):
        @self.app.route("/")
        def home():
            return "Bot rodando no Render!", 200

        @self.app.route("/health")
        def health():
            return {"status": "ok"}, 200

    def configurar_comandos_telegram(self):
        self.bot.set_my_commands([
            telebot.types.BotCommand("dir", "Lista arquivos e pastas"),
            telebot.types.BotCommand("cd", "Entra em uma pasta"),
            telebot.types.BotCommand("mkdir", "Cria uma pasta"),
            telebot.types.BotCommand("ler", "Lê um arquivo"),
            telebot.types.BotCommand("del", "Exclui um arquivo ou pasta vazia"),
            telebot.types.BotCommand("analisar", "Analisa CSV e planilhas"),
            telebot.types.BotCommand("analisar_ultimo", "Analisa novamente o último arquivo recebido"),
            telebot.types.BotCommand("autoajuste", "Gera proposta de autoajuste no código"),
            telebot.types.BotCommand("aplicar_autoajuste", "Aplica a última proposta gerada"),
            telebot.types.BotCommand("rollback_codigo", "Restaura o último backup do código"),
            telebot.types.BotCommand("help", "Mostra os comandos disponíveis"),
            telebot.types.BotCommand("start", "Inicia o bot"),
        ])

    def iniciar_web(self):
        porta = int(os.environ.get("PORT", 10000))
        self.app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)

    def atualizar_historico(self, role, content, limite=10):
        self.historico_conversa.append({"role": role, "content": content})
        while len(self.historico_conversa) > limite:
            self.historico_conversa.pop(0)

    def registrar_log_autoajuste(self, mensagem):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(self.arquivo_log_autoajuste, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {mensagem}\n")

    def ler_proprio_codigo(self):
        with open(self.codigo_arquivo_atual, "r", encoding="utf-8") as f:
            return f.read()

    def extrair_codigo_completo(self, resposta_modelo):
        padrao = r"\[CODIGO_COMPLETO\](.*?)\[/CODIGO_COMPLETO\]"
        match = re.search(padrao, resposta_modelo, re.DOTALL)
        if not match:
            raise Exception("A IA não retornou o código no formato esperado [CODIGO_COMPLETO].")
        return match.group(1).strip()

    def validar_codigo_python(self, codigo):
        compile(codigo, self.codigo_arquivo_atual, "exec")
        return True

    def criar_backup_codigo(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nome_backup = f"meubot_backup_{timestamp}.py"
        caminho_backup = os.path.join(self.diretorio_backups, nome_backup)
        shutil.copy2(self.codigo_arquivo_atual, caminho_backup)
        self.ultimo_backup_codigo = caminho_backup
        self.registrar_log_autoajuste(f"Backup criado: {caminho_backup}")
        return caminho_backup

    def gerar_proposta_autoajuste(self, instrucao_usuario):
        codigo_atual = self.ler_proprio_codigo()

        prompt_autoajuste = f"""
Você é um engenheiro de software extremamente cuidadoso.

Sua tarefa é modificar o código Python abaixo conforme a instrução do usuário.

REGRAS OBRIGATÓRIAS:
- preserve o funcionamento já existente, exceto onde a instrução pedir mudança;
- mantenha o código completo e funcional;
- não remova recursos existentes sem necessidade;
- não inclua explicações fora do bloco;
- responda APENAS neste formato:

[CODIGO_COMPLETO]
<cole aqui o arquivo python completo>
[/CODIGO_COMPLETO]

INSTRUÇÃO DO USUÁRIO:
{instrucao_usuario}

CÓDIGO ATUAL:
{codigo_atual}
"""

        resposta = self.cliente_groq.chat.completions.create(
            messages=[
                {"role": "system", "content": "Você gera código Python completo e funcional."},
                {"role": "user", "content": prompt_autoajuste}
            ],
            model="llama-3.3-70b-versatile",
        )

        texto_resposta = resposta.choices[0].message.content
        codigo_proposto = self.extrair_codigo_completo(texto_resposta)
        self.validar_codigo_python(codigo_proposto)

        self.codigo_proposto_pendente = codigo_proposto
        self.instrucao_autoajuste_pendente = instrucao_usuario

        self.registrar_log_autoajuste(f"Proposta gerada para instrução: {instrucao_usuario}")

        return codigo_proposto

    def aplicar_autoajuste_pendente(self):
        if not self.codigo_proposto_pendente:
            raise Exception("Não existe proposta pendente para aplicar.")

        self.validar_codigo_python(self.codigo_proposto_pendente)
        backup = self.criar_backup_codigo()

        caminho_temp = self.codigo_arquivo_atual + ".tmp"
        with open(caminho_temp, "w", encoding="utf-8") as f:
            f.write(self.codigo_proposto_pendente)

        self.validar_codigo_python(self.codigo_proposto_pendente)
        os.replace(caminho_temp, self.codigo_arquivo_atual)

        self.registrar_log_autoajuste(
            f"Autoajuste aplicado com sucesso. Backup: {backup}. Instrução: {self.instrucao_autoajuste_pendente}"
        )

        self.codigo_proposto_pendente = None
        self.instrucao_autoajuste_pendente = None

        return backup

    def rollback_codigo(self):
        if not self.ultimo_backup_codigo or not os.path.exists(self.ultimo_backup_codigo):
            raise Exception("Não existe backup disponível para rollback.")

        shutil.copy2(self.ultimo_backup_codigo, self.codigo_arquivo_atual)
        self.registrar_log_autoajuste(f"Rollback aplicado a partir de: {self.ultimo_backup_codigo}")
        return self.ultimo_backup_codigo

    def executar_analise_ia(self, nome_arquivo):
        resumo_estruturado = self.analyzer.gerar_resumo_arquivo(nome_arquivo)
        extensao = os.path.splitext(nome_arquivo)[1].lower()

        if extensao in [".csv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt"]:
            instrucao_ia = f"""
Atue como um analista financeiro pessoal e organizador financeiro.

Leia o resumo estruturado do arquivo abaixo e faça uma análise com foco em FINANÇAS PESSOAIS.

Quero que você me entregue:
1. Um resumo financeiro geral
2. Principais padrões de entradas e saídas
3. Possíveis excessos ou desequilíbrios
4. Gastos recorrentes ou categorias que merecem atenção
5. Alertas de inconsistência ou possíveis problemas
6. Insights práticos sobre o comportamento financeiro
7. Sugestões reais de melhoria, economia e organização

Se for possível identificar:
- receitas
- despesas
- saldo
- categorias
- frequência de gastos
- meses/períodos mais pesados

destaque isso de forma clara.

Se for planilha com múltiplas abas, considere o conjunto inteiro e destaque diferenças entre elas.

Resumo do arquivo:
{resumo_estruturado}
"""
        else:
            instrucao_ia = f"""
Atue como um analista de dados avançado.

Leia o resumo estruturado do arquivo abaixo e me entregue:
1. Um resumo executivo
2. Principais padrões encontrados
3. Possíveis inconsistências ou problemas
4. Insights práticos
5. Sugestões de melhoria

Resumo do arquivo:
{resumo_estruturado}
"""

        resposta = self.cliente_groq.chat.completions.create(
            messages=[
                {"role": "system", "content": self.pessoal},
                {"role": "user", "content": instrucao_ia}
            ],
            model="llama-3.3-70b-versatile",
        )

        return resposta.choices[0].message.content

    def limpar_estado_envio(self):
        self.aguardando_confirmacao_envio = False
        self.arquivo_pendente_envio = None

    def iniciar_fluxo_envio(self, nome_arquivo):
        self.aguardando_confirmacao_envio = True
        self.arquivo_pendente_envio = nome_arquivo

    def enviar_arquivo_para_chat(self, chat_id):
        if not self.arquivo_pendente_envio:
            self.bot.send_message(chat_id, "❌ Não há nenhum arquivo pendente para envio.")
            return

        try:
            with open(self.arquivo_pendente_envio, "rb") as arquivo_pronto:
                self.bot.send_document(chat_id, arquivo_pronto)
            self.bot.send_message(
                chat_id,
                f"✅ Arquivo `{self.arquivo_pendente_envio}` enviado com sucesso!",
                parse_mode="Markdown"
            )
        except FileNotFoundError:
            self.bot.send_message(
                chat_id,
                f"❌ O arquivo `{self.arquivo_pendente_envio}` não foi encontrado.",
                parse_mode="Markdown"
            )
        finally:
            self.limpar_estado_envio()

    def registrar_handlers(self):
        @self.bot.message_handler(commands=["help", "start"])
        def comando_help(mensagem):
            texto = (
                "🤖 *Comandos disponíveis:*\n\n"
                "/dir - Lista arquivos e pastas\n"
                "/cd nome_da_pasta - Entra em uma pasta\n"
                "/mkdir nome_da_pasta - Cria uma pasta\n"
                "/ler nome_arquivo - Lê um arquivo de texto\n"
                "/del nome - Exclui arquivo ou pasta vazia\n"
                "/analisar nome_arquivo - Analisa CSV ou planilha\n"
                "/analisar_ultimo - Reanalisa o último arquivo enviado\n"
                "/autoajuste instrução - Gera uma proposta de mudança no próprio código\n"
                "/aplicar_autoajuste - Aplica a última proposta gerada\n"
                "/rollback_codigo - Restaura o último backup do código\n\n"
                "*Extensões aceitas no /analisar:*\n"
                "`.csv`, `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, `.ods`, `.odt`\n\n"
                "Você também pode me enviar um arquivo direto no chat, que eu salvo e analiso automaticamente."
            )
            self.bot.reply_to(mensagem, texto, parse_mode="Markdown")

        @self.bot.message_handler(commands=["dir"])
        def comando_dir(mensagem):
            caminho_atual = os.getcwd()
            try:
                itens = os.listdir(caminho_atual)
                texto = f"📂 *Pasta atual:* `{caminho_atual}`\n\n"
                for item in itens:
                    texto += f"📄 {item}\n" if os.path.isfile(item) else f"📁 {item}/\n"
                self.bot.reply_to(mensagem, texto, parse_mode="Markdown")
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Erro ao listar: {e}")

        @self.bot.message_handler(commands=["cd"])
        def comando_cd(mensagem):
            pasta = mensagem.text.replace("/cd", "").strip()
            if not pasta:
                self.bot.reply_to(mensagem, "Faltou a pasta. Ex: `/cd ..` ou `/cd Projetos`", parse_mode="Markdown")
                return
            try:
                os.chdir(pasta)
                self.bot.reply_to(mensagem, f"🚶‍♂️ *Entrou na pasta:*\n`{os.getcwd()}`", parse_mode="Markdown")
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Erro ao entrar na pasta: {e}")

        @self.bot.message_handler(commands=["mkdir"])
        def comando_mkdir(mensagem):
            pasta = mensagem.text.replace("/mkdir", "").strip()
            if not pasta:
                self.bot.reply_to(mensagem, "Qual o nome da pasta? Ex: `/mkdir Nova_Pasta`", parse_mode="Markdown")
                return
            try:
                os.makedirs(pasta, exist_ok=True)
                self.bot.reply_to(mensagem, f"✅ Pasta `{pasta}` criada com sucesso!", parse_mode="Markdown")
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Erro ao criar pasta: {e}")

        @self.bot.message_handler(commands=["ler"])
        def comando_ler(mensagem):
            arquivo = mensagem.text.replace("/ler", "").strip()
            if not arquivo:
                self.bot.reply_to(mensagem, "Qual arquivo quer ler? Ex: `/ler notas.txt`", parse_mode="Markdown")
                return
            try:
                conteudo = self.analyzer.ler_texto_generico(arquivo, limite_bytes=4000)
                self.bot.reply_to(mensagem, f"📖 *Conteúdo de {arquivo}:*\n\n{conteudo}", parse_mode="Markdown")
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Erro ao ler o arquivo: {e}")

        @self.bot.message_handler(commands=["del"])
        def comando_del(mensagem):
            alvo = mensagem.text.replace("/del", "").strip()

            if not alvo:
                self.bot.reply_to(
                    mensagem,
                    "Chefe, o que é pra apagar? Ex: `/del relatorio.csv` ou `/del Pasta_Velha`",
                    parse_mode="Markdown"
                )
                return

            try:
                if os.path.isfile(alvo):
                    os.remove(alvo)
                    self.bot.reply_to(mensagem, f"🗑️ O arquivo `{alvo}` foi mandado pro espaço!", parse_mode="Markdown")
                elif os.path.isdir(alvo):
                    os.rmdir(alvo)
                    self.bot.reply_to(mensagem, f"🗑️ A pasta `{alvo}` foi deletada com sucesso!", parse_mode="Markdown")
                else:
                    self.bot.reply_to(mensagem, f"❌ Não achei nenhum arquivo ou pasta com o nome: `{alvo}`", parse_mode="Markdown")

            except OSError:
                self.bot.reply_to(
                    mensagem,
                    "❌ Erro de segurança: se for uma pasta, ela precisa estar **vazia** para eu poder deletar.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Dificuldade para excluir: {e}")

        @self.bot.message_handler(content_types=["document"])
        def receber_documento(mensagem):
            try:
                arquivo_info = self.bot.get_file(mensagem.document.file_id)
                arquivo_baixado = self.bot.download_file(arquivo_info.file_path)

                nome_arquivo = mensagem.document.file_name

                with open(nome_arquivo, "wb") as f:
                    f.write(arquivo_baixado)

                extensao = os.path.splitext(nome_arquivo)[1].lower()

                if extensao not in self.analyzer.EXTENSOES_ANALISE:
                    self.bot.reply_to(
                        mensagem,
                        (
                            f"✅ Arquivo `{nome_arquivo}` salvo com sucesso!\n\n"
                            f"Formato detectado: `{extensao}`\n"
                            f"Esse formato não entra no `/analisar`.\n"
                            f"Formatos aceitos: `{', '.join(self.analyzer.EXTENSOES_ANALISE)}`"
                        ),
                        parse_mode="Markdown"
                    )
                    return

                self.ultimo_arquivo_recebido = nome_arquivo

                self.bot.reply_to(
                    mensagem,
                    (
                        f"✅ Arquivo `{nome_arquivo}` salvo com sucesso!\n"
                        f"🔎 Iniciando análise automática..."
                    ),
                    parse_mode="Markdown"
                )

                self.bot.send_chat_action(mensagem.chat.id, "typing")

                analise_final = self.executar_analise_ia(nome_arquivo)

                self.atualizar_historico("user", f"Analise automática do arquivo {nome_arquivo}.")
                self.atualizar_historico("assistant", analise_final)

                self.bot.send_message(
                    mensagem.chat.id,
                    (
                        f"📊 *Análise automática concluída para:* `{nome_arquivo}`\n\n"
                        f"{analise_final}\n\n"
                        f"💡 Depois, se quiser repetir a análise do último arquivo, use:\n"
                        f"`/analisar_ultimo`"
                    ),
                    parse_mode="Markdown"
                )

            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Erro ao salvar ou analisar o documento: {e}")

        @self.bot.message_handler(commands=["analisar"])
        def comando_analisar(mensagem):
            arquivo = mensagem.text.replace("/analisar", "").strip()

            if not arquivo:
                self.bot.reply_to(
                    mensagem,
                    "Qual arquivo eu devo analisar? Ex: `/analisar financas.xlsx`",
                    parse_mode="Markdown"
                )
                return

            self.bot.send_chat_action(mensagem.chat.id, "typing")

            try:
                analise_final = self.executar_analise_ia(arquivo)

                self.atualizar_historico("user", f"Analise o arquivo {arquivo}.")
                self.atualizar_historico("assistant", analise_final)

                self.bot.reply_to(mensagem, analise_final, parse_mode="Markdown")

            except FileNotFoundError:
                self.bot.reply_to(
                    mensagem,
                    f"❌ Não achei o arquivo `{arquivo}`. Dá um `/dir` pra confirmar o nome.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Deu tilt na análise: {e}")

        @self.bot.message_handler(commands=["analisar_ultimo"])
        def comando_analisar_ultimo(mensagem):
            if not self.ultimo_arquivo_recebido:
                self.bot.reply_to(
                    mensagem,
                    "❌ Ainda não recebi nenhum arquivo compatível para analisar.",
                    parse_mode="Markdown"
                )
                return

            self.bot.send_chat_action(mensagem.chat.id, "typing")

            try:
                analise_final = self.executar_analise_ia(self.ultimo_arquivo_recebido)

                self.atualizar_historico("user", f"Analise o último arquivo recebido: {self.ultimo_arquivo_recebido}.")
                self.atualizar_historico("assistant", analise_final)

                self.bot.reply_to(
                    mensagem,
                    f"📊 *Análise do último arquivo recebido:* `{self.ultimo_arquivo_recebido}`\n\n{analise_final}",
                    parse_mode="Markdown"
                )

            except FileNotFoundError:
                self.bot.reply_to(
                    mensagem,
                    f"❌ O último arquivo registrado (`{self.ultimo_arquivo_recebido}`) não foi encontrado.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Deu tilt na análise do último arquivo: {e}")

        @self.bot.message_handler(commands=["autoajuste"])
        def comando_autoajuste(mensagem):
            instrucao = mensagem.text.replace("/autoajuste", "", 1).strip()

            if not instrucao:
                self.bot.reply_to(
                    mensagem,
                    "Descreva a mudança. Exemplo:\n`/autoajuste quero melhorar o /analisar para aceitar arquivos .py`",
                    parse_mode="Markdown"
                )
                return

            self.bot.send_chat_action(mensagem.chat.id, "typing")

            try:
                codigo = self.gerar_proposta_autoajuste(instrucao)
                linhas = len(codigo.splitlines())
                self.bot.reply_to(
                    mensagem,
                    (
                        f"✅ Proposta de autoajuste gerada com sucesso.\n\n"
                        f"🧠 Instrução: `{instrucao}`\n"
                        f"📄 Linhas do código proposto: `{linhas}`\n\n"
                        f"Se quiser aplicar no arquivo atual, use:\n"
                        f"`/aplicar_autoajuste`\n\n"
                        f"Se aplicar e der ruim, você poderá usar:\n"
                        f"`/rollback_codigo`"
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Não consegui gerar a proposta de autoajuste: {e}")

        @self.bot.message_handler(commands=["aplicar_autoajuste"])
        def comando_aplicar_autoajuste(mensagem):
            try:
                backup = self.aplicar_autoajuste_pendente()
                self.bot.reply_to(
                    mensagem,
                    (
                        f"✅ Autoajuste aplicado no arquivo atual.\n\n"
                        f"🛟 Backup salvo em:\n`{backup}`\n\n"
                        f"⚠️ Para o código novo passar a valer totalmente, reinicie o processo ou faça redeploy."
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Não consegui aplicar o autoajuste: {e}")

        @self.bot.message_handler(commands=["rollback_codigo"])
        def comando_rollback_codigo(mensagem):
            try:
                backup = self.rollback_codigo()
                self.bot.reply_to(
                    mensagem,
                    (
                        f"✅ Rollback concluído com sucesso.\n\n"
                        f"Arquivo restaurado a partir de:\n`{backup}`\n\n"
                        f"⚠️ Reinicie o processo ou faça redeploy para carregar o código restaurado."
                    ),
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.bot.reply_to(mensagem, f"❌ Não consegui fazer rollback: {e}")

        @self.bot.message_handler(content_types=["photo"])
        def analisar_foto(mensagem):
            self.bot.send_chat_action(mensagem.chat.id, "typing")

            try:
                id_foto = mensagem.photo[-1].file_id
                info_arquivo = self.bot.get_file(id_foto)
                arquivo_baixado = self.bot.download_file(info_arquivo.file_path)

                imagem_base64 = base64.b64encode(arquivo_baixado).decode("utf-8")
                pergunta = mensagem.caption if mensagem.caption else "O que você vê nesta imagem? Seja direto."

                resposta = self.cliente_groq.chat.completions.create(
                    model="llama-3.2-11b-vision-preview",
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": pergunta},
                                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{imagem_base64}"}}
                            ]
                        }
                    ]
                )

                analise_foto = resposta.choices[0].message.content

                self.atualizar_historico("user", f"[FOTO ENVIADA]: {pergunta}")
                self.atualizar_historico("assistant", analise_foto)

                self.bot.reply_to(mensagem, analise_foto, parse_mode="Markdown")

            except Exception as e:
                print(f"❌ ERRO NA FOTO: {e}")
                self.bot.reply_to(mensagem, f"❌ Chefe, deu miopia na IA: {e}")

        @self.bot.message_handler(func=lambda message: True)
        def conversar(mensagem):
            texto_usuario = (mensagem.text or "").strip()
            texto_normalizado = texto_usuario.lower()

            if self.aguardando_confirmacao_envio:
                if texto_normalizado in ["não", "nao", "cancelar", "cancela", "deixa", "deixa pra lá", "deixa pra la"]:
                    nome_cancelado = self.arquivo_pendente_envio
                    self.limpar_estado_envio()
                    self.bot.reply_to(
                        mensagem,
                        f"✅ Beleza. Não vou enviar o arquivo `{nome_cancelado}`.",
                        parse_mode="Markdown"
                    )
                    return

                if texto_normalizado in ["sim", "envia", "enviar", "manda", "pode enviar"]:
                    self.enviar_arquivo_para_chat(mensagem.chat.id)
                    return

            self.bot.send_chat_action(mensagem.chat.id, "typing")

            self.atualizar_historico("user", mensagem.text)

            pacote_mensagens = [{"role": "system", "content": self.pessoal}] + self.historico_conversa

            try:
                resposta = self.cliente_groq.chat.completions.create(
                    messages=pacote_mensagens,
                    model="llama-3.3-70b-versatile",
                )
                texto_final = resposta.choices[0].message.content

                padrao = r"\[CRIAR_ARQUIVO:\s*(.+?)\](.*?)\[/CRIAR_ARQUIVO\]"
                match = re.search(padrao, texto_final, re.DOTALL)

                if match:
                    nome_arquivo = match.group(1).strip()
                    conteudo_arquivo = match.group(2).strip()

                    with open(nome_arquivo, "w", encoding="utf-8") as arquivo:
                        arquivo.write(conteudo_arquivo)

                    texto_final = re.sub(padrao, "", texto_final, flags=re.DOTALL).strip()

                    if not texto_final:
                        texto_final = "✅ Arquivo criado com sucesso."

                    texto_final += f"\n\n✅ O arquivo `{nome_arquivo}` foi criado no seu PC! Quer que eu envie por aqui?"

                    markup = types.InlineKeyboardMarkup()
                    botao_sim = types.InlineKeyboardButton("📥 Sim, me envia", callback_data="enviar_arquivo_pendente")
                    botao_nao = types.InlineKeyboardButton("❌ Não, cancelar", callback_data="cancelar_envio_arquivo")
                    markup.add(botao_sim)
                    markup.add(botao_nao)

                    self.iniciar_fluxo_envio(nome_arquivo)
                    self.atualizar_historico("assistant", texto_final)

                    self.bot.send_message(
                        mensagem.chat.id,
                        texto_final,
                        parse_mode="Markdown",
                        reply_markup=markup
                    )
                    return

                self.atualizar_historico("assistant", texto_final)
                self.bot.send_message(mensagem.chat.id, texto_final, parse_mode="Markdown")

            except Exception as e:
                print(f"❌ ERRO REAL: {e}")
                self.bot.reply_to(mensagem, "Opa, deu um curto aqui chefe 😵‍💫")

        @self.bot.callback_query_handler(func=lambda call: call.data in ["enviar_arquivo_pendente", "cancelar_envio_arquivo"])
        def tratar_botoes_envio(call):
            if call.data == "enviar_arquivo_pendente":
                if not self.arquivo_pendente_envio:
                    self.bot.answer_callback_query(call.id, "❌ Não há arquivo pendente para envio.")
                    try:
                        self.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
                    except Exception:
                        pass
                    return

                nome_arquivo = self.arquivo_pendente_envio

                try:
                    with open(nome_arquivo, "rb") as arquivo_pronto:
                        self.bot.send_document(call.message.chat.id, arquivo_pronto)

                    self.bot.answer_callback_query(call.id, "Arquivo enviado!")
                    try:
                        self.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
                    except Exception:
                        pass

                    self.bot.send_message(
                        call.message.chat.id,
                        f"✅ Arquivo `{nome_arquivo}` enviado com sucesso!",
                        parse_mode="Markdown"
                    )

                except FileNotFoundError:
                    self.bot.answer_callback_query(call.id, "❌ Arquivo não encontrado.")
                    self.bot.send_message(
                        call.message.chat.id,
                        f"❌ O arquivo `{nome_arquivo}` não foi encontrado.",
                        parse_mode="Markdown"
                    )
                finally:
                    self.limpar_estado_envio()

            elif call.data == "cancelar_envio_arquivo":
                nome_arquivo = self.arquivo_pendente_envio

                self.limpar_estado_envio()
                self.bot.answer_callback_query(call.id, "Envio cancelado.")

                try:
                    self.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
                except Exception:
                    pass

                if nome_arquivo:
                    self.bot.send_message(
                        call.message.chat.id,
                        f"✅ Beleza. Não vou enviar o arquivo `{nome_arquivo}`.",
                        parse_mode="Markdown"
                    )
                else:
                    self.bot.send_message(
                        call.message.chat.id,
                        "✅ Beleza. Não vou enviar o arquivo.",
                        parse_mode="Markdown"
                    )

    def run(self):
        print("Iniciando servidor web para o Render...")
        thread_web = threading.Thread(target=self.iniciar_web, daemon=True)
        thread_web.start()

        print("Bot rodando!")
        self.bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    app = TelegramAIBotApp()
    app.run()
