// src/components/ASSidebar.tsx — AS deep-dive drawer
// Desktop: fixed 265px right panel
// Mobile: bottom sheet with swipe-to-dismiss
import { useEffect, useState, useRef, useCallback } from 'react'
import { useNOCStore } from '../store/nocStore'
import { getASMeta, severityColor, severityLabel, eventColor, eventIcon } from '../lib/asData'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { api } from '../api/client'

function StatRow({ label, value, color='#ddeeff' }:
  { label:string; value:string|number; color?:string }) {
  return (
    <div style={{ display:'flex', justifyContent:'space-between', marginBottom:5 }}>
      <span style={{ color:'#7ab8d4', fontSize:'9px' }}>{label}</span>
      <span style={{ color, fontSize:'9px' }}>{String(value)}</span>
    </div>
  )
}

function MiniBar({ value, max, color }: { value:number; max:number; color:string }) {
  const pct = Math.round((value / Math.max(max, 1)) * 100)
  return (
    <div style={{ height:3, background:'#071420', borderRadius:2, marginTop:2 }}>
      <div style={{ width:`${pct}%`, height:'100%', background:color, borderRadius:2 }} />
    </div>
  )
}

function MiniSparkline({ values, color }: { values:number[]; color:string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current; if (!c || values.length < 2) return
    const W = 220, H = 32, ctx = c.getContext('2d')!
    ctx.clearRect(0, 0, W, H)
    const mn = Math.min(...values), mx = Math.max(...values), range = mx - mn || 1
    const grad = ctx.createLinearGradient(0, 0, 0, H)
    grad.addColorStop(0, color + '44'); grad.addColorStop(1, color + '00')
    ctx.beginPath()
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * W
      const y = H - ((v - mn) / range) * (H - 4) - 2
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath()
    ctx.fillStyle = grad; ctx.fill()
    ctx.beginPath()
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * W
      const y = H - ((v - mn) / range) * (H - 4) - 2
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke()
  }, [values, color])
  return <canvas ref={ref} width={220} height={32}
    style={{ display:'block', width:'100%', height:32 }} />
}

export function ASSidebar() {
  const { selectedASN, setSelectedASN, anomalies, pathSrcASN, pathDstASN,
          setPathSrc, setPathDst, selectedAnomaly, setTraceHops } = useNOCStore()
  const isMobile = useBreakpoint(768)

  const [profile,     setProfile]     = useState<any>(null)
  const [pathResult,  setPathResult]  = useState<any>(null)
  const [loading,     setLoading]     = useState(false)
  const [pathLoading, setPathLoading] = useState(false)
  const [tab,         setTab]         = useState<'info'|'path'|'flaps'>('info')
  const [flapData,    setFlapData]    = useState<any>(null)
  const [flapLoading, setFlapLoading] = useState(false)
  const [traceSrcIP,  setTraceSrcIP]  = useState('')
  const [traceDstIP,  setTraceDstIP]  = useState('')
  const [traceResult, setTraceResult] = useState<any>(null)
  const [traceLoading,setTraceLoading]= useState(false)

  // Swipe-to-dismiss for mobile bottom sheet
  const touchStartY  = useRef<number>(0)
  const touchDeltaY  = useRef<number>(0)
  const [swipeOffset,setSwipeOffset] = useState(0)

  const handleTouchStart = useCallback((e: React.TouchEvent) => {
    touchStartY.current = e.touches[0].clientY; touchDeltaY.current = 0
  }, [])
  const handleTouchMove = useCallback((e: React.TouchEvent) => {
    const d = e.touches[0].clientY - touchStartY.current
    touchDeltaY.current = d; if (d > 0) setSwipeOffset(d)
  }, [])
  const handleTouchEnd = useCallback(() => {
    if (touchDeltaY.current > 80) { setSelectedASN(null); setPathSrc(null); setPathDst(null) }
    setSwipeOffset(0)
  }, [setSelectedASN, setPathSrc, setPathDst])

  const dismiss = () => { setSelectedASN(null); setPathSrc(null); setPathDst(null) }

  const staticMeta = selectedASN ? getASMeta(selectedASN) : null

  useEffect(() => {
    if (!selectedASN) { setProfile(null); return }
    setLoading(true)
    api.asProfile(selectedASN).then(d => { setProfile(d); setLoading(false) })
      .catch(() => { setProfile(null); setLoading(false) })
  }, [selectedASN])

  useEffect(() => {
    if (!selectedASN) { setFlapData(null); return }
    const myFlaps = anomalies.filter(a => a.origin_asn === selectedASN && a.event_type === 'bgp_flap')
    if (myFlaps.length === 0) { setFlapData(null); return }
    setFlapLoading(true)
    fetch(`/api/v1/globe/flaps?asn=${selectedASN}&window_m=15`)
      .then(r => r.json()).then(d => { setFlapData(d); setFlapLoading(false) })
      .catch(() => { setFlapData(null); setFlapLoading(false) })
  }, [selectedASN, anomalies])

  useEffect(() => {
    if (!selectedAnomaly) return
    if (selectedAnomaly.event_type === 'bgp_hijack' && selectedAnomaly.expected_asn)
      setTab('path')
  }, [selectedAnomaly])

  useEffect(() => {
    if (!pathSrcASN || !pathDstASN) { setPathResult(null); return }
    setPathLoading(true); setTab('path')
    api.pathAnalysis(pathSrcASN, pathDstASN)
      .then(r => { setPathResult(r); setPathLoading(false) })
      .catch(() => { setPathResult(null); setPathLoading(false) })
  }, [pathSrcASN, pathDstASN])

  if (!selectedASN && !pathSrcASN) return null

  const liveAnomaly    = anomalies.find(a => a.origin_asn === selectedASN) as any
  const displayName    = liveAnomaly?.origin_name    ?? staticMeta?.name    ?? `AS${selectedASN}`
  const displayCountry = liveAnomaly?.origin_country ?? staticMeta?.country ?? '?'
  const dotColor       = staticMeta?.color ?? '#00ccee'
  const myAnomalies    = anomalies.filter(a => a.origin_asn === selectedASN || a.expected_asn === selectedASN)
  const sparkVals      = profile?.hourly_updates?.slice(0, 12).reverse().map((h: any) => h.updates) ?? []

  // ── Shared tab content ─────────────────────────────────────────────────────
  const tabContent = (
    <div style={{ flex:1, overflowY:'auto', padding:'10px 12px',
      WebkitOverflowScrolling:'touch' as any }}>

      {tab === 'info' && selectedASN && (
        <>
          <div style={{ marginBottom:10 }}>
            <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:5 }}>IDENTITY</div>
            <StatRow label="ASN"     value={`AS${selectedASN}`} />
            <StatRow label="Name"    value={displayName} />
            <StatRow label="Country" value={displayCountry} />
            {staticMeta && <StatRow label="Tier" value={staticMeta.tier} />}
            {loading && <div style={{ color:'#6aa8c0', fontSize:'8px', marginTop:4 }}>Loading…</div>}
          </div>
          {sparkVals.length > 1 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:4 }}>
                UPDATES / HOUR (12h)
              </div>
              <MiniSparkline values={sparkVals} color={dotColor} />
              <div style={{ display:'flex', justifyContent:'space-between', marginTop:2 }}>
                <span style={{ color:'#6aa8c0', fontSize:'7px' }}>12h ago</span>
                <span style={{ color:'#00ccee', fontSize:'7px' }}>
                  {sparkVals[sparkVals.length-1]?.toLocaleString()} now
                </span>
              </div>
            </div>
          )}

          {myAnomalies.length > 0 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:5 }}>
                ACTIVE ANOMALIES ({myAnomalies.length})
              </div>
              {myAnomalies.slice(0, 5).map(a => (
                <div key={a.event_id} style={{ background:'#06111e',
                  border:`1px solid ${eventColor(a.event_type)}33`,
                  borderLeft:`2px solid ${eventColor(a.event_type)}`,
                  borderRadius:3, padding:'5px 7px', marginBottom:4 }}>
                  <div style={{ display:'flex', justifyContent:'space-between', marginBottom:2 }}>
                    <span style={{ color:eventColor(a.event_type), fontSize:'8px' }}>
                      {eventIcon(a.event_type)} {a.event_type.replace(/_/g,' ').toUpperCase()}
                    </span>
                    <span style={{ color:severityColor(a.severity), fontSize:'7px' }}>
                      {severityLabel(a.severity)}
                    </span>
                  </div>
                  {a.affected_prefix && <div style={{ color:'#ddeeff', fontSize:'8px' }}>{a.affected_prefix}</div>}
                  <div style={{ color:'#7ab8d4', fontSize:'7px', marginTop:2 }}>
                    conf {(a.confidence*100).toFixed(0)}%
                  </div>
                </div>
              ))}
            </div>
          )}
          {profile?.top_prefixes?.length > 0 && (
            <div style={{ marginBottom:10 }}>
              <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:5 }}>
                TOP PREFIXES (24h)
              </div>

              {profile.top_prefixes.slice(0, 8).map((p: any) => (
                <div key={p.prefix} style={{ marginBottom:5 }}>
                  <div style={{ display:'flex', justifyContent:'space-between', marginBottom:1 }}>
                    <span style={{ color:'#ddeeff', fontSize:'8px' }}>{p.prefix}</span>
                    <span style={{ color:'#7ab8d4', fontSize:'7px' }}>{p.changes.toLocaleString()}</span>
                  </div>
                  <MiniBar value={p.changes} max={profile.top_prefixes[0].changes} color={dotColor} />
                </div>
              ))}
            </div>
          )}
          {!loading && !profile && myAnomalies.length === 0 && (
            <div style={{ color:'#6aa8c0', textAlign:'center', padding:'20px 0', fontSize:'9px' }}>
              No data in last 24h for AS{selectedASN}
            </div>
          )}
          <button onClick={() => { setPathSrc(selectedASN); setTab('path') }}
            style={{ width:'100%', marginTop:8, background:'#0a0820',
              border:'1px solid #aa44ff44', borderRadius:4, color:'#aa44ff',
              padding:'6px', cursor:'pointer', fontSize:'8px', fontFamily:'inherit' }}>
            TRACE PATH FROM AS{selectedASN} →
          </button>
        </>
      )}

      {tab === 'path' && (
        <div>
          <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:8 }}>AS PATH ANALYSIS</div>
          {selectedAnomaly?.event_type === 'bgp_hijack' && (
            <div style={{ background:'#1a0a05', border:'1px solid #ffdd0044',
              borderRadius:4, padding:'7px 9px', marginBottom:10 }}>
              <div style={{ color:'#ffdd00', fontSize:'8px', fontWeight:700, marginBottom:4 }}>
                HIJACK CONTEXT
              </div>
              <div style={{ color:'#ccaa44', fontSize:'8px', marginBottom:2 }}>
                Attacker: AS{selectedAnomaly.origin_asn}
              </div>
              {selectedAnomaly.expected_asn && (
                <div style={{ color:'#8fc4dc', fontSize:'8px', marginBottom:2 }}>
                  Victim: AS{selectedAnomaly.expected_asn}
                </div>
              )}
              {selectedAnomaly.affected_prefix && (
                <div style={{ color:'#7ab8d4', fontSize:'8px', fontFamily:'monospace' }}>
                  Prefix: {selectedAnomaly.affected_prefix}
                </div>
              )}
              <div style={{ color:'#887755', fontSize:'7px', marginTop:3 }}>
                conf {(selectedAnomaly.confidence*100).toFixed(0)}% · S{selectedAnomaly.severity}
              </div>
            </div>
          )}
          <div style={{ background:'#060e18', border:'1px solid #0d1e30',
            borderRadius:4, padding:'8px 9px', marginBottom:10 }}>

            <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:6 }}>
              IP TRACEROUTE
            </div>
            {[
              { label:'SRC IP', val:traceSrcIP, set:setTraceSrcIP,
                hint: (selectedAnomaly?.affected_prefix?.split('/')[0]) ?? 'e.g. 1.2.3.4' },
              { label:'DST IP', val:traceDstIP, set:setTraceDstIP, hint:'e.g. 8.8.8.8' },
            ].map(({ label, val, set, hint }) => (
              <div key={label} style={{ marginBottom:5 }}>
                <div style={{ color:'#6aa8c0', fontSize:'7px', marginBottom:2 }}>{label}</div>
                <input value={val} onChange={e => set(e.target.value)} placeholder={hint}
                  style={{ width:'100%', background:'#040d18', border:'1px solid #0d2035',
                    borderRadius:3, color:'#ddeeff', padding:'4px 6px', fontSize:'9px',
                    fontFamily:'monospace', outline:'none', boxSizing:'border-box' as const }} />
              </div>
            ))}
            <button
              disabled={!traceSrcIP || !traceDstIP || traceLoading}
              onClick={() => {
                if (!traceSrcIP || !traceDstIP) return
                setTraceLoading(true)
                fetch(`/api/v1/intelligence/traceroute?src=${encodeURIComponent(traceSrcIP)}&dst=${encodeURIComponent(traceDstIP)}`)
                  .then(r => r.json())
                  .then(d => { setTraceResult(d); setTraceLoading(false); if (d.hops?.length) setTraceHops(d.hops) })
                  .catch(() => setTraceLoading(false))
              }}
              style={{ width:'100%', marginTop:4,
                background: traceSrcIP && traceDstIP ? '#0a0820' : '#040810',
                border:`1px solid ${traceSrcIP && traceDstIP ? '#aa44ff66' : '#0d2035'}`,
                borderRadius:3, color: traceSrcIP && traceDstIP ? '#aa66ff' : '#4a6070',
                padding:'5px', cursor: traceSrcIP && traceDstIP ? 'pointer' : 'default',
                fontSize:'8px', fontFamily:'monospace', letterSpacing:'.08em' }}>
              {traceLoading ? 'TRACING...' : 'RUN TRACEROUTE'}
            </button>
          </div>

          {traceResult?.hops && (
            <div style={{ marginBottom:10 }}>
              <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:5 }}>
                HOPS ({traceResult.hops.length})
              </div>
              {traceResult.hops.map((h: any, i: number) => (
                <div key={i} style={{ display:'flex', alignItems:'center', gap:6,
                  padding:'4px 6px', marginBottom:3, background:'#06101c',
                  border:'1px solid #0a1828', borderRadius:3 }}>
                  <span style={{ color:'#4a7090', fontSize:'7px', minWidth:14 }}>{i+1}</span>
                  <span style={{ color:'#ddeeff', fontSize:'8px', fontFamily:'monospace', flex:1 }}>
                    {h.ip ?? '*'}
                  </span>
                  {h.asn && <span style={{ color:'#00ccee', fontSize:'7px' }}>AS{h.asn}</span>}
                  {h.org && (
                    <span style={{ color:'#6aa8c0', fontSize:'7px',
                      maxWidth:60, overflow:'hidden', textOverflow:'ellipsis',
                      whiteSpace:'nowrap' as const }}>
                      {h.org.slice(0,12)}
                    </span>
                  )}
                  {h.rtt_ms && <span style={{ color:'#7ab8d4', fontSize:'7px' }}>{h.rtt_ms}ms</span>}
                </div>
              ))}
            </div>
          )}
          {(['src','dst'] as const).map(k => {
            const asn = k === 'src' ? pathSrcASN : pathDstASN
            return (
              <div key={k} style={{ marginBottom:6 }}>
                <div style={{ color:'#6aa8c0', fontSize:'7px', marginBottom:2 }}>
                  {k === 'src' ? 'SOURCE' : 'DESTINATION'}
                </div>

                <div style={{ display:'flex', gap:4 }}>
                  <div style={{ flex:1, background:'#06111e', border:'1px solid #0d2035',
                    borderRadius:3, padding:'4px 6px',
                    color: asn ? '#aa44ff' : '#6aa8c0', fontSize:'8px' }}>
                    {asn ? `AS${asn}` : 'Click AS on globe'}
                  </div>
                  {asn && (
                    <button onClick={() => k==='src' ? setPathSrc(null) : setPathDst(null)}
                      style={{ background:'none', border:'1px solid #0d2035', borderRadius:3,
                        color:'#7ab8d4', padding:'2px 5px', cursor:'pointer',
                        fontFamily:'inherit' }}>x</button>
                  )}
                </div>
              </div>
            )
          })}
          {pathLoading && (
            <div style={{ color:'#7ab8d4', textAlign:'center', padding:'20px 0' }}>
              Querying Neo4j + TimescaleDB...
            </div>
          )}
          {pathResult && !pathLoading && (
            <>
              <div style={{ display:'flex', gap:8, margin:'10px 0' }}>
                {[
                  { label:'PATHS',     val:pathResult.path_count,                         c:'#aa44ff' },
                  { label:'STABILITY', val:`${pathResult.stability?.score?.toFixed(0)}%`, c:'#00ccee' },
                  { label:'OBS',       val:pathResult.stability?.total_observations,      c:'#ffaa00' },
                ].map(({ label, val, c }) => (
                  <div key={label} style={{ flex:1, background:'#06111e',
                    border:'1px solid #0d2035', borderRadius:3, padding:'5px', textAlign:'center' }}>
                    <div style={{ color:c, fontSize:13, fontWeight:700 }}>{val ?? '—'}</div>
                    <div style={{ color:'#6aa8c0', fontSize:'7px' }}>{label}</div>
                  </div>
                ))}
              </div>

              {pathResult.paths?.slice(0, 6).map((p: any, i: number) => {
                const col = p.path_type === 'graph' ? '#aa44ff' : '#ffaa00'
                return (
                  <div key={i} style={{ background:'#06111e', border:`1px solid ${col}22`,
                    borderLeft:`2px solid ${col}`, borderRadius:3, padding:'6px 8px', marginBottom:5 }}>
                    <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
                      <span style={{ color:col, fontSize:'7px' }}>
                        {p.path_type === 'graph' ? 'GRAPH' : 'OBSERVED'}
                      </span>
                      <span style={{ color:'#7ab8d4', fontSize:'7px' }}>{p.hops} hops</span>
                    </div>
                    <div style={{ display:'flex', alignItems:'center', flexWrap:'wrap', gap:2 }}>
                      {p.asns?.map((asn: number, j: number) => {
                        const m = getASMeta(asn)
                        return (
                          <span key={j} style={{ display:'flex', alignItems:'center', gap:2 }}>
                            <span style={{ color: m?.color ?? '#ddeeff',
                              background: m ? `${m.color}15` : '#0a1828',
                              border:`1px solid ${m?.color ?? '#8fc4dc'}44`,
                              borderRadius:2, padding:'1px 4px', fontSize:'7px' }}>
                              {m ? m.short : `AS${asn}`}
                            </span>
                            {j < p.asns.length - 1 && <span style={{ color:'#8fc4dc', fontSize:'8px' }}>›</span>}
                          </span>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}

      {tab === 'flaps' && selectedASN && (
        <div>
          <div style={{ color:'#ff3b3b', fontSize:'8px', letterSpacing:'.1em', marginBottom:8 }}>
            BGP FLAP DETAIL
          </div>
          {flapLoading && <div style={{ color:'#7ab8d4', fontSize:'9px' }}>Loading...</div>}
          {flapData && !flapLoading && (
            <>
              <StatRow label="Window"       value="15 min" />
              <StatRow label="Total flaps"  value={flapData.total_flaps} color="#ff3b3b" />
              <StatRow label="Prefixes"     value={flapData.prefixes?.length ?? 0} />
              {flapData.timeline?.length > 1 && (
                <div style={{ marginTop:8, marginBottom:8 }}>
                  <div style={{ color:'#7ab8d4', fontSize:'8px', marginBottom:3 }}>FLAP RATE / MINUTE</div>
                  <MiniSparkline values={flapData.timeline.map((b: any) => b.count)} color="#ff3b3b" />
                </div>
              )}
              <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginTop:10, marginBottom:5 }}>
                UNSTABLE PREFIXES
              </div>
              {(flapData.prefixes ?? []).map((p: any) => (
                <div key={p.prefix} style={{ background:'#060e1a', border:'1px solid #1a0808',
                  borderRadius:3, padding:'5px 7px', marginBottom:5 }}>
                  <div style={{ color:'#ff6060', fontSize:'9px', fontWeight:700, marginBottom:3 }}>
                    {p.prefix}
                  </div>
                  <StatRow label="Flaps (15m)"  value={p.flap_count}    color="#ff3b3b" />
                  <StatRow label="Confidence"   value={`${(p.avg_confidence*100).toFixed(0)}%`} color="#ffaa44" />
                  <StatRow label="Max severity" value={p.max_severity} />
                  <div style={{ marginTop:3 }}>
                    <MiniBar value={p.flap_count} max={flapData.total_flaps} color="#ff3b3b" />
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

    </div>
  )  // end tabContent

  // Shared tab bar — used in both mobile and desktop renders
  const tabBar = (fontSize: string, minH: number) => (
    <div style={{ display:'flex', borderBottom:'1px solid #0d2035', flexShrink:0 }}>
      {(['info','path'] as const).map(t => (
        <button key={t} onClick={() => setTab(t)} style={{
          flex:1, background: tab===t ? '#071420' : 'none', border:'none',
          borderBottom: tab===t ? '2px solid #00ccee' : '2px solid transparent',
          color: tab===t ? '#00ccee' : '#6aa8c0',
          padding:'5px', cursor:'pointer', fontSize, fontFamily:'inherit', minHeight:minH,
        }}>{t === 'info' ? 'AS INFO' : 'PATH'}</button>
      ))}
      {flapData && flapData.total_flaps > 0 && (
        <button onClick={() => setTab('flaps')} style={{
          flex:1, background: tab==='flaps' ? '#1a0808' : 'none', border:'none',
          borderBottom: tab==='flaps' ? '2px solid #ff3b3b' : '2px solid transparent',
          color: tab==='flaps' ? '#ff3b3b' : '#c06060',
          padding:'5px', cursor:'pointer', fontSize, fontFamily:'inherit', minHeight:minH,
        }}>FLAPS ({flapData.total_flaps})</button>
      )}
    </div>
  )

  // ── MOBILE: bottom sheet ──────────────────────────────────────────────────
  if (isMobile) {
    return (
      <>
        <div onClick={dismiss} style={{ position:'fixed', inset:0,
          background:'rgba(0,0,0,0.55)', zIndex:100, backdropFilter:'blur(2px)' }} />
        <div
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          style={{
            position:'fixed', left:0, right:0, bottom:0, zIndex:101,
            height:'78vh', maxHeight:'78vh',
            background:'#05101e', borderTop:'1px solid #0d2035',
            borderRadius:'16px 16px 0 0',
            boxShadow:'0 -8px 32px rgba(0,0,0,0.7)',
            display:'flex', flexDirection:'column',
            fontFamily:'monospace',
            transform: `translateY(${swipeOffset > 0 ? swipeOffset : 0}px)`,
            transition: swipeOffset === 0 ? 'transform .25s ease' : 'none',
          }}>
          <div style={{ display:'flex', justifyContent:'center', padding:'10px 0 4px', flexShrink:0 }}>
            <div style={{ width:36, height:4, borderRadius:2, background:'#1a3a50' }} />
          </div>
          <div style={{ padding:'8px 16px 10px', borderBottom:'1px solid #0d2035',
            display:'flex', alignItems:'center', gap:10, flexShrink:0 }}>
            <div style={{ width:12, height:12, borderRadius:'50%',
              background:dotColor, boxShadow:`0 0 8px ${dotColor}`, flexShrink:0 }} />
            <div style={{ flex:1, minWidth:0 }}>
              <div style={{ color:dotColor, fontSize:14, fontWeight:700 }}>
                AS{selectedASN ?? pathSrcASN}
              </div>
              <div style={{ color:'#8fc4dc', fontSize:10, overflow:'hidden',
                textOverflow:'ellipsis', whiteSpace:'nowrap' as const }}>
                {displayName}{displayCountry !== '?' ? ` · ${displayCountry}` : ''}
              </div>
            </div>
            <button onClick={dismiss} style={{ background:'#0a1828',
              border:'1px solid #1a3a50', borderRadius:6, color:'#7ab8d4',
              padding:'6px 12px', cursor:'pointer', fontFamily:'inherit',
              fontSize:11, minHeight:36 }}>x</button>
          </div>
          {tabBar('10px', 44)}
          {tabContent}
        </div>
      </>
    )
  }

  // ── DESKTOP: side panel ───────────────────────────────────────────────────
  return (
    <div style={{ width:265, height:'100%',
      background:'#05101ef0', borderLeft:'1px solid #0d2035',
      backdropFilter:'blur(8px)', display:'flex', flexDirection:'column',
      fontFamily:'monospace', fontSize:'9px',
      boxShadow:'-4px 0 20px rgba(0,0,0,0.6)' }}>
      <div style={{ padding:'10px 12px', borderBottom:'1px solid #0d2035',
        display:'flex', alignItems:'center', gap:8 }}>
        <div style={{ width:10, height:10, borderRadius:'50%',
          background:dotColor, boxShadow:`0 0 6px ${dotColor}`, flexShrink:0 }} />
        <div style={{ flex:1, minWidth:0 }}>
          <div style={{ color:dotColor, fontSize:11, fontWeight:700 }}>
            AS{selectedASN ?? pathSrcASN}
          </div>
          <div style={{ color:'#8fc4dc', fontSize:'8px', overflow:'hidden',
            textOverflow:'ellipsis', whiteSpace:'nowrap' as const }}>
            {displayName}{displayCountry !== '?' ? ` · ${displayCountry}` : ''}
          </div>
        </div>
        <button onClick={dismiss}
          style={{ background:'none', border:'1px solid #0d2035', borderRadius:3,
            color:'#7ab8d4', padding:'2px 6px', cursor:'pointer',
            fontFamily:'inherit', flexShrink:0 }}>x</button>
      </div>
      {tabBar('8px', 28)}
      {tabContent}
    </div>
  )
}
