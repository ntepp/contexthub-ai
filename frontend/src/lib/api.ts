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
  progress: { current: number; total: number }
  has_file: boolean
  created_at: string | null
  indexed_at: string | null
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
}

export interface AnswerResult {
  answer: string
  sources: Source[]
  confidence_metric: number
  refused: boolean
  latency_seconds: number
  model: string
  standalone_question: string
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
}

export interface ConversationSummary {
  id: string
  title: string
  collection_ids: string[]
  updated_at: string | null
}

export interface Model {
  id: string
  provider: string
  provider_label: string
  name: string
  is_local: boolean
  available: boolean
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
  documentFileUrl: (id: string, page?: number | null) => `/api/documents/${id}/file${page ? `#page=${page}` : ''}`,

  // chat
  models: () => http.get<{ models: Model[]; default: string }>('/models').then((r) => r.data),
  conversations: () => http.get<ConversationSummary[]>('/conversations').then((r) => r.data),
  conversation: (id: string) =>
    http
      .get<{ id: string; title: string; collection_ids: string[]; messages: ChatMessage[] }>(`/conversations/${id}`)
      .then((r) => r.data),
  renameConversation: (id: string, title: string) => http.patch(`/conversations/${id}`, { title }),
  deleteConversation: (id: string) => http.delete(`/conversations/${id}`),
  feedback: (messageId: string, value: -1 | 0 | 1, comment?: string) =>
    http.post(`/messages/${messageId}/feedback`, { value, comment }),

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
}

export interface SystemStatus {
  version: string
  environment: string
  checks: Record<string, { ok: boolean; error?: string; workers?: string[]; models?: string[] }>
  counts: Record<string, number>
  configuration: {
    models: Model[]
    embedding: { provider: string; model: string }
    retrieval: { max_distance: number; top_k: number; candidates: number; hybrid: boolean; reranker: string }
    ocr: { enabled: boolean; available: boolean }
    url_ingestion: boolean
    sso: boolean
    retention_days: { audit: number; conversations: number }
    external_tracing: boolean
    upload_max_mb: number
  }
}

// ---------------------------------------------------------------- streaming

export type StreamEvent =
  | { type: 'meta'; conversation_id: string; model: string }
  | { type: 'token'; content: string }
  | ({ type: 'final' } & AnswerResult)
  | { type: 'error'; message: string }
  | { type: 'done' }

export async function streamAsk(
  body: { question: string; collection_ids?: string[]; conversation_id?: string; model?: string },
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
    throw new Error(message)
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
