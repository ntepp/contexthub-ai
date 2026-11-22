import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Link } from 'react-router-dom'
import { ChartColumn, ThumbsDown, ThumbsUp } from 'lucide-react'
import { api, type ChatMessage, type StreamStep } from '@/lib/api'
import { cn } from '@/lib/utils'

/** Shown under the question until the first words of the answer arrive. */
const STEP_LABELS: Record<StreamStep, string> = {
  condense: 'Lecture de votre question…',
  retrieve: 'Recherche dans vos documents…',
  rerank: 'Relecture des passages…',
  plan: 'Recherche dans vos documents…',
  generate: 'Rédaction…',
}

const CITATION = /\[(\d+(?:\s*[,;]\s*\d+)*)\]/g

/** Turn "[1]" and "[2, 3]" into markdown links the renderer swaps for citation chips. */
function linkCitations(text: string, valid: Set<number>): string {
  return text.replace(CITATION, (whole, group: string) => {
    const numbers = group.split(/[,;]/).map((n) => parseInt(n.trim(), 10))
    if (!numbers.every((n) => valid.has(n))) return whole
    return numbers.map((n) => `[${n}](#cite-${n})`).join('')
  })
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

  if (message.error && message.code === 'budget_exceeded') {
    return (
      <div role="alert" className="rounded-md border border-refusal/30 bg-refusal-soft px-4 py-3 text-sm text-refusal">
        <p className="font-medium">Budget atteint</p>
        <p className="mt-1">{message.content}</p>
        <Link to="/consommation" className="mt-2 inline-flex items-center gap-1.5 font-medium underline underline-offset-2 hover:text-ink">
          <ChartColumn size={14} />
          Voir ma consommation
        </Link>
      </div>
    )
  }

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
                        // Touch screens: a 40 px hit area around the chip, without moving the text.
                        "max-md:relative max-md:after:absolute max-md:after:-inset-2.5 max-md:after:content-['']",
                        activeSource === n ? 'bg-ledger text-white' : 'bg-ledger-soft text-ledger hover:bg-ledger hover:text-white',
                      )}
                      aria-label={`Voir la source ${n}`}
                    >
                      {n}
                    </button>
                  )
                }
                // The answer is model output over documents anyone may have written: only
                // plain web links become clickable.
                if (!href || !/^https?:\/\//i.test(href)) return <>{children}</>
                return (
                  <a href={href} target="_blank" rel="noreferrer noopener" className="text-ledger underline">
                    {children}
                  </a>
                )
              },
              // Never load an image named by the answer: its address could carry data out
              // (prompt injection through a document). The alternative text is shown instead.
              img: ({ alt }) => <>{alt}</>,
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
          <p role="status" aria-live="polite" className="flex items-center gap-2 font-sans text-sm text-faint">
            {streaming && <span aria-hidden className="inline-block h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-ledger" />}
            {STEP_LABELS[message.step ?? 'retrieve']}
          </p>
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
          {/* No "confidence" indicator on purpose: on the MiFID benchmark the retrieval score
              does not separate right from wrong answers (docs/evaluation.md). */}
          {message.latency_seconds != null && <span>{message.latency_seconds.toFixed(1).replace('.', ',')} s</span>}
          {message.model && <span>{message.model.split(':').slice(1).join(':')}</span>}
          <span className="ml-auto flex gap-1">
            <button
              onClick={() => sendFeedback(1)}
              className={cn('rounded p-1 hover:bg-wash max-md:p-2.5', feedback === 1 ? 'text-ledger' : 'text-faint')}
              aria-label="Réponse utile"
              aria-pressed={feedback === 1}
            >
              <ThumbsUp size={14} />
            </button>
            <button
              onClick={() => sendFeedback(-1)}
              className={cn('rounded p-1 hover:bg-wash max-md:p-2.5', feedback === -1 ? 'text-danger' : 'text-faint')}
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
