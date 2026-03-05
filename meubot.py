import telebot
from telebot import types
from groq import Groq
import re 
import os
import base64
import os # <-- Adicione esse import no topo

# Em vez de colar a chave aqui, o Python vai "pedir" para o servidor
CHAVE_GROQ = os.environ.get('GROQ_API_KEY')
TOKEN_TELEGRAM = os.environ.get('TELEGRAM_TOKEN')

# O restante do seu código continua igual...
cliente_groq = Groq(api_key=CHAVE_GROQ)
bot = telebot.TeleBot(TOKEN_TELEGRAM)

# 2. O Dossiê
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

# ==========================================
# 3. OS COMANDOS DO SISTEMA (O EXPLORER)
# ==========================================

@bot.message_handler(commands=['dir'])
def comando_dir(mensagem):
    caminho_atual = os.getcwd()
    try:
        itens = os.listdir(caminho_atual)
        texto = f"📂 *Pasta atual:* `{caminho_atual}`\n\n"
        for item in itens:
            # Coloca um ícone diferente se for arquivo ou pasta
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
        conteudo = ""
        # Testando vários encodings (incluindo MS-DOS e Mac)
        for codificacao in ["utf-8", "latin-1", "cp1252", "mac_roman", "cp850"]:
            try:
                with open(arquivo, "r", encoding=codificacao) as f:
                    conteudo = f.read(4000)
                break
            except UnicodeDecodeError:
                continue
                
        if not conteudo:
            bot.reply_to(mensagem, "❌ Não consegui decifrar os caracteres desse arquivo.")
            return

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
        # Verifica se é um arquivo e deleta
        if os.path.isfile(alvo):
            os.remove(alvo)
            bot.reply_to(mensagem, f"🗑️ O arquivo `{alvo}` foi mandado pro espaço!", parse_mode="Markdown")
            
        # Verifica se é uma pasta e deleta (só se estiver vazia)
        elif os.path.isdir(alvo):
            os.rmdir(alvo)
            bot.reply_to(mensagem, f"🗑️ A pasta `{alvo}` foi deletada com sucesso!", parse_mode="Markdown")
            
        # Se você digitar um nome errado
        else:
            bot.reply_to(mensagem, f"❌ Não achei nenhum arquivo ou pasta com o nome: `{alvo}`")
            
    except OSError:
        bot.reply_to(mensagem, "❌ Erro de segurança: Se for uma pasta, ela precisa estar **vazia** para eu poder deletar.")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Dificuldade para excluir: {e}")

# ==========================================
# NOVO COMANDO: O ANALISTA DE DADOS DA IA
# ==========================================
@bot.message_handler(commands=['analisar'])
def comando_analisar(mensagem):
    arquivo = mensagem.text.replace("/analisar", "").strip()
    
    if not arquivo:
        bot.reply_to(mensagem, "Qual arquivo eu devo analisar? Ex: `/analisar financas.csv`", parse_mode="Markdown")
        return
        
    bot.send_chat_action(mensagem.chat.id, 'typing')
    
    try:
        conteudo = ""
        # Testando encodings para análise também
        for codificacao in ["utf-8", "latin-1", "cp1252", "mac_roman", "cp850"]:
            try:
                with open(arquivo, "r", encoding=codificacao) as f:
                    conteudo = f.read(6000)
                break
            except UnicodeDecodeError:
                continue
                
        if not conteudo:
            bot.reply_to(mensagem, "❌ Não consegui decifrar a formatação desse arquivo.")
            return
            
        instrucao_ia = f"Atue como um analista de dados avançado. Leia o conteúdo deste arquivo ({arquivo}) e me dê um resumo claro, principais pontos, padrões encontrados e insights de melhoria. Dados do arquivo:\n\n{conteudo}"
        
        resposta = cliente_groq.chat.completions.create(
            messages=[
                {"role": "system", "content": pessoal},
                {"role": "user", "content": instrucao_ia}
            ],
            model="llama-3.3-70b-versatile",
        )
        
        analise_final = resposta.choices[0].message.content
        
        # Guarda no histórico para você poder fazer perguntas sobre a análise logo em seguida
        historico_conversa.append({"role": "user", "content": f"Analise o arquivo {arquivo}."})
        historico_conversa.append({"role": "assistant", "content": analise_final})
        if len(historico_conversa) > 10:
            historico_conversa.pop(0)
            historico_conversa.pop(0)
            
        bot.reply_to(mensagem, analise_final, parse_mode="Markdown")
        
    except FileNotFoundError:
        bot.reply_to(mensagem, f"❌ Não achei o arquivo `{arquivo}`. Dá um `/dir` pra confirmar o nome.", parse_mode="Markdown")
    except Exception as e:
        bot.reply_to(mensagem, f"❌ Deu tilt na análise: {e}")

# ==========================================
# NOVO COMANDO: OLHOS DA IA (Análise de Foto)
# ==========================================
@bot.message_handler(content_types=['photo'])
def analisar_foto(mensagem):
    bot.send_chat_action(mensagem.chat.id, 'typing')
    
    try:
        # 1. Pega a foto com a melhor qualidade (a última da lista do Telegram)
        id_foto = mensagem.photo[-1].file_id
        info_arquivo = bot.get_file(id_foto)
        arquivo_baixado = bot.download_file(info_arquivo.file_path)
        
        # 2. Converte a foto em código Base64 para a IA conseguir "enxergar"
        imagem_base64 = base64.b64encode(arquivo_baixado).decode('utf-8')
        
        # 3. Se você mandou texto junto com a foto (legenda), ele usa. Senão, usa um padrão.
        pergunta = mensagem.caption if mensagem.caption else "O que você vê nesta imagem? Seja direto."
        
        # 4. Aciona o motor ESPECÍFICO para Visão (único que aceita imagem)
        resposta = cliente_groq.chat.completions.create(
            model="llama-3.2-11b-vision-preview", # <-- Use EXATAMENTE esse nome
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
        
        # Salva no histórico pra você poder continuar conversando sobre a foto
        historico_conversa.append({"role": "user", "content": f"[FOTO ENVIADA]: {pergunta}"})
        historico_conversa.append({"role": "assistant", "content": analise_foto})
        if len(historico_conversa) > 10:
            historico_conversa.pop(0)
            historico_conversa.pop(0)
            
        bot.reply_to(mensagem, analise_foto, parse_mode="Markdown")
        
    except Exception as e:
        print(f"❌ ERRO NA FOTO: {e}")
        bot.reply_to(mensagem, f"❌ Chefe, deu miopia na IA: {e}")

# ==========================================
# 4. A LÓGICA TURBINADA (Bate-papo normal)
# ==========================================
@bot.message_handler(func=lambda message: True)
def conversar(mensagem):
    bot.send_chat_action(mensagem.chat.id, 'typing')
    
    historico_conversa.append({"role": "user", "content": mensagem.text})
    if len(historico_conversa) > 10:
        historico_conversa.pop(0)

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
            texto_final += f"\n\n✅ O arquivo `{nome_arquivo}` foi criado no seu PC! Quer que eu envie por aqui?"

            markup = types.InlineKeyboardMarkup()
            botao_baixar = types.InlineKeyboardButton("📥 Sim, me envia", callback_data=f"baixar_{nome_arquivo}")
            markup.add(botao_baixar)

            historico_conversa.append({"role": "assistant", "content": texto_final})
            bot.send_message(mensagem.chat.id, texto_final, parse_mode="Markdown", reply_markup=markup)
            return

        historico_conversa.append({"role": "assistant", "content": texto_final})
        bot.send_message(mensagem.chat.id, texto_final, parse_mode="Markdown")
        
    except Exception as e:
        print(f"❌ ERRO REAL: {e}")
        bot.reply_to(mensagem, "Opa, deu um curto aqui chefe 😵‍💫")

# 5. O ESCUTADOR DE BOTÕES
@bot.callback_query_handler(func=lambda call: call.data.startswith("baixar_"))
def enviar_arquivo_solicitado(call):
    nome_arquivo = call.data.replace("baixar_", "")
    try:
        with open(nome_arquivo, "rb") as arquivo_pronto:
            bot.send_document(call.message.chat.id, arquivo_pronto)
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except FileNotFoundError:
        bot.answer_callback_query(call.id, "❌ Arquivo não encontrado no PC.")

print("Bot rodando!")
bot.polling()
