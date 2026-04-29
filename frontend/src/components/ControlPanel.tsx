// src/components/ControlPanel.tsx — P1: collapsible icon strip + full panel
// Collapsed: 44px icon strip. Expanded: 224px full panel (hover or click pin).
import { useState } from 'react'
import { useNOCStore } from '../store/nocStore'

const ICON_W  = 44   // collapsed strip width
const PANEL_W = 224  // expanded panel width

const S = {
  section: { borderBottom:'1px solid #0a1a2e', padding:'10px 12px' },
  hdr: { color:'#4a9fc8', fontSize:'8px', letterSpacing:'.14em', marginBottom:8 },
  row: { display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:6 },
  label: { color:'#8fc4dc', fontSize:'10px' },
  val: { color:'#00ccee', fontSize:'10px', minWidth:28, textAlign:'right' as const },
}

// Icon-strip button
function IconBtn({ icon, label, active, color, onClick }:
  { icon:string; label:string; active?:boolean; color?:string; onClick?:()=>void }) {
  return (
    <button onClick={onClick} title={label} style={{
      width:44, height:38, display:'flex', flexDirection:'column' as const,
      alignItems:'center', justifyContent:'center', gap:2,
      background: active ? `${color ?? '#00ccee'}18` : 'none',
      border:'none', borderLeft: active ? `2px solid ${color ?? '#00ccee'}` : '2px solid transparent',
      cursor:'pointer', flexShrink:0,
    }}>
      <span style={{ fontSize:14, lineHeight:1 }}>{icon}</span>
      <span style={{ color: active ? (color ?? '#00ccee') : '#3a5a70',
        fontSize:'6px', fontFamily:'monospace', letterSpacing:'.06em' }}>
        {label.slice(0,4)}
      </span>
    </button>
  )
}

function Toggle({ label, color, value, count, onChange }:
  { label:string; color:string; value:boolean; count?:number; onChange:(v:boolean)=>void }) {
  const hasEvents = (count ?? 0) > 0
  return (
    <div style={S.row}>
      <div style={{ display:'flex', alignItems:'center', gap:6, flex:1, minWidth:0 }}>
        <div style={{ width:7, height:7, borderRadius:'50%', flexShrink:0,
          background: value ? color : '#1a2a3a',
          boxShadow: value && hasEvents ? `0 0 6px ${color}` : 'none',
          transition:'all .2s' }} />
        <span style={{ ...S.label, color: value ? '#c8e0ec' : '#5a8090',
          overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' as const }}>
          {label}
        </span>
      </div>
      <div style={{ display:'flex', alignItems:'center', gap:5, flexShrink:0 }}>
        {count !== undefined && (
          <span style={{
            color: hasEvents ? color : '#2a4050',
            background: hasEvents ? `${color}18` : 'transparent',
            border:`1px solid ${hasEvents ? color+'44' : '#1a3040'}`,
            borderRadius:3, fontSize:'8px', padding:'0 5px',
            fontWeight: hasEvents ? 700 : 400, minWidth:24,
            textAlign:'center' as const, transition:'all .3s',
          }}>
            {(count ?? 0) > 999 ? `${Math.floor((count??0)/1000)}k` : (count ?? 0)}
          </span>
        )}
        <button onClick={() => onChange(!value)} style={{
          background: value ? `${color}22` : '#0a1828',
          border:`1px solid ${value ? color+'55' : '#2a4050'}`,
          borderRadius:3, color: value ? color : '#4a7090',
          padding:'2px 7px', cursor:'pointer', fontSize:'8px',
          fontFamily:'inherit', transition:'all .2s',
        }}>{value ? 'ON' : 'OFF'}</button>
      </div>
    </div>
  )
}

function Slider({ label, min, max, step=1, value, fmt, onChange }:
  { label:string; min:number; max:number; step?:number
    value:number; fmt:(v:number)=>string; onChange:(v:number)=>void }) {
  return (
    <div style={{ marginBottom:10 }}>
      <div style={S.row}>
        <span style={S.label}>{label}</span>
        <span style={S.val}>{fmt(value)}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(Number(e.target.value))}
        style={{ width:'100%', accentColor:'#00ccee', height:3 }} />
    </div>
  )
}

export function ControlPanel() {
  const { controls, setControl, resetControls, setPathMode, pathMode, anomalies,
          activeView, setActiveView } = useNOCStore()
  const [expanded, setExpanded] = useState(false)
  const [pinned,   setPinned]   = useState(false)
  const c = controls

  const flapCount   = anomalies.filter(a => a.event_type === 'bgp_flap').length
  const hijackCount = anomalies.filter(a => a.event_type === 'bgp_hijack').length
  const surgeCount  = anomalies.filter(a => a.event_type === 'withdrawal_surge').length
  const leakCount   = anomalies.filter(a => a.event_type === 'route_leak').length
  const cleanCount  = anomalies.filter(a => a.severity <= 1).length
  const rpkiInvalid = anomalies.filter(a => (a as any).rpki_status === 'invalid').length

  const isExpanded = expanded || pinned

  // View-switch icons for the collapsed strip
  const viewIcons = [
    { id:'globe'     as const, icon:'🌐', label:'GLOBE'   },
    { id:'feed'      as const, icon:'⚡', label:'EVENTS'  },
    { id:'path'      as const, icon:'⟶', label:'PATH'    },
    { id:'baseline'  as const, icon:'📈', label:'BASE'    },
    { id:'community' as const, icon:'◎',  label:'COMM'    },
  ]

  return (
    <div style={{
      background:'#05101e', borderRight:'1px solid #0d2035',
      width: isExpanded ? PANEL_W : ICON_W,
      minWidth: isExpanded ? PANEL_W : ICON_W,
      display:'flex', flexShrink:0, overflow:'hidden',
      transition:'width .2s ease, min-width .2s ease',
      position:'relative',
    }}
      onMouseEnter={() => !pinned && setExpanded(true)}
      onMouseLeave={() => !pinned && setExpanded(false)}
    >
      {/* Icon strip — always visible */}
      <div style={{ width:ICON_W, display:'flex', flexDirection:'column',
        flexShrink:0, overflowY:'auto', background:'#040d1a',
        borderRight: isExpanded ? '1px solid #0d2035' : 'none' }}>

        {/* Pin/unpin toggle */}
        <button onClick={() => setPinned(!pinned)} title={pinned ? 'Unpin panel' : 'Pin panel open'}
          style={{ width:44, height:36, background: pinned ? '#00ccee18' : 'none',
            border:'none', borderBottom:'1px solid #0a1828',
            color: pinned ? '#00ccee' : '#3a5a70',
            cursor:'pointer', fontSize:12 }}>
          {pinned ? '📌' : '⠿'}
        </button>

        {/* Layer toggles as compact icons */}
        <div style={{ borderBottom:'1px solid #0a1828', paddingBottom:4, marginBottom:4 }}>
          <IconBtn icon="〜" label="FLAP"  active={c.showFlaps}    color="#ff3b3b"
            onClick={() => setControl('showFlaps',   !c.showFlaps)} />
          <IconBtn icon="⚠" label="HIJCK" active={c.showHijacks}  color="#ffdd00"
            onClick={() => setControl('showHijacks', !c.showHijacks)} />
          <IconBtn icon="↓" label="SURGE" active={c.showSurges}   color="#ff8800"
            onClick={() => setControl('showSurges',  !c.showSurges)} />
          <IconBtn icon="⌁" label="RPKI"  active={c.showRPKI}     color="#00ee88"
            onClick={() => setControl('showRPKI',    !c.showRPKI)} />
          <IconBtn icon="〰" label="CABLE" active={c.showFiber}    color="#00ffcc"
            onClick={() => setControl('showFiber',   !c.showFiber)} />
        </div>

        {/* View navigation */}
        {viewIcons.map(v => (
          <IconBtn key={v.id} icon={v.icon} label={v.label}
            active={activeView === v.id} color="#00ccee"
            onClick={() => setActiveView(v.id)} />
        ))}

        {/* Path mode toggle */}
        <div style={{ marginTop:'auto', borderTop:'1px solid #0a1828' }}>
          <IconBtn icon="⟶" label="PATH" active={pathMode} color="#aa44ff"
            onClick={() => setPathMode(!pathMode)} />
        </div>
      </div>

      {/* Expanded panel — slides in */}
      {isExpanded && (
        <div style={{ flex:1, display:'flex', flexDirection:'column', overflowY:'auto',
          width: PANEL_W - ICON_W }}>

          <div style={S.section}>
            <div style={S.hdr}>EVENT LAYERS</div>
            <Toggle label="BGP Flap"         color="#ff3b3b" value={c.showFlaps}
              count={flapCount}   onChange={v => setControl('showFlaps', v)} />
            <Toggle label="BGP Hijack"       color="#ffdd00" value={c.showHijacks}
              count={hijackCount} onChange={v => setControl('showHijacks', v)} />
            <Toggle label="Withdrawal Surge" color="#ff8800" value={c.showSurges}
              count={surgeCount}  onChange={v => setControl('showSurges', v)} />
            <Toggle label="Route Leak"       color="#ff44cc" value={c.showHijacks}
              count={leakCount}   onChange={v => setControl('showHijacks', v)} />
            <Toggle label="Clean ASes"       color="#00aaff" value={c.showClean}
              count={cleanCount}  onChange={v => setControl('showClean', v)} />
            <Toggle label="RPKI Invalid"     color="#00ee88" value={c.showRPKI}
              count={rpkiInvalid} onChange={v => setControl('showRPKI', v)} />
            <Toggle label="Fiber Cables"     color="#00ffcc" value={c.showFiber}
              onChange={v => setControl('showFiber', v)} />
          </div>

          <div style={S.section}>
            <div style={S.hdr}>FILTERS</div>
            <Slider label="Min Severity" min={1} max={5} value={c.severityMin}
              fmt={v => ['','S1','S2','S3','S4','S5'][v] ?? `S${v}`}
              onChange={v => setControl('severityMin', v as any)} />
            <Slider label="Min Confidence" min={0} max={100} value={Math.round(c.confidenceMin*100)}
              fmt={v => `${v}%`}
              onChange={v => setControl('confidenceMin', v/100)} />
            <Slider label="Time Window" min={1} max={24} value={c.timeWindowH}
              fmt={v => `${v}h`}
              onChange={v => setControl('timeWindowH', v)} />
          </div>

          <div style={S.section}>
            <div style={S.hdr}>GLOBE</div>
            <Slider label="Arc Speed" min={0.2} max={3} step={0.1} value={c.arcSpeed}
              fmt={v => `${v.toFixed(1)}x`}
              onChange={v => setControl('arcSpeed', v)} />
            <Toggle label="Auto Rotate" color="#00ccee" value={c.globeAutoRotate}
              onChange={v => setControl('globeAutoRotate', v)} />
            <Toggle label="AS Labels"   color="#00ccee" value={c.showLabels}
              onChange={v => setControl('showLabels', v)} />
          </div>

          <div style={S.section}>
            <div style={S.hdr}>PATH ANALYSIS</div>
            <Toggle label="Path Mode" color="#aa44ff" value={pathMode}
              onChange={v => setPathMode(v)} />
            {pathMode && (
              <div style={{ background:'#0a1828', borderRadius:4, padding:'6px 8px',
                marginTop:6, border:'1px solid #aa44ff33',
                fontSize:'9px', color:'#8fc4dc', lineHeight:1.6 }}>
                Click 1st AS = source<br/>
                Click 2nd AS = dest<br/>
                Path draws automatically
              </div>
            )}
          </div>

          <div style={{ padding:'10px 12px', marginTop:'auto' }}>
            <button onClick={resetControls} style={{
              width:'100%', background:'#0a1828', border:'1px solid #0d2035',
              borderRadius:4, color:'#7ab8d4', padding:'6px', cursor:'pointer',
              fontSize:'9px', fontFamily:'inherit', letterSpacing:'.08em',
            }}>RESET ALL</button>
          </div>
        </div>
      )}
    </div>
  )
}
