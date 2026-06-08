import { useState, useEffect, useCallback } from 'react'
import { api } from '../api/client'
import { useAuth } from '../context/AuthContext'

export default function Sidebar({ selectedWatchlist, onSelectWatchlist }) {
  const { user } = useAuth()
  const [watchlists, setWatchlists] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [newName, setNewName] = useState('')
  const [managingWlId, setManagingWlId] = useState(null)

  const fetchWatchlists = useCallback(() => {
    if (!user) return
    api('/watchlists/')
      .then(res => res.json())
      .then(data => setWatchlists(data.watchlists || []))
      .catch(() => {})
  }, [user])

  useEffect(() => { fetchWatchlists() }, [fetchWatchlists])

  // Derive the managed watchlist from the current watchlists array so it stays fresh
  const managingWl = watchlists.find(wl => wl.id === managingWlId) || null

  const createWatchlist = async (e) => {
    e.preventDefault()
    if (!newName.trim()) return
    const res = await api('/watchlists/', {
      method: 'POST',
      body: JSON.stringify({ name: newName.trim() }),
    })
    if (res.ok) {
      const data = await res.json()
      setNewName('')
      setShowCreate(false)
      fetchWatchlists()
      // Open the manager immediately so the user can add companies
      if (data.watchlist?.id) setManagingWlId(data.watchlist.id)
    }
  }

  const deleteWatchlist = async (id) => {
    await api(`/watchlists/${id}`, { method: 'DELETE' })
    if (selectedWatchlist?.id === id) onSelectWatchlist(null)
    if (managingWlId === id) setManagingWlId(null)
    fetchWatchlists()
  }

  if (!user) return null

  return (
    <aside className="w-56 border-r border-slate-700 p-4 shrink-0 overflow-y-auto flex flex-col">
      <h2 className="text-slate-500 text-xs uppercase tracking-wide mb-3 font-medium">
        Watchlists
      </h2>

      <button
        onClick={() => onSelectWatchlist(null)}
        className={`w-full text-left px-3 py-2 rounded text-sm mb-1 transition-colors ${
          !selectedWatchlist ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'
        }`}
      >
        All Events
      </button>

      {watchlists.map(wl => (
        <div key={wl.id} className="group flex items-center mb-1">
          <button
            onClick={() => onSelectWatchlist(wl)}
            className={`flex-1 text-left px-3 py-2 rounded text-sm transition-colors ${
              selectedWatchlist?.id === wl.id ? 'bg-slate-700 text-white' : 'text-slate-400 hover:bg-slate-800'
            }`}
          >
            {wl.name}
            {wl.companies?.length > 0 && (
              <span className="text-slate-600 text-xs ml-1">({wl.companies.length})</span>
            )}
          </button>
          <button
            onClick={() => setManagingWlId(managingWlId === wl.id ? null : wl.id)}
            className="text-slate-600 hover:text-slate-300 px-1 text-xs opacity-0 group-hover:opacity-100"
            title="Manage"
          >
            ...
          </button>
        </div>
      ))}

      {/* Manage panel */}
      {managingWl && (
        <WatchlistManager
          watchlist={managingWl}
          onClose={() => setManagingWlId(null)}
          onRefresh={fetchWatchlists}
          onDelete={deleteWatchlist}
        />
      )}

      {/* Create form */}
      {showCreate ? (
        <form onSubmit={createWatchlist} className="mt-2">
          <input
            type="text"
            placeholder="Watchlist name"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            autoFocus
            className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1.5 text-sm text-white placeholder-slate-500 outline-none focus:border-slate-400"
          />
          <div className="flex gap-1 mt-1">
            <button type="submit" className="text-xs text-blue-400 hover:text-blue-300 px-2 py-1">Create</button>
            <button type="button" onClick={() => setShowCreate(false)} className="text-xs text-slate-500 px-2 py-1">Cancel</button>
          </div>
        </form>
      ) : (
        <button
          onClick={() => setShowCreate(true)}
          className="mt-2 text-slate-500 hover:text-slate-300 text-xs px-3 py-1"
        >
          + New Watchlist
        </button>
      )}
    </aside>
  )
}

function WatchlistManager({ watchlist, onClose, onRefresh, onDelete }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)

  const searchCompanies = async (q) => {
    if (!q.trim()) { setResults([]); return }
    setSearching(true)
    try {
      const res = await api(`/companies/?ticker=${encodeURIComponent(q.trim())}`)
      const data = await res.json()
      setResults(data.companies || [])
    } catch {}
    setSearching(false)
  }

  useEffect(() => {
    const timer = setTimeout(() => searchCompanies(query), 300)
    return () => clearTimeout(timer)
  }, [query])

  const addCompany = async (companyId) => {
    const res = await api(`/watchlists/${watchlist.id}/companies`, {
      method: 'POST',
      body: JSON.stringify({ company_id: companyId }),
    })
    if (res.ok) onRefresh()
  }

  const removeCompany = async (companyId) => {
    await api(`/watchlists/${watchlist.id}/companies/${companyId}`, { method: 'DELETE' })
    onRefresh()
  }

  const existingIds = new Set(watchlist.companies?.map(c => c.id))

  return (
    <div className="mt-2 bg-slate-800 border border-slate-700 rounded p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-white font-medium">{watchlist.name}</span>
        <button onClick={onClose} className="text-slate-500 hover:text-slate-300 text-xs">x</button>
      </div>

      {/* Current companies */}
      {watchlist.companies?.length > 0 && (
        <div className="mb-2 space-y-1">
          {watchlist.companies.map(c => (
            <div key={c.id} className="flex items-center justify-between text-xs">
              <span className="text-slate-300">{c.ticker || c.name}</span>
              <button
                onClick={() => removeCompany(c.id)}
                className="text-red-400/60 hover:text-red-400"
              >
                remove
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Search companies */}
      <input
        type="text"
        placeholder="Search ticker..."
        value={query}
        onChange={e => setQuery(e.target.value)}
        className="w-full bg-slate-900 border border-slate-600 rounded px-2 py-1 text-xs text-white placeholder-slate-500 outline-none focus:border-slate-400"
      />
      {results.length > 0 && (
        <div className="mt-1 max-h-32 overflow-y-auto space-y-0.5">
          {results.filter(c => !existingIds.has(c.id)).map(c => (
            <button
              key={c.id}
              onClick={() => addCompany(c.id)}
              className="w-full text-left px-2 py-1 text-xs text-slate-300 hover:bg-slate-700 rounded"
            >
              {c.ticker} &mdash; {c.name}
            </button>
          ))}
        </div>
      )}
      {searching && <p className="text-slate-600 text-xs mt-1">Searching...</p>}

      <button
        onClick={() => { onDelete(watchlist.id); onClose() }}
        className="mt-2 text-red-400/60 hover:text-red-400 text-xs"
      >
        Delete watchlist
      </button>
    </div>
  )
}
