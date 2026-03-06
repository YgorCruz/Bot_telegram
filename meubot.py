import telebot
from telebot import types
from groq import Groq
import re
import os
import base64
import pandas as pd
import threading
from flask import Flask

# ==========================================
# 1. CHAVES E CLIENTES
# ==========================================
CHAVE_GROQ = os.environ.get('GROQ_API_KEY')
TOKEN_TELEGRAM = os.environ.get('TELEGRAM_TOKEN')

if not CHAVE_GROQ or not TOKEN_TELEGRAM:
    raise ValueError("As variáveis de ambiente GROQ_API_KEY e TELEGRAM_TOKEN precisam estar configuradas.")

cliente_groq = Groq(api_key=CHAVE_GROQ)
bot = telebot.TeleBot(TOKEN_TELEGRAM)

# ==========================================
# 2. SERVIDOR WEB MÍNIMO PARA O RENDER
# ==========================================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot rodando no Render!", 200

@app.route("/health")
def health():
    return {"status": "ok"}, 200

def iniciar_web():
    porta = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=porta, debug=False, use_reloader=False)

# ==========================================
# 3. O DOSSIÊ
# ==========================================
pessoal = """Você é o assistente pessoal de Ygor. 
Seu objetivo é ajudar na organização de tarefas, rotina e no dia a dia.
O intuito será você falar igual humano e aprender com a forma dele de falar.
Seja breve, use poucas palavras, mas seja claro e fale o mais próximo de um humano.

[REGRA DE CRIAÇÃO DE ARQUIVO]:
Se o Ygor pedir para você criar, salvar ou gerar um arquivo (txt, csv para Excel, etc) com alguma informação, você deve no final da sua resposta usar EXATAMENTE este formato oculto:
[CRIAR_ARQUIVO: nome_do_arquivo.extensao]
O conteúdo que deve ser salvo dentro do arquivo vai aqui.
[/CRIAR_ARQUIVO]

ATENÇÃO: Use a tag [CRIAR_ARQUIVO] APENAS UMA ÚNICA VEZ no exato momento do pedido inicial. NUNCA repita a tag em mensagens seguintes ou de confirmação, mesmo que o usuário pergunte sobre o arquivo."""

historico_conversa = []
ultimo_arquivo_recebido = None

# Controle de confirmação de envio do arquivo criado pela IA
aguardando_confirmacao_envio = False
arquivo_pendente_envio = None

# ==========================================
# 4. COMANDOS DO TELEGRAM
# ==========================================
bot.set_my_commands([
    telebot.types.BotCommand("dir", "Lista arquivos e pastas"),
    telebot.types.BotCommand("cd", "Entra em uma pasta"),
    telebot.types.BotCommand("mkdir", "Cria uma pasta"),
    telebot.types.BotCommand("ler", "Lê um arquivo"),
    telebot.types.BotCommand("del", "Exclui um arquivo ou pasta vazia"),
    telebot.types.BotCommand("analisar", "Analisa CSV e planilhas"),
    telebot.types.BotCommand("analisar_ultimo", "Analisa novamente o último arquivo recebido"),
    telebot.types.BotCommand("help", "Mostra os comandos disponíveis"),
    telebot.types.BotCommand("start", "Inicia o bot"),
])

# Modelo para BotFather / Telegram:
"""
dir - Lista arquivos e pastas
cd - Entra em uma pasta
mkdir - Cria uma pasta
ler - Lê um arquivo
del - Exclui um arquivo ou pasta vazia
analisar - Analisa CSV e planilhas
analisar_ultimo - Analisa novamente o último arquivo recebido
help - Mostra os comandos disponíveis
start - Inicia o bot
"""

# ==========================================
# 5. FUNÇÕES AUXILIARES
# ==========================================
EXTENSOES_ANALISE = [".csv", ".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt"]

def limitar_texto(texto, limite=12000):
    if len(texto) <= limite:
        return texto
    return texto[:limite] + "\n\n[...conteúdo resumido por limite de tamanho...]"

def resumir_dataframe(df, nome_aba=None, limite_linhas=8):
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

def ler_texto_generico(caminho_arquivo, limite_bytes=4000):
    ultimo_erro = None
    for codificacao in ["utf-8", "latin-1", "cp1252", "mac_roman", "cp850"]:
        try:
            with open(caminho_arquivo, "r", encoding=codificacao) as f:
                return f.read(limite_bytes)
        except Exception as e:
            ultimo_erro = e
    raise Exception(f"Não consegui ler o arquivo como texto. Último erro: {ultimo_erro}")

def ler_csv_com_pandas(caminho_arquivo):
    ultimo_erro = None
    for encoding in ["utf-8", "latin-1", "cp1252", "cp850"]:
        try:
            df = pd.read_csv(caminho_arquivo, sep=None, engine="python", encoding=encoding)
            return df, encoding
        except Exception as e:
            ultimo_erro = e
    raise Exception(f"Não consegui ler o CSV. Último erro: {ultimo_erro}")

def ler_planilha_excel(caminho_arquivo):
    return pd.read_excel(caminho_arquivo, sheet_name=None)

def gerar_resumo_arquivo(caminho_arquivo):
    extensao = os.path.splitext(caminho_arquivo)[1].lower()

    if not os.path.exists(caminho_arquivo):
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_arquivo}")

    if extensao == ".csv":
        df, encoding = ler_csv_com_pandas(caminho_arquivo)
        resumo = [
            "Tipo detectado: CSV",
            f"Encoding utilizado: {encoding}",
            resumir_dataframe(df)
        ]
        return limitar_texto("\n\n".join(resumo))

    elif extensao in [".xls", ".xlsx", ".xlsm", ".xlsb", ".ods", ".odt"]:
        abas = ler_planilha_excel(caminho_arquivo)
        partes = [
            f"Tipo detectado: Planilha {extensao}",
            f"Total de abas: {len(abas)}"
        ]

        for nome_aba, df in abas.items():
            partes.append("\n" + "=" * 60)
            partes.append(resumir_dataframe(df, nome_aba=nome_aba))

        return limitar_texto("\n\n".join(partes))

    else:
        raise Exception(
            f"Formato não suportado para /analisar: {extensao}\n"
            f"Formatos aceitos: {', '.join(EXTENSOES_ANALISE)}"
        )

def atualizar_historico(role, content, limite=10):
    historico_conversa.append({"role": role, "content": content})
    while len(historico_conversa) > limite:
        historico_conversa.pop(0)

def executar_analise_ia(nome_arquivo):
    resumo_estruturado = gerar_resumo_arquivo(nome_arquivo)

    instrucao_ia = f"""
Atue como um analista de dados avançado.

Leia o resumo estruturado do arquivo abaixo e me entregue:
1. Um resumo executivo
2. Principais padrões encontrados
3. Possíveis inconsistências ou problemas
4. Insights práticos
5. Sugestões de melhoria

Se for planilha com múltiplas abas, considere o conjunto inteiro e destaque diferenças entre elas.

Resumo do arquivo:
{resumo_estruturado}
"""

    resposta = cliente_groq.chat.completions.create(
        messages=[
            {"role": "system", "content": pessoal},
            {"role": "user", "content": instrucao_ia}
        ],
        model="llama-3.3-70b-versatile",
    )

    return resposta.choices[0].message.content

def limpar_estado_envio():
    global aguardando_confirmacao_envio, arquivo_pendente_envio
    aguardando_confirmacao_envio = False
    arquivo_pendente_envio = None

def iniciar_fluxo_envio(nome_arquivo):
    global aguardando_confirmacao_envio, arquivo_pendente_envio
    aguardando_confirmacao_envio = True
    arquivo_pendente_envio = nome_arquivo

def enviar_arquivo_para_chat(chat_id):
    global arquivo_pendente_envio

    if not arquivo_pendente_envio:
        bot.send_message(chat_id, "❌ Não há nenhum arquivo pendente para envio.")
        return

    try:
        with open(arquivo_pendente_envio, "rb") as arquivo_pronto:
            bot.send_document(chat_id, arquivo_pronto)
        bot.send_message(chat_id, f"✅ Arquivo `{arquivo_pendente_envio}` enviado com sucesso!", parse_mode="Markdown")
    except FileNotFoundError:
        bot.send_message(chat_id, f"❌ O arquivo `{arquivo_pendente_envio}` não foi encontrado.", parse_mode="Markdown")
    finally:
        limpar_estado_envio()

# ==========================================
# 6. COMANDOS DE AJUDA
# ==========================================
@bot.message_handler(commands=['help', 'start'])
def comando_help(mensagem):
    texto = (
        "🤖 *Comandos disponíveis:*\n\n"
        "/dir - Lista arquivos e pastas\n"
        "/cd nome_da_pasta - Entra em uma pasta\n"
        "/mkdir nome_da_pasta - Cria uma pasta\n"
        "/ler nome_arquivo - Lê um arquivo de texto\n"
        "/del nome - Exclui arquivo ou pasta vazia\n"
        "/analisar nome_arquivo - Analisa CSV ou planilha\n"
        "/analisar_ultimo - Reanalisa o último arquivo enviado\n\n"
        "*Extensões aceitas no /analisar:*\n"
        "`.csv`, `.xls`, `.xlsx`, `.xlsm`, `.xlsb`, `.ods`, `.odt`\n\n"
        "Você também pode me enviar um arquivo direto no chat, que eu salvo e analiso automaticamente."
    )
    bot.reply_to(mensagem, texto, parse_mode="Markdown")

# ==========================================
# 7. COMANDOS DE ARQUIVO
# ==========================================
@bot.message_handler(commands=['dir'])
def comando_dir(mensagem):
    caminho_atual = os.getcwd()
    try:
        itens = os.listdir(caminho_atual)
        texto = f"📂 *Pasta atual:* `{caminho_atual}`\n\n"
        for item in itens:
            texto += f"📄 {item}\n" if os.path.isfile(item) else f"📁 {item}/\n"
        bot.reply_to(mensagem, texto, parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Erro ao listar: {e}")

@bot.message_handler(commands=['cd'])
def comando_cd(mensagem):
    pasta = mensagem.text.replace("/cd", "").strip()
    if not pasta:
        bot.reply_to(mensagem, "Faltou a pasta. Ex: `/cd ..` ou `/cd Projetos`", parse_mode="Markdown")
        return
    try:
        os.chdir(pasta)
        bot.reply_to(mensagem, f"🚶‍♂️ *Entrou na pasta:*\n`{os.getcwd()}`", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Erro ao entrar na pasta: {e}")

@bot.message_handler(commands=['mkdir'])
def comando_mkdir(mensagem):
    pasta = mensagem.text.replace("/mkdir", "").strip()
    if not pasta:
        bot.reply_to(mensagem, "Qual o nome da pasta? Ex: `/mkdir Nova_Pasta`", parse_mode="Markdown")
        return
    try:
        os.makedirs(pasta, exist_ok=True)
        bot.reply_to(mensagem, f"✅ Pasta `{pasta}` criada com sucesso!", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Erro ao criar pasta: {e}")

@bot.message_handler(commands=['ler'])
def comando_ler(mensagem):
    arquivo = mensagem.text.replace("/ler", "").strip()
    if not arquivo:
        bot.reply_to(mensagem, "Qual arquivo quer ler? Ex: `/ler notas.txt`", parse_mode="Markdown")
        return
    try:
        conteudo = ler_texto_generico(arquivo, limite_bytes=4000)
        bot.reply_to(mensagem, f"📖 *Conteúdo de {arquivo}:*\n\n{conteudo}", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Erro ao ler o arquivo: {e}")

@bot.message_handler(commands=['del'])
def comando_del(mensagem):
    alvo = mensagem.text.replace("/del", "").strip()

    if not alvo:
        bot.reply_to(mensagem, "Chefe, o que é pra apagar? Ex: `/del relatorio.csv` ou `/del Pasta_Velha`", parse_mode="Markdown")
        return

    try:
        if os.path.isfile(alvo):
            os.remove(alvo)
            bot.reply_to(mensagem, f"🗑️ O arquivo `{alvo}` foi mandado pro espaço!", parse_mode="Markdown")
        elif os.path.isdir(alvo):
            os.rmdir(alvo)
            bot.reply_to(mensagem, f"🗑️ A pasta `{alvo}` foi deletada com sucesso!", parse_mode="Markdown")
        else:
            bot.reply_to(mensagem, f"❌ Não achei nenhum arquivo ou pasta com o nome: `{alvo}`", parse_mode="Markdown")

    except OSError:
        bot.reply_to(mensagem, "❌ Erro de segurança: se for uma pasta, ela precisa estar **vazia** para eu poder deletar.", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Dificuldade para excluir: {e}")

# ==========================================
# 8. RECEBER DOCUMENTOS ENVIADOS NO TELEGRAM
# ==========================================
@bot.message_handler(content_types=['document'])
def receber_documento(mensagem):
    global ultimo_arquivo_recebido

    try:
        arquivo_info = bot.get_file(mensagem.document.file_id)
        arquivo_baixado = bot.download_file(arquivo_info.file_path)

        nome_arquivo = mensagem.document.file_name

        with open(nome_arquivo, "wb") as f:
            f.write(arquivo_baixado)

        extensao = os.path.splitext(nome_arquivo)[1].lower()

        if extensao not in EXTENSOES_ANALISE:
            bot.reply_to(
                mensagem,
                (
                    f"✅ Arquivo `{nome_arquivo}` salvo com sucesso!\n\n"
                    f"Formato detectado: `{extensao}`\n"
                    f"Esse formato não entra no `/analisar`.\n"
                    f"Formatos aceitos: `{', '.join(EXTENSOES_ANALISE)}`"
                ),
                parse_mode="Markdown"
            )
            return

        ultimo_arquivo_recebido = nome_arquivo

        bot.reply_to(
            mensagem,
            (
                f"✅ Arquivo `{nome_arquivo}` salvo com sucesso!\n"
                f"🔎 Iniciando análise automática..."
            ),
            parse_mode="Markdown"
        )

        bot.send_chat_action(mensagem.chat.id, 'typing')

        analise_final = executar_analise_ia(nome_arquivo)

        atualizar_historico("user", f"Analise automática do arquivo {nome_arquivo}.")
        atualizar_historico("assistant", analise_final)

        bot.send_message(
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
        bot.reply_to(mensagem, f"❌ Erro ao salvar ou analisar o documento: {e}")

# ==========================================
# 9. ANALISADOR DE DADOS DA IA
# ==========================================
@bot.message_handler(commands=['analisar'])
def comando_analisar(mensagem):
    arquivo = mensagem.text.replace("/analisar", "").strip()

    if not arquivo:
        bot.reply_to(
            mensagem,
            "Qual arquivo eu devo analisar? Ex: `/analisar financas.xlsx`",
            parse_mode="Markdown"
        )
        return

    bot.send_chat_action(mensagem.chat.id, 'typing')

    try:
        analise_final = executar_analise_ia(arquivo)

        atualizar_historico("user", f"Analise o arquivo {arquivo}.")
        atualizar_historico("assistant", analise_final)

        bot.reply_to(mensagem, analise_final, parse_mode="Markdown")

    except FileNotFoundError:
        bot.reply_to(
            mensagem,
            f"❌ Não achei o arquivo `{arquivo}`. Dá um `/dir` pra confirmar o nome.",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Deu tilt na análise: {e}")

@bot.message_handler(commands=['analisar_ultimo'])
def comando_analisar_ultimo(mensagem):
    global ultimo_arquivo_recebido

    if not ultimo_arquivo_recebido:
        bot.reply_to(
            mensagem,
            "❌ Ainda não recebi nenhum arquivo compatível para analisar.",
            parse_mode="Markdown"
        )
        return

    bot.send_chat_action(mensagem.chat.id, 'typing')

    try:
        analise_final = executar_analise_ia(ultimo_arquivo_recebido)

        atualizar_historico("user", f"Analise o último arquivo recebido: {ultimo_arquivo_recebido}.")
        atualizar_historico("assistant", analise_final)

        bot.reply_to(
            mensagem,
            f"📊 *Análise do último arquivo recebido:* `{ultimo_arquivo_recebido}`\n\n{analise_final}",
            parse_mode="Markdown"
        )

    except FileNotFoundError:
        bot.reply_to(
            mensagem,
            f"❌ O último arquivo registrado (`{ultimo_arquivo_recebido}`) não foi encontrado.",
            parse_mode="Markdown"
        )
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Deu tilt na análise do último arquivo: {e}")

# ==========================================
# 10. OLHOS DA IA (ANÁLISE DE FOTO)
# ==========================================
@bot.message_handler(content_types=['photo'])
def analisar_foto(mensagem):
    bot.send_chat_action(mensagem.chat.id, 'typing')

    try:
        id_foto = mensagem.photo[-1].file_id
        info_arquivo = bot.get_file(id_foto)
        arquivo_baixado = bot.download_file(info_arquivo.file_path)

        imagem_base64 = base64.b64encode(arquivo_baixado).decode('utf-8')
        pergunta = mensagem.caption if mensagem.caption else "O que você vê nesta imagem? Seja direto."

        resposta = cliente_groq.chat.completions.create(
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

        atualizar_historico("user", f"[FOTO ENVIADA]: {pergunta}")
        atualizar_historico("assistant", analise_foto)

        bot.reply_to(mensagem, analise_foto, parse_mode="Markdown")

    except Exception as e:
        print(f"❌ ERRO NA FOTO: {e}")
        bot.reply_to(mensagem, f"❌ Chefe, deu miopia na IA: {e}")

# ==========================================
# 11. BATE-PAPO NORMAL
# ==========================================
@bot.message_handler(func=lambda message: True)
def conversar(mensagem):
    global aguardando_confirmacao_envio, arquivo_pendente_envio

    texto_usuario = (mensagem.text or "").strip()
    texto_normalizado = texto_usuario.lower()

    # Se estiver aguardando confirmação de envio, não deixa isso cair no chat da IA
    if aguardando_confirmacao_envio:
        if texto_normalizado in ["não", "nao", "cancelar", "cancela", "deixa", "deixa pra lá", "deixa pra la"]:
            nome_cancelado = arquivo_pendente_envio
            limpar_estado_envio()
            bot.reply_to(
                mensagem,
                f"✅ Beleza. Não vou enviar o arquivo `{nome_cancelado}`.",
                parse_mode="Markdown"
            )
            return

        if texto_normalizado in ["sim", "envia", "enviar", "manda", "pode enviar"]:
            enviar_arquivo_para_chat(mensagem.chat.id)
            return

    bot.send_chat_action(mensagem.chat.id, 'typing')

    atualizar_historico("user", mensagem.text)

    pacote_mensagens = [{"role": "system", "content": pessoal}] + historico_conversa

    try:
        resposta = cliente_groq.chat.completions.create(
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

            iniciar_fluxo_envio(nome_arquivo)
            atualizar_historico("assistant", texto_final)

            bot.send_message(
                mensagem.chat.id,
                texto_final,
                parse_mode="Markdown",
                reply_markup=markup
            )
            return

        atualizar_historico("assistant", texto_final)
        bot.send_message(mensagem.chat.id, texto_final, parse_mode="Markdown")

    except Exception as e:
        print(f"❌ ERRO REAL: {e}")
        bot.reply_to(mensagem, "Opa, deu um curto aqui chefe 😵‍💫")

# ==========================================
# 12. BOTÕES DE AÇÃO
# ==========================================
@bot.callback_query_handler(func=lambda call: call.data in ["enviar_arquivo_pendente", "cancelar_envio_arquivo"])
def tratar_botoes_envio(call):
    global arquivo_pendente_envio

    if call.data == "enviar_arquivo_pendente":
        if not arquivo_pendente_envio:
            bot.answer_callback_query(call.id, "❌ Não há arquivo pendente para envio.")
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception:
                pass
            return

        nome_arquivo = arquivo_pendente_envio

        try:
            with open(nome_arquivo, "rb") as arquivo_pronto:
                bot.send_document(call.message.chat.id, arquivo_pronto)

            bot.answer_callback_query(call.id, "Arquivo enviado!")
            try:
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
            except Exception:
                pass

            bot.send_message(
                call.message.chat.id,
                f"✅ Arquivo `{nome_arquivo}` enviado com sucesso!",
                parse_mode="Markdown"
            )

        except FileNotFoundError:
            bot.answer_callback_query(call.id, "❌ Arquivo não encontrado.")
            bot.send_message(
                call.message.chat.id,
                f"❌ O arquivo `{nome_arquivo}` não foi encontrado.",
                parse_mode="Markdown"
            )
        finally:
            limpar_estado_envio()

    elif call.data == "cancelar_envio_arquivo":
        nome_arquivo = arquivo_pendente_envio

        limpar_estado_envio()
        bot.answer_callback_query(call.id, "Envio cancelado.")

        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass

        if nome_arquivo:
            bot.send_message(
                call.message.chat.id,
                f"✅ Beleza. Não vou enviar o arquivo `{nome_arquivo}`.",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(
                call.message.chat.id,
                "✅ Beleza. Não vou enviar o arquivo.",
                parse_mode="Markdown"
            )

# ==========================================
# 13. INICIALIZAÇÃO
# ==========================================
if __name__ == "__main__":
    print("Iniciando servidor web para o Render...")
    thread_web = threading.Thread(target=iniciar_web, daemon=True)
    thread_web.start()

    print("Bot rodando!")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)