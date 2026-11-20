import { useEffect, useState } from 'react'
import * as RadixDialog from '@radix-ui/react-dialog'
import { Link, Navigate, Outlet, useLocation } from 'react-router-dom'
import { Menu, MessageSquarePlus } from 'lucide-react'
import type { ConversationSummary } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { MD, useMediaQuery } from '@/lib/useMediaQuery'
import { NavigationDrawer, Sidebar, useConversationList } from './Sidebar'

export function AppLayout() {
  const { user, loading } = useAuth()
  const location = useLocation()

  if (loading) {
    return <div className="flex h-full items-center justify-center text-sm text-slate">Chargement…</div>
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return <SignedInLayout />
}

/** What the mobile header shows: the conversation title, or the page name. */
function pageTitle(pathname: string, conversations: ConversationSummary[]): string {
  if (pathname.startsWith('/c/')) return conversations.find((c) => c.id === pathname.slice(3))?.title ?? 'Conversation'
  if (pathname.startsWith('/documents')) return 'Documents'
  if (pathname.startsWith('/admin')) return 'Administration'
  if (pathname.startsWith('/consommation')) return 'Ma consommation'
  if (pathname.startsWith('/compte')) return 'Mon compte'
  return 'Nouvelle question'
}

function SignedInLayout() {
  const location = useLocation()
  const desktop = useMediaQuery(MD)
  const list = useConversationList()
  const [menuOpen, setMenuOpen] = useState(false)

  // The drawer closes after every navigation (links, "Nouvelle question", a deleted conversation)
  // and when the window grows wide enough for the sidebar.
  useEffect(() => setMenuOpen(false), [location.key, desktop])

  return (
    <RadixDialog.Root open={menuOpen && !desktop} onOpenChange={setMenuOpen}>
      <div className="flex h-full max-md:flex-col">
        {desktop ? (
          <Sidebar list={list} />
        ) : (
          <header className="shrink-0 border-b border-rule bg-surface pt-[env(safe-area-inset-top)]">
            <div className="flex h-12 items-center gap-1 px-1">
              <RadixDialog.Trigger className="btn-ghost h-10 w-10 shrink-0 p-0 text-ink" aria-label="Ouvrir le menu">
                <Menu size={20} />
              </RadixDialog.Trigger>
              <img src="/favicon.svg" alt="" className="h-6 w-6 shrink-0" />
              <p className="min-w-0 flex-1 truncate px-1.5 text-sm font-semibold">
                {pageTitle(location.pathname, list.conversations)}
              </p>
              <Link to="/" className="btn-ghost h-10 w-10 shrink-0 p-0" aria-label="Nouvelle question" title="Nouvelle question">
                <MessageSquarePlus size={20} />
              </Link>
            </div>
          </header>
        )}
        <main className="min-w-0 flex-1 overflow-hidden max-md:min-h-0">
          <Outlet />
        </main>
      </div>
      {!desktop && <NavigationDrawer list={list} />}
    </RadixDialog.Root>
  )
}

export function RequireAdmin({ children }: { children: JSX.Element }) {
  const { user } = useAuth()
  if (user?.role !== 'admin') return <Navigate to="/" replace />
  return children
}
