import { useEffect, useState } from 'react'
import { api, errorMessage, type Collection, type Grant, type User } from '@/lib/api'
import { Notice } from '@/components/ui/Notice'

export function Access() {
  const [collections, setCollections] = useState<Collection[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [selected, setSelected] = useState<string>('')
  const [grants, setGrants] = useState<Grant[]>([])
  const [newUser, setNewUser] = useState('')
  const [newLevel, setNewLevel] = useState<'viewer' | 'editor'>('viewer')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.collections().then((list) => {
      setCollections(list)
      setSelected((s) => s || list[0]?.id || '')
    })
    api.users().then(setUsers)
  }, [])

  const load = () => (selected ? api.grants(selected).then(setGrants) : Promise.resolve())
  useEffect(() => {
    load()
  }, [selected])

  const collection = collections.find((c) => c.id === selected)
  const candidates = users.filter((u) => u.role !== 'admin' && u.is_active && !grants.some((g) => g.user_id === u.id))

  const run = async (action: () => Promise<unknown>) => {
    setError(null)
    try {
      await action()
      await load()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  if (collections.length === 0) {
    return <p className="text-sm text-slate">Créez d'abord une collection depuis la page Documents.</p>
  }

  return (
    <section className="max-w-3xl">
      <label className="label" htmlFor="acc-col">
        Collection
      </label>
      <select id="acc-col" className="input mb-2 max-w-sm" value={selected} onChange={(e) => setSelected(e.target.value)}>
        {collections.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <p className="mb-6 text-sm text-slate">
        {collection?.is_open
          ? 'Collection ouverte : tous les membres peuvent l’interroger. Les droits ci-dessous servent à donner la modification.'
          : 'Collection fermée : seules les personnes listées ci-dessous y ont accès.'}{' '}
        Les administrateurs ont accès à tout.
      </p>
      {error && <Notice tone="error" className="mb-4">{error}</Notice>}

      <table className="mb-6 w-full border-collapse bg-surface">
        <thead>
          <tr>
            <th className="table-head">Personne</th>
            <th className="table-head">Droit</th>
            <th className="table-head" />
          </tr>
        </thead>
        <tbody>
          {grants.length === 0 && (
            <tr>
              <td className="table-cell text-slate" colSpan={3}>
                Aucun droit nominatif sur cette collection.
              </td>
            </tr>
          )}
          {grants.map((g) => (
            <tr key={g.user_id}>
              <td className="table-cell">
                {g.full_name || g.email}
                {g.full_name && <span className="block text-xs text-slate">{g.email}</span>}
              </td>
              <td className="table-cell">
                <select
                  className="rounded border border-rule bg-surface px-1.5 py-1 text-sm"
                  value={g.level}
                  onChange={(e) => run(() => api.setGrant(selected, g.user_id, e.target.value as 'viewer' | 'editor'))}
                  aria-label={`Droit de ${g.email}`}
                >
                  <option value="viewer">Lecture : peut interroger</option>
                  <option value="editor">Modification : peut aussi importer et supprimer</option>
                </select>
              </td>
              <td className="table-cell text-right">
                <button className="btn-ghost px-2 py-1 text-xs text-danger" onClick={() => run(() => api.revokeGrant(selected, g.user_id))}>
                  Retirer
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-rule bg-surface p-4">
        <div className="min-w-56 flex-1">
          <label className="label" htmlFor="acc-user">
            Donner accès à
          </label>
          <select id="acc-user" className="input" value={newUser} onChange={(e) => setNewUser(e.target.value)}>
            <option value="">Choisir une personne…</option>
            {candidates.map((u) => (
              <option key={u.id} value={u.id}>
                {u.full_name ? `${u.full_name} (${u.email})` : u.email}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="acc-level">
            Droit
          </label>
          <select id="acc-level" className="input" value={newLevel} onChange={(e) => setNewLevel(e.target.value as 'viewer' | 'editor')}>
            <option value="viewer">Lecture</option>
            <option value="editor">Modification</option>
          </select>
        </div>
        <button
          className="btn-primary"
          disabled={!newUser}
          onClick={() =>
            run(async () => {
              await api.setGrant(selected, newUser, newLevel)
              setNewUser('')
            })
          }
        >
          Donner l'accès
        </button>
      </div>
    </section>
  )
}
