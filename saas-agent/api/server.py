from dotenv import load_dotenv
load_dotenv()  # Carrega variáveis antes de importar o grafo

from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage
from api.schemas import SolicitacaoCS, RespostaCS, DadosConsultados
from agent.graph import agent_graph

app = FastAPI(
    title="Agente Operacional de CS - Créditos de API",
    description="API REST para consulta de assinaturas e bonificação segura de créditos de API via LangGraph.",
    version="1.0.0"
)


@app.post("/agent/executar", response_model=RespostaCS, tags=["Agente CS"])
async def executar_agente(payload: SolicitacaoCS):
    try:
        estado_inicial = {
            "messages": [HumanMessage(content=payload.mensagem)],
            "empresa_id": None,
            "assinatura_id": None,
            "status_assinatura": None,
            "saldo_atual": None,
            "creditos_solicitados": None,
            "status_operacao": "EM_ANDAMENTO",
            "iteracoes": 0
        }

        # Executa o grafo aplicando também o recursion_limit nativo do LangGraph
        resultado = agent_graph.invoke(
            estado_inicial,
            config={"recursion_limit": 25}
        )

        parecer = resultado["messages"][-1].content

        return RespostaCS(
            status_operacao=resultado.get("status_operacao", "CONCLUIDA"),
            parecer_agente=parecer,
            dados_consultados=DadosConsultados(
                empresa_id=resultado.get("empresa_id"),
                assinatura_id=resultado.get("assinatura_id"),
                status_assinatura=resultado.get("status_assinatura"),
                saldo_atual=resultado.get("saldo_atual"),
                creditos_solicitados=resultado.get("creditos_solicitados")
            ),
            iteracoes_executadas=resultado.get("iteracoes", 1)
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro interno na orquestração do agente: {str(e)}"
        )
