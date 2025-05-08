import gradio as gr
import pandas as pd
import openai
import os
import tempfile

tickets_data = {
    "1": {"ID": "1", "Título": "Bug no login", "Status": "Aberto", "Responsável": "Alice"},
    "2": {"ID": "2", "Título": "Erro 500", "Status": "Fechado", "Responsável": "Bob"},
    "3": {"ID": "3", "Título": "Solicitação de feature", "Status": "Em andamento", "Responsável": "Carol"}
}

def load_ticket(ticket_id):
    ticket = tickets_data.get(ticket_id)
    if ticket:
        return [[ticket["ID"], ticket["Título"], ticket["Status"], ticket["Responsável"]]]
    return []

def process_files(config_file, tickets_file):
    """Carrega planilhas, classifica tickets e retorna arquivo Excel"""
    df_config = pd.read_excel(config_file.name)
    df_tickets = pd.read_excel(tickets_file.name)
    # Classificação de tickets usando IA - placeholder
    df_tickets['Categoria'] = df_tickets.apply(lambda row: 'Não Classificado', axis=1)
    df_tickets['Resposta'] = df_tickets.apply(lambda row: '', axis=1)
    output_path = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx').name
    df_tickets.to_excel(output_path, index=False)
    return output_path

def create_ticket_review_ui():
    """
    Interface crua para revisão de tickets.
    """
    with gr.Blocks() as demo:
        gr.Markdown("""
        # 📋 Ticket Review
        Interface independente para visualização e revisão de tickets.
        """)
        with gr.Row():
            ticket_id = gr.Textbox(label="Ticket ID", placeholder="Insira o ID do ticket...")
            load_btn = gr.Button("Carregar Ticket")
        data_table = gr.Dataframe(
            headers=["ID", "Título", "Status", "Responsável"],
            datatype=["str", "str", "str", "str"],
            interactive=False,
            label="Detalhes do Ticket"
        )
        load_btn.click(load_ticket, inputs=[ticket_id], outputs=[data_table])
        
        config_upload = gr.File(label="Planilha de Configuração (.xlsx)")
        tickets_upload = gr.File(label="Planilha de Tickets (.xlsx)")
        process_btn = gr.Button("Processar Planilhas")
        download = gr.File(label="Baixar Resultado (.xlsx)")
        process_btn.click(fn=process_files, inputs=[config_upload, tickets_upload], outputs=[download])
        
        # TODO: Conectar ações de carregamento e exibição de dados do ticket
    return demo

if __name__ == "__main__":
    ui = create_ticket_review_ui()
    ui.launch(debug=True)
