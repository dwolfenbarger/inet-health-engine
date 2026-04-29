// src/components/VitalsStrip.tsx — persistent bottom bar: health score, z-scores, BGP rate, anomaly counts
import { useEffect, useState } from 'react'
import { useNOCStore } from '../store/nocStore'
import { api } from '../api/client'

export function VitalsStrip() {
  const { healthScore, anomalies, updateRate1h, wsConnected } = useNOCStore()
  const [zscores, setZscores] = useState<any>(null)
  const collectorCount = 5

  useEffect(() => {
    const load = () =>
      api.healthScore().then((d: any) => {
        if (d?.z_scores) setZscores(d.z_scores)
      }).catch(() => {})
    load()
    const iv = setInterval(load, 30000)
    return () => clearInterval(iv)
  }, [])

  const hs  = healthScore
  const hsc = hs === null ? '#4a9fc8' : hs >= 80 ? '#00ee88' : hs >= 60 ? '#ffaa00' : '#ff3b3b'

  const flapCnt   = anomalies.filter(a => a.event_type === 'bgp_flap').length
  const hijackCnt = anomalies.filter(a => a.event_type === 'bgp_hijack').length
  const surgeCnt  = anomalies.filter(a => a.event_type === 'withdrawal_surge').length
  const hiSev     = anomalies.filter(a => a.severity >= 4).length

  const ZBadge = ({ label, z }: { label: string; z: number | null }) => {
    if (z === null || z === undefined) return null
    const abs = Math.abs(z)
    const col = abs > 3 ? '#ff3b3b' : abs > 1.5 ? '#ffaa00' : '#00ee88'
    const sign = z > 0 ? '+' : ''
    return (
      <div style={{ display:'flex', alignItems:'center', gap:4,
        background:`${col}0e`, border:`1px solid ${col}22`,
        borderRadius:3, padding:'2px 7px' }}>
        <span style={{ color:'#4a7090', fontSize:7, letterSpacing:'.06em' }}>{label}</span>
        <span style={{ color:col, fontSize:9, fontWeight:700, fontFamily:'monospace' }}>
          {sign}{z.toFixed(1)}σ
        </span>
      </div>
    )
  }

  return (
    <div style={{ height:34, flexShrink:0, background:'#030c18',
      borderTop:'1px solid #0d2035', display:'flex',
      alignItems:'center', gap:8, padding:'0 12px',
      fontFamily:'monospace', overflowX:'auto', overflowY:'hidden' }}>

      {/* Health score pill */}
      <div style={{ display:'flex', alignItems:'center', gap:6,
        background:`${hsc}0e`, border:`1px solid ${hsc}33`,
        borderRadius:4, padding:'3px 10px', flexShrink:0 }}>
        <div style={{ width:6, height:6, borderRadius:'50%',
          background:hsc, boxShadow:`0 0 6px ${hsc}` }} />
        <span style={{ color:'#5a8090', fontSize:7, letterSpacing:'.08em' }}>HEALTH</span>
        <span style={{ color:hsc, fontSize:13, fontWeight:700, lineHeight:1 }}>
          {hs !== null ? hs!.toFixed(1) : '—'}
        </span>
      </div>

      {/* Z-scores */}
      {zscores && (
        <>
          <ZBadge label="UPD" z={zscores.update_rate} />
          <ZBadge label="WDR" z={zscores.withdrawal_rate} />
        </>
      )}

      <div style={{ width:1, height:20, background:'#0d2035', flexShrink:0 }} />

      {/* BGP rate */}
      <div style={{ display:'flex', alignItems:'center', gap:4, flexShrink:0 }}>
        <span style={{ color:'#3a6070', fontSize:7 }}>UPD/H</span>
        <span style={{ color:'#00ccee', fontSize:10, fontWeight:700 }}>
          {updateRate1h > 0 ? `${(updateRate1h/1000).toFixed(0)}K` : '—'}
        </span>
      </div>

      {/* Collectors */}
      <div style={{ display:'flex', alignItems:'center', gap:4, flexShrink:0 }}>
        <span style={{ color:'#3a6070', fontSize:7 }}>RIS</span>
        <span style={{ color:'#00aaff', fontSize:10, fontWeight:700 }}>{collectorCount}</span>
      </div>

      <div style={{ width:1, height:20, background:'#0d2035', flexShrink:0 }} />

      {/* Anomaly type breakdown */}
      {flapCnt > 0   && <span style={{ color:'#ff3b3b', fontSize:9, fontWeight:700 }}>F:{flapCnt}</span>}
      {hijackCnt > 0 && <span style={{ color:'#ffdd00', fontSize:9, fontWeight:700 }}>H:{hijackCnt}</span>}
      {surgeCnt > 0  && <span style={{ color:'#ff8800', fontSize:9, fontWeight:700 }}>S:{surgeCnt}</span>}
      {hiSev > 0     && (
        <span style={{ color:'#ff3b3b', fontSize:8, background:'#ff3b3b18',
          border:'1px solid #ff3b3b44', borderRadius:2, padding:'0 5px' }}>
          {hiSev} S4+
        </span>
      )}
      {flapCnt === 0 && hijackCnt === 0 && surgeCnt === 0 && (
        <span style={{ color:'#00ee88', fontSize:8 }}>✓ nominal</span>
      )}

      <div style={{ flex:1 }} />

      {/* WS indicator */}
      <div style={{ display:'flex', alignItems:'center', gap:4, flexShrink:0 }}>
        <div style={{ width:5, height:5, borderRadius:'50%',
          background: wsConnected ? '#00ee88' : '#ff3b3b',
          boxShadow: wsConnected ? '0 0 5px #00ee88' : 'none' }} />
        <span style={{ color: wsConnected ? '#2a6040' : '#602a2a', fontSize:7 }}>
          {wsConnected ? 'STREAM LIVE' : 'OFFLINE'}
        </span>
      </div>
    </div>
  )
}
