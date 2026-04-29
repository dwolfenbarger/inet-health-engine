// @ts-nocheck
// src/components/CommunityPanel.tsx — correlated community signals
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'
import { useNOCStore } from '../store/nocStore'

const SOURCE_COLOR: Record<string,string> = {
  reddit:'#ff4500', x:'#1da1f2', nanog:'#00e5ff',
  hackernews:'#ff6600', hn:'#ff6600', statuspage:'#00ee88',
}
const SOURCE_ICON: Record<string,string> = {
  reddit:'r/', x:'𝕏', nanog:'NG', hackernews:'Y', hn:'Y', statuspage:'⚡',
}

export function CommunityPanel() {
  const { setSelectedASN } = useNOCStore()

  const { data, isLoading, error } = useQuery({
    queryKey: ['communityCorrelated'],
    queryFn:  () => api.communityCorrelated(48, 50),
    refetchInterval: 120000,
  })

  const signals: any[] = data?.signals ?? []
  const matched  = signals.filter(s => s.match_count > 0)
  const unmatched = signals.filter(s => s.match_count === 0)

  return (
    <div style={{ height:'100%', overflowY:'auto', padding:'14px 16px',
      fontFamily:'monospace', background:'#030810' }}>

      {/* Header */}
      <div style={{ marginBottom:14 }}>
        <div style={{ color:'#4a9fc8', fontSize:'8px', letterSpacing:'.12em', marginBottom:6 }}>
          COMMUNITY SIGNALS · NLP-CORRELATED TO BGP ANOMALIES
        </div>
        <div style={{ display:'flex', gap:10, flexWrap:'wrap' }}>
          {[
            { label:'TOTAL SIGNALS', val:signals.length,           c:'#8fc4dc' },
            { label:'BGP CORRELATED', val:matched.length,          c:'#00ee88' },
            { label:'ACTIVE ANOMALIES', val:data?.active_anomalies ?? 0, c:'#ffaa00' },
          ].map(p => (
            <div key={p.label} style={{ background:`${p.c}0e`, border:`1px solid ${p.c}33`,
              borderRadius:4, padding:'5px 12px', textAlign:'center' }}>
              <div style={{ color:p.c, fontSize:16, fontWeight:700 }}>{p.val}</div>
              <div style={{ color:'#8fc4dc', fontSize:'7px' }}>{p.label}</div>
            </div>
          ))}
        </div>
      </div>

      {isLoading && (
        <div style={{ color:'#6aa8c0', textAlign:'center', padding:'30px 0' }}>
          Correlating signals against {data?.active_anomalies ?? '…'} active anomalies…
        </div>
      )}

      {error && (
        <div style={{ color:'#ff3b3b', fontSize:'9px', padding:'10px 0' }}>
          Error loading signals. Elasticsearch may be indexing.
        </div>
      )}

      {/* BGP-Correlated signals */}
      {matched.length > 0 && (
        <div style={{ marginBottom:16 }}>
          <div style={{ color:'#00ee88', fontSize:'8px', letterSpacing:'.1em', marginBottom:8 }}>
            ● BGP-CORRELATED ({matched.length})
          </div>
          {matched.map((s: any, i: number) => (
            <SignalCard key={i} sig={s} setSelectedASN={setSelectedASN}
              sourceColor={SOURCE_COLOR} sourceIcon={SOURCE_ICON} />
          ))}
        </div>
      )}

      {/* Unmatched but high-urgency signals */}
      {unmatched.filter((s:any) => s.urgency_score >= 0.3).length > 0 && (
        <div>
          <div style={{ color:'#8fc4dc', fontSize:'8px', letterSpacing:'.1em', marginBottom:8 }}>
            ○ HIGH URGENCY · NOT YET CORRELATED
          </div>
          {unmatched.filter((s:any) => s.urgency_score >= 0.3).slice(0,10).map((s:any,i:number) => (
            <SignalCard key={i} sig={s} setSelectedASN={setSelectedASN}
              sourceColor={SOURCE_COLOR} sourceIcon={SOURCE_ICON} dim />
          ))}
        </div>
      )}

      {signals.length === 0 && !isLoading && (
        <div style={{ color:'#7ab8d4', textAlign:'center', padding:'30px 0', fontSize:'9px' }}>
          No signals in last 48h.<br/>
          <span style={{ color:'#6aa8c0', fontSize:'8px' }}>
            Reddit .json ✅ · HN Algolia ✅ · NANOG ⛔ · X ⛔
          </span>
        </div>
      )}
    </div>
  )
}

function SignalCard({ sig, setSelectedASN, sourceColor, sourceIcon, dim=false }: any) {
  const sc  = sourceColor[sig.source] ?? '#8fc4dc'
  const icon = sourceIcon[sig.source] ?? '●'
  const hasMatch = sig.match_count > 0

  return (
    <div style={{ background:'#06101c',
      border:`1px solid ${hasMatch ? '#00ee8833' : '#0a1828'}`,
      borderLeft:`3px solid ${sc}`, borderRadius:4,
      padding:'9px 11px', marginBottom:6,
      opacity: dim ? 0.7 : 1 }}>

      {/* Source + score */}
      <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:5 }}>
        <span style={{ color:sc, background:`${sc}22`, border:`1px solid ${sc}44`,
          borderRadius:2, fontSize:'8px', padding:'1px 5px', fontWeight:700 }}>{icon}</span>
        <span style={{ color:'#8fc4dc', fontSize:'8px' }}>{sig.source?.toUpperCase()}</span>
        {hasMatch && <span style={{ color:'#00ee88', fontSize:'8px' }}>● BGP match</span>}
        <div style={{ flex:1 }} />
        <span style={{ color:'#ffaa00', fontSize:'8px' }}>
          urgency {(sig.urgency_score * 100).toFixed(0)}%
        </span>
      </div>

      {/* Title */}
      {sig.title && (
        <div style={{ color:'#ddeeff', fontSize:'9px', marginBottom:5,
          overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
          {sig.title}
        </div>
      )}

      {/* Extracted ASNs */}
      {sig.extracted_asns?.length > 0 && (
        <div style={{ display:'flex', gap:4, flexWrap:'wrap', marginBottom:4 }}>
          {sig.extracted_asns.slice(0,5).map((asn: number) => (
            <span key={asn}
              onClick={() => setSelectedASN(asn)}
              style={{ color:'#00ccee', background:'#00ccee14', border:'1px solid #00ccee33',
                borderRadius:2, fontSize:'7px', padding:'1px 4px', cursor:'pointer' }}>
              AS{asn}
            </span>
          ))}
          {sig.extracted_prefixes?.slice(0,3).map((p: string) => (
            <span key={p} style={{ color:'#aa44ff', background:'#aa44ff14',
              border:'1px solid #aa44ff33', borderRadius:2, fontSize:'7px', padding:'1px 4px' }}>
              {p}
            </span>
          ))}
        </div>
      )}

      {/* Matched anomalies */}
      {sig.matched_anomalies?.length > 0 && (
        <div style={{ borderTop:'1px solid #0d2035', marginTop:5, paddingTop:4 }}>
          <div style={{ color:'#6aa8c0', fontSize:'7px', marginBottom:3 }}>
            MATCHED BGP ANOMALIES:
          </div>
          {sig.matched_anomalies.slice(0,2).map((a: any, j: number) => (
            <div key={j} style={{ color:'#8fc4dc', fontSize:'7px', marginBottom:2 }}>
              {a.event_type} · {a.affected_prefix} · AS{a.origin_asn} · conf {(a.confidence*100).toFixed(0)}%
            </div>
          ))}
        </div>
      )}

      {sig.url && (
        <div style={{ marginTop:4 }}>
          <a href={sig.url} target="_blank" rel="noreferrer"
            style={{ color:'#6aa8c0', fontSize:'7px', textDecoration:'none' }}>
            ↗ {sig.url.length > 50 ? sig.url.slice(0,50)+'…' : sig.url}
          </a>
        </div>
      )}
    </div>
  )
}
