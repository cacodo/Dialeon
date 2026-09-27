// Cliente de API isolado (Decision Delta secao 20) -- nenhum componente
// deve fazer fetch() diretamente. Centraliza base path, parsing JSON,
// parsing de erro HTTP e DTOs tipados.

import type {
  CouncilReadiness,
  CouncilReadinessRequest,
  CreateRunRequest,
  ErrorResponse,
  ProvidersResponse,
  RunAuditResponse,
  RunListResponse,
  RunResponse,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly code: ErrorResponse['error']['code'] | 'unknown'
  readonly details: Record<string, unknown> | null

  constructor(
    status: number,
    code: ErrorResponse['error']['code'] | 'unknown',
    message: string,
    details: Record<string, unknown> | null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

// Base vazia: em dev, o proxy do Vite intercepta; em produção, o
// frontend é servido pelo próprio FastAPI, então caminhos relativos já
// resolvem para o host correto (Decision Delta secao 30 -- "não
// introduzir URL absoluta hardcoded nos componentes").
const BASE_PATH = ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_PATH}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  })

  if (!response.ok) {
    let parsed: ErrorResponse | null = null
    try {
      parsed = (await response.json()) as ErrorResponse
    } catch {
      // corpo não era JSON (ex.: erro de infraestrutura antes de chegar
      // no handler da API) -- ainda assim reportamos algo útil e seguro
    }
    if (parsed?.error) {
      throw new ApiError(response.status, parsed.error.code, parsed.error.message, parsed.error.details)
    }
    throw new ApiError(response.status, 'unknown', `Erro HTTP ${response.status}`, null)
  }

  return (await response.json()) as T
}

export const apiClient = {
  getProviders(): Promise<ProvidersResponse> {
    return request<ProvidersResponse>('/providers')
  },

  // Council Local Execution Readiness & Admission V1 -- prévia sem efeito
  // (nenhuma execução, nenhuma chamada a modelo). Não é autorização: a
  // criação reavalia no aceite.
  previewCouncilReadiness(body: CouncilReadinessRequest): Promise<CouncilReadiness> {
    return request<CouncilReadiness>('/runs/readiness', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  createRun(body: CreateRunRequest): Promise<RunResponse> {
    return request<RunResponse>('/runs', {
      method: 'POST',
      body: JSON.stringify(body),
    })
  },

  listRuns(limit: number, offset: number): Promise<RunListResponse> {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    return request<RunListResponse>(`/runs?${params.toString()}`)
  },

  getRun(runId: string): Promise<RunResponse> {
    return request<RunResponse>(`/runs/${encodeURIComponent(runId)}`)
  },

  getRunAudit(runId: string): Promise<RunAuditResponse> {
    return request<RunAuditResponse>(`/runs/${encodeURIComponent(runId)}/audit`)
  },
}

// Provenance-Preserving Human-Readable Run Export -- endereço do documento de
// texto gerado pelo SERVIDOR (a partir do registro, não dos dados desta tela);
// baixado pelo navegador como anexo, sem fetch() aqui. Só existe pra runs
// concluídas.
export function runExportUrl(runId: string): string {
  return `${BASE_PATH}/runs/${encodeURIComponent(runId)}/export`
}
