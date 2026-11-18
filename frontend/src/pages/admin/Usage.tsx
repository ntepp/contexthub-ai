import { useState } from 'react'
import { Download, Wallet } from 'lucide-react'
import { usage, type AdminUsageReport, type UsageBudget, type UserUsage } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  budgetPeriodLabel,
  budgetRatio,
  downloadBlob,
  formatCompact,
  formatCost,
  formatInteger,
  formatPeriod,
  formatShare,
  presetPeriod,
  usersCsv,
  type Period,
  type PeriodPreset,
} from '@/lib/usage'
import { Notice } from '@/components/ui/Notice'
import { BudgetDialog, type BudgetTarget } from '@/components/usage/BudgetDialog'
import {
  BudgetBar,
  ModelTable,
  NotProvided,
  PeriodPicker,
  UsageChart,
  UsageKpis,
  UsageStatus,
  isEmptyReport,
  useUsageReport,
} from '@/components/usage/UsageParts'

function byCost(rows: UserUsage[]): UserUsage[] {
  return [...rows].sort((a, b) => b.cost - a.cost || b.requests - a.requests || a.label.localeCompare(b.label, 'fr'))
}

function BudgetCell({ budget, label, currency }: { budget: UsageBudget | null; label: string; currency: string }) {
  if (!budget) return <span className="text-faint">Aucun</span>
  const ratio = budgetRatio(budget)
  return (
    <div className="w-full min-w-[9rem]">
      <span className={cn('whitespace-nowrap tabular-nums', ratio !== null && ratio >= 1 && 'font-medium text-danger')}>
        {budget.spent !== null ? formatCost(budget.spent, currency) : '—'}
        <span className="text-slate"> / {budget.limit !== null ? formatCost(budget.limit, currency) : '—'}</span>
      </span>
      <BudgetBar budget={budget} label={`Part du budget de ${label} consommée`} className="my-1" />
      <span className="block text-xs text-slate">
        {budgetPeriodLabel(budget)}
        {!budget.managed && <span className="block text-faint">défini dans la passerelle LLM</span>}
      </span>
    </div>
  )
}

function UserTable({ report }: { report: AdminUsageReport }) {
  // Budgets changed from this page, shown without reloading the report (dropped when it reloads).
  const [changed, setChanged] = useState<{ report: AdminUsageReport; budgets: Map<string, UsageBudget | null> }>({
    report,
    budgets: new Map(),
  })
  const [editing, setEditing] = useState<BudgetTarget | null>(null)
  const budgets = changed.report === report ? changed.budgets : null
  const rows = byCost(report.by_user).map((u) =>
    u.user_id && budgets?.has(u.user_id) ? { ...u, budget: budgets.get(u.user_id) ?? null } : u,
  )
  const total = report.totals.cost
  const withBudgets = report.budgets_available
  const exportCsv = () => {
    const { from, to } = report.period
    downloadBlob(usersCsv(rows, total, report.currency, withBudgets), `contexthub-consommation-utilisateurs-${from}-${to}.csv`)
  }

  return (
    <section aria-labelledby="admin-usage-users-title">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 id="admin-usage-users-title" className="font-semibold">
          Par utilisateur
        </h2>
        {rows.length > 0 && (
          <button type="button" className="btn-secondary px-2.5 py-1.5 text-xs" onClick={exportCsv}>
            <Download size={14} aria-hidden="true" />
            Exporter en CSV
          </button>
        )}
      </div>
      {!withBudgets && !report.missing.includes('by_user') && (
        <p className="mb-2 text-sm text-slate">
          Les budgets individuels ne peuvent pas être gérés depuis ContextHub avec la configuration actuelle de la passerelle LLM.
        </p>
      )}
      {withBudgets && report.missing.includes('budgets') && (
        <p className="mb-2 text-xs text-faint">
          Budgets partiellement lus : la passerelle LLM n'a pas fourni tous les budgets, une ligne marquée « Aucun » peut en avoir un.
        </p>
      )}
      {report.missing.includes('by_user') ? (
        <NotProvided what="Ventilation par utilisateur" />
      ) : rows.length === 0 ? (
        <p className="text-sm text-slate">Aucun utilisateur n'a consommé sur cette période.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-rule bg-surface">
          <table className="w-full border-collapse">
            <caption className="sr-only">Consommation par utilisateur, du plus coûteux au moins coûteux</caption>
            <thead>
              <tr>
                <th scope="col" className="table-head min-w-[11rem]">
                  Utilisateur
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right">
                  Requêtes
                </th>
                {/* Below 1280 px the two token columns are merged to leave room for the budget. */}
                <th scope="col" className="table-head whitespace-nowrap text-right xl:hidden">
                  Jetons <span className="font-normal">(entrée / sortie)</span>
                </th>
                <th scope="col" className="table-head hidden whitespace-nowrap text-right xl:table-cell">
                  Jetons d'entrée
                </th>
                <th scope="col" className="table-head hidden whitespace-nowrap text-right xl:table-cell">
                  Jetons de sortie
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right" aria-sort="descending">
                  Coût
                </th>
                {/* Below 1280 px the share is shown under the cost. */}
                <th scope="col" className="table-head hidden whitespace-nowrap text-right xl:table-cell">
                  Part du total
                </th>
                {withBudgets && (
                  <th scope="col" className="table-head">
                    Budget
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((u, i) => (
                <tr key={u.user_id ?? `external-${i}`}>
                  <th
                    scope="row"
                    className={cn('table-cell text-left font-normal', !u.known && 'italic text-slate')}
                    title={!u.known && u.user_id ? `Identifiant : ${u.user_id}` : undefined}
                  >
                    {u.label}
                    {u.email && <span className="block break-all text-xs not-italic text-slate">{u.email}</span>}
                  </th>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(u.requests)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums xl:hidden">
                    {formatCompact(u.input_tokens)}
                    <span className="text-slate"> / {formatCompact(u.output_tokens)}</span>
                  </td>
                  <td className="table-cell hidden whitespace-nowrap text-right tabular-nums xl:table-cell">{formatInteger(u.input_tokens)}</td>
                  <td className="table-cell hidden whitespace-nowrap text-right tabular-nums xl:table-cell">{formatInteger(u.output_tokens)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">
                    {formatCost(u.cost, report.currency)}
                    <span className="block text-xs text-slate xl:hidden">{formatShare(u.cost, total)} du total</span>
                  </td>
                  <td className="table-cell hidden whitespace-nowrap text-right tabular-nums text-slate xl:table-cell">
                    {formatShare(u.cost, total)}
                  </td>
                  {withBudgets && (
                    <td className="table-cell">
                      {u.known && u.user_id ? (
                        <div className="flex flex-col items-start gap-1">
                          <BudgetCell budget={u.budget} label={u.label} currency={report.currency} />
                          <button
                            type="button"
                            className="btn-ghost -ml-2 whitespace-nowrap px-2 py-1 text-xs"
                            onClick={() => setEditing({ id: u.user_id as string, label: u.label })}
                            aria-label={`${u.budget ? 'Modifier le budget' : 'Définir un budget'} de ${u.label}`}
                          >
                            <Wallet size={14} aria-hidden="true" />
                            {u.budget ? 'Modifier' : 'Définir'}
                          </button>
                        </div>
                      ) : (
                        <span className="text-faint">—</span>
                      )}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {withBudgets && (
        <p className="mt-2 text-xs text-faint">
          Pour un utilisateur absent de ce tableau (aucune consommation sur la période), utilisez l'action « Budget » de la page
          Utilisateurs.
        </p>
      )}

      <BudgetDialog
        target={editing}
        currency={report.currency}
        onClose={() => setEditing(null)}
        onChanged={(userId, budget) =>
          setChanged((current) => {
            const next = new Map(current.report === report ? current.budgets : [])
            next.set(userId, budget)
            return { report, budgets: next }
          })
        }
      />
    </section>
  )
}

/** Administration › Consommation: every user's consumption, read from the LLM gateway. */
export function Usage() {
  const [preset, setPreset] = useState<PeriodPreset>('month')
  const [period, setPeriod] = useState<Period>(() => presetPeriod('month'))
  const { state, reload, refreshing } = useUsageReport(usage.admin, period)

  return (
    <section className="max-w-6xl">
      <div className={cn('mb-6', state.status === 'unavailable' && 'hidden')}>
        <PeriodPicker
          preset={preset}
          period={period}
          onChange={(nextPreset, nextPeriod) => {
            setPreset(nextPreset)
            setPeriod(nextPeriod)
          }}
        />
      </div>

      {state.status !== 'ready' ? (
        <UsageStatus
          state={state}
          reload={reload}
          unavailableFallback={
            <>
              Le suivi de la consommation n'est pas activé : il nécessite une passerelle LLM (par exemple LiteLLM),
              déclarée par <code>LLM_GATEWAY_URL</code>. Voir le guide d'installation, section « Passerelle LLM ».
            </>
          }
        />
      ) : (
        <div className={cn('space-y-8 transition-opacity', refreshing && 'opacity-60')} aria-busy={refreshing}>
          <p className="-mt-2 text-sm text-slate">
            Consommation de l'ensemble des utilisateurs, {formatPeriod(state.report.period)}.
          </p>

          {isEmptyReport(state.report) ? (
            <Notice>Aucune consommation enregistrée sur cette période.</Notice>
          ) : (
            <>
              <UsageKpis totals={state.report.totals} currency={state.report.currency} />

              {state.report.missing.includes('daily') ? (
                <NotProvided what="Évolution par jour" />
              ) : (
                <UsageChart report={state.report} />
              )}

              <UserTable report={state.report} />

              <section aria-labelledby="admin-usage-models-title">
                <h2 id="admin-usage-models-title" className="mb-2 font-semibold">
                  Par modèle
                </h2>
                {state.report.missing.includes('by_model') || state.report.by_model.length === 0 ? (
                  <NotProvided what="Ventilation par modèle" />
                ) : (
                  <ModelTable rows={state.report.by_model} total={state.report.totals.cost} currency={state.report.currency} />
                )}
              </section>
            </>
          )}

          <p className="text-xs text-faint">
            Chiffres lus à la passerelle LLM au moment de l'affichage ; ContextHub ne stocke aucune donnée de coût. Les
            montants sont exprimés en {state.report.currency}.
            {state.report.budgets_available &&
              ' Les budgets individuels se définissent ici ou depuis la page Utilisateurs ; la passerelle LLM les applique et refuse les questions une fois le montant atteint.'}
          </p>
        </div>
      )}
    </section>
  )
}
