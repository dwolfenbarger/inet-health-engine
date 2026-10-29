"""Bounded equality probes for IP LPM; no index, storage or database tuning."""
from ipaddress import ip_address,ip_network
from api.catalogue_backend import ScopedPool


def covering_networks(address):
    ip=ip_address(address)
    return [str(ip_network((ip,bits),strict=False)) for bits in range(ip.max_prefixlen,-1,-1)]


def snapshot_lpm_query(pool,g,address,collector='all',after=None,limit=51):
    observers=[o['id'] for o in g['observers'] if collector=='all' or o.get('collector')==collector]
    if len(observers)>32:raise ValueError('Viewpoint budget exceeded')
    args=[observers,covering_networks(address)];branches=[]
    if isinstance(pool,ScopedPool):
        for history in [False,True]:
            projection,source,version=pool.relation('routes',history)
            branches.append(f'SELECT {projection} FROM {source} WHERE {version} AND r.observer=k.observer AND r.prefix>=k.prefix AND r.prefix<=k.prefix')
    else:
        branches.append(f'SELECT r.*,r.prefix::text prefix_text FROM inet_catalog.routes r WHERE r.generation={int(g["id"])} AND r.observer=k.observer AND r.prefix>=k.prefix AND r.prefix<=k.prefix')
    sql='''WITH keys AS MATERIALIZED (
      SELECT o.observer,p.prefix FROM unnest($1::text[]) o(observer) CROSS JOIN unnest($2::cidr[]) p(prefix))
      SELECT * FROM (SELECT DISTINCT ON (found.observer) found.* FROM keys k CROSS JOIN LATERAL ('''
    sql+=' UNION ALL '.join(branches)+''' OFFSET 0) found ORDER BY found.observer,masklen(found.prefix) DESC,found.prefix) chosen'''
    if after:
        args.extend(after);sql+=' WHERE (observer,prefix)>($3::text,$4::text::cidr)'
    args.append(limit);sql+=f' ORDER BY observer,prefix LIMIT ${len(args)}'
    return sql,args


def live_lpm_query(address):
    return '''WITH keys AS MATERIALIZED (
      SELECT o.id observer,o.epoch,o.generation,p.prefix FROM routing_observers o
      CROSS JOIN unnest($1::cidr[]) p(prefix))
      SELECT lpm.observer,lpm.origin_asn,lpm.prefix::text prefix,lpm.event_at,
       o.baseline_state,o.error_code,o.mode,o.peer_state,o.stream_state,o.received_at,o.event_at observer_event_at,
       EXISTS(SELECT 1 FROM routing_gaps gap WHERE gap.observer=o.id AND gap.state NOT IN ('repaired','snapshot_superseded')) pending
      FROM (SELECT DISTINCT ON (found.observer) found.* FROM keys k CROSS JOIN LATERAL (
       SELECT r.observer,r.origin_asn,r.prefix,r.event_at FROM routing_routes r
       WHERE r.observer=k.observer AND r.prefix>=k.prefix AND r.prefix<=k.prefix
        AND r.present AND r.epoch=k.epoch AND r.generation=k.generation OFFSET 0) found
       ORDER BY found.observer,masklen(found.prefix) DESC,found.prefix) lpm
      JOIN routing_observers o ON o.id=lpm.observer ORDER BY lpm.observer LIMIT 17''',[covering_networks(address)]
