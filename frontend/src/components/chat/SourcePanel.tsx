import { useEffect, useRef, useState } from 'react'
import { ExternalLink, FileText, X } from 'lucide-react'
import { api, isImageFile, type Source } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Dialog } from '@/components/ui/Dialog'
import { LG, useMediaQuery } from '@/lib/useMediaQuery'

interface Props {
  sources: Source[]
  active: number | null
  onSelect: (index: number) => void
  onClose: () => void
}

const KIND_LABELS: Record<string, string> = { confluence: 'Confluence', notion: 'Notion' }

function kindLabel(source: Source): string {
  return KIND_LABELS[source.source_kind ?? ''] ?? 'la source externe'
}

export function SourcePanel({ sources, active, onSelect, onClose }: Props) {
  const [viewing, setViewing] = useState<Source | null>(null)
  const refs = useRef<Record<number, HTMLElement | null>>({})
  const closeButton = useRef<HTMLButtonElement>(null)
  const ordered = [...sources].sort((a, b) => Number(b.cited) - Number(a.cited) || a.index - b.index)
  // Large screens: a column beside the answer. Below lg it covers the conversation (full screen on
  // phones, a sheet on the right on tablets): it then takes the focus and closes with Escape.
  const docked = useMediaQuery(LG)

  useEffect(() => {
    if (active != null) refs.current[active]?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [active])

  useEffect(() => {
    if (docked) return
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    closeButton.current?.focus()
    return () => {
      if (opener?.isConnected) opener.focus()
    }
  }, [docked])

  useEffect(() => {
    // While the document viewer is open, Escape belongs to it.
    if (docked || viewing) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !event.defaultPrevented) onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [docked, viewing, onClose])

  return (
    <>
      {!docked && <div className="fixed inset-0 z-30 bg-ink/30 max-md:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        className="flex w-[26rem] shrink-0 flex-col bg-surface shadow-panel max-lg:fixed max-lg:inset-y-0 max-lg:right-0 max-lg:z-30 max-lg:max-w-full max-md:w-full max-md:pb-[env(safe-area-inset-bottom)] max-md:pt-[env(safe-area-inset-top)]"
        aria-label="Sources de la réponse"
      >
        <div className="flex items-center justify-between border-b border-rule px-5 py-3 max-md:py-1.5 max-md:pl-4 max-md:pr-1.5">
          <h2 className="text-sm font-semibold">Sources</h2>
          <button ref={closeButton} className="btn-ghost p-1.5" onClick={onClose} aria-label="Fermer les sources">
            <X size={16} className="max-md:h-5 max-md:w-5" />
          </button>
        </div>
        <ol className="flex-1 space-y-3 overflow-y-auto overscroll-contain px-5 py-4 max-md:px-4">
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
                  <p className="truncate text-sm font-medium" title={s.doc_title ?? s.filename}>
                    {s.doc_title ?? s.filename}
                  </p>
                  <p className="truncate text-xs text-slate" title={s.filename}>
                    {s.url ? kindLabel(s) : s.page ? `Page ${s.page}` : 'Document sans pagination'}
                    {s.doc_title && `, ${s.filename}`}
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
              {s.url ? (
                <a
                  className="btn-ghost mt-2 -ml-2 px-2 py-1 text-xs"
                  href={s.url}
                  target="_blank"
                  rel="noreferrer noopener"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink size={14} />
                  Ouvrir dans {kindLabel(s)}
                </a>
              ) : (
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
              )}
            </li>
          ))}
        </ol>

        <Dialog
          open={viewing !== null}
          onOpenChange={(open) => !open && setViewing(null)}
          title={viewing?.doc_title ?? viewing?.filename ?? ''}
          description={viewing?.section_path.join(' › ')}
          wide
        >
          {viewing && (
            // Phones show PDFs inline poorly (or not at all): the new-tab link comes first there.
            <div className="space-y-3 max-md:flex max-md:flex-col max-md:gap-3 max-md:space-y-0">
              {isImageFile(viewing.filename) ? (
                <div className="max-h-[72vh] overflow-auto rounded border border-rule bg-wash">
                  <img
                    src={api.documentPageUrl(viewing.document_id, viewing.page)}
                    alt={`${viewing.filename}, page ${viewing.page ?? 1} (document numérisé)`}
                    className="mx-auto block max-w-full"
                  />
                </div>
              ) : viewing.filename.toLowerCase().endsWith('.pdf') ? (
                <iframe
                  title={`${viewing.filename}, page ${viewing.page ?? 1}`}
                  src={api.documentFileUrl(viewing.document_id, viewing.page)}
                  className="h-[72vh] w-full rounded border border-rule max-md:h-[60dvh]"
                />
              ) : (
                <blockquote className="max-h-[60vh] overflow-y-auto whitespace-pre-wrap border-l-2 border-ledger/40 pl-4 font-serif text-sm leading-relaxed">
                  {viewing.snippet}
                </blockquote>
              )}
              <a
                className="btn-secondary max-md:order-first"
                href={
                  isImageFile(viewing.filename)
                    ? api.documentPageUrl(viewing.document_id, viewing.page)
                    : api.documentFileUrl(viewing.document_id, viewing.page)
                }
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
    </>
  )
}
