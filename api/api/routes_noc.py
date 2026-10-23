"""Bounded, read-only RIS observations for the global NOC interface.

These are update observations, not a complete RIB or a performance measurement.
All SQL arguments are bound. No synthetic collector is eligible.
"""
from datetime import datetime, timezone
from ipaddress import ip_network
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from api.deps import get_pg_pool

router = APIRouter(prefix="/noc", tags=["noc"])
COLLECTORS = {
    "rrc00": {"city": "Amsterdam", "lat": 52.37, "lon": 4.90},
    "rrc11": {"city": "New York", "lat": 40.71, "lon": -74.01},
    "rrc14": {"city": "Palo Alto", "lat": 37.44, "lon": -122.14},
    "rrc16": {"city": "Miami", "lat": 25.76, "lon": -80.19},
    "rrc21": {"city": "Paris", "lat": 48.86, "lon": 2.35},
    "rrc23": {"city":"Singapore","lat":1.35,"lon":103.82},
    "route-views.kixp": {"city":"Nairobi","lat":-1.29,"lon":36.82},
}
CAP = 20000


def freshness(last_seen, now):
    if last_seen is None:
        return {"state": "unavailable", "age_seconds": None}
    age = max(0, (now - last_seen).total_seconds())
    return {"state": "fresh" if age <= 240 else "stale", "age_seconds": round(age)}


def canonical_prefix(value):
    if not value:
        return None
    try:
        return str(ip_network(value, strict=False))
    except ValueError:
        raise HTTPException(422, "Enter an IPv4 or IPv6 CIDR prefix")


@router.get("/snapshot")
async def snapshot(
    minutes: int = Query(15, ge=1, le=60),
    family: Literal["all", "4", "6"] = "all",
    collector: str = Query("all", max_length=64),
    asn: int | None = Query(None, ge=1, le=4294967295),
    prefix: str | None = Query(None, max_length=49),
):
    prefix = canonical_prefix(prefix)
    if collector != "all" and collector not in COLLECTORS:
        raise HTTPException(422, "Unknown configured collector")
    now = datetime.now(timezone.utc)
    try:
        pool = await get_pg_pool()
        # BIGINT stores every unsigned four-byte ASN, including observed reserved values.
        stored_asn = asn
        rows = await pool.fetch("""
            SELECT time, prefix, origin_asn, as_path, communities,
                   change_type, collector, peer_asn, host(next_hop) AS next_hop, next_hop_raw, rpki_status
            FROM bgp_updates
            WHERE time > NOW() - make_interval(mins => $1)
              AND collector = ANY($2::text[])
              AND ($3::bigint IS NULL OR origin_asn = $3)
              AND ($4::text IS NULL OR prefix = $4)
              AND ($5 = 'all' OR ($5 = '6') = (position(':' in prefix) > 0))
              AND ($6 = 'all' OR collector = $6)
            ORDER BY time DESC LIMIT 20001
        """, minutes, list(COLLECTORS), stored_asn, prefix, family, collector)
        capped = len(rows) > CAP
        rows = rows[:CAP]
        # Global source state is independent of the selected resource. An
        # unobserved prefix must not make a working source look unavailable.
        latest = await pool.fetchval("""
            SELECT time FROM bgp_updates
            WHERE time > NOW() - INTERVAL '24 hours'
              AND collector = ANY($1::text[])
            ORDER BY time DESC LIMIT 1
        """, list(COLLECTORS))
        signals = await pool.fetch("""
            SELECT time, event_id::text, event_type, affected_prefix, origin_asn,
                   expected_asn, severity, confidence, source
            FROM bgp_anomalies
            WHERE time > NOW() - make_interval(mins => $1)
              AND source LIKE 'ris/%'
              AND ($2::bigint IS NULL OR origin_asn = $2)
              AND ($3::text IS NULL OR affected_prefix = $3)
              AND ($4 = 'all' OR ($4 = '6') = (position(':' in affected_prefix) > 0))
              AND ($5 = 'all' OR source = $6)
            ORDER BY time DESC LIMIT 100
        """, minutes, stored_asn, prefix, family, collector, f"ris/{collector}")
    except Exception as exc:
        # Do not expose DB connection details or silently return a healthy empty result.
        import structlog
        structlog.get_logger("noc").error("snapshot_unavailable", error_type=type(exc).__name__)
        raise HTTPException(503, "Live observation store unavailable; retry shortly") from None

    seen, routes, collectors = set(), [], {}
    for row in rows:
        r = dict(row)
        c = collectors.setdefault(r["collector"], {"updates": 0, "last_seen": r["time"], "peers": set()})
        c["updates"] += 1
        if r["peer_asn"] is not None:
            c["peers"].add(r["peer_asn"])
        key = (r["collector"], r["peer_asn"], r["prefix"])
        if key not in seen and len(routes) < 200:
            seen.add(key)
            # Old enum serialization remains readable; new writes use values.
            r["change_type"] = (r["change_type"] or "unknown").split(".")[-1].lower()
            r["rpki_status"] = (r["rpki_status"] or "unknown").split(".")[-1].lower()
            routes.append(r)
    origins = {}
    for row in rows:
        a = row["origin_asn"]
        if a:
            origins[a] = origins.get(a, 0) + 1
    # Current validation is distinct from the status cached at ingestion.
    if routes:
        import json
        try:
            evidence_rows=await pool.fetch("""SELECT evidence_id,observed_at,payload FROM source_evidence
                WHERE source='rpki-local' AND evidence_id=ANY($1::text[])
                  AND observed_at>now()-interval '20 minutes'""",
                [f"{r['prefix']}|{r['origin_asn']}" for r in routes if r['origin_asn']])
        except Exception:
            raise HTTPException(503,'Current origin validation evidence unavailable') from None
        validations={r['evidence_id']:r for r in evidence_rows}
        for route in routes:
            route['rpki_at_ingestion']=route['rpki_status']
            record=validations.get(f"{route['prefix']}|{route['origin_asn']}")
            value=record['payload'] if record else None
            if isinstance(value,str):value=json.loads(value)
            route['rpki_validation']=value
            route['rpki_status']=value['status'] if value else 'unknown'
    return {
        "as_of": now, "source": "RIPE RIS Live", "source_state": freshness(latest, now),
        "last_observation": latest, "health_score": None,
        "scope": {"minutes": minutes, "family": family, "collector": collector, "asn": asn, "prefix": prefix,
                  "sample_limit": CAP, "sample_capped": capped, "route_limit": 200},
        "summary": {"updates": len(rows), "origins": len(origins),
                    "prefixes": len({r["prefix"] for r in rows}),
                    "observers": len({(r["collector"], r["peer_asn"]) for r in rows})},
        "collectors": [{"id": name, **geo, "updates": collectors.get(name, {}).get("updates", 0),
                        "last_seen": collectors.get(name, {}).get("last_seen"),
                        "peers": len(collectors.get(name, {}).get("peers", set()))}
                       for name, geo in COLLECTORS.items()],
        "origins": [{"asn": a, "updates": n} for a, n in sorted(origins.items(), key=lambda x: -x[1])[:12]],
        "routes": routes, "signals": [dict(r) for r in signals],
        "limitations": ["Sampled update stream, not a complete routing table or proof of reachability.",
                        "Collector coordinates are approximate city locations, not ASN locations or traffic paths.",
                        "Anomaly candidates are heuristic signals, not confirmed hijacks or outages.",
                        "Congestion, packet loss and latency are not established by this BGP feed; inspect separate measurement evidence.",
                        "Historical AS paths before this release may have lost repeated ASNs.",
                        "Historical candidates without collector attribution are excluded from collector-filtered results.",
                        "Collection is a bounded sample: up to 50,000 updates per 60-second window, scheduled every 90 seconds.",
                        "The next_hop_raw field preserves multi-address IPv6 attributes; next_hop is null when not a single address.",
                        "Complex AS_SET paths cannot be represented by the current schema and have an empty AS sequence."],
        "metadata_source": "https://ris.ripe.net/docs/route-collectors/",
    }


# Source collection status is global metadata, independent of investigation filters.
SOURCE_CATALOG = [
    ('ris','RIPE RIS Live','Routing','RIS','https://ris-live.ripe.net/',240),
    ('ris-continuous','RIS selected continuous observer','Routing / maintained RIB','RIS; overlaps the sampled RIS source','https://ris-live.ripe.net/',180),
    ('rpki-local','Local RPKI validator','Origin authorization','RIR signed ROAs','https://routinator.docs.nlnetlabs.nl/',1200),
    ('atlas','RIPE Atlas','Reachability / latency / DNS','Atlas probes','https://atlas.ripe.net/',1200),
    ('ioda','IODA','Outage candidates','RIS + RouteViews / active probing / telescope','https://ioda.inetintel.cc.gatech.edu/',1200),
    ('routeviews','RouteViews','Routing','RouteViews (some peers overlap RIS)','https://www.routeviews.org/',1200),
    ('radar','Cloudflare Radar','Traffic / provider events','Cloudflare perspective; routing may overlap collectors','https://radar.cloudflare.com/',1200),
    ('mlab','M-Lab / HERMES','Performance baselines','M-Lab test population','https://www.measurementlab.net/',86400),
]


def source_state(row,now,threshold):
    if not row:return 'unavailable'
    if row['state'] in ['error','unconfigured']:return row['state']
    if not row['last_success']:return 'unavailable'
    if (now-row['last_success']).total_seconds()>threshold:return 'stale'
    return row['state']


@router.get('/sources')
async def sources():
    import json
    now=datetime.now(timezone.utc)
    try:
        pool=await get_pg_pool()
        rows=await pool.fetch('SELECT * FROM source_status')
        by_source={r['source']:dict(r) for r in rows}
        latest=await pool.fetchval("SELECT max(time) FROM bgp_updates WHERE time>now()-interval '24 hours' AND collector=ANY($1::text[])",list(COLLECTORS))
        result=[]
        for key,name,role,lineage,url,threshold in SOURCE_CATALOG:
            row=by_source.get(key)
            if key=='ris':
                row={'state':'partial','last_attempt':None,'last_success':latest,
                     'last_observation':latest,'records':None,'error_code':None,
                     'detail':{'scope':'Five configured RIS collectors; bounded intermittent update sample',
                               'limitations':['This sampled source has no complete RIB; selected continuous observers are reported separately.']}}
            if key in ['ris-continuous','routeviews']:
                from api.routes_routing import read_observers
                views=[v for v in await read_observers(pool) if v['source']==key]
                if views:
                    last=max((v['committed_at'] for v in views if v['committed_at']),default=None)
                    latest_event=max((v['event_at'] for v in views if v['event_at']),default=None)
                    row={'state':'partial','last_attempt':last,'last_success':last,'last_observation':latest_event,
                         'records':sum(v['updates'] if key=='ris-continuous' else v['snapshot_v4']+v['snapshot_v6'] for v in views),'error_code':None,
                         'detail':{'scope':'Selected peer IP/session views', 'records_basis':'Committed prefix changes since registration' if key=='ris-continuous' else 'Prefixes in baseline snapshots; current counts can differ',
                                   'observers':[{**v,'snapshot_at':str(v['snapshot_at']),'event_at':str(v['event_at']),
                                     'received_at':str(v['received_at']),'committed_at':str(v['committed_at'])} for v in views],
                                   'routing_endpoint':'/api/v1/noc/routing/routes?source='+key,
                                   'limitations':['A reconstructed observer view is not proof of reachability or global coverage.',
                                                  'RIS views share source lineage; they are not independent confirmations.']}}
            value={k:row.get(k) if row else None for k in ['last_attempt','last_success','last_observation','records','error_code']}
            detail=row.get('detail',{}) if row else {'scope':'No verified collection is available'}
            if isinstance(detail,str):detail=json.loads(detail)
            result.append({'id':key,'name':name,'role':role,'lineage':lineage,'url':url,
                'state':('stale' if key in ['ris-continuous','routeviews'] and row and row['last_observation'] and (now-row['last_observation']).total_seconds()>threshold else source_state(row,now,threshold)),'detail':detail,**value})
        return {'as_of':now,'scope':'Global source collection status; counts are not filtered by the selected ASN or prefix',
                'health_score':None,'sources':result}
    except Exception:
        raise HTTPException(503,'Source evidence store unavailable') from None


@router.get('/evidence/{source}')
async def evidence(source:str,asn:int|None=Query(None,ge=1,le=4294967295),
                   family:Literal['all','4','6']='all',limit:int=Query(25,ge=1,le=100)):
    import json
    if source not in {r[0] for r in SOURCE_CATALOG}:raise HTTPException(404,'Unknown source')
    if source in ['routeviews','ris-continuous']:
        from api.routes_routing import routes
        maintained=await routes(observer=None,source=source,collector='all',prefix=None,ip=None,
                                asn=asn,family=family,include_withdrawn=False,limit=limit)
        return {'source':source,'as_of':maintained['as_of'],'records':[
            {'observed_at':r['time'],'asn':r['origin_asn'],'family':6 if ':' in r['prefix'] else 4,
             'resource':r['prefix'],'payload':r} for r in maintained['routes']],
            'observers':maintained['observers'],'scope':maintained['scope'],'health_score':None}
    try:
        pool=await get_pg_pool()
        rows=await pool.fetch('''SELECT observed_at,asn,family,resource,payload FROM source_evidence
            WHERE source=$1 AND ($2::bigint IS NULL OR asn=$2)
              AND ($3='all' OR family::text=$3) ORDER BY observed_at DESC LIMIT $4
        ''',source,asn,family,limit)
        records=[]
        for row in rows:
            r=dict(row)
            if isinstance(r['payload'],str):r['payload']=json.loads(r['payload'])
            records.append(r)
        return {'source':source,'as_of':datetime.now(timezone.utc),'records':records,
                'scope':{'asn':asn,'family':family,'limit':limit},'health_score':None}
    except Exception:
        raise HTTPException(503,'Source evidence store unavailable') from None

from api.routes_routing import router as routing_router
router.include_router(routing_router)
