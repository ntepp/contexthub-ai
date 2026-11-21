import { useCallback, useEffect, useState } from 'react'
import * as RadixDialog from '@radix-ui/react-dialog'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { BookOpen, ChartColumn, LogOut, MessageSquarePlus, Settings2, ShieldCheck, Trash2, UserRound, X } from 'lucide-react'
import { api, MODELS_CHANGED, type ConversationSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, relativeDay } from '@/lib/utils'

// Lets the chat page refresh the list after a new conversation is created.
export const CONVERSATIONS_CHANGED = 'contexthub:conversations-changed'

/** The signed-in user's conversations, reloaded on CONVERSATIONS_CHANGED (sidebar and mobile header). */
export function useConversationList() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([])

  useEffect(() => {
    const load = () => api.conversations().then(setConversations).catch(() => {})
    load()
    window.addEventListener(CONVERSATIONS_CHANGED, load)
    return () => window.removeEventListener(CONVERSATIONS_CHANGED, load)
  }, [])

  const remove = useCallback(async (id: string) => {
    await api.deleteConversation(id)
    setConversations((list) => list.filter((c) => c.id !== id))
  }, [])

  return { conversations, remove }
}

export type ConversationList = ReturnType<typeof useConversationList>

function NavItem({ to, icon: Icon, children }: { to: string; icon: typeof BookOpen; children: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          'flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm text-slate hover:bg-wash hover:text-ink max-md:py-2.5',
          isActive && 'bg-wash font-medium text-ink',
        )
      }
    >
      <Icon size={16} className="shrink-0" />
      {children}
    </NavLink>
  )
}

/** Desktop and tablet (md and up): the fixed column on the left. */
export function Sidebar({ list }: { list: ConversationList }) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-rule bg-surface">
      <SidebarBody list={list} />
    </aside>
  )
}

/**
 * Phones (below md): the same navigation in an off-canvas drawer. A Radix dialog, opened by the
 * menu button of the mobile header (same Root): focus trap, Escape, click outside, scroll lock
 * and focus return come with it.
 */
export function NavigationDrawer({ list }: { list: ConversationList }) {
  return (
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="fixed inset-0 z-40 bg-ink/30 data-[state=closed]:animate-fade-out data-[state=open]:animate-fade-in" />
      <RadixDialog.Content
        aria-describedby={undefined}
        className="fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85vw] flex-col bg-surface pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)] shadow-xl data-[state=closed]:animate-drawer-out data-[state=open]:animate-drawer-in"
      >
        <RadixDialog.Title className="sr-only">Menu</RadixDialog.Title>
        <SidebarBody list={list} drawer />
      </RadixDialog.Content>
    </RadixDialog.Portal>
  )
}

function SidebarBody({ list, drawer = false }: { list: ConversationList; drawer?: boolean }) {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const conversationId = location.pathname.startsWith('/c/') ? location.pathname.slice(3) : undefined
  const { conversations, remove } = list
  // Consumption is only tracked behind an LLM gateway, whose models are "gateway:…". The model
  // list is shared with the chat page and requested once per session (`api.models`).
  const [gatewayMode, setGatewayMode] = useState(false)

  useEffect(() => {
    const load = () =>
      api
        .models()
        .then((data) => setGatewayMode(data.models.some((m) => m.provider === 'gateway')))
        .catch(() => {})
    load()
    // The administrator changed the offered models (Administration › Modèles).
    window.addEventListener(MODELS_CHANGED, load)
    return () => window.removeEventListener(MODELS_CHANGED, load)
  }, [])

  const removeConversation = async (id: string) => {
    await remove(id)
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
    <>
      <div className={cn('flex items-center gap-2.5 px-4 py-4', drawer && 'py-2 pr-2')}>
        <img src="/favicon.svg" alt="" className="h-7 w-7" />
        <span className="font-semibold tracking-tight">ContextHub</span>
        {drawer && (
          <RadixDialog.Close className="btn-ghost ml-auto h-10 w-10 p-0" aria-label="Fermer le menu">
            <X size={20} />
          </RadixDialog.Close>
        )}
      </div>

      <div className="px-3">
        <button className="btn-primary w-full justify-start" onClick={() => navigate('/')}>
          <MessageSquarePlus size={16} />
          Nouvelle question
        </button>
      </div>

      <nav className="mt-3 flex-1 overflow-y-auto overscroll-contain px-3" aria-label="Conversations">
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
                    'block truncate rounded-md py-1.5 pl-2.5 pr-8 text-sm text-slate hover:bg-wash hover:text-ink max-md:py-2.5 max-md:pr-11',
                    location.pathname === `/c/${c.id}` && 'bg-wash text-ink',
                  )}
                  title={c.title}
                >
                  {c.title}
                </NavLink>
                {/* No hover on touch screens: the delete button is always shown there. */}
                <button
                  onClick={() => removeConversation(c.id)}
                  className="absolute right-1 top-1/2 hidden -translate-y-1/2 rounded p-1 text-faint hover:text-danger group-hover:block group-focus-within:block max-md:right-0 max-md:block max-md:p-2.5"
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
        {gatewayMode && (
          <NavItem to="/consommation" icon={ChartColumn}>
            Ma consommation
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
            className="rounded p-1 text-faint hover:text-ink max-md:-mr-1.5 max-md:p-2.5"
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
    </>
  )
}
