// src/lib/asData.ts — AS metadata + colour/label helpers
export interface ASMeta {
  name: string; short: string
  lat: number; lon: number
  color: string; tier: number; country: string
}

export const AS_META: Record<number, ASMeta> = {
  23969: { name:'VNPT Vietnam',    short:'VN-ISP', lat:21.03,  lon:105.85, color:'#ff3b3b', tier:2, country:'VN' },
  24164: { name:'VNPT-Corp',       short:'VN-CRP', lat:10.82,  lon:106.63, color:'#ff5544', tier:2, country:'VN' },
  45899: { name:'VNPT VN',         short:'VN-TEL', lat:16.08,  lon:108.22, color:'#ff2222', tier:2, country:'VN' },
  23736: { name:'AS-BANCA',        short:'BANC',   lat:40.41,  lon:-3.70,  color:'#ffaa00', tier:2, country:'ES' },
  20473: { name:'Vultr',           short:'VLTR',   lat:37.39,  lon:-121.9, color:'#aa55ff', tier:2, country:'US' },
  16509: { name:'Amazon AWS',      short:'AWS',    lat:47.60,  lon:-122.3, color:'#ff9900', tier:1, country:'US' },
  328608:{ name:'Africa-on-Cloud', short:'AFCL',   lat:-1.29,  lon:36.82,  color:'#ff3b3b', tier:2, country:'KE' },
  13335: { name:'Cloudflare',      short:'CF',     lat:37.77,  lon:-122.5, color:'#f6821f', tier:1, country:'US' },
  15169: { name:'Google',          short:'GOOG',   lat:37.42,  lon:-122.1, color:'#4285f4', tier:1, country:'US' },
  1299:  { name:'Telia',           short:'TEL',    lat:59.33,  lon:18.07,  color:'#aa44ff', tier:1, country:'SE' },
  3356:  { name:'Lumen',           short:'L3',     lat:39.73,  lon:-104.9, color:'#cc2200', tier:1, country:'US' },
  174:   { name:'Cogent',          short:'COG',    lat:38.90,  lon:-77.03, color:'#e54b4b', tier:1, country:'US' },
  2914:  { name:'NTT',             short:'NTT',    lat:35.68,  lon:139.69, color:'#e8380d', tier:1, country:'JP' },
  3320:  { name:'DTAG',            short:'DT',     lat:52.52,  lon:13.40,  color:'#e20074', tier:1, country:'DE' },
  6939:  { name:'HE.net',          short:'HE',     lat:37.50,  lon:-121.9, color:'#00aaff', tier:1, country:'US' },
  262287:{ name:'BR-Cloud',        short:'BR-CL',  lat:-23.55, lon:-46.63, color:'#ffcc00', tier:2, country:'BR' },
  43016: { name:'AS43016-EU',      short:'EU-43',  lat:52.22,  lon:21.01,  color:'#ffaa00', tier:2, country:'PL' },
  32098: { name:'AS32098',         short:'32098',  lat:37.77,  lon:-122.4, color:'#ff8800', tier:2, country:'US' },
  9294:  { name:'AS9294',          short:'9294',   lat:52.37,  lon:4.90,   color:'#ff6600', tier:2, country:'NL' },
  34701: { name:'AS34701',         short:'34701',  lat:48.86,  lon:2.35,   color:'#ff8800', tier:2, country:'FR' },
}

export const getASMeta = (asn: number): ASMeta | null => AS_META[asn] ?? null

export const severityColor = (s: number) =>
  s >= 5 ? '#ff0000' : s >= 4 ? '#ff3b3b' : s >= 3 ? '#ffaa00' : s >= 2 ? '#ffdd00' : '#00ff88'

export const severityLabel = (s: number) =>
  s >= 5 ? 'CRITICAL' : s >= 4 ? 'HIGH' : s >= 3 ? 'MEDIUM' : s >= 2 ? 'LOW' : 'INFO'

export const eventColor = (t: string) =>
  ({ bgp_hijack:'#ffdd00', route_leak:'#ff6b00', bgp_flap:'#ff3b3b',
     withdrawal_surge:'#ff8800', outage:'#cc0000' }[t] ?? '#4488aa')

export const eventIcon = (t: string) =>
  ({ bgp_hijack:'⚡', route_leak:'⬆', bgp_flap:'↻',
     withdrawal_surge:'↓', outage:'✕' }[t] ?? '●')
