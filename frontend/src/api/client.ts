// src/api/client.ts — typed API client for all endpoints

const BASE = '/api/v1'

async function get<T>(path: string, timeoutMs = 25000): Promise<T> {
  const ctrl = new AbortController()
  const tid  = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetch(`${BASE}${path}`, { signal: ctrl.signal })
    if (!res.ok) throw new Error(`API ${res.status}: ${path}`)
    return res.json()
  } finally {
    clearTimeout(tid)
  }
}

export interface GlobalStatus {
  global_health_score: number
  bgp_updates_1h: number
  anomalies_1h: number
  high_severity_events_1h: number
  active_events: number
  community_signals_1h: number
  last_bgp_update: string
  data_sources: Record<string, string>
}

export interface BGPAnomaly {
  time: string
  event_id: string
  event_type: string
  affected_prefix: string | null
  origin_asn: number | null
  expected_asn: number | null
  severity: number
  confidence: number
  source: string
}

export interface BGPUpdate {
  time: string; prefix: string; origin_asn: number
  as_path: number[]; change_type: string
  collector: string; rpki_status: string
}

export interface BGPSummary {
  updates_last_1h: number
  anomalies_last_1h: number
  top_active_asns: { origin_asn: number; update_count: number }[]
  top_active_prefixes: { prefix: string; change_count: number }[]
}

export interface CommunitySignal {
  signal_id: string; source: string
  urgency_score: number; sentiment: string; correlation_score: number
  entities: { asns: number[]; prefixes: string[]; orgs: string[] }
  matched_anomalies: { event_type: string; prefix: string; severity: number }[]
  title?: string; text?: string; subject?: string; collected_at: string
}

export interface HealthScore {
  health_score: number; timestamp: string
  z_scores: { update_rate: number; withdrawal_rate: number; prefix_diversity: number }
  severity: { overall: number }
  alerts: (string | null)[]
  current: { updates_5m: number; withdrawals_5m: number }
}

export interface PathResult {
  source_asn: number; dest_asn: number; path_count: number
  paths: { asns: number[]; hops: number; path_type: string; prefix?: string }[]
  stability: { score: number; path_count: number; dominant_pct: number }
}

export const api = {
  status:         ()              => get<GlobalStatus>('/status'),
  bgpSummary:     ()              => get<BGPSummary>('/bgp/summary'),
  bgpAnomalies:   (limit = 50)   => get<{ count: number; anomalies: BGPAnomaly[] }>(`/bgp/anomalies?limit=${limit}&severity_min=1`),
  bgpUpdates:     (limit = 50)   => get<{ count: number; updates: BGPUpdate[] }>(`/bgp/updates?limit=${limit}`),
  eventFeed:      (limit = 50)   => get<{ count: number; feed: any[] }>(`/events/feed?limit=${limit}`),
  community:      (limit = 30)   => get<{ count: number; total: number; signals: CommunitySignal[] }>(`/events/community?limit=${limit}&urgency_min=0.1`),
  healthScore:    ()              => get<HealthScore>('/intelligence/health-score'),
  anomalyZscores: ()              => get<any>('/intelligence/anomaly-zscores?hours=6'),
  asProfile:      (asn: number)  => get<any>(`/intelligence/as-profile/${asn}`),
  pathAnalysis:     (src: number, dst: number) => get<PathResult>(`/intelligence/path?source_asn=${src}&dest_asn=${dst}`),
  // Lifecycle + cross-reference (added for EventFeed / EventRail)
  eventLifecycle:   (status = 'all', limit = 100) => get<any>(`/events/lifecycle?status=${status}&limit=${limit}`),
  crossReference:   (windowM = 60)               => get<any>(`/events/cross-reference?window_m=${windowM}`),
  globeNodes:       (windowM = 30)                => get<any>(`/globe/nodes?window_m=${windowM}`),
  globeArcs:        (windowM = 5)                 => get<any>(`/globe/arcs?window_m=${windowM}`),
  globePathHops:    (srcAsn: number, dstAsn?: number) => get<any>(
    `/globe/path-hops?src_asn=${srcAsn}${dstAsn ? `&dst_asn=${dstAsn}` : ''}`
  ),
  communityCorrelated: (hours = 48, limit = 50)  => get<any>(`/events/community-correlated?hours=${hours}&limit=${limit}`),
  baseline:         (hours = 6)                   => get<any>(`/intelligence/baseline?hours=${hours}`),
}
