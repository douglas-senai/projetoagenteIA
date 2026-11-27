from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Any, Dict


# --- Schemas de Entrada/Saída da API FastAPI ---

class SolicitacaoCS(BaseModel):
    mensagem: str = Field(
        ...,
        min_length=5,
        description="Instrução em linguagem natural enviada pelo analista de CS.",
        json_schema_extra={
            "examples": [
                "Consulte o saldo da Logística Sul e adicione 1.500 créditos extras"
            ]
        }
    )


class DadosConsultados(BaseModel):
    empresa_id: Optional[int] = None
    assinatura_id: Optional[int] = None
    status_assinatura: Optional[str] = None
    saldo_atual: Optional[int] = None
    creditos_solicitados: Optional[int] = None


class RespostaCS(BaseModel):
    status_operacao: str = Field(
        ...,
        description="Status final: CONCLUIDA, RECUSADA, CONSULTA_REALIZADA, AMBIGUIDADE_OU_ERRO"
    )
    parecer_agente: str = Field(
        ...,
        description="Resposta explicativa e estruturada do agente para o analista."
    )
    dados_consultados: DadosConsultados = Field(
        default_factory=DadosConsultados,
        description="Dados operacionais extraídos e validados durante a execução."
    )
    iteracoes_executadas: int = Field(
        ...,
        description="Quantidade de ciclos de raciocínio executados pelo grafo."
    )


# --- Schemas de Validação de Entrada das Ferramentas (@tool) ---

class BuscarEmpresaInput(BaseModel):
    nome: str = Field(
        ...,
        min_length=2,
        description="Nome fantasia ou termo de busca da empresa cliente."
    )


class ConsultarAssinaturaInput(BaseModel):
    empresa_id: int = Field(
        ...,
        gt=0,
        description="Identificador numérico positivo (ID) da empresa."
    )


class IncluirCreditosInput(BaseModel):
    assinatura_id: int = Field(..., description="ID da assinatura ativa.")
    status_assinatura: str = Field(..., description="Status atual da assinatura (deve ser 'ATIVA').")
    quantidade: int = Field(..., description="Quantidade de créditos a incluir (entre 1 e 3000).")
    motivo: str = Field(default="Bonificação operacional solicitada via CS", description="Justificativa ou motivo da inclusão de créditos.")
