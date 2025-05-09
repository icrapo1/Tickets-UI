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


def process_tickets_ui(excel_path, tickets_path):
    wb = pd.ExcelFile(excel_path)
    df_assuntos = pd.read_excel(wb, 'assuntos')
    df_respostas = pd.read_excel(wb, 'respostas')
    assuntos = df_assuntos['Assunto'].tolist()
    respostas = df_respostas.to_dict(orient='records')
    df_t = pd.read_excel(tickets_path)
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
    # Tratamento de quebras de linha no Texto e numeração de links na coluna Mídias
    df_out['Texto'] = df_out['Texto'].apply(lambda x: x.replace('\n', '<br>'))
    df_out['Mídias'] = df_out['Mídias'].apply(lambda cell: '<br>'.join([
        f"Link {i+1}: <a href='{url}' target='_blank'>{url}</a>" for i, url in enumerate(cell.split('\n')) if url
    ]))
    # Gera tabela HTML editável com links numerados
    html_table = df_out.to_html(classes="tickets-table", index=False, escape=False)
    html_table = html_table.replace('<table ', '<table id="tickets-review-table" contenteditable="true" ')
    html = f"""
<style>
  .tickets-table {{ border-collapse: collapse; width: 100%; table-layout: fixed; }}
  .tickets-table th, .tickets-table td {{ border: 1px solid #ddd; padding: 8px; }}
  .tickets-table tr:nth-child(even) {{ background-color: #f2f2f2; }}
  .tickets-table th {{ padding: 12px; background-color: #4CAF50; color: white; }}
  .tickets-table td {{ white-space: pre-wrap; word-break: break-word; }}
  .tickets-table a {{ pointer-events: auto; color: #1a0dab; text-decoration: underline; }}
</style>
<div>{html_table}</div>
"""
    return html


def create_ticket_review_ui():
    with gr.Blocks(title="Ticket Review") as demo:
        with gr.Group():
            excel_file = gr.File(label="Arquivo de configuração (torabit.xlsx)", type="filepath")
            tickets_file = gr.File(label="Arquivo de tickets extraídos (tickets_extraidos.xlsx)", type="filepath")
            process_btn = gr.Button("Processar Tickets", elem_id="process-btn")
            # Loader HTML spinner
            gr.HTML("""
<div id="loader" style="display:none; justify-content:center; align-items:center;">
  <div class="spinner"></div>
</div>
<style>
#loader {
  position: fixed;
  top: 0; left: 0;
  width: 100%; height: 100%;
  display: none;
  justify-content: center;
  align-items: center;
  background: rgba(255,255,255,0.8);
  z-index: 1000;
}
.spinner {
  border: 16px solid #f3f3f3;
  border-top: 16px solid #3498db;
  border-radius: 50%;
  width: 120px;
  height: 120px;
  animation: spin 1.5s linear infinite;
}
@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}
</style>
<script>
document.getElementById('process-btn').addEventListener('click', function() {
  document.getElementById('loader').style.display = 'flex';
});
</script>
""")
        with gr.Group():
            review_html = gr.HTML(label="Revisão de Tickets")
            process_btn.click(
                fn=process_tickets_ui,
                inputs=[excel_file, tickets_file],
                outputs=[review_html],
                _js="() => document.getElementById('loader').style.display='none'"
            )
    return demo


if __name__ == "__main__":
    ui = create_ticket_review_ui()
    ui.launch(debug=True)
