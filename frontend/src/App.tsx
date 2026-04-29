// src/App.tsx — NOC shell with P1 layout restructure
import { useEffect, useRef } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { NOCTopBar }     from './components/NOCTopBar'
import { ControlPanel }  from './components/ControlPanel'
import { GlobeView }     from './components/GlobeView'
import { EventRail }     from './components/EventRail'
import { EventFeed }     from './components/EventFeed'
import { ASSidebar }     from './components/ASSidebar'
import { BaselinePanel } from './components/BaselinePanel'
import { CommunityPanel }from './components/CommunityPanel'
import { PathPanel }     from './components/PathPanel'
import { VitalsStrip }   from './components/VitalsStrip'
import { useNOCStore }   from './store/nocStore'
import { MobileShell }   from './components/MobileShell'
import { useBreakpoint }  from './hooks/useBreakpoint'
import { api }           from './api/client'

const qc = new QueryClient({ defaultOptions:{ queries:{ staleTime:30000, retry:1 }}})

function DataPoller() {
  const { setAnomalies, setHealthScore, setUpdateRate1h } = useNOCStore()
  useQuery({ queryKey:['anomalies'], refetchInterval:20000,
    queryFn: async () => {
      const d = await api.bgpAnomalies(150)
      setAnomalies(d.anomalies as any); return d
    }
  })
  useQuery({ queryKey:['healthScore'], refetchInterval:30000,
    queryFn: async () => {
      const d = await api.healthScore()
      if (d.health_score !== null && d.health_score !== undefined)
        setHealthScore(d.health_score)
      return d
    }
  })
  useQuery({ queryKey:['summary'], refetchInterval:30000,
    queryFn: async () => {
      const d = await api.bgpSummary()
      setUpdateRate1h(d.updates_last_1h ?? 0); return d
    }
  })
  return null
}

function WSListener() {
  const { addLiveEvent, setWsConnected } = useNOCStore()
  const wsRef = useRef<WebSocket|null>(null)
  useEffect(() => {
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      const ws = new WebSocket(`${proto}://${location.host}/ws/events`)
      wsRef.current = ws
      ws.onopen  = () => setWsConnected(true)
      ws.onclose = () => { setWsConnected(false); setTimeout(connect, 5000) }
      ws.onmessage = e => {
        try {
          const m = JSON.parse(e.data)
          if (m.type === 'ping') return
          addLiveEvent({ id:`${Date.now()}-${Math.random()}`,
            stream:m.stream??'ws', data:m.data??m, timestamp:Date.now() })
        } catch {}
      }
    }
    connect()
    return () => wsRef.current?.close()
  }, [])
  return null
}

function MainLayout() {
  const { activeView, selectedASN, pathSrcASN } = useNOCStore()
  const showDrawer = !!(selectedASN || pathSrcASN)

  return (
    // Outer: full height, column flex so VitalsStrip sits at bottom
    <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden' }}>

      {/* Middle row: controls + content + event rail */}
      <div style={{ flex:1, display:'flex', overflow:'hidden', position:'relative' }}>

        {/* Left: collapsible control panel */}
        <ControlPanel />

        {/* Centre: main content area */}
        <div style={{ flex:1, position:'relative', overflow:'hidden' }}>

          {/* Globe view — always mounted, hidden when other views active */}
          <div style={{ position:'absolute', inset:0,
            visibility: activeView === 'globe' ? 'visible' : 'hidden',
            pointerEvents: activeView === 'globe' ? 'auto' : 'none' }}>
            <GlobeView />
          </div>

          {/* Secondary views overlay */}
          {activeView === 'feed' && (
            <div style={{ position:'absolute', inset:0, overflowY:'auto', zIndex:5 }}>
              <EventFeed />
            </div>
          )}
          {activeView === 'path' && (
            <div style={{ position:'absolute', inset:0, zIndex:5 }}>
              <PathPanel />
            </div>
          )}
          {activeView === 'baseline' && (
            <div style={{ position:'absolute', inset:0, zIndex:5 }}>
              <BaselinePanel />
            </div>
          )}
          {activeView === 'community' && (
            <div style={{ position:'absolute', inset:0, zIndex:5 }}>
              <CommunityPanel />
            </div>
          )}
        </div>

        {/* Right: event rail — always visible, not just on globe */}
        <EventRail />

        {/* AS detail drawer — slides in from right-of-rail, doesn't cover globe */}
        {showDrawer && (
          <div style={{ position:'absolute', right:300, top:0, bottom:0,
            zIndex:30, display:'flex', alignItems:'stretch' }}>
            <ASSidebar />
          </div>
        )}
      </div>

      {/* Bottom: persistent vitals strip */}
      <VitalsStrip />
    </div>
  )
}

function NOCApp() {
  const isMobile = useBreakpoint(768)
  if (isMobile) {
    return (
      <>
        <DataPoller />
        <WSListener />
        <MobileShell />
      </>
    )
  }
  return (
    <>
      <DataPoller />
      <WSListener />
      <div style={{ display:'flex', flexDirection:'column', height:'100vh',
        background:'#030810', overflow:'hidden',
        fontFamily:"'JetBrains Mono','Fira Code','Courier New',monospace" }}>
        <NOCTopBar />
        <MainLayout />
      </div>
    </>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <NOCApp />
    </QueryClientProvider>
  )
}
