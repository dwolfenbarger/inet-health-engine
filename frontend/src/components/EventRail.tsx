// src/components/EventRail.tsx — live event stream, severity-tiered visual hierarchy
import { useQuery } from '@tanstack/react-query'
import { useNOCStore } from '../store/nocStore'
import { eventColor, eventIcon, severityColor } from '../lib/asData'
import { api } from '../api/client'

const STATUS_COLOR: Record<string, string> = {
  escalated: '#ff3b3b', open: '#ffaa00', resolved: '#00ee88',
}
const STATUS_LABEL: Record<string, string> = {
  escalated: 'ESCALATED', open: 'OPEN', resolved: 'RESOLVED',
}

// ── Severity-tiered card ─────────────────────────────────────────────────────
function EventCard({ e, isMS, onClick, onTrace }: { e: any; isMS: boolean; onClick: () => void; onTrace?: () => void }) {
  const ec  = eventColor(e.event_type)
  const sev = e.peak_severity ?? e.severity ?? 1
  const sc  = STATUS_COLOR[e.status] ?? '#8fc4dc'
  const name = e.origin_name
    ? `${e.origin_name}${e.origin_country ? ` · ${e.origin_country}` : ''}`
    : e.origin_asn ? `AS${e.origin_asn}` : '—'
  const conf = e.peak_confidence ?? e.confidence ?? 0
  const occurrences = e.occurrence_count ?? 1

  // S5 — critical banner
  if (sev >= 5) return (
    <div onClick={onClick} style={{
      background: `linear-gradient(135deg, #1a0505 0%, #0d0208 100%)`,
      border: `1px solid ${ec}88`,
      borderLeft: `4px solid ${ec}`,
      borderRadius: 4, padding: '10px 11px', marginBottom: 6, cursor: 'pointer',
      boxShadow: `0 0 12px ${ec}22`,
      animation: 'railPulse 1.8s ease-in-out infinite',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
        <span style={{ fontSize: 16, lineHeight:1 }}>{eventIcon(e.event_type)}</span>
        <span style={{ color: ec, fontSize: 11, fontWeight: 700, flex: 1, letterSpacing:'.04em' }}>
          {e.event_type.replace(/_/g,' ').toUpperCase()}
        </span>
        {isMS && <span style={{ color:'#00ee88', fontSize:9, fontWeight:700 }}>✓✓</span>}
      </div>
      <div style={{ color:'#fff', fontSize:10, fontWeight:600, marginBottom:3 }}>{name}</div>
      {e.affected_prefix && (
        <div style={{ color:'#ffaaaa', fontSize:9, marginBottom:4, fontFamily:'monospace' }}>
          {e.affected_prefix}
        </div>
      )}
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <span style={{ color: sc, background:`${sc}25`, border:`1px solid ${sc}66`,
          borderRadius:3, fontSize:8, padding:'2px 7px', fontWeight:700, letterSpacing:'.06em' }}>
          {STATUS_LABEL[e.status] ?? e.status?.toUpperCase()}
        </span>
        <div style={{ display:'flex', gap:8, alignItems:'center' }}>
          <span style={{ color:'#ff8888', fontSize:8 }}>×{occurrences}</span>
          <span style={{ color:severityColor(sev), fontSize:10, fontWeight:700 }}>S{sev}</span>
          <span style={{ color:'#ff6666', fontSize:8 }}>{(conf*100).toFixed(0)}%</span>
        </div>
      </div>
      {onTrace && (
        <button onClick={ev => { ev.stopPropagation(); onTrace() }} style={{
          marginTop:6, width:"100%", background:"#1a0828",
          border:"1px solid #aa44ff66", borderRadius:3,
          color:"#cc88ff", fontSize:8, padding:"4px", cursor:"pointer",
          fontFamily:"monospace", letterSpacing:".08em",
        }}>TRACE PATH ON GLOBE</button>
      )}
    </div>
  )

  // S4 — heavy amber card
  if (sev === 4) return (
    <div onClick={onClick} style={{
      background: '#0d0e05',
      border: `1px solid ${ec}55`,
      borderLeft: `3px solid ${ec}`,
      borderRadius: 4, padding: '9px 10px', marginBottom: 5, cursor: 'pointer',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:5, marginBottom:4 }}>
        <span style={{ fontSize:14, lineHeight:1 }}>{eventIcon(e.event_type)}</span>
        <span style={{ color:ec, fontSize:10, fontWeight:700, flex:1 }}>
          {e.event_type.replace(/_/g,' ').toUpperCase()}
        </span>
        {isMS && <span style={{ color:'#00ee88', fontSize:8 }}>✓✓</span>}
        <span style={{ color:sc, background:`${sc}20`, border:`1px solid ${sc}55`,
          borderRadius:2, fontSize:8, padding:'1px 6px', fontWeight:600 }}>
          {STATUS_LABEL[e.status] ?? e.status?.toUpperCase()}
        </span>
      </div>
      <div style={{ color:'#ddeeff', fontSize:9, fontWeight:500, marginBottom:2 }}>{name}</div>
      {e.affected_prefix && (
        <div style={{ color:'#8fc4dc', fontSize:8, marginBottom:3, fontFamily:'monospace' }}>
          {e.affected_prefix}
        </div>
      )}
      <div style={{ display:'flex', justifyContent:'space-between' }}>
        <span style={{ color:'#6aa8c0', fontSize:7 }}>
          {e.duration_human ?? '—'} · ×{occurrences}
        </span>
        <div style={{ display:'flex', gap:6 }}>
          <span style={{ color:severityColor(sev), fontSize:8, fontWeight:700 }}>S{sev}</span>
          <span style={{ color:'#00ccee', fontSize:7 }}>{(conf*100).toFixed(0)}%</span>
        </div>
      </div>
      {onTrace && (
        <button onClick={ev => { ev.stopPropagation(); onTrace() }} style={{
          marginTop:5, width:'100%', background:'#0d0a1a',
          border:'1px solid #aa44ff44', borderRadius:3,
          color:'#aa66ff', fontSize:7, padding:'3px', cursor:'pointer',
          fontFamily:'monospace', letterSpacing:'.08em',
        }}>⟶ TRACE PATH</button>
      )}
    </div>
  )

  // S3 — standard card
  if (sev === 3) return (
    <div onClick={onClick} style={{
      background: '#060e18',
      border: `1px solid ${isMS ? '#00ee8833' : '#0c1a2a'}`,
      borderLeft: `2px solid ${ec}`,
      borderRadius: 3, padding: '7px 9px', marginBottom: 4, cursor: 'pointer',
    }}>
      <div style={{ display:'flex', alignItems:'center', gap:5, marginBottom:3 }}>
        <span style={{ fontSize:12 }}>{eventIcon(e.event_type)}</span>
        <span style={{ color:ec, fontSize:9, flex:1 }}>
          {e.event_type.replace(/_/g,' ').toUpperCase()}
        </span>
        <span style={{ color:sc, fontSize:7, background:`${sc}18`,
          border:`1px solid ${sc}44`, borderRadius:2, padding:'0 4px' }}>
          {(STATUS_LABEL[e.status] ?? e.status)?.slice(0,3)}
        </span>
      </div>
      <div style={{ display:'flex', justifyContent:'space-between', marginBottom:2 }}>
        <span style={{ color:'#aaccdd', fontSize:9 }}>
          {e.affected_prefix ?? name}
        </span>
        <span style={{ color:severityColor(sev), fontSize:8 }}>S{sev}</span>
      </div>
      <div style={{ display:'flex', justifyContent:'space-between' }}>
        <span style={{ color:'#5a8090', fontSize:7 }}>{e.duration_human ?? '—'} · ×{occurrences}</span>
        <span style={{ color:'#009ab8', fontSize:7 }}>{(conf*100).toFixed(0)}%</span>
      </div>
    </div>
  )

  // S1–S2 — compact row
  return (
    <div onClick={onClick} style={{
      display: 'flex', alignItems: 'center', gap: 6,
      borderLeft: `2px solid ${ec}66`,
      padding: '4px 6px 4px 8px', marginBottom: 3, cursor: 'pointer',
      borderRadius: '0 2px 2px 0',
    }}>
      <span style={{ fontSize:10, opacity:.75 }}>{eventIcon(e.event_type)}</span>
      <span style={{ color:'#6a9ab0', fontSize:8, flex:1 }}>
        {e.affected_prefix ?? name}
      </span>
      <span style={{ color:severityColor(sev), fontSize:7 }}>S{sev}</span>
      <span style={{ color:'#446070', fontSize:7 }}>{(conf*100).toFixed(0)}%</span>
    </div>
  )
}

export function EventRail() {
  const { anomalies, setSelectedASN, setSelectedAnomaly, traceFromEvent } = useNOCStore()
  const { data: lifecycle } = useQuery({
    queryKey: ['lifecycle'],
    queryFn: () => api.eventLifecycle('all', 60),
    refetchInterval: 30000, staleTime: 20000, retry: 2, retryDelay: 3000,
  })
  const { data: xref } = useQuery({
    queryKey: ['crossRef'],
    queryFn: () => api.crossReference(60),
    refetchInterval: 60000,
  })
  const { data: community } = useQuery({
    queryKey: ['communityRail'],
    queryFn: () => api.communityCorrelated(48, 10),
    refetchInterval: 120000,
    staleTime: 90000,
  })
  const communityHits = (community?.signals ?? []).filter((s: any) =>
    s.matched_anomalies?.length > 0 && s.urgency_score > 0.2
  ).length
  const confirmedKeys = new Set<string>(
    (xref?.events ?? []).filter((e: any) => e.multi_source)
      .map((e: any) => `${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)
  )
  const lifecycleEvents: any[] = lifecycle?.events ?? []
  const critical = lifecycleEvents.filter(e => (e.peak_severity ?? e.severity) >= 5)
  const high     = lifecycleEvents.filter(e => (e.peak_severity ?? e.severity) === 4)
  const medium   = lifecycleEvents.filter(e => (e.peak_severity ?? e.severity) === 3)
  const low      = lifecycleEvents.filter(e => (e.peak_severity ?? e.severity) <= 2)
  const escalatedCount = lifecycle?.escalated ?? 0
  const openCount      = lifecycle?.open ?? 0
  const msCount        = xref?.multi_source_confirmed ?? 0
  const sorted = [...anomalies].sort((a, b) =>
    new Date(b.time).getTime() - new Date(a.time).getTime())
  const handleClick = (e: any) => {
    if (e.origin_asn) setSelectedASN(e.origin_asn)
    setSelectedAnomaly(e)
  }
  // isTraceable: events with known origin that can drive globe path rendering
  const isTraceable = (e: any): boolean =>
    !!(e.origin_asn && (e.event_type === "bgp_hijack" || e.event_type === "route_leak" || e.expected_asn))
  const makeTraceHandler = (e: any) =>
    isTraceable(e) ? () => traceFromEvent(e) : undefined

  return (
    <div style={{ width:300, background:'#040d1a', borderLeft:'1px solid #0d2035',
      display:'flex', flexDirection:'column', flexShrink:0, overflow:'hidden', fontFamily:'monospace' }}>
      <style>{`
        @keyframes railPulse {
          0%,100% { box-shadow: 0 0 8px #ff3b3b22; }
          50%      { box-shadow: 0 0 20px #ff3b3b55; }
        }
      `}</style>

      {/* Header */}
      <div style={{ padding:'8px 10px', borderBottom:'1px solid #0d2035', flexShrink:0 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:5 }}>
          <span style={{ color:'#4a9fc8', fontSize:9, letterSpacing:'.12em', fontWeight:700 }}>
            LIVE EVENTS
          </span>
          {msCount > 0 && (
            <span style={{ color:'#00ee88', fontSize:8, background:'#00ee8814',
              border:'1px solid #00ee8833', borderRadius:3, padding:'1px 6px' }}>
              ✓✓ {msCount}
            </span>
          )}
          {communityHits > 0 && (
            <span style={{ color:'#aa88ff', fontSize:8, background:'#aa88ff14',
              border:'1px solid #aa88ff33', borderRadius:3, padding:'1px 6px' }}>
              ◎ {communityHits}
            </span>
          )}
        </div>
        <div style={{ display:'flex', gap:5 }}>
          {[
            { label:'ESC',  count: escalatedCount,  c:'#ff3b3b' },
            { label:'OPEN', count: openCount,        c:'#ffaa00' },
            { label:'S5',   count: critical.length,  c:'#ff0055' },
            { label:'S4',   count: high.length,      c:'#ff8800' },
          ].map(p => (
            <div key={p.label} style={{ flex:1,
              background: `${p.c}${p.count > 0 ? '18' : '08'}`,
              border: `1px solid ${p.c}${p.count > 0 ? '55' : '22'}`,
              borderRadius:3, padding:'3px 0', textAlign:'center' }}>
              <div style={{ color: p.count > 0 ? p.c : `${p.c}44`,
                fontSize:11, fontWeight:700, lineHeight:1 }}>{p.count}</div>
              <div style={{ color:`${p.c}77`, fontSize:7 }}>{p.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Multi-source confirmed strip */}
      {msCount > 0 && (
        <div style={{ background:'#00ee8808', borderBottom:'1px solid #00ee8820',
          padding:'5px 10px', flexShrink:0 }}>
          <div style={{ color:'#00ee88', fontSize:8, marginBottom:3 }}>
            ✓✓ RIS + RADAR CONFIRMED
          </div>
          {(xref!.events as any[]).filter(e => e.multi_source).slice(0,2).map((e:any,i:number) => (
            <div key={i} style={{ display:'flex', justifyContent:'space-between', fontSize:8 }}>
              <span style={{ color:'#3a8060' }}>
                {eventIcon(e.event_type)} AS{e.origin_asn}
                {e.origin_name ? ` · ${e.origin_name.slice(0,14)}` : ''}
              </span>
              <span style={{ color:'#00ee88', fontWeight:700 }}>
                {(e.compound_confidence*100).toFixed(0)}%
              </span>
            </div>
          ))}
        </div>
      )}

      {/* ── Zone 1: Incident zone (critical + high — always visible) ── */}
      {(critical.length > 0 || high.length > 0) && (
        <div style={{ flexShrink:0, maxHeight:'45%', overflowY:'auto',
          borderBottom:'1px solid #1a0808', padding:'4px 8px',
          background:'#040810' }}>
          <div style={{ color:'#6a2020', fontSize:7, letterSpacing:'.1em',
            padding:'2px 0 4px', display:'flex', alignItems:'center', gap:4 }}>
            <div style={{ width:5, height:5, borderRadius:'50%',
              background:'#ff3b3b', boxShadow:'0 0 4px #ff3b3b' }} />
            ACTIVE INCIDENTS
          </div>
          {critical.map((e,i) => (
            <EventCard key={`ic${i}`} e={e}
              isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
              onClick={() => handleClick(e)} onTrace={makeTraceHandler(e)} />
          ))}
          {high.map((e,i) => (
            <EventCard key={`ih${i}`} e={e}
              isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
              onClick={() => handleClick(e)} onTrace={makeTraceHandler(e)} />
          ))}
        </div>
      )}

      {/* ── Zone 2: Full event stream ── */}
      <div style={{ flex:1, overflowY:'auto', padding:'6px 8px' }}>
        {lifecycleEvents.length > 0 ? (
          <>
            {critical.map((e,i) => (
              <EventCard key={`c${i}`} e={e}
                isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                onClick={() => handleClick(e)}
                onTrace={makeTraceHandler(e)} />
            ))}
            {high.map((e,i) => (
              <EventCard key={`h${i}`} e={e}
                isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                onClick={() => handleClick(e)}
                onTrace={makeTraceHandler(e)} />
            ))}

            {medium.map((e,i) => (
              <EventCard key={`m${i}`} e={e}
                isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                onClick={() => handleClick(e)}
                onTrace={makeTraceHandler(e)} />
            ))}
            {low.length > 0 && (
              <>
                <div style={{ color:'#3a6070', fontSize:7, letterSpacing:'.1em',
                  padding:'4px 0 3px', marginTop:2 }}>
                  LOW SEVERITY ({low.length})
                </div>
                {low.map((e,i) => (
                  <EventCard key={`l${i}`} e={e}
                    isMS={confirmedKeys.has(`${e.event_type}:${e.affected_prefix}:${e.origin_asn}`)}
                    onClick={() => handleClick(e)}
                onTrace={makeTraceHandler(e)} />
                ))}
              </>
            )}
          </>
        ) : (
          sorted.slice(0,40).map(a => {
            const ec  = eventColor(a.event_type)
            const ago = Math.round((Date.now() - new Date(a.time).getTime()) / 60000)
            return (
              <div key={a.event_id}
                onClick={() => { setSelectedAnomaly(a); if (a.origin_asn) setSelectedASN(a.origin_asn) }}
                style={{ background:'#06101c', borderLeft:`2px solid ${ec}`,
                  borderRadius:3, padding:'6px 8px', marginBottom:4, cursor:'pointer' }}>
                <div style={{ display:'flex', gap:5, alignItems:'center', marginBottom:2 }}>
                  <span style={{ fontSize:12 }}>{eventIcon(a.event_type)}</span>
                  <span style={{ color:ec, fontSize:9, flex:1 }}>
                    {a.event_type.replace(/_/g,' ').toUpperCase()}
                  </span>
                  <span style={{ color:'#6aa8c0', fontSize:7 }}>{ago}m</span>
                </div>

                <div style={{ display:'flex', justifyContent:'space-between' }}>
                  <span style={{ color:'#8fc4dc', fontSize:8 }}>
                    {a.affected_prefix ?? `AS${a.origin_asn}`}
                  </span>
                  <span style={{ color:severityColor(a.severity), fontSize:8 }}>S{a.severity}</span>
                </div>
              </div>
            )
          })
        )}
        {lifecycleEvents.length === 0 && sorted.length === 0 && (
          <div style={{ color:'#4a7090', textAlign:'center', paddingTop:40, fontSize:9 }}>
            Waiting for events…
          </div>
        )}

        {/* Community signal strip - shown when correlated posts exist */}
        {communityHits > 0 && (
          <div style={{ marginTop:8 }}>
            <div style={{ color:'#6a5090', fontSize:7, letterSpacing:'.1em',
              padding:'4px 0 3px', display:'flex', alignItems:'center', gap:5 }}>
              <span style={{ color:'#aa88ff' }}>◎</span>
              COMMUNITY SIGNALS ({communityHits})
            </div>
            {(community!.signals as any[])
              .filter((s: any) => s.matched_anomalies?.length > 0 && s.urgency_score > 0.2)
              .slice(0, 4)
              .map((s: any, i: number) => {
                const srcColor = s.source === 'mastodon' ? '#aa88ff'
                               : s.source === 'reddit'   ? '#ff6b35'
                               : '#00ccee'
                const srcLabel = s.source === 'mastodon' ? 'M'
                               : s.source === 'reddit'   ? 'R'
                               : s.source === 'hackernews' ? 'HN' : s.source?.slice(0,3).toUpperCase()
                return (
                  <div key={i} style={{ display:'flex', alignItems:'flex-start', gap:5,
                    padding:'4px 6px', marginBottom:3, background:'#06080e',
                    border:'1px solid #1a0d2e', borderLeft:`2px solid ${srcColor}`,
                    borderRadius:'0 3px 3px 0' }}>
                    <span style={{ color:srcColor, fontSize:8, fontWeight:700,
                      minWidth:16, textAlign:'center', marginTop:1 }}>{srcLabel}</span>
                    <div style={{ flex:1, minWidth:0 }}>
                      <div style={{ color:'#9a7ab4', fontSize:8, lineHeight:1.3,
                        overflow:'hidden', display:'-webkit-box',
                        WebkitLineClamp:2, WebkitBoxOrient:'vertical' as const }}>
                        {(s.title ?? s.text ?? '').slice(0, 80)}
                      </div>
                      {s.matched_anomalies?.[0] && (
                        <div style={{ color:'#554070', fontSize:7, marginTop:2 }}>
                          {s.matched_anomalies[0].event_type?.replace(/_/g,' ')}
                          {s.matched_anomalies[0].prefix ? ` · ${s.matched_anomalies[0].prefix}` : ''}
                        </div>
                      )}
                    </div>
                    <span style={{ color:'#6a5090', fontSize:7, flexShrink:0 }}>
                      {Math.round(s.urgency_score * 100)}%
                    </span>
                  </div>
                )
              })
            }
          </div>
        )}
      </div>
    </div>
  )
}
