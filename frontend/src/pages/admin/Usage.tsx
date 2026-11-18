import { useState } from 'react'
import { Download } from 'lucide-react'
import { usage, type AdminUsageReport, type UserUsage } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  downloadBlob,
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
import {
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

function UserTable({ report }: { report: AdminUsageReport }) {
  const rows = byCost(report.by_user)
  const total = report.totals.cost
  const exportCsv = () => {
    const { from, to } = report.period
    downloadBlob(usersCsv(rows, total, report.currency), `contexthub-consommation-utilisateurs-${from}-${to}.csv`)
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
                <th scope="col" className="table-head">
                  Utilisateur
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
                <th scope="col" className="table-head whitespace-nowrap text-right" aria-sort="descending">
                  Coût
                </th>
                <th scope="col" className="table-head whitespace-nowrap text-right">
                  Part du total
                </th>
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
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(u.input_tokens)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatInteger(u.output_tokens)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums">{formatCost(u.cost, report.currency)}</td>
                  <td className="table-cell whitespace-nowrap text-right tabular-nums text-slate">{formatShare(u.cost, total)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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
            montants sont exprimés en {state.report.currency}. Les budgets individuels se définissent sur la passerelle.
          </p>
        </div>
      )}
    </section>
  )
}
