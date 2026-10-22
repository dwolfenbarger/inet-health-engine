from fastapi import APIRouter
from api.deps import get_pg_pool
router = APIRouter(tags=["assessment"])

@router.get("/incidents")
async def list_incidents(limit: int = 50):
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT time, event_type, affected_prefix, origin_asn, expected_asn, severity FROM bgp_anomalies ORDER BY time DESC LIMIT %s" % int(limit))
    return [{"time": str(r["time"]), "type": r["event_type"], "prefix": r["affected_prefix"], "origin": r["origin_asn"], "expected": r["expected_asn"], "severity": r["severity"]} for r in rows]

@router.get("/asn/{asn}/peers")
async def asn_peers(asn: int, limit: int = 50):
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT peer_asn, last_seen, observation_count FROM asn_peers WHERE asn = %s ORDER BY last_seen DESC LIMIT %s" % (int(asn), int(limit)))
    return [{"peer": r["peer_asn"], "last_seen": str(r["last_seen"]), "count": r["observation_count"]} for r in rows]

@router.get("/watchlist")
async def get_watchlist():
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, item_type, item_value, label FROM watchlist ORDER BY id DESC")
    return [{"id": r["id"], "type": r["item_type"], "value": r["item_value"], "label": r["label"]} for r in rows]

@router.post("/watchlist")
async def add_watchlist(item_type: str, item_value: str, label: str = None):
    if item_type not in ("asn", "prefix"):
        from fastapi import HTTPException
        raise HTTPException(400, "item_type must be asn or prefix")
    safe_type = item_type.replace("'", "")
    safe_val = item_value.replace("'", "")
    label_sql = "'" + label.replace("'", "") + "'" if label else "NULL"
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO watchlist (item_type, item_value, label) VALUES ('%s', '%s', %s) ON CONFLICT (item_type, item_value) DO UPDATE SET label = EXCLUDED.label RETURNING id, item_type, item_value, label" % (safe_type, safe_val, label_sql)
        )
    return {"id": row["id"], "type": row["item_type"], "value": row["item_value"], "label": row["label"]}
