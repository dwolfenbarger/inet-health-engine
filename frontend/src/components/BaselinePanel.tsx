// src/components/BaselinePanel.tsx — z-scores, health gauge, bucket chart
import { useRef, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useNOCStore } from '../store/nocStore'

// Canvas sparkline for the update-rate bucket chart
function BucketChart({ buckets }: { buckets: any[] }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current; if (!c || buckets.length < 2) return
    const W = c.clientWidth || 400, H = 80
    c.width = W; c.height = H
    const ctx = c.getContext('2d')!
    ctx.clearRect(0, 0, W, H)
    const vals = buckets.map(b => b.updates)
    const mn = Math.min(...vals), mx = Math.max(...vals), range = mx - mn || 1
    const barW = W / buckets.length

    buckets.forEach((b, i) => {
      const h = ((b.updates - mn) / range) * (H - 8) + 4
      const x = i * barW
      // Colour by withdrawal ratio
      const wdRatio = b.withdrawals / Math.max(b.updates, 1)
      const col = wdRatio > 0.3 ? '#ff3b3b' : wdRatio > 0.1 ? '#ffaa00' : '#00ccee'
      ctx.fillStyle = col + '55'
      ctx.fillRect(x + 1, H - h, barW - 2, h)
      ctx.fillStyle = col
      ctx.fillRect(x + 1, H - h, barW - 2, 2)
    })
  }, [buckets])
  return <canvas ref={ref} style={{ display:'block', width:'100%', height:80 }} />
}

// Z-score bar: normalised so ±4σ fills the bar, centre-anchored
function ZBar({ z, color }: { z: number; color: string }) {
  const MAX_Z = 4
  const clamped = Math.max(-MAX_Z, Math.min(MAX_Z, z))
  const positive = clamped >= 0
  const pct = Math.round(Math.abs(clamped) / MAX_Z * 50)  // 0–50% of half-bar
  return (
    <div style={{ height:6, background:'#0a1828', borderRadius:3,
      position:'relative', display:'flex', alignItems:'center' }}>
      {/* centre line */}
      <div style={{ position:'absolute', left:'50%', top:0, width:1,
        height:'100%', background:'#6aa8c0' }} />
      {/* fill */}
      <div style={{
        position:'absolute',
        left:  positive ? '50%' : `${50 - pct}%`,
        width: `${pct}%`,
        height:'100%', background:color, borderRadius:3,
        boxShadow: Math.abs(z) > 2.5 ? `0 0 4px ${color}` : 'none',
      }} />
    </div>
  )
}

export function BaselinePanel() {
  const healthScore = useNOCStore(s => s.healthScore)
  const hs  = healthScore ?? 0
  const hsc = hs >= 80 ? '#00ee88' : hs >= 60 ? '#ffaa00' : '#ff3b3b'

  const { data: zs }   = useQuery({ queryKey:['anomalyZscores'], refetchInterval:60000,
    queryFn: () => api.anomalyZscores() })
  const { data: base } = useQuery({ queryKey:['baseline'], refetchInterval:60000,
    queryFn: () => api.baseline(2) })

  const buckets: any[] = base?.buckets ?? []
  const zsArr: any[]   = zs?.anomaly_zscores ?? []
  const summary        = base?.summary

  const zColor = (z: number) =>
    Math.abs(z) > 3.5 ? '#ff3b3b' :
    Math.abs(z) > 2.0 ? '#ffaa00' : '#00ccee'

  return (
    <div style={{ padding:16, overflowY:'auto', height:'100%', fontFamily:'monospace',
      display:'flex', flexDirection:'column', gap:14 }}>

      <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.12em' }}>
        BASELINE MODELING · Statistical anomaly detection
      </div>

      {/* Health score gauge */}
      <div style={{ background:'#06111e', border:`1px solid ${hsc}44`,
        borderRadius:6, padding:16, textAlign:'center' }}>
        <div style={{ color:hsc, fontSize:52, fontWeight:700, lineHeight:1,
          fontFamily:'monospace', textShadow:`0 0 18px ${hsc}66` }}>
          {hs > 0 ? hs.toFixed(1) : '—'}
        </div>
        <div style={{ color:'#4a9fc8', fontSize:'9px', marginTop:4 }}>GLOBAL HEALTH SCORE</div>
        {/* Gauge bar */}
        <div style={{ marginTop:10, height:6, background:'#0a1828', borderRadius:3 }}>
          <div style={{ width:`${Math.min(hs, 100)}%`, height:'100%',
            background: `linear-gradient(90deg, #ff3b3b, #ffaa00 50%, #00ee88)`,
            borderRadius:3, transition:'width .6s ease' }} />
        </div>
        <div style={{ display:'flex', justifyContent:'space-between', marginTop:3 }}>
          <span style={{ color:'#ff3b3b', fontSize:'7px' }}>0 CRITICAL</span>
          <span style={{ color:'#ffaa00', fontSize:'7px' }}>50</span>
          <span style={{ color:'#00ee88', fontSize:'7px' }}>100 HEALTHY</span>
        </div>
      </div>

      {/* Z-score bars */}
      {zsArr.length > 0 && (
        <div style={{ background:'#06111e', border:'1px solid #0d2035', borderRadius:5, padding:12 }}>
          <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:10 }}>
            ANOMALY Z-SCORES (±4σ scale)
          </div>
          {zsArr.map((r: any) => {
            const col = zColor(r.z_score)
            return (
              <div key={r.event_type} style={{ marginBottom:10 }}>
                <div style={{ display:'flex', justifyContent:'space-between', marginBottom:3 }}>
                  <span style={{ color:'#8fc4dc', fontSize:'9px' }}>
                    {r.event_type.replace(/_/g, ' ')}
                  </span>
                  <div style={{ display:'flex', gap:8 }}>
                    <span style={{ color:'#6aa8c0', fontSize:'8px' }}>
                      {r.current_rate_ph.toFixed(1)}/h
                    </span>
                    <span style={{ color:col, fontSize:'9px', fontWeight:700 }}>
                      {r.z_score > 0 ? '+' : ''}{r.z_score.toFixed(2)}σ
                    </span>
                  </div>
                </div>
                <ZBar z={r.z_score} color={col} />
              </div>
            )
          })}
          <div style={{ display:'flex', justifyContent:'space-between', marginTop:6 }}>
            <span style={{ color:'#6aa8c0', fontSize:'7px' }}>−4σ</span>
            <span style={{ color:'#6aa8c0', fontSize:'7px' }}>baseline</span>
            <span style={{ color:'#6aa8c0', fontSize:'7px' }}>+4σ</span>
          </div>
        </div>
      )}

      {/* Update rate chart */}
      {buckets.length > 0 && (
        <div style={{ background:'#06111e', border:'1px solid #0d2035', borderRadius:5, padding:12 }}>
          <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:8 }}>
            UPDATE RATE · LAST 6 HOURS (5-min buckets)
          </div>
          <BucketChart buckets={[...buckets].reverse()} />
          <div style={{ display:'flex', justifyContent:'space-between', marginTop:4 }}>
            <span style={{ color:'#6aa8c0', fontSize:'7px' }}>6h ago</span>
            <span style={{ color:'#6aa8c0', fontSize:'7px' }}>now</span>
          </div>
          <div style={{ display:'flex', gap:12, marginTop:6 }}>
            {[['#ff3b3b','High withdrawal'],['#ffaa00','Moderate'],['#00ccee','Normal']].map(([c,l])=>(
              <div key={l} style={{ display:'flex', alignItems:'center', gap:4 }}>
                <div style={{ width:10, height:4, background:c, borderRadius:1 }} />
                <span style={{ color:'#7ab8d4', fontSize:'7px' }}>{l}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Stats */}
      {summary && (
        <div style={{ background:'#06111e', border:'1px solid #0d2035', borderRadius:5, padding:12 }}>
          <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.1em', marginBottom:8 }}>
            BGP UPDATE STATISTICS
          </div>
          {[
            ['Mean / 5m',   summary.mean_updates_per_5m?.toLocaleString()],
            ['Std dev',     summary.std_dev?.toLocaleString()],
            ['Buckets',     summary.bucket_count],
            ['Window',      `${summary.window_hours}h`],
          ].map(([k, v]) => (
            <div key={k as string} style={{ display:'flex', justifyContent:'space-between', marginBottom:5 }}>
              <span style={{ color:'#7ab8d4', fontSize:'9px' }}>{k}</span>
              <span style={{ color:'#ddeeff', fontSize:'9px' }}>{v}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
