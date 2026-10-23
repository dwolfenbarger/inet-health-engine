BEGIN;
CREATE TABLE IF NOT EXISTS routing_observers (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, collector TEXT NOT NULL, peer_ip INET NOT NULL,
 peer_asn BIGINT NOT NULL CHECK(peer_asn BETWEEN 1 AND 4294967295),
 mode TEXT NOT NULL CHECK(mode IN ('ris-live','mrt')), metadata JSONB NOT NULL,
 epoch BIGINT NOT NULL DEFAULT 1, generation UUID NOT NULL,
 stream_state TEXT NOT NULL DEFAULT 'starting', peer_state TEXT NOT NULL DEFAULT 'unknown',
 baseline_state TEXT NOT NULL DEFAULT 'required', baseline_at TIMESTAMPTZ,
 reset_at TIMESTAMPTZ, committed_at TIMESTAMPTZ, event_at TIMESTAMPTZ, received_at TIMESTAMPTZ,
 sequence BIGINT NOT NULL DEFAULT 0, messages BIGINT NOT NULL DEFAULT 0,
 updates BIGINT NOT NULL DEFAULT 0, reconnects BIGINT NOT NULL DEFAULT 0,
 snapshot_v4 BIGINT NOT NULL DEFAULT 0, snapshot_v6 BIGINT NOT NULL DEFAULT 0,
 error_code TEXT, UNIQUE(source,collector,peer_ip)
);
CREATE TABLE IF NOT EXISTS routing_messages (
 observer TEXT NOT NULL REFERENCES routing_observers(id), message_id TEXT NOT NULL,
 received_at TIMESTAMPTZ NOT NULL, event_at TIMESTAMPTZ NOT NULL, sequence BIGINT NOT NULL,
 payload JSONB NOT NULL, PRIMARY KEY(observer,message_id)
);
CREATE INDEX IF NOT EXISTS routing_messages_recent ON routing_messages(observer,received_at DESC);
CREATE TABLE IF NOT EXISTS routing_routes (
 observer TEXT NOT NULL REFERENCES routing_observers(id), prefix CIDR NOT NULL,
 epoch BIGINT NOT NULL, generation UUID NOT NULL, event_at TIMESTAMPTZ NOT NULL,
 rank SMALLINT NOT NULL, event_order TEXT NOT NULL, present BOOLEAN NOT NULL,
 origin_asn BIGINT CHECK(origin_asn BETWEEN 1 AND 4294967295), path JSONB NOT NULL,
 attrs JSONB NOT NULL, evidence_id TEXT NOT NULL,
 PRIMARY KEY(observer,prefix)
);
CREATE INDEX IF NOT EXISTS routing_routes_prefix ON routing_routes USING gist(prefix inet_ops);
CREATE INDEX IF NOT EXISTS routing_routes_origin ON routing_routes(observer,origin_asn) WHERE present;
CREATE TABLE IF NOT EXISTS routing_gaps (
 id UUID PRIMARY KEY, observer TEXT NOT NULL REFERENCES routing_observers(id),
 opened_at TIMESTAMPTZ NOT NULL DEFAULT now(), from_at TIMESTAMPTZ, until_at TIMESTAMPTZ,
 reason TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', repaired_at TIMESTAMPTZ,
 error_code TEXT
);
CREATE INDEX IF NOT EXISTS routing_gaps_pending ON routing_gaps(observer,opened_at) WHERE state<>'repaired';
CREATE UNIQUE INDEX IF NOT EXISTS routing_gaps_one_open ON routing_gaps(observer) WHERE until_at IS NULL;
CREATE TABLE IF NOT EXISTS routing_archives (
 observer TEXT NOT NULL REFERENCES routing_observers(id), url TEXT NOT NULL,
 generation UUID NOT NULL, kind TEXT NOT NULL, start_at TIMESTAMPTZ NOT NULL,
 until_at TIMESTAMPTZ NOT NULL, sha256 TEXT NOT NULL, bytes BIGINT NOT NULL,
 records BIGINT NOT NULL, audit JSONB NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(observer,url,generation)
);
GRANT SELECT,INSERT,UPDATE ON routing_observers,routing_messages,routing_routes,routing_gaps,routing_archives TO inetuser;
COMMIT;
