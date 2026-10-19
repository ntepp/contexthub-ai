import { useEffect, useState } from 'react'
import { KeyRound, UserPlus } from 'lucide-react'
import { api, errorMessage, type Role, type User } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatDateTime } from '@/lib/utils'
import { ConfirmDialog, Dialog } from '@/components/ui/Dialog'
import { Notice } from '@/components/ui/Notice'

export function Users() {
  const { user: me } = useAuth()
  const [users, setUsers] = useState<User[]>([])
  const [error, setError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(null)
  const [toDelete, setToDelete] = useState<User | null>(null)
  const [deleted, setDeleted] = useState<string | null>(null)

  const load = () => api.users().then(setUsers).catch((e) => setError(errorMessage(e)))
  useEffect(() => {
    load()
  }, [])

  const update = async (u: User, patch: Partial<Pick<User, 'role' | 'is_active'>>) => {
    setError(null)
    try {
      await api.updateUser(u.id, patch)
      await load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <section>
      <div className="mb-4 flex items-center justify-between">
        <p className="text-sm text-slate">
          Les comptes SSO sont créés automatiquement à la première connexion. Créez ici les comptes à mot de passe.
        </p>
        <button className="btn-primary" onClick={() => setCreating(true)}>
          <UserPlus size={16} />
          Ajouter un utilisateur
        </button>
      </div>
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}
      {deleted && <Notice tone="success" className="mb-4">{deleted}</Notice>}

      <table className="w-full border-collapse bg-surface">
        <thead>
          <tr>
            <th className="table-head">Utilisateur</th>
            <th className="table-head">Rôle</th>
            <th className="table-head">Connexion</th>
            <th className="table-head">Dernière connexion</th>
            <th className="table-head">Statut</th>
            <th className="table-head" />
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.id} className={u.is_active ? '' : 'text-faint'}>
              <td className="table-cell">
                <p className="font-medium">{u.full_name || u.email}</p>
                {u.full_name && <p className="text-xs text-slate">{u.email}</p>}
              </td>
              <td className="table-cell">
                <select
                  className="rounded border border-rule bg-surface px-1.5 py-1 text-sm"
                  value={u.role}
                  disabled={u.id === me?.id}
                  onChange={(e) => update(u, { role: e.target.value as Role })}
                  aria-label={`Rôle de ${u.email}`}
                >
                  <option value="member">Membre</option>
                  <option value="admin">Administrateur</option>
                </select>
              </td>
              <td className="table-cell text-slate">{u.auth_provider === 'oidc' ? 'SSO' : 'Mot de passe'}</td>
              <td className="table-cell text-slate">{formatDateTime(u.last_login_at)}</td>
              <td className="table-cell">{u.is_active ? 'Actif' : 'Désactivé'}</td>
              <td className="table-cell">
                {u.id !== me?.id && (
                  <div className="flex justify-end gap-1">
                    {u.auth_provider === 'local' && (
                      <button
                        className="btn-ghost px-2 py-1 text-xs"
                        onClick={async () => {
                          const r = await api.resetPassword(u.id)
                          setSecret({ email: u.email, password: r.temporary_password })
                        }}
                        title="Générer un mot de passe temporaire"
                      >
                        <KeyRound size={14} />
                        Réinitialiser
                      </button>
                    )}
                    <button className="btn-ghost px-2 py-1 text-xs" onClick={() => update(u, { is_active: !u.is_active })}>
                      {u.is_active ? 'Désactiver' : 'Réactiver'}
                    </button>
                    <button className="btn-ghost px-2 py-1 text-xs text-danger" onClick={() => setToDelete(u)}>
                      Supprimer
                    </button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <CreateUserDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(email, password) => {
          load()
          if (password) setSecret({ email, password })
        }}
      />

      <Dialog
        open={secret !== null}
        onOpenChange={(open) => !open && setSecret(null)}
        title="Mot de passe temporaire"
        description={`Transmettez-le à ${secret?.email} par un canal sûr. Il ne sera plus affiché.`}
        footer={
          <button className="btn-primary" onClick={() => setSecret(null)}>
            J'ai noté le mot de passe
          </button>
        }
      >
        <code className="block select-all rounded border border-rule bg-wash px-3 py-2 text-base">{secret?.password}</code>
        <p className="mt-2 text-sm text-slate">L'utilisateur pourra le changer depuis Mon compte.</p>
      </Dialog>

      <ConfirmDialog
        open={toDelete !== null}
        onOpenChange={(open) => !open && setToDelete(null)}
        title={`Supprimer le compte de ${toDelete?.email} ?`}
        description="Effacement RGPD : le compte, ses conversations, ses clés d'API et ses droits sont supprimés. Dans le journal d'audit, son adresse et ses adresses IP sont masquées ; les événements restent pour la traçabilité, sans casser la chaîne d'intégrité."
        confirmLabel="Supprimer définitivement"
        danger
        onConfirm={async () => {
          if (!toDelete) return
          try {
            const r = await api.deleteUser(toDelete.id)
            setDeleted(
              `Compte supprimé : ${r.conversations_deleted} conversation(s) effacée(s), ${r.audit_events_redacted} événement(s) d'audit anonymisé(s).`,
            )
            await load()
          } catch (e) {
            setError(errorMessage(e))
          }
        }}
      />
    </section>
  )
}

function CreateUserDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (email: string, password?: string) => void
}) {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('member')
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    try {
      const u = await api.createUser({ email: email.trim(), full_name: name.trim() || undefined, role })
      setEmail('')
      setName('')
      setRole('member')
      setError(null)
      onOpenChange(false)
      onCreated(u.email, u.temporary_password)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Ajouter un utilisateur"
      description="Un mot de passe temporaire est généré et affiché une seule fois."
      footer={
        <>
          <button className="btn-secondary" onClick={() => onOpenChange(false)}>
            Annuler
          </button>
          <button className="btn-primary" disabled={!email.trim()} onClick={submit}>
            Créer le compte
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {error && <Notice tone="error">{error}</Notice>}
        <div>
          <label className="label" htmlFor="u-email">
            Adresse e-mail
          </label>
          <input id="u-email" type="email" className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="u-name">
            Nom complet
          </label>
          <input id="u-name" className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="u-role">
            Rôle
          </label>
          <select id="u-role" className="input" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="member">Membre : interroge les collections autorisées</option>
            <option value="admin">Administrateur : tous les droits</option>
          </select>
        </div>
      </div>
    </Dialog>
  )
}
