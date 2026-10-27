-- Diagnostic evidence owns observer identifiers independently of mutable intake rows.
-- Even a foreign-key KEY SHARE check conflicts with the collector's FOR UPDATE.
BEGIN;
SET LOCAL lock_timeout='200ms';
SET LOCAL statement_timeout='3s';
DO $$
DECLARE r record;
BEGIN
 FOR r IN SELECT c.conname,c.conrelid::regclass tbl
 FROM pg_constraint c
 WHERE c.contype='f' AND c.confrelid='public.routing_observers'::regclass
 AND c.conrelid IN ('public.routing_diagnostic_cursors'::regclass,
 'public.routing_diagnostic_state'::regclass,'public.routing_diagnostic_origins'::regclass,
 'public.routing_diagnostic_events'::regclass)
 LOOP
  IF pg_get_constraintdef((SELECT oid FROM pg_constraint WHERE conrelid=r.tbl AND conname=r.conname)) <> 'FOREIGN KEY (observer) REFERENCES routing_observers(id)' THEN
   RAISE EXCEPTION 'Unexpected diagnostic observer constraint %',r.conname;
  END IF;
  EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',r.tbl,r.conname);
 END LOOP;
END $$;
COMMIT;
