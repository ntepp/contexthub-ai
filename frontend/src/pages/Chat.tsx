import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowUp, BookOpen, CloudUpload, Square } from 'lucide-react'
import { api, errorMessage, streamAsk, type ChatMessage, type Model } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn } from '@/lib/utils'
import { Answer } from '@/components/chat/Answer'
import { SourcePanel } from '@/components/chat/SourcePanel'
import { CONVERSATIONS_CHANGED } from '@/components/layout/Sidebar'

const ALL = '__all__'

export function Chat() {
  const { conversationId } = useParams()
  const navigate = useNavigate()
  const { user, refresh } = useAuth()
  const collections = user?.collections ?? []

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [scope, setScope] = useState<string>(ALL)
  const [models, setModels] = useState<Model[]>([])
  const [model, setModel] = useState<string>('')
  const [panel, setPanel] = useState<{ message: number; source: number } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)
  // A conversation created by the current stream must not be reloaded from the server mid-answer.
  const createdHere = useRef<string | null>(null)

  useEffect(() => {
    refresh() // access rights may have changed since login
    api
      .models()
      .then((data) => {
        setModels(data.models)
        setModel((current) => current || data.default)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    setPanel(null)
    if (!conversationId) {
      setMessages([])
      inputRef.current?.focus()
      return
    }
    if (createdHere.current === conversationId) return
    api
      .conversation(conversationId)
      .then((c) => {
        setMessages(c.messages)
        if (c.collection_ids.length === 1) setScope(c.collection_ids[0])
        else setScope(ALL)
      })
      .catch(() => navigate('/', { replace: true }))
  }, [conversationId, navigate])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages])

  const updateLast = (patch: (m: ChatMessage) => ChatMessage) =>
    setMessages((list) => {
      const next = [...list]
      next[next.length - 1] = patch(next[next.length - 1])
      return next
    })

  const send = useCallback(async () => {
    const question = input.trim()
    if (!question || streaming) return
    setInput('')
    setPanel(null)
    setMessages((list) => [...list, { role: 'user', content: question }, { role: 'assistant', content: '', pending: true }])
    setStreaming(true)
    const abort = new AbortController()
    abortRef.current = abort
    let created: string | null = null
    try {
      await streamAsk(
        {
          question,
          conversation_id: conversationId,
          collection_ids: scope === ALL ? undefined : [scope],
          model: model || undefined,
        },
        (event) => {
          if (event.type === 'meta' && !conversationId) {
            created = event.conversation_id
            createdHere.current = event.conversation_id
          } else if (event.type === 'token') {
            updateLast((m) => ({ ...m, content: m.content + event.content }))
          } else if (event.type === 'final') {
            updateLast(() => ({
              id: event.message_id,
              role: 'assistant',
              content: event.answer,
              sources: event.sources,
              confidence: event.confidence_metric,
              refused: event.refused,
              model: event.model,
              latency_seconds: event.latency_seconds,
            }))
          } else if (event.type === 'error') {
            updateLast(() => ({ role: 'assistant', content: event.message, error: true }))
          }
        },
        abort.signal,
      )
    } catch (err) {
      if (!abort.signal.aborted) {
        updateLast(() => ({ role: 'assistant', content: errorMessage(err), error: true }))
      } else {
        updateLast((m) => ({ ...m, pending: false, content: m.content || 'Réponse interrompue.' }))
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
      // Navigate only once the answer is complete, so the page is never re-rendered mid-stream.
      if (created) {
        navigate(`/c/${created}`, { replace: true })
        window.dispatchEvent(new Event(CONVERSATIONS_CHANGED))
      }
    }
  }, [input, streaming, conversationId, scope, model, navigate])

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  const selectedModel = models.find((m) => m.id === model)
  const panelSources = panel ? messages[panel.message]?.sources ?? [] : []
  const noCollections = collections.length === 0

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-3 border-b border-rule bg-surface px-6 py-2.5">
          <label className="flex items-center gap-2 text-sm text-slate">
            <BookOpen size={16} />
            <span className="sr-only">Périmètre de recherche</span>
            <select
              className="rounded-md border border-rule bg-surface py-1 pl-2 pr-7 text-sm text-ink"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              disabled={streaming}
            >
              <option value={ALL}>Toutes mes collections</option>
              {collections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
          {models.length > 1 && (
            <label className="flex items-center gap-2 text-sm text-slate">
              <span className="sr-only">Modèle</span>
              <select
                className="rounded-md border border-rule bg-surface py-1 pl-2 pr-7 text-sm text-ink"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={streaming}
              >
                {models.map((m) => (
                  <option key={m.id} value={m.id} disabled={!m.available}>
                    {m.name} ({m.is_local ? 'local' : m.provider_label}){m.available ? '' : ', indisponible'}
                  </option>
                ))}
              </select>
            </label>
          )}
          {selectedModel && !selectedModel.is_local && (
            <span className="flex items-center gap-1.5 rounded-md bg-cloud-soft px-2 py-1 text-xs text-cloud">
              <CloudUpload size={14} />
              Les extraits utiles de vos documents sont envoyés à {selectedModel.provider_label}.
            </span>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-6 py-8">
            {messages.length === 0 ? (
              <div className="pt-[12vh]">
                <h1 className="font-serif text-3xl leading-tight">Que cherchez-vous dans vos textes ?</h1>
                <p className="mt-3 max-w-xl text-slate">
                  Chaque réponse s'appuie uniquement sur les documents de vos collections et renvoie à la page exacte.
                  Si les documents ne permettent pas de répondre, ContextHub le dit.
                </p>
                {noCollections && (
                  <p className="mt-6 rounded-md border border-refusal/30 bg-refusal-soft px-4 py-3 text-sm text-refusal">
                    Vous n'avez encore accès à aucune collection. Demandez un accès à votre administrateur.
                  </p>
                )}
              </div>
            ) : (
              <div className="space-y-8">
                {messages.map((m, i) =>
                  m.role === 'user' ? (
                    <div key={i} className="flex justify-end">
                      <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-wash px-4 py-2.5 text-ink">{m.content}</p>
                    </div>
                  ) : (
                    <Answer
                      key={i}
                      message={m}
                      streaming={streaming && i === messages.length - 1}
                      activeSource={panel?.message === i ? panel.source : null}
                      onCite={(source) => setPanel({ message: i, source })}
                    />
                  ),
                )}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        <div className="border-t border-rule bg-surface px-6 py-4">
          <div className="mx-auto max-w-3xl">
            <div className="flex items-end gap-2 rounded-lg border border-rule bg-surface p-2 focus-within:border-ledger">
              <label htmlFor="question" className="sr-only">
                Votre question
              </label>
              <textarea
                id="question"
                ref={inputRef}
                rows={1}
                value={input}
                disabled={noCollections}
                onChange={(e) => {
                  setInput(e.target.value)
                  e.currentTarget.style.height = 'auto'
                  e.currentTarget.style.height = `${Math.min(e.currentTarget.scrollHeight, 200)}px`
                }}
                onKeyDown={onKeyDown}
                placeholder="Posez une question sur vos documents. Maj + Entrée pour un saut de ligne."
                className="max-h-[200px] flex-1 resize-none bg-transparent px-2 py-1.5 text-ink placeholder:text-faint focus:outline-none"
              />
              <button
                onClick={streaming ? () => abortRef.current?.abort() : send}
                disabled={!streaming && !input.trim()}
                className={cn('btn h-9 w-9 shrink-0 p-0', streaming ? 'bg-wash text-ink' : 'bg-ledger text-white hover:bg-ledger-dark')}
                aria-label={streaming ? 'Interrompre la réponse' : 'Envoyer la question'}
              >
                {streaming ? <Square size={14} fill="currentColor" /> : <ArrowUp size={16} />}
              </button>
            </div>
            <p className="mt-2 text-xs text-faint">
              Outil d'aide à la recherche documentaire. Vérifiez les sources citées ; une réponse ne constitue pas un avis
              juridique.
            </p>
          </div>
        </div>
      </div>

      {panel && panelSources.length > 0 && (
        <SourcePanel
          sources={panelSources}
          active={panel.source}
          onSelect={(source) => setPanel({ ...panel, source })}
          onClose={() => setPanel(null)}
        />
      )}
    </div>
  )
}
