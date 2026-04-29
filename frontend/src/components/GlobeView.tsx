// src/components/GlobeView.tsx — Three.js globe with CSS2D AS labels + live auto-rotate
import { useEffect, useRef, useCallback } from 'react'
import { useBreakpoint } from '../hooks/useBreakpoint'
import * as THREE from 'three'
import { CSS2DRenderer, CSS2DObject } from 'three/addons/renderers/CSS2DRenderer.js'
import { useNOCStore } from '../store/nocStore'
import COUNTRIES from '../lib/countries.json'
import CABLES   from '../lib/cables.json'
import LANDING  from '../lib/landing.json'
import { AS_META } from '../lib/asData'

const GLOBE_R = 1.5
const CAM_MIN = 2.4
const CAM_MAX = 7.5
const CAM_DEF = 4.6

function ll3(lat: number, lon: number, r = GLOBE_R): THREE.Vector3 {
  const phi   = (90 - lat)  * (Math.PI / 180)
  const theta = (lon + 180) * (Math.PI / 180)
  return new THREE.Vector3(
    -r * Math.sin(phi) * Math.cos(theta),
     r * Math.cos(phi),
     r * Math.sin(phi) * Math.sin(theta)
  )
}

function surfaceArc(
  fromLat: number, fromLon: number, toLat: number, toLon: number,
  lift = 0.035, n = 80
): THREE.Vector3[] {
  const s = ll3(fromLat, fromLon), e = ll3(toLat, toLon)
  return Array.from({ length: n + 1 }, (_, i) => {
    const t = i / n
    const pt = new THREE.Vector3().lerpVectors(s, e, t)
    pt.normalize().multiplyScalar(GLOBE_R + Math.sin(Math.PI * t) * lift)
    return pt
  })
}

function pathArc(fromLat: number, fromLon: number, toLat: number, toLon: number): THREE.Vector3[] {
  const sep = ll3(fromLat, fromLon).angleTo(ll3(toLat, toLon))
  return surfaceArc(fromLat, fromLon, toLat, toLon, Math.min(sep * 0.18, 0.15), 120)
}

function buildCountryOutlines(group: THREE.Group) {
  const R   = GLOBE_R * 1.0015
  const mat = new THREE.LineBasicMaterial({ color: 0x2a8abf, transparent: true, opacity: 0.9 })
  for (const country of COUNTRIES as any[]) {
    for (const ring of (country.r as number[][][])) {
      if (ring.length < 3) continue
      const pts = ring.map(([lon, lat]: number[]) => ll3(lat, lon, R))
      pts.push(pts[0].clone())
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat))
    }
  }
}

function buildFiberLayer(group: THREE.Group): THREE.Group {
  const fiberGroup = new THREE.Group()
  fiberGroup.name = 'fiber'
  const R = GLOBE_R * 1.003
  for (const cable of CABLES as any[]) {
    const col = parseInt((cable.c as string).replace('#', ''), 16)
    const mat = new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.72 })
    for (const line of (cable.l as number[][][])) {
      if (line.length < 2) continue
      // Geodesic interpolation: arc each consecutive waypoint pair so cables
      // hug the globe surface rather than cutting through it as straight chords.
      const allPts: THREE.Vector3[] = []
      for (let i = 0; i < line.length - 1; i++) {
        const [lon0, lat0] = line[i]
        const [lon1, lat1] = line[i + 1]
        const a = ll3(lat0, lon0, R), b = ll3(lat1, lon1, R)
        const sep = a.clone().normalize().angleTo(b.clone().normalize())
        const steps = Math.max(4, Math.min(32, Math.round(sep * 40)))
        for (let s = 0; s < steps; s++) {
          const t = s / steps
          const pt = new THREE.Vector3().lerpVectors(a, b, t)
          pt.normalize().multiplyScalar(R)
          allPts.push(pt)
        }
      }
      allPts.push(ll3(line[line.length-1][1], line[line.length-1][0], R))
      fiberGroup.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(allPts), mat))
    }
  }
  const dotGeo = new THREE.SphereGeometry(0.007, 4, 4)
  const dotMat = new THREE.MeshBasicMaterial({ color: 0x00ffcc })
  for (const [lon, lat] of LANDING as any[]) {
    const dot = new THREE.Mesh(dotGeo, dotMat)
    dot.position.copy(ll3(lat as number, lon as number, R + 0.004))
    fiberGroup.add(dot)
  }
  group.add(fiberGroup)
  return fiberGroup
}

interface GlobeNode {
  asn: number; name: string; lat: number; lon: number; country: string
  updates: number; intensity: number; withdrawals: number
}
interface GlobeArc {
  event_type: string; origin_asn: number; expected_asn: number | null
  affected_prefix: string | null; confidence: number; severity: number
  src_lat: number; src_lon: number; dst_lat: number; dst_lon: number
  has_dst: boolean
}
const ARC_COLORS: Record<string, number> = {
  bgp_hijack: 0xffdd00, route_leak: 0xff8800,
  bgp_flap: 0xff3b3b, withdrawal_surge: 0xff6600, outage: 0xcc0000,
}
function nodeColor(intensity: number, withdrawals: number): number {
  if (withdrawals > 50) return 0xff8800
  if (intensity > 0.5)  return 0xff3b3b
  if (intensity > 0.15) return 0xffaa00
  return 0x00aaff
}

export function GlobeView() {
  const mountRef     = useRef<HTMLDivElement>(null)
  const rendRef      = useRef<THREE.WebGLRenderer | null>(null)
  const labelRendRef = useRef<CSS2DRenderer | null>(null)
  const camRef       = useRef<THREE.PerspectiveCamera | null>(null)
  const groupRef     = useRef<THREE.Group | null>(null)
  const fiberRef     = useRef<THREE.Group | null>(null)
  const nodeMapRef   = useRef<Map<number, THREE.Mesh>>(new Map())
  const labelMapRef  = useRef<Map<number, CSS2DObject>>(new Map())
  const ringMapRef   = useRef<Map<number, { mesh: THREE.Mesh; phase: number }>>(new Map())
  const arcMapRef    = useRef<Map<string, { line: THREE.Line; pts: THREE.Vector3[]; prog: number; spd: number }>>(new Map())
  const pathLineRef  = useRef<THREE.Line | null>(null)
  const rotRef       = useRef({ x: 0, y: 0.3, drag: false, px: 0, py: 0 })
  const camZRef      = useRef(CAM_DEF)
  const rafRef       = useRef(0)
  // Ref for auto-rotate — escapes stale closure in animate loop
  const autoRotateRef = useRef(false)
  const showLabelsRef = useRef(true)

  const { controls, setSelectedASN, selectedASN,
          pathMode, pathSrcASN, pathDstASN, setPathSrc, setPathDst, traceHops } = useNOCStore()
  const isMobile = useBreakpoint(768)

  // Sync controls into refs so the animate loop always sees current values
  useEffect(() => { autoRotateRef.current = controls.globeAutoRotate }, [controls.globeAutoRotate])
  useEffect(() => { showLabelsRef.current = controls.showLabels ?? true }, [controls.showLabels])

  // ── Static scene ──────────────────────────────────────────────────────
  useEffect(() => {
    if (!mountRef.current) return
    const mount = mountRef.current
    const W = mount.clientWidth || 700, H = mount.clientHeight || 500

    // WebGL renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setSize(W, H)
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2))
    mount.appendChild(renderer.domElement)
    rendRef.current = renderer

    // CSS2D renderer — overlays HTML labels on top of the canvas
    const labelRenderer = new CSS2DRenderer()
    labelRenderer.setSize(W, H)
    labelRenderer.domElement.style.position = 'absolute'
    labelRenderer.domElement.style.top = '0'
    labelRenderer.domElement.style.left = '0'
    labelRenderer.domElement.style.pointerEvents = 'none'
    mount.appendChild(labelRenderer.domElement)
    labelRendRef.current = labelRenderer

    const scene  = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 100)
    camera.position.set(0, 0, CAM_DEF)
    camRef.current  = camera
    camZRef.current = CAM_DEF

    const group = new THREE.Group()
    scene.add(group)
    groupRef.current = group

    scene.add(new THREE.AmbientLight(0x223344, 3))
    const sun = new THREE.DirectionalLight(0x6699bb, 1.8)
    sun.position.set(5, 3, 5); scene.add(sun)

    // Globe sphere
    group.add(new THREE.Mesh(
      new THREE.SphereGeometry(GLOBE_R, 64, 64),
      new THREE.MeshStandardMaterial({
        color: 0x061422, roughness: 0.8, metalness: 0.1,
        emissive: new THREE.Color(0x0a1f2e), emissiveIntensity: 0.3,
      })
    ))
    // Atmosphere
    group.add(new THREE.Mesh(
      new THREE.SphereGeometry(GLOBE_R * 1.04, 32, 32),
      new THREE.MeshPhongMaterial({ color: 0x1a55bb, emissive: 0x071030,
        transparent: true, opacity: 0.08, side: THREE.BackSide })
    ))

    // Enhanced visuals
    const cyanLight = new THREE.PointLight(0x00d4ff, 0.3)
    cyanLight.position.set(-10, -10, -10)
    scene.add(cyanLight)

    const outerGlow = new THREE.Mesh(
      new THREE.SphereGeometry(GLOBE_R * 1.095, 24, 24),
      new THREE.MeshBasicMaterial({ color: 0x00d4ff, side: THREE.BackSide, transparent: true, opacity: 0.13 })
    )
    scene.add(outerGlow)

    const innerGlow = new THREE.Mesh(
      new THREE.SphereGeometry(GLOBE_R * 1.060, 24, 24),
      new THREE.MeshBasicMaterial({ color: 0x00a8cc, side: THREE.BackSide, transparent: true, opacity: 0.08 })
    )
    scene.add(innerGlow)

    const starCount = 600
    const starPos = new Float32Array(starCount * 3)
    const starCol = new Float32Array(starCount * 3)
    for (let i = 0; i < starCount; i++) {
      const r = 50 + Math.random() * 50
      const theta = Math.random() * Math.PI * 2
      const phi   = Math.acos(2 * Math.random() - 1)
      starPos[i*3]   = r * Math.sin(phi) * Math.cos(theta)
      starPos[i*3+1] = r * Math.sin(phi) * Math.sin(theta)
      starPos[i*3+2] = r * Math.cos(phi)
      const b = 0.5 + Math.random() * 0.5
      starCol[i*3] = b; starCol[i*3+1] = b; starCol[i*3+2] = b
    }
    const starGeo = new THREE.BufferGeometry()
    starGeo.setAttribute('position', new THREE.BufferAttribute(starPos, 3))
    starGeo.setAttribute('color',    new THREE.BufferAttribute(starCol, 3))
    const starField = new THREE.Points(starGeo,
      new THREE.PointsMaterial({ size: 0.08, vertexColors: true, transparent: true }))
    scene.add(starField)

    // Country outlines + grid
    buildCountryOutlines(group)
    const gm = new THREE.LineBasicMaterial({ color: 0x1a5070, transparent: true, opacity: 0.55 })
    for (let lat = -75; lat <= 75; lat += 30)
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 91 }, (_, i) => ll3(lat, i * 4 - 180, GLOBE_R * 1.0005))), gm))
    for (let lon = 0; lon < 360; lon += 30)
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(
        Array.from({ length: 61 }, (_, i) => ll3(i * 3 - 90, lon - 180, GLOBE_R * 1.0005))), gm))

    fiberRef.current = buildFiberLayer(group)
    fiberRef.current.visible = false

    // Drag + zoom
    const rot = rotRef.current
    const onDown = (e: MouseEvent) => { rot.drag=true; rot.px=e.clientX; rot.py=e.clientY }
    const onUp   = () => { rot.drag = false }
    const onMove = (e: MouseEvent) => {
      if (!rot.drag) return
      rot.y += (e.clientX - rot.px) * 0.005
      rot.x  = Math.max(-1.2, Math.min(1.2, rot.x + (e.clientY - rot.py) * 0.004))
      rot.px = e.clientX; rot.py = e.clientY
    }
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      camZRef.current = Math.max(CAM_MIN, Math.min(CAM_MAX, camZRef.current + e.deltaY * 0.005))
    }

    // ── Touch support: single-finger rotate, two-finger pinch-zoom ────────────
    let pinchDist0 = 0
    let pinchCam0  = 0
    const getTouchDist = (t: TouchList) => {
      const dx = t[0].clientX - t[1].clientX
      const dy = t[0].clientY - t[1].clientY
      return Math.sqrt(dx*dx + dy*dy)
    }
    const onTouchStart = (e: TouchEvent) => {
      e.preventDefault()
      if (e.touches.length === 1) {
        rot.drag = true
        rot.px = e.touches[0].clientX
        rot.py = e.touches[0].clientY
      } else if (e.touches.length === 2) {
        rot.drag = false
        pinchDist0 = getTouchDist(e.touches)
        pinchCam0  = camZRef.current
      }
    }
    const onTouchMove = (e: TouchEvent) => {
      e.preventDefault()
      if (e.touches.length === 1 && rot.drag) {
        // Single finger: rotate globe
        rot.y += (e.touches[0].clientX - rot.px) * 0.005
        rot.x  = Math.max(-1.2, Math.min(1.2, rot.x + (e.touches[0].clientY - rot.py) * 0.004))
        rot.px = e.touches[0].clientX
        rot.py = e.touches[0].clientY
      } else if (e.touches.length === 2) {
        // Two fingers: pinch to zoom
        const dist  = getTouchDist(e.touches)
        const scale = pinchDist0 / dist   // larger dist = zoom in = smaller camZ
        camZRef.current = Math.max(CAM_MIN, Math.min(CAM_MAX, pinchCam0 * scale))
      }
    }
    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length === 0) rot.drag = false
      if (e.touches.length === 1) {
        // Switched from pinch to single finger: reset rotate anchor
        rot.px = e.touches[0].clientX
        rot.py = e.touches[0].clientY
      }
    }
    // touch-action:none lets Three.js own all touch events on the canvas
    renderer.domElement.style.touchAction = 'none'
    renderer.domElement.addEventListener('touchstart', onTouchStart, { passive: false })
    renderer.domElement.addEventListener('touchmove',  onTouchMove,  { passive: false })
    renderer.domElement.addEventListener('touchend',   onTouchEnd,   { passive: false })
    // ─────────────────────────────────────────────────────────────────────────

    renderer.domElement.addEventListener('mousedown', onDown)
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false })
    window.addEventListener('mouseup', onUp)
    window.addEventListener('mousemove', onMove)

    const handleResize = () => {
      const w = mount.clientWidth, h = mount.clientHeight
      renderer.setSize(w, h)
      labelRenderer.setSize(w, h)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    }
    window.addEventListener('resize', handleResize)

    // Idle rendering
    let frame = 0
    let lastInteraction = performance.now()
    let rafPaused = false
    const wakeGlobe = () => {
      lastInteraction = performance.now()
      if (rafPaused) { rafPaused = false; animate() }
    }
    renderer.domElement.addEventListener('mousedown', wakeGlobe, { passive: true })
    renderer.domElement.addEventListener('touchstart', wakeGlobe, { passive: true })
    renderer.domElement.addEventListener('mousemove', wakeGlobe, { passive: true })
    const onVisibility = () => {
      if (document.hidden) { rafPaused = true; cancelAnimationFrame(rafRef.current) }
      else wakeGlobe()
    }
    document.addEventListener('visibilitychange', onVisibility)

    const animate = () => {
      const idleMs = performance.now() - lastInteraction
      // Use ref — not captured controls — so toggle changes take effect immediately
      if (idleMs > 15000 && !rot.drag && !autoRotateRef.current) {
        rafPaused = true; return
      }
      rafRef.current = requestAnimationFrame(animate)
      frame++

      outerGlow.rotation.y += 0.0003
      if (frame % 2 === 0) starField.rotation.y += 0.00005

      const cam = camRef.current!
      cam.position.z += (camZRef.current - cam.position.z) * 0.12

      // Auto-rotate via ref — always current, never stale
      if (!rot.drag && autoRotateRef.current) rot.y += 0.0015
      group.rotation.y = rot.y
      group.rotation.x = rot.x

      const t = frame * 0.018
      for (const rk of ringMapRef.current.values()) {
        const fc   = (rk.mesh as any).userData?.flapCount ?? 0
        const spd  = fc > 0 ? 2.2 + Math.min(fc * 0.3, 4.0) : 2.2
        const amp  = fc > 0 ? 0.5 + Math.min(fc * 0.05, 0.4) : 0.3
        const sc   = 1 + amp * Math.sin(t * spd + rk.phase)
        rk.mesh.scale.setScalar(sc)
        const baseOpacity = fc > 0 ? 0.35 : 0.2
        ;(rk.mesh.material as THREE.MeshBasicMaterial).opacity =
          baseOpacity + 0.22 * Math.sin(t * 1.8 + rk.phase)
      }
      for (const ao of arcMapRef.current.values()) {
        ao.prog = (ao.prog + ao.spd) % 1
        const tail = 0.18, n = ao.pts.length - 1
        const si = Math.floor(Math.max(0, ao.prog - tail) * n)
        const ei = Math.floor(ao.prog * n)
        const slice = ao.pts.slice(si, Math.min(ei + 1, n + 1))
        if (slice.length >= 2) {
          ao.line.geometry.setFromPoints(slice)
          ao.line.geometry.attributes.position.needsUpdate = true
        }
      }

      // Scale labels with zoom + back-face cull: hide nodes on far side of globe
      const zoomFactor = Math.max(0, Math.min(1, (CAM_MAX - cam.position.z) / (CAM_MAX - CAM_MIN)))
      const showLabels = showLabelsRef.current && zoomFactor > 0.25
      // Camera look direction in world space (points away from camera into scene)
      const camFwd = new THREE.Vector3(0, 0, -1).applyQuaternion(cam.quaternion)
      for (const [asn, label] of labelMapRef.current) {
        // Find the node mesh world position to determine if it faces the camera
        const nodeMesh = nodeMapRef.current.get(asn)
        let onFront = true
        if (nodeMesh) {
          const worldPos = new THREE.Vector3()
          nodeMesh.getWorldPosition(worldPos)
          // dot > 0 means node normal and camera direction agree → node faces camera
          // threshold -0.05 gives a small fade margin at the limb
          onFront = worldPos.normalize().dot(camFwd.clone().negate()) > 0.15
        }
        const visible = showLabels && onFront
        label.visible = visible
        const el = label.element as HTMLDivElement
        if (!visible) {
          el.style.display = 'none'
        } else {
          const scale = 0.6 + zoomFactor * 0.6
          el.style.display = ''
          el.style.transform = `scale(${scale.toFixed(2)})`
          el.style.opacity = String(Math.min(1, (zoomFactor - 0.25) * 4))
        }
      }

      renderer.render(scene, camera)
      labelRenderer.render(scene, camera)
    }
    animate()

    return () => {
      cancelAnimationFrame(rafRef.current)
      for (const obj of [outerGlow, innerGlow, starField, cyanLight]) {
        scene.remove(obj)
        if ((obj as any).geometry) (obj as any).geometry.dispose()
        if ((obj as any).material) (obj as any).material.dispose()
      }
      for (const [, label] of labelMapRef.current) { label.element.remove() }
      labelMapRef.current.clear()
      renderer.domElement.removeEventListener('mousedown', wakeGlobe)
      renderer.domElement.removeEventListener('touchstart', wakeGlobe)
      renderer.domElement.removeEventListener('mousemove', wakeGlobe)
      document.removeEventListener('visibilitychange', onVisibility)
      renderer.domElement.removeEventListener('touchstart', onTouchStart)
      renderer.domElement.removeEventListener('touchmove',  onTouchMove)
      renderer.domElement.removeEventListener('touchend',   onTouchEnd)
      renderer.domElement.removeEventListener('mousedown', onDown)
      renderer.domElement.removeEventListener('wheel', onWheel)
      window.removeEventListener('mouseup', onUp)
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('resize', handleResize)
      if (mount.contains(labelRenderer.domElement)) mount.removeChild(labelRenderer.domElement)
      renderer.dispose()
      if (mount.contains(renderer.domElement)) mount.removeChild(renderer.domElement)
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Fiber toggle ───────────────────────────────────────────────────────
  useEffect(() => {
    if (fiberRef.current) fiberRef.current.visible = controls.showFiber
  }, [controls.showFiber])

  // ── Live nodes + arcs ─────────────────────────────────────────────────
  useEffect(() => {
    const group = groupRef.current; if (!group) return
    const updateGlobe = async () => {
      try {
        const winM = Math.min(Math.max(Math.round(controls.timeWindowH * 60), 1), 30)
        const [nr, ar] = await Promise.all([
          fetch(`/api/v1/globe/nodes?window_m=${winM}`),
          fetch('/api/v1/globe/arcs?window_m=5'),
        ])
        if (!nr.ok || !ar.ok) return
        const nd: { nodes: GlobeNode[] } = await nr.json()
        const ad: { arcs:  GlobeArc[]  } = await ar.json()
        const nodeMap  = nodeMapRef.current
        const labelMap = labelMapRef.current
        const ringMap  = ringMapRef.current
        const seenASNs = new Set<number>()

        for (const n of (nd.nodes ?? [])) {
          seenASNs.add(n.asn)
          const col   = nodeColor(n.intensity, n.withdrawals)
          const isSel = n.asn === selectedASN
          const pos   = ll3(n.lat, n.lon, GLOBE_R)

          if (!nodeMap.has(n.asn)) {
            const radius = Math.min(0.010 + n.intensity * 0.018, 0.028)
            const sphere = new THREE.Mesh(
              new THREE.SphereGeometry(radius, 8, 8),
              new THREE.MeshBasicMaterial({ color: isSel ? 0xffffff : col })
            )
            sphere.position.copy(ll3(n.lat, n.lon, GLOBE_R + radius * 0.5))
            sphere.userData = { asn: n.asn, name: n.name, country: n.country, lat: n.lat, lon: n.lon }
            group.add(sphere)
            nodeMap.set(n.asn, sphere)

            // CSS2D label — 3-tier visual hierarchy:
            //   Tier 1 (major transit/hyperscaler): bright cyan, 10px, always visible
            //   Tier 2 (active anomaly, intensity>0.4): amber/red, 10px bold, pulsing border
            //   Tier 3 (standard): dim, 8px, zoom-only
            const staticMeta = (AS_META as any)[n.asn]
            const isMajorTransit = staticMeta?.tier === 1
            const isActiveAnomaly = n.intensity > 0.4
            const labelTier = isMajorTransit ? 1 : isActiveAnomaly ? 2 : 3

            const tierStyle: Record<number, string> = {
              1: [
                'pointer-events:none', 'font-family:monospace',
                'font-size:10px', 'font-weight:700', 'line-height:1.3',
                'padding:2px 6px',
                'background:rgba(0,20,40,0.88)',
                'border:1px solid #00ccee99',
                'border-radius:3px',
                'color:#00eeff',
                'white-space:nowrap',
                'text-shadow:0 0 8px #00ccff99',
                'transform-origin:bottom center',
                'user-select:none',
              ].join(';'),
              2: [
                'pointer-events:none', 'font-family:monospace',
                'font-size:10px', 'font-weight:700', 'line-height:1.3',
                'padding:2px 6px',
                'background:rgba(28,8,4,0.90)',
                `border:1px solid ${col.toString(16).padStart(6,'0').replace(/^/,'#')}99`,
                'border-radius:3px',
                `color:#ffcc88`,
                'white-space:nowrap',
                'text-shadow:0 0 6px #ff880066',
                'transform-origin:bottom center',
                'user-select:none',
              ].join(';'),
              3: [
                'pointer-events:none', 'font-family:monospace',
                'font-size:8px', 'line-height:1.3',
                'padding:1px 4px',
                'background:rgba(2,8,16,0.75)',
                'border:1px solid #0d2030',
                'border-radius:2px',
                'color:#4a8090',
                'white-space:nowrap',
                'transform-origin:bottom center',
                'user-select:none',
              ].join(';'),
            }

            const div = document.createElement('div')
            div.className = `globe-label globe-label-t${labelTier}`
            div.style.cssText = tierStyle[labelTier]
            const label_asn  = n.name && n.name !== `AS${n.asn}` ? n.name : `AS${n.asn}`
            const label_ctry = n.country ? ` · ${n.country}` : ''
            div.textContent  = `${label_asn}${label_ctry}`
            const label = new CSS2DObject(div)
            // Tier 1 labels float higher so they don't crowd transit hubs
            const labelLift = labelTier === 1 ? 0.10 : labelTier === 2 ? 0.08 : 0.06
            label.position.copy(pos.clone().normalize().multiplyScalar(GLOBE_R + labelLift))
            group.add(label)
            labelMap.set(n.asn, label)

            if (n.intensity > 0.05) {
              const rr   = radius * 2.0
              const ring = new THREE.Mesh(
                new THREE.RingGeometry(rr, rr * 1.4, 24),
                new THREE.MeshBasicMaterial({ color: col, side: THREE.DoubleSide, transparent: true, opacity: 0.3 })
              )
              ring.position.copy(sphere.position)
              ring.lookAt(new THREE.Vector3(0, 0, 0))
              group.add(ring)
              ringMap.set(n.asn, { mesh: ring, phase: Math.random() * Math.PI * 2 })
            }
          } else {
            const mesh = nodeMap.get(n.asn)!
            ;(mesh.material as THREE.MeshBasicMaterial).color.setHex(isSel ? 0xffffff : col)
            mesh.scale.setScalar(isSel ? 1.6 : 1 + n.intensity * 0.35)
            // Update label colour on selection
            const lbl = labelMap.get(n.asn)
            if (lbl) {
              const el  = lbl.element as HTMLDivElement
              const t3  = el.className.includes('globe-label-t3')
              const t1  = el.className.includes('globe-label-t1')
              if (isSel) {
                el.style.color       = '#ffffff'
                el.style.borderColor = '#ffffff88'
                el.style.fontWeight  = '700'
                el.style.background  = 'rgba(0,30,60,0.95)'
              } else {
                el.style.color       = t1 ? '#00eeff' : t3 ? '#4a8090' : '#ffcc88'
                el.style.borderColor = t1 ? '#00ccee99' : t3 ? '#0d2030' : ''
                el.style.fontWeight  = t3 ? '400' : '700'
                el.style.background  = t3 ? 'rgba(2,8,16,0.75)' : t1 ? 'rgba(0,20,40,0.88)' : 'rgba(28,8,4,0.90)'
              }
            }
          }
        }

        // Remove stale nodes + labels
        for (const [asn, mesh] of nodeMap.entries()) {
          if (!seenASNs.has(asn)) {
            group.remove(mesh); nodeMap.delete(asn)
            const lbl = labelMap.get(asn)
            if (lbl) { group.remove(lbl); lbl.element.remove(); labelMap.delete(asn) }
            const rk = ringMap.get(asn)
            if (rk)  { group.remove(rk.mesh); ringMap.delete(asn) }
          }
        }

        // Arcs + flap ring pulse tracking
        // bgp_flap events (has_dst=false) are NOT drawn as arcs - they are local
        // instability with no meaningful destination. Count flaps per ASN to drive
        // faster ring oscillation on the origin node instead.
        const arcMap = arcMapRef.current, seenIds = new Set<string>()
        const flapIntensity = new Map<number, number>()
        for (const a of (ad.arcs ?? [])) {
          if (a.severity < controls.severityMin)     continue
          if (a.confidence < controls.confidenceMin) continue
          if (!controls.showHijacks && a.event_type === 'bgp_hijack')       continue
          if (!controls.showSurges  && a.event_type === 'withdrawal_surge') continue
          if (a.event_type === 'bgp_flap') {
            if (controls.showFlaps)
              flapIntensity.set(a.origin_asn, (flapIntensity.get(a.origin_asn) ?? 0) + 1)
            continue
          }
          if (!a.has_dst) continue
          const id = `${a.event_type}-${a.origin_asn}-${a.expected_asn}-${a.affected_prefix}`
          seenIds.add(id)
          if (!arcMap.has(id)) {
            const pts  = surfaceArc(a.src_lat, a.src_lon, a.dst_lat, a.dst_lon)
            const col  = ARC_COLORS[a.event_type] ?? 0x4488aa
            const line = new THREE.Line(
              new THREE.BufferGeometry().setFromPoints(pts.slice(0, 2)),
              new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.85 })
            )
            group.add(line)
            arcMap.set(id, { line, pts, prog: Math.random(), spd: (0.005 + Math.random() * 0.005) * controls.arcSpeed })
          }
        }
        for (const [id, ao] of arcMap.entries()) {
          if (!seenIds.has(id)) { group.remove(ao.line); arcMap.delete(id) }
        }
        // Drive ring pulse intensity from flap count per ASN
        for (const [asn, cnt] of flapIntensity) {
          const rk = ringMapRef.current.get(asn)
          if (rk) {
            ;(rk.mesh as any).userData.flapCount = cnt
            ;(rk.mesh.material as THREE.MeshBasicMaterial).color.setHex(0xff3b3b)
          }
        }
        for (const [asn, rk] of ringMapRef.current) {
          if (!flapIntensity.has(asn) && (rk.mesh as any).userData?.flapCount) {
            ;(rk.mesh as any).userData.flapCount = 0
            ;(rk.mesh.material as THREE.MeshBasicMaterial).color.setHex(0x00aaff)
          }
        }
      } catch (e) { console.warn('Globe fetch failed', e) }
    }
    updateGlobe()
    const iv = setInterval(updateGlobe, 15000)
    return () => clearInterval(iv)
  }, [controls, selectedASN]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Path hops ─────────────────────────────────────────────────────────
  const hopLinesRef = useRef<THREE.Line[]>([])
  useEffect(() => {
    const group = groupRef.current; if (!group) return
    hopLinesRef.current.forEach(l => group.remove(l))
    for (const [id] of arcMapRef.current.entries())
      if (id.startsWith('path-hop-')) arcMapRef.current.delete(id)
    hopLinesRef.current = []
    if (pathLineRef.current) { group.remove(pathLineRef.current); pathLineRef.current = null }
    if (!pathSrcASN) return
    const url = pathDstASN
      ? `/api/v1/globe/path-hops?src_asn=${pathSrcASN}&dst_asn=${pathDstASN}`
      : `/api/v1/globe/path-hops?src_asn=${pathSrcASN}`
    fetch(url).then(r => r.json()).then((data: any) => {
      const g = groupRef.current; if (!g) return
      const paths = data.paths ?? []
      if (!paths.length) {
        const sm = nodeMapRef.current.get(pathSrcASN)
        const dm = pathDstASN ? nodeMapRef.current.get(pathDstASN) : null
        if (sm && dm) {
          const pts  = pathArc(sm.userData.lat, sm.userData.lon, dm.userData.lat, dm.userData.lon)
          const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
            new THREE.LineBasicMaterial({ color: 0xaa44ff, transparent: true, opacity: 0.85 }))
          g.add(line); pathLineRef.current = line
        }
        return
      }
      const knownHops = (paths[0].hops as any[]).filter((h: any) => h.known)
      const HOP_COLORS = [0xaa44ff, 0xcc66ff, 0xdd88ff, 0xee99ff, 0xffeeff]
      knownHops.forEach((h1: any, i: number) => {
        if (i >= knownHops.length - 1) return
        const h2   = knownHops[i + 1]
        const pts  = surfaceArc(h1.lat, h1.lon, h2.lat, h2.lon, 0.05, 80)
        const col  = HOP_COLORS[Math.min(i, HOP_COLORS.length - 1)]
        const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts.slice(0, 2)),
          new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.95 }))
        g.add(line); hopLinesRef.current.push(line)
        arcMapRef.current.set(`path-hop-${i}-${h1.asn}-${h2.asn}`,
          { line, pts, prog: (i / knownHops.length) * 0.6, spd: 0.010 * controls.arcSpeed })
      })
    }).catch(() => {})
  }, [pathSrcASN, pathDstASN]) // eslint-disable-line react-hooks/exhaustive-deps

  // ── Click handler ──────────────────────────────────────────────────────
  const handleClick = useCallback((e: React.MouseEvent<HTMLDivElement>) => {
    if (!camRef.current) return
    const rect  = (e.currentTarget as HTMLDivElement).getBoundingClientRect()
    const mouse = new THREE.Vector2(
      ((e.clientX - rect.left) / rect.width)  *  2 - 1,
      -((e.clientY - rect.top)  / rect.height) *  2 + 1
    )
    const ray = new THREE.Raycaster()
    ray.setFromCamera(mouse, camRef.current)
    const hits = ray.intersectObjects([...nodeMapRef.current.values()])
    if (!hits.length || !hits[0].object.userData.asn) return
    const asn = hits[0].object.userData.asn as number
    if (pathMode) {
      if (!pathSrcASN)             setPathSrc(asn)
      else if (asn !== pathSrcASN) setPathDst(asn)
      else                         { setPathSrc(null); setPathDst(null) }
    } else {
      setSelectedASN(selectedASN === asn ? null : asn)
    }
  }, [pathMode, pathSrcASN, selectedASN, setSelectedASN, setPathSrc, setPathDst])

  const zoom = useCallback((dir: 1 | -1) => {
    camZRef.current = Math.max(CAM_MIN, Math.min(CAM_MAX, camZRef.current + dir * -0.6))
  }, [])

  // ── JSX ────────────────────────────────────────────────────────────────
  const btnStyle = {
    display:'flex', alignItems:'center', justifyContent:'center',
    width:28, height:28, background:'#06101ccc', border:'1px solid #0d2840',
    borderRadius:4, color:'#8fc4dc', fontSize:16, cursor:'pointer',
    fontFamily:'monospace', userSelect:'none' as const,
  }


  // Traceroute hop rendering: teal animated arcs between geo-resolved IP hops
  const traceLineRef = useRef<THREE.Line[]>([])
  useEffect(() => {
    const group = groupRef.current; if (!group) return
    traceLineRef.current.forEach(l => group.remove(l))
    traceLineRef.current = []
    const geoHops = (traceHops ?? []).filter((h: any) => h.lat != null && h.lon != null)
    if (geoHops.length < 2) return
    const TRACE_COL = [0x00ffcc, 0x00ddaa, 0x00bb88, 0x009966, 0x007744]
    geoHops.forEach((h1: any, i: number) => {
      if (i >= geoHops.length - 1) return
      const h2  = geoHops[i + 1]
      const pts = surfaceArc(h1.lat, h1.lon, h2.lat, h2.lon, 0.07, 80)
      const col = TRACE_COL[Math.min(i, TRACE_COL.length - 1)]
      const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts.slice(0, 2)),
        new THREE.LineBasicMaterial({ color: col, transparent: true, opacity: 0.95 })
      )
      group.add(line)
      traceLineRef.current.push(line)
      arcMapRef.current.set(`trace-hop-${i}`,
        { line, pts, prog: (i / geoHops.length) * 0.5, spd: 0.008 * controls.arcSpeed })
    })
  }, [traceHops]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ position:'relative', width:'100%', height:'100%',
      background:'#020c18', cursor:'grab' }} onClick={handleClick}>
      <div ref={mountRef} style={{ width:'100%', height:'100%' }} />

      {/* Zoom controls */}
      <div style={{ position:'absolute', top:12, right:12, display:'flex', flexDirection:'column', gap:4 }}>
        <div style={btnStyle} onClick={e => { e.stopPropagation(); zoom(1) }} title="Zoom in">+</div>
        <div style={btnStyle} onClick={e => { e.stopPropagation(); zoom(-1) }} title="Zoom out">−</div>
        <div style={{ ...btnStyle, fontSize:9, height:22, color:'#6aa8c0', letterSpacing:'.05em' }}
          onClick={e => { e.stopPropagation(); camZRef.current = CAM_DEF }} title="Reset zoom">⊙</div>
      </div>

      {/* Path mode hint */}
      {pathMode && (
        <div style={{ position:'absolute', top:10, left:'50%', transform:'translateX(-50%)',
          background:'#0a0820cc', border:'1px solid #aa44ff66', borderRadius:4,
          padding:'4px 14px', fontSize:'9px', color:'#aa44ff', fontFamily:'monospace',
          pointerEvents:'none' }}>
          {!pathSrcASN ? 'Click SOURCE AS on globe'
            : !pathDstASN ? `SRC: AS${pathSrcASN} — Click DEST AS`
            : `PATH: AS${pathSrcASN} → AS${pathDstASN}`}
        </div>
      )}

      {/* Legend — hidden on mobile (interactive layer toggles replace it) */}
      {!isMobile && <div style={{ position:'absolute', bottom:10, left:10, fontFamily:'monospace',
        background:'#020c18bb', padding:'6px 8px', borderRadius:4 }}>
        {([
          ['#ff3b3b','BGP Flap'], ['#ffdd00','Hijack'], ['#ff8800','Route Leak / Surge'],
          ['#00aaff','Active AS'], ['#aa44ff','Selected path'],
          ...(controls.showFiber ? [['#00ffcc','Submarine fiber']] : []),
        ] as [string,string][]).map(([c,l]) => (
          <div key={l} style={{ display:'flex', alignItems:'center', gap:5, marginBottom:3 }}>
            <div style={{ width:14, height:2, background:c, borderRadius:1 }} />
            <span style={{ color:'#7ab8d4', fontSize:'8px' }}>{l}</span>
          </div>
        ))}
      </div>}

      {!isMobile && <div style={{ position:'absolute', bottom:8, right:10,
        fontSize:'8px', color:'#8fc4dc', fontFamily:'monospace', pointerEvents:'none' }}>
        RIPE RIS · Natural Earth 110m · TeleGeography · scroll to zoom
      </div>}
    </div>
  )
}
