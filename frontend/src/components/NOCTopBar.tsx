// src/components/NOCTopBar.tsx — P4: VENGEANCE label, anomaly breakdown, collector status, UTC clock
import { useEffect, useRef, useState } from 'react'
import { useNOCStore } from '../store/nocStore'
import { api } from '../api/client'

function Sparkline({ values, color, width=90, height=30 }:
  { values:number[]; color:string; width?:number; height?:number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current; if (!c || values.length < 2) return
    const ctx = c.getContext('2d')!
    ctx.clearRect(0, 0, width, height)
    const mn = Math.min(...values), mx = Math.max(...values), range = mx - mn || 1
    const grad = ctx.createLinearGradient(0, 0, 0, height)
    grad.addColorStop(0, color + '55'); grad.addColorStop(1, color + '00')
    ctx.beginPath()
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = height - ((v - mn) / range) * (height - 4) - 2
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.lineTo(width, height); ctx.lineTo(0, height); ctx.closePath()
    ctx.fillStyle = grad; ctx.fill()
    ctx.beginPath()
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * width
      const y = height - ((v - mn) / range) * (height - 4) - 2
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)
    })
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke()
  }, [values, color, width, height])
  return <canvas ref={ref} width={width} height={height} style={{ display:'block' }} />
}

export function NOCTopBar() {
  const { healthScore, anomalies, updateRate1h, wsConnected } = useNOCStore()
  const [sparkVals,  setSparkVals]  = useState<number[]>([])
  const [utcTime,    setUtcTime]    = useState('')
  const [status,     setStatus]     = useState<any>(null)
  const [zscores,    setZscores]    = useState<any>(null)

  // Real health score — null means computing, not zero
  const hs  = healthScore   // null until first compute
  const hsDisplay = hs !== null ? hs!.toFixed(1) : null
  const hsc = hs === null ? '#4a9fc8'
            : hs >= 80    ? '#00ee88'
            : hs >= 60    ? '#ffaa00'
            : '#ff3b3b'

  // Anomaly type breakdown for compact header row
  const flapCnt   = anomalies.filter(a => a.event_type === 'bgp_flap').length
  const hijackCnt = anomalies.filter(a => a.event_type === 'bgp_hijack').length
  const surgeCnt  = anomalies.filter(a => a.event_type === 'withdrawal_surge').length
  const hiSev     = anomalies.filter(a => a.severity >= 4).length

  // UTC clock — ticks every second
  useEffect(() => {
    const tick = () => setUtcTime(new Date().toUTCString().slice(17, 25))
    tick()
    const iv = setInterval(tick, 1000)
    return () => clearInterval(iv)
  }, [])

  // Sparkline from real baseline buckets
  useEffect(() => {
    const load = () =>
      api.baseline(2).then((d: any) => {
        const vals = (d.buckets ?? []).slice(0, 12).reverse()
          .map((b: any) => b.updates as number)
        if (vals.length >= 2) setSparkVals(vals)
      }).catch(() => {})
    load()
    const iv = setInterval(load, 60000)
    return () => clearInterval(iv)
  }, [])

  // Collector + data-source status
  useEffect(() => {
    const load = () =>
      api.status().then(d => setStatus(d)).catch(() => {})
    load()
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [])

  // Z-scores for baseline context
  useEffect(() => {
    const load = () =>
      api.healthScore().then((d: any) => {
        if (d?.z_scores) setZscores(d.z_scores)
      }).catch(() => {})
    load()
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [])

  const dataSources: Record<string, string> = status?.data_sources ?? {}
  
  return (
    <div style={{ flexShrink:0, background:'#040d1a', borderBottom:'1px solid #0d2035' }}>
      <div style={{ display:'flex', alignItems:'center', gap:10, padding:'6px 14px', flexWrap:'wrap' }}>

        {/* ── Logo ───────────────────────────────────────────────── */}
        <div style={{ display:'flex', alignItems:'center', gap:8, flexShrink:0 }}>
          <div style={{ width:30, height:30, borderRadius:'50%',
            border:`1px solid ${hsc}55`, background:`radial-gradient(circle,${hsc}18,transparent)`,
            display:'flex', alignItems:'center', justifyContent:'center' }}>
            <div style={{ width:7, height:7, borderRadius:'50%',
              background:hsc, boxShadow:`0 0 8px ${hsc}` }} />
          </div>
          <div>
            <div style={{ color:'#00ccee', fontSize:12, letterSpacing:'.2em',
              fontWeight:700, fontFamily:'monospace' }}>INET·HEALTH</div>
            <div style={{ color:'#4a7090', fontSize:'7px', letterSpacing:'.12em',
              fontFamily:'monospace' }}>NETWORK INTELLIGENCE · VENGEANCE</div>
          </div>
        </div>

        {/* ── Health score + sparkline ────────────────────────── */}
        <div style={{ display:'flex', alignItems:'center', gap:10,
          background:`${hsc}0e`, border:`1px solid ${hsc}33`,
          borderRadius:5, padding:'4px 12px', flexShrink:0 }}>
          <div>
            {hsDisplay !== null ? (
              <>
                <div style={{ color:hsc, fontSize:22, fontWeight:700, lineHeight:1,
                  fontFamily:'monospace', textShadow:`0 0 14px ${hsc}66` }}>
                  {hsDisplay}
                </div>
                <div style={{ color:'#8fc4dc', fontSize:'7px', letterSpacing:'.1em' }}>HEALTH</div>
              </>
            ) : (
              <>
                <div style={{ color:'#4a9fc8', fontSize:12, fontWeight:700,
                  fontFamily:'monospace', letterSpacing:'.1em' }}>—</div>
                <div style={{ color:'#4a7090', fontSize:'7px', letterSpacing:'.08em' }}>COMPUTING</div>
              </>
            )}
          </div>
          {sparkVals.length >= 2
            ? <Sparkline values={sparkVals} color={hsc} />
            : <div style={{ width:90, height:30, display:'flex', alignItems:'center',
                justifyContent:'center', color:'#4a6070', fontSize:'7px' }}>loading…</div>
          }
          {/* Z-score context — shows what's driving the score */}
          {zscores && (
            <div style={{ borderLeft:'1px solid #0d2035', paddingLeft:10 }}>
              {[
                { k:'update_rate',    label:'UPD' },
                { k:'withdrawal_rate',label:'WDR' },
              ].map(({ k, label }) => {
                const z = zscores[k] ?? 0
                const zc = Math.abs(z) > 3 ? '#ff3b3b' : Math.abs(z) > 1.5 ? '#ffaa00' : '#00ee88'
                return (
                  <div key={k} style={{ display:'flex', gap:5, alignItems:'center', marginBottom:1 }}>
                    <span style={{ color:'#4a7090', fontSize:'7px', width:26 }}>{label}</span>
                    <span style={{ color:zc, fontSize:'8px', fontWeight:700, fontFamily:'monospace' }}>
                      {z > 0 ? '+' : ''}{z.toFixed(1)}σ
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        {/* ── Anomaly breakdown ──────────────────────────────── */}
        <div style={{ background:'#06101c', border:'1px solid #0d2035',
          borderRadius:4, padding:'4px 10px', flexShrink:0 }}>
          <div style={{ color:'#4a7090', fontSize:'7px', letterSpacing:'.1em', marginBottom:3 }}>
            ANOMALIES
          </div>
          <div style={{ display:'flex', gap:8, alignItems:'center' }}>
            {flapCnt > 0 && (
              <span style={{ color:'#ff3b3b', fontSize:'9px', fontWeight:700,
                fontFamily:'monospace' }}>F:{flapCnt}</span>
            )}
            {hijackCnt > 0 && (
              <span style={{ color:'#ffdd00', fontSize:'9px', fontWeight:700,
                fontFamily:'monospace' }}>H:{hijackCnt}</span>
            )}
            {surgeCnt > 0 && (
              <span style={{ color:'#ff8800', fontSize:'9px', fontWeight:700,
                fontFamily:'monospace' }}>S:{surgeCnt}</span>
            )}
            {flapCnt === 0 && hijackCnt === 0 && surgeCnt === 0 && (
              <span style={{ color:'#2a5060', fontSize:'9px', fontFamily:'monospace' }}>—</span>
            )}
            {hiSev > 0 && (
              <span style={{ color:'#ff3b3b', fontSize:'8px', background:'#ff3b3b18',
                border:'1px solid #ff3b3b44', borderRadius:2, padding:'0 4px' }}>
                {hiSev} S4+
              </span>
            )}
          </div>
        </div>

        {/* ── Data source health ─────────────────────────────── */}
        <div style={{ background:'#06101c', border:'1px solid #0d2035',
          borderRadius:4, padding:'4px 10px', flexShrink:0 }}>
          <div style={{ color:'#4a7090', fontSize:'7px', letterSpacing:'.1em', marginBottom:3 }}>
            DATA SOURCES
          </div>
          <div style={{ display:'flex', gap:6, alignItems:'center' }}>
            {Object.entries(dataSources).map(([name, state]) => {
              const ok = state === 'healthy'
              const short = name === 'timescaledb' ? 'TSDB'
                          : name === 'elasticsearch' ? 'ES'
                          : name.slice(0,5).toUpperCase()
              return (
                <div key={name} style={{ display:'flex', alignItems:'center', gap:3 }}>
                  <div style={{ width:4, height:4, borderRadius:'50%',
                    background: ok ? '#00ee88' : '#ff3b3b',
                    boxShadow: ok ? '0 0 4px #00ee88' : '0 0 4px #ff3b3b' }} />
                  <span style={{ color: ok ? '#4a8060' : '#ff3b3b',
                    fontSize:'7px', fontFamily:'monospace' }}>{short}</span>
                </div>
              )
            })}
            {Object.keys(dataSources).length === 0 && (
              <span style={{ color:'#2a4050', fontSize:'7px' }}>—</span>
            )}
          </div>
        </div>

        <div style={{ flex:1 }} />

        {/* ── UPD/H pill ─────────────────────────────────────── */}
        <div style={{ background:'#00ccee0e', border:'1px solid #00ccee2a',
          borderRadius:4, padding:'4px 10px', textAlign:'center', flexShrink:0 }}>
          <div style={{ color:'#00ccee', fontSize:13, fontWeight:700, fontFamily:'monospace' }}>
            {updateRate1h > 0 ? `${(updateRate1h/1000).toFixed(0)}K` : '—'}
          </div>
          <div style={{ color:'#4a7090', fontSize:'7px', fontFamily:'monospace' }}>UPD/H</div>
        </div>

        {/* ── UTC clock + WS status ──────────────────────────── */}
        <div style={{ textAlign:'right', flexShrink:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:6, justifyContent:'flex-end',
            marginBottom:2 }}>
            <div style={{ width:5, height:5, borderRadius:'50%',
              background: wsConnected ? '#00ee88' : '#ff3b3b',
              boxShadow: wsConnected ? '0 0 6px #00ee88' : 'none' }} />
            <span style={{ color: wsConnected ? '#00ee88' : '#ff3b3b',
              fontSize:'8px', fontFamily:'monospace', fontWeight:700 }}>
              {wsConnected ? 'LIVE' : 'OFFLINE'}
            </span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:4, justifyContent:'flex-end' }}>
            <span style={{ color:'#4a7090', fontSize:'7px', fontFamily:'monospace',
              letterSpacing:'.06em' }}>UTC</span>
            <span style={{ color:'#8fc4dc', fontSize:'10px', fontFamily:'monospace',
              letterSpacing:'.08em', fontWeight:700 }}>{utcTime}</span>
          </div>
        </div>
      </div>

    </div>
  )
}
