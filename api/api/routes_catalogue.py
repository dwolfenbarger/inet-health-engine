"""Bounded navigation of immutable, full-scope observed routing generations."""
import asyncio,base64,hashlib,json,re
from datetime import datetime,timezone
from ipaddress import ip_address,ip_network
from typing import Literal
import httpx
from fastapi import APIRouter,HTTPException,Query
from api.deps import get_pg_pool,get_redis
from api.routes_routing import read_observers
from api.local_validation import validate
router=APIRouter(prefix='/catalogue',tags=['observed-asn-inventory'])
Role=Literal['any','origin','transit','peer']
_metadata_slots=asyncio.Semaphore(3)
def decoded(value):return json.loads(value) if isinstance(value,str) else value
async def generation(pool,requested=None):
 row=await pool.fetchrow('SELECT * FROM inet_catalog.generations WHERE published_at IS NOT NULL AND ($1::bigint IS NULL OR id=$1) ORDER BY id DESC LIMIT 1',requested)
 if not row:raise HTTPException(409 if requested else 503,'Browsing snapshot expired; refresh to continue.' if requested else 'ASN inventory is building; live route evidence remains available.')
 result=dict(row)
 for k in ['observers','summary']:result[k]=decoded(result[k])
 result['age_seconds']=max(0,int((datetime.now(timezone.utc)-row['captured_at']).total_seconds()))
 result['stale']=result['age_seconds']>3600
 return result

async def ensure_retained(pool,gen):
 if not await pool.fetchval('SELECT EXISTS(SELECT 1 FROM inet_catalog.generations WHERE id=$1 AND published_at IS NOT NULL)',gen):
  raise HTTPException(409,'Browsing snapshot expired; refresh to continue.')

async def matching_count(pool,g,base,args,asn,role,family,collector,prefix,ip,scope):
 if not any([asn,prefix,ip]) and family=='all' and collector=='all':return g['summary']['routes']
 if asn and not prefix and not ip and family=='all' and collector=='all':
  column={'any':'routes','origin':'origins','transit':'transits','peer':'peers'}[role]
  return await pool.fetchval('SELECT '+column+' FROM inet_catalog.asns WHERE generation=$1 AND asn=$2',g['id'],asn) or 0
 cache=None
 try:
  cache=await get_redis();key=f"inet:catalogue-count:{g['id']}:{scope}";saved=await cache.get(key)
  if saved is not None:return int(saved)
 except Exception:cache=None
 total=await pool.fetchval('SELECT count(*) FROM ('+base+') matched',*args)
 if cache:
  try:await cache.setex(key,7200,str(total))
  except Exception:pass
 return total

def query_plan(gen,asn,role,family,collector,prefix,ip):
 values=[gen];conditions=['r.generation=$1']
 def add(sql,value):values.append(value);conditions.append(sql.replace('?',f'${len(values)}'))
 if asn:
  add({'any':'r.members @> ARRAY[?::bigint]','origin':'r.origin_asn=?','transit':'r.transit @> ARRAY[?::bigint]','peer':'r.peer_asn=?'}[role],asn)
 if family!='all':add('r.family=?',int(family))
 if collector!='all':add('r.collector=?',collector)
 if prefix:add('r.prefix=?::text::cidr',str(ip_network(prefix,strict=False)))
 if ip:add('r.prefix>>=?::text::inet',str(ip_address(ip)))
 return conditions,values

def fingerprint(parts):return hashlib.sha256(json.dumps(parts,sort_keys=True).encode()).hexdigest()[:24]
def encode_cursor(gen,observer,prefix,scope):
 return base64.urlsafe_b64encode(json.dumps([gen,observer,prefix,scope]).encode()).decode().rstrip('=')
def decode_cursor(token,scope):
 try:
  value=json.loads(base64.urlsafe_b64decode(token+'='*(-len(token)%4)))
  if not isinstance(value,list) or len(value)!=4 or type(value[0]) is not int or value[0]<1 or value[3]!=scope:raise ValueError()
  if not isinstance(value[1],str) or not 1<=len(value[1])<=100:raise ValueError()
  value[2]=str(ip_network(value[2],strict=True));return value
 except Exception:raise HTTPException(422,'Invalid page cursor or changed filters; start from the first page.') from None

@router.get('/asns')
async def asns(q:str=Query('',max_length=32),role:Role='any',after:int=Query(0,ge=0,le=4294967295),
               generation_id:int|None=Query(None,ge=1),limit:int=Query(50,ge=1,le=100)):
 pool=await get_pg_pool();g=await generation(pool,generation_id)
 q=q.strip().upper().removeprefix('AS');number=None
 if q:
  if not re.fullmatch('[0-9]{1,10}',q) or not 1<=int(q)<=4294967295:raise HTTPException(422,'Search by ASN number, for example AS13335.')
  number=int(q)
 role_column={'any':'routes','origin':'origins','transit':'transits','peer':'peers'}[role]
 where=f'generation=$1 AND {role_column}>0 AND ($2::bigint IS NULL OR asn=$2)'
 total=await pool.fetchval('SELECT count(*) FROM inet_catalog.asns WHERE '+where,g['id'],number)
 rows=await pool.fetch('SELECT * FROM inet_catalog.asns WHERE '+where+' AND asn>$3 ORDER BY asn LIMIT $4',g['id'],number,after,limit+1)
 more=len(rows)>limit;rows=rows[:limit]
 await ensure_retained(pool,g['id'])
 return {'generation':g,'total':total,'asns':[dict(r) for r in rows],'next_after':rows[-1]['asn'] if more else None,
  'scope':'ASNs observed as origins, ordered-path transit or collector peers in selected maintained views. Not an allocation registry or global reachability score.'}

@router.get('/routes')
async def routes(asn:int|None=Query(None,ge=1,le=4294967295),role:Role='any',family:Literal['all','4','6']='all',
 collector:str=Query('all',max_length=64),prefix:str|None=Query(None,max_length=49),ip:str|None=Query(None,max_length=45),
 cursor:str|None=Query(None,max_length=800),generation_id:int|None=Query(None,ge=1),limit:int=Query(100,ge=1,le=200)):
 if ip and prefix:raise HTTPException(422,'Choose a prefix or an IP address.')
 scope=fingerprint([asn,role,family,collector,prefix,ip]);page=decode_cursor(cursor,scope) if cursor else None
 if page and generation_id and generation_id!=page[0]:raise HTTPException(422,'Page generation differs.')
 pool=await get_pg_pool();g=await generation(pool,page[0] if page else generation_id)
 try:clauses,args=query_plan(g['id'],asn,role,family,collector,prefix,ip)
 except ValueError:raise HTTPException(422,'Enter a valid CIDR prefix or IP address.') from None
 where=' AND '.join(clauses)
 # IP lookup selects the longest present match per viewpoint in the captured generation.
 base='SELECT r.* FROM inet_catalog.routes r WHERE '+where
 if ip:base='SELECT DISTINCT ON (r.observer) r.* FROM inet_catalog.routes r WHERE '+where+' ORDER BY r.observer,masklen(r.prefix) DESC,r.prefix'
 unfiltered=not any([asn,prefix,ip]) and family=='all' and collector=='all'
 total=await matching_count(pool,g,base,args,asn,role,family,collector,prefix,ip,scope)
 summary={'updates':total,'origins':g['summary']['origins'] if unfiltered else None,
  'prefixes':g['summary']['prefixes'] if unfiltered else None,'observers':g['summary']['observers'] if unfiltered else None}
 query='SELECT matched.*,prefix::text prefix_text FROM ('+base+') matched'
 if page:
  args.extend(page[1:3]);query+=f' WHERE (observer,prefix)>(${len(args)-1}::text,${len(args)}::text::cidr)'
 args.append(limit+1);query+=f' ORDER BY observer,prefix LIMIT ${len(args)}'
 rows=await pool.fetch(query,*args);more=len(rows)>limit;rows=rows[:limit]
 pairs=list(dict.fromkeys((r['prefix_text'],r['origin_asn']) for r in rows if r['origin_asn']))
 proof_rows=await pool.fetch("SELECT evidence_id,payload FROM source_evidence WHERE source='rpki-local' AND evidence_id=ANY($1::text[]) AND observed_at>now()-interval '60 minutes'",[f'{p}|{a}' for p,a in pairs])
 proofs={r['evidence_id']:decoded(r['payload']) for r in proof_rows}
 if asn or prefix or ip:
  missing=[(p,a) for p,a in pairs if f'{p}|{a}' not in proofs][:8]
  async with httpx.AsyncClient(base_url='http://127.0.0.1:8323',timeout=3,limits=httpx.Limits(max_connections=4)) as client:
   answers=await asyncio.gather(*(validate(client,p,a) for p,a in missing))
  proofs.update({f'{p}|{a}':v for (p,a),v in zip(missing,answers) if v})
 evidence=[]
 for r in rows:
  path=decoded(r['path']);proof=proofs.get(f"{r['prefix_text']}|{r['origin_asn']}")
  evidence.append({'observer':r['observer'],'collector':r['collector'],'prefix':r['prefix_text'],'origin_asn':r['origin_asn'],
   'peer_asn':r['peer_asn'],'time':r['event_at'],'as_path':path if all(type(x) is int for x in path) else [],'path_segments':path,
   'attrs':decoded(r['attrs']),'evidence_id':r['evidence_id'],'change_type':{0:'snapshot',1:'archive update',2:'announcement'}[r['rank']],
   'rpki_status':proof['status'] if proof else 'unknown','rpki_validation':proof,'roles':{'origin':r['origin_asn']==asn,'transit':asn in r['transit'] if asn else False,'peer':r['peer_asn']==asn}})
 obs=await read_observers(pool)
 await ensure_retained(pool,g['id'])
 return {'as_of':g['captured_at'],'generation':g,'total':total,'summary':summary,'routes':evidence,'observers':obs,
  'next_cursor':encode_cursor(g['id'],rows[-1]['observer'],rows[-1]['prefix_text'],scope) if more else None,
  'scope':{'asn':asn,'role':role,'family':family,'collector':collector,'prefix':prefix,'ip':ip},
  'limitations':['Browsing is pinned to the displayed capture time; live updates continue independently.',
   'Current observer continuity is shown separately from capture-time observer state.',
   'AS_SET values remain raw evidence; ambiguous set membership is not classified as ordered transit.',
   'Unmeasured performance and missing validation are unknown, not healthy.']}

@router.get('/asns/{asn}')
async def detail(asn:int,generation_id:int|None=Query(None,ge=1)):
 if not 1<=asn<=4294967295:raise HTTPException(422,'Invalid ASN')
 pool=await get_pg_pool();g=await generation(pool,generation_id)
 row=await pool.fetchrow('SELECT * FROM inet_catalog.asns WHERE generation=$1 AND asn=$2',g['id'],asn)
 metadata={'state':'unavailable','holder':None,'source':'RIPEstat AS Overview'}
 try:
  cache=await get_redis();key=f'inet:asn-identity:{asn}';saved=await cache.get(key)
  if saved:metadata=json.loads(saved)
  else:
   async with _metadata_slots,httpx.AsyncClient(timeout=4) as client:
    response=await client.get('https://stat.ripe.net/data/as-overview/data.json',params={'resource':f'AS{asn}'})
    response.raise_for_status();body=response.json();data=body.get('data',{})
    if body.get('status')!='ok' or str(data.get('resource','')).upper().removeprefix('AS')!=str(asn):raise ValueError('Identity mismatch')
    metadata={'state':'available','holder':data.get('holder'),'source':'RIPEstat AS Overview','source_url':str(response.url),
     'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'Registry holder identity; not geographic footprint or measured network health.'}
    await cache.setex(key,86400,json.dumps(metadata))
 except Exception:pass
 # Recent candidates retain source evidence and do not imply confirmed incident/recovery.
 signals=await pool.fetch("SELECT time,event_type,affected_prefix,origin_asn,severity,confidence,source FROM bgp_anomalies WHERE origin_asn=$1 AND time>now()-interval '1 hour' AND source LIKE 'ris/%' ORDER BY time DESC LIMIT 20",asn)
 await ensure_retained(pool,g['id'])
 return {'generation':g,'observed':row is not None,'inventory':dict(row) if row else None,'metadata':metadata,
  'signals':[dict(r) for r in signals],'signals_scope':'Latest 20 sampled origin candidates in one hour; absence is not an all-clear.',
  'performance_scope':'Measured paths match known endpoints only. Transit appearance does not establish measured ASN performance.'}
