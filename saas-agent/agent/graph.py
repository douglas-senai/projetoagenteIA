import json
import re
from typing import Annotated, Literal, Optional, TypedDict
from agent.prompts import SYSTEM_PROMPT
from agent.tools import buscar_empresa, consultar_assinatura, incluir_creditos
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_groq import ChatGroq
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

MAX_ITERACOES = 6


# 1. Estado Tipado (Histórico + Dados Operacionais)
class AgentState(TypedDict):
  messages: Annotated[list[BaseMessage], add_messages]
  empresa_id: Optional[int]
  assinatura_id: Optional[int]
  status_assinatura: Optional[str]
  saldo_atual: Optional[int]
  creditos_solicitados: Optional[int]
  status_operacao: str
  iteracoes: int


tools = [buscar_empresa, consultar_assinatura, incluir_creditos]
llm = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0,
    reasoning_effort="medium",
    max_retries=2,
).bind_tools(tools)
tool_node_executor = ToolNode(tools)


def _primeiro_dict(dado) -> dict:
  """Garante a extração de um dicionário mesmo que a API retorne uma lista de objetos."""
  if isinstance(dado, list):
    return next((item for item in dado if isinstance(item, dict)), {})
  if isinstance(dado, dict):
    return dado
  return {}


def _extrair_saldo(dados) -> Optional[int]:
  """Busca o valor de saldo em diferentes formatos de chave ou sub-objetos."""
  registro = _primeiro_dict(dados)
  if not registro:
    return None

  alvo = (
      _primeiro_dict(registro.get("assinatura"))
      if "assinatura" in registro
      else registro
  )
  chaves_possiveis = (
      "saldo_creditos_api",
      "saldo_atual",
      "novo_saldo",
      "saldo_final",
      "saldo_creditos",
      "creditos_atuais",
      "creditos",
      "saldo",
  )

  for chave in chaves_possiveis:
    if alvo.get(chave) is not None:
      try:
        return int(alvo[chave])
      except (ValueError, TypeError):
        pass
  return None


def _pedido_envolve_inclusao(state: AgentState) -> bool:
  """Verifica na mensagem inicial do usuário se há intenção de adicionar/incluir créditos."""
  termos = (
      "adicion",
      "inclu",
      "credit",
      "crédit",
      "bonific",
      "inser",
      "aument",
      "somar",
      "acrescent",
  )
  for msg in state["messages"]:
    if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
      texto = msg.content.lower()
      return any(t in texto for t in termos)
  return False


# 2. Nós do Grafo
def node_raciocinio_llm(state: AgentState) -> dict:
  """Nó de raciocínio do LLM com controle de passos e proteção contra falhas de parse."""
  iteracao_atual = state.get("iteracoes", 0) + 1
  instrucao_blindada = (
      f"{SYSTEM_PROMPT}\n\n"
      "REGRAS OBRIGATÓRIAS DE EXECUÇÃO:\n"
      "1. Quando precisar chamar uma ferramenta, execute a tool call"
      " diretamente sem escrever texto antes.\n"
      "2. NUNCA afirme que créditos foram incluídos se você ainda não executou"
      " a ferramenta `incluir_creditos` e recebeu o retorno dela."
  )
  mensagens = [SystemMessage(content=instrucao_blindada)] + state["messages"]

  try:
    resposta = llm.invoke(mensagens)
  except Exception as exc:
    if "output_parse_failed" in str(exc):
      mensagens_retry = mensagens + [
          SystemMessage(
              content=(
                  "ERRO DE PARSE ANTERIOR: Não escreva texto algum. Apenas"
                  " invoque a ferramenta necessária agora."
              )
          )
      ]
      resposta = llm.invoke(mensagens_retry)
    else:
      raise

  # Trava anti-alucinação: se o LLM tentou responder sem chamar `incluir_creditos` quando deveria
  sem_tool_call = not getattr(resposta, "tool_calls", None)
  ja_executou_inclusao = any(
      isinstance(m, ToolMessage) and m.name == "incluir_creditos"
      for m in state["messages"]
  )

  if (
      sem_tool_call
      and not ja_executou_inclusao
      and state.get("status_operacao") == "CONSULTA_REALIZADA"
      and state.get("assinatura_id") is not None
      and _pedido_envolve_inclusao(state)
      and iteracao_atual < MAX_ITERACOES
  ):
    mensagens_forcar = mensagens + [
        SystemMessage(
            content=(
                "ATENÇÃO: Você consultou a assinatura, mas AINDA NÃO executou"
                " a ferramenta `incluir_creditos`. Chame `incluir_creditos`"
                " agora passando assinatura_id, status_assinatura e quantidade"
                " (a própria ferramenta validará as regras de negócio)."
            )
        )
    ]
    resposta = llm.invoke(mensagens_forcar)

  if not resposta.content and not getattr(resposta, "tool_calls", None):
    reasoning = resposta.additional_kwargs.get("reasoning_content", "")
    if reasoning:
      resposta = AIMessage(content=reasoning)

  return {"messages": [resposta], "iteracoes": iteracao_atual}


def node_sincronizar_estado(state: AgentState) -> dict:
  """Nó de validação e extração dos retornos das ferramentas para o Estado Tipado."""
  updates: dict = {}

  for msg in reversed(state["messages"]):
    if not isinstance(msg, ToolMessage):
      break

    try:
      conteudo = (
          json.loads(msg.content)
          if isinstance(msg.content, str)
          else msg.content
      )
    except Exception:
      continue

    if not isinstance(conteudo, dict):
      continue

    # Atualiza dados da empresa
    if msg.name == "buscar_empresa":
      if conteudo.get("status") == "sucesso" and "empresa" in conteudo:
        emp = _primeiro_dict(conteudo["empresa"])
        if emp:
          updates["empresa_id"] = emp.get("id") or emp.get("empresa_id")
      elif conteudo.get("status") in (
          "nao_encontrada",
          "ambiguo",
          "erro_api",
          "erro_conexao",
      ):
        updates["status_operacao"] = "AMBIGUIDADE_OU_ERRO"

    # Atualiza dados da assinatura
    elif msg.name == "consultar_assinatura":
      if conteudo.get("status") == "sucesso" and "assinatura" in conteudo:
        ass = _primeiro_dict(conteudo["assinatura"])
        if ass:
          updates["assinatura_id"] = ass.get("id") or ass.get("assinatura_id")
          updates["status_assinatura"] = ass.get("status")
          saldo = _extrair_saldo(ass)
          if saldo is not None:
            updates["saldo_atual"] = saldo
        if updates.get("status_operacao") not in ("CONCLUIDA", "RECUSADA"):
          updates["status_operacao"] = "CONSULTA_REALIZADA"
      else:
        updates["status_operacao"] = "AMBIGUIDADE_OU_ERRO"

    # Atualiza resultado da inclusão de créditos
    elif msg.name == "incluir_creditos":
      if conteudo.get("status") == "concluido":
        updates["status_operacao"] = "CONCLUIDA"
        qtd_adicionada = conteudo.get("creditos_adicionados") or 0
        updates["creditos_solicitados"] = qtd_adicionada

        res_saas = _primeiro_dict(conteudo.get("resultado_saas", {}))
        saldo_encontrado = _extrair_saldo(res_saas)

        if saldo_encontrado is not None:
          updates["saldo_atual"] = saldo_encontrado
        else:
          saldo_anterior = updates.get("saldo_atual") or state.get(
              "saldo_atual"
          )
          if saldo_anterior is not None:
            updates["saldo_atual"] = saldo_anterior + int(qtd_adicionada)

      elif conteudo.get("status") == "recusado":
        updates["status_operacao"] = "RECUSADA"
        if conteudo.get("creditos_solicitados") is not None:
          updates["creditos_solicitados"] = conteudo["creditos_solicitados"]
      else:
        updates["status_operacao"] = "AMBIGUIDADE_OU_ERRO"

  return updates


def node_resposta_final(state: AgentState) -> dict:
  """Nó finalizador que garante um status de operação consistente antes do END."""
  status_atual = state.get("status_operacao")
  updates: dict = {}

  if not status_atual or status_atual == "EM_ANDAMENTO":
    updates["status_operacao"] = "RECUSADA"
    return updates

  # Se o agente consultou a assinatura e recusou a inclusão direto no parecer (ex.: limite > 3000 ou status inativo)
  if status_atual == "CONSULTA_REALIZADA" and _pedido_envolve_inclusao(state):
    updates["status_operacao"] = "RECUSADA"

    if state.get("creditos_solicitados") is None:
      for msg in state["messages"]:
        if isinstance(msg, HumanMessage) and isinstance(msg.content, str):
          texto_limpo = re.sub(r"(?<=\d)\.(?=\d{3})", "", msg.content)
          match = re.search(r"(\d+)\s*cr[eé]dit", texto_limpo, re.IGNORECASE)
          if match:
            updates["creditos_solicitados"] = int(match.group(1))
          break

  return updates


# 3. Arestas Condicionais
def decidir_proximo_passo(
    state: AgentState,
) -> Literal["executar_ferramentas", "resposta_final"]:
  ultima_msg = state["messages"][-1]
  iteracoes = state.get("iteracoes", 0)

  if iteracoes >= MAX_ITERACOES:
    return "resposta_final"

  if hasattr(ultima_msg, "tool_calls") and ultima_msg.tool_calls:
    return "executar_ferramentas"

  return "resposta_final"


# 4. Construção e Compilação do StateGraph
builder = StateGraph(AgentState)

builder.add_node("raciocinio_llm", node_raciocinio_llm)
builder.add_node("executar_ferramentas", tool_node_executor)
builder.add_node("sincronizar_estado", node_sincronizar_estado)
builder.add_node("resposta_final", node_resposta_final)

builder.add_edge(START, "raciocinio_llm")
builder.add_conditional_edges(
    "raciocinio_llm",
    decidir_proximo_passo,
    {
        "executar_ferramentas": "executar_ferramentas",
        "resposta_final": "resposta_final",
    },
)
builder.add_edge("executar_ferramentas", "sincronizar_estado")
builder.add_edge("sincronizar_estado", "raciocinio_llm")
builder.add_edge("resposta_final", END)

agent_graph = builder.compile()
