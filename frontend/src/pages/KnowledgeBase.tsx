import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, FileText, FolderPlus, Link2, RefreshCw, Settings, Trash2, Upload } from 'lucide-react'
import { api, errorMessage, type Collection, type StoredDocument } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatBytes, formatDateTime, plural } from '@/lib/utils'
import { ConfirmDialog, Dialog } from '@/components/ui/Dialog'
import { Notice, PageHeader } from '@/components/ui/Notice'

const ACCEPT = '.pdf,.docx,.txt,.md,.html,.htm'
const STATUS_LABEL: Record<StoredDocument['status'], string> = {
  queued: 'En attente',
  processing: 'Indexation',
  ready: 'Prêt',
  failed: 'Échec',
}

interface UploadState {
  name: string
  ratio: number
  error?: string
}

function StatusCell({ doc }: { doc: StoredDocument }) {
  if (doc.status === 'processing' || doc.status === 'queued') {
    const { current, total } = doc.progress
    const pct = total ? Math.round((current / total) * 100) : 0
    return (
      <div className="w-36">
        <p className="text-sm text-slate">
          {STATUS_LABEL[doc.status]}
          {total > 0 && ` ${pct} %`}
        </p>
        <div className="mt-1 h-1 overflow-hidden rounded bg-wash">
          <div className="h-full bg-ledger transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
    )
  }
  return (
    <span className={cn('text-sm', doc.status === 'ready' ? 'text-ledger' : 'text-danger')}>{STATUS_LABEL[doc.status]}</span>
  )
}

export function KnowledgeBase() {
  const { user, refresh } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [collections, setCollections] = useState<Collection[]>([])
  const [activeId, setActiveId] = useState<string | null>(null)
  const [documents, setDocuments] = useState<StoredDocument[]>([])
  const [uploads, setUploads] = useState<UploadState[]>([])
  const [notice, setNotice] = useState<{ tone: 'error' | 'success' | 'warning'; text: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [urlOpen, setUrlOpen] = useState(false)
  const [toDelete, setToDelete] = useState<StoredDocument | null>(null)
  const [deleteCollection, setDeleteCollection] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const active = collections.find((c) => c.id === activeId) ?? null
  const canEdit = active?.access === 'editor' || active?.access === 'admin'

  const loadCollections = useCallback(async () => {
    const list = await api.collections()
    setCollections(list)
    setActiveId((current) => (current && list.some((c) => c.id === current) ? current : list[0]?.id ?? null))
  }, [])

  const loadDocuments = useCallback(async () => {
    if (!activeId) return setDocuments([])
    setDocuments(await api.documents(activeId))
  }, [activeId])

  useEffect(() => {
    loadCollections().catch((e) => setNotice({ tone: 'error', text: errorMessage(e) }))
  }, [loadCollections])

  useEffect(() => {
    loadDocuments().catch(() => {})
  }, [loadDocuments])

  // Poll while something is being indexed.
  const busy = documents.some((d) => d.status === 'queued' || d.status === 'processing')
  useEffect(() => {
    if (!busy) return
    const timer = setInterval(() => loadDocuments().catch(() => {}), 2000)
    return () => clearInterval(timer)
  }, [busy, loadDocuments])

  const uploadFiles = async (files: FileList | File[]) => {
    if (!activeId) return
    setNotice(null)
    const list = Array.from(files)
    setUploads(list.map((f) => ({ name: f.name, ratio: 0 })))
    const failures: string[] = []
    for (const [i, file] of list.entries()) {
      try {
        await api.upload(activeId, file, (ratio) =>
          setUploads((u) => u.map((x, j) => (j === i ? { ...x, ratio } : x))),
        )
        setUploads((u) => u.map((x, j) => (j === i ? { ...x, ratio: 1 } : x)))
      } catch (err) {
        failures.push(`${file.name} : ${errorMessage(err)}`)
        setUploads((u) => u.map((x, j) => (j === i ? { ...x, error: errorMessage(err) } : x)))
      }
      loadDocuments().catch(() => {})
    }
    setUploads([])
    if (failures.length) setNotice({ tone: 'error', text: failures.join('\n') })
    else setNotice({ tone: 'success', text: `${plural(list.length, 'document importé', 'documents importés')}. L'indexation suit son cours.` })
    loadCollections().catch(() => {})
  }

  return (
    <div className="flex h-full">
      <nav className="w-60 shrink-0 overflow-y-auto border-r border-rule bg-surface px-3 py-4" aria-label="Collections">
        <div className="mb-2 flex items-center justify-between px-2.5">
          <h2 className="text-sm font-semibold">Collections</h2>
          {isAdmin && (
            <button className="btn-ghost p-1.5" onClick={() => setCreating(true)} aria-label="Créer une collection">
              <FolderPlus size={16} />
            </button>
          )}
        </div>
        {collections.length === 0 && (
          <p className="px-2.5 text-sm text-faint">
            {isAdmin ? 'Créez une première collection pour y importer des documents.' : "Aucune collection ne vous est encore ouverte."}
          </p>
        )}
        {collections.map((c) => (
          <button
            key={c.id}
            onClick={() => setActiveId(c.id)}
            className={cn(
              'flex w-full items-baseline justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm hover:bg-wash',
              c.id === activeId ? 'bg-wash font-medium text-ink' : 'text-slate',
            )}
          >
            <span className="truncate">{c.name}</span>
            <span className="text-xs text-faint">{c.document_count}</span>
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1 overflow-y-auto px-8 py-8">
        {active ? (
          <>
            <PageHeader
              title={active.name}
              description={
                <>
                  {active.description || 'Aucune description.'}{' '}
                  {active.is_open ? 'Ouverte à tous les membres.' : 'Accès sur autorisation.'}
                </>
              }
              actions={
                <>
                  {isAdmin && (
                    <button className="btn-secondary" onClick={() => setSettingsOpen(true)}>
                      <Settings size={16} />
                      Réglages
                    </button>
                  )}
                  {canEdit && (
                    <button className="btn-primary" onClick={() => fileInput.current?.click()}>
                      <Upload size={16} />
                      Importer
                    </button>
                  )}
                </>
              }
            />

            {notice && (
              <Notice tone={notice.tone} className="mb-4 whitespace-pre-line">
                {notice.text}
              </Notice>
            )}

            {canEdit && (
              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  uploadFiles(e.dataTransfer.files)
                }}
                className={cn(
                  'mb-6 rounded-lg border border-dashed px-6 py-5 text-sm transition-colors',
                  dragging ? 'border-ledger bg-ledger-soft' : 'border-rule bg-surface',
                )}
              >
                <input
                  ref={fileInput}
                  type="file"
                  multiple
                  accept={ACCEPT}
                  className="hidden"
                  onChange={(e) => e.target.files && uploadFiles(e.target.files)}
                />
                {uploads.length > 0 ? (
                  <ul className="space-y-1.5">
                    {uploads.map((u) => (
                      <li key={u.name} className="flex items-center gap-3">
                        <span className="w-64 truncate">{u.name}</span>
                        <span className="h-1 flex-1 overflow-hidden rounded bg-wash">
                          <span className="block h-full bg-ledger" style={{ width: `${Math.round(u.ratio * 100)}%` }} />
                        </span>
                        {u.error && <span className="text-danger">refusé</span>}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-slate">
                    Déposez ici des fichiers PDF, Word, texte, Markdown ou HTML, ou{' '}
                    <button className="text-ledger underline" onClick={() => fileInput.current?.click()}>
                      choisissez-les
                    </button>
                    . Les doublons sont détectés automatiquement.
                    <button className="ml-3 inline-flex items-center gap-1 text-ledger underline" onClick={() => setUrlOpen(true)}>
                      <Link2 size={14} />
                      Importer une page web
                    </button>
                  </p>
                )}
              </div>
            )}

            {documents.length === 0 ? (
              <div className="rounded-lg border border-rule bg-surface px-6 py-10 text-center">
                <FileText size={24} className="mx-auto text-faint" />
                <p className="mt-2 text-sm text-slate">
                  {canEdit ? 'Cette collection est vide : importez vos premiers documents.' : 'Cette collection ne contient pas encore de document.'}
                </p>
              </div>
            ) : (
              <table className="w-full border-collapse rounded-lg bg-surface">
                <thead>
                  <tr>
                    <th className="table-head">Document</th>
                    <th className="table-head">Statut</th>
                    <th className="table-head">Contenu</th>
                    <th className="table-head">Ajouté le</th>
                    {canEdit && <th className="table-head w-20" />}
                  </tr>
                </thead>
                <tbody>
                  {documents.map((d) => (
                    <tr key={d.id}>
                      <td className="table-cell max-w-xs">
                        {d.has_file ? (
                          <a className="block truncate hover:text-ledger hover:underline" href={api.documentFileUrl(d.id)} target="_blank" rel="noreferrer" title={d.filename}>
                            {d.filename}
                          </a>
                        ) : (
                          <span className="block truncate" title={d.filename}>
                            {d.filename}
                          </span>
                        )}
                        {d.error_message && <p className="mt-1 text-xs text-danger">{d.error_message}</p>}
                        {d.warnings.map((w) => (
                          <p key={w} className="mt-1 flex items-start gap-1 text-xs text-refusal">
                            <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                            {w}
                          </p>
                        ))}
                      </td>
                      <td className="table-cell">
                        <StatusCell doc={d} />
                      </td>
                      <td className="table-cell text-slate">
                        {d.page_count > 0 && <span className="block">{plural(d.page_count, 'page')}</span>}
                        {d.chunk_count > 0 && <span className="block">{plural(d.chunk_count, 'passage indexé', 'passages indexés')}</span>}
                        {d.size_bytes > 0 && <span className="block text-xs text-faint">{formatBytes(d.size_bytes)}</span>}
                      </td>
                      <td className="table-cell text-slate">{formatDateTime(d.created_at)}</td>
                      {canEdit && (
                        <td className="table-cell">
                          <div className="flex justify-end gap-1">
                            {d.status === 'failed' && (d.has_file || d.source_url) && (
                              <button
                                className="btn-ghost p-1.5"
                                onClick={() => api.reindexDocument(d.id).then(loadDocuments)}
                                aria-label={`Relancer l'indexation de ${d.filename}`}
                                title="Relancer l'indexation"
                              >
                                <RefreshCw size={15} />
                              </button>
                            )}
                            <button
                              className="btn-ghost p-1.5 hover:text-danger"
                              onClick={() => setToDelete(d)}
                              aria-label={`Supprimer ${d.filename}`}
                              title="Supprimer"
                            >
                              <Trash2 size={15} />
                            </button>
                          </div>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        ) : (
          <PageHeader
            title="Documents"
            description={isAdmin ? 'Créez une collection pour commencer.' : "Vous n'avez accès à aucune collection pour le moment."}
          />
        )}
      </div>

      <CreateCollectionDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={async (c) => {
          await loadCollections()
          await refresh()
          setActiveId(c.id)
        }}
      />

      {active && (
        <CollectionSettingsDialog
          open={settingsOpen}
          onOpenChange={setSettingsOpen}
          collection={active}
          onSaved={loadCollections}
          onDelete={() => {
            setSettingsOpen(false)
            setDeleteCollection(true)
          }}
        />
      )}

      {active && (
        <ImportUrlDialog
          open={urlOpen}
          onOpenChange={setUrlOpen}
          collectionId={active.id}
          onImported={() => loadDocuments()}
        />
      )}

      <ConfirmDialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
        title="Supprimer ce document ?"
        description={
          <>
            « {toDelete?.filename} » sera retiré de la collection : fichier original, passages indexés et métadonnées.
            Les réponses passées qui le citaient restent dans l'historique.
          </>
        }
        confirmLabel="Supprimer le document"
        danger
        onConfirm={async () => {
          if (!toDelete) return
          await api.deleteDocument(toDelete.id)
          await loadDocuments()
          await loadCollections()
        }}
      />

      <ConfirmDialog
        open={deleteCollection}
        onOpenChange={setDeleteCollection}
        title={`Supprimer la collection « ${active?.name} » ?`}
        description={`Les ${plural(active?.document_count ?? 0, 'document')} qu'elle contient seront définitivement supprimés, ainsi que les droits d'accès associés. Cette action est irréversible.`}
        confirmLabel="Supprimer la collection"
        danger
        onConfirm={async () => {
          if (!active) return
          await api.deleteCollection(active.id)
          setActiveId(null)
          await loadCollections()
          await refresh()
        }}
      />
    </div>
  )
}

function CreateCollectionDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (c: Collection) => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [isOpen, setIsOpen] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    try {
      const c = await api.createCollection({ name: name.trim(), description: description.trim() || undefined, is_open: isOpen })
      setName('')
      setDescription('')
      setIsOpen(false)
      setError(null)
      onOpenChange(false)
      onCreated(c)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Nouvelle collection"
      description="Une collection regroupe des documents interrogés ensemble, avec ses propres droits d'accès."
      footer={
        <>
          <button className="btn-secondary" onClick={() => onOpenChange(false)}>
            Annuler
          </button>
          <button className="btn-primary" onClick={submit} disabled={!name.trim()}>
            Créer la collection
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        <div>
          <label className="label" htmlFor="col-name">
            Nom
          </label>
          <input id="col-name" className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Réglementation UE" />
        </div>
        <div>
          <label className="label" htmlFor="col-desc">
            Description
          </label>
          <textarea id="col-desc" className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={isOpen} onChange={(e) => setIsOpen(e.target.checked)} />
          <span>
            Ouverte à tous les membres
            <span className="block text-slate">Sinon, seules les personnes autorisées dans l'administration y ont accès.</span>
          </span>
        </label>
      </div>
    </Dialog>
  )
}

function CollectionSettingsDialog({
  open,
  onOpenChange,
  collection,
  onSaved,
  onDelete,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  collection: Collection
  onSaved: () => void
  onDelete: () => void
}) {
  const [description, setDescription] = useState(collection.description ?? '')
  const [isOpen, setIsOpen] = useState(collection.is_open)
  useEffect(() => {
    setDescription(collection.description ?? '')
    setIsOpen(collection.is_open)
  }, [collection])

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={`Réglages de « ${collection.name} »`}
      footer={
        <>
          <button className="btn-ghost mr-auto text-danger" onClick={onDelete}>
            <Trash2 size={15} />
            Supprimer la collection
          </button>
          <button className="btn-secondary" onClick={() => onOpenChange(false)}>
            Annuler
          </button>
          <button
            className="btn-primary"
            onClick={async () => {
              await api.updateCollection(collection.id, { description: description || null, is_open: isOpen })
              onSaved()
              onOpenChange(false)
            }}
          >
            Enregistrer
          </button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <label className="label" htmlFor="set-desc">
            Description
          </label>
          <textarea id="set-desc" className="input" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" checked={isOpen} onChange={(e) => setIsOpen(e.target.checked)} />
          <span>Ouverte à tous les membres</span>
        </label>
        <p className="text-sm text-slate">Les droits nominatifs se gèrent dans Administration, onglet Accès.</p>
      </div>
    </Dialog>
  )
}

function ImportUrlDialog({
  open,
  onOpenChange,
  collectionId,
  onImported,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  collectionId: string
  onImported: () => void
}) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Importer une page web"
      description="La page est téléchargée par le serveur. Les adresses internes sont refusées."
      footer={
        <>
          <button className="btn-secondary" onClick={() => onOpenChange(false)}>
            Annuler
          </button>
          <button
            className="btn-primary"
            disabled={!url.trim()}
            onClick={async () => {
              try {
                await api.importUrl(collectionId, url.trim())
                setUrl('')
                setError(null)
                onOpenChange(false)
                onImported()
              } catch (err) {
                setError(errorMessage(err))
              }
            }}
          >
            Importer la page
          </button>
        </>
      }
    >
      {error && <Notice tone="error" className="mb-3">{error}</Notice>}
      <label className="label" htmlFor="import-url">
        Adresse
      </label>
      <input id="import-url" className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://eur-lex.europa.eu/…" />
    </Dialog>
  )
}
