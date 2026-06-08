import { useState, useEffect, useRef } from 'react'
import { useAuth } from '../context/AuthContext'
import { connectSocket, disconnectSocket } from '../api/socket'
import { api, getTokens } from '../api/client'
import FilingCard from './FilingCard'

export default function Feed({ significanceFilter, eventTypeFilter, search, selectedWatchlist }) {
  const { user } = useAuth()
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [hasMore, setHasMore] = useState(false)
  const pageRef = useRef(1)

  // Fetch history from REST API
  useEffect(() => {
    setLoadingHistory(true)
    setEvents([])
    pageRef.current = 1

    // Logged-in users with a watchlist selected hit /events/, otherwise /events/all (public)
    const endpoint = (user && selectedWatchlist) ? '/events/' : '/events/all'
    api(`${endpoint}?page=1&per_page=50`)
      .then(res => res.json())
      .then(data => {
        setEvents(data.events || [])
        setHasMore((data.events?.length || 0) < data.total)
      })
      .catch(() => {})
      .finally(() => setLoadingHistory(false))
  }, [user, selectedWatchlist])

  const loadMore = async () => {
    if (loadingHistory) return
    setLoadingHistory(true)
    pageRef.current += 1
    const endpoint = (user && selectedWatchlist) ? '/events/' : '/events/all'
    try {
      const res = await api(`${endpoint}?page=${pageRef.current}&per_page=50`)
      const data = await res.json()
      setEvents(prev => [...prev, ...(data.events || [])])
      setHasMore(events.length + (data.events?.length || 0) < data.total)
    } catch {}
    setLoadingHistory(false)
  }

  // Connect to WebSocket for live events
  useEffect(() => {
    const { access } = getTokens()
    const socket = connectSocket(access)

    socket.on('connect', () => setConnected(true))
    socket.on('disconnect', () => setConnected(false))

    socket.on('filing_event', (event) => {
      setEvents(prev => {
        if (prev.some(e => e.edgar_id === event.edgar_id)) return prev
        return [event, ...prev]
      })
    })

    return () => disconnectSocket()
  }, [user])

  // Client-side filtering
  const filtered = events.filter(e => {
    const sig = e.briefing?.significance || 'Medium'
    if (!significanceFilter.has(sig)) return false
    if (eventTypeFilter.size > 0) {
      if (!e.event_types?.some(t => eventTypeFilter.has(t))) return false
    }
    if (search) {
      const q = search.toLowerCase()
      if (!e.ticker?.toLowerCase().includes(q) && !e.company_name?.toLowerCase().includes(q)) {
        return false
      }
    }
    if (selectedWatchlist) {
      const tickers = new Set(selectedWatchlist.companies?.map(c => c.ticker?.toUpperCase()))
      if (!tickers.has(e.ticker?.toUpperCase())) return false
    }
    return true
  })

  return (
    <div className="flex-1 overflow-y-auto p-4">
      <div className="flex items-center gap-2 mb-4">
        <div className={`w-2 h-2 rounded-full ${connected ? 'bg-green-500' : 'bg-red-500'}`} />
        <span className="text-slate-500 text-xs">
          {connected ? 'Live' : 'Connecting...'} &middot; {filtered.length} event{filtered.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="space-y-2 max-w-3xl">
        {filtered.map(event => (
          <FilingCard key={event.edgar_id || event.id} event={event} />
        ))}
      </div>

      {hasMore && (
        <button
          onClick={loadMore}
          disabled={loadingHistory}
          className="mt-4 mx-auto block text-slate-400 hover:text-slate-200 text-sm py-2 px-4"
        >
          {loadingHistory ? 'Loading...' : 'Load more'}
        </button>
      )}

      {filtered.length === 0 && events.length > 0 && (
        <p className="text-slate-500 text-sm text-center mt-12">No events match your filters.</p>
      )}
      {events.length === 0 && !loadingHistory && (
        <div className="text-center mt-16">
          <p className="text-slate-500 text-sm">No filing events yet.</p>
          <p className="text-slate-600 text-xs mt-1">New 8-K filings will appear here in real time.</p>
        </div>
      )}
      {loadingHistory && events.length === 0 && (
        <p className="text-slate-500 text-sm text-center mt-12">Loading events...</p>
      )}
    </div>
  )
}