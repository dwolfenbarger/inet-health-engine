// src/components/MobileShell.tsx — full mobile layout (≤768px)
// Architecture: compact topbar → 3-tab nav → full-width content → bottom vitals
import { useState, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNOCStore } from '../store/nocStore'
import { eventColor, eventIcon, severityColor } from '../lib/asData'
import { api } from '../api/client'
import { GlobeView } from './GlobeView'
import { ASSidebar } from './ASSidebar'
import { EventFeed } from './EventFeed'

type MobileTab = 'globe' | 'events' | 'status'

// ── Compact topbar ───────────────────────────────────────────────────────────
function MobileTopBar({ tab, setTab }: { tab: MobileTab; setTab: (t: MobileTab) => void }) {
  const { healthScore, wsConnected, anomalies } = useNOCStore()
  const hs  = healthScore
  const hsc = hs === null ? '#4a9fc8' : hs >= 80 ? '#00ee88' : hs >= 60 ? '#ffaa00' : '#ff3b3b'
  const hiSev = anomalies.filter(a => a.severity >= 4).length

  return (
    <div style={{ background:'#040d1a', borderBottom:'1px solid #0d2035', flexShrink:0 }}>
      {/* Title row */}
      <div style={{ display:'flex', alignItems:'center', padding:'8px 14px', gap:10 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, flex:1 }}>
          <div style={{ width:22, height:22, borderRadius:'50%',
            border:`1px solid ${hsc}55`, background:`radial-gradient(circle,${hsc}18,transparent)`,
            display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0 }}>
            <div style={{ width:6, height:6, borderRadius:'50%',
              background:hsc, boxShadow:`0 0 6px ${hsc}` }} />
          </div>
          <span style={{ color:'#00ccee', fontSize:13, fontWeight:700,
            letterSpacing:'.15em', fontFamily:'monospace' }}>INET·HEALTH</span>
        </div>
        {/* Health pill */}
        <div style={{ display:'flex', alignItems:'center', gap:6,
          background:`${hsc}12`, border:`1px solid ${hsc}33`,
          borderRadius:4, padding:'3px 10px' }}>
          <span style={{ color:hsc, fontSize:16, fontWeight:700,
            fontFamily:'monospace', lineHeight:1 }}>
            {hs !== null ? hs!.toFixed(0) : '—'}
          </span>
          <span style={{ color:`${hsc}88`, fontSize:8 }}>HEALTH</span>
        </div>
        {/* WS + hi-sev */}
        <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-end', gap:2 }}>
          <div style={{ display:'flex', alignItems:'center', gap:4 }}>
            <div style={{ width:5, height:5, borderRadius:'50%',
              background: wsConnected ? '#00ee88' : '#ff3b3b',
              boxShadow: wsConnected ? '0 0 5px #00ee88' : 'none' }} />
            <span style={{ color: wsConnected ? '#00ee88' : '#ff3b3b',
              fontSize:8, fontFamily:'monospace' }}>
              {wsConnected ? 'LIVE' : 'OFFLINE'}
            </span>
          </div>
          {hiSev > 0 && (
            <span style={{ color:'#ff3b3b', fontSize:8, background:'#ff3b3b18',
              border:'1px solid #ff3b3b44', borderRadius:2, padding:'0 5px' }}>
              {hiSev} S4+
            </span>
          )}
        </div>
      </div>

      {/* Tab bar — large tap targets */}
      <div style={{ display:'flex', borderTop:'1px solid #071828' }}>
        {([
          { id:'globe'  as const, icon:'🌐', label:'GLOBE'  },
          { id:'events' as const, icon:'⚡', label:'EVENTS' },
          { id:'status' as const, icon:'📊', label:'STATUS' },
        ] as const).map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            flex:1, height:44, display:'flex', flexDirection:'column',
            alignItems:'center', justifyContent:'center', gap:2,
            background:   tab === t.id ? '#071420' : 'transparent',
            border:       'none',
            borderBottom: tab === t.id ? '2px solid #00ccee' : '2px solid transparent',
            color:        tab === t.id ? '#00ccee' : '#4a7090',
            cursor:'pointer', fontFamily:'monospace',
          }}>
            <span style={{ fontSize:16 }}>{t.icon}</span>
            <span style={{ fontSize:7, letterSpacing:'.1em' }}>{t.label}</span>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── Mobile status view ───────────────────────────────────────────────────────
function MobileStatusView() {
  const { healthScore, anomalies, updateRate1h } = useNOCStore()
  const [zscores, setZscores] = useState<any>(null)
  const [status,  setStatus]  = useState<any>(null)

  useEffect(() => {
    api.healthScore().then((d: any) => { if (d?.z_scores) setZscores(d.z_scores) }).catch(() => {})
    api.status().then(d => setStatus(d)).catch(() => {})
  }, [])

  const { data: lifecycle } = useQuery({
    queryKey: ['mobileLifecycle'],
    queryFn:  () => api.eventLifecycle('escalated', 10),
    refetchInterval: 30000,
  })

  const hs  = healthScore
  const hsc = hs === null ? '#4a9fc8' : hs >= 80 ? '#00ee88' : hs >= 60 ? '#ffaa00' : '#ff3b3b'
  const escalated = lifecycle?.events ?? []
  const flapCnt   = anomalies.filter(a => a.event_type === 'bgp_flap').length
  const hijackCnt = anomalies.filter(a => a.event_type === 'bgp_hijack').length
  const surgeCnt  = anomalies.filter(a => a.event_type === 'withdrawal_surge').length

  return (
    <div style={{ overflowY:'auto', padding:'14px', fontFamily:'monospace' }}>

      {/* Big health score */}
      <div style={{ background:`${hsc}0e`, border:`1px solid ${hsc}33`,
        borderRadius:8, padding:'20px', textAlign:'center', marginBottom:14 }}>
        <div style={{ color:hsc, fontSize:56, fontWeight:700,
          lineHeight:1, textShadow:`0 0 20px ${hsc}55`, fontFamily:'monospace' }}>
          {hs !== null ? hs!.toFixed(1) : '—'}
        </div>
        <div style={{ color:`${hsc}88`, fontSize:10, letterSpacing:'.2em', marginTop:6 }}>
          INTERNET HEALTH SCORE
        </div>
        {zscores && (
          <div style={{ display:'flex', justifyContent:'center', gap:14, marginTop:10 }}>
            {[
              { label:'UPD', z: zscores.update_rate },
              { label:'WDR', z: zscores.withdrawal_rate },
            ].map(({ label, z }) => {
              const c = Math.abs(z) > 3 ? '#ff3b3b' : Math.abs(z) > 1.5 ? '#ffaa00' : '#00ee88'
              return (
                <div key={label} style={{ display:'flex', gap:5, alignItems:'center' }}>
                  <span style={{ color:'#4a7090', fontSize:9 }}>{label}</span>
                  <span style={{ color:c, fontSize:11, fontWeight:700 }}>
                    {z > 0 ? '+' : ''}{z.toFixed(1)}σ
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Anomaly type breakdown */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr',
        gap:8, marginBottom:14 }}>
        {[
          { label:'FLAPS',   count:flapCnt,   color:'#ff3b3b' },
          { label:'HIJACKS', count:hijackCnt, color:'#ffdd00' },
          { label:'SURGES',  count:surgeCnt,  color:'#ff8800' },
        ].map(p => (
          <div key={p.label} style={{ background:`${p.color}0e`,
            border:`1px solid ${p.color}33`, borderRadius:6,
            padding:'12px 8px', textAlign:'center' }}>
            <div style={{ color: p.count > 0 ? p.color : `${p.color}44`,
              fontSize:22, fontWeight:700, lineHeight:1 }}>{p.count}</div>
            <div style={{ color:'#4a7090', fontSize:8, marginTop:3,
              letterSpacing:'.06em' }}>{p.label}</div>
          </div>
        ))}
      </div>

      {/* Data sources */}
      {status?.data_sources && (
        <div style={{ background:'#06101c', border:'1px solid #0d2035',
          borderRadius:6, padding:'12px', marginBottom:14 }}>
          <div style={{ color:'#4a9fc8', fontSize:9, letterSpacing:'.1em', marginBottom:8 }}>
            DATA SOURCES
          </div>
          {Object.entries(status.data_sources).map(([name, state]: any) => (
            <div key={name} style={{ display:'flex', justifyContent:'space-between',
              alignItems:'center', padding:'6px 0',
              borderBottom:'1px solid #0a1828' }}>
              <span style={{ color:'#8fc4dc', fontSize:10, textTransform:'uppercase' as const }}>
                {name}
              </span>
              <span style={{ color: state === 'healthy' ? '#00ee88' : '#ff3b3b',
                fontSize:10, fontWeight:700 }}>
                {state === 'healthy' ? '● OK' : '● DOWN'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Top escalated events */}
      {escalated.length > 0 && (
        <div>
          <div style={{ color:'#ff3b3b', fontSize:9, letterSpacing:'.1em', marginBottom:8 }}>
            ESCALATED ({escalated.length})
          </div>
          {escalated.slice(0,5).map((e: any, i: number) => {
            const ec = eventColor(e.event_type)
            return (
              <div key={i} style={{ background:'#06101c',
                borderLeft:`3px solid ${ec}`, borderRadius:3,
                padding:'10px 12px', marginBottom:6 }}>
                <div style={{ display:'flex', gap:6, alignItems:'center', marginBottom:4 }}>
                  <span style={{ fontSize:13 }}>{eventIcon(e.event_type)}</span>
                  <span style={{ color:ec, fontSize:10, fontWeight:700, flex:1 }}>
                    {e.event_type.replace(/_/g,' ').toUpperCase()}
                  </span>
                  <span style={{ color:severityColor(e.peak_severity), fontSize:10,
                    fontWeight:700 }}>S{e.peak_severity}</span>
                </div>
                <div style={{ color:'#ddeeff', fontSize:10 }}>
                  {e.origin_name ?? `AS${e.origin_asn}`}
                  {e.origin_country ? ` · ${e.origin_country}` : ''}
                </div>
                {e.affected_prefix && (
                  <div style={{ color:'#6a9ab0', fontSize:9, fontFamily:'monospace',
                    marginTop:2 }}>{e.affected_prefix}</div>
                )}
                <div style={{ color:'#4a7090', fontSize:8, marginTop:4 }}>
                  {e.duration_human} · {e.occurrence_count}× ·{' '}
                  {(e.peak_confidence*100).toFixed(0)}% conf
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* BGP rate */}
      <div style={{ background:'#06101c', border:'1px solid #0d2035',
        borderRadius:6, padding:'12px', marginTop:8 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
          <span style={{ color:'#4a7090', fontSize:9 }}>BGP UPDATES / HOUR</span>
          <span style={{ color:'#00ccee', fontSize:16, fontWeight:700, fontFamily:'monospace' }}>
            {updateRate1h > 0 ? `${(updateRate1h/1000).toFixed(0)}K` : '—'}
          </span>
        </div>
      </div>
    </div>
  )
}

// ── Mobile bottom vitals strip ───────────────────────────────────────────────

// Mobile globe layer controls — floating overlay on the globe
// Interactive legend: tap to toggle layers on/off
// Settings button opens a compact filter drawer
function MobileGlobeControls() {
  const { controls, setControl } = useNOCStore()
  const [showSettings, setShowSettings] = useState(false)
  const c = controls

  const layers = [
    { key: 'showFlaps', label: 'FLAP',  color: '#ff3b3b' },
    { key: 'showHijacks', label: 'HIJCK', color: '#ffdd00' },
    { key: 'showSurges', label: 'SURGE', color: '#ff8800' },
    { key: 'showRPKI', label: 'RPKI',  color: '#00ee88' },
    { key: 'showFiber', label: 'CABLE', color: '#00ffcc' },
  ] as const

  return (
    <>
      {/* Layer toggle bar — bottom left above legend area */}
      <div style={{
        position: 'absolute', bottom: 52, left: 8, right: 8,
        display: 'flex', gap: 6, zIndex: 20,
        pointerEvents: 'auto', flexWrap: 'wrap',
      }}>
        {layers.map(({ key, label, color }) => {
          const active = c[key as keyof typeof c] as boolean
          return (
            <button
              key={key}
              onClick={() => setControl(key as any, !active)}
              style={{
                width: 48, height: 48,
                background: active ? `${color}22` : 'rgba(3,8,16,0.82)',
                border: `1.5px solid ${active ? color : '#1a3a50'}`,
                borderRadius: 10,
                display: 'flex', flexDirection: 'column',
                alignItems: 'center', justifyContent: 'center', gap: 2,
                cursor: 'pointer', backdropFilter: 'blur(6px)',
                opacity: active ? 1 : 0.45,
                transition: 'all .18s',
              }}>
              <div style={{ width:8, height:8, borderRadius:'50%', background: active ? color : '#2a4050', boxShadow: active ? `0 0 5px ${color}` : 'none', marginBottom:1 }} />
              <span style={{
                color: active ? color : '#4a6070',
                fontSize: 7, fontFamily: 'monospace', letterSpacing: '.05em',
              }}>{label}</span>
            </button>
          )
        })}

        {/* Settings button */}
        <button
          onClick={() => setShowSettings(s => !s)}
          style={{
            width: 48, height: 48,
            background: showSettings ? '#00ccee22' : 'rgba(3,8,16,0.82)',
            border: `1.5px solid ${showSettings ? '#00ccee' : '#1a3a50'}`,
            borderRadius: 10,
            display: 'flex', flexDirection: 'column',
            alignItems: 'center', justifyContent: 'center', gap: 2,
            cursor: 'pointer', backdropFilter: 'blur(6px)',
            transition: 'all .18s',
          }}>
          <span style={{ fontSize: 12, lineHeight: 1, color: showSettings ? '#00ccee' : '#4a6070' }}>SET</span>
          <span style={{
            color: showSettings ? '#00ccee' : '#4a6070',
            fontSize: 7, fontFamily: 'monospace',
          }}>FILTER</span>
        </button>
      </div>

      {/* Settings drawer — slides up from bottom when open */}
      {showSettings && (
        <>
          <div
            onClick={() => setShowSettings(false)}
            style={{ position:'absolute', inset:0, zIndex:21 }} />
          <div style={{
            position: 'absolute', bottom: 110, left: 8, right: 8, zIndex: 22,
            background: 'rgba(5,16,30,0.96)', border: '1px solid #0d2035',
            borderRadius: 12, padding: '14px 16px',
            backdropFilter: 'blur(12px)',
            boxShadow: '0 -4px 24px rgba(0,0,0,0.6)',
            fontFamily: 'monospace',
            pointerEvents: 'auto',
          }}>
            <div style={{ color:'#4a9fc8', fontSize:9, letterSpacing:'.12em', marginBottom:12 }}>
              GLOBE FILTERS
            </div>

            {/* Min Severity */}
            <div style={{ marginBottom:12 }}>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
                <span style={{ color:'#8fc4dc', fontSize:10 }}>Min Severity</span>
                <span style={{ color:'#00ccee', fontSize:10, fontFamily:'monospace' }}>
                  S{c.severityMin}
                </span>
              </div>
              <input type="range" min={1} max={5} step={1}
                value={c.severityMin}
                onChange={e => setControl('severityMin', Number(e.target.value) as any)}
                style={{ width:'100%', accentColor:'#00ccee', height:4 }} />
              <div style={{ display:'flex', justifyContent:'space-between', marginTop:2 }}>
                <span style={{ color:'#3a5a70', fontSize:8 }}>S1</span>
                <span style={{ color:'#3a5a70', fontSize:8 }}>S5</span>
              </div>
            </div>

            {/* Min Confidence */}
            <div style={{ marginBottom:12 }}>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:4 }}>
                <span style={{ color:'#8fc4dc', fontSize:10 }}>Min Confidence</span>
                <span style={{ color:'#00ccee', fontSize:10, fontFamily:'monospace' }}>
                  {Math.round(c.confidenceMin * 100)}%
                </span>
              </div>
              <input type="range" min={0} max={100} step={5}
                value={Math.round(c.confidenceMin * 100)}
                onChange={e => setControl('confidenceMin', Number(e.target.value) / 100)}
                style={{ width:'100%', accentColor:'#00ccee', height:4 }} />
            </div>

            {/* Toggles row */}
            <div style={{ display:'flex', gap:8 }}>
              {[
                { key:'globeAutoRotate', label:'Auto Rotate' },
                { key:'showLabels',      label:'AS Labels'   },
              ].map(({ key, label }) => {
                const on = c[key as keyof typeof c] as boolean
                return (
                  <button key={key}
                    onClick={() => setControl(key as any, !on)}
                    style={{
                      flex:1, padding:'8px 4px',
                      background: on ? '#00ccee18' : 'rgba(3,8,16,0.6)',
                      border: `1px solid ${on ? '#00ccee55' : '#1a3a50'}`,
                      borderRadius:8, color: on ? '#00ccee' : '#4a6070',
                      fontSize:9, fontFamily:'monospace', cursor:'pointer',
                    }}>
                    {label}<br />
                    <span style={{ fontSize:8, opacity:.7 }}>{on ? 'ON' : 'OFF'}</span>
                  </button>
                )
              })}
            </div>
          </div>
        </>
      )}
    </>
  )
}

function MobileVitals() {
  const { healthScore, anomalies, wsConnected } = useNOCStore()
  const hs  = healthScore
  const hsc = hs === null ? '#4a9fc8' : hs >= 80 ? '#00ee88' : hs >= 60 ? '#ffaa00' : '#ff3b3b'
  const hiSev   = anomalies.filter(a => a.severity >= 4).length
  const flapCnt = anomalies.filter(a => a.event_type === 'bgp_flap').length
  const hijCnt  = anomalies.filter(a => a.event_type === 'bgp_hijack').length

  return (
    <div style={{ height:40, background:'#030c18', borderTop:'1px solid #0d2035',
      display:'flex', alignItems:'center', gap:10, padding:'0 14px',
      fontFamily:'monospace', flexShrink:0, overflowX:'auto' }}>
      <span style={{ color:hsc, fontSize:11, fontWeight:700 }}>
        {hs !== null ? hs!.toFixed(0) : '—'}
      </span>
      <span style={{ color:`${hsc}66`, fontSize:8 }}>HEALTH</span>
      <div style={{ width:1, height:18, background:'#0d2035', flexShrink:0 }} />
      {flapCnt > 0  && <span style={{ color:'#ff3b3b', fontSize:9, fontWeight:700 }}>F:{flapCnt}</span>}
      {hijCnt  > 0  && <span style={{ color:'#ffdd00', fontSize:9, fontWeight:700 }}>H:{hijCnt}</span>}
      {hiSev   > 0  && (
        <span style={{ color:'#ff3b3b', fontSize:8, background:'#ff3b3b18',
          border:'1px solid #ff3b3b44', borderRadius:2, padding:'0 5px' }}>
          {hiSev} S4+
        </span>
      )}
      {flapCnt === 0 && hijCnt === 0 && (
        <span style={{ color:'#00ee88', fontSize:8 }}>✓ nominal</span>
      )}
      <div style={{ flex:1 }} />
      <div style={{ display:'flex', alignItems:'center', gap:4 }}>
        <div style={{ width:5, height:5, borderRadius:'50%',
          background: wsConnected ? '#00ee88' : '#ff3b3b' }} />
        <span style={{ color:'#3a6050', fontSize:7 }}>
          {wsConnected ? 'LIVE' : 'OFFLINE'}
        </span>
      </div>
    </div>
  )
}

// ── Root mobile shell ────────────────────────────────────────────────────────
export function MobileShell() {
  const [tab, setTab] = useState<MobileTab>('events')  // events default — most useful on mobile

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100vh',
      background:'#030810', overflow:'hidden',
      fontFamily:"'JetBrains Mono','Fira Code','Courier New',monospace" }}>

      <MobileTopBar tab={tab} setTab={setTab} />

      {/* Content area — full width, full remaining height */}
      <div style={{ flex:1, overflow:'hidden', position:'relative' }}>

        {/* Globe — always mounted to preserve WebGL, hidden when not active */}
        <div style={{ position:'absolute', inset:0,
          visibility: tab === 'globe' ? 'visible' : 'hidden',
          pointerEvents: tab === 'globe' ? 'auto' : 'none' }}>
          <GlobeView />
          {tab === 'globe' && <MobileGlobeControls />}
        </div>
        {/* ASSidebar renders as bottom sheet on mobile when an ASN is selected */}
        <ASSidebar />

        {/* Events — full-width EventFeed */}
        {tab === 'events' && (
          <div style={{ position:'absolute', inset:0, overflowY:'auto' }}>
            <EventFeed />
          </div>
        )}

        {/* Status dashboard */}
        {tab === 'status' && (
          <div style={{ position:'absolute', inset:0, overflowY:'auto',
            background:'#030810' }}>
            <MobileStatusView />
          </div>
        )}
      </div>

      <MobileVitals />
    </div>
  )
}
