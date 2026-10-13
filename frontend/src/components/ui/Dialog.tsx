import * as RadixDialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { cn } from '@/lib/utils'

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  wide?: boolean
}

export function Dialog({ open, onOpenChange, title, description, children, footer, wide }: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-ink/30" />
        <RadixDialog.Content
          className={cn(
            'fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-lg border border-rule bg-surface p-6 shadow-xl',
            wide ? 'max-w-5xl' : 'max-w-md',
          )}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <RadixDialog.Title className="text-base font-semibold">{title}</RadixDialog.Title>
              {description && (
                <RadixDialog.Description className="mt-1 text-sm text-slate">{description}</RadixDialog.Description>
              )}
            </div>
            <RadixDialog.Close className="btn-ghost -mr-2 -mt-1 p-1.5" aria-label="Fermer">
              <X size={16} />
            </RadixDialog.Close>
          </div>
          {children}
          {footer && <div className="mt-6 flex justify-end gap-2">{footer}</div>}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  )
}

interface ConfirmProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: ReactNode
  confirmLabel: string
  onConfirm: () => Promise<unknown> | void
  danger?: boolean
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, onConfirm, danger }: ConfirmProps) {
  const [busy, setBusy] = useState(false)
  const run = async () => {
    setBusy(true)
    try {
      await onConfirm()
      onOpenChange(false)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={description}
      footer={
        <>
          <button className="btn-secondary" onClick={() => onOpenChange(false)} disabled={busy}>
            Annuler
          </button>
          <button className={danger ? 'btn-danger' : 'btn-primary'} onClick={run} disabled={busy}>
            {confirmLabel}
          </button>
        </>
      }
    />
  )
}
