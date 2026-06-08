import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { api, setTokens, clearTokens, getTokens } from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)

  const fetchUser = useCallback(async () => {
    const { access } = getTokens()
    if (!access) {
      setLoading(false)
      return
    }
    try {
      const res = await api('/auth/me')
      if (res.ok) {
        const data = await res.json()
        setUser(data.user)
      } else {
        clearTokens()
      }
    } catch {
      clearTokens()
    }
    setLoading(false)
  }, [])

  useEffect(() => { fetchUser() }, [fetchUser])

  const login = async (email, password) => {
    const res = await api('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || 'Login failed')
    setTokens(data.access_token, data.refresh_token)
    setUser(data.user)
  }

  const register = async (name, email, password) => {
    const res = await api('/auth/register', {
      method: 'POST',
      body: JSON.stringify({ name, email, password }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.error || 'Registration failed')
    setTokens(data.access_token, data.refresh_token)
    setUser(data.user)
  }

  const logout = () => {
    clearTokens()
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}