import { useEffect, useState, type FormEvent } from 'react'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { api, errorMessage, type AuthConfig } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { Notice } from '@/components/ui/Notice'

const SSO_ERRORS: Record<string, string> = {
  sso: "La connexion par SSO a échoué. Réessayez, ou contactez votre administrateur si le problème persiste.",
  inactive: 'Votre compte est désactivé. Contactez votre administrateur.',
}

export function Login() {
  const { user, refresh } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [params] = useSearchParams()
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(SSO_ERRORS[params.get('error') ?? ''] ?? null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.authConfig().then(setConfig).catch(() => setError('Le serveur est injoignable.'))
  }, [])

  if (user) return <Navigate to={(location.state as { from?: string })?.from || '/'} replace />

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.login(email, password)
      await refresh()
      navigate('/', { replace: true })
    } catch (err) {
      setError(errorMessage(err, 'Connexion impossible.'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-full items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="h-9 w-9" />
          <div>
            <p className="text-lg font-semibold leading-tight">ContextHub</p>
            <p className="text-sm text-slate">Vos textes de référence, cités à la page près.</p>
          </div>
        </div>

        <div className="rounded-lg border border-rule bg-surface p-6">
          <h1 className="mb-5 text-base font-semibold">Connexion</h1>
          {error && (
            <Notice tone="error" className="mb-4">
              {error}
            </Notice>
          )}

          {config?.oidc_enabled && (
            <>
              <a className="btn-primary w-full" href="/api/auth/oidc/login">
                Se connecter avec {config.oidc_label}
              </a>
              {config.local_enabled && (
                <div className="my-5 flex items-center gap-3 text-xs text-faint">
                  <span className="h-px flex-1 bg-rule" />
                  ou avec un mot de passe
                  <span className="h-px flex-1 bg-rule" />
                </div>
              )}
            </>
          )}

          {(config?.local_enabled ?? true) && (
            <form onSubmit={submit} className="space-y-4">
              <div>
                <label htmlFor="email" className="label">
                  Adresse e-mail
                </label>
                <input
                  id="email"
                  type="email"
                  autoComplete="username"
                  required
                  className="input"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
              <div>
                <label htmlFor="password" className="label">
                  Mot de passe
                </label>
                <input
                  id="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  className="input"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
              <button type="submit" className={config?.oidc_enabled ? 'btn-secondary w-full' : 'btn-primary w-full'} disabled={busy}>
                {busy ? 'Connexion…' : 'Se connecter'}
              </button>
            </form>
          )}
        </div>
        {config && (
          <p className="mt-4 text-center text-xs text-faint">
            Version {config.version}
            {config.source_code_url && (
              <>
                {', '}
                <a className="underline hover:text-slate" href={config.source_code_url} target="_blank" rel="noreferrer">
                  code source sous licence AGPL-3.0
                </a>
              </>
            )}
          </p>
        )}
      </div>
    </div>
  )
}
