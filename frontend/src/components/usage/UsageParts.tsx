import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { RefreshCw } from 'lucide-react'
import axios from 'axios'
import { errorMessage, type ModelUsage, type UsageBudget, type UsageReport, type UsageTotals, type UsageUnavailable } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  PERIOD_PRESETS,
  budgetPeriodLabel,
  budgetRatio,
  budgetTone,
  chartSeries,
  formatCompact,
  formatCost,
  formatDateTimeLong,
  formatInteger,
  formatPeriod,
  formatShare,
  periodProblem,
  presetPeriod,
  type Period,
  type PeriodPreset,
} from '@/lib/usage'
import { Notice } from '@/components/ui/Notice'

// ---------------------------------------------------------------- loading

export type UsageState<T extends UsageReport> =
  | { status: 'loading' }
  | { status: 'unavailable'; reason: string }
  | { status: 'error'; message: string; retryable: boolean }
  | { status: 'ready'; report: T }

/** Loads a report for a period; a newer request always wins over a slower older one. */
export function useUsageReport<T extends UsageReport>(
  fetcher: (from: string, to: string) => Promise<T | UsageUnavailable>,
  period: Period,
): { state: UsageState<T>; reload: () => void; refreshing: boolean } {
  const [state, setState] = useState<UsageState<T>>({ status: 'loading' })
  const [refreshing, setRefreshing] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const latest = useRef(0)

  useEffect(() => {
    const request = ++latest.current
    setRefreshing(true)
    setState((current) => (current.status === 'ready' ? current : { status: 'loading' }))
    fetcher(period.from, period.to)
      .then((data) => {
        if (request !== latest.current) return
        setState(data.available ? { status: 'ready', report: data } : { status: 'unavailable', reason: data.reason ?? '' })
      })
      .catch((error: unknown) => {
        if (request !== latest.current) return
        const status = axios.isAxiosError(error) ? error.response?.status : undefined
        // 422 (period refused) will not change on retry; 503 (gateway down) or no answer may.
        setState({ status: 'error', message: errorMessage(error), retryable: status !== 422 && status !== 403 })
      })
      .finally(() => {
        if (request === latest.current) setRefreshing(false)
      })
  }, [fetcher, period.from, period.to, attempt])

  const reload = useCallback(() => setAttempt((n) => n + 1), [])
  return { state, reload, refreshing }
}

/** `unavailableFallback` is only shown when the API gives no reason of its own. */
export function UsageStatus<T extends UsageReport>({
  state,
  reload,
  unavailableFallback,
}: {
  state: Exclude<UsageState<T>, { status: 'ready' }>
  reload: () => void
  unavailableFallback: ReactNode
}) {
  if (state.status === 'loading') return <p className="text-sm text-slate">Chargement de la consommation…</p>
  if (state.status === 'unavailable') {
    const reason = state.reason.trim()
    return <Notice>{reason ? <span className="whitespace-pre-line">{reason}</span> : unavailableFallback}</Notice>
  }
  return (
    <Notice tone="error" className="flex flex-wrap items-center justify-between gap-3">
      <span>{state.message}</span>
      {state.retryable && (
        <button className="btn-secondary px-2.5 py-1 text-xs" onClick={reload}>
          <RefreshCw size={14} />
          Réessayer
        </button>
      )}
    </Notice>
  )
}

/** Discreet line for a breakdown the gateway does not provide. */
export function NotProvided({ what }: { what: string }) {
  return <p className="text-sm text-faint">{what} : non fourni par la passerelle LLM.</p>
}

// ---------------------------------------------------------------- period

/** `.input` drops the outline for a border colour: keyboard focus also gets a ring here. */
const FOCUS_RING = 'focus-visible:ring-2 focus-visible:ring-ledger focus-visible:ring-offset-1'

export function PeriodPicker({
  preset,
  period,
  onChange,
  disabled,
}: {
  preset: PeriodPreset
  period: Period
  onChange: (preset: PeriodPreset, period: Period) => void
  disabled?: boolean
}) {
  const [from, setFrom] = useState(period.from)
  const [to, setTo] = useState(period.to)
  const [problem, setProblem] = useState<string | null>(null)

  useEffect(() => {
    setFrom(period.from)
    setTo(period.to)
  }, [period.from, period.to])

  const choose = (next: PeriodPreset) => {
    setProblem(null)
    if (next === 'custom') onChange('custom', period)
    else onChange(next, presetPeriod(next))
  }

  const apply = () => {
    const issue = periodProblem(from, to)
    setProblem(issue)
    if (!issue) onChange('custom', { from, to })
  }

  return (
    <div>
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label className="label" htmlFor="usage-preset">
            Période
          </label>
          <select id="usage-preset" className={cn('input w-auto pr-8', FOCUS_RING)} value={preset} onChange={(e) => choose(e.target.value as PeriodPreset)} disabled={disabled}>
            {PERIOD_PRESETS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {preset === 'custom' && (
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              apply()
            }}
          >
            <div>
              <label className="label" htmlFor="usage-from">
                Du
              </label>
              <input id="usage-from" type="date" className={cn('input', FOCUS_RING)} value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} required />
            </div>
            <div>
              <label className="label" htmlFor="usage-to">
                Au
              </label>
              <input id="usage-to" type="date" className={cn('input', FOCUS_RING)} value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} required />
            </div>
            <button className="btn-secondary" type="submit" disabled={disabled}>
              Afficher
            </button>
          </form>
        )}
      </div>
      {problem && (
        <Notice tone="error" className="mt-3">
          {problem}
        </Notice>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- indicators

function Kpi({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-rule bg-surface px-4 py-3">
      <dt className="text-xs text-slate">{label}</dt>
      <dd className="mt-1 whitespace-nowrap text-xl font-semibold tabular-nums text-ink" title={title}>
        {value}
      </dd>
    </div>
  )
}

export function UsageKpis({ totals, currency }: { totals: UsageTotals; currency: string }) {
  return (
    // Four side by side only from 1280 px: beside the 256 px sidebar, narrower screens leave
    // too little room for an amount such as "12 345,67 $US" on one line (2 × 2 instead).
    <dl className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <Kpi label="Coût" value={formatCost(totals.cost, currency)} />
      <Kpi label="Requêtes" value={formatInteger(totals.requests)} />
      <Kpi label="Jetons d'entrée" value={formatCompact(totals.input_tokens)} title={formatInteger(totals.input_tokens)} />
      <Kpi label="Jetons de sortie" value={formatCompact(totals.output_tokens)} title={formatInteger(totals.output_tokens)} />
    </dl>
  )
}

export function isEmptyReport(report: UsageReport): boolean {
  const t = report.totals
  return t.requests === 0 && t.cost === 0 && t.input_tokens === 0 && t.output_tokens === 0
}

// ---------------------------------------------------------------- chart

type Metric = 'cost' | 'requests'

export function UsageChart({ report }: { report: UsageReport }) {
  const [metric, setMetric] = useState<Metric>('cost')
  const [hover, setHover] = useState<number | null>(null)
  const { unit, points } = useMemo(() => chartSeries(report.daily, report.period), [report.daily, report.period])
  const values = points.map((p) => p[metric])
  const max = Math.max(0, ...values)
  const format = (n: number) => (metric === 'cost' ? formatCost(n, report.currency) : formatInteger(n))
  const metricLabel = metric === 'cost' ? 'Coût' : 'Requêtes'
  const peak = max > 0 ? points[values.indexOf(max)] : null
  const shown = hover !== null ? points[hover] : null
  const unitLabel = unit === 'day' ? 'jour' : 'mois'
  const ticks = points.length <= 1 ? [0] : [0, Math.floor((points.length - 1) / 2), points.length - 1]
  const dense = points.length > 62

  return (
    <section className="rounded-lg border border-rule bg-surface p-4" aria-labelledby="usage-chart-title">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 id="usage-chart-title" className="font-semibold">
          Évolution par {unitLabel}
        </h2>
        <div className="flex rounded-md border border-rule p-0.5" role="group" aria-label="Mesure affichée">
          {(['cost', 'requests'] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={metric === m}
              onClick={() => setMetric(m)}
              className={cn('rounded px-2.5 py-1 text-xs', metric === m ? 'bg-wash font-medium text-ink' : 'text-slate hover:text-ink')}
            >
              {m === 'cost' ? 'Coût' : 'Requêtes'}
            </button>
          ))}
        </div>
      </div>

      <p className="mb-2 h-5 text-sm tabular-nums text-slate" aria-hidden="true">
        {shown ? (
          <>
            <span className="text-ink">{shown.label}</span> · {formatCost(shown.cost, report.currency)} · {formatInteger(shown.requests)}{' '}
            {shown.requests > 1 ? 'requêtes' : 'requête'}
          </>
        ) : peak ? (
          <>
            Maximum : <span className="text-ink">{format(max)}</span> ({peak.label})
          </>
        ) : (
          'Aucune valeur sur la période.'
        )}
      </p>

      <div
        role="img"
        aria-label={`Graphique en barres, ${metricLabel.toLowerCase()} par ${unitLabel}, ${formatPeriod(report.period)}. ${
          peak ? `Valeur la plus haute : ${format(max)}, ${peak.label}.` : 'Aucune valeur sur la période.'
        } Le détail figure dans le tableau qui suit.`}
        className="relative"
      >
        <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-rule" aria-hidden="true" />
        <span className="pointer-events-none absolute right-0 top-0 -translate-y-full pb-0.5 text-[11px] tabular-nums text-faint" aria-hidden="true">
          {max > 0 ? format(max) : ''}
        </span>
        <div className={cn('flex h-40 items-end border-b border-rule', dense ? 'gap-0' : 'gap-0.5')} onMouseLeave={() => setHover(null)}>
          {points.map((p, i) => {
            const height = max > 0 ? (p[metric] / max) * 100 : 0
            return (
              <div key={p.key} className="flex h-full min-w-0 flex-1 items-end" onMouseEnter={() => setHover(i)}>
                <div
                  className={cn('w-full rounded-t-[3px] transition-colors', hover === i ? 'bg-ledger-dark' : 'bg-ledger', p[metric] > 0 && 'min-h-[2px]')}
                  style={{ height: `${height}%` }}
                />
              </div>
            )
          })}
        </div>
        <div className="relative mt-1 h-4 text-[11px] text-faint" aria-hidden="true">
          {ticks.map((t, i) => (
            <span
              key={t}
              className={cn('absolute top-0 whitespace-nowrap', i === 0 ? 'left-0' : i === ticks.length - 1 ? 'right-0' : '-translate-x-1/2')}
              style={i > 0 && i < ticks.length - 1 ? { left: `${((t + 0.5) / points.length) * 100}%` } : undefined}
            >
              {points[t]?.tick}
            </span>
          ))}
        </div>
      </div>

      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-slate hover:text-ink">Afficher les valeurs</summary>
        <div className="mt-2 max-h-72 overflow-y-auto">
          <table className="w-full border-collapse">
            <caption className="sr-only">
              Consommation par {unitLabel}, {formatPeriod(report.period)}
            </caption>
            <thead>
              <tr>
                <th scope="col" className="table-head">
                  {unit === 'day' ? 'Jour' : 'Mois'}
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right">
                  Requêtes
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right">
                  Jetons d'entrée
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right">
                  Jetons de sortie
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right">
                  Coût
                </th>
              </tr>
            </thead>
            <tbody>
              {points.map((p) => (
                <tr key={p.key} className={p.requests === 0 && p.cost === 0 ? 'text-faint' : undefined}>
                  <th scope="row" className="table-cell text-left font-normal">
                    {p.label}
                  </th>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(p.requests)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(p.input_tokens)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(p.output_tokens)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatCost(p.cost, report.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </section>
  )
}

// ---------------------------------------------------------------- per model

export function ModelTable({ rows, total, currency }: { rows: ModelUsage[]; total: number; currency: string }) {
  const sorted = [...rows].sort((a, b) => b.cost - a.cost || b.requests - a.requests)
  return (
    <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
      <table className="w-full border-collapse">
        <caption className="sr-only">Consommation par modèle</caption>
        <thead>
          <tr>
            <th scope="col" className="table-head">
              Modèle
            </th>
            <th scope="col" className="table-head whitespace-nowrap text-right">
              Requêtes
            </th>
            <th scope="col" className="table-head whitespace-nowrap text-right">
              Jetons d'entrée
            </th>
            <th scope="col" className="table-head whitespace-nowrap text-right">
              Jetons de sortie
            </th>
            <th scope="col" className="table-head whitespace-nowrap text-right">
              Coût
            </th>
            <th scope="col" className="table-head whitespace-nowrap text-right">
              Part
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((m) => (
            <tr key={m.model}>
              <th scope="row" className="table-cell text-left font-normal">
                <code className="text-[13px]">{m.model}</code>
              </th>
              <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(m.requests)}</td>
              <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(m.input_tokens)}</td>
              <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(m.output_tokens)}</td>
              <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatCost(m.cost, currency)}</td>
              <td className="table-cell whitespace-nowrap text-right tabular-nums text-slate">{formatShare(m.cost, total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ---------------------------------------------------------------- budget

/** Thin bar of the share consumed, with the thresholds of the budget pages. */
export function BudgetBar({ budget, label, className }: { budget: UsageBudget; label: string; className?: string }) {
  const ratio = budgetRatio(budget)
  if (ratio === null) return null
  const percent = Math.round(Math.min(ratio, 1) * 100)
  return (
    <div
      className={cn('h-1.5 overflow-hidden rounded-full bg-wash', className)}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={`${percent} % du budget consommé`}
    >
      <div className={cn('h-full rounded-full', budgetTone(ratio))} style={{ width: `${percent}%` }} />
    </div>
  )
}

export function BudgetSummary({ budget, currency }: { budget: UsageBudget; currency: string }) {
  const { limit, spent, remaining } = budget
  const ratio = budgetRatio(budget)
  const percent = ratio === null ? null : Math.round(Math.min(ratio, 1) * 100)
  const window = budgetPeriodLabel(budget)

  return (
    <div className="rounded-lg border border-rule bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <p className="text-sm">
          <span className="text-xl font-semibold tabular-nums">{spent !== null ? formatCost(spent, currency) : '—'}</span>
          <span className="text-slate"> consommés sur {limit !== null ? formatCost(limit, currency) : '—'}</span>
        </p>
        {remaining !== null && (
          <p className={cn('text-sm', ratio !== null && ratio >= 1 ? 'font-medium text-danger' : 'text-slate')}>
            {ratio !== null && ratio >= 1 ? 'Budget atteint' : `Reste ${formatCost(remaining, currency)}`}
          </p>
        )}
      </div>
      {percent !== null && (
        <div
          className="mt-3 h-2 overflow-hidden rounded-full bg-wash"
          role="progressbar"
          aria-label="Part du budget consommée"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-valuetext={`${percent} % du budget consommé`}
        >
          <div className={cn('h-full rounded-full', budgetTone(ratio))} style={{ width: `${percent}%` }} />
        </div>
      )}
      <p className="mt-3 text-xs text-slate">
        {window ? `Budget ${window}${budget.period_kind === 'month' ? ' glissants' : ''}, décompté par la passerelle LLM` : 'Budget décompté par la passerelle LLM'}
        {budget.resets_at ? `, remis à zéro le ${formatDateTimeLong(budget.resets_at)}.` : '.'} Ce décompte suit sa propre
        période, indépendamment de celle choisie ci-dessus.
      </p>
    </div>
  )
}
