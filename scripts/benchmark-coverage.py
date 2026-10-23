"""Small LAN API concurrency smoke check, not a capacity benchmark or SLO."""
import asyncio,statistics,time,json
import httpx
async def main():
 paths=['/coverage','/mesh?family=6','/routing/routes?ip=1.1.1.1','/routing/routes?ip=2606:4700:4700::1111']*8
 sem=asyncio.Semaphore(4);results=[]
 async with httpx.AsyncClient(base_url='http://127.0.0.1/api/v1/noc/',timeout=15) as client:
  async def one(path):
   async with sem:
    start=time.monotonic()
    try:
     response=await client.get(path.lstrip('/'));response.raise_for_status()
     assert len(response.content)<5*1024**2
     assert response.json()['health_score'] is None
     results.append({'path':path,'seconds':time.monotonic()-start,'ok':True})
    except Exception as exc:results.append({'path':path,'seconds':time.monotonic()-start,'ok':False,'error':type(exc).__name__})
  await asyncio.gather(*(one(p) for p in paths))
 times=sorted(r['seconds'] for r in results)
 print(json.dumps({'requests':len(results),'concurrency':4,'failures':[r for r in results if not r['ok']],
                   'median_seconds':round(statistics.median(times),3),'p95_seconds':round(times[int((len(times)-1)*.95)],3),
                   'max_seconds':round(max(times),3),'scope':'Warm limited LAN API smoke check; no public-load or SLO claim'}),flush=True)
 assert all(r['ok'] for r in results)
asyncio.run(main())
