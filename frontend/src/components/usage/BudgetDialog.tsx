import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import { api, errorMessage, type BudgetPeriodKind, type UsageBudget } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  BUDGET_PERIODS,
  amountInput,
  amountProblem,
  budgetPeriodLabel,
  formatAmount,
  formatDateTimeLong,
  parseAmount,
} from '@/lib/usage'
import { Dialog } from '@/components/ui/Dialog'
import { Notice } from '@/components/ui/Notice'
import { BudgetBar } from './UsageParts'

export interface BudgetTarget {
  id: string
  label: string
}

type Phase =
  | { status: 'loading' }
  | { status: 'unavailable'; reason: string }
  | { status: 'error'; message: string }
  | { status: 'ready' }

const FOCUS_RING = 'focus-visible:ring-2 focus-visible:ring-ledger focus-visible:ring-offset-1'

/**
 * "Budget de <nom>": reads the user's budget from the LLM gateway (through the API), sets or
 * removes it. Amounts are in the currency returned by the budget routes (`currency`, the
 * report's one, is only shown until the budget is read).
 */
export function BudgetDialog({
  target,
  currency,
  onClose,
  onChanged,
}: {
  target: BudgetTarget | null
  currency?: string
  onClose: () => void
  onChanged?: (userId: string, budget: UsageBudget | null) => void
}) {
  const ids = useId()
  const [phase, setPhase] = useState<Phase>({ status: 'loading' })
  const [budget, setBudget] = useState<UsageBudget | null>(null)
  const [readCurrency, setReadCurrency] = useState<string | null>(null)
  const [amount, setAmount] = useState('')
  const [period, setPeriod] = useState<BudgetPeriodKind>('month')
  const [fieldError, setFieldError] = useState<string | null>(null)
  const [serverError, setServerError] = useState<string | null>(null)
  const [info, setInfo] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const amountRef = useRef<HTMLInputElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const targetId = target?.id ?? null

  useEffect(() => {
    if (!targetId) return
    let cancelled = false
    setPhase({ status: 'loading' })
    setServerError(null)
    setFieldError(null)
    setInfo(null)
    setConfirming(false)
    setReadCurrency(null)
    api
      .userBudget(targetId)
      .then((data) => {
        if (cancelled) return
        if (!data.available) {
          setPhase({ status: 'unavailable', reason: data.reason })
          return
        }
        setBudget(data.budget)
        setReadCurrency(data.currency || null)
        setAmount(data.budget?.limit != null ? amountInput(data.budget.limit) : '')
        setPeriod(data.budget?.period_kind ?? 'month')
        setPhase({ status: 'ready' })
      })
      .catch((error: unknown) => {
        if (!cancelled) setPhase({ status: 'error', message: errorMessage(error) })
      })
    return () => {
      cancelled = true
    }
  }, [targetId, attempt])

  // Focus moves to the field once it exists (the dialog first focuses its close button).
  useEffect(() => {
    if (phase.status === 'ready' && !confirming) amountRef.current?.focus()
  }, [phase.status, confirming])
  useEffect(() => {
    if (confirming) confirmRef.current?.focus()
  }, [confirming])

  const label = target?.label ?? ''
  const shownCurrency = readCurrency ?? currency
  const managedElsewhere = budget !== null && !budget.managed
  const typed = parseAmount(amount)
  const periodChanges = budget !== null && budget.period_kind !== period
  const amountChanges = budget !== null && !periodChanges && typed !== null && typed !== budget.limit

  const save = async () => {
    if (!targetId) return
    const problem = amountProblem(amount)
    setFieldError(problem)
    setServerError(null)
    if (problem) {
      amountRef.current?.focus()
      return
    }
    setBusy(true)
    try {
      const data = await api.setUserBudget(targetId, { limit: parseAmount(amount) as number, period })
      if (!data.available) {
        setServerError(data.reason)
        return
      }
      setReadCurrency(data.currency || null)
      onChanged?.(targetId, data.budget)
      onClose()
    } catch (error) {
      setServerError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!targetId) return
    setBusy(true)
    setServerError(null)
    try {
      const data = await api.removeUserBudget(targetId)
      if (!data.available) {
        setServerError(data.reason)
        setConfirming(false)
        return
      }
      setReadCurrency(data.currency || null)
      onChanged?.(targetId, data.budget)
      if (data.budget === null) {
        onClose()
        return
      }
      // Read back by the API: the gateway still applies a budget of its own.
      setBudget(data.budget)
      setAmount(data.budget.limit != null ? amountInput(data.budget.limit) : '')
      setPeriod(data.budget.period_kind ?? 'month')
      setInfo('Le budget défini depuis ContextHub a été retiré. La passerelle LLM applique encore le budget ci-dessous.')
      setConfirming(false)
    } catch (error) {
      setServerError(errorMessage(error))
      setConfirming(false)
    } finally {
      setBusy(false)
    }
  }

  let footer: ReactNode
  if (phase.status !== 'ready') {
    footer = (
      <button type="button" className="btn-secondary" onClick={onClose}>
        Fermer
      </button>
    )
  } else if (confirming) {
    footer = (
      <>
        <button type="button" className="btn-secondary" onClick={() => setConfirming(false)} disabled={busy}>
          Ne pas retirer
        </button>
        <button ref={confirmRef} type="button" className="btn-danger" onClick={remove} disabled={busy}>
          Retirer le budget
        </button>
      </>
    )
  } else {
    footer = (
      <>
        {budget !== null && (
          <button
            type="button"
            className="btn-ghost mr-auto text-danger"
            onClick={() => setConfirming(true)}
            disabled={busy || managedElsewhere}
            aria-describedby={managedElsewhere ? `${ids}-elsewhere` : undefined}
          >
            Retirer le budget
          </button>
        )}
        <button type="button" className="btn-secondary" onClick={onClose} disabled={busy}>
          Annuler
        </button>
        <button type="submit" form={`${ids}-form`} className="btn-primary" disabled={busy}>
          {busy ? 'Enregistrement…' : 'Enregistrer'}
        </button>
      </>
    )
  }

  return (
    <Dialog open={target !== null} onOpenChange={(open) => !open && !busy && onClose()} title={`Budget de ${label}`} footer={footer}>
      {phase.status === 'loading' && (
        <p className="text-sm text-slate" role="status">
          Lecture du budget auprès de la passerelle LLM…
        </p>
      )}
      {phase.status === 'unavailable' && <Notice>{phase.reason}</Notice>}
      {phase.status === 'error' && (
        <Notice tone="error" className="flex flex-wrap items-center justify-between gap-3">
          <span>{phase.message}</span>
          <button type="button" className="btn-secondary px-2.5 py-1 text-xs" onClick={() => setAttempt((n) => n + 1)}>
            <RefreshCw size={14} aria-hidden="true" />
            Réessayer
          </button>
        </Notice>
      )}

      {phase.status === 'ready' && confirming && (
        <div className="space-y-3 text-sm">
          {serverError && <Notice tone="error">{serverError}</Notice>}
          <p className="font-medium">Retirer le budget de {label} ?</p>
          <p className="text-slate">
            La passerelle LLM ne plafonnera plus ses dépenses. Vous pourrez définir un nouveau budget à tout moment.
          </p>
        </div>
      )}

      {phase.status === 'ready' && !confirming && (
        <form
          id={`${ids}-form`}
          className="space-y-4"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            save()
          }}
        >
          {info && <Notice tone="success">{info}</Notice>}
          {serverError && <Notice tone="error">{serverError}</Notice>}

          <div className="rounded-md border border-rule bg-wash px-3 py-2.5 text-sm">
            {budget === null ? (
              <p className="text-slate">Aucun budget n'est défini : les dépenses de cet utilisateur ne sont pas plafonnées.</p>
            ) : (
              <>
                <p>
                  <span className="font-medium tabular-nums">{budget.spent !== null ? formatAmount(budget.spent, shownCurrency) : '—'}</span>
                  <span className="text-slate">
                    {' '}
                    consommés sur {budget.limit !== null ? formatAmount(budget.limit, shownCurrency) : '—'} {budgetPeriodLabel(budget)}
                  </span>
                </p>
                <BudgetBar budget={budget} label={`Part du budget de ${label} consommée`} className="mt-2" />
                {budget.resets_at && (
                  <p className="mt-2 text-xs text-slate">Remise à zéro le {formatDateTimeLong(budget.resets_at)}.</p>
                )}
                {managedElsewhere && (
                  <p id={`${ids}-elsewhere`} className="mt-2 text-xs text-refusal">
                    Ce budget a été défini dans la console de la passerelle LLM : il ne peut pas être retiré d'ici.
                    Enregistrer un montant ci-dessous le remplace par un budget géré depuis ContextHub.
                  </p>
                )}
              </>
            )}
          </div>

          <div>
            <label className="label" htmlFor={`${ids}-amount`}>
              Montant maximal {shownCurrency ? `(${shownCurrency})` : ''}
            </label>
            <input
              ref={amountRef}
              id={`${ids}-amount`}
              className={cn('input', FOCUS_RING, fieldError && 'border-danger')}
              inputMode="decimal"
              autoComplete="off"
              placeholder="Par exemple 50 ou 12,50"
              value={amount}
              onChange={(e) => {
                setAmount(e.target.value)
                if (fieldError) setFieldError(null)
              }}
              aria-invalid={fieldError ? true : undefined}
              aria-describedby={`${fieldError ? `${ids}-amount-error ` : ''}${ids}-amount-help`}
              disabled={busy}
            />
            {fieldError && (
              <p id={`${ids}-amount-error`} className="mt-1 text-sm text-danger">
                {fieldError}
              </p>
            )}
            <p id={`${ids}-amount-help`} className="mt-1 text-xs text-slate">
              {shownCurrency
                ? `Montant en ${shownCurrency}. Virgule ou point acceptés.`
                : 'Montant dans la devise de la passerelle LLM. Virgule ou point acceptés.'}
            </p>
          </div>

          <fieldset>
            <legend className="label">Période</legend>
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {BUDGET_PERIODS.map(([value, text]) => (
                <label key={value} className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={`${ids}-period`}
                    value={value}
                    checked={period === value}
                    onChange={() => setPeriod(value)}
                    disabled={busy}
                    className="accent-ledger"
                  />
                  {text}
                </label>
              ))}
            </div>
            <p className="mt-2 text-xs text-slate">
              « 30 jours glissants » n'est pas un mois civil : le compteur couvre les 30 jours qui suivent sa remise à zéro.
            </p>
          </fieldset>

          <p className="text-xs text-slate" aria-live="polite">
            {periodChanges
              ? "La période change : le compteur repartira de zéro à l'enregistrement, pour une nouvelle fenêtre."
              : amountChanges
                ? 'Seul le montant change : la fenêtre en cours et le montant déjà consommé sont conservés.'
                : budget === null
                  ? "Le décompte commence à l'enregistrement ; une fois le montant atteint, la passerelle LLM refuse les nouvelles questions."
                  : 'Changer la période relance la fenêtre ; changer seulement le montant ne la relance pas.'}
          </p>
        </form>
      )}
    </Dialog>
  )
}
