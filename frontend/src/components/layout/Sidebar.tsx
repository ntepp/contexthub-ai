import { useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { BookOpen, LogOut, MessageSquarePlus, Settings2, ShieldCheck, Trash2, UserRound } from 'lucide-react'
import { api, type ConversationSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, relativeDay } from '@/lib/utils'

// Lets the chat page refresh the list after a new conversation is created.
export const CONVERSATIONS_CHANGED = 'contexthub:conversations-changed'

function NavItem({ to, icon: Icon, children }: { to: string; icon: typeof BookOpen; children: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-slate hover:bg-wash hover:text-ink',
          isActive && 'bg-wash font-medium text-ink',
        )
      }
    >
      <Icon size={16} className="shrink-0" />
      {children}
    </NavLink>
  )
}

export function Sidebar() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const conversationId = location.pathname.startsWith('/c/') ? location.pathname.slice(3) : undefined
  const [conversations, setConversations] = useState<ConversationSummary[]>([])

  useEffect(() => {
    const load = () => api.conversations().then(setConversations).catch(() => {})
    load()
    window.addEventListener(CONVERSATIONS_CHANGED, load)
    return () => window.removeEventListener(CONVERSATIONS_CHANGED, load)
  }, [])

  const remove = async (id: string) => {
    await api.deleteConversation(id)
    setConversations((list) => list.filter((c) => c.id !== id))
    if (id === conversationId) navigate('/')
  }

  const groups: [string, ConversationSummary[]][] = []
  for (const c of conversations) {
    const label = relativeDay(c.updated_at)
    const last = groups[groups.length - 1]
    if (last && last[0] === label) last[1].push(c)
    else groups.push([label, [c]])
  }

  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-rule bg-surface">
      <div className="flex items-center gap-2.5 px-4 py-4">
        <img src="/favicon.svg" alt="" className="h-7 w-7" />
        <span className="font-semibold tracking-tight">ContextHub</span>
      </div>

      <div className="px-3">
        <button className="btn-primary w-full justify-start" onClick={() => navigate('/')}>
          <MessageSquarePlus size={16} />
          Nouvelle question
        </button>
      </div>

      <nav className="mt-3 flex-1 overflow-y-auto px-3" aria-label="Conversations">
        {groups.length === 0 && (
          <p className="px-2.5 py-2 text-sm text-faint">Vos conversations apparaîtront ici.</p>
        )}
        {groups.map(([label, items]) => (
          <section key={label} className="mb-3">
            <h2 className="px-2.5 pb-1 pt-2 text-xs text-faint">{label}</h2>
            {items.map((c) => (
              <div key={c.id} className="group relative">
                <NavLink
                  to={`/c/${c.id}`}
                  className={cn(
                    'block truncate rounded-md py-1.5 pl-2.5 pr-8 text-sm text-slate hover:bg-wash hover:text-ink',
                    location.pathname === `/c/${c.id}` && 'bg-wash text-ink',
                  )}
                  title={c.title}
                >
                  {c.title}
                </NavLink>
                <button
                  onClick={() => remove(c.id)}
                  className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded p-1 text-faint hover:text-danger group-hover:block group-focus-within:block"
                  aria-label={`Supprimer la conversation « ${c.title} »`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </section>
        ))}
      </nav>

      <div className="space-y-0.5 border-t border-rule px-3 py-3">
        <NavItem to="/documents" icon={BookOpen}>
          Documents
        </NavItem>
        {user?.role === 'admin' && (
          <NavItem to="/admin" icon={ShieldCheck}>
            Administration
          </NavItem>
        )}
        <NavItem to="/compte" icon={Settings2}>
          Mon compte
        </NavItem>
        <div className="mt-2 flex items-center gap-2 px-2.5 pt-2 text-sm">
          <UserRound size={16} className="shrink-0 text-faint" />
          <span className="min-w-0 flex-1 truncate text-slate" title={user?.email}>
            {user?.full_name || user?.email}
          </span>
          <button
            className="rounded p-1 text-faint hover:text-ink"
            onClick={async () => {
              await logout()
              navigate('/login')
            }}
            aria-label="Se déconnecter"
            title="Se déconnecter"
          >
            <LogOut size={16} />
          </button>
        </div>
      </div>
    </aside>
  )
}
