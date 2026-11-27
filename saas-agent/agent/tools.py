import os
import httpx
from langchain_core.tools import tool
from api.schemas import BuscarEmpresaInput, ConsultarAssinaturaInput, IncluirCreditosInput

SAAS_API_URL = os.getenv("SAAS_API_URL", "http://localhost:8002")
TIMEOUT_SEGUNDOS = 10.0


@tool(args_schema=BuscarEmpresaInput)
def buscar_empresa(nome: str) -> dict:
    """Busca empresas pelo nome fantasia na API SaaS para obter o empresa_id."""
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as client:
            response = client.get(f"{SAAS_API_URL}/empresas", params={"nome": nome.strip()})

        if response.status_code == 404:
            return {"status": "nao_encontrada", "mensagem": f"Nenhuma empresa encontrada com o nome '{nome}'."}
        if response.status_code >= 400:
            return {"status": "erro_api", "codigo": response.status_code, "mensagem": "Erro ao consultar cadastro de empresas na API SaaS."}

        empresas = response.json()
        if not empresas:
            return {"status": "nao_encontrada", "mensagem": f"Nenhuma empresa encontrada com o nome '{nome}'."}
        if len(empresas) > 1:
            return {
                "status": "ambiguo",
                "mensagem": f"Foram encontradas {len(empresas)} empresas semelhantes. Solicite ao analista especificar o ID ou nome exato.",
                "empresas": empresas
            }
        return {"status": "sucesso", "empresa": empresas[0]}

    except httpx.TimeoutException:
        return {"status": "erro_conexao", "mensagem": "Timeout ao conectar com a API SaaS na busca de empresas."}
    except httpx.RequestError as exc:
        return {"status": "erro_conexao", "mensagem": f"Falha de comunicação com a API SaaS: {str(exc)}"}


@tool(args_schema=ConsultarAssinaturaInput)
def consultar_assinatura(empresa_id: int) -> dict:
    """Consulta os dados da assinatura (assinatura_id, plano, status e saldo_atual) a partir do empresa_id."""
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as client:
            response = client.get(f"{SAAS_API_URL}/assinaturas/{empresa_id}")

        if response.status_code == 404:
            return {"status": "nao_encontrada", "mensagem": f"Assinatura não localizada para a empresa ID {empresa_id}."}
        if response.status_code == 422:
            return {"status": "erro_validacao", "mensagem": "Parâmetro empresa_id inválido enviado à API SaaS."}
        if response.status_code >= 500:
            return {"status": "erro_api", "mensagem": "Erro interno no servidor SaaS ao consultar assinatura."}

        return {"status": "sucesso", "assinatura": response.json()}

    except httpx.TimeoutException:
        return {"status": "erro_conexao", "mensagem": "Timeout ao consultar assinatura na API SaaS."}
    except httpx.RequestError as exc:
        return {"status": "erro_conexao", "mensagem": f"Falha de rede ao consultar assinatura: {str(exc)}"}


@tool(args_schema=IncluirCreditosInput)
def incluir_creditos(
    assinatura_id: int,
    status_assinatura: str,
    quantidade: int,
    motivo: str = "Bonificação operacional solicitada via CS"
) -> dict:
    """
    Inclui créditos extras em uma assinatura ativa.
    Valida rigorosamente o status ('ativa') e o limite operacional (1 a 3000 créditos).
    """
    # 1. Validação Programática de Status
    if str(status_assinatura).strip().lower() != "ativa":
        return {
            "status": "recusado",
            "motivo": f"Regra de Negócio violada: A assinatura ID {assinatura_id} possui status '{status_assinatura}'. Créditos extras só podem ser concedidos a assinaturas ativas."
        }

    # 2. Validação Programática de Limite Operacional
    if quantidade <= 0 or quantidade > 3000:
        return {
            "status": "recusado",
            "motivo": f"Regra de Negócio violada: A quantidade de {quantidade} créditos está fora do limite operacional permitido (mínimo 1 e máximo 3.000 créditos por operação)."
        }

    # 3. Chamada HTTP Segura
    try:
        with httpx.Client(timeout=TIMEOUT_SEGUNDOS) as client:
            response = client.post(
                f"{SAAS_API_URL}/assinaturas/{assinatura_id}/creditos",
                json={"quantidade": quantidade, "motivo": motivo}
            )

        if response.status_code == 404:
            return {"status": "erro_api", "motivo": f"Assinatura ID {assinatura_id} não encontrada para inclusão de créditos."}
        if response.status_code == 422:
            return {"status": "erro_validacao", "motivo": f"Dados rejeitados pela API SaaS: {response.text}"}
        if response.status_code >= 500:
            return {"status": "erro_api", "motivo": "Erro interno na API SaaS ao tentar creditar saldo."}

        return {
            "status": "concluido",
            "creditos_adicionados": quantidade,
            "resultado_saas": response.json()
        }

    except httpx.TimeoutException:
        return {"status": "erro_conexao", "motivo": "Timeout ao enviar inclusão de créditos para a API SaaS."}
    except httpx.RequestError as exc:
        return {"status": "erro_conexao", "motivo": f"Falha de comunicação com a API SaaS: {str(exc)}"}
