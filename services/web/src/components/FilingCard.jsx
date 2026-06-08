import { useState } from 'react'

function timeAgo(dateStr) {
  if (!dateStr) return ''
  const seconds = Math.floor((Date.now() - new Date(dateStr).getTime()) / 1000)
  if (seconds < 0) return 'just now'
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

function formatCatalystDate(dateStr) {
  if (!dateStr) return '  TBD'
  try {
    const d = new Date(dateStr + 'T00:00:00')
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch {
    return dateStr
  }
}

const SIGNIFICANCE_CONFIG = {
  High:   { label: 'HIGH', badge: 'bg-red-500/20 text-red-400 border-red-500/30', border: 'border-red-500/30' },
  Medium: { label: 'MED',  badge: 'bg-amber-500/20 text-amber-400 border-amber-500/30', border: 'border-amber-500/30' },
  Low:    { label: 'LOW',  badge: 'bg-slate-500/20 text-slate-400 border-slate-500/30', border: 'border-slate-700' },
}

const SENTIMENT_CONFIG = {
  Positive: { color: 'bg-green-400', title: 'Positive' },
  Negative: { color: 'bg-red-400', title: 'Negative' },
  Neutral:  { color: 'bg-slate-500', title: 'Neutral' },
  Mixed:    { color: 'bg-amber-400', title: 'Mixed' },
}

export default function FilingCard({ event }) {
  const { ticker, company_name, briefing, items, exhibits, filing_date, edgar_url, received_at, event_types, catalysts: eventCatalysts } = event

  const significance = briefing?.significance || 'Medium'
  const sigConfig = SIGNIFICANCE_CONFIG[significance] || SIGNIFICANCE_CONFIG.Medium
  const sentiment = briefing?.sentiment || 'Neutral'
  const sentConfig = SENTIMENT_CONFIG[sentiment] || SENTIMENT_CONFIG.Neutral
  const isLow = significance === 'Low'

  // Low-significance cards start collapsed
  const [expanded, setExpanded] = useState(false)

  // Merge catalysts from briefing and from persisted DB rows
  const catalysts = eventCatalysts?.length > 0
    ? eventCatalysts
    : briefing?.catalysts || []

  return (
    <div
      className={`bg-slate-800 rounded-lg border ${sigConfig.border} p-4 cursor-pointer transition-colors hover:bg-slate-800/80 ${isLow ? 'opacity-60' : ''}`}
      onClick={() => setExpanded(e => !e)}
    >
      {/* Header row */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border ${sigConfig.badge}`}>
            {sigConfig.label}
          </span>
          {ticker && <span className="font-mono font-bold text-white">{ticker}</span>}
          <span className="text-slate-400 text-sm truncate">{company_name}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0 ml-2">
          <span className={`w-2 h-2 rounded-full ${sentConfig.color}`} title={sentConfig.title} />
          <span className="text-slate-500 text-xs whitespace-nowrap">
            {timeAgo(received_at || filing_date)}
          </span>
        </div>
      </div>

      {/* Briefing */}
      {briefing && (
        <div className="mb-2">
          <div className="flex items-center gap-2 mb-1">
            <p className="text-slate-200 text-sm font-medium flex-1 min-w-0">{briefing.headline}</p>
            {briefing.primary_event_type && briefing.primary_event_type !== 'Other' && (
              <span className="shrink-0 px-2 py-0.5 bg-blue-500/20 text-blue-300 rounded text-xs font-semibold uppercase tracking-wide">
                {briefing.primary_event_type}
              </span>
            )}
          </div>

          {/* Investor takeaway — the "so what" */}
          {briefing.investor_takeaway && (
            <p className="text-slate-200 text-sm italic mt-1">{briefing.investor_takeaway}</p>
          )}

          {/* Summary (hidden on Low significance unless expanded) */}
          {briefing.summary && (!isLow || expanded) && (
            <p className="text-slate-400 text-sm leading-relaxed mt-1">{briefing.summary}</p>
          )}

          {/* Deal terms */}
          {briefing.deal_terms && Object.keys(briefing.deal_terms).length > 0 && (
            <div className="mt-2 pt-2 border-t border-slate-700/50">
              <p className="text-slate-500 text-xs uppercase tracking-wide font-semibold mb-1">Deal Terms</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
                {Object.entries(briefing.deal_terms).map(([key, value]) => (
                  <div key={key} className="flex gap-2 text-xs">
                    <span className="text-slate-500 uppercase">{key.replace(/_/g, ' ')}</span>
                    <span className="text-slate-300 font-medium">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Catalysts */}
          {catalysts.length > 0 && (
            <div className="mt-2 pt-2 border-t border-slate-700/50">
              <p className="text-slate-500 text-xs uppercase tracking-wide font-semibold mb-1">Catalysts</p>
              <div className="space-y-0.5">
                {catalysts.map((cat, i) => (
                  <div key={i} className="flex gap-2 text-xs">
                    <span className="text-slate-500 font-mono w-16 shrink-0">{formatCatalystDate(cat.date)}</span>
                    <span className="text-slate-300">{cat.event}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Secondary event type tags (skip primary, already shown above) */}
      {event_types?.filter(t => t !== briefing?.primary_event_type).length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1">
          {event_types.filter(t => t !== briefing?.primary_event_type).map((type, i) => (
            <span key={i} className="px-1.5 py-0.5 bg-blue-500/10 text-blue-400/70 rounded text-xs">
              {type}
            </span>
          ))}
        </div>
      )}

      {/* Category tags */}
      {items?.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {items.map((item, i) => (
            <span key={i} className="px-1.5 py-0.5 bg-slate-700 text-slate-400 rounded text-xs">
              {item.category}
            </span>
          ))}
        </div>
      )}

      {/* Expanded details */}
      {expanded && (
        <div className="mt-3 pt-3 border-t border-slate-700" onClick={e => e.stopPropagation()}>
          {items?.map((item, i) => (
            <div key={i} className="mb-3">
              <div className="flex items-center gap-2 mb-1">
                <span className="text-slate-300 text-sm font-medium">
                  Item {item.number}: {item.title}
                </span>
              </div>
              {item.text && (
                <p className="text-slate-400 text-xs whitespace-pre-wrap max-h-40 overflow-y-auto leading-relaxed">
                  {item.text.slice(0, 2000)}
                </p>
              )}
            </div>
          ))}

          {exhibits?.length > 0 && (
            <div className="mt-2">
              <p className="text-slate-500 text-xs uppercase tracking-wide mb-1">Exhibits</p>
              {exhibits.map((ex, i) => (
                <a
                  key={i}
                  href={ex.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="block text-blue-400 hover:text-blue-300 text-xs mb-0.5"
                >
                  {ex.type} &mdash; {ex.description}
                </a>
              ))}
            </div>
          )}

          {edgar_url && (
            <a
              href={edgar_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-block mt-3 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded text-xs font-semibold uppercase tracking-wide transition-colors"
            >
              Read SEC Filing &rarr;
            </a>
          )}
        </div>
      )}
    </div>
  )
}
