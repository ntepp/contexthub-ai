import { useEffect, useState, type FormEvent } from 'react'
import { ExternalLink, Search } from 'lucide-react'
import { api, errorMessage, type AttachResult, type Connection, type RemotePage } from '@/lib/api'
import { cn, formatDateTime } from '@/lib/utils'
import { Dialog } from '@/components/ui/Dialog'
import { Notice } from '@/components/ui/Notice'

interface Props {
  open: boolean
  onClose: () => void
  connections: Connection[]
  conversationId?: string
  onAttached: (result: AttachResult) => void
}

type Mode = 'link' | 'search'
const MAX_PAGES = 10

/** Pick pages in Confluence or Notion, by link or by search, and attach them to the conversation. */
export function AttachDialog({ open, onClose, connections, conversationId, onAttached }: Props) {
  const [connectionId, setConnectionId] = useState('')
  const [mode, setMode] = useState<Mode>('link')
  const [links, setLinks] = useState('')
  const [query, setQuery] = useState('')
  const [space, setSpace] = useState('')
  const [label, setLabel] = useState('')
  const [results, setResults] = useState<RemotePage[] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [searching, setSearching] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connection = connections.find((c) => c.id === connectionId) ?? connections[0]
  const confluence = connection?.kind === 'confluence'

  useEffect(() => {
    if (open) {
      setError(null)
      setResults(null)
      setPicked(new Set())
      setLinks('')
    }
  }, [open, connectionId])

  const references =
    mode === 'link'
      ? links
          .split(/\s+/)
          .map((l) => l.trim())
          .filter(Boolean)
      : [...picked]

  const search = async (e?: FormEvent) => {
    e?.preventDefault()
    if (!connection) return
    setSearching(true)
    setError(null)
    try {
      setResults(await api.searchConnection(connection.id, { q: query.trim(), space: space.trim(), label: label.trim() }))
      setPicked(new Set())
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSearching(false)
    }
  }

  const attach = async () => {
    if (!connection || references.length === 0) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.attachSources({
        connection_id: connection.id,
        references: references.slice(0, MAX_PAGES),
        conversation_id: conversationId,
      })
      onAttached(result)
      if (result.errors.length > 0) {
        setError(result.errors.map((e) => e.message).join(' '))
      } else {
        onClose()
      }
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const toggle = (id: string) =>
    setPicked((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else if (next.size < MAX_PAGES) next.add(id)
      return next
    })

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Joindre des pages à la conversation"
      description="Les pages choisies sont lues une fois, utilisées dans cette conversation uniquement, puis supprimées avec elle."
      wide
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            Annuler
          </button>
          <button className="btn-primary" onClick={attach} disabled={busy || references.length === 0}>
            {busy
              ? 'Lecture des pages…'
              : references.length > 1
                ? `Joindre ${references.length} pages`
                : 'Joindre la page'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}

        <div className="flex flex-wrap items-end gap-4">
          <div className="min-w-56 flex-1">
            <label className="label" htmlFor="attach-connection">
              Source
            </label>
            <select
              id="attach-connection"
              className="input"
              value={connection?.id ?? ''}
              onChange={(e) => setConnectionId(e.target.value)}
            >
              {connections.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} ({c.kind_label})
                </option>
              ))}
            </select>
          </div>
          <div className="flex rounded-md border border-rule p-0.5" role="tablist" aria-label="Façon de choisir les pages">
            {(
              [
                ['link', 'Par lien'],
                ['search', 'Rechercher'],
              ] as const
            ).map(([value, text]) => (
              <button
                key={value}
                role="tab"
                aria-selected={mode === value}
                className={cn(
                  'rounded px-3 py-1.5 text-sm',
                  mode === value ? 'bg-ledger text-white' : 'text-slate hover:text-ink',
                )}
                onClick={() => setMode(value)}
              >
                {text}
              </button>
            ))}
          </div>
        </div>

        {mode === 'link' ? (
          <div>
            <label className="label" htmlFor="attach-links">
              Adresses des pages
            </label>
            <textarea
              id="attach-links"
              rows={4}
              className="input font-mono text-xs"
              value={links}
              onChange={(e) => setLinks(e.target.value)}
              placeholder={
                confluence
                  ? 'https://entreprise.atlassian.net/wiki/spaces/RH/pages/123456/Politique-de-teletravail'
                  : 'https://www.notion.so/entreprise/Guide-d-accueil-0123456789abcdef0123456789abcdef'
              }
            />
            <p className="mt-1 text-xs text-slate">Une adresse par ligne, {MAX_PAGES} pages au plus.</p>
          </div>
        ) : (
          <div className="space-y-3">
            <form className="flex flex-wrap items-end gap-3" onSubmit={search}>
              <div className="min-w-56 flex-1">
                <label className="label" htmlFor="attach-query">
                  Mots du titre ou du contenu
                </label>
                <input id="attach-query" className="input" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
              {confluence && (
                <>
                  <div className="w-32">
                    <label className="label" htmlFor="attach-space">
                      Espace
                    </label>
                    <input
                      id="attach-space"
                      className="input"
                      value={space}
                      onChange={(e) => setSpace(e.target.value)}
                      placeholder="RH"
                    />
                  </div>
                  <div className="w-36">
                    <label className="label" htmlFor="attach-label">
                      Étiquette
                    </label>
                    <input id="attach-label" className="input" value={label} onChange={(e) => setLabel(e.target.value)} />
                  </div>
                </>
              )}
              <button type="submit" className="btn-secondary" disabled={searching}>
                <Search size={14} />
                {searching ? 'Recherche…' : 'Rechercher'}
              </button>
            </form>

            {results !== null &&
              (results.length === 0 ? (
                <Notice>Aucune page ne correspond. Essayez d'autres mots, ou collez directement le lien de la page.</Notice>
              ) : (
                <ul className="max-h-72 divide-y divide-rule overflow-y-auto rounded-md border border-rule" aria-label="Pages trouvées">
                  {results.map((page) => (
                    <li key={page.external_id} className="flex items-start gap-3 px-3 py-2">
                      <input
                        id={`page-${page.external_id}`}
                        type="checkbox"
                        className="mt-1"
                        checked={picked.has(page.external_id)}
                        onChange={() => toggle(page.external_id)}
                      />
                      <label htmlFor={`page-${page.external_id}`} className="min-w-0 flex-1 cursor-pointer">
                        <span className="block truncate text-sm font-medium">{page.title}</span>
                        <span className="block truncate text-xs text-slate">
                          {[page.location, page.updated_at && `modifiée le ${formatDateTime(page.updated_at)}`]
                            .filter(Boolean)
                            .join(', ')}
                        </span>
                      </label>
                      <a
                        href={page.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        className="btn-ghost p-1.5"
                        aria-label={`Ouvrir « ${page.title} » dans ${connection?.kind_label}`}
                      >
                        <ExternalLink size={14} />
                      </a>
                    </li>
                  ))}
                </ul>
              ))}
          </div>
        )}
      </div>
    </Dialog>
  )
}
