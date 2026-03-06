import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

// Collections
export const getCollections = () => api.get('/collections').then((r) => r.data)
export const createCollection = (name: string, description?: string) =>
  api.post('/collections', { name, description }).then((r) => r.data)

// Upload
export const uploadPDF = (file: File, collection = 'default') => {
  const form = new FormData()
  form.append('file', file)
  return api
    .post<{ task_id: string; status: string; collection: string }>(
      `/upload?collection=${encodeURIComponent(collection)}`,
      form,
      { headers: { 'Content-Type': 'multipart/form-data' } },
    )
    .then((r) => r.data)
}

export const getTaskStatus = (taskId: string) =>
  api.get(`/tasks/${taskId}`).then((r) => r.data)

// Ask (non-streaming, kept for CLI compat)
export interface AskResponse {
  answer: string
  sources: { source: string; page: number | null }[]
}
export const askQuestion = (question: string, collection = 'default') =>
  api.post<AskResponse>('/ask', { question, collection }).then((r) => r.data)

// Streaming ask via SSE
export interface StreamCallbacks {
  onToken: (token: string) => void
  onSources: (sources: AskResponse['sources']) => void
  onDone: () => void
  onError?: (err: Error) => void
  signal?: AbortSignal
}

export async function streamAnswer(
  question: string,
  collection = 'default',
  { onToken, onSources, onDone, onError, signal }: StreamCallbacks,
): Promise<void> {
  const resp = await fetch('/api/ask/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, collection }),
    signal,
  })

  if (!resp.ok || !resp.body) {
    onError?.(new Error(`HTTP ${resp.status}`))
    return
  }

  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const event = JSON.parse(line.slice(6))
        if (event.type === 'token') onToken(event.content)
        else if (event.type === 'sources') onSources(event.sources)
        else if (event.type === 'done') onDone()
      } catch {
        // malformed line — skip
      }
    }
  }
}

// Documents
export interface StoredDocument {
  id: string
  filename: string
  collection: string
  status: 'indexing' | 'ready' | 'failed'
  chunk_count: number
  created_at: string
}
export const getDocuments = (collection: string) =>
  api.get<StoredDocument[]>(`/documents?collection=${encodeURIComponent(collection)}`).then((r) => r.data)

// Models
export interface Model {
  id: string
  provider: 'ollama' | 'openai'
  name: string
}
export const getModels = () =>
  api.get<{ models: Model[] }>('/models').then((r) => r.data.models)
