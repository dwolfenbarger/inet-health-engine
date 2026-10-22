BEGIN;
CREATE TABLE IF NOT EXISTS source_status (
 source TEXT PRIMARY KEY, last_attempt TIMESTAMPTZ NOT NULL,
 last_success TIMESTAMPTZ, last_observation TIMESTAMPTZ,
 state TEXT NOT NULL, records BIGINT NOT NULL DEFAULT 0,
 detail JSONB NOT NULL DEFAULT '{}', error_code TEXT
);
-- Latest evidence per provider identity. Historical raw BGP remains separate.
CREATE TABLE IF NOT EXISTS source_evidence (
 source TEXT NOT NULL, evidence_id TEXT NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL, asn BIGINT, family SMALLINT,
 resource TEXT, payload JSONB NOT NULL,
 PRIMARY KEY(source,evidence_id)
);
CREATE INDEX IF NOT EXISTS source_evidence_recent ON source_evidence(source,observed_at DESC);
CREATE INDEX IF NOT EXISTS source_evidence_asn ON source_evidence(asn,observed_at DESC) WHERE asn IS NOT NULL;
GRANT SELECT,INSERT,UPDATE ON source_status,source_evidence TO inetuser;
COMMIT;
