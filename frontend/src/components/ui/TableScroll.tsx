import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * Keeps a wide table from pushing the page sideways: it scrolls horizontally inside this box.
 * Lists that read better as cards on phones also take the `table-cards` class (index.css).
 */
export function TableScroll({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('max-w-full overflow-x-auto', className)}>{children}</div>
}
