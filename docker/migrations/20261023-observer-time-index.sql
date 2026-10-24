-- Existing deployments: run outside a transaction; keep stream writers online.
CREATE INDEX CONCURRENTLY IF NOT EXISTS routing_routes_observer_time ON routing_routes(observer,event_at);
