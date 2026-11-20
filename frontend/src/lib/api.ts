import axios, { AxiosError } from 'axios'

// Every call goes through /api (Vite proxy in dev, nginx in production). The session is
// an httpOnly cookie; X-Requested-With is required by the API on state-changing calls.
const CSRF_HEADERS = { 'X-Requested-With': 'contexthub' }

export const http = axios.create({ baseURL: '/api', withCredentials: true, headers: CSRF_HEADERS })

http.interceptors.response.use(
  (r) => r,
  (error: AxiosError) => {
    if (error.response?.status === 401 && !error.config?.url?.startsWith('/auth/')) {
      window.dispatchEvent(new Event('contexthub:unauthorized'))
    }
    return Promise.reject(error)
  },
)

export function errorMessage(error: unknown, fallback = 'Une erreur est survenue.'): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (detail && typeof detail.message === 'string') return detail.message
    if (!error.response) return 'Le serveur est injoignable. Vérifiez votre connexion.'
  }
  if (error instanceof Error && error.message) return error.message
  return fallback
}

// ---------------------------------------------------------------- types

export type Role = 'admin' | 'member'
export type Access = 'viewer' | 'editor' | 'admin'

export interface User {
  id: string
  email: string
  full_name: string | null
  role: Role
  is_active: boolean
  auth_provider: 'local' | 'oidc'
  /** Groups carried by the last SSO login. */
  sso_groups?: string[]
  created_at: string | null
  last_login_at: string | null
}

export interface Me extends User {
  collections: { id: string; name: string; access: Access }[]
}

export interface AuthConfig {
  local_enabled: boolean
  oidc_enabled: boolean
  oidc_label: string
  app_name: string
  version: string
  source_code_url: string | null
}

export interface Collection {
  id: string
  name: string
  description: string | null
  is_open: boolean
  access: Access
  document_count: number
  created_at: string | null
}

export interface StoredDocument {
  id: string
  collection_id: string
  filename: string
  title: string | null
  mime_type: string | null
  size_bytes: number
  source_url: string | null
  status: 'queued' | 'processing' | 'ready' | 'failed'
  error_message: string | null
  warnings: string[]
  chunk_count: number
  page_count: number
  /** How the text was read (ADR-003); null until indexed. "partial": see `ocr_low_pages`. */
  read_mode: ReadMode | null
  /** Pages whose text comes from OCR. */
  ocr_pages: number
  /** Mean OCR confidence of those pages, 0-100. */
  ocr_confidence: number | null
  /** Badly read or unread pages (1-based). */
  ocr_low_pages: number[]
  /** PDF or image with its original file: OCR can be run again. */
  ocr_eligible: boolean
  progress: { current: number; total: number }
  has_file: boolean
  created_at: string | null
  indexed_at: string | null
}

export type ReadMode = 'native' | 'ocr' | 'partial'

/** Formats read by OCR, shown page by page through `documentPageUrl` (browsers cannot all show TIFF). */
export const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.tif', '.tiff']

export function isImageFile(filename: string): boolean {
  const name = filename.toLowerCase()
  return IMAGE_EXTENSIONS.some((ext) => name.endsWith(ext))
}

export interface Source {
  index: number
  document_id: string
  collection_id: string
  filename: string
  doc_title: string | null
  page: number | null
  section: string | null
  section_path: string[]
  snippet: string
  distance: number
  cited: boolean
  /** Page fetched through a connector: where it lives in its own system. */
  url?: string | null
  source_kind?: ConnectorKind | null
}

export type ConnectorKind = 'confluence' | 'notion'

/** A configured link to an external document system. Members see id, kind and name only. */
export interface Connection {
  id: string
  kind: ConnectorKind
  kind_label: string
  name: string
  base_url?: string | null
  account?: string | null
  is_enabled?: boolean
  created_at?: string | null
  updated_at?: string | null
}

export interface RemotePage {
  external_id: string
  title: string
  url: string
  location: string | null
  updated_at: string | null
}

/** A page attached to one conversation. */
export interface AttachedSource {
  id: string
  title: string
  url: string | null
  kind: ConnectorKind | null
  kind_label: string
  connection_id: string | null
  status: 'queued' | 'processing' | 'ready' | 'failed'
  error_message: string | null
  warnings: string[]
}

export interface AttachResult {
  conversation_id: string
  sources: AttachedSource[]
  errors: { reference: string; message: string }[]
}

export interface AnswerResult {
  answer: string
  sources: Source[]
  confidence_metric: number
  refused: boolean
  latency_seconds: number
  model: string
  standalone_question: string
  /** Searches actually run: the question, then the sub-queries planned by the model. */
  search_queries: string[]
  conversation_id: string
  message_id: string
}

export interface ChatMessage {
  id?: string
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  confidence?: number | null
  refused?: boolean
  model?: string | null
  latency_seconds?: number | null
  feedback?: number | null
  pending?: boolean
  error?: boolean
  /** Client side only: why the LLM gateway refused to answer, when it did. */
  code?: GatewayErrorCode
}

export interface ConversationSummary {
  id: string
  title: string
  collection_ids: string[]
  updated_at: string | null
}

/** "unknown": the gateway (or Ollama) could not be reached, nothing could be checked. */
export type ModelAvailability = 'available' | 'unavailable' | 'unknown'

export interface Model {
  id: string
  provider: string
  provider_label: string
  name: string
  is_local: boolean
  /** Boolean form of `availability`: true only when available. */
  available: boolean
  /** Absent from older answers and from the system page: see `modelAvailability()`. */
  availability?: ModelAvailability
}

export function modelAvailability(model: Pick<Model, 'available' | 'availability'>): ModelAvailability {
  return model.availability ?? (model.available ? 'available' : 'unavailable')
}

export interface ModelList {
  models: Model[]
  default: string
}

/** Administration › Modèles: one model of the catalogue, offered in the chat or not. */
export interface AdminModel extends Model {
  /** Offered in the chat selector (offered ones come first, in their order). */
  offered: boolean
  is_default: boolean
}

export interface AdminModels {
  /** "settings": chosen in the administration; "environment": LLM_MODELS / gateway defaults. */
  source: 'settings' | 'environment'
  gateway: boolean
  /** False when the LLM gateway cannot be reached: nothing can be changed. */
  catalogue_available: boolean
  default: string
  /** Gateway mode only; null when the console address is not safely known. */
  console_url?: string | null
  models: AdminModel[]
}

/** Fired after the administrator changes the offered models (the chat list is reloaded). */
export const MODELS_CHANGED = 'contexthub:models-changed'

export function modelsChanged(): void {
  forgetModels()
  window.dispatchEvent(new Event(MODELS_CHANGED))
}

// The model list depends on the signed-in user's session only: one request, dropped when the
// session ends (logout, expiry, another account).
let modelsRequest: Promise<ModelList> | null = null

export function forgetModels(): void {
  modelsRequest = null
}

export interface ApiKey {
  id: string
  name: string
  prefix: string
  created_at: string | null
  last_used_at: string | null
  revoked: boolean
  key?: string
}

export interface AuditEvent {
  id: number
  ts: string
  actor_id: string | null
  actor_email: string | null
  action: string
  outcome: string
  resource_type: string | null
  resource_id: string | null
  collection_id: string | null
  details: Record<string, unknown> | null
  ip: string | null
  hash: string
}

export interface GroupGrant {
  group: string
  level: 'viewer' | 'editor'
}

export interface Grant {
  user_id: string
  email: string
  full_name: string | null
  level: 'viewer' | 'editor'
}

// ---------------------------------------------------------------- auth

export const api = {
  authConfig: () => http.get<AuthConfig>('/auth/config').then((r) => r.data),
  login: (email: string, password: string) => http.post<User>('/auth/login', { email, password }).then((r) => r.data),
  logout: () => http.post('/auth/logout'),
  me: () => http.get<Me>('/auth/me').then((r) => r.data),
  changePassword: (current_password: string, new_password: string) =>
    http.post('/auth/password', { current_password, new_password }),
  apiKeys: () => http.get<ApiKey[]>('/auth/api-keys').then((r) => r.data),
  createApiKey: (name: string) => http.post<ApiKey>('/auth/api-keys', { name }).then((r) => r.data),
  revokeApiKey: (id: string) => http.delete(`/auth/api-keys/${id}`),

  // knowledge base
  collections: () => http.get<Collection[]>('/collections').then((r) => r.data),
  createCollection: (body: { name: string; description?: string; is_open?: boolean }) =>
    http.post<Collection>('/collections', body).then((r) => r.data),
  updateCollection: (id: string, body: { description?: string | null; is_open?: boolean }) =>
    http.patch<Collection>(`/collections/${id}`, body).then((r) => r.data),
  deleteCollection: (id: string) => http.delete(`/collections/${id}`),
  documents: (collectionId: string) =>
    http.get<StoredDocument[]>(`/collections/${collectionId}/documents`).then((r) => r.data),
  upload: (collectionId: string, file: File, onProgress?: (ratio: number) => void) => {
    const form = new FormData()
    form.append('file', file)
    return http
      .post<StoredDocument>(`/collections/${collectionId}/documents`, form, {
        onUploadProgress: (e) => e.total && onProgress?.(e.loaded / e.total),
      })
      .then((r) => r.data)
  },
  importUrl: (collectionId: string, url: string) =>
    http.post<StoredDocument>(`/collections/${collectionId}/documents/url`, { url }).then((r) => r.data),
  deleteDocument: (id: string) => http.delete(`/documents/${id}`),
  reindexDocument: (id: string) => http.post<StoredDocument>(`/documents/${id}/reindex`).then((r) => r.data),
  rerunOcr: (id: string, allPages = false) =>
    http.post<StoredDocument>(`/documents/${id}/ocr`, { all_pages: allPages }).then((r) => r.data),
  documentFileUrl: (id: string, page?: number | null) => `/api/documents/${id}/file${page ? `#page=${page}` : ''}`,
  /** One page of an imported image (TIFF frame) as PNG. */
  documentPageUrl: (id: string, page?: number | null) => `/api/documents/${id}/pages/${page && page > 0 ? page : 1}`,

  // chat
  /** Loaded once per session and shared (sidebar, chat): see `forgetModels()`. */
  models: (): Promise<ModelList> => {
    if (!modelsRequest) {
      modelsRequest = http.get<ModelList>('/models').then((r) => r.data)
      // A failed call is not kept: the next caller tries again.
      modelsRequest.catch(() => {
        modelsRequest = null
      })
    }
    return modelsRequest
  },
  conversations: () => http.get<ConversationSummary[]>('/conversations').then((r) => r.data),
  conversation: (id: string) =>
    http
      .get<{
        id: string
        title: string
        collection_ids: string[]
        messages: ChatMessage[]
        sources: AttachedSource[]
      }>(`/conversations/${id}`)
      .then((r) => r.data),
  renameConversation: (id: string, title: string) => http.patch(`/conversations/${id}`, { title }),
  deleteConversation: (id: string) => http.delete(`/conversations/${id}`),
  feedback: (messageId: string, value: -1 | 0 | 1, comment?: string) =>
    http.post(`/messages/${messageId}/feedback`, { value, comment }),

  // connectors
  connections: () => http.get<Connection[]>('/connections').then((r) => r.data),
  searchConnection: (id: string, params: { q?: string; space?: string; label?: string }) =>
    http.get<{ pages: RemotePage[] }>(`/connections/${id}/search`, { params }).then((r) => r.data.pages),
  conversationSources: (conversationId: string) =>
    http.get<AttachedSource[]>(`/conversations/${conversationId}/sources`).then((r) => r.data),
  attachSources: (body: { connection_id: string; references: string[]; conversation_id?: string }) =>
    http.post<AttachResult>('/chat/sources', body).then((r) => r.data),
  detachSource: (conversationId: string, documentId: string) =>
    http.delete(`/conversations/${conversationId}/sources/${documentId}`),
  adminConnections: () => http.get<Connection[]>('/admin/connections').then((r) => r.data),
  createConnection: (body: { kind: ConnectorKind; name: string; base_url?: string; account?: string; secret: string }) =>
    http.post<Connection>('/admin/connections', body).then((r) => r.data),
  updateConnection: (
    id: string,
    body: { name?: string; base_url?: string; account?: string; secret?: string; is_enabled?: boolean },
  ) => http.patch<Connection>(`/admin/connections/${id}`, body).then((r) => r.data),
  deleteConnection: (id: string) => http.delete(`/admin/connections/${id}`),
  testConnection: (id: string) =>
    http.post<{ ok: boolean; message: string }>(`/admin/connections/${id}/test`).then((r) => r.data),

  // admin
  users: () => http.get<User[]>('/admin/users').then((r) => r.data),
  createUser: (body: { email: string; full_name?: string; role: Role }) =>
    http.post<User & { temporary_password?: string }>('/admin/users', body).then((r) => r.data),
  updateUser: (id: string, body: Partial<Pick<User, 'full_name' | 'role' | 'is_active'>>) =>
    http.patch<User>(`/admin/users/${id}`, body).then((r) => r.data),
  resetPassword: (id: string) =>
    http.post<{ temporary_password: string }>(`/admin/users/${id}/reset-password`).then((r) => r.data),
  deleteUser: (id: string) =>
    http.delete<{ conversations_deleted: number; audit_events_redacted: number }>(`/admin/users/${id}`).then((r) => r.data),
  grants: (collectionId: string) => http.get<Grant[]>(`/admin/collections/${collectionId}/grants`).then((r) => r.data),
  setGrant: (collectionId: string, user_id: string, level: 'viewer' | 'editor') =>
    http.put(`/admin/collections/${collectionId}/grants`, { user_id, level }),
  revokeGrant: (collectionId: string, userId: string) =>
    http.delete(`/admin/collections/${collectionId}/grants/${userId}`),
  ssoGroups: () => http.get<string[]>('/admin/sso-groups').then((r) => r.data),
  groupGrants: (collectionId: string) =>
    http.get<GroupGrant[]>(`/admin/collections/${collectionId}/group-grants`).then((r) => r.data),
  setGroupGrant: (collectionId: string, group: string, level: 'viewer' | 'editor') =>
    http.put(`/admin/collections/${collectionId}/group-grants`, { group, level }),
  revokeGroupGrant: (collectionId: string, group: string) =>
    http.delete(`/admin/collections/${collectionId}/group-grants`, { params: { group } }),
  audit: (params: Record<string, string | number | undefined>) =>
    http.get<{ total: number; items: AuditEvent[] }>('/admin/audit', { params }).then((r) => r.data),
  auditExportUrl: (params: Record<string, string | undefined>) => {
    const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][])
    return `/api/admin/audit/export.csv?${query}`
  },
  verifyAudit: () =>
    http
      .get<{ valid: boolean; checked: number; broken_at: number | null; reason: string | null }>('/admin/audit/verify')
      .then((r) => r.data),
  system: () => http.get<SystemStatus>('/admin/system').then((r) => r.data),

  // offered models
  adminModels: () => http.get<AdminModels>('/admin/models').then((r) => r.data),
  setAdminModels: (body: { offered: string[]; default: string }) =>
    http.put<AdminModels>('/admin/models', body).then((r) => r.data),
  resetAdminModels: () => http.delete<AdminModels>('/admin/models').then((r) => r.data),

  // per-user budgets (held by the LLM gateway)
  userBudget: (userId: string) => http.get<UserBudgetState>(`/admin/users/${userId}/budget`).then((r) => r.data),
  setUserBudget: (userId: string, body: { limit: number; period: BudgetPeriodKind }) =>
    http.put<UserBudgetState>(`/admin/users/${userId}/budget`, body).then((r) => r.data),
  removeUserBudget: (userId: string) =>
    http.delete<UserBudgetState>(`/admin/users/${userId}/budget`).then((r) => r.data),
}

export interface SystemCheck {
  ok: boolean
  error?: string
  workers?: string[]
  models?: string[]
  /** Gateway only: whether consumption can be read (admin key configured). */
  consumption?: boolean
  /** Configuration remarks in French (gateway, workers, OCR), shown even when `ok` is true. */
  notes?: string[]
  /** OCR only. */
  enabled?: boolean
  available?: boolean
  engine?: string | null
  version?: string | null
  languages?: string[]
  configured_languages?: string[]
}

export interface SystemStatus {
  version: string
  environment: string
  checks: Record<string, SystemCheck>
  counts: Record<string, number>
  configuration: {
    models: Model[]
    embedding: { provider: string; model: string }
    retrieval: { max_distance: number; top_k: number; candidates: number; hybrid: boolean; reranker: string }
    ocr: {
      enabled: boolean
      available: boolean
      languages?: string
      min_page_chars?: number
      min_confidence?: number
      dpi?: number
      page_timeout_seconds?: number
      worker_concurrency?: number
    }
    url_ingestion: boolean
    sso: boolean
    retention_days: { audit: number; conversations: number }
    external_tracing: boolean
    upload_max_mb: number
  }
}

// ---------------------------------------------------------------- consumption (LLM gateway)

// Costs live in the LLM gateway: the API reads them there for the signed-in user (or, for an
// administrator, for everybody) and ContextHub stores none. Dates are inclusive "YYYY-MM-DD".

export interface UsageTotals {
  cost: number
  input_tokens: number
  output_tokens: number
  requests: number
}

export interface ModelUsage extends UsageTotals {
  model: string
}

export interface DailyUsage extends UsageTotals {
  date: string
}

/** Budget windows ContextHub offers; "month" is a sliding 30-day window, not a calendar month. */
export type BudgetPeriodKind = 'day' | 'week' | 'month'

/** Counted by the gateway over its own budget window, which may differ from the period shown. */
export interface UsageBudget {
  limit: number | null
  spent: number | null
  remaining: number | null
  /** As configured on the gateway, e.g. "30d". */
  period: string | null
  /** `period` in ContextHub's terms; null when the gateway window is another one. */
  period_kind: BudgetPeriodKind | null
  resets_at: string | null
  /** True: set from ContextHub (removable here); false: defined in the gateway console. */
  managed: boolean
}

/** GET/PUT/DELETE /admin/users/{id}/budget. */
export type UserBudgetState =
  | { available: true; user_id: string; budget: UsageBudget | null; currency: string }
  | { available: false; reason: string }

/**
 * Breakdowns the gateway may not provide; they are then empty and listed in `missing`.
 * "budgets" (administration only): per-user budgets were only partly read.
 */
export type UsageBreakdown = 'by_model' | 'daily' | 'budget' | 'by_user' | 'budgets'

export interface UsageReport {
  available: true
  gateway: string
  period: { from: string; to: string }
  currency: string
  totals: UsageTotals
  by_model: ModelUsage[]
  daily: DailyUsage[]
  budget: UsageBudget | null
  missing: UsageBreakdown[]
}

export interface UserUsage extends UsageTotals {
  /** ContextHub id; null when the gateway knows the caller under another kind of identifier. */
  user_id: string | null
  /** False for a deleted account or another application of the gateway: `label` is then neutral. */
  known: boolean
  email: string | null
  full_name: string | null
  label: string
  /** Known users only; null without a budget or when it could not be read (see `missing`). */
  budget: UsageBudget | null
}

/** Global report: there is no global budget, so `budget` is always null and never in `missing`. */
export interface AdminUsageReport extends UsageReport {
  budget: null
  missing: Exclude<UsageBreakdown, 'budget'>[]
  by_user: UserUsage[]
  /** False: budgets cannot be managed from ContextHub with this gateway configuration. */
  budgets_available: boolean
}

/**
 * Consumption is not tracked: no gateway, or a gateway without the key that reads its
 * spend. `reason` is written by the API for the caller (plain for a user, precise for an admin).
 */
export interface UsageUnavailable {
  available: false
  reason: string
}

export const usage = {
  me: (from?: string, to?: string) =>
    http.get<UsageReport | UsageUnavailable>('/usage/me', { params: { from, to } }).then((r) => r.data),
  admin: (from?: string, to?: string) =>
    http.get<AdminUsageReport | UsageUnavailable>('/admin/usage', { params: { from, to } }).then((r) => r.data),
}

// ---------------------------------------------------------------- streaming

/** Why the LLM gateway refused a chat call (`src/llm/gateway.py`). */
export type GatewayErrorCode = 'budget_exceeded' | 'rate_limited' | 'unreachable' | 'unavailable' | 'rejected'

export type StreamEvent =
  | { type: 'meta'; conversation_id: string; model: string }
  | { type: 'token'; content: string }
  | ({ type: 'final' } & AnswerResult)
  /** `code` and `status` are only present when the LLM gateway refused the call. */
  | { type: 'error'; message: string; code?: GatewayErrorCode; status?: number }
  | { type: 'done' }

/** A non-2xx answer outside axios (streaming), with the server's French message. */
export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** HTTP 402 is only ever the gateway's "budget reached" refusal. */
export function isBudgetExceeded(error: unknown): boolean {
  if (error instanceof ApiError) return error.status === 402
  return axios.isAxiosError(error) && error.response?.status === 402
}

export async function streamAsk(
  body: {
    question: string
    collection_ids?: string[]
    conversation_id?: string
    model?: string
    attached_only?: boolean
  },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...CSRF_HEADERS },
    body: JSON.stringify(body),
    signal,
  })
  if (resp.status === 401) window.dispatchEvent(new Event('contexthub:unauthorized'))
  if (!resp.ok || !resp.body) {
    let message = `Erreur ${resp.status}`
    try {
      const data = await resp.json()
      if (typeof data.detail === 'string') message = data.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(message, resp.status)
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const line = frame.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)) as StreamEvent)
      } catch {
        /* malformed frame — ignore */
      }
    }
  }
}
