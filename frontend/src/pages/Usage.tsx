import { useState } from 'react'
import { usage } from '@/lib/api'
import { cn } from '@/lib/utils'
import { formatPeriod, presetPeriod, type Period, type PeriodPreset } from '@/lib/usage'
import { Notice, PageHeader } from '@/components/ui/Notice'
import {
  BudgetSummary,
  ModelTable,
  NotProvided,
  PeriodPicker,
  UsageChart,
  UsageKpis,
  UsageStatus,
  isEmptyReport,
  useUsageReport,
} from '@/components/usage/UsageParts'

/** "Ma consommation": the signed-in user's questions to the language models, read from the LLM gateway. */
export function Usage() {
  const [preset, setPreset] = useState<PeriodPreset>('month')
  const [period, setPeriod] = useState<Period>(() => presetPeriod('month'))
  const { state, reload, refreshing } = useUsageReport(usage.me, period)

  return (
    <div className="h-full overflow-y-auto px-4 py-8 sm:px-8">
      <div className="max-w-5xl">
        <PageHeader
          title="Ma consommation"
          description="Utilisation des modèles de langage par vos questions : coût, nombre de requêtes et volume de texte traité, en jetons."
        />

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
            unavailableFallback="Le suivi de la consommation n'est pas activé : il nécessite une passerelle LLM, que votre administrateur peut mettre en place."
          />
        ) : (
          <div className={cn('space-y-8 transition-opacity', refreshing && 'opacity-60')} aria-busy={refreshing}>
            <p className="-mt-2 text-sm text-slate">Période affichée : {formatPeriod(state.report.period)}.</p>

            <section aria-labelledby="usage-budget-title">
              <h2 id="usage-budget-title" className="mb-2 font-semibold">
                Budget
              </h2>
              {state.report.budget ? (
                <BudgetSummary budget={state.report.budget} currency={state.report.currency} />
              ) : (
                <p className="text-sm text-slate">Aucun budget individuel n'est défini.</p>
              )}
            </section>

            {isEmptyReport(state.report) ? (
              <Notice>Aucune consommation enregistrée sur cette période.</Notice>
            ) : (
              <>
                <section aria-labelledby="usage-totals-title">
                  <h2 id="usage-totals-title" className="mb-2 font-semibold">
                    Sur la période
                  </h2>
                  <UsageKpis totals={state.report.totals} currency={state.report.currency} />
                </section>

                {state.report.missing.includes('daily') ? (
                  <NotProvided what="Évolution par jour" />
                ) : (
                  <UsageChart report={state.report} />
                )}

                <section aria-labelledby="usage-models-title">
                  <h2 id="usage-models-title" className="mb-2 font-semibold">
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
              Chiffres fournis par la passerelle LLM de votre organisation ; ContextHub ne les conserve pas. Les montants
              sont exprimés en {state.report.currency}.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
