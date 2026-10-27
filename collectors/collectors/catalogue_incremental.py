"""Bounded shadow catalogue maintenance; publication remains separately gated.

Source mutations and dirty keys commit atomically through database triggers.
The consumer takes the observer locks before queue locks, matching ingestion.
Reset barriers deliberately refuse publication until a controlled reseed: a
partial observer reset must never masquerade as a complete current view.
"""

DDL='''CREATE SCHEMA IF NOT EXISTS inet_incremental;
CREATE TABLE IF NOT EXISTS inet_incremental.current_routes (
 observer text NOT NULL,prefix cidr NOT NULL,origin_asn bigint,peer_asn bigint NOT NULL,
 collector text NOT NULL,transit bigint[] NOT NULL,members bigint[] NOT NULL,
 family smallint NOT NULL,event_at timestamptz NOT NULL,path jsonb NOT NULL,
 attrs jsonb NOT NULL,evidence_id text NOT NULL,rank smallint NOT NULL,
 PRIMARY KEY(observer,prefix));
CREATE INDEX IF NOT EXISTS incremental_route_origin ON inet_incremental.current_routes(origin_asn,observer,prefix);
CREATE INDEX IF NOT EXISTS incremental_route_peer ON inet_incremental.current_routes(peer_asn,observer,prefix);
CREATE INDEX IF NOT EXISTS incremental_route_prefix ON inet_incremental.current_routes USING gist(prefix inet_ops);
CREATE TABLE IF NOT EXISTS inet_incremental.current_memberships (
 asn bigint NOT NULL,observer text NOT NULL,prefix cidr NOT NULL,roles smallint NOT NULL,
 PRIMARY KEY(asn,observer,prefix));
CREATE INDEX IF NOT EXISTS incremental_membership_resource
 ON inet_incremental.current_memberships(observer,prefix);
CREATE TABLE IF NOT EXISTS inet_incremental.prefix_refs(asn bigint,prefix cidr,refs bigint NOT NULL CHECK(refs>=0),PRIMARY KEY(asn,prefix));
CREATE TABLE IF NOT EXISTS inet_incremental.observer_refs(asn bigint,observer text,refs bigint NOT NULL CHECK(refs>=0),PRIMARY KEY(asn,observer));
CREATE TABLE IF NOT EXISTS inet_incremental.asns(asn bigint PRIMARY KEY,routes bigint NOT NULL,prefixes bigint NOT NULL,origins bigint NOT NULL,transits bigint NOT NULL,peers bigint NOT NULL,ipv4 bigint NOT NULL,ipv6 bigint NOT NULL,observers bigint NOT NULL);
'''
# Same membership semantics as the existing full builder: AS_SET remains raw.
CURRENT='''SELECT r.observer,r.prefix,r.origin_asn,o.peer_asn,o.collector,
 ARRAY(SELECT DISTINCT a FROM unnest(p.asns) a WHERE a IS DISTINCT FROM r.origin_asn) transit,
 ARRAY(SELECT DISTINCT a FROM unnest(p.asns||ARRAY[r.origin_asn,o.peer_asn]) a WHERE a IS NOT NULL) members,
 family(r.prefix)::smallint family,r.event_at,r.path,r.attrs,r.evidence_id,r.rank
 FROM routing_routes r JOIN routing_observers o ON o.id=r.observer
 CROSS JOIN LATERAL (SELECT ARRAY(SELECT DISTINCT (v#>>'{}')::bigint FROM jsonb_array_elements(r.path) v
 WHERE jsonb_typeof(v)='number' AND (v#>>'{}') ~ '^[0-9]{1,10}$' AND
 CASE WHEN (v#>>'{}') ~ '^[0-9]{1,10}$' THEN (v#>>'{}')::bigint BETWEEN 1 AND 4294967295 ELSE false END) asns OFFSET 0) p
 WHERE r.present AND r.epoch=o.epoch AND r.generation=o.generation'''
FIELDS='observer,prefix,origin_asn,peer_asn,collector,transit,members,family,event_at,path,attrs,evidence_id,rank'

async def install(conn,*,production_shadow=False):
    database=await conn.fetchval('SELECT current_database()')
    if database not in {'inet_catalogue_qa','inet_catalogue_lifecycle_qa'} and not (production_shadow and database=='inethealth'):
        raise RuntimeError('Explicit production shadow installation required')
    await conn.execute(DDL)
    await install_versions(conn)
    await conn.execute(RUNTIME_DDL)
    await conn.execute(GLOBAL_DDL)
    await conn.execute('''CREATE INDEX IF NOT EXISTS incremental_history_origin ON inet_incremental.current_routes_history(origin_asn,observer,prefix,_from);
    CREATE INDEX IF NOT EXISTS incremental_history_peer ON inet_incremental.current_routes_history(peer_asn,observer,prefix,_from);
    CREATE INDEX IF NOT EXISTS incremental_history_prefix ON inet_incremental.current_routes_history USING gist(prefix inet_ops);
    CREATE INDEX IF NOT EXISTS facets_expiry ON inet_incremental.facets_history(_until)''')
    await conn.execute('CREATE INDEX IF NOT EXISTS incremental_dirty_age ON inet_incremental.dirty(queued_at)')
    await conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA inet_incremental TO inetuser')
    await conn.execute('DROP TRIGGER IF EXISTS incremental_version ON inet_incremental.facets; CREATE TRIGGER incremental_version BEFORE INSERT OR UPDATE OR DELETE ON inet_incremental.facets FOR EACH ROW EXECUTE FUNCTION inet_incremental.version_change()')

async def seed(conn):
    """DEV qualification seed, same locked snapshot as the dirty barrier.

    Caller must bound fixture/clone size and transaction duration. No production
    seed command or automatic reset clearing is exposed by this prototype.
    """
    assert await conn.fetchval('SELECT current_database()')=='inet_catalogue_qa','Qualification database only'
    async with conn.transaction():
        await conn.fetch('SELECT id FROM routing_observers ORDER BY id FOR UPDATE')
        await conn.execute('TRUNCATE inet_incremental.current_routes_history,inet_incremental.current_memberships_history,inet_incremental.asns_history,inet_incremental.publications')
        await begin_version(conn)
        await conn.execute('TRUNCATE inet_incremental.current_memberships,inet_incremental.current_routes')
        await conn.execute('INSERT INTO inet_incremental.current_routes ('+FIELDS+') '+CURRENT)
        await conn.execute('''INSERT INTO inet_incremental.current_memberships(asn,observer,prefix,roles)
        SELECT a,observer,prefix,((CASE WHEN origin_asn=a THEN 1 ELSE 0 END)+
        (CASE WHEN transit @> ARRAY[a] THEN 2 ELSE 0 END)+
        (CASE WHEN peer_asn=a THEN 4 ELSE 0 END))::smallint
        FROM inet_incremental.current_routes CROSS JOIN LATERAL unnest(members) a''')
        await conn.execute('TRUNCATE inet_incremental.prefix_refs,inet_incremental.observer_refs,inet_incremental.asns,inet_incremental.origin_refs,inet_incremental.facets,inet_incremental.facets_history')
        await conn.execute('INSERT INTO inet_incremental.prefix_refs SELECT asn,prefix,count(*) FROM inet_incremental.current_memberships GROUP BY asn,prefix')
        await conn.execute('INSERT INTO inet_incremental.observer_refs SELECT asn,observer,count(*) FROM inet_incremental.current_memberships GROUP BY asn,observer')
        await conn.execute('''INSERT INTO inet_incremental.asns(asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers)
        SELECT asn,count(*),count(DISTINCT prefix),count(*) FILTER(WHERE (roles&1)<>0),count(*) FILTER(WHERE (roles&2)<>0),count(*) FILTER(WHERE (roles&4)<>0),count(*) FILTER(WHERE family(prefix)=4),count(*) FILTER(WHERE family(prefix)=6),count(DISTINCT observer)
        FROM inet_incremental.current_memberships GROUP BY asn''')
        await seed_globals(conn)
        await conn.execute('TRUNCATE inet_incremental.dirty,inet_incremental.resets')
        await conn.execute('UPDATE inet_incremental.runtime SET initialized=true,reset_barrier=false WHERE singleton')

async def maintain(conn,limit=256):
    if not 1<=limit<=4096:raise ValueError('Batch budget')
    async with conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext('inet-incremental-shadow'))"):
            return {'processed':0,'changed':0,'publication_enabled':False,'busy':True}
        observer=await conn.fetchval('SELECT observer FROM inet_incremental.dirty ORDER BY queued_at,observer,prefix LIMIT 1')
        if observer:await conn.fetchrow('SELECT id FROM routing_observers WHERE id=$1 FOR UPDATE',observer)
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.resets)'):
            raise RuntimeError('Observer reset requires controlled reseed; publication blocked')
        await conn.execute('''CREATE TEMP TABLE IF NOT EXISTS inc_keys(observer text,prefix cidr,PRIMARY KEY(observer,prefix)) ON COMMIT DELETE ROWS;
        CREATE TEMP TABLE IF NOT EXISTS inc_new AS SELECT observer,prefix,origin_asn,peer_asn,collector,transit,members,family,event_at,path,attrs,evidence_id,rank FROM inet_incremental.current_routes WITH NO DATA;
        CREATE TEMP TABLE IF NOT EXISTS inc_diff(asn bigint,observer text,prefix cidr,oldroles smallint,newroles smallint);
        CREATE TEMP TABLE IF NOT EXISTS inc_refdelta(asn bigint,prefix cidr,observer text,delta bigint);
        CREATE TEMP TABLE IF NOT EXISTS inc_distinct(asn bigint,prefixes bigint,observers bigint);
        CREATE TEMP TABLE IF NOT EXISTS inc_globaldiff(observer text,prefix cidr,origin_asn bigint,family smallint,collector text,delta integer);
        CREATE TEMP TABLE IF NOT EXISTS inc_origindelta(origin_asn bigint,delta bigint);
        TRUNCATE inc_keys,inc_new,inc_diff,inc_refdelta,inc_distinct,inc_globaldiff,inc_origindelta;''')
        keys=await conn.fetch('SELECT observer,prefix::text prefix FROM inet_incremental.dirty WHERE observer=$1 ORDER BY prefix LIMIT $2 FOR UPDATE',observer,limit)
        if not keys:return {'processed':0,'changed':0,'publication_enabled':False}
        await conn.executemany('INSERT INTO inc_keys VALUES($1,$2::text::cidr)',[(k['observer'],k['prefix']) for k in keys])
        await conn.execute('ANALYZE inc_keys')
        current=CURRENT.replace('FROM routing_routes r JOIN routing_observers o','FROM inc_keys k JOIN routing_routes r ON r.observer=k.observer AND r.prefix=k.prefix JOIN routing_observers o')
        await conn.execute('INSERT INTO inc_new '+current)
        await conn.execute('''INSERT INTO inc_diff
        WITH old AS (SELECT m.* FROM inet_incremental.current_memberships m JOIN inc_keys k USING(observer,prefix)),
        new AS (SELECT a asn,n.observer,n.prefix,((CASE WHEN n.origin_asn=a THEN 1 ELSE 0 END)+(CASE WHEN n.transit @> ARRAY[a] THEN 2 ELSE 0 END)+(CASE WHEN n.peer_asn=a THEN 4 ELSE 0 END))::smallint roles FROM inc_new n CROSS JOIN LATERAL unnest(n.members) a)
        SELECT coalesce(o.asn,n.asn),coalesce(o.observer,n.observer),coalesce(o.prefix,n.prefix),o.roles,n.roles
        FROM old o FULL JOIN new n USING(asn,observer,prefix) WHERE o.roles IS DISTINCT FROM n.roles;
        INSERT INTO inc_globaldiff SELECT r.observer,r.prefix,r.origin_asn,r.family,r.collector,-1 FROM inet_incremental.current_routes r JOIN inc_keys k USING(observer,prefix)
          UNION ALL SELECT observer,prefix,origin_asn,family,collector,1 FROM inc_new;
        INSERT INTO inc_diff SELECT 0,coalesce(o.observer,n.observer),coalesce(o.prefix,n.prefix),CASE WHEN o.observer IS NOT NULL THEN 0::smallint END,CASE WHEN n.observer IS NOT NULL THEN 0::smallint END
          FROM (SELECT r.* FROM inet_incremental.current_routes r JOIN inc_keys k USING(observer,prefix)) o FULL JOIN inc_new n USING(observer,prefix) WHERE o.observer IS NULL OR n.observer IS NULL;
        INSERT INTO inc_origindelta SELECT origin_asn,sum(delta) FROM inc_globaldiff WHERE origin_asn IS NOT NULL GROUP BY origin_asn HAVING sum(delta)<>0;
        INSERT INTO inc_refdelta SELECT asn,prefix,observer,((newroles IS NOT NULL)::int-(oldroles IS NOT NULL)::int) FROM inc_diff WHERE (newroles IS NULL)<>(oldroles IS NULL);
        INSERT INTO inc_distinct
        SELECT d.asn,sum((coalesce(r.refs,0)+d.delta>0)::int-(coalesce(r.refs,0)>0)::int),0 FROM (SELECT asn,prefix,sum(delta) delta FROM inc_refdelta GROUP BY asn,prefix) d LEFT JOIN inet_incremental.prefix_refs r USING(asn,prefix) GROUP BY d.asn;
        INSERT INTO inc_distinct
        SELECT d.asn,0,sum((coalesce(r.refs,0)+d.delta>0)::int-(coalesce(r.refs,0)>0)::int) FROM (SELECT asn,observer,sum(delta) delta FROM inc_refdelta GROUP BY asn,observer) d LEFT JOIN inet_incremental.observer_refs r USING(asn,observer) GROUP BY d.asn;''')
        version=await begin_version(conn)
        origin_change=await conn.fetchval('''SELECT coalesce(sum((coalesce(r.refs,0)+d.delta>0)::int-(coalesce(r.refs,0)>0)::int),0) FROM inc_origindelta d LEFT JOIN inet_incremental.origin_refs r USING(origin_asn)''')
        await conn.execute('''UPDATE inet_incremental.origin_refs r SET refs=r.refs+d.delta FROM inc_origindelta d WHERE r.origin_asn=d.origin_asn;
        INSERT INTO inet_incremental.origin_refs SELECT d.origin_asn,d.delta FROM inc_origindelta d WHERE d.delta>0 AND NOT EXISTS(SELECT 1 FROM inet_incremental.origin_refs r WHERE r.origin_asn=d.origin_asn);
        DELETE FROM inet_incremental.origin_refs r USING inc_origindelta d WHERE r.origin_asn=d.origin_asn AND r.refs=0;''')
        for table,column in [('prefix_refs','prefix'),('observer_refs','observer')]:
            await conn.execute(f'''UPDATE inet_incremental.{table} r SET refs=r.refs+d.delta FROM (SELECT asn,{column},sum(delta) delta FROM inc_refdelta GROUP BY asn,{column}) d WHERE r.asn=d.asn AND r.{column}=d.{column};
            INSERT INTO inet_incremental.{table}(asn,{column},refs) SELECT d.asn,d.{column},d.delta FROM (SELECT asn,{column},sum(delta) delta FROM inc_refdelta GROUP BY asn,{column}) d WHERE d.delta>0 AND NOT EXISTS(SELECT 1 FROM inet_incremental.{table} r WHERE r.asn=d.asn AND r.{column}=d.{column});
            DELETE FROM inet_incremental.{table} r USING (SELECT DISTINCT asn,{column} FROM inc_refdelta) d WHERE r.asn=d.asn AND r.{column}=d.{column} AND r.refs=0;''')
        withdrawn=await conn.fetchval('SELECT count(*) FROM inet_incremental.current_routes r JOIN inc_keys k USING(observer,prefix) WHERE NOT EXISTS(SELECT 1 FROM inc_new n WHERE n.observer=r.observer AND n.prefix=r.prefix)')
        await conn.execute('''INSERT INTO inet_incremental.asns(asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers)
        WITH d AS (SELECT asn,sum((newroles IS NOT NULL)::int-(oldroles IS NOT NULL)::int) routes,
        sum((coalesce(newroles,0)&1)-(coalesce(oldroles,0)&1)) origins,
        sum(((coalesce(newroles,0)&2)<>0)::int-((coalesce(oldroles,0)&2)<>0)::int) transits,
        sum(((coalesce(newroles,0)&4)<>0)::int-((coalesce(oldroles,0)&4)<>0)::int) peers,
        sum(((newroles IS NOT NULL)::int-(oldroles IS NOT NULL)::int)*(family(prefix)=4)::int) ipv4,
        sum(((newroles IS NOT NULL)::int-(oldroles IS NOT NULL)::int)*(family(prefix)=6)::int) ipv6 FROM inc_diff GROUP BY asn),
        r AS (SELECT asn,sum(prefixes) prefixes,sum(observers) observers FROM inc_distinct GROUP BY asn)
        SELECT d.asn,d.routes,coalesce(r.prefixes,0),d.origins,d.transits,d.peers,d.ipv4,d.ipv6,coalesce(r.observers,0) FROM d LEFT JOIN r USING(asn)
        ON CONFLICT(asn) DO UPDATE SET routes=inet_incremental.asns.routes+excluded.routes,prefixes=inet_incremental.asns.prefixes+excluded.prefixes,
        origins=inet_incremental.asns.origins+excluded.origins,transits=inet_incremental.asns.transits+excluded.transits,peers=inet_incremental.asns.peers+excluded.peers,
        ipv4=inet_incremental.asns.ipv4+excluded.ipv4,ipv6=inet_incremental.asns.ipv6+excluded.ipv6,observers=inet_incremental.asns.observers+excluded.observers;
        DELETE FROM inet_incremental.asns a USING (SELECT DISTINCT asn FROM inc_diff) d WHERE a.asn=d.asn AND a.routes=0;
        DELETE FROM inet_incremental.current_memberships m USING inc_diff d WHERE d.asn>0 AND m.asn=d.asn AND m.observer=d.observer AND m.prefix=d.prefix;
        INSERT INTO inet_incremental.current_memberships(asn,observer,prefix,roles) SELECT asn,observer,prefix,newroles FROM inc_diff WHERE asn>0 AND newroles IS NOT NULL;
        DELETE FROM inet_incremental.current_routes r USING inc_keys k WHERE r.observer=k.observer AND r.prefix=k.prefix AND NOT EXISTS(SELECT 1 FROM inc_new n WHERE n.observer=r.observer AND n.prefix=r.prefix);''')
        if origin_change:
            await conn.execute('UPDATE inet_incremental.asns SET origins=origins+$1 WHERE asn=0',origin_change)
        await conn.execute('''INSERT INTO inet_incremental.facets(observer,collector,family,routes)
          SELECT observer,collector,family,sum(delta) FROM inc_globaldiff GROUP BY observer,collector,family HAVING sum(delta)<>0
          ON CONFLICT(observer,family) DO UPDATE SET collector=excluded.collector,routes=inet_incremental.facets.routes+excluded.routes;
          DELETE FROM inet_incremental.facets f USING (SELECT DISTINCT observer,family FROM inc_globaldiff) d WHERE f.observer=d.observer AND f.family=d.family AND f.routes=0;''')
        fields=FIELDS.split(',');sets=','.join(f'{f}=excluded.{f}' for f in fields[2:])
        old_values=','.join('inet_incremental.current_routes.'+f for f in fields[2:]);new_values=','.join('excluded.'+f for f in fields[2:])
        result=await conn.execute('INSERT INTO inet_incremental.current_routes ('+FIELDS+') SELECT '+FIELDS+' FROM inc_new ON CONFLICT(observer,prefix) DO UPDATE SET '+sets+' WHERE ('+old_values+') IS DISTINCT FROM ('+new_values+')')
        await conn.execute('DELETE FROM inet_incremental.dirty d USING inc_keys k WHERE d.observer=k.observer AND d.prefix=k.prefix')
        return {'processed':len(keys),'changed':int(result.split()[-1])+withdrawn,'withdrawn':withdrawn,'publication_enabled':False,'version':version}

VERSION_DDL='''
CREATE TABLE IF NOT EXISTS inet_incremental.publications(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,captured_at timestamptz NOT NULL DEFAULT clock_timestamp());
ALTER TABLE inet_incremental.current_routes ADD COLUMN IF NOT EXISTS _from bigint NOT NULL DEFAULT 0;
ALTER TABLE inet_incremental.current_memberships ADD COLUMN IF NOT EXISTS _from bigint NOT NULL DEFAULT 0;
ALTER TABLE inet_incremental.asns ADD COLUMN IF NOT EXISTS _from bigint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS inet_incremental.current_routes_history(LIKE inet_incremental.current_routes INCLUDING DEFAULTS,_until bigint NOT NULL);
CREATE TABLE IF NOT EXISTS inet_incremental.current_memberships_history(LIKE inet_incremental.current_memberships INCLUDING DEFAULTS,_until bigint NOT NULL);
CREATE TABLE IF NOT EXISTS inet_incremental.asns_history(LIKE inet_incremental.asns INCLUDING DEFAULTS,_until bigint NOT NULL);
CREATE INDEX IF NOT EXISTS incremental_history_resource ON inet_incremental.current_routes_history(observer,prefix,_from);
CREATE INDEX IF NOT EXISTS incremental_history_asn ON inet_incremental.current_memberships_history(asn,observer,prefix,_from);
CREATE INDEX IF NOT EXISTS incremental_history_counts ON inet_incremental.asns_history(asn,_from);
CREATE OR REPLACE FUNCTION inet_incremental.version_change() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE version bigint:=coalesce(nullif(current_setting('inet_incremental.version',true),''),'0')::bigint;
BEGIN
 IF version<=0 THEN RAISE EXCEPTION 'Version writer context required'; END IF;
 IF TG_OP<>'INSERT' AND OLD._from<version THEN
  EXECUTE format('INSERT INTO inet_incremental.%I SELECT ($1).*,$2',TG_TABLE_NAME||'_history') USING OLD,version;
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 NEW._from:=version; RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION inet_incremental.routes_at(version bigint) RETURNS SETOF inet_incremental.current_routes LANGUAGE sql STABLE AS $$
 SELECT * FROM inet_incremental.current_routes WHERE _from<=version
 UNION ALL SELECT observer,prefix,origin_asn,peer_asn,collector,transit,members,family,event_at,path,attrs,evidence_id,rank,_from FROM inet_incremental.current_routes_history WHERE _from<=version AND _until>version
$$;
CREATE OR REPLACE FUNCTION inet_incremental.memberships_at(version bigint) RETURNS SETOF inet_incremental.current_memberships LANGUAGE sql STABLE AS $$
 SELECT * FROM inet_incremental.current_memberships WHERE _from<=version
 UNION ALL SELECT asn,observer,prefix,roles,_from FROM inet_incremental.current_memberships_history WHERE _from<=version AND _until>version
$$;
CREATE OR REPLACE FUNCTION inet_incremental.asns_at(version bigint) RETURNS SETOF inet_incremental.asns LANGUAGE sql STABLE AS $$
 SELECT * FROM inet_incremental.asns WHERE _from<=version
 UNION ALL SELECT asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers,_from FROM inet_incremental.asns_history WHERE _from<=version AND _until>version
$$;
'''
async def install_versions(conn):
    await conn.execute(VERSION_DDL)
    for table in ['current_routes','current_memberships','asns']:
        await conn.execute(f'DROP TRIGGER IF EXISTS incremental_version ON inet_incremental.{table}')
        await conn.execute(f'CREATE TRIGGER incremental_version BEFORE INSERT OR UPDATE OR DELETE ON inet_incremental.{table} FOR EACH ROW EXECUTE FUNCTION inet_incremental.version_change()')
        await conn.execute(f'CREATE INDEX IF NOT EXISTS {table}_expiry ON inet_incremental.{table}_history(_until)')

async def begin_version(conn):
    version=await conn.fetchval('INSERT INTO inet_incremental.publications DEFAULT VALUES RETURNING id')
    await conn.fetchval("SELECT set_config('inet_incremental.version',$1,true)",str(version))
    return version

async def snapshot(conn,version):
    row=await conn.fetchrow('SELECT id,captured_at FROM inet_incremental.publications WHERE id=$1',version)
    if row is None:raise LookupError('Browsing snapshot expired; refresh to continue')
    return dict(row)

async def prune_versions(conn,retain_seconds=3600,limit=1024):
    """Expire publication tokens first, reclaim only history they cannot read.

    Kept current rows always remain available. Every history delete is bounded;
    backlog can be reclaimed over subsequent cycles without a large delete job.
    """
    if not 60<=retain_seconds<=86400 or not 1<=limit<=4096:raise ValueError('Retention budget')
    async with conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext('inet-incremental-shadow'))"):return {'busy':True}
        floor=await conn.fetchval("SELECT least(coalesce(min(id) FILTER(WHERE captured_at>=clock_timestamp()-$1::int*interval '1 second'),max(id)),coalesce(max(id) FILTER(WHERE ready),max(id))) FROM inet_incremental.publications",retain_seconds)
        if floor is None:return {'deleted':0}
        await conn.execute('''WITH old AS (SELECT id FROM inet_incremental.publications WHERE id<$1 ORDER BY id LIMIT $2)
          DELETE FROM inet_incremental.publications p USING old WHERE p.id=old.id''',floor,limit)
        # Bounded metadata deletion may leave older tokens. They still own their
        # history until a later cycle expires them, even if outside the time goal.
        floor=await conn.fetchval('SELECT min(id) FROM inet_incremental.publications')
        removed=0
        for table in ['current_routes_history','current_memberships_history','asns_history','facets_history']:
            result=await conn.execute(f'''WITH old AS (SELECT ctid FROM inet_incremental.{table} WHERE _until<=$1 LIMIT $2 FOR UPDATE SKIP LOCKED)
              DELETE FROM inet_incremental.{table} h USING old WHERE h.ctid=old.ctid''',floor,limit)
            removed+=int(result.split()[-1])
        return {'deleted':removed,'floor':floor}

RUNTIME_DDL='''
CREATE TABLE IF NOT EXISTS inet_incremental.runtime(singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),initialized boolean NOT NULL DEFAULT false,reset_barrier boolean NOT NULL DEFAULT true);
INSERT INTO inet_incremental.runtime(singleton) VALUES(true) ON CONFLICT DO NOTHING;
ALTER TABLE inet_incremental.resets ADD COLUMN IF NOT EXISTS scan_prefix cidr;
ALTER TABLE inet_incremental.resets ADD COLUMN IF NOT EXISTS scan_revision bigint;
ALTER TABLE inet_incremental.publications ADD COLUMN IF NOT EXISTS ready boolean NOT NULL DEFAULT false;
CREATE INDEX IF NOT EXISTS incremental_ready_publication ON inet_incremental.publications(id DESC) WHERE ready;
'''

async def bootstrap(conn,after_routes=None,check_budget=None,bulk=True):
    """Initialize an empty shadow from one MVCC snapshot, without source locks.

    Source triggers must already be installed. Preserve every dirty key and reset
    marker, including mutations made while the snapshot is copied. A failure rolls
    back the derived seed; intake and its durable queue continue independently.
    """
    async with conn.transaction(isolation='repeatable_read'):
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext('inet-incremental-shadow'))"):
            raise RuntimeError('Another catalogue writer is active')
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.current_routes)'):
            raise RuntimeError('Bootstrap requires an empty shadow; preserve existing versions')
        if await conn.fetchval('SELECT initialized FROM inet_incremental.runtime WHERE singleton') or await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.publications WHERE ready)'):
            raise RuntimeError('Bootstrap refuses an initialized or previously published shadow')
        if bulk:
            # Aborted INSERTs can leave allocated pages despite having no visible
            # rows. Reclaim only verified-empty, never-published derived objects.
            empty_tables=['current_routes','current_memberships','prefix_refs','observer_refs','asns','origin_refs','facets','current_routes_history','current_memberships_history','asns_history','facets_history']
            for table in empty_tables:
                if await conn.fetchval(f'SELECT EXISTS(SELECT 1 FROM inet_incremental.{table})'):
                    raise RuntimeError('Bootstrap requires all derived state empty: '+table)
            await conn.execute('TRUNCATE '+','.join('inet_incremental.'+table for table in empty_tables))
        version=await begin_version(conn)
        if bulk:
            # Only never-published, empty derived tables are locked. Source writers
            # and their dirty capture remain independent throughout this copy.
            for table in ['current_routes','current_memberships','prefix_refs','observer_refs']:
                await conn.execute(f'ALTER TABLE inet_incremental.{table} DROP CONSTRAINT {table}_pkey')
            for table in ['current_routes','current_memberships']:
                await conn.execute(f'ALTER TABLE inet_incremental.{table} DISABLE TRIGGER incremental_version')
            for index in ['incremental_route_origin','incremental_route_peer','incremental_route_prefix','incremental_membership_resource']:
                await conn.execute('DROP INDEX inet_incremental.'+index)
        await conn.execute('INSERT INTO inet_incremental.current_routes ('+FIELDS+',_from) SELECT s.*,$1 FROM ('+CURRENT+') s',version)
        if check_budget:await check_budget(conn)
        if after_routes:await after_routes()
        await conn.execute('''INSERT INTO inet_incremental.current_memberships(asn,observer,prefix,roles,_from)
        SELECT a,observer,prefix,((CASE WHEN origin_asn=a THEN 1 ELSE 0 END)+(CASE WHEN transit @> ARRAY[a] THEN 2 ELSE 0 END)+(CASE WHEN peer_asn=a THEN 4 ELSE 0 END))::smallint
        ,$1 FROM inet_incremental.current_routes CROSS JOIN LATERAL unnest(members) a;''',version)
        if check_budget:await check_budget(conn)
        await conn.execute('''INSERT INTO inet_incremental.prefix_refs SELECT asn,prefix,count(*) FROM inet_incremental.current_memberships GROUP BY asn,prefix;
        INSERT INTO inet_incremental.observer_refs SELECT asn,observer,count(*) FROM inet_incremental.current_memberships GROUP BY asn,observer;
        INSERT INTO inet_incremental.asns(asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers)
        SELECT asn,count(*),count(DISTINCT prefix),count(*) FILTER(WHERE (roles&1)<>0),count(*) FILTER(WHERE (roles&2)<>0),count(*) FILTER(WHERE (roles&4)<>0),count(*) FILTER(WHERE family(prefix)=4),count(*) FILTER(WHERE family(prefix)=6),count(DISTINCT observer)
        FROM inet_incremental.current_memberships GROUP BY asn;
        UPDATE inet_incremental.runtime SET initialized=true,reset_barrier=true WHERE singleton;''')
        await seed_globals(conn)
        if check_budget:await check_budget(conn)
        if bulk:
            for table,columns in [('current_routes','observer,prefix'),('current_memberships','asn,observer,prefix'),('prefix_refs','asn,prefix'),('observer_refs','asn,observer')]:
                await conn.execute(f'ALTER TABLE inet_incremental.{table} ADD PRIMARY KEY({columns})')
            await conn.execute(DDL)
            for table in ['current_routes','current_memberships']:
                await conn.execute(f'ALTER TABLE inet_incremental.{table} ENABLE TRIGGER incremental_version')
            if check_budget:await check_budget(conn)
        return {'version':version,'publication_enabled':False,'catchup_required':True}

async def reconcile_reset(conn,limit=256):
    """Expand a reset into bounded dirty work, including vanished catalogue keys.

    Source and catalogue keys are both scanned. A new reset revision restarts the
    cursor; already queued keys coalesce safely. No row-level source lock is held
    across batches and no old publication is modified.
    """
    if not 1<=limit<=4096:raise ValueError('Batch budget')
    async with conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext('inet-incremental-shadow'))"):
            return {'busy':True}
        observer=await conn.fetchval('SELECT observer FROM inet_incremental.resets ORDER BY observer LIMIT 1')
        if observer is None:return {'queued':0,'reset':False}
        await conn.fetchrow('SELECT id FROM routing_observers WHERE id=$1 FOR UPDATE',observer)
        reset=await conn.fetchrow('SELECT * FROM inet_incremental.resets WHERE observer=$1 FOR UPDATE',observer)
        await conn.execute('UPDATE inet_incremental.runtime SET reset_barrier=true WHERE singleton')
        cursor=reset['scan_prefix'] if reset['scan_revision']==reset['revision'] else None
        # LIMIT on each side prevents scanning a whole observer to form the union.
        rows=await conn.fetch('''SELECT prefix FROM (
          (SELECT prefix FROM routing_routes WHERE observer=$1 AND ($2::cidr IS NULL OR prefix>$2) ORDER BY prefix LIMIT $3)
          UNION
          (SELECT prefix FROM inet_incremental.current_routes WHERE observer=$1 AND ($2::cidr IS NULL OR prefix>$2) ORDER BY prefix LIMIT $3)
          ) candidates ORDER BY prefix LIMIT $3''',observer,cursor,limit)
        if rows:
            await conn.executemany('INSERT INTO inet_incremental.dirty(observer,prefix) VALUES($1,$2::cidr) ON CONFLICT DO NOTHING',[(observer,r['prefix']) for r in rows])
            await conn.execute('UPDATE inet_incremental.resets SET scan_prefix=$2::cidr,scan_revision=revision WHERE observer=$1',observer,rows[-1]['prefix'])
        else:
            await conn.execute('DELETE FROM inet_incremental.resets WHERE observer=$1',observer)
        return {'queued':len(rows),'reset':True,'observer':observer,'expansion_complete':not rows}

async def finish_catchup(conn):
    """Publish only after an atomic empty-queue check serialized with intake."""
    async with conn.transaction():
        if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext('inet-incremental-shadow'))"):return False
        await conn.fetch('SELECT id FROM routing_observers ORDER BY id FOR UPDATE')
        if await conn.fetchval('SELECT EXISTS(SELECT 1 FROM inet_incremental.dirty) OR EXISTS(SELECT 1 FROM inet_incremental.resets)'):return False
        initialized=await conn.fetchval('SELECT initialized FROM inet_incremental.runtime WHERE singleton')
        if not initialized:return False
        await conn.execute('UPDATE inet_incremental.runtime SET reset_barrier=false WHERE singleton')
        version=await begin_version(conn)
        await publish_metadata(conn,version)
        return version

GLOBAL_DDL='''
CREATE TABLE IF NOT EXISTS inet_incremental.origin_refs(origin_asn bigint PRIMARY KEY,refs bigint NOT NULL CHECK(refs>=0));
CREATE TABLE IF NOT EXISTS inet_incremental.facets(observer text,collector text,family smallint,routes bigint NOT NULL,_from bigint NOT NULL DEFAULT 0,PRIMARY KEY(observer,family));
CREATE TABLE IF NOT EXISTS inet_incremental.facets_history(LIKE inet_incremental.facets INCLUDING DEFAULTS,_until bigint NOT NULL);
ALTER TABLE inet_incremental.publications ADD COLUMN IF NOT EXISTS summary jsonb NOT NULL DEFAULT '{}';
ALTER TABLE inet_incremental.publications ADD COLUMN IF NOT EXISTS observers jsonb NOT NULL DEFAULT '[]';
CREATE OR REPLACE FUNCTION inet_incremental.facets_at(version bigint) RETURNS SETOF inet_incremental.facets LANGUAGE sql STABLE AS $$
 SELECT * FROM inet_incremental.facets WHERE _from<=version UNION ALL
 SELECT observer,collector,family,routes,_from FROM inet_incremental.facets_history WHERE _from<=version AND _until>version
$$;
'''

async def seed_globals(conn):
    await conn.execute('''INSERT INTO inet_incremental.prefix_refs SELECT 0,prefix,count(*) FROM inet_incremental.current_routes GROUP BY prefix;
    INSERT INTO inet_incremental.observer_refs SELECT 0,observer,count(*) FROM inet_incremental.current_routes GROUP BY observer;
    INSERT INTO inet_incremental.origin_refs SELECT origin_asn,count(*) FROM inet_incremental.current_routes WHERE origin_asn IS NOT NULL GROUP BY origin_asn;
    INSERT INTO inet_incremental.asns(asn,routes,prefixes,origins,transits,peers,ipv4,ipv6,observers)
    SELECT 0,count(*),count(DISTINCT prefix),count(DISTINCT origin_asn),0,0,count(*) FILTER(WHERE family=4),count(*) FILTER(WHERE family=6),count(DISTINCT observer) FROM inet_incremental.current_routes;
    INSERT INTO inet_incremental.facets(observer,collector,family,routes) SELECT observer,collector,family,count(*) FROM inet_incremental.current_routes GROUP BY observer,collector,family;''')

async def publish_metadata(conn,version):
    import json
    global_row=await conn.fetchrow('SELECT routes,prefixes,origins,observers FROM inet_incremental.asns WHERE asn=0')
    summary=dict(global_row) if global_row else {'routes':0,'prefixes':0,'origins':0,'observers':0}
    summary['asns']=await conn.fetchval('SELECT count(*) FROM inet_incremental.asns WHERE asn>0 AND routes>0')
    summary['facets']=[dict(r) for r in await conn.fetch('SELECT observer,collector,family,routes FROM inet_incremental.facets ORDER BY observer,family')]
    observers=await conn.fetch('''SELECT id,collector,peer_asn,host(peer_ip) peer_ip,metadata,event_at,baseline_at,stream_state,baseline_state,error_code,
      (SELECT count(*) FROM routing_gaps g WHERE g.observer=o.id AND g.state NOT IN ('repaired','snapshot_superseded')) pending_gaps FROM routing_observers o ORDER BY id''')
    await conn.execute('UPDATE inet_incremental.publications SET ready=true,captured_at=clock_timestamp(),summary=$2::jsonb,observers=$3::jsonb WHERE id=$1',version,json.dumps(summary),json.dumps([dict(r) for r in observers],default=str))
