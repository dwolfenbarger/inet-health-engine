BEGIN;
CREATE TABLE IF NOT EXISTS inet_catalog.memberships(
 generation bigint NOT NULL, asn bigint NOT NULL, observer text NOT NULL,
 prefix cidr NOT NULL, roles smallint NOT NULL,
 PRIMARY KEY(generation,asn,observer,prefix)) PARTITION BY LIST(generation);
GRANT SELECT ON inet_catalog.memberships TO inetuser;
COMMIT;
