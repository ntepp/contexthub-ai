import { useEffect, useState } from 'react'
import { CheckCircle, XCircle, Loader } from 'lucide-react'
import { cn } from '@/lib/utils'
import { getTaskStatus } from '@/lib/api'

interface Props {
  taskId: string
  filename: string
  onFinished?: () => void
}

type Status = 'PENDING' | 'STARTED' | 'SUCCESS' | 'FAILURE' | 'RETRY'

export function TaskStatus({ taskId, filename, onFinished }: Props) {
  const [status, setStatus] = useState<Status>('PENDING')

  useEffect(() => {
    if (status === 'SUCCESS' || status === 'FAILURE') return

    const interval = setInterval(async () => {
      try {
        const data = await getTaskStatus(taskId)
        const newStatus = data.status as Status
        setStatus(newStatus)
        if (newStatus === 'SUCCESS' || newStatus === 'FAILURE') {
          onFinished?.()
        }
      } catch {
        // backend unreachable, keep polling
      }
    }, 1500)

    return () => clearInterval(interval)
  }, [taskId, status, onFinished])

  const isTerminal = status === 'SUCCESS' || status === 'FAILURE'

  return (
    <div
      className={cn(
        'flex items-center gap-3 px-4 py-3 rounded-lg border transition-colors',
        status === 'SUCCESS' && 'border-green-800/50 bg-green-900/10',
        status === 'FAILURE' && 'border-red-800/50 bg-red-900/10',
        !isTerminal && 'border-border bg-surface',
      )}
    >
      {/* Icon */}
      <div className="shrink-0">
        {status === 'SUCCESS' && <CheckCircle size={16} className="text-green-400" />}
        {status === 'FAILURE' && <XCircle size={16} className="text-red-400" />}
        {!isTerminal && <Loader size={16} className="text-accent animate-spin" />}
      </div>

      {/* Info */}
      <div className="flex-1 min-w-0">
        <p className="text-sm text-primary truncate">{filename}</p>
        <p className="text-xs text-muted-fg mt-0.5">
          {status === 'SUCCESS' && 'Indexed successfully'}
          {status === 'FAILURE' && 'Indexing failed'}
          {status === 'PENDING' && 'Queued…'}
          {status === 'STARTED' && 'Indexing PDF…'}
          {status === 'RETRY' && 'Retrying…'}
        </p>
      </div>

      {/* Status badge */}
      <span
        className={cn(
          'text-[10px] font-medium px-2 py-0.5 rounded-full shrink-0',
          status === 'SUCCESS' && 'bg-green-900/30 text-green-400',
          status === 'FAILURE' && 'bg-red-900/30 text-red-400',
          !isTerminal && 'bg-accent/10 text-accent',
        )}
      >
        {status}
      </span>
    </div>
  )
}
