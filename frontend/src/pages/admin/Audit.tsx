import { Fragment, useEffect, useState } from 'react'
import { Download, ShieldCheck } from 'lucide-react'
import { api, errorMessage, type AuditEvent } from '@/lib/api'
import { cn, formatDateTime } from '@/lib/utils'
import { Notice } from '@/components/ui/Notice'

const ACTIONS: [string, string][] = [
  ['', 'Toutes les actions'],
  ['query.', 'Questions et retours'],
  ['auth.', 'Connexions'],
  ['document.', 'Documents'],
  ['collection.', 'Collections'],
  ['grant.', 'Droits'],
  ['user.', 'Utilisateurs'],
  ['audit.', 'Consultations du journal'],
]

const ACTION_LABELS: Record<string, string> = {
  'query.ask': 'Question posée',
  'query.feedback': 'Retour sur une réponse',
  'auth.login': 'Connexion',
  'auth.logout': 'Déconnexion',
  'auth.password_change': 'Changement de mot de passe',
  'document.upload': 'Document importé',
  'document.import_url': 'Page web importée',
  'document.index': 'Indexation',
  'document.view': 'Document consulté',
  'document.delete': 'Document supprimé',
  'document.reindex': 'Réindexation demandée',
  'collection.create': 'Collection créée',
  'collection.update': 'Collection modifiée',
  'collection.delete': 'Collection supprimée',
  'grant.set': 'Droit accordé',
  'grant.revoke': 'Droit retiré',
  'user.create': 'Utilisateur créé',
  'user.update': 'Utilisateur modifié',
  'user.delete': 'Utilisateur supprimé',
  'user.reset_password': 'Mot de passe réinitialisé',
  'user.bootstrap_admin': 'Administrateur initial créé',
  'apikey.create': "Clé d'API créée",
  'apikey.revoke': "Clé d'API révoquée",
  'conversation.delete': 'Conversation supprimée',
  'audit.export': 'Journal exporté',
  'audit.verify': 'Intégrité vérifiée',
  'retention.purge': 'Purge de conservation',
}

const PAGE = 50

function summary(e: AuditEvent): string {
  const d = (e.details ?? {}) as Record<string, unknown>
  if (e.action === 'query.ask' && typeof d.question === 'string') return d.question
  if (typeof d.filename === 'string') return d.filename
  if (typeof d.name === 'string') return d.name
  if (typeof d.email === 'string') return d.email
  if (typeof d.reason === 'string') return d.reason
  return ''
}

export function Audit() {
  const [items, setItems] = useState<AuditEvent[]>([])
  const [total, setTotal] = useState(0)
  const [action, setAction] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [offset, setOffset] = useState(0)
  const [open, setOpen] = useState<number | null>(null)
  const [verify, setVerify] = useState<{ valid: boolean; checked: number; broken_at: number | null; reason: string | null } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const params = {
    action: action || undefined,
    start: start ? new Date(start).toISOString() : undefined,
    end: end ? new Date(new Date(end).getTime() + 86_400_000).toISOString() : undefined,
  }

  useEffect(() => {
    api
      .audit({ ...params, limit: PAGE, offset })
      .then((r) => {
        setItems(r.items)
        setTotal(r.total)
      })
      .catch((e) => setError(errorMessage(e)))
  }, [action, start, end, offset])

  return (
    <section>
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <label className="label" htmlFor="au-action">
            Type d'action
          </label>
          <select id="au-action" className="input" value={action} onChange={(e) => (setOffset(0), setAction(e.target.value))}>
            {ACTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="au-start">
            Du
          </label>
          <input id="au-start" type="date" className="input" value={start} onChange={(e) => (setOffset(0), setStart(e.target.value))} />
        </div>
        <div>
          <label className="label" htmlFor="au-end">
            Au
          </label>
          <input id="au-end" type="date" className="input" value={end} onChange={(e) => (setOffset(0), setEnd(e.target.value))} />
        </div>
        <div className="ml-auto flex gap-2">
          <button
            className="btn-secondary"
            onClick={() => api.verifyAudit().then(setVerify).catch((e) => setError(errorMessage(e)))}
          >
            <ShieldCheck size={16} />
            Vérifier l'intégrité
          </button>
          <a className="btn-secondary" href={api.auditExportUrl(params)}>
            <Download size={16} />
            Exporter en CSV
          </a>
        </div>
      </div>

      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {verify && (
        <Notice tone={verify.valid ? 'success' : 'error'} className="mb-4">
          {verify.valid
            ? `Chaîne intègre : ${verify.checked.toLocaleString('fr-FR')} événements vérifiés, aucune modification ni suppression détectée.`
            : `Intégrité rompue à l'événement n° ${verify.broken_at} (${verify.reason}). Le journal a été altéré en dehors de l'application.`}
        </Notice>
      )}

      <table className="w-full border-collapse bg-surface">
        <thead>
          <tr>
            <th className="table-head w-44">Date</th>
            <th className="table-head">Qui</th>
            <th className="table-head">Action</th>
            <th className="table-head">Objet</th>
          </tr>
        </thead>
        <tbody>
          {items.map((e) => (
            <Fragment key={e.id}>
              <tr className="cursor-pointer hover:bg-wash" onClick={() => setOpen(open === e.id ? null : e.id)}>
                <td className="table-cell whitespace-nowrap text-slate">{formatDateTime(e.ts)}</td>
                <td className="table-cell">{e.actor_email ?? 'Système'}</td>
                <td className={cn('table-cell', e.outcome !== 'success' && 'text-danger')}>
                  {ACTION_LABELS[e.action] ?? e.action}
                  {e.outcome === 'failure' && ' (échec)'}
                  {e.outcome === 'denied' && ' (refusé)'}
                </td>
                <td className="table-cell max-w-md truncate text-slate">{summary(e)}</td>
              </tr>
              {open === e.id && (
                <tr>
                  <td colSpan={4} className="border-b border-rule bg-wash px-3 py-3">
                    <pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs leading-relaxed">
                      {JSON.stringify({ ...e.details, ip: e.ip, empreinte: e.hash }, null, 2)}
                    </pre>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>

      <div className="mt-4 flex items-center justify-between text-sm text-slate">
        <span>{total.toLocaleString('fr-FR')} événements</span>
        <div className="flex gap-2">
          <button className="btn-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
            Plus récents
          </button>
          <button className="btn-secondary" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
            Plus anciens
          </button>
        </div>
      </div>
    </section>
  )
}
