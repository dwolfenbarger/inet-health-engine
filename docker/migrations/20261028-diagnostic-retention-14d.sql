-- Extend the existing owned policy's admitted retention; no data is expired here.
BEGIN;
SET LOCAL lock_timeout='1s';
SET LOCAL statement_timeout='5s';
ALTER TABLE routing_diagnostic_policy DROP CONSTRAINT routing_diagnostic_policy_retention_hours_check;
ALTER TABLE routing_diagnostic_policy ADD CONSTRAINT routing_diagnostic_policy_retention_hours_check CHECK(retention_hours BETWEEN 1 AND 336);
COMMIT;
