import { useState, useEffect, useRef } from 'react'
import { useAuth } from '../context/AuthContext'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:5000'

const SIGNIFICANCE_LEVELS = ['High', 'Medium', 'Low']

const SIGNIFICANCE_ACTIVE = {
  High:   'bg-red-500/20 text-red-400',
  Medium: 'bg-amber-500/20 text-amber-400',
  Low:    'bg-slate-500/20 text-slate-400',
}

export default function TopBar({ significanceFilter, onSignificanceToggle, eventTypeFilter, onEventTypeToggle, onEventTypeClear, search, onSearchChange, onAuthClick }) {
  const { user, logout } = useAuth()
  const [eventTypes, setEventTypes] = useState([])
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef(null)

  useEffect(() => {
    fetch(`${API_URL}/events/types`)
      .then(res => res.json())
      .then(data => setEventTypes(data.event_types || []))
      .catch(() => {})
  }, [])

  // Close dropdown on outside click
  useEffect(() => {
    if (!dropdownOpen) return
    const handleClick = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [dropdownOpen])

  const activeCount = eventTypeFilter.size

  return (
    <header className="h-14 border-b border-slate-700 flex items-center px-4 gap-4 shrink-0">
      <h1 className="text-white font-bold text-lg mr-4">Sensybull</h1>

      {/* Significance filter toggles */}
      <div className="flex gap-1">
        {SIGNIFICANCE_LEVELS.map(level => (
          <button
            key={level}
            onClick={() => onSignificanceToggle(level)}
            className={`px-2.5 py-1 rounded text-xs font-semibold transition-colors ${
              significanceFilter.has(level) ? SIGNIFICANCE_ACTIVE[level] : 'bg-slate-800 text-slate-600'
            }`}
          >
            {level === 'Medium' ? 'Med' : level}
          </button>
        ))}
      </div>

      {/* Event type filter dropdown */}
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setDropdownOpen(prev => !prev)}
          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors flex items-center gap-1.5 ${
            activeCount > 0
              ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
              : 'bg-slate-800 text-slate-400 border border-slate-700 hover:border-slate-500'
          }`}
        >
          Event Types
          {activeCount > 0 && (
            <span className="bg-blue-500 text-white rounded-full w-4.5 h-4.5 flex items-center justify-center text-[10px] leading-none px-1">
              {activeCount}
            </span>
          )}
        </button>

        {dropdownOpen && (
          <div className="absolute top-full left-0 mt-1 w-64 bg-slate-800 border border-slate-700 rounded-lg shadow-xl z-50 overflow-hidden">
            {activeCount > 0 && (
              <div className="px-3 py-2 border-b border-slate-700">
                <button
                  onClick={onEventTypeClear}
                  className="text-xs text-slate-400 hover:text-slate-200"
                >
                  Clear all ({activeCount})
                </button>
              </div>
            )}
            <div className="max-h-72 overflow-y-auto py-1">
              {eventTypes.map(type => (
                <label
                  key={type}
                  className="flex items-center gap-2 px-3 py-1.5 hover:bg-slate-700/50 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={eventTypeFilter.has(type)}
                    onChange={() => onEventTypeToggle(type)}
                    className="rounded border-slate-600 bg-slate-900 text-blue-500 focus:ring-0 focus:ring-offset-0 w-3.5 h-3.5"
                  />
                  <span className="text-xs text-slate-300">{type}</span>
                </label>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Search */}
      <input
        type="text"
        placeholder="Search ticker or company..."
        value={search}
        onChange={e => onSearchChange(e.target.value)}
        className="flex-1 max-w-xs bg-slate-800 border border-slate-700 rounded px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-slate-500"
      />

      {/* Auth */}
      <div className="ml-auto flex items-center gap-3">
        {user ? (
          <>
            <span className="text-slate-400 text-sm">{user.name}</span>
            <button onClick={logout} className="text-slate-500 hover:text-slate-300 text-sm">
              Logout
            </button>
          </>
        ) : (
          <button
            onClick={onAuthClick}
            className="bg-blue-600 hover:bg-blue-500 text-white px-3 py-1.5 rounded text-sm font-medium"
          >
            Sign In
          </button>
        )}
      </div>
    </header>
  )
}
