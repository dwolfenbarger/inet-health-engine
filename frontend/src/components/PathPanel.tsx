// src/components/PathPanel.tsx — standalone path analysis panel
import { useNOCStore } from '../store/nocStore'
import { getASMeta } from '../lib/asData'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { AS_META } from '../lib/asData'

export function PathPanel() {
  const { pathSrcASN, pathDstASN, setPathSrc, setPathDst, setPathMode } = useNOCStore()
  const { data, isLoading } = useQuery({
    queryKey: ['path', pathSrcASN, pathDstASN],
    queryFn: () => pathSrcASN && pathDstASN ? api.pathAnalysis(pathSrcASN, pathDstASN) : null,
    enabled: !!(pathSrcASN && pathDstASN),
    staleTime: 30000,
  })

  return (
    <div style={{ padding:16, height:'100%', display:'flex', flexDirection:'column', overflowY:'auto', fontFamily:'monospace' }}>
      <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.12em', marginBottom:14 }}>
        AS PATH ANALYSIS · Neo4j + TimescaleDB
      </div>
      <div style={{ display:'flex', gap:8, marginBottom:14, alignItems:'center' }}>
        {(['src','dst'] as const).map(k => {
          const asn = k==='src' ? pathSrcASN : pathDstASN
          const lbl = k==='src' ? 'SOURCE ASN' : 'DEST ASN'
          return (
            <div key={k} style={{ flex:1 }}>
              <div style={{ color:'#6aa8c0', fontSize:'8px', marginBottom:3 }}>{lbl}</div>
              <input type="number" placeholder="e.g. 13335"
                value={asn ?? ''} onChange={e => {
                  const v = e.target.value ? parseInt(e.target.value) : null
                  k==='src' ? setPathSrc(v) : setPathDst(v)
                }}
                style={{ width:'100%', background:'#06111e', border:'1px solid #0d2035',
                  borderRadius:4, color:'#ddeeff', padding:'6px 8px',
                  fontSize:'11px', fontFamily:'monospace', outline:'none' }} />
            </div>
          )
        })}
        <button onClick={() => { setPathMode(true) }} style={{
          background:'#aa44ff22', border:'1px solid #aa44ff55', borderRadius:4,
          color:'#aa44ff', padding:'6px 12px', cursor:'pointer',
          fontSize:'9px', fontFamily:'monospace', marginTop:13,
        }}>PICK ON GLOBE</button>
      </div>
      <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginBottom:14 }}>
        {Object.entries(AS_META).slice(0,10).map(([asn,m]) => (
          <button key={asn} onClick={() => setPathSrc(parseInt(asn))} style={{
            background:'transparent', border:`1px solid ${m.color}44`,
            borderRadius:3, color:m.color, padding:'2px 7px',
            cursor:'pointer', fontSize:'8px', fontFamily:'monospace',
          }}>AS{asn}</button>
        ))}
      </div>
      {isLoading && <div style={{ color:'#7ab8d4', textAlign:'center', padding:40 }}>Querying…</div>}
      {data && (
        <>
          <div style={{ display:'flex', gap:16, marginBottom:14 }}>
            {[['PATHS',data.path_count],['STABILITY',`${data.stability?.score?.toFixed(0)}%`],
              ['UNIQUE',data.stability?.path_count],['DOM%',`${data.stability?.dominant_pct?.toFixed(0)}%`]]
              .map(([l,v]) => (
                <div key={l as string}>
                  <div style={{ color:'#aa44ff', fontSize:18, fontWeight:700 }}>{v}</div>
                  <div style={{ color:'#6aa8c0', fontSize:'8px' }}>{l}</div>
                </div>
            ))}
          </div>
          {data.paths?.slice(0,8).map((p:any,i:number) => (
            <div key={i} style={{ background:'#06111e', border:'1px solid #0d2035',
              borderLeft:`3px solid ${p.path_type==='graph'?'#aa44ff':'#ffaa00'}`,
              borderRadius:4, padding:'9px 11px', marginBottom:8 }}>
              <div style={{ display:'flex', gap:8, marginBottom:6 }}>
                <span style={{ color:p.path_type==='graph'?'#aa44ff':'#ffaa00', fontSize:'8px' }}>
                  {p.path_type === 'graph' ? '◆ GRAPH' : '● OBSERVED'}
                </span>
                <span style={{ color:'#7ab8d4', fontSize:'8px' }}>{p.hops} hops</span>
                {p.prefix && <span style={{ color:'#6aa8c0', fontSize:'8px' }}>{p.prefix}</span>}
              </div>
              <div style={{ display:'flex', alignItems:'center', flexWrap:'wrap', gap:3 }}>
                {p.asns?.map((asn:number,j:number) => {
                  const m = getASMeta(asn)
                  return (
                    <span key={j} style={{ display:'flex', alignItems:'center', gap:3 }}>
                      <span style={{
                        color:m?.color??'#ddeeff', background:m?`${m.color}15`:'#0a1828',
                        border:`1px solid ${m?.color??'#8fc4dc'}44`,
                        borderRadius:3, fontSize:'9px', padding:'2px 6px',
                      }}>AS{asn}{m?` · ${m.short}`:''}</span>
                      {j<p.asns.length-1&&<span style={{color:'#8fc4dc'}}>→</span>}
                    </span>
                  )
                })}
              </div>
            </div>
          ))}
        </>
      )}
    </div>
  )
}
