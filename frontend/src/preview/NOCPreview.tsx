import {useEffect, useMemo, useRef, useState} from 'react'
import type {FormEvent, PointerEvent as ReactPointerEvent} from 'react'
import {geoArea, geoDistance, geoGraticule10, geoNaturalEarth1, geoOrthographic, geoPath} from 'd3'
import {Activity, ArrowDownRight, ArrowRight, ArrowUpRight, Check, ChevronRight, CircleHelp, Compass, ExternalLink, Globe2, Layers3, Link2, Map, Network, Plus, Minus, RotateCcw, Search, ShieldCheck, SlidersHorizontal, X} from 'lucide-react'
import countries from '../lib/countries.json'
import {initialState, parseInvestigation, serializeInvestigation, sourceState, visibleIncidents, comparisonPaths} from './investigation'
import type {Incident, Investigation, MapMode, Workspace} from './investigation'

const networks = [
  {asn:64496,name:'Northstar Transit',region:'North Atlantic',lat:51.51,lon:-0.13,status:'Routing instability',color:'#efab59'},
  {asn:64497,name:'Meridian Networks',region:'Western Europe',lat:52.37,lon:4.9,status:'Performance degradation',color:'#7ab9f4'},
  {asn:64498,name:'Pacific Example',region:'Asia Pacific',lat:35.68,lon:139.69,status:'Origin change',color:'#efab59'},
  {asn:64499,name:'Southern Exchange',region:'Southern Africa',lat:-33.92,lon:18.42,status:'No anomaly in fixture',color:'#65d9bd'},
]
const sites = [
  ...networks,
  {asn:64496,name:'New York observer',region:'North America',lat:40.71,lon:-74.01,status:'Demonstration observer',color:'#65d9bd'},
  {asn:64497,name:'São Paulo observer',region:'South America',lat:-23.55,lon:-46.63,status:'Demonstration observer',color:'#65d9bd'},
  {asn:64498,name:'Singapore observer',region:'Asia Pacific',lat:1.35,lon:103.82,status:'Demonstration observer',color:'#65d9bd'},
  {asn:64499,name:'Sydney observer',region:'Oceania',lat:-33.87,lon:151.21,status:'Demonstration observer',color:'#65d9bd'},
]
const links = [[4,0],[0,1],[1,3],[1,6],[6,2],[6,7],[5,3]]
const workspaces: {id:Workspace;label:string;icon:typeof Globe2}[] = [
  {id:'overview',label:'Overview',icon:Globe2}, {id:'asn',label:'ASN investigation',icon:Network},
  {id:'prefix',label:'Prefix investigation',icon:Layers3}, {id:'compare',label:'Compare routes',icon:ArrowRight},
]
const titles = {
  overview:['A clearer view of the Internet.','Explore routing, performance, and the evidence behind every signal.'],
  asn:['ASN investigation','Understand this network from multiple points of observation.'],
  prefix:['Prefix investigation','Follow announcements, authorization, and reachability for one resource.'],
  compare:['One prefix. Different perspectives.','Compare observed AS paths without losing your investigation context.'],
}
type Camera = {zoom:number;pan:[number,number];rotation:[number,number]}
type Update = (patch:Partial<Investigation>)=>void

function Chip({children,tone='neutral'}:{children:React.ReactNode;tone?:string}) {
  return <span className={`chip ${tone}`}><span className="status-dot" />{children}</span>
}
function Sparkline({values=[9,10,8,9,11,10,9,10,12,13,12,23,29,25,28,26],color='var(--mint)'}:{values?:number[];color?:string}) {
  const points = values.map((v,i)=>`${i/(values.length-1)*150},${38-v}`).join(' ')
  return <svg viewBox="0 0 150 40" aria-hidden="true" className="sparkline"><polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" /></svg>
}
function Metric({label,value,detail,tone='mint',values}:{label:string;value:string;detail:string;tone?:string;values?:number[]}) {
  return <article className="metric"><div className="eyebrow">{label}</div><div className={`metric-value ${tone}`}>{value}</div><div className="metric-detail">{detail}</div>{value!=='—'&&<Sparkline values={values} color={`var(--${tone})`} />}</article>
}
function MapView({state,update,camera,setCamera,compact=false}:{state:Investigation;update:Update;camera:Camera;setCamera:(c:Camera)=>void;compact?:boolean}) {
  const drag=useRef<{x:number;y:number;camera:Camera}|null>(null)
  const moved=useRef(false)
  const projection=useMemo(()=>state.mode==='3d'
    ? geoOrthographic().scale(225).translate([500,250]).rotate(camera.rotation).clipAngle(90)
    : geoNaturalEarth1().scale(174).translate([500,245]),[state.mode,camera.rotation])
  const draw=geoPath(projection)
  const show=state.scenario!=='unavailable'
  const signals=visibleIncidents(state)
  const signalColor=(asn:number)=>signals.some(i=>i.asn===asn&&i.type!=='Performance degradation')?'#efab59':signals.some(i=>i.asn===asn)?'#7ab9f4':'#65d9bd'
  const isFront=(lon:number,lat:number)=>state.mode!=='3d'||geoDistance([lon,lat],[-camera.rotation[0],-camera.rotation[1]])<Math.PI/2
  const project=(lon:number,lat:number)=>projection([lon,lat])??[0,0]
  const select=(asn:number)=>{if(!moved.current)update({asn,prefix:asn===64498?'2001:db8::/48':'203.0.113.0/24'})}
  const start=(e:ReactPointerEvent<SVGSVGElement>)=>{
    if(e.button!==0||state.mode==='topology')return
    moved.current=false;drag.current={x:e.clientX,y:e.clientY,camera}
    // Leave interactive nodes in control of their own click and focus.
    if((e.target as Element).closest('[role="button"]')){drag.current=null;return}
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const move=(e:ReactPointerEvent<SVGSVGElement>)=>{
    if(!drag.current)return
    const dx=e.clientX-drag.current.x,dy=e.clientY-drag.current.y
    if(Math.abs(dx)+Math.abs(dy)<4)return
    moved.current=true
    if(state.mode==='3d')setCamera({...camera,rotation:[drag.current.camera.rotation[0]+dx*.3,Math.max(-75,Math.min(75,drag.current.camera.rotation[1]-dy*.3))]})
    else setCamera({...camera,pan:[drag.current.camera.pan[0]+dx,drag.current.camera.pan[1]+dy]})
  }
  return <section className={`panel visual-panel ${compact?'compact':''}`} aria-label="Network visualization">
    <div className="panel-heading"><div><span className="eyebrow">{state.mode==='topology'?'LOGICAL AS RELATIONSHIPS':'GLOBAL PERSPECTIVE'}</span><h2>{state.mode==='2d'?'Internet activity map':state.mode==='3d'?'Internet activity globe':'Selected network topology'}</h2></div><div className="segmented" aria-label="Visualization mode">{(['2d','3d','topology'] as MapMode[]).map((mode)=><button key={mode} aria-pressed={state.mode===mode} onClick={()=>update({mode})}>{mode==='2d'?<Map size={14}/>:mode==='3d'?<Globe2 size={14}/>:<Network size={14}/>}<span>{mode==='2d'?'2D':mode==='3d'?'3D':'Topology'}</span></button>)}</div></div>
    <div className="map-surface">
      <svg viewBox="0 0 1000 490" className="world-map" aria-label={`${state.mode} demonstration network visualization. Select a network node to investigate.`} onPointerDown={start} onPointerMove={move} onPointerUp={()=>{drag.current=null}} onPointerCancel={()=>{drag.current=null}}>
        <defs><radialGradient id="ocean"><stop stopColor="#142d3d"/><stop offset="1" stopColor="#0b1925"/></radialGradient><radialGradient id="nodeGlow"><stop stopColor="#efab59" stopOpacity=".2"/><stop offset="1" stopColor="#efab59" stopOpacity="0"/></radialGradient></defs>
        {state.mode==='topology'?<g>
          {[[0,1],[1,2],[1,3]].map(([a,b])=><line key={`${a}-${b}`} x1={150+a*220} y1={a===3?350:230} x2={150+b*220} y2={b===3?350:230} className="topology-edge"/>)}
          {show&&networks.map((n,i)=><g key={n.asn} role="button" tabIndex={0} aria-label={`Select AS${n.asn} ${n.name}`} onClick={()=>select(n.asn)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(n.asn)}}} transform={`translate(${150+i*220},${i===3?350:230})`}><rect x="-78" y="-40" width="156" height="80" rx="10" fill={n.asn===state.asn?'#183b40':'#122231'} stroke={n.asn===state.asn?'#65d9bd':'#304b5b'}/><text textAnchor="middle" y="-5" className="map-label">AS{n.asn}</text><text textAnchor="middle" y="17" className="map-small">{n.name}</text></g>)}
          <text x="35" y="445" className="map-small">Illustrative relationships. Not physical circuits or a measured forwarding path.</text>
        </g>:<g transform={`translate(${state.mode==='2d'?camera.pan[0]:0},${state.mode==='2d'?camera.pan[1]:0}) translate(500 245) scale(${camera.zoom}) translate(-500 -245)`}>
          <path d={draw({type:'Sphere'})??''} fill="url(#ocean)" stroke="#244052"/>
          <path d={draw(geoGraticule10())??''} fill="none" stroke="#1e3948" strokeWidth=".55"/>
          {countries.map((country,i)=><g key={`${country.i}-${i}`}>{country.r.map((ring,j)=>{const polygon={type:'Polygon' as const,coordinates:[ring]};if(geoArea(polygon)>Math.PI*2)polygon.coordinates=[[...ring].reverse()];return <path key={j} d={draw(polygon)??''} fill="#1f3a4b" stroke="#486477" strokeWidth=".65"/>})}</g>)}
          {show&&links.map(([a,b],i)=><path key={i} d={draw({type:'LineString',coordinates:[[sites[a].lon,sites[a].lat],[sites[b].lon,sites[b].lat]]})??''} fill="none" stroke={signalColor(sites[b].asn)} strokeWidth={i<2?1.8:1} strokeDasharray={i<2?'':'4 5'} opacity=".7"/>)}
          {show&&sites.map((n,i)=>{if(!isFront(n.lon,n.lat))return null;const [x,y]=project(n.lon,n.lat);return <g key={i} transform={`translate(${x},${y})`} role="button" tabIndex={0} aria-label={`Select AS${n.asn} ${n.name}`} onClick={()=>select(n.asn)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();select(n.asn)}}}>
            {signals.some(s=>s.asn===n.asn)&&<circle r="38" fill="url(#nodeGlow)"/>}<circle r={n.asn===state.asn?11:7} fill="none" stroke={signalColor(n.asn)} opacity=".45"/><circle r="4" fill={signalColor(n.asn)}/><circle r="17" fill="transparent"/>
            {(i===0||i===2||i===3||i===4)&&<text x="13" y={i===0?-16:-9} className="map-label">{i===0?'AS64496 · London':i===2?'Tokyo':i===3?'Cape Town':'New York'}</text>}
          </g>})}
        </g>}
      </svg>
      <div className="map-caption"><span className="status-dot amber" />{show?'Illustrative activity · documentation ASNs':'No observations to display'}</div>
      {state.mode!=='topology'&&<div className="map-tools"><button aria-label="Zoom in" onClick={()=>setCamera({...camera,zoom:Math.min(3,camera.zoom+.2)})}><Plus size={16}/></button><button aria-label="Zoom out" onClick={()=>setCamera({...camera,zoom:Math.max(.8,camera.zoom-.2)})}><Minus size={16}/></button><button aria-label="Reset map camera" onClick={()=>setCamera({zoom:1,pan:[0,0],rotation:[-10,-18]})}><RotateCcw size={15}/></button></div>}
      {state.scenario==='unavailable'&&<div className="map-empty"><CircleHelp size={28}/><strong>Coverage unavailable</strong><span>We cannot infer Internet health without observations.</span></div>}
    </div>
    <div className="map-footer"><div><span className="legend-dot amber"/>Routing signal<span className="legend-dot blue"/>Performance signal<span className="legend-dot mint"/>Demo observer</div><span>{state.mode==='3d'?'Drag to rotate':'Drag to pan'} · approximate geography</span></div>
  </section>
}

export default function NOCPreview() {
  const [state,setState]=useState<Investigation>(()=>parseInvestigation(location.hash))
  const [camera,setCamera]=useState<Camera>({zoom:1,pan:[0,0],rotation:[-10,-18]})
  const [search,setSearch]=useState('')
  const [message,setMessage]=useState('')
  const [detail,setDetail]=useState<Incident|null>(null)
  const compare=state.comparison
  const paths=comparisonPaths(state.asn,compare)
  const dialogRef=useRef<HTMLElement>(null)
  const openerRef=useRef<HTMLElement|null>(null)
  useEffect(()=>{
    if(!detail)return
    const prior=openerRef.current
    const overflow=document.body.style.overflow
    document.body.style.overflow='hidden'
    const trap=(e:KeyboardEvent)=>{
      if(e.key==='Escape'){setDetail(null);return}
      if(e.key!=='Tab')return
      const nodes=Array.from(dialogRef.current?.querySelectorAll<HTMLElement>('button,a[href],input,select,[tabindex="0"]')??[])
      const first=nodes[0],last=nodes[nodes.length-1]
      if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus()}
      else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus()}
    }
    document.addEventListener('keydown',trap)
    return()=>{document.removeEventListener('keydown',trap);document.body.style.overflow=overflow;prior?.focus()}
  },[detail])
  useEffect(()=>{const sync=()=>{setState(parseInvestigation(location.hash));setDetail(null)};addEventListener('hashchange',sync);addEventListener('popstate',sync);return()=>{removeEventListener('hashchange',sync);removeEventListener('popstate',sync)}},[])
  useEffect(()=>{document.title=`INET Health | ${workspaces.find(w=>w.id===state.workspace)?.label} · Design Preview`},[state.workspace])
  const update:Update=patch=>{const next={...state,...patch};history.pushState(null,'',serializeInvestigation(next));setState(next);setMessage('');if(patch.scenario)setDetail(null)}
  const selected=networks.find(n=>n.asn===state.asn)??networks[0]
  const rows=visibleIncidents(state)
  const coverage=sourceState(state.scenario)
  const unavailable=state.scenario==='unavailable'
  const openAsn=(asn:number)=>update({workspace:'asn',asn,prefix:asn===64498?'2001:db8::/48':'203.0.113.0/24'})
  const submitSearch=(e:FormEvent)=>{
    e.preventDefault();const q=search.trim().toLowerCase();if(!q){setMessage('Try AS64496, Northstar, 203.0.113.0/24, or 2001:db8::/48.');return}
    if(['203.0.113.0/24','203.0.113.1','2001:db8::/48','2001:db8::1'].includes(q)){update({workspace:'prefix',prefix:q.startsWith('2001')?'2001:db8::/48':'203.0.113.0/24',asn:q.startsWith('2001')?64498:64496});setSearch('');return}
    const found=networks.find(n=>q===String(n.asn)||q===`as${n.asn}`||n.name.toLowerCase().includes(q))
    if(found){openAsn(found.asn);setSearch('')}else setMessage('No matching demonstration resource. Try AS64496 or 203.0.113.0/24. No live lookup was sent.')
  }
  const copyLink=async()=>{try{await navigator.clipboard.writeText(location.href.split('#')[0]+serializeInvestigation(state));setMessage('Investigation link copied. It preserves this demonstration context.')}catch{setMessage('Use the address bar to copy this investigation link.')}}
  const showEvidence=(incident:Incident)=>{openerRef.current=document.activeElement as HTMLElement;setDetail(incident)}
  const metricValue=(s:string)=>unavailable?'—':s
  return <div className="noc-preview">
    <a className="skip-link" href="#main-content" onClick={e=>{e.preventDefault();document.getElementById('main-content')?.focus()}}>Skip to investigation</a>
    <aside className="sidebar"><a className="brand" href="design.html"><span className="brand-symbol"><Compass size={25}/></span><span>INET<span className="brand-health">HEALTH</span><small>GLOBAL INTERNET OBSERVATORY</small></span></a><span className="nav-label">WORKSPACE</span><nav aria-label="Main navigation">{workspaces.map(({id,label,icon:Icon})=><button key={id} aria-current={state.workspace===id?'page':undefined} onClick={()=>update({workspace:id})}><Icon size={18}/><span>{label}</span>{state.workspace===id&&<span className="nav-active"/>}</button>)}</nav><div className="sidebar-bottom"><div className="preview-note"><span className="eyebrow">DESIGN PREVIEW · R1</span><p>Explore the next operator experience.</p><span>All networks and incidents are demonstration fixtures.</span></div><a href="/" className="legacy-link"><ExternalLink size={14}/>Existing application</a><div className="environment"><span className="status-dot"/>DEV1 <span>UI review</span></div></div></aside>
    <div className="app-body"><header className="topbar"><div className="breadcrumb">Workspace <ChevronRight size={13}/><strong>{workspaces.find(w=>w.id===state.workspace)?.label}</strong></div><form className="search" onSubmit={submitSearch}><Search size={17}/><input aria-label="Search demonstration ASN, IP or prefix" placeholder="Search ASN, IP, prefix, or organization" value={search} onChange={e=>setSearch(e.target.value)}/><kbd>↵</kbd></form><button className="icon-button share-button" aria-label="Copy investigation link" onClick={copyLink}><Link2 size={18}/></button><span className="avatar">WD</span></header>
      <div className="demo-banner"><span><span className="status-dot"/><strong>DEMONSTRATION</strong> Illustrative data. No live Internet health assessment.</span><label>Preview scenario <select aria-label="Preview scenario" value={state.scenario} onChange={e=>update({scenario:e.target.value as Investigation['scenario']})}><option value="demo">Demonstration</option><option value="partial">Delayed observer</option><option value="unavailable">No coverage</option></select></label></div>
      {message&&<div className="toast" role="status">{message}<button aria-label="Dismiss message" onClick={()=>setMessage('')}><X size={15}/></button></div>}
      <main id="main-content" tabIndex={-1}><div className="page-heading"><div><div className="eyebrow">{state.workspace==='overview'?'GLOBAL OVERVIEW':'INVESTIGATION WORKSPACE'}</div><h1>{titles[state.workspace][0]}</h1><p>{titles[state.workspace][1]}</p></div><div className="snapshot-time"><span className="eyebrow">DEMO SNAPSHOT</span><strong>22 Oct 2026 <span>14:00 UTC</span></strong></div></div>
        <div className="filterbar"><div><SlidersHorizontal size={15}/><span className="filter-label">Perspective</span><select aria-label="Observer perspective" value={state.observer} onChange={e=>update({observer:e.target.value as Investigation['observer']})}><option value="all">All demo observers</option><option value="london">London observer</option><option value="amsterdam">Amsterdam observer</option></select></div><div><select aria-label="Address family" value={state.family} onChange={e=>update({family:e.target.value as Investigation['family']})}><option value="all">IPv4 + IPv6</option><option value="ipv4">IPv4</option><option value="ipv6">IPv6</option></select></div><div><select aria-label="Incident severity" value={state.severity} onChange={e=>update({severity:e.target.value as Investigation['severity']})}><option value="all">All severities</option><option value="major">Major only</option></select></div><div className="time-select"><span className="filter-label">Time window</span><select aria-label="Time window" value={state.window} onChange={e=>update({window:e.target.value as Investigation['window']})}><option value="1h">Last hour</option><option value="6h">Last 6 hours</option><option value="24h">Last 24 hours</option></select></div><button className="text-button" onClick={()=>{update({...initialState,workspace:state.workspace});setCamera({zoom:1,pan:[0,0],rotation:[-10,-18]})}}>Reset filters</button></div>
        {state.scenario!=='demo'&&<div className="coverage-warning" role="status"><CircleHelp size={18}/><div><strong>{coverage.label}</strong><p>{coverage.detail}</p></div></div>}
        {state.workspace==='overview'?<>
          <div className="metrics"><Metric label="ROUTING SIGNALS" value={metricValue(String(rows.filter(r=>r.type!=='Performance degradation').length).padStart(2,'0'))} detail="Matching fixture incidents" tone="amber"/><Metric label="PERFORMANCE" value={rows.some(r=>r.type==='Performance degradation')?'81 ms':'—'} detail="Example RTT · baseline 24 ms" tone="blue"/><Metric label="OBSERVATION COVERAGE" value={metricValue(state.scenario==='partial'?'2 / 4':'3 / 4')} detail="Current demo observer snapshots"/><Metric label="GLOBAL HEALTH" value="—" detail="Withheld · demonstration data" tone="muted"/></div>
          <div className="overview-grid"><MapView state={state} update={update} camera={camera} setCamera={setCamera}/><aside className="insight-stack"><section className="panel brief-panel"><span className="eyebrow">OPERATOR BRIEF</span><div className="brief-icon"><Activity size={21}/></div><h2>{unavailable?'Observation unavailable':'Routing changes deserve a closer look.'}</h2><p>{unavailable?'Source silence is not evidence of recovery. Restore observations before assessing health.':'The sample highlights unstable announcements and elevated latency. Neither establishes a global outage.'}</p><button className="primary-button" onClick={()=>openAsn(state.asn)}>Investigate AS{state.asn}<ArrowRight size={15}/></button></section><section className="panel coverage-panel"><div className="panel-heading"><h2>Evidence coverage</h2><Chip tone={state.scenario==='demo'?'mint':'amber'}>{unavailable?'Unknown':'Fixture'}</Chip></div>{[['Routing observations',unavailable?'Unavailable':state.scenario==='partial'?'Partial':'3 of 4 observers'],['Performance probes',unavailable?'Unavailable':'2 demo paths'],['IPv6 measurements','Not in this preview']].map(([name,value])=><div className="coverage-row" key={name}><span>{name}</span><strong>{value}</strong></div>)}<p>Coverage describes our observations, not the percentage of the Internet that is healthy.</p></section></aside></div>
          <IncidentTable rows={rows} unavailable={unavailable} onEvidence={showEvidence} openAsn={openAsn}/>
        </>:state.workspace==='asn'?<>
          <div className="entity-heading"><span className="entity-icon"><Network size={24}/></span><div><span className="eyebrow">DOCUMENTATION ASN</span><h2>AS{selected.asn} <span>{selected.name}</span></h2><p>{selected.region} · Illustrative organization · IPv4 / IPv6</p></div><Chip tone="amber">{unavailable?'Unknown':selected.status}</Chip></div>
          <div className="metrics"><Metric label="ANNOUNCED PREFIXES" value={metricValue('2')} detail="Documentation resources"/><Metric label="ROUTE STABILITY" value={metricValue(selected.asn===64496?'7 cycles':'—')} detail="Example announcement changes" tone="amber"/><Metric label="MEASURED PERFORMANCE" value={metricValue('Partial')} detail="No network-wide conclusion" tone="blue"/><Metric label="AUTHORIZATION" value={metricValue('Review')} detail="Fixture origin validation" tone="muted"/></div>
          <div className="investigation-grid"><section className="panel entity-panel"><div className="panel-heading"><h2>What changed?</h2><Chip tone="neutral">{state.window} window</Chip></div><div className="narrative"><span className="narrative-line"/><div><span className="eyebrow">13:42 UTC · DEMONSTRATION</span><h3>{unavailable?'No current evidence':selected.status}</h3><p>{unavailable?'There are no observations from which to assess this ASN.':selected.asn===64496?'The fixture contains repeated announcements and withdrawals for 203.0.113.0/24. Three demonstration observers provide routing evidence. Service impact is not confirmed.':'This selected documentation network illustrates how an ASN investigation keeps routing, performance, and confidence separate.'}</p><button className="text-button" onClick={()=>update({workspace:'prefix'})}>Inspect selected prefix <ArrowRight size={14}/></button></div></div><h3 className="subheading">Observed resources</h3><div className="resource-row"><button onClick={()=>update({workspace:'prefix',prefix:'203.0.113.0/24'})}>203.0.113.0/24</button><span>IPv4 · documentation</span><ChevronRight size={16}/></div><div className="resource-row"><button onClick={()=>update({workspace:'prefix',prefix:'2001:db8::/48'})}>2001:db8::/48</button><span>IPv6 · documentation</span><ChevronRight size={16}/></div><div className="evidence-callout"><ShieldCheck size={19}/><div><strong>Evidence before attribution</strong><p>Selected-AS context is retained. Prefixes are examples, not a complete route table.</p></div></div></section><MapView state={state} update={update} camera={camera} setCamera={setCamera} compact/></div>
          <IncidentTable rows={rows.filter(r=>r.asn===state.asn)} unavailable={unavailable} onEvidence={showEvidence} openAsn={openAsn}/>
        </>:state.workspace==='prefix'?<>
          <div className="entity-heading"><span className="entity-icon"><Layers3 size={24}/></span><div><span className="eyebrow">DOCUMENTATION PREFIX</span><h2 className="mono">{state.prefix}</h2><p>Selected context AS{state.asn} · Illustrative routing evidence</p></div><button className="primary-button" onClick={()=>update({workspace:'compare'})}>Compare viewpoints<ArrowRight size={15}/></button></div>
          <div className="metrics"><Metric label="ORIGIN ASN" value={metricValue(`AS${state.asn}`)} detail="Demonstration observation"/><Metric label="VISIBILITY" value={metricValue(state.scenario==='partial'?'2 / 4':'3 / 4')} detail="Eligible fixture observers" tone="blue"/><Metric label="RPKI ORIGIN STATE" value={metricValue('Not found')} detail="No live validator was queried" tone="muted"/><Metric label="LAST ROUTE CHANGE" value={metricValue('13:42')} detail="Separate from feed freshness" tone="amber"/></div>
          <section className="panel"><div className="panel-heading"><div><span className="eyebrow">ROUTING EVIDENCE</span><h2>Observed paths to this prefix</h2></div><Chip tone="neutral">Documentation data</Chip></div>{unavailable?<EmptyState title="No route observations" text="This does not establish that the prefix has been withdrawn."/>:<><div className="table-scroll"><table><thead><tr><th>Observer</th><th>AS path</th><th>Confirmed at</th><th>State</th></tr></thead><tbody><tr><td><strong>London</strong><small>Demonstration viewpoint</small></td><td className="mono">{paths.left.join(' → ')}</td><td>13:59:45 UTC</td><td><Chip tone="amber">Unstable fixture</Chip></td></tr><tr><td><strong>Amsterdam</strong><small>Demonstration viewpoint</small></td><td className="mono">{paths.right.join(' → ')}</td><td>{state.scenario==='partial'?'13:38:00 UTC':'13:59:48 UTC'}</td><td><Chip tone={state.scenario==='partial'?'amber':'mint'}>{state.scenario==='partial'?'Delayed observer':'Observed'}</Chip></td></tr></tbody></table></div><div className="prefix-footer"><p>AS paths describe routing observations. They are not measured router-by-router forwarding paths.</p><button className="text-button" onClick={()=>update({workspace:'compare'})}>Open route comparison<ArrowRight size={14}/></button></div></> }</section>
          <div className="two-columns"><section className="panel explanatory"><h2>Authorization context</h2><p>“Not found” means no matching authorization is provided in this fixture. It does not mean the announcement is malicious.</p><span className="eyebrow">VALIDATION SOURCE · UNAVAILABLE IN PREVIEW</span></section><section className="panel explanatory"><h2>Raw observation</h2><details><summary>Inspect demonstration JSON</summary><pre>{JSON.stringify({fixture:true,prefix:state.prefix,origin_asn:state.asn,source:'demonstration',observed_at:'2026-10-22T13:59:45Z',live_validation:false},null,2)}</pre></details></section></div>
        </>:<>
          <div className="comparison-context"><Layers3 size={19}/><strong className="mono">{state.prefix}</strong><span>Selected AS{state.asn}</span><button className="text-button" onClick={()=>update({workspace:'prefix'})}>Prefix details<ArrowRight size={14}/></button></div>
          <div className="two-columns compare-grid"><RouteCard name="London" code="LON · DEMO" path={paths.left} unavailable={unavailable} delayed={false}/><RouteCard name={compare==='amsterdam'?'Amsterdam':'Singapore'} code={compare==='amsterdam'?'AMS · DEMO':'SIN · DEMO'} path={paths.right} unavailable={unavailable} delayed={state.scenario==='partial'} controls={<select aria-label="Comparison observer" value={compare} onChange={e=>update({comparison:e.target.value as 'amsterdam'|'singapore'})}><option value="amsterdam">Amsterdam</option><option value="singapore">Singapore</option></select>}/></div>
          <section className="panel comparison-summary"><div className="brief-icon"><ArrowDownRight size={21}/></div><div><span className="eyebrow">PATH DIFFERENCE</span><h2>{unavailable?'Comparison unavailable':compare==='amsterdam'?'One intermediate ASN differs.':'The example paths have different lengths.'}</h2><p>{unavailable?'Restore observations before comparing paths.':compare==='amsterdam'?`London observes AS${paths.leftTransit} where Amsterdam observes AS${paths.rightTransit}. The origin is unchanged in these fixtures. A route difference alone does not prove a fault.`:'Singapore shows a shorter illustrative AS path. Fewer AS hops do not establish lower latency or a better forwarding path.'}</p></div><Chip tone="neutral">Cause unconfirmed</Chip></section>
          <div className="panel explanatory"><h2>Keep the observation context</h2><p>Both routes refer to the selected prefix and snapshot. The global filters remain preserved; this comparison is an explicit pair of demonstration observers. Changing the right observer updates its path. Full historical replay and live router queries belong to later phases.</p><button className="text-button" onClick={copyLink}><Link2 size={14}/>Copy investigation link</button></div>
        </>}
        <footer className="page-footer"><span><Compass size={13}/>INET Health · R1 design preview</span><span>Documentation ASNs / prefixes · No live measurements or diagnostics</span></footer>
      </main>
    </div>
    {detail&&<div className="modal-backdrop" onClick={()=>setDetail(null)}><section ref={dialogRef} className="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="evidence-title" onClick={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='Escape')setDetail(null)}}><button autoFocus className="modal-close icon-button" aria-label="Close evidence" onClick={()=>setDetail(null)}><X size={19}/></button><span className="eyebrow">DEMONSTRATION EVIDENCE · {detail.id}</span><h2 id="evidence-title">{detail.title}</h2><Chip tone="amber">{detail.type}</Chip><p>{detail.evidence}</p><dl><dt>Scope</dt><dd>{detail.impact}</dd><dt>Confidence</dt><dd>{detail.confidence}</dd><dt>Resource</dt><dd>AS{detail.asn} · documentation network</dd><dt>Snapshot</dt><dd>22 Oct 2026, 14:00 UTC · fixture</dd></dl><button className="primary-button" onClick={()=>{openAsn(detail.asn);setDetail(null)}}>Open ASN investigation<ArrowRight size={15}/></button></section></div>}
  </div>
}

function IncidentTable({rows,unavailable,onEvidence,openAsn}:{rows:Incident[];unavailable:boolean;onEvidence:(i:Incident)=>void;openAsn:(n:number)=>void}) {
  return <section className="panel incident-panel"><div className="panel-heading"><div><span className="eyebrow">WHAT NEEDS ATTENTION</span><h2>Incident observations <span className="count">{rows.length}</span></h2></div><span className="small-muted">Filtered demonstration snapshot</span></div>{!rows.length?<EmptyState title={unavailable?'No usable observations':'No matching demonstration incidents'} text={unavailable?'Health is unknown. Source silence does not confirm recovery.':'Try a longer time window or broader filters. This is not a global all-clear.'}/>:<div className="table-scroll"><table><thead><tr><th>Signal / network</th><th>Severity</th><th>Observed scope</th><th>Started</th><th>Evidence</th></tr></thead><tbody>{rows.map(i=><tr key={i.id}><td><button className="incident-title" onClick={()=>onEvidence(i)}>{i.type}</button><button className="asn-link" onClick={()=>openAsn(i.asn)}>AS{i.asn} · {networks.find(n=>n.asn===i.asn)?.name}</button></td><td><Chip tone={i.severity==='Major'?'amber':'blue'}>{i.severity}</Chip></td><td>{i.impact}<small>{i.confidence}</small></td><td>{i.ageMinutes} min ago<small>Fixture timestamp</small></td><td><button className="evidence-button" onClick={()=>onEvidence(i)}>Inspect<ArrowUpRight size={14}/></button></td></tr>)}</tbody></table></div>}</section>
}
function EmptyState({title,text}:{title:string;text:string}) {return <div className="empty-state"><CircleHelp size={25}/><strong>{title}</strong><p>{text}</p></div>}
function RouteCard({name,code,path,unavailable,delayed,controls}:{name:string;code:string;path:number[];unavailable:boolean;delayed:boolean;controls?:React.ReactNode}) {
  return <section className="panel route-card"><div className="panel-heading"><div><span className="eyebrow">{code}</span><h2>{name} viewpoint</h2></div>{controls??<Chip tone="neutral">Reference</Chip>}</div>{unavailable?<EmptyState title="Observer unavailable" text="There is no current route to compare."/>:<><div className="route-status"><Chip tone={delayed?'amber':'mint'}>{delayed?'Delayed fixture':'Demonstration route'}</Chip><span>{delayed?'Confirmed 13:38:00':'Confirmed 13:59:45'} UTC</span></div><ol className="route-path">{path.map((asn,i)=><li key={i} className={i===path.length-1?'destination':''}><span className="route-node">{i===path.length-1?<Check size={14}/>:i+1}</span><div><strong>AS{asn}</strong><span>{networks.find(n=>n.asn===asn)?.name}</span></div>{i===path.length-1&&<span className="origin-tag">ORIGIN</span>}</li>)}</ol><div className="route-facts"><div><span>Observed AS hops</span><strong>{path.length}</strong></div><div><span>Origin validation</span><strong>Fixture only</strong></div><div><span>Community</span><strong className="mono">64499:100</strong></div></div><details><summary>View raw observation</summary><pre>{JSON.stringify({fixture:true,observer:name,as_path:path,community:['64499:100'],geography:'approximate'},null,2)}</pre></details></>}</section>
}
