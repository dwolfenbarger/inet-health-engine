// src/store/nocStore.ts — global NOC state
import { create } from 'zustand'

export type ActiveView = 'globe' | 'feed' | 'path' | 'baseline' | 'community'
export type EventType = 'bgp_flap' | 'bgp_hijack' | 'withdrawal_surge' | 'route_leak' | 'outage'
export type SeverityLevel = 1 | 2 | 3 | 4 | 5

export interface BGPAnomaly {
  time: string; event_id: string; event_type: EventType
  affected_prefix: string | null; origin_asn: number | null
  expected_asn: number | null; severity: SeverityLevel
  confidence: number; source: string
}

export interface LiveEvent {
  id: string; stream: string
  data: Record<string, string>; timestamp: number
}

export interface ControlState {
  // Layer toggles
  showFlaps: boolean; showHijacks: boolean
  showSurges: boolean; showClean: boolean; showRPKI: boolean
  // Filters
  severityMin: SeverityLevel; confidenceMin: number
  timeWindowH: number; collectorFilter: string[]
  // Viz
  arcSpeed: number; globeAutoRotate: boolean; showLabels: boolean; showFiber: boolean
}

interface NOCState {
  activeView: ActiveView
  setActiveView: (v: ActiveView) => void

  // Selected entities
  selectedASN: number | null
  setSelectedASN: (asn: number | null) => void
  pathSrcASN: number | null
  pathDstASN: number | null
  setPathSrc: (asn: number | null) => void
  setPathDst: (asn: number | null) => void
  pathMode: boolean
  setPathMode: (v: boolean) => void

  selectedAnomaly: BGPAnomaly | null
  setSelectedAnomaly: (a: BGPAnomaly | null) => void
  traceFromEvent: (a: BGPAnomaly) => void  // auto-wire path src/dst from anomaly
  traceHops: any[]
  setTraceHops: (hops: any[]) => void

  // Live data
  anomalies: BGPAnomaly[]
  setAnomalies: (a: BGPAnomaly[]) => void
  healthScore: number | null
  setHealthScore: (h: number | null) => void
  updateRate1h: number; setUpdateRate1h: (n: number) => void

  // WS
  liveEvents: LiveEvent[]
  addLiveEvent: (e: LiveEvent) => void
  wsConnected: boolean
  setWsConnected: (v: boolean) => void

  // Controls
  controls: ControlState
  setControl: <K extends keyof ControlState>(k: K, v: ControlState[K]) => void
  resetControls: () => void
}

const DEFAULT_CONTROLS: ControlState = {
  showFlaps: true, showHijacks: true, showSurges: true,
  showClean: true, showRPKI: false,
  severityMin: 1, confidenceMin: 0,
  timeWindowH: 1, collectorFilter: [],
  arcSpeed: 1.0, globeAutoRotate: true, showLabels: true, showFiber: false,
}

export const useNOCStore = create<NOCState>((set) => ({
  activeView: 'globe',
  setActiveView: (v) => set({ activeView: v }),

  selectedASN: null,
  setSelectedASN: (asn) => set({ selectedASN: asn }),
  pathSrcASN: null, pathDstASN: null,
  setPathSrc: (asn) => set({ pathSrcASN: asn }),
  setPathDst: (asn) => set({ pathDstASN: asn }),
  pathMode: false,
  setPathMode: (v) => set({ pathMode: v }),

  selectedAnomaly: null,
  setSelectedAnomaly: (a) => set({ selectedAnomaly: a }),
  traceHops: [],
  setTraceHops: (hops) => set({ traceHops: hops }),
  traceFromEvent: (a) => {
    // For hijacks: src = detected origin (attacker), dst = legitimate expected owner
    // For flaps/surges: src = origin AS, dst = null (single-ended trace)
    set({
      selectedAnomaly: a,
      selectedASN: a.origin_asn,
      pathSrcASN:  a.origin_asn,
      pathDstASN:  a.expected_asn ?? null,
    })
  },

  anomalies: [], setAnomalies: (a) => set({ anomalies: a }),
  healthScore: null, setHealthScore: (h) => set({ healthScore: h }),
  updateRate1h: 0, setUpdateRate1h: (n) => set({ updateRate1h: n }),

  liveEvents: [],
  addLiveEvent: (e) => set(s => ({ liveEvents: [e, ...s.liveEvents].slice(0, 200) })),
  wsConnected: false,
  setWsConnected: (v) => set({ wsConnected: v }),

  controls: DEFAULT_CONTROLS,
  setControl: (k, v) => set(s => ({ controls: { ...s.controls, [k]: v } })),
  resetControls: () => set({ controls: DEFAULT_CONTROLS }),
}))
