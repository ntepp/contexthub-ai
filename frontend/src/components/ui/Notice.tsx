import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

const TONES = {
  info: 'border-rule bg-wash text-ink',
  success: 'border-ledger/30 bg-ledger-soft text-ledger-dark',
  warning: 'border-refusal/30 bg-refusal-soft text-refusal',
  error: 'border-danger/30 bg-danger-soft text-danger',
}

export function Notice({
  tone = 'info',
  children,
  className,
}: {
  tone?: keyof typeof TONES
  children: ReactNode
  className?: string
}) {
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cn('rounded-md border px-3 py-2 text-sm', TONES[tone], className)}>
      {children}
    </div>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4 border-b border-rule pb-4">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-slate">{description}</p>}
      </div>
      {actions && <div className="flex gap-2">{actions}</div>}
    </header>
  )
}
