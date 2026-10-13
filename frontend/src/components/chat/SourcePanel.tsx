import { useEffect, useRef, useState } from 'react'
import { ExternalLink, FileText, X } from 'lucide-react'
import { api, type Source } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Dialog } from '@/components/ui/Dialog'

interface Props {
  sources: Source[]
  active: number | null
  onSelect: (index: number) => void
  onClose: () => void
}

export function SourcePanel({ sources, active, onSelect, onClose }: Props) {
  const [viewing, setViewing] = useState<Source | null>(null)
  const refs = useRef<Record<number, HTMLElement | null>>({})
  const ordered = [...sources].sort((a, b) => Number(b.cited) - Number(a.cited) || a.index - b.index)

  useEffect(() => {
    if (active != null) refs.current[active]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [active])

  return (
    <aside className="flex w-[26rem] shrink-0 flex-col bg-surface shadow-panel" aria-label="Sources de la réponse">
      <div className="flex items-center justify-between border-b border-rule px-5 py-3">
        <h2 className="text-sm font-semibold">Sources</h2>
        <button className="btn-ghost p-1.5" onClick={onClose} aria-label="Fermer les sources">
          <X size={16} />
        </button>
      </div>
      <ol className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
        {ordered.map((s) => (
          <li
            key={s.index}
            ref={(el) => (refs.current[s.index] = el)}
            className={cn(
              'cursor-pointer rounded-md border p-3 transition-colors',
              active === s.index ? 'border-ledger bg-ledger-soft/40' : 'border-rule hover:border-slate/40',
              !s.cited && 'opacity-80',
            )}
            onClick={() => onSelect(s.index)}
          >
            <div className="flex items-start gap-2">
              <span
                className={cn(
                  'mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded px-1 text-xs font-semibold',
                  s.cited ? 'bg-ledger text-white' : 'bg-wash text-slate',
                )}
              >
                {s.index}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium" title={s.filename}>
                  {s.filename}
                </p>
                <p className="text-xs text-slate">
                  {s.page ? `Page ${s.page}` : 'Document sans pagination'}
                  {!s.cited && ', consultée mais non citée'}
                </p>
              </div>
            </div>
            {s.section_path.length > 0 && (
              <p className="mt-2 text-xs leading-relaxed text-slate">{s.section_path.join(' › ')}</p>
            )}
            <blockquote className="mt-2 line-clamp-6 border-l-2 border-ledger/40 pl-3 font-serif text-sm leading-relaxed text-ink">
              {s.snippet}
            </blockquote>
            <button
              className="btn-ghost mt-2 -ml-2 px-2 py-1 text-xs"
              onClick={(e) => {
                e.stopPropagation()
                setViewing(s)
              }}
            >
              <FileText size={14} />
              {s.page ? `Ouvrir à la page ${s.page}` : 'Ouvrir le document'}
            </button>
          </li>
        ))}
      </ol>

      <Dialog
        open={viewing !== null}
        onOpenChange={(open) => !open && setViewing(null)}
        title={viewing?.filename ?? ''}
        description={viewing?.section_path.join(' › ')}
        wide
      >
        {viewing && (
          <div className="space-y-3">
            {viewing.filename.toLowerCase().endsWith('.pdf') ? (
              <iframe
                title={`${viewing.filename}, page ${viewing.page ?? 1}`}
                src={api.documentFileUrl(viewing.document_id, viewing.page)}
                className="h-[72vh] w-full rounded border border-rule"
              />
            ) : (
              <blockquote className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap border-l-2 border-ledger/40 pl-4 font-serif text-sm leading-relaxed">
                {viewing.snippet}
              </blockquote>
            )}
            <a
              className="btn-secondary"
              href={api.documentFileUrl(viewing.document_id, viewing.page)}
              target="_blank"
              rel="noreferrer"
            >
              <ExternalLink size={14} />
              Ouvrir dans un nouvel onglet
            </a>
          </div>
        )}
      </Dialog>
    </aside>
  )
}
