// src/components/EventFeed.tsx — full-width event feed with severity hierarchy
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNOCStore } from '../store/nocStore'
import { eventColor, eventIcon, severityColor } from '../lib/asData'
import { api } from '../api/client'

const STATUS_COLOR: Record<string, string> = {
  escalated: '#ff3b3b', open: '#ffaa00', resolved: '#00ee88',
}

// ── Full-width severity-tiered event card ────────────────────────────────────
function FeedCard({ e, isMS, onSelect, onTrace }:
  { e:any; isMS:boolean; onSelect:()=>void; onTrace?:()=>void }) {
  const ec   = eventColor(e.event_type)
  const sc   = STATUS_COLOR[e.status] ?? '#8fc4dc'
  const sev  = e.peak_severity ?? e.severity ?? 1
  const conf = e.peak_confidence ?? e.confidence ?? 0
  const name = e.origin_name
    ? `${e.origin_name}${e.origin_country ? ` · ${e.origin_country}` : ''}`
    : e.origin_asn ? `AS${e.origin_asn}` : '—'

  // S5 — critical full-width banner
  if (sev >= 5) return (
    <div onClick={onSelect} style={{
      background:'linear-gradient(135deg,#1a0505 0%,#0a0208 100%)',
      border:`1px solid ${ec}88`, borderLeft:`5px solid ${ec}`,
      borderRadius:5, padding:'14px 16px', marginBottom:8, cursor:'pointer',
      animation:'feedPulse 2s ease-in-out infinite',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:8 }}>
        <span style={{ fontSize:20 }}>{eventIcon(e.event_type)}</span>
        <span style={{ color:ec, fontSize:13, fontWeight:700, flex:1, letterSpacing:'.04em' }}>
          {e.event_type.replace(/_/g,' ').toUpperCase()}
        </span>
        {isMS && <span style={{ color:'#00ee88', fontSize:9, background:'#00ee8818',
          border:'1px solid #00ee8833', borderRadius:3, padding:'1px 6px' }}>✓✓ CONFIRMED</span>}
        <span style={{ color:sc, background:`${sc}22`, border:`1px solid ${sc}66`,
          borderRadius:3, fontSize:9, padding:'2px 8px', fontWeight:700 }}>
          {e.status?.toUpperCase()}
        </span>
      </div>
      <div style={{ display:'flex', gap:20, marginBottom:6, flexWrap:'wrap' as const }}>
        <div>
          <div style={{ color:'#887755', fontSize:8, marginBottom:1 }}>OPERATOR</div>
          <div style={{ color:'#fff', fontSize:11, fontWeight:600 }}>{name}</div>
        </div>
        {e.affected_prefix && (
          <div>
            <div style={{ color:'#887755', fontSize:8, marginBottom:1 }}>PREFIX</div>
            <div style={{ color:'#ffaaaa', fontSize:11, fontFamily:'monospace' }}>{e.affected_prefix}</div>
          </div>
        )}
        {e.expected_asn && (
          <div>
            <div style={{ color:'#887755', fontSize:8, marginBottom:1 }}>EXPECTED ORIGIN</div>
            <div style={{ color:'#8fc4dc', fontSize:11 }}>AS{e.expected_asn}</div>
          </div>
        )}
      </div>
      <div style={{ display:'flex', gap:16, alignItems:'center', flexWrap:'wrap' as const }}>
        <span style={{ color:'#886655', fontSize:8 }}>⏱ {e.duration_human}</span>
        <span style={{ color:'#886655', fontSize:8 }}>age {e.age_human}</span>
        <span style={{ color:'#886655', fontSize:8 }}>×{e.occurrence_count}</span>
        <div style={{ flex:1 }} />
        <span style={{ color:severityColor(sev), fontSize:11, fontWeight:700 }}>S{sev}</span>
        <span style={{ color:'#ff8888', fontSize:9 }}>{(conf*100).toFixed(0)}% conf</span>
      </div>
      {onTrace && (
        <button onClick={ev => { ev.stopPropagation(); onTrace() }} style={{
          marginTop:10, width:'100%', background:'#1a0828',
          border:'1px solid #aa44ff66', borderRadius:3,
          color:'#cc88ff', fontSize:9, padding:'5px', cursor:'pointer',
          fontFamily:'monospace', letterSpacing:'.08em',
        }}>⟶ TRACE PATH ON GLOBE</button>
      )}
    </div>
  )

  // S4 — heavy card
  if (sev === 4) return (
    <div onClick={onSelect} style={{
      background:'#0d0e05', border:`1px solid ${ec}55`,
      borderLeft:`4px solid ${ec}`, borderRadius:5,
      padding:'12px 14px', marginBottom:7, cursor:'pointer',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6 }}>
        <span style={{ fontSize:16 }}>{eventIcon(e.event_type)}</span>
        <span style={{ color:ec, fontSize:11, fontWeight:700, flex:1 }}>
          {e.event_type.replace(/_/g,' ').toUpperCase()}
        </span>
        {isMS && <span style={{ color:'#00ee88', fontSize:8 }}>✓✓</span>}
        <span style={{ color:sc, background:`${sc}18`, border:`1px solid ${sc}55`,
          borderRadius:3, fontSize:8, padding:'2px 7px', fontWeight:600 }}>
          {e.status?.toUpperCase()}
        </span>
      </div>
      <div style={{ display:'flex', gap:16, marginBottom:5, flexWrap:'wrap' as const }}>
        <div style={{ flex:1 }}>
          <div style={{ color:'#665533', fontSize:8, marginBottom:1 }}>OPERATOR</div>
          <div style={{ color:'#ddeeff', fontSize:10, fontWeight:500 }}>{name}</div>
        </div>
        {e.affected_prefix && (
          <div>
            <div style={{ color:'#665533', fontSize:8, marginBottom:1 }}>PREFIX</div>
            <div style={{ color:'#8fc4dc', fontSize:10, fontFamily:'monospace' }}>{e.affected_prefix}</div>
          </div>
        )}
      </div>
      <div style={{ display:'flex', gap:14, alignItems:'center' }}>
        <span style={{ color:'#6a7040', fontSize:8 }}>⏱ {e.duration_human}</span>
        <span style={{ color:'#6a7040', fontSize:8 }}>×{e.occurrence_count}</span>
        <div style={{ flex:1 }} />
        <span style={{ color:severityColor(sev), fontSize:10, fontWeight:700 }}>S{sev}</span>
        <span style={{ color:'#00ccee', fontSize:8 }}>{(conf*100).toFixed(0)}%</span>
      </div>
      {onTrace && (
        <button onClick={ev => { ev.stopPropagation(); onTrace() }} style={{
          marginTop:8, width:'100%', background:'#0d0a1a',
          border:'1px solid #aa44ff44', borderRadius:3,
          color:'#aa66ff', fontSize:8, padding:'4px', cursor:'pointer',
          fontFamily:'monospace', letterSpacing:'.08em',
        }}>⟶ TRACE PATH</button>
      )}
    </div>
  )

  // S3 — standard card
  if (sev === 3) return (
    <div onClick={onSelect} style={{
      background:'#060e18', border:`1px solid ${isMS ? '#00ee8833' : '#0c1a2a'}`,
      borderLeft:`3px solid ${ec}`, borderRadius:4,
      padding:'10px 12px', marginBottom:6, cursor:'pointer',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
        <span style={{ fontSize:13 }}>{eventIcon(e.event_type)}</span>
        <span style={{ color:ec, fontSize:10, flex:1 }}>
          {e.event_type.replace(/_/g,' ').toUpperCase()}
        </span>
        {isMS && <span style={{ color:'#00ee88', fontSize:8 }}>✓✓</span>}
        <span style={{ color:sc, fontSize:8, background:`${sc}18`,
          border:`1px solid ${sc}44`, borderRadius:2, padding:'1px 5px' }}>
          {e.status?.slice(0,3).toUpperCase()}
        </span>
      </div>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
        <span style={{ color:'#aaccdd', fontSize:9 }}>{name}</span>
        <span style={{ color:severityColor(sev), fontSize:9, fontWeight:700 }}>S{sev}</span>
      </div>
      {e.affected_prefix && (
        <div style={{ color:'#6a9ab0', fontSize:9, fontFamily:'monospace', marginBottom:3 }}>
          {e.affected_prefix}
        </div>
      )}
      <div style={{ display:'flex', justifyContent:'space-between' }}>
        <span style={{ color:'#4a7080', fontSize:8 }}>
          {e.duration_human} · ×{e.occurrence_count}
        </span>
        <span style={{ color:'#009ab8', fontSize:8 }}>{(conf*100).toFixed(0)}%</span>
      </div>
    </div>
  )

  // S1–S2 — compact row
  return (
    <div onClick={onSelect} style={{
      display:'flex', alignItems:'center', gap:8,
      borderLeft:`2px solid ${ec}55`, padding:'5px 8px 5px 10px',
      marginBottom:3, cursor:'pointer', borderRadius:'0 3px 3px 0',
    }}>
      <span style={{ fontSize:11, opacity:.7 }}>{eventIcon(e.event_type)}</span>
      <span style={{ color:'#6a9ab0', fontSize:9, flex:1 }}>
        {e.affected_prefix ?? name}
      </span>
      <span style={{ color:'#4a7080', fontSize:8 }}>{e.duration_human}</span>
      <span style={{ color:severityColor(sev), fontSize:8 }}>S{sev}</span>
      <span style={{ color:'#446070', fontSize:8 }}>{(conf*100).toFixed(0)}%</span>
    </div>
  )
}

// ── Section header with collapse support ────────────────────────────────────
function SectionHeader({ label, count, color, collapsed, onToggle, oldest }:
  { label:string; count:number; color:string; collapsed:boolean;
    onToggle:()=>void; oldest?:string }) {
  return (
    <div onClick={onToggle} style={{
      display:'flex', alignItems:'center', gap:8, marginBottom: collapsed ? 4 : 10,
      cursor:'pointer', userSelect:'none' as const,
      padding:'6px 0', borderBottom:`1px solid ${color}22`,
    }}>
      <div style={{ width:8, height:8, borderRadius:'50%',
        background: count > 0 ? color : `${color}44`,
        boxShadow: count > 0 ? `0 0 6px ${color}` : 'none' }} />
      <span style={{ color: count > 0 ? color : `${color}66`,
        fontSize:9, fontWeight:700, letterSpacing:'.12em', flex:1 }}>
        {label}
      </span>
      <span style={{ color:`${color}88`, fontSize:9,
        background:`${color}12`, border:`1px solid ${color}33`,
        borderRadius:3, padding:'1px 7px', fontWeight:700 }}>
        {count}
      </span>
      {oldest && count > 0 && (
        <span style={{ color:`${color}55`, fontSize:8 }}>oldest {oldest}</span>
      )}
      <span style={{ color:`${color}55`, fontSize:10 }}>{collapsed ? '▶' : '▼'}</span>
    </div>
  )
}

// ── Main EventFeed component ─────────────────────────────────────────────────
export function EventFeed() {
  const { setSelectedASN, traceFromEvent } = useNOCStore()
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set(['resolved']))

  const { data: lifecycle, isLoading: lcLoading } = useQuery({
    queryKey: ['lifecycleFeed'],
    queryFn:  () => api.eventLifecycle('all', 200),
    refetchInterval: 30000,
  })
  const { data: xref } = useQuery({
    queryKey: ['xrefFeed'],
    queryFn:  () => api.crossReference(60),
    refetchInterval: 60000,
  })

  const events: any[] = lifecycle?.events ?? []
  const confirmedKeys = new Set<string>(
    (xref?.events ?? []).filter((e: any) => e.multi_source)
      .map((e: any) => `${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)
  )

  // Sort each group: S5 first, then S4, then by occurrence_count desc
  const sortBySev = (arr: any[]) =>
    [...arr].sort((a,b) =>
      (b.peak_severity ?? 0) - (a.peak_severity ?? 0) ||
      (b.occurrence_count ?? 0) - (a.occurrence_count ?? 0)
    )

  const escalated = sortBySev(events.filter(e => e.status === 'escalated'))
  const open      = sortBySev(events.filter(e => e.status === 'open'))
  const resolved  = sortBySev(events.filter(e => e.status === 'resolved'))

  const msCount   = xref?.multi_source_confirmed ?? 0
  const hiSev     = events.filter(e => (e.peak_severity ?? 0) >= 4).length

  // Oldest escalated event age for header context
  const oldestEsc = escalated.length > 0
    ? escalated.reduce((a,b) => (a.age_s ?? 0) > (b.age_s ?? 0) ? a : b).age_human
    : undefined

  const toggleSection = (key: string) =>
    setCollapsedSections(prev => {
      const n = new Set(prev)
      n.has(key) ? n.delete(key) : n.add(key)
      return n
    })

  const isTraceable = (e: any) =>
    e.origin_asn && (e.event_type === 'bgp_hijack' || e.event_type === 'route_leak' || e.expected_asn)

  return (
    <div style={{ height:'100%', overflowY:'auto', padding:'14px 18px',
      fontFamily:'monospace', background:'#030810' }}>
      <style>{`
        @keyframes feedPulse {
          0%,100% { box-shadow: 0 0 8px #ff3b3b22; }
          50%      { box-shadow: 0 0 22px #ff3b3b44; }
        }
      `}</style>

      {/* ── Summary header ────────────────────────────────────────── */}
      <div style={{ display:'flex', gap:10, marginBottom:16, flexWrap:'wrap' as const }}>
        {[
          { label:'ESCALATED', count:escalated.length, c:'#ff3b3b' },
          { label:'OPEN',      count:open.length,      c:'#ffaa00' },
          { label:'RESOLVED',  count:resolved.length,  c:'#00ee88' },
          { label:'S4+ EVENTS',count:hiSev,            c:'#ff6600' },
          { label:'✓✓ CONFIRMED', count:msCount,       c:'#00ccee' },
        ].map(p => (
          <div key={p.label} style={{ background:`${p.c}0e`, border:`1px solid ${p.c}33`,
            borderRadius:5, padding:'6px 14px', textAlign:'center' as const }}>
            <div style={{ color: p.count > 0 ? p.c : `${p.c}44`,
              fontSize:20, fontWeight:700, lineHeight:1 }}>{p.count}</div>
            <div style={{ color:'#5a8090', fontSize:'7px', letterSpacing:'.08em',
              marginTop:2 }}>{p.label}</div>
          </div>
        ))}
        {lcLoading && (
          <div style={{ color:'#4a7090', fontSize:'9px', alignSelf:'center' }}>
            Loading…
          </div>
        )}
      </div>

      {/* ── Multi-source confirmed banner ─────────────────────────── */}
      {msCount > 0 && (
        <div style={{ background:'#00ee8808', border:'1px solid #00ee8822',
          borderRadius:5, padding:'10px 12px', marginBottom:14 }}>
          <div style={{ color:'#00ee88', fontSize:'8px', letterSpacing:'.1em', marginBottom:6 }}>
            ✓✓ MULTI-SOURCE CONFIRMED — RIS + CLOUDFLARE RADAR
          </div>
          {(xref!.events as any[]).filter(e => e.multi_source).map((e:any,i:number) => (
            <div key={i} style={{ display:'flex', justifyContent:'space-between',
              alignItems:'center', marginBottom:3 }}>
              <span style={{ color:'#3a7050', fontSize:'9px' }}>
                {eventIcon(e.event_type)} {e.event_type.replace(/_/g,' ')}
                {e.origin_name ? ` · ${e.origin_name}` : ` · AS${e.origin_asn}`}
                {e.origin_country ? ` (${e.origin_country})` : ''}
              </span>
              <span style={{ color:'#00ee88', fontSize:'9px', fontWeight:700 }}>
                {(e.compound_confidence*100).toFixed(0)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── ESCALATED ────────────────────────────────────────────── */}
      <div style={{ marginBottom:18 }}>
        <SectionHeader label="ESCALATED" count={escalated.length}
          color="#ff3b3b" collapsed={collapsedSections.has('escalated')}
          onToggle={() => toggleSection('escalated')} oldest={oldestEsc} />
        {!collapsedSections.has('escalated') && (
          escalated.length === 0
            ? <div style={{ color:'#4a7090', fontSize:'9px', padding:'6px 0' }}>No escalated events</div>
            : escalated.map((e,i) => (
                <FeedCard key={i} e={e}
                  isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                  onSelect={() => e.origin_asn && setSelectedASN(e.origin_asn)}
                  onTrace={isTraceable(e) ? () => traceFromEvent(e) : undefined} />
              ))
        )}
      </div>

      {/* ── OPEN ─────────────────────────────────────────────────── */}
      <div style={{ marginBottom:18 }}>
        <SectionHeader label="OPEN" count={open.length}
          color="#ffaa00" collapsed={collapsedSections.has('open')}
          onToggle={() => toggleSection('open')} />
        {!collapsedSections.has('open') && (
          open.length === 0
            ? <div style={{ color:'#4a7090', fontSize:'9px', padding:'6px 0' }}>No open events</div>
            : open.map((e,i) => (
                <FeedCard key={i} e={e}
                  isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                  onSelect={() => e.origin_asn && setSelectedASN(e.origin_asn)}
                  onTrace={isTraceable(e) ? () => traceFromEvent(e) : undefined} />
              ))
        )}
      </div>

      {/* ── RESOLVED — collapsed by default ──────────────────────── */}
      <div style={{ marginBottom:18 }}>
        <SectionHeader label="RESOLVED" count={resolved.length}
          color="#00ee88" collapsed={collapsedSections.has('resolved')}
          onToggle={() => toggleSection('resolved')} />
        {!collapsedSections.has('resolved') && (
          resolved.length === 0
            ? null
            : resolved.map((e,i) => (
                <FeedCard key={i} e={e}
                  isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                  onSelect={() => e.origin_asn && setSelectedASN(e.origin_asn)} />
              ))
        )}
      </div>

      {/* Empty state */}
      {events.length === 0 && !lcLoading && (
        <div style={{ color:'#6aa8c0', textAlign:'center', padding:'40px 0', fontSize:'10px' }}>
          No events in last 6 hours
        </div>
      )}

      {/* All-clear state */}
      {events.length > 0 && escalated.length === 0 && open.length === 0 && !lcLoading && (
        <div style={{ background:'#00ee8808', border:'1px solid #00ee8822',
          borderRadius:5, padding:'16px', marginBottom:14, textAlign:'center' as const }}>
          <div style={{ color:'#00ee88', fontSize:12, marginBottom:5 }}>✓ Network healthy</div>
          <div style={{ color:'#4a8060', fontSize:'9px' }}>
            {resolved.length} event{resolved.length !== 1 ? 's' : ''} resolved — no active anomalies
          </div>
        </div>
      )}
    </div>
  )
}
