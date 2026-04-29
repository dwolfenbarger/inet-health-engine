// src/hooks/useBreakpoint.ts — reactive viewport width hook
import { useState, useEffect } from 'react'

export function useBreakpoint(maxWidth: number): boolean {
  const [isMobile, setIsMobile] = useState(() => window.innerWidth <= maxWidth)
  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${maxWidth}px)`)
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches)
    setIsMobile(mq.matches)
    mq.addEventListener('change', handler)
    return () => mq.removeEventListener('change', handler)
  }, [maxWidth])
  return isMobile
}
