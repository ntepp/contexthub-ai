import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ThumbsDown, ThumbsUp } from 'lucide-react'
import { api, type ChatMessage } from '@/lib/api'
import { cn } from '@/lib/utils'

const CITATION = /\[(\d+(?:\s*[,;]\s*\d+)*)\]/g

/** Turn "[1]" and "[2, 3]" into markdown links the renderer swaps for citation chips. */
function linkCitations(text: string, valid: Set<number>): string {
  return text.replace(CITATION, (whole, group: string) => {
    const numbers = group.split(/[,;]/).map((n) => parseInt(n.trim(), 10))
    if (!numbers.every((n) => valid.has(n))) return whole
    return numbers.map((n) => `[${n}](#cite-${n})`).join('')
  })
}

export function relevanceLabel(confidence: number | null | undefined): string | null {
  if (confidence == null || confidence <= 0) return null
  if (confidence >= 0.55) return 'élevée'
  if (confidence >= 0.4) return 'moyenne'
  return 'faible'
}

interface Props {
  message: ChatMessage
  streaming: boolean
  activeSource: number | null
  onCite: (index: number) => void
}

export function Answer({ message, streaming, activeSource, onCite }: Props) {
  const [feedback, setFeedback] = useState<number | null>(message.feedback ?? null)
  const sources = message.sources ?? []
  const valid = useMemo(() => new Set(sources.map((s) => s.index)), [sources])
  const content = useMemo(() => linkCitations(message.content, valid), [message.content, valid])

  if (message.error) {
    return <p className="rounded-md border border-danger/30 bg-danger-soft px-4 py-3 text-sm text-danger">{message.content}</p>
  }

  if (message.refused && !streaming) {
    return (
      <div className="rounded-md border border-refusal/30 bg-refusal-soft px-4 py-3">
        <p className="font-serif text-answer text-refusal">{message.content}</p>
        <p className="mt-1 text-sm text-refusal/80">
          Aucun passage des documents accessibles ne répond à cette question. Reformulez-la, ou vérifiez que le texte
          concerné a bien été importé.
        </p>
      </div>
    )
  }

  const sendFeedback = async (value: -1 | 1) => {
    if (!message.id) return
    const next = feedback === value ? 0 : value
    setFeedback(next || null)
    await api.feedback(message.id, next as -1 | 0 | 1).catch(() => setFeedback(feedback))
  }

  const relevance = relevanceLabel(message.confidence)
  const cited = sources.filter((s) => s.cited).length

  return (
    <div>
      <div className="answer">
        {message.content ? (
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              a: ({ href, children }) => {
                if (href?.startsWith('#cite-')) {
                  const n = parseInt(href.slice(6), 10)
                  return (
                    <button
                      type="button"
                      onClick={() => onCite(n)}
                      className={cn(
                        'mx-0.5 inline-flex h-5 min-w-5 -translate-y-0.5 items-center justify-center rounded px-1 align-middle font-sans text-xs font-semibold transition-colors',
                        activeSource === n ? 'bg-ledger text-white' : 'bg-ledger-soft text-ledger hover:bg-ledger hover:text-white',
                      )}
                      aria-label={`Voir la source ${n}`}
                    >
                      {n}
                    </button>
                  )
                }
                return (
                  <a href={href} target="_blank" rel="noreferrer" className="text-ledger underline">
                    {children}
                  </a>
                )
              },
              table: ({ children }) => (
                <div className="my-3 overflow-x-auto">
                  <table className="w-full border-collapse font-sans text-sm">{children}</table>
                </div>
              ),
              th: ({ children }) => <th className="table-head">{children}</th>,
              td: ({ children }) => <td className="table-cell">{children}</td>,
            }}
          >
            {content}
          </ReactMarkdown>
        ) : (
          <p className="font-sans text-sm text-faint">Recherche dans vos documents…</p>
        )}
        {streaming && message.content && <span className="ml-0.5 inline-block h-4 w-0.5 animate-pulse bg-ledger align-middle" />}
      </div>

      {!streaming && message.id && (
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate">
          {sources.length > 0 && (
            <span>
              {cited > 0 ? `${cited} source${cited > 1 ? 's' : ''} citée${cited > 1 ? 's' : ''}` : `${sources.length} sources consultées`}
            </span>
          )}
          {relevance && (
            <span title="Proximité entre la question et les passages retrouvés. Elle ne garantit pas l'exactitude de la réponse : vérifiez les sources.">
              Pertinence des sources : {relevance}
            </span>
          )}
          {message.latency_seconds != null && <span>{message.latency_seconds.toFixed(1).replace('.', ',')} s</span>}
          {message.model && <span>{message.model.split(':').slice(1).join(':')}</span>}
          <span className="ml-auto flex gap-1">
            <button
              onClick={() => sendFeedback(1)}
              className={cn('rounded p-1 hover:bg-wash', feedback === 1 ? 'text-ledger' : 'text-faint')}
              aria-label="Réponse utile"
              aria-pressed={feedback === 1}
            >
              <ThumbsUp size={14} />
            </button>
            <button
              onClick={() => sendFeedback(-1)}
              className={cn('rounded p-1 hover:bg-wash', feedback === -1 ? 'text-danger' : 'text-faint')}
              aria-label="Réponse incorrecte ou incomplète"
              aria-pressed={feedback === -1}
            >
              <ThumbsDown size={14} />
            </button>
          </span>
        </div>
      )}
    </div>
  )
}
