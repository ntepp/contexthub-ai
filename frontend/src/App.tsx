import { Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './lib/auth'
import { AppLayout, RequireAdmin } from './components/layout/AppLayout'
import { Account } from './pages/Account'
import { Admin } from './pages/admin/Admin'
import { Chat } from './pages/Chat'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Login } from './pages/Login'

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
                <Admin />
              </RequireAdmin>
            }
          />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
