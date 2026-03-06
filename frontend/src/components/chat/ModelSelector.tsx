import { useEffect, useState } from 'react'
import { ChevronDown, Cpu } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getModels, type Model } from '@/lib/api'

interface Props {
  value: string
  onChange: (id: string) => void
}

export function ModelSelector({ value, onChange }: Props) {
  const [models, setModels] = useState<Model[]>([])
  const [open, setOpen] = useState(false)

  useEffect(() => {
    getModels().then(setModels).catch(() => {})
  }, [])

  const selected = models.find((m) => m.id === value)

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border bg-surface',
          'text-sm text-secondary hover:text-primary hover:border-muted transition-colors',
        )}
      >
        <Cpu size={14} className="text-accent" />
        <span>{selected?.name ?? 'Select model'}</span>
        <ChevronDown size={12} className={cn('transition-transform', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="absolute top-full mt-1 left-0 z-50 min-w-48 rounded-lg border border-border bg-surface shadow-xl animate-fade-in">
          {models.length === 0 ? (
            <p className="px-3 py-2 text-xs text-muted-fg">No models found</p>
          ) : (
            models.map((m) => (
              <button
                key={m.id}
                onClick={() => { onChange(m.id); setOpen(false) }}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 text-sm text-left',
                  'hover:bg-muted/40 transition-colors first:rounded-t-lg last:rounded-b-lg',
                  m.id === value ? 'text-accent' : 'text-secondary',
                )}
              >
                <span
                  className={cn(
                    'text-[10px] font-medium px-1.5 py-0.5 rounded',
                    m.provider === 'ollama' ? 'bg-accent/10 text-accent' : 'bg-accent-violet/10 text-accent-violet',
                  )}
                >
                  {m.provider}
                </span>
                {m.name}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  )
}
