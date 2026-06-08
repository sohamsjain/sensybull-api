import { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export default function AuthModal({ onClose }) {
  const { login, register } = useAuth()
  const [mode, setMode] = useState('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (mode === 'login') {
        await login(email, password)
      } else {
        await register(name, email, password)
      }
      onClose()
    } catch (err) {
      setError(err.message)
    }
    setLoading(false)
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-slate-800 border border-slate-700 rounded-lg p-6 w-full max-w-sm"
        onClick={e => e.stopPropagation()}
      >
        {/* Tabs */}
        <div className="flex gap-4 mb-6">
          <button
            onClick={() => { setMode('login'); setError('') }}
            className={`text-sm font-semibold pb-1 border-b-2 transition-colors ${
              mode === 'login' ? 'border-blue-500 text-white' : 'border-transparent text-slate-400'
            }`}
          >
            Sign In
          </button>
          <button
            onClick={() => { setMode('register'); setError('') }}
            className={`text-sm font-semibold pb-1 border-b-2 transition-colors ${
              mode === 'register' ? 'border-blue-500 text-white' : 'border-transparent text-slate-400'
            }`}
          >
            Register
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          {mode === 'register' && (
            <input
              type="text"
              placeholder="Name"
              value={name}
              onChange={e => setName(e.target.value)}
              required
              className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-slate-400"
            />
          )}
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            required
            className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-slate-400"
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            minLength={6}
            className="w-full bg-slate-900 border border-slate-600 rounded px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-slate-400"
          />
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white py-2 rounded text-sm font-medium"
          >
            {loading ? '...' : mode === 'login' ? 'Sign In' : 'Create Account'}
          </button>
        </form>
      </div>
    </div>
  )
}