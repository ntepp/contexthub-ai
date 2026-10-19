import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './lib/auth'
import { AppLayout, RequireAdmin } from './components/layout/AppLayout'
import { Account } from './pages/Account'
import { Chat } from './pages/Chat'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Login } from './pages/Login'

// Administration is only for admins: keep it out of everybody else's initial bundle.
const Admin = lazy(() => import('./pages/admin/Admin').then((m) => ({ default: m.Admin })))

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<AppLayout />}>
          <Route index element={<Chat />} />
          <Route path="/c/:conversationId" element={<Chat />} />
          <Route path="/documents" element={<KnowledgeBase />} />
          <Route path="/compte" element={<Account />} />
          <Route
            path="/admin/*"
            element={
              <RequireAdmin>
                <Suspense fallback={<p className="p-8 text-sm text-slate">Chargement…</p>}>
                  <Admin />
                </Suspense>
              </RequireAdmin>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
