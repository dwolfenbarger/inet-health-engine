"""
api/ws.py
WebSocket live event feed.
Subscribes to Redis streams and pushes events to connected clients.
"""

import asyncio
import json
from fastapi import WebSocket, WebSocketDisconnect
from api.deps import get_redis
import structlog

log = structlog.get_logger("ws")

# Active WebSocket connections
_connections: set[WebSocket] = set()

# Redis stream keys to subscribe
STREAMS = {
    "raw.anomalies": "0",
    "raw.community": "0",
    "raw.traffic":   "0",
}


async def broadcast(message: dict):
    global _connections
    """Send message to all connected WebSocket clients."""
    if not _connections:
        return
    payload = json.dumps(message)
    dead    = set()
    for ws in _connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _connections -= dead


async def redis_stream_listener():
    """
    Background task: reads Redis streams and broadcasts to WS clients.
    Uses XREAD with blocking to get new events as they arrive.
    Restarts automatically on connection errors.
    """
    stream_positions = {k: "$" for k in STREAMS}  # $ = only new messages

    while True:
        try:
            r = await get_redis()
            log.info("ws_stream_listener_start", streams=list(STREAMS.keys()))

            while True:
                streams_arg = list(stream_positions.items())
                results = await r.xread(
                    streams={k: v for k, v in streams_arg},
                    count=10,
                    block=1000,   # 1 second block
                )

                for stream_name, messages in (results or []):
                    for msg_id, fields in messages:
                        stream_positions[stream_name] = msg_id
                        await broadcast({
                            "stream":    stream_name,
                            "msg_id":    msg_id,
                            "data":      fields,
                        })

        except Exception as e:
            log.warning("ws_stream_listener_error", error=str(e))
            await asyncio.sleep(5)


async def websocket_endpoint(websocket: WebSocket):
    """Handle individual WebSocket connection lifecycle."""
    await websocket.accept()
    _connections.add(websocket)
    log.info("ws_client_connected", total=len(_connections))

    # Send immediate snapshot on connect
    try:
        pool_mod = __import__("api.deps", fromlist=["get_pg_pool"])
        pool     = await pool_mod.get_pg_pool()
        recent   = await pool.fetch("""
            SELECT event_id::text, event_type, severity, confidence,
                   affected_prefix, origin_asn, time
            FROM bgp_anomalies
            WHERE time > NOW() - INTERVAL '1 hour'
            ORDER BY severity DESC LIMIT 10
        """)
        await websocket.send_text(json.dumps({
            "type":    "snapshot",
            "source":  "bgp_anomalies",
            "payload": [dict(r) for r in recent],
        }, default=str))
    except Exception as e:
        log.warning("ws_snapshot_error", error=str(e))

    try:
        while True:
            # Keep connection alive — client can send pings
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        _connections.discard(websocket)
        log.info("ws_client_disconnected", total=len(_connections))
    except Exception as e:
        _connections.discard(websocket)
        log.warning("ws_error", error=str(e))
