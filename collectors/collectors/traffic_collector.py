"""Provider outage evidence; Radar awaits a validated credentialed adapter."""
from collectors.config import settings
from collectors.public_sources import run_ioda_cycle
from collectors.source_evidence import save_cycle

async def run_collection_cycle():
    result = await run_ioda_cycle()
    if not settings.cloudflare_radar_token:
        await save_cycle('radar', [], {'scope':'Cloudflare Radar requires a configured API token'}, state='unconfigured')
    else:
        await save_cycle('radar', [], {'scope':'Radar token configured; adapter validation is pending'}, state='unavailable')
    return result
