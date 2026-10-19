import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api, errorMessage, type SystemStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Notice } from '@/components/ui/Notice'

const CHECK_LABELS: Record<string, string> = {
  database: 'Base de données',
  redis: "File d'attente (Redis)",
  workers: "Workers d'indexation",
  ollama: 'Serveur de modèles local (Ollama)',
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-4 border-b border-rule py-2 text-sm last:border-0">
      <dt className="w-56 shrink-0 text-slate">{label}</dt>
      <dd className="min-w-0 flex-1">{children}</dd>
    </div>
  )
}

export function System() {
  const [status, setStatus] = useState<SystemStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = () => api.system().then(setStatus).catch((e) => setError(errorMessage(e)))
  useEffect(() => {
    load()
  }, [])

  if (error) return <Notice tone="error">{error}</Notice>
  if (!status) return <p className="text-sm text-slate">Chargement…</p>
  const c = status.configuration
  const cloudModels = c.models.filter((m) => !m.is_local)

  return (
    <section className="grid max-w-5xl gap-8 lg:grid-cols-2">
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-semibold">État des services</h2>
          <button className="btn-ghost px-2 py-1 text-xs" onClick={load}>
            <RefreshCw size={14} />
            Actualiser
          </button>
        </div>
        <dl className="rounded-lg border border-rule bg-surface px-4">
          {Object.entries(status.checks).map(([key, check]) => (
            <Row key={key} label={CHECK_LABELS[key] ?? key}>
              <span className={cn('font-medium', check.ok ? 'text-ledger' : 'text-danger')}>
                {check.ok ? 'Opérationnel' : `Indisponible${check.error ? ` (${check.error})` : ''}`}
              </span>
              {check.workers && check.workers.length > 0 && (
                <span className="block text-xs text-slate">{check.workers.join(', ')}</span>
              )}
              {check.models && <span className="block text-xs text-slate">{check.models.join(', ')}</span>}
            </Row>
          ))}
        </dl>

        <h2 className="mb-2 mt-8 font-semibold">Volumétrie</h2>
        <dl className="rounded-lg border border-rule bg-surface px-4">
          <Row label="Utilisateurs">{status.counts.users}</Row>
          <Row label="Collections">{status.counts.collections}</Row>
          <Row label="Documents">{status.counts.documents}</Row>
          <Row label="Messages">{status.counts.messages}</Row>
        </dl>
        <p className="mt-3 text-xs text-faint">
          Version {status.version}, environnement {status.environment}.
        </p>
      </div>

      <div>
        <h2 className="mb-2 font-semibold">Configuration et flux de données</h2>
        {cloudModels.length === 0 && !c.external_tracing ? (
          <Notice tone="success" className="mb-3">
            Mode 100 % local : aucun contenu de document ne quitte votre infrastructure.
          </Notice>
        ) : (
          <Notice tone="warning" className="mb-3">
            {cloudModels.length > 0 &&
              `Modèles cloud autorisés : ${cloudModels.map((m) => `${m.name} (${m.provider_label})`).join(', ')}. Les extraits utiles des documents leur sont transmis quand un utilisateur les choisit. `}
            {c.external_tracing && 'Le traçage LangSmith est activé : les prompts sont envoyés à LangSmith.'}
          </Notice>
        )}
        <dl className="rounded-lg border border-rule bg-surface px-4">
          <Row label="Modèles autorisés">
            {c.models.map((m) => (
              <span key={m.id} className="block">
                {m.name} <span className="text-slate">({m.is_local ? 'local' : m.provider_label})</span>
              </span>
            ))}
          </Row>
          <Row label="Embeddings">
            {c.embedding.model} <span className="text-slate">({c.embedding.provider})</span>
          </Row>
          <Row label="Recherche">
            {c.retrieval.hybrid ? 'Hybride (sémantique et plein texte)' : 'Sémantique'}, {c.retrieval.top_k} passages
            retenus, distance maximale {c.retrieval.max_distance}
            {c.retrieval.reranker !== 'none' && ', re-classement activé'}
          </Row>
          <Row label="OCR">
            {c.ocr.enabled ? (c.ocr.available ? 'Activé' : 'Activé mais Tesseract introuvable') : 'Désactivé'}
          </Row>
          <Row label="Import de pages web">{c.url_ingestion ? 'Autorisé' : 'Désactivé'}</Row>
          <Row label="SSO">{c.sso ? 'Configuré' : 'Non configuré'}</Row>
          <Row label="Taille maximale d'import">{c.upload_max_mb} Mo</Row>
          <Row label="Conservation">
            Journal d'audit : {c.retention_days.audit ? `${c.retention_days.audit} jours` : 'illimitée'}
            <span className="block">
              Conversations : {c.retention_days.conversations ? `${c.retention_days.conversations} jours` : 'illimitée'}
            </span>
          </Row>
        </dl>
      </div>
    </section>
  )
}
