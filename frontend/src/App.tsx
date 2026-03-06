import { Navigate, Route, Routes } from 'react-router-dom'
import { AppLayout } from './components/layout/AppLayout'
import { KnowledgeBase } from './pages/KnowledgeBase'
import { Chat } from './pages/Chat'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/chat" replace />} />
        <Route path="/knowledge-base" element={<KnowledgeBase />} />
        <Route path="/chat" element={<Chat />} />
      </Route>
    </Routes>
  )
}
