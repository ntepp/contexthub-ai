import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useBlocker } from 'react-router-dom'
import { ArrowDown, ArrowUp, CloudUpload, ExternalLink, HardDrive, RefreshCw, RotateCcw } from 'lucide-react'
import { api, errorMessage, modelAvailability, modelsChanged, type AdminModel, type AdminModels } from '@/lib/api'
import { cn, plural } from '@/lib/utils'
import { ConfirmDialog, Dialog } from '@/components/ui/Dialog'
import { Notice } from '@/components/ui/Notice'

/** Same bound as the API (MAX_OFFERED in src/llm/factory.py). */
const MAX_OFFERED = 50

interface Draft {
  offered: string[]
  default: string
}

type LoadState = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; view: AdminModels }

function savedDraft(view: AdminModels): Draft {
  return { offered: view.models.filter((m) => m.offered).map((m) => m.id), default: view.default }
}

function sameDraft(a: Draft, b: Draft): boolean {
  return a.default === b.default && a.offered.length === b.offered.length && a.offered.every((id, i) => b.offered[i] === id)
}

/** Only http(s) links are opened (the address comes from the server configuration). */
function safeConsoleUrl(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? parsed.href : null
  } catch {
    return null
  }
}

function KindBadge({ model }: { model: AdminModel }) {
  return model.is_local ? (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded bg-ledger-soft px-1.5 py-0.5 text-xs text-ledger-dark">
      <HardDrive size={12} aria-hidden="true" />
      Local
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded bg-cloud-soft px-1.5 py-0.5 text-xs text-cloud">
      <CloudUpload size={12} aria-hidden="true" />
      Cloud
    </span>
  )
}

function isUnavailable(model: AdminModel): boolean {
  return modelAvailability(model) === 'unavailable'
}

function Availability({ model }: { model: AdminModel }) {
  const state = modelAvailability(model)
  if (state === 'unknown') return <span className="text-faint">Non vérifiable</span>
  return state === 'available' ? (
    <span className="text-ledger">Disponible</span>
  ) : (
    <span className="font-medium text-danger">Sorti du catalogue</span>
  )
}

function ModelName({ model }: { model: AdminModel }) {
  return (
    <>
      <span className="font-medium">{model.name}</span>
      <span className="block text-xs text-slate">{model.provider_label}</span>
      <code className="block break-all text-[11px] text-faint">{model.id}</code>
    </>
  )
}

/** Administration › Modèles: which models the chat offers, in which order, and the default one. */
export function Models() {
  const [state, setState] = useState<LoadState>({ status: 'loading' })
  const [draft, setDraft] = useState<Draft>({ offered: [], default: '' })
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [confirmSave, setConfirmSave] = useState(false)
  const [confirmReset, setConfirmReset] = useState(false)
  const [announce, setAnnounce] = useState('')
  const moveButtons = useRef(new Map<string, HTMLButtonElement>())
  const [focusAfterMove, setFocusAfterMove] = useState<{ id: string; dir: 'up' | 'down' } | null>(null)

  const apply = useCallback((view: AdminModels) => {
    setState({ status: 'ready', view })
    setDraft(savedDraft(view))
  }, [])

  const load = useCallback(() => {
    setState((s) => (s.status === 'ready' ? s : { status: 'loading' }))
    setPageError(null)
    api
      .adminModels()
      .then(apply)
      .catch((e: unknown) => setState({ status: 'error', message: errorMessage(e) }))
  }, [apply])

  useEffect(() => {
    load()
  }, [load])

  // Keyboard reordering: focus stays on the moved model (on its other arrow at either end).
  useEffect(() => {
    if (!focusAfterMove) return
    const { id, dir } = focusAfterMove
    const same = moveButtons.current.get(`${id}:${dir}`)
    const other = moveButtons.current.get(`${id}:${dir === 'up' ? 'down' : 'up'}`)
    ;(same && !same.disabled ? same : other)?.focus()
    setFocusAfterMove(null)
  }, [focusAfterMove, draft])

  const view = state.status === 'ready' ? state.view : null
  const byId = useMemo(() => new Map((view?.models ?? []).map((m) => [m.id, m])), [view])
  const saved = useMemo(() => (view ? savedDraft(view) : null), [view])
  const dirty = saved !== null && !sameDraft(draft, saved)

  // Unsaved changes: leaving the tab or the page asks first (in-app links, back button, reload, close).
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  if (state.status === 'loading') return <p className="text-sm text-slate">Chargement des modèles…</p>
  if (state.status === 'error' || !view || !saved) {
    return (
      <Notice tone="error" className="flex max-w-3xl flex-wrap items-center justify-between gap-3">
        <span>{state.status === 'error' ? state.message : 'Une erreur est survenue.'}</span>
        <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={load}>
          <RefreshCw size={14} aria-hidden="true" />
          Réessayer
        </button>
      </Notice>
    )
  }

  const readOnly = !view.catalogue_available
  const offered = draft.offered.map((id) => byId.get(id)).filter((m): m is AdminModel => m !== undefined)
  const others = view.models.filter((m) => !draft.offered.includes(m.id))
  const outOfCatalogue = offered.filter(isUnavailable)
  const savedUnavailable = saved.offered.some((id) => {
    const model = byId.get(id)
    return model !== undefined && isUnavailable(model)
  })
  const cloud = offered.filter((m) => !m.is_local)
  const removed = saved.offered.filter((id) => !draft.offered.includes(id)).map((id) => byId.get(id)?.name ?? id)
  const consoleUrl = view.gateway ? safeConsoleUrl(view.console_url) : null

  const problems: string[] = []
  if (draft.offered.length === 0) problems.push('Proposez au moins un modèle.')
  if (draft.offered.length > MAX_OFFERED) problems.push(`Vous ne pouvez pas proposer plus de ${MAX_OFFERED} modèles.`)
  if (draft.offered.length > 0 && !draft.offered.includes(draft.default))
    problems.push('Choisissez le modèle par défaut parmi les modèles proposés.')
  if (outOfCatalogue.length > 0)
    problems.push(`Retirez les modèles sortis du catalogue avant d'enregistrer : ${outOfCatalogue.map((m) => m.name).join(', ')}.`)

  const change = (next: Draft) => {
    setDraft(next)
    setSaveError(null)
    setDone(null)
  }

  const toggle = (model: AdminModel, on: boolean) => {
    if (on) {
      const list = [...draft.offered, model.id]
      change({ offered: list, default: draft.offered.includes(draft.default) ? draft.default : model.id })
    } else {
      change({ offered: draft.offered.filter((id) => id !== model.id), default: draft.default === model.id ? '' : draft.default })
    }
  }

  const move = (model: AdminModel, dir: 'up' | 'down') => {
    const from = draft.offered.indexOf(model.id)
    const to = dir === 'up' ? from - 1 : from + 1
    if (from < 0 || to < 0 || to >= draft.offered.length) return
    const list = [...draft.offered]
    list.splice(from, 1)
    list.splice(to, 0, model.id)
    change({ ...draft, offered: list })
    setFocusAfterMove({ id: model.id, dir })
    setAnnounce(`${model.name} : position ${to + 1} sur ${list.length}.`)
  }

  const save = async () => {
    setBusy(true)
    setSaveError(null)
    try {
      const next = await api.setAdminModels({ offered: draft.offered, default: draft.default })
      apply(next)
      modelsChanged()
      const name = next.models.find((m) => m.is_default)?.name ?? next.default
      setDone(
        `Réglage enregistré : le chat propose désormais ${plural(next.models.filter((m) => m.offered).length, 'modèle')}, « ${name} » par défaut.`,
      )
    } catch (e) {
      setSaveError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    setBusy(true)
    setPageError(null)
    try {
      apply(await api.resetAdminModels())
      modelsChanged()
      setSaveError(null)
      setDone("Retour aux valeurs de l'environnement : le chat propose de nouveau les modèles prévus par la configuration du serveur.")
    } catch (e) {
      setPageError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const disabled = readOnly || busy

  return (
    <section className="max-w-5xl space-y-6 pb-2">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h2 className="font-semibold">Modèles proposés dans le chat</h2>
          <p className="mt-1 text-sm text-slate">
            Choisissez les modèles que les utilisateurs peuvent sélectionner, leur ordre dans la liste et le modèle utilisé
            par défaut.
          </p>
          <p className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <span className="text-slate">Origine :</span>
            {view.source === 'settings' ? (
              <span className="rounded bg-ledger-soft px-1.5 py-0.5 text-xs font-medium text-ledger-dark">Réglage défini ici</span>
            ) : (
              <span className="rounded bg-wash px-1.5 py-0.5 text-xs font-medium text-ink">Valeurs de l'environnement</span>
            )}
            <span className="text-xs text-slate">
              {view.source === 'settings'
                ? 'enregistré depuis cette page, il remplace la configuration du serveur.'
                : "tant qu'aucun réglage n'est enregistré ici, la liste vient de la configuration du serveur."}
            </span>
          </p>
        </div>
        {view.source === 'settings' && (
          <button type="button" className="btn-secondary" onClick={() => setConfirmReset(true)} disabled={disabled}>
            <RotateCcw size={16} aria-hidden="true" />
            Revenir aux valeurs de l'environnement
          </button>
        )}
      </div>

      {readOnly && (
        <Notice tone="warning" className="flex flex-wrap items-center justify-between gap-3">
          <span>
            La passerelle LLM est injoignable : les modèles qu'elle sert ne peuvent pas être vérifiés. La liste est affichée
            en lecture seule ; aucune modification n'est possible pour l'instant.
          </span>
          <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={load}>
            <RefreshCw size={14} aria-hidden="true" />
            Réessayer
          </button>
        </Notice>
      )}
      {pageError && <Notice tone="error">{pageError}</Notice>}
      {done && <Notice tone="success">{done}</Notice>}

      {view.gateway && (
        <div className="rounded-lg border border-rule bg-surface px-4 py-3 text-sm">
          <p className="text-slate">
            Les fournisseurs, les clés d'accès et les tarifs se gèrent dans la console de la passerelle LLM ; seuls les modèles
            qu'elle sert apparaissent ici.
          </p>
          {consoleUrl && (
            <a
              href={consoleUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="mt-2 inline-flex items-center gap-1.5 font-medium text-ledger underline-offset-2 hover:underline"
            >
              Ouvrir la console de la passerelle LLM
              <ExternalLink size={14} aria-hidden="true" />
              <span className="sr-only">(nouvel onglet)</span>
            </a>
          )}
        </div>
      )}

      {offered.length > 0 &&
        (cloud.length > 0 ? (
          <Notice tone="warning">
            Modèles cloud proposés : {cloud.map((m) => `${m.name} (${m.provider_label})`).join(', ')}. Les extraits utiles des
            documents leur sont transmis quand un utilisateur les choisit.
          </Notice>
        ) : (
          <Notice tone="success">Seuls des modèles locaux sont proposés : aucun extrait de document n'est transmis à un modèle cloud.</Notice>
        ))}

      {savedUnavailable && !dirty && (
        <Notice tone="warning">
          Certains modèles proposés ne sont plus servis : ils apparaissent comme indisponibles dans le chat. Décochez-les puis
          enregistrez.
        </Notice>
      )}

      <section aria-labelledby="models-offered-title">
        <h3 id="models-offered-title" className="mb-2 text-sm font-semibold">
          Proposés dans le chat <span className="font-normal text-slate">({draft.offered.length})</span>
        </h3>
        {offered.length === 0 ? (
          <p className="rounded-lg border border-dashed border-rule bg-surface px-4 py-3 text-sm text-slate">
            Aucun modèle proposé. Cochez au moins un modèle ci-dessous.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
            <table className="w-full border-collapse">
              <caption className="sr-only">Modèles proposés dans le chat, dans l'ordre d'affichage</caption>
              <thead>
                <tr>
                  <th scope="col" className="table-head w-24">
                    Proposé
                  </th>
                  <th scope="col" className="table-head">
                    Modèle
                  </th>
                  <th scope="col" className="table-head">
                    Type
                  </th>
                  <th scope="col" className="table-head">
                    État
                  </th>
                  <th scope="col" className="table-head text-center">
                    Par défaut
                  </th>
                  <th scope="col" className="table-head text-right">
                    Ordre
                  </th>
                </tr>
              </thead>
              <tbody>
                {offered.map((m, i) => {
                  const unavailable = isUnavailable(m)
                  return (
                    <tr key={m.id} className={unavailable ? 'bg-danger-soft/40' : undefined}>
                      <td className="table-cell">
                        <label className="flex items-center gap-2 text-xs text-slate">
                          <input
                            type="checkbox"
                            className="h-4 w-4 accent-ledger"
                            checked
                            onChange={() => toggle(m, false)}
                            disabled={disabled}
                            aria-label={`Proposer ${m.name} dans le chat`}
                          />
                          <span aria-hidden="true">n° {i + 1}</span>
                        </label>
                      </td>
                      <td className="table-cell">
                        <ModelName model={m} />
                      </td>
                      <td className="table-cell">
                        <KindBadge model={m} />
                      </td>
                      <td className="table-cell whitespace-nowrap">
                        <Availability model={m} />
                      </td>
                      <td className="table-cell text-center">
                        <input
                          type="radio"
                          name="default-model"
                          className="h-4 w-4 accent-ledger"
                          checked={draft.default === m.id}
                          onChange={() => change({ ...draft, default: m.id })}
                          disabled={disabled || unavailable}
                          aria-label={`Modèle par défaut : ${m.name}`}
                        />
                      </td>
                      <td className="table-cell">
                        <div className="flex justify-end gap-1">
                          <button
                            type="button"
                            ref={(el) => {
                              if (el) moveButtons.current.set(`${m.id}:up`, el)
                              else moveButtons.current.delete(`${m.id}:up`)
                            }}
                            className="btn-ghost p-1.5"
                            onClick={() => move(m, 'up')}
                            disabled={disabled || i === 0}
                            aria-label={`Monter ${m.name}`}
                            title="Monter"
                          >
                            <ArrowUp size={16} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            ref={(el) => {
                              if (el) moveButtons.current.set(`${m.id}:down`, el)
                              else moveButtons.current.delete(`${m.id}:down`)
                            }}
                            className="btn-ghost p-1.5"
                            onClick={() => move(m, 'down')}
                            disabled={disabled || i === offered.length - 1}
                            aria-label={`Descendre ${m.name}`}
                            title="Descendre"
                          >
                            <ArrowDown size={16} aria-hidden="true" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
        <p className="sr-only" aria-live="polite">
          {announce}
        </p>
      </section>

      <section aria-labelledby="models-others-title">
        <h3 id="models-others-title" className="mb-2 text-sm font-semibold">
          Autres modèles du catalogue <span className="font-normal text-slate">({others.length})</span>
        </h3>
        {others.length === 0 ? (
          <p className="text-sm text-slate">
            {readOnly ? 'Catalogue indisponible : les autres modèles ne peuvent pas être listés.' : 'Tous les modèles du catalogue sont proposés.'}
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
            <table className="w-full border-collapse">
              <caption className="sr-only">Modèles du catalogue non proposés dans le chat</caption>
              <thead>
                <tr>
                  <th scope="col" className="table-head w-24">
                    Proposé
                  </th>
                  <th scope="col" className="table-head">
                    Modèle
                  </th>
                  <th scope="col" className="table-head">
                    Type
                  </th>
                  <th scope="col" className="table-head">
                    État
                  </th>
                </tr>
              </thead>
              <tbody>
                {others.map((m) => {
                  const unavailable = isUnavailable(m)
                  return (
                    <tr key={m.id} className={unavailable ? 'text-faint' : undefined}>
                      <td className="table-cell">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-ledger"
                          checked={false}
                          onChange={() => toggle(m, true)}
                          disabled={disabled || unavailable}
                          aria-label={`Proposer ${m.name} dans le chat`}
                          title={unavailable ? "Ce modèle n'est plus servi : il ne peut pas être proposé." : undefined}
                        />
                      </td>
                      <td className="table-cell">
                        <ModelName model={m} />
                      </td>
                      <td className="table-cell">
                        <KindBadge model={m} />
                      </td>
                      <td className="table-cell whitespace-nowrap">
                        <Availability model={m} />
                        {unavailable && <span className="block text-xs text-slate">Retiré à l'enregistrement</span>}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {dirty && !readOnly && (
        <div
          className="sticky bottom-0 z-10 rounded-lg border border-rule bg-surface px-4 py-3 shadow-[0_-8px_24px_-16px_rgba(22,33,58,0.35)]"
          role="region"
          aria-label="Modifications non enregistrées"
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm font-medium">Modifications non enregistrées</p>
            <div className="flex gap-2">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => {
                  setDraft(saved)
                  setSaveError(null)
                }}
                disabled={busy}
              >
                Annuler
              </button>
              <button
                type="button"
                className="btn-primary"
                onClick={() => (removed.length > 0 ? setConfirmSave(true) : save())}
                disabled={busy || problems.length > 0}
                aria-describedby={problems.length > 0 ? 'models-problems' : undefined}
              >
                {busy ? 'Enregistrement…' : 'Enregistrer'}
              </button>
            </div>
          </div>
          {problems.length > 0 && (
            <ul id="models-problems" className="mt-2 space-y-0.5 text-sm text-danger">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
          {saveError && (
            <Notice tone="error" className="mt-2">
              {saveError}
            </Notice>
          )}
        </div>
      )}

      <ConfirmDialog
        open={confirmSave}
        onOpenChange={setConfirmSave}
        title={removed.length > 1 ? `Retirer ${removed.length} modèles du chat ?` : 'Retirer ce modèle du chat ?'}
        description={
          <>
            {removed.join(', ')} ne {removed.length > 1 ? 'seront' : 'sera'} plus proposé{removed.length > 1 ? 's' : ''} aux
            utilisateurs. Les conversations en cours ne pourront plus {removed.length > 1 ? 'les ' : "l'"}utiliser.
          </>
        }
        confirmLabel="Retirer et enregistrer"
        onConfirm={save}
      />

      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="Revenir aux valeurs de l'environnement ?"
        description="Le réglage défini ici est supprimé : le chat propose de nouveau les modèles prévus par la configuration du serveur, avec son modèle par défaut. Les conversations en cours ne pourront plus utiliser un modèle qui n'en fait pas partie."
        confirmLabel="Revenir aux valeurs de l'environnement"
        onConfirm={reset}
      />

      <Dialog
        open={blocker.state === 'blocked'}
        onOpenChange={(open) => !open && blocker.state === 'blocked' && blocker.reset()}
        title="Quitter sans enregistrer ?"
        description="Les modifications des modèles proposés n'ont pas été enregistrées : elles seront perdues."
        footer={
          <>
            <button type="button" className="btn-secondary" onClick={() => blocker.reset?.()}>
              Rester sur la page
            </button>
            <button type="button" className="btn-danger" onClick={() => blocker.proceed?.()}>
              Quitter sans enregistrer
            </button>
          </>
        }
      />
    </section>
  )
}
