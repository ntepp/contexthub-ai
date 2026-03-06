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

// Ask
export interface AskResponse {
  answer: string
  sources: { source: string; page: number | null }[]
}
export const askQuestion = (question: string, collection = 'default') =>
  api.post<AskResponse>('/ask', { question, collection }).then((r) => r.data)

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
