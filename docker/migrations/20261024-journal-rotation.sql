-- UAT-qualified operational journal. Payload expiry requires a verified NAS receipt.
BEGIN;
SET LOCAL lock_timeout='3s';
CREATE TABLE IF NOT EXISTS routing_messages (
 observer text NOT NULL REFERENCES routing_observers(id),message_id text NOT NULL,
 received_at timestamptz NOT NULL,event_at timestamptz NOT NULL,sequence bigint NOT NULL,payload jsonb NOT NULL,
 PRIMARY KEY(observer,message_id)
);
CREATE INDEX IF NOT EXISTS routing_messages_recent ON routing_messages(observer,received_at DESC);
CREATE TABLE IF NOT EXISTS routing_journal_policy (
 id boolean PRIMARY KEY DEFAULT true CHECK(id),
 local_hours integer NOT NULL DEFAULT 24 CHECK(local_hours>=24),
 dedup_hours integer NOT NULL DEFAULT 72 CHECK(dedup_hours>=72),
 hot_limit bigint NOT NULL DEFAULT 8589934592,
 key_limit bigint NOT NULL DEFAULT 8589934592,
 root_reserve bigint NOT NULL DEFAULT 21474836480,
 enabled boolean NOT NULL DEFAULT false,
 updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO routing_journal_policy(id) VALUES(true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS routing_journal_segments (
 name text PRIMARY KEY, from_at timestamptz NOT NULL, until_at timestamptz NOT NULL,
 state text NOT NULL DEFAULT 'local', receipt jsonb, archived_at timestamptz, retired_at timestamptz,
 CHECK(until_at>from_at)
);
CREATE TABLE IF NOT EXISTS routing_journal_copy_progress (
 observer text PRIMARY KEY, message_id text NOT NULL DEFAULT '', copied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS routing_message_keys (
 observer text NOT NULL REFERENCES routing_observers(id), message_id text NOT NULL,
 received_at timestamptz NOT NULL, event_at timestamptz NOT NULL, sequence bigint NOT NULL, stored_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(observer,message_id)
);
CREATE INDEX IF NOT EXISTS routing_keys_age ON routing_message_keys(event_at,stored_at);
ALTER TABLE routing_observers ADD COLUMN IF NOT EXISTS dedup_floor_at timestamptz;
GRANT SELECT ON routing_journal_policy,routing_journal_segments,routing_message_keys TO inetuser;
GRANT INSERT(observer,message_id,received_at,event_at,sequence) ON routing_message_keys TO inetuser;
COMMIT;
