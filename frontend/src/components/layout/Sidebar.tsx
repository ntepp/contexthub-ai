import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import { BookOpen, MessageSquare, ChevronLeft, ChevronRight, Zap } from 'lucide-react'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/knowledge-base', icon: BookOpen, label: 'Knowledge Base' },
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
]

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)

  return (
    <aside
      className={cn(
        'relative flex flex-col bg-surface border-r border-border transition-all duration-300 ease-in-out shrink-0',
        collapsed ? 'w-16' : 'w-56',
      )}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-4 py-5 border-b border-border overflow-hidden">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-accent/10 text-accent shrink-0">
          <Zap size={16} />
        </div>
        {!collapsed && (
          <span className="text-sm font-semibold tracking-tight text-primary whitespace-nowrap">
            ContextHub
          </span>
        )}
      </div>

      {/* Nav */}
      <nav className="flex flex-col gap-1 p-2 flex-1">
        {NAV.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors',
                'text-secondary hover:text-primary hover:bg-muted/40',
                isActive && 'bg-accent/10 text-accent hover:bg-accent/15 hover:text-accent',
                collapsed && 'justify-center px-2',
              )
            }
          >
            <Icon size={18} className="shrink-0" />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      {/* Collapse toggle */}
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="absolute -right-3 top-6 z-10 flex items-center justify-center w-6 h-6 rounded-full bg-surface border border-border text-secondary hover:text-primary transition-colors"
      >
        {collapsed ? <ChevronRight size={12} /> : <ChevronLeft size={12} />}
      </button>
    </aside>
  )
}
