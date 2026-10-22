export type Workspace = 'overview' | 'asn' | 'prefix' | 'compare'
export type MapMode = '2d' | '3d' | 'topology'
export type Scenario = 'demo' | 'partial' | 'unavailable'
export interface Investigation {
  workspace: Workspace
  mode: MapMode
  asn: number
  prefix: string
  window: '1h' | '6h' | '24h'
  family: 'all' | 'ipv4' | 'ipv6'
  severity: 'all' | 'major'
  scenario: Scenario
  observer: 'all' | 'london' | 'amsterdam'
  comparison: 'amsterdam' | 'singapore'
}
export const initialState: Investigation = {
  workspace: 'overview', mode: '2d', asn: 64496, prefix: '203.0.113.0/24',
  window: '1h', family: 'all', severity: 'all', scenario: 'demo', observer: 'all', comparison: 'amsterdam',
}
const allowed = <T extends string>(value: string | null, choices: readonly T[], fallback: T): T =>
  choices.includes(value as T) ? value as T : fallback
export function parseInvestigation(hash: string): Investigation {
  const q = new URLSearchParams(hash.replace(/^#/, ''))
  const asn = Number(q.get('asn'))
  return {
    workspace: allowed(q.get('view'), ['overview', 'asn', 'prefix', 'compare'], 'overview'),
    mode: allowed(q.get('map'), ['2d', '3d', 'topology'], '2d'),
    asn: [64496, 64497, 64498, 64499].includes(asn) ? asn : initialState.asn,
    prefix: allowed(q.get('prefix'), ['203.0.113.0/24', '2001:db8::/48'], initialState.prefix),
    window: allowed(q.get('window'), ['1h', '6h', '24h'], '1h'),
    family: allowed(q.get('family'), ['all', 'ipv4', 'ipv6'], 'all'),
    severity: allowed(q.get('severity'), ['all', 'major'], 'all'),
    scenario: allowed(q.get('scenario'), ['demo', 'partial', 'unavailable'], 'demo'),
    observer: allowed(q.get('observer'), ['all', 'london', 'amsterdam'], 'all'),
    comparison: allowed(q.get('compare'), ['amsterdam', 'singapore'], 'amsterdam'),
  }
}
export function serializeInvestigation(s: Investigation): string {
  return '#' + new URLSearchParams({view: s.workspace, map: s.mode, asn: String(s.asn),
    prefix: s.prefix, window: s.window, family: s.family, severity: s.severity,
    scenario: s.scenario, observer: s.observer, compare: s.comparison}).toString()
}
export interface Incident {
  id: string; title: string; type: string; asn: number; severity: 'Major' | 'Moderate'
  family: 'ipv4' | 'ipv6'; ageMinutes: number; observer: 'london' | 'amsterdam'
  impact: string; evidence: string; confidence: string
}
export const incidents: Incident[] = [
  {id:'DEMO-018',title:'Repeated announcements and withdrawals',type:'Routing instability',asn:64496,severity:'Major',family:'ipv4',ageMinutes:18,observer:'london',impact:'1 documentation prefix · 3 demo observers',evidence:'Seven announcement / withdrawal cycles in the fixture window. This is a routing observation, not a confirmed service outage.',confidence:'High for observed instability'},
  {id:'DEMO-021',title:'Round-trip latency above its baseline',type:'Performance degradation',asn:64497,severity:'Moderate',family:'ipv4',ageMinutes:34,observer:'amsterdam',impact:'1 demonstration path · 2 demo probes',evidence:'Fixture RTT increases from 24 ms to 81 ms. Congestion is one possible explanation; interface utilization and an independently measured return path are unavailable.',confidence:'Cause unconfirmed'},
  {id:'DEMO-024',title:'New origin needs investigation',type:'Origin change',asn:64498,severity:'Major',family:'ipv6',ageMinutes:77,observer:'amsterdam',impact:'1 documentation prefix · 2 demo observers',evidence:'A documentation prefix appears with a new origin. An origin change alone does not establish a hijack; authorization and operator context require investigation.',confidence:'Suspicious, not confirmed'},
]
export function visibleIncidents(s: Investigation): Incident[] {
  if (s.scenario === 'unavailable') return []
  return incidents.filter(i =>
    (s.window !== '1h' || i.ageMinutes <= 60) &&
    (s.family === 'all' || i.family === s.family) &&
    (s.severity === 'all' || i.severity === 'Major') &&
    (s.observer === 'all' || i.observer === s.observer))
}
export function sourceState(s: Scenario) {
  return s === 'unavailable'
    ? {label:'No usable observations', score:null, detail:'Unknown. No current evidence is available.'}
    : s === 'partial'
      ? {label:'Partial demonstration coverage', score:null, detail:'One fixture observer is delayed. A global score is withheld.'}
      : {label:'Demonstration coverage', score:null, detail:'Prototype fixture only. No live global health score is calculated.'}
}
export function comparisonPaths(origin:number, observer:'amsterdam'|'singapore') {
  const leftTransit=origin===64497?64496:64497
  const rightTransit=[64498,64496,64497].find(asn=>asn!==origin&&asn!==leftTransit)!
  const ingress=[64499,64496,64497,64498].find(asn=>asn!==origin&&asn!==leftTransit&&asn!==rightTransit)!
  return {left:[ingress,leftTransit,origin],right:observer==='amsterdam'?[ingress,rightTransit,origin]:[leftTransit,origin],leftTransit,rightTransit}
}
