import { useEffect, useState } from 'react'
import { PlugZap, Plus } from 'lucide-react'
import { api, errorMessage, type Connection, type ConnectorKind } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import { ConfirmDialog, Dialog } from '@/components/ui/Dialog'
import { Notice } from '@/components/ui/Notice'

type TestResult = { id: string; ok: boolean; message: string }

export function Connections() {
  const [connections, setConnections] = useState<Connection[]>([])
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState<Connection | 'new' | null>(null)
  const [toDelete, setToDelete] = useState<Connection | null>(null)
  const [testing, setTesting] = useState<string | null>(null)
  const [result, setResult] = useState<TestResult | null>(null)

  const load = () => api.adminConnections().then(setConnections).catch((e) => setError(errorMessage(e)))
  useEffect(() => {
    load()
  }, [])

  const test = async (c: Connection) => {
    setTesting(c.id)
    setResult(null)
    try {
      const r = await api.testConnection(c.id)
      setResult({ id: c.id, ...r })
    } catch (e) {
      setResult({ id: c.id, ok: false, message: errorMessage(e) })
    } finally {
      setTesting(null)
    }
  }

  const toggle = async (c: Connection) => {
    setError(null)
    try {
      await api.updateConnection(c.id, { is_enabled: !c.is_enabled })
      await load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <section>
      <div className="mb-4 flex items-start justify-between gap-6">
        <p className="max-w-2xl text-sm text-slate">
          Un connecteur permet aux utilisateurs de joindre des pages Confluence ou Notion à une conversation, par lien
          ou par recherche. Rien n'est copié en masse : seules les pages choisies sont lues, puis supprimées avec la
          conversation. Les utilisateurs voient tout ce que le compte de la connexion peut lire : limitez ses droits
          aux espaces que tout le monde peut consulter.
        </p>
        <button className="btn-primary shrink-0" onClick={() => setEditing('new')}>
          <Plus size={16} />
          Ajouter une connexion
        </button>
      </div>
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}

      {connections.length === 0 ? (
        <Notice>Aucune connexion configurée. Ajoutez-en une pour proposer « Joindre des pages » dans le chat.</Notice>
      ) : (
        <table className="w-full border-collapse bg-surface">
          <thead>
            <tr>
              <th className="table-head">Connexion</th>
              <th className="table-head">Type</th>
              <th className="table-head">Adresse</th>
              <th className="table-head">Modifiée</th>
              <th className="table-head">Statut</th>
              <th className="table-head" />
            </tr>
          </thead>
          <tbody>
            {connections.map((c) => (
              <tr key={c.id} className={c.is_enabled ? '' : 'text-faint'}>
                <td className="table-cell">
                  <p className="font-medium">{c.name}</p>
                  {c.account && <p className="text-xs text-slate">{c.account}</p>}
                  {result?.id === c.id && (
                    <p className={`mt-1 text-xs ${result.ok ? 'text-ledger-dark' : 'text-danger'}`} role="status">
                      {result.message}
                    </p>
                  )}
                </td>
                <td className="table-cell">{c.kind_label}</td>
                <td className="table-cell text-slate">{c.base_url ?? 'api.notion.com'}</td>
                <td className="table-cell text-slate">{formatDateTime(c.updated_at ?? null)}</td>
                <td className="table-cell">{c.is_enabled ? 'Active' : 'Désactivée'}</td>
                <td className="table-cell">
                  <div className="flex justify-end gap-1">
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => test(c)} disabled={testing === c.id}>
                      <PlugZap size={14} />
                      {testing === c.id ? 'Test en cours…' : 'Tester'}
                    </button>
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => setEditing(c)}>
                      Modifier
                    </button>
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => toggle(c)}>
                      {c.is_enabled ? 'Désactiver' : 'Réactiver'}
                    </button>
                    <button className="btn-ghost px-2 py-1 text-xs text-danger" onClick={() => setToDelete(c)}>
                      Supprimer
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <ConnectionDialog
          connection={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            load()
          }}
        />
      )}

      <ConfirmDialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
        title={`Supprimer la connexion « ${toDelete?.name} » ?`}
        description="Le jeton enregistré est effacé. Les pages déjà jointes à des conversations y restent jusqu'à la suppression de ces conversations."
        confirmLabel="Supprimer la connexion"
        danger
        onConfirm={async () => {
          if (!toDelete) return
          try {
            await api.deleteConnection(toDelete.id)
            await load()
          } catch (e) {
            setError(errorMessage(e))
          }
        }}
      />
    </section>
  )
}

function ConnectionDialog({
  connection,
  onClose,
  onSaved,
}: {
  connection: Connection | null
  onClose: () => void
  onSaved: () => void
}) {
  const [kind, setKind] = useState<ConnectorKind>(connection?.kind ?? 'confluence')
  const [name, setName] = useState(connection?.name ?? '')
  const [baseUrl, setBaseUrl] = useState(connection?.base_url ?? '')
  const [account, setAccount] = useState(connection?.account ?? '')
  const [secret, setSecret] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const confluence = kind === 'confluence'
  const complete = name.trim() && (connection || secret.trim()) && (!confluence || baseUrl.trim())

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (connection) {
        await api.updateConnection(connection.id, {
          name: name.trim(),
          ...(confluence ? { base_url: baseUrl.trim(), account: account.trim() } : {}),
          ...(secret.trim() ? { secret: secret.trim() } : {}),
        })
      } else {
        await api.createConnection({
          kind,
          name: name.trim(),
          secret: secret.trim(),
          ...(confluence ? { base_url: baseUrl.trim(), account: account.trim() || undefined } : {}),
        })
      }
      onSaved()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(open) => !open && onClose()}
      title={connection ? `Modifier « ${connection.name} »` : 'Ajouter une connexion'}
      description="Le jeton est chiffré en base et n'est plus jamais affiché."
      footer={
        <>
          <button className="btn-secondary" onClick={onClose} disabled={busy}>
            Annuler
          </button>
          <button className="btn-primary" onClick={submit} disabled={!complete || busy}>
            {connection ? 'Enregistrer' : 'Ajouter la connexion'}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        {!connection && (
          <div>
            <label className="label" htmlFor="c-kind">
              Type
            </label>
            <select id="c-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as ConnectorKind)}>
              <option value="confluence">Confluence (Cloud ou Data Center)</option>
              <option value="notion">Notion</option>
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="c-name">
            Nom affiché aux utilisateurs
          </label>
          <input
            id="c-name"
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={confluence ? 'Wiki de l’entreprise' : 'Notion de l’équipe'}
          />
        </div>
        {confluence && (
          <>
            <div>
              <label className="label" htmlFor="c-url">
                Adresse de Confluence
              </label>
              <input
                id="c-url"
                className="input"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://entreprise.atlassian.net/wiki"
              />
              <p className="mt-1 text-xs text-slate">
                Cloud : l'adresse se termine par /wiki. Data Center : la racine de votre Confluence.
              </p>
            </div>
            <div>
              <label className="label" htmlFor="c-account">
                Adresse e-mail du compte (Cloud)
              </label>
              <input
                id="c-account"
                type="email"
                className="input"
                value={account}
                onChange={(e) => setAccount(e.target.value)}
                placeholder="Laisser vide pour un jeton d'accès personnel Data Center"
              />
            </div>
          </>
        )}
        <div>
          <label className="label" htmlFor="c-secret">
            {confluence ? "Jeton d'API" : "Jeton d'intégration Notion"}
          </label>
          <input
            id="c-secret"
            type="password"
            autoComplete="off"
            className="input"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder={connection ? 'Laisser vide pour conserver le jeton actuel' : ''}
          />
          {!confluence && (
            <p className="mt-1 text-xs text-slate">
              Dans Notion, partagez avec l'intégration les pages que les utilisateurs pourront joindre.
            </p>
          )}
        </div>
      </div>
    </Dialog>
  )
}
