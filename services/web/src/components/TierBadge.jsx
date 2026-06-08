const TIER_CONFIG = {
  1: { label: 'T1', classes: 'bg-red-500/20 text-red-400 border-red-500/30' },
  2: { label: 'T2', classes: 'bg-amber-500/20 text-amber-400 border-amber-500/30' },
  3: { label: 'T3', classes: 'bg-slate-500/20 text-slate-400 border-slate-500/30' },
}

export default function TierBadge({ tier }) {
  const config = TIER_CONFIG[tier] || TIER_CONFIG[3]
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold border ${config.classes}`}>
      {config.label}
    </span>
  )
}