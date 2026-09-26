"""
Council Accepted Effective Participant Model Choice V1.

Para cada participante selecionado de uma run do Conselho, o modelo
EFETIVAMENTE solicitado é:

    escolha explícita da run (se houver)  -->  senão, o modelo padrão
    configurado no deployment pra aquele provider

Resolvido UMA vez, no aceite (`CouncilExecutionService`), a partir do modelo
padrão lido dos objetos `LLMProvider` REAIS (o mesmo snapshot de
`DefaultModelAuthoritySnapshot`), e congelado no `RunConfig` aceito
(`RunConfig.participant_models`). A prévia de prontidão usa a MESMA função.
A execução só lê o mapa congelado -- nunca resolve o padrão de novo.

O QUE UM IDENTIFICADOR ESCOLHIDO É: uma string específica do provider, no
mesmo formato de um modelo padrão configurado (`OPENAI_DEFAULT_MODEL` etc.).
Este repositório não tem catálogo, lista de modelos permitidos, descoberta
remota nem metadados de capacidade/versão -- e esta regra não inventa
nenhum: a validação aqui é só de FORMA. Um identificador aceito não é
promessa de que o modelo exista, esteja disponível ou funcione no fornecedor;
um fornecedor que o recusar produz uma falha registrada da chamada, como
qualquer outra. A tabela de preços não é lista de permissão: um modelo sem
preço conhecido tem custo DESCONHECIDO, nunca zero.

Escopo: só os participantes (resposta inicial e crítica). Os papéis internos
(extração, análise da fonte, juiz, editor, revisão) continuam com o modelo
padrão do provider deles, mesmo quando o provider é o mesmo de um
participante com escolha explícita. O mesmo identificador em dois providers
são dois pedidos distintos -- nenhuma equivalência entre providers.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.config import validate_model_identifier_edges

_CONFIG = ConfigDict(frozen=True, extra="forbid")

# Teto de tamanho de um identificador escolhido numa run -- identificadores
# reais de modelo são curtos; o teto só impede entrada absurda (é forma, não
# catálogo).
MAX_MODEL_IDENTIFIER_CHARACTERS = 256

# `configured_default`: o participante usa o modelo padrão configurado no
# deployment (nenhuma escolha na run). `run_override`: escolhido
# explicitamente pra esta run.
ParticipantModelOrigin = Literal["configured_default", "run_override"]

# Categorias Unicode proibidas num identificador escolhido: toda "Other"
# (Cc controles C0/C1/DEL; Cf formato -- bidi como U+202E, largura zero,
# soft hyphen, BOM; Cs surrogates; Co uso privado; Cn não atribuído) e todo
# "Separator" (Zs espaço, Zl linha, Zp parágrafo). São exatamente os
# caracteres invisíveis/não imprimíveis: um identificador aceito é sempre
# visível como é. Letras, marcas, números, pontuação e símbolos de qualquer
# escrita (L/M/N/P/S) continuam aceitos -- a regra não é "só ASCII".
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zs", "Zl", "Zp"})

# A propriedade Unicode `Default_Ignorable_Code_Point` COMPLETA, versão
# 16.0.0 (DerivedCoreProperties.txt), em intervalos fechados. `unicodedata`
# não expõe essa propriedade; a tabela é a enumeração dela no Unicode 16.0.0
# (a mesma versão do `unicodedata` do Python 3.14). Ela cobre os invisíveis
# que as categorias acima deixam passar por serem Mn/Lo -- COMBINING GRAPHEME
# JOINER (U+034F), seletores de variação (U+FE00..U+FE0F, U+E0100..U+E01EF),
# seletores/separadores mongóis, vogais inerentes khmer e os preenchimentos
# hangul -- além de Cf/Cn já rejeitados. Não é detecção de homóglifos nem
# normalização: só "não tem aparência própria".
_DEFAULT_IGNORABLE_CODE_POINT_RANGES = (
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180B, 0x180F),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0x3164, 0x3164),
    (0xFE00, 0xFE0F),
    (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0),
    (0xFFF0, 0xFFF8),
    (0x1BCA0, 0x1BCA3),
    (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def is_default_ignorable_code_point(char: str) -> bool:
    code_point = ord(char)
    return any(start <= code_point <= end for start, end in _DEFAULT_IGNORABLE_CODE_POINT_RANGES)


def _first_forbidden_character(value: str) -> str | None:
    return next(
        (
            char
            for char in value
            if char.isspace()
            or unicodedata.category(char) in _FORBIDDEN_CATEGORIES
            or is_default_ignorable_code_point(char)
        ),
        None,
    )


def validate_model_override_identifier(value: str) -> str:
    """Forma de um identificador escolhido numa run: a mesma regra mínima do
    modelo padrão configurado (`validate_model_identifier_edges`), mais: sem
    espaço em branco nem caractere de controle, de formato ou outro
    invisível em lugar nenhum (ver `_FORBIDDEN_CATEGORIES` e
    `_DEFAULT_IGNORABLE_CODE_POINT_RANGES`), e no máximo
    `MAX_MODEL_IDENTIFIER_CHARACTERS`. Nunca reescreve nem normaliza: aceita
    ou rejeita."""
    validate_model_identifier_edges(value, "o modelo escolhido")
    forbidden = _first_forbidden_character(value)
    if forbidden is not None:
        # o caractere vai como U+XXXX: nunca ecoado cru (poderia ser invisível
        # ou reordenar o texto da mensagem)
        raise ValueError(
            "o modelo escolhido não pode conter espaço em branco nem caractere de controle "
            f"ou invisível (U+{ord(forbidden):04X}, {unicodedata.category(forbidden)})"
        )
    if len(value) > MAX_MODEL_IDENTIFIER_CHARACTERS:
        raise ValueError(
            f"o modelo escolhido excede o máximo de {MAX_MODEL_IDENTIFIER_CHARACTERS} caracteres"
        )
    return value


class ParticipantModelChoice(BaseModel):
    """O modelo efetivamente solicitado a UM participante, e de onde veio.
    Fato de ACEITE: o que a run pediu -- nunca o que o provider reportou
    (isso fica em cada `ModelResponse`: `requested_model`/`model`/
    `model_identity_source`)."""

    model_config = _CONFIG

    provider: str
    requested_model: str
    origin: ParticipantModelOrigin

    @field_validator("requested_model")
    @classmethod
    def _well_formed(cls, value: str) -> str:
        # Mínimo comum a padrão configurado e escolha explícita; a regra
        # mais estrita da escolha é aplicada na entrada (ver
        # `validate_participant_model_overrides`).
        return validate_model_identifier_edges(value, "requested_model")


def validate_participant_model_overrides(
    overrides: Mapping[str, str], enabled_providers: Iterable[str]
) -> dict[str, str]:
    """ÚNICA validação da ENTRADA de escolhas explícitas (API, CLI, Web e
    service delegam aqui): cada chave precisa ser um participante
    selecionado, cada valor um identificador bem formado. Devolve uma cópia
    sem reescrever nada."""
    selected = set(enabled_providers)
    unselected = sorted(p for p in overrides if p not in selected)
    if unselected:
        raise ValueError(
            "escolha de modelo só vale para participante selecionado; não selecionado(s): "
            f"{unselected}"
        )
    return {provider: validate_model_override_identifier(model) for provider, model in overrides.items()}


def resolve_participant_models(
    enabled_providers: Iterable[str],
    overrides: Mapping[str, str] | None,
    configured_default_models: Mapping[str, str],
) -> tuple[ParticipantModelChoice, ...]:
    """ÚNICA regra de resolução: pra cada participante selecionado, na ordem
    da seleção, a escolha explícita (válida) ou o modelo padrão configurado.
    `configured_default_models` vem do snapshot lido dos providers reais no
    aceite/prévia -- nunca de `Settings` relido. Um participante sem modelo
    padrão conhecido é erro de programação (o chamador já rejeitou provider
    desconhecido), nunca um modelo inventado."""
    providers = tuple(enabled_providers)
    checked = validate_participant_model_overrides(overrides or {}, providers)
    missing_defaults = sorted(p for p in providers if p not in configured_default_models)
    if missing_defaults:
        raise ValueError(f"sem modelo padrão configurado conhecido para: {missing_defaults}")
    return tuple(
        ParticipantModelChoice(provider=p, requested_model=checked[p], origin="run_override")
        if p in checked
        else ParticipantModelChoice(
            provider=p, requested_model=configured_default_models[p], origin="configured_default"
        )
        for p in providers
    )
