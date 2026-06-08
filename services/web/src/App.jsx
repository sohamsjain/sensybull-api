import { useState, useCallback } from 'react'
import { AuthProvider } from './context/AuthContext'
import TopBar from './components/TopBar'
import Sidebar from './components/Sidebar'
import Feed from './components/Feed'
import AuthModal from './components/AuthModal'

function AppInner() {
  const [significanceFilter, setSignificanceFilter] = useState(new Set(['High', 'Medium', 'Low']))
  const [eventTypeFilter, setEventTypeFilter] = useState(new Set())
  const [search, setSearch] = useState('')
  const [selectedWatchlist, setSelectedWatchlist] = useState(null)
  const [showAuth, setShowAuth] = useState(false)

  const handleSignificanceToggle = useCallback((level) => {
    setSignificanceFilter(prev => {
      const next = new Set(prev)
      if (next.has(level)) next.delete(level)
      else next.add(level)
      return next
    })
  }, [])

  const handleEventTypeToggle = useCallback((type) => {
    setEventTypeFilter(prev => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }, [])

  const handleEventTypeClear = useCallback(() => {
    setEventTypeFilter(new Set())
  }, [])

  return (
    <div className="h-screen flex flex-col bg-slate-900 text-slate-100">
      <TopBar
        significanceFilter={significanceFilter}
        onSignificanceToggle={handleSignificanceToggle}
        eventTypeFilter={eventTypeFilter}
        onEventTypeToggle={handleEventTypeToggle}
        onEventTypeClear={handleEventTypeClear}
        search={search}
        onSearchChange={setSearch}
        onAuthClick={() => setShowAuth(true)}
      />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar
          selectedWatchlist={selectedWatchlist}
          onSelectWatchlist={setSelectedWatchlist}
        />
        <Feed
          significanceFilter={significanceFilter}
          eventTypeFilter={eventTypeFilter}
          search={search}
          selectedWatchlist={selectedWatchlist}
        />
      </div>
      {showAuth && <AuthModal onClose={() => setShowAuth(false)} />}
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppInner />
    </AuthProvider>
  )
}
