import { useEffect, useState, type FormEvent } from 'react'
import { Copy, KeyRound } from 'lucide-react'
import { api, errorMessage, type ApiKey } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatDateTime } from '@/lib/utils'
import { Notice, PageHeader } from '@/components/ui/Notice'

export function Account() {
  const { user } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [pwdNotice, setPwdNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [keyName, setKeyName] = useState('')
  const [created, setCreated] = useState<string | null>(null)

  const loadKeys = () => api.apiKeys().then(setKeys).catch(() => {})
  useEffect(() => {
    loadKeys()
  }, [])

  const changePassword = async (e: FormEvent) => {
    e.preventDefault()
    try {
      await api.changePassword(current, next)
      setCurrent('')
      setNext('')
      setPwdNotice({ tone: 'success', text: 'Mot de passe modifié. Vos autres sessions ont été déconnectées.' })
    } catch (err) {
      setPwdNotice({ tone: 'error', text: errorMessage(err) })
    }
  }

  return (
    <div className="h-full overflow-y-auto px-8 py-8">
      <div className="max-w-2xl">
        <PageHeader title="Mon compte" description={`${user?.full_name ? `${user.full_name}, ` : ''}${user?.email}`} />

        {user?.auth_provider === 'local' && (
          <section className="mb-10">
            <h2 className="mb-3 font-semibold">Mot de passe</h2>
            {pwdNotice && <Notice tone={pwdNotice.tone} className="mb-3">{pwdNotice.text}</Notice>}
            <form onSubmit={changePassword} className="space-y-4 rounded-lg border border-rule bg-surface p-5">
              <div>
                <label className="label" htmlFor="pwd-current">
                  Mot de passe actuel
                </label>
                <input id="pwd-current" type="password" autoComplete="current-password" className="input" value={current} onChange={(e) => setCurrent(e.target.value)} required />
              </div>
              <div>
                <label className="label" htmlFor="pwd-new">
                  Nouveau mot de passe
                </label>
                <input id="pwd-new" type="password" autoComplete="new-password" className="input" value={next} onChange={(e) => setNext(e.target.value)} required />
                <p className="mt-1 text-xs text-slate">Au moins 12 caractères, en mélangeant minuscules, majuscules, chiffres ou symboles.</p>
              </div>
              <button className="btn-primary" type="submit">
                Changer le mot de passe
              </button>
            </form>
          </section>
        )}

        <section>
          <h2 className="mb-1 font-semibold">Clés d'API</h2>
          <p className="mb-3 text-sm text-slate">
            Pour interroger ContextHub depuis un autre outil (en-tête <code>Authorization: Bearer …</code>). Une clé agit
            avec vos droits.
          </p>
          {created && (
            <Notice tone="success" className="mb-3">
              Copiez cette clé maintenant, elle ne sera plus affichée :
              <span className="mt-2 flex items-center gap-2">
                <code className="select-all break-all rounded bg-surface px-2 py-1 text-ink">{created}</code>
                <button className="btn-ghost p-1.5" onClick={() => navigator.clipboard.writeText(created)} aria-label="Copier la clé">
                  <Copy size={14} />
                </button>
              </span>
            </Notice>
          )}
          <form
            className="mb-4 flex gap-2"
            onSubmit={async (e) => {
              e.preventDefault()
              const k = await api.createApiKey(keyName.trim())
              setCreated(k.key ?? null)
              setKeyName('')
              loadKeys()
            }}
          >
            <label htmlFor="key-name" className="sr-only">
              Nom de la clé
            </label>
            <input id="key-name" className="input" placeholder="Nom de la clé, par exemple « Script de veille »" value={keyName} onChange={(e) => setKeyName(e.target.value)} />
            <button className="btn-secondary shrink-0" disabled={!keyName.trim()}>
              <KeyRound size={16} />
              Créer une clé
            </button>
          </form>
          {keys.length > 0 && (
            <table className="w-full border-collapse bg-surface">
              <thead>
                <tr>
                  <th className="table-head">Nom</th>
                  <th className="table-head">Préfixe</th>
                  <th className="table-head">Dernière utilisation</th>
                  <th className="table-head" />
                </tr>
              </thead>
              <tbody>
                {keys.map((k) => (
                  <tr key={k.id} className={k.revoked ? 'text-faint' : ''}>
                    <td className="table-cell">{k.name}</td>
                    <td className="table-cell">
                      <code>{k.prefix}…</code>
                    </td>
                    <td className="table-cell text-slate">{k.last_used_at ? formatDateTime(k.last_used_at) : 'Jamais'}</td>
                    <td className="table-cell text-right">
                      {k.revoked ? (
                        'Révoquée'
                      ) : (
                        <button className="btn-ghost px-2 py-1 text-xs text-danger" onClick={() => api.revokeApiKey(k.id).then(loadKeys)}>
                          Révoquer
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>
    </div>
  )
}
