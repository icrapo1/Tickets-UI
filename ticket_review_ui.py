import gradio as gr
import pandas as pd
import openai
import os
import time
import json
from difflib import SequenceMatcher

# Padrões de respostas automáticas a serem ignoradas
auto_respostas = [
    "Para serviços do Bilhete Único",
    "Outros assuntos:",
    "Sugestões, reclamações e elogios no Portal SP156"
]

def is_resposta_automatica(msg):
    if not msg or not isinstance(msg, str):
        return False
    txt = msg.strip()
    if any(p in txt for p in auto_respostas):
        return True
    auto_patterns = [
        " Para serviços do Bilhete Único:",
        " Outros assuntos:",
        " Sugestões, reclamações e elogios",
        "https://atendimento.sptrans.com.br/login",
        "https://linktr.ee/sptransoficial",
        "sp156.prefeitura.sp.gov.br"
    ]
    return any(p in txt for p in auto_patterns)


def classificar_assunto(texto, lista_assuntos):
    best, best_score = 'Outro', 0.0
    for a in lista_assuntos:
        score = SequenceMatcher(None, texto, a).ratio()
        if score > best_score:
            best_score, best = score, a
    return best if best_score >= 0.7 else 'Outro'


def respostas_relevantes(texto, respostas, top_n=50):
    scored = [(r, SequenceMatcher(None, texto, r.get('Título','')).ratio()) for r in respostas]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r,_ in scored[:top_n]]


def extrair_primeiro_nome(nome_completo):
    return nome_completo.strip().split()[0] if nome_completo else ''


def analisar_ticket_ui(texto, usuario, handle, assuntos, respostas, midia_urls=None):
    openai.api_key = os.getenv("OPENAI_API_KEY", "")
    # filtrar respostas automáticas
    linhas = texto.split('\n')
    filtr = [l for l in linhas if not is_resposta_automatica(l)]
    txt = '\n'.join(filtr) or "[Conteúdo apenas em mídia/imagem]"
    assuntos_str = '\n'.join(f"- {a}" for a in assuntos)
    respostas_str = '\n'.join(f"{r['Título']}: {r['Conteúdo']}" for r in respostas)
    nome = extrair_primeiro_nome(usuario)
    greeting = f"Olá, {nome}"
    # prompt multimodal
    base = f"""Comece sua resposta com '{greeting}'.

Você é um assistente especializado em atendimento ao cliente brasileiro.
O assunto classificado deve sempre ser o conteúdo literal da lista de assuntos {assuntos_str}.
A resposta sugerida sempre deve seguir 90% o conteúdo literal da lista de respostas-padrão {respostas_str} e 10% para ajustes contextuais. Não crie nenhum outro tipo de resposta.
Os sentimentos só podem ser 3: positivo, negativo ou neutro.

Lista de assuntos:
{assuntos_str}

Respostas modelo:
{respostas_str}

Analise o seguinte diálogo do cliente (cada linha com | representa uma mensagem diferente):
"{txt}"

Responda em JSON: {{ "assunto": ..., "sentiment": ..., "response": ... }}
"""
    parts = [{"type":"text","text":base}]
    if midia_urls:
        for url in midia_urls:
            parts.append({"type":"image_url","image_url":{"url":url}})
    msgs = [
        {"role":"system","content":"Você é um assistente especializado em atendimento ao cliente brasileiro."},
        {"role":"user","content":parts}
    ]
    try:
        resp = openai.chat.completions.create(
            model="gpt-4o", messages=msgs, max_tokens=1024, temperature=1
        )
    except Exception as e:
        if "invalid_image_url" in str(e):
            resp = openai.chat.completions.create(
                model="gpt-4o", messages=[msgs[0],{"role":"user","content":base}], max_tokens=1024, temperature=1
            )
        else:
            raise
    content = resp.choices[0].message.content.strip()
    # extrair JSON
    try:
        result = json.loads(content)
    except:
        import re
        m = re.search(r'\{.*?\}', content, re.DOTALL)
        result = json.loads(m.group(0)) if m else {}
    assunto = result.get('assunto') or classificar_assunto(txt, assuntos)
    sentiment = result.get('sentiment','neutro').lower()
    response = result.get('response','')
    return sentiment, response, assunto


def process_tickets_ui(excel_file, tickets_file):
    wb = pd.ExcelFile(excel_file)
    df_assuntos = pd.read_excel(wb, 'assuntos')
    df_respostas = pd.read_excel(wb, 'respostas')
    assuntos = df_assuntos['Assunto'].tolist()
    respostas = df_respostas.to_dict(orient='records')
    df_t = pd.read_excel(tickets_file)
    agrup = []
    for t, g in df_t.groupby('ticket'):
        msgs, mids = [], []
        for i, m in enumerate(g['mensagem']):
            if isinstance(m, str) and not is_resposta_automatica(m):
                msgs.append(f"| Mensagem {i+1}: {m.strip()}")
        for mid in g.get('midia', pd.Series()).dropna().astype(str):
            mids.append(mid)
        if not msgs and not mids:
            continue
        if not msgs and mids:
            msgs = ["| Mensagem 1: [Conteúdo apenas em mídia/imagem]"]
        agrup.append({
            'ticket': t,
            'mensagem': '\n\n'.join(msgs),
            'midia': ';'.join(mids),
            'nome': g.get('nome', pd.Series([''])).iloc[0],
            'handle': g.get('handle', pd.Series([''])).iloc[0]
        })
    df_agr = pd.DataFrame(agrup)
    registros = []
    for idx, row in df_agr.iterrows():
        sentiment, sug, asn = analisar_ticket_ui(
            row['mensagem'], row['nome'], row['handle'], assuntos,
            respostas_relevantes(row['mensagem'], respostas), [m for m in row['midia'].split(';') if m]
        )
        # filter media links, ignore torabit domain
        media_list = [m.strip() for m in row['midia'].split(';') if m.strip() and not m.strip().startswith('https://tora.torabit.com.br')]
        # join raw links with newline
        links_str = '\n'.join(media_list)
        registros.append({
            'ID': idx+1,
            'Nome': row['nome'],
            'Handle': row['handle'],
            'Texto': row['mensagem'],
            'Mídias': links_str,
            'Assunto': asn,
            'Sentimento': sentiment,
            'Sugestão': sug,
            'Enviar': ''
        })
        time.sleep(7)
    df_out = pd.DataFrame(registros)
    # Return DataFrame for editable display (preserve newlines)
    return df_out


def create_ticket_review_ui():
    with gr.Blocks(title="Ticket Review") as demo:
        with gr.Group():
            excel_file = gr.File(label="Arquivo de configuração (torabit.xlsx)", type="filepath")
            tickets_file = gr.File(label="Arquivo de tickets extraídos (tickets_extraidos.xlsx)", type="filepath")
            process_btn = gr.Button("Processar Tickets")
        with gr.Group():
            review_sheet = gr.Sheet(
                value=None,
                headers=["ID","Nome","Handle","Texto","Mídias","Assunto","Sentimento","Sugestão","Enviar"],
                datatype=["int","str","str","str","str","str","str","str","str"],
                label="Revisão de Tickets",
                interactive=True,
                editable=True,
                wrap=True,
                col_widths=["50px","150px","150px","300px","150px","100px","100px","200px","100px"]
            )
            process_btn.click(
                fn=process_tickets_ui,
                inputs=[excel_file, tickets_file],
                outputs=[review_sheet],
                show_progress=True
            )
    return demo


if __name__ == "__main__":
    ui = create_ticket_review_ui()
    ui.launch(debug=True)
