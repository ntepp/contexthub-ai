import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, forgetModels, type Me } from './api'

interface AuthState {
  user: Me | null
  loading: boolean
  refresh: () => Promise<Me | null>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  // Session-wide caches (the model list) are dropped when the account changes or the session ends.
  const userId = useRef<string | null>(null)
  const endSession = useCallback(() => {
    userId.current = null
    forgetModels()
    setUser(null)
  }, [])

  const refresh = useCallback(async () => {
    try {
      const me = await api.me()
      if (me.id !== userId.current) {
        forgetModels()
        userId.current = me.id
      }
      setUser(me)
      return me
    } catch {
      endSession()
      return null
    } finally {
      setLoading(false)
    }
  }, [endSession])

  const logout = useCallback(async () => {
    try {
      await api.logout()
    } finally {
      endSession()
    }
  }, [endSession])

  useEffect(() => {
    refresh()
    const onUnauthorized = () => endSession()
    window.addEventListener('contexthub:unauthorized', onUnauthorized)
    return () => window.removeEventListener('contexthub:unauthorized', onUnauthorized)
  }, [refresh, endSession])

  return <AuthContext.Provider value={{ user, loading, refresh, logout }}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
