-- M9-style policy mutation: contacts_delete USING (true). Committed on purpose, in
-- its own transaction, so the probe's own BEGIN...ROLLBACK cannot discard it.
BEGIN;
DROP POLICY IF EXISTS contacts_delete ON public.contacts;
CREATE POLICY contacts_delete ON public.contacts AS PERMISSIVE FOR DELETE TO public USING (true);
COMMIT;
SELECT 'applied contacts_delete_qual=' || coalesce(qual,'(null)')
  FROM pg_policies WHERE schemaname='public' AND tablename='contacts' AND policyname='contacts_delete';
