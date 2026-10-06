-- Correct restore. IF EXISTS is load-bearing: a bare DROP POLICY errors when the
-- policy is absent, and under ON_ERROR_STOP the rest of the restore is skipped,
-- leaving the database mutated.
BEGIN;
DROP POLICY IF EXISTS contacts_delete ON public.contacts;
CREATE POLICY contacts_delete ON public.contacts AS PERMISSIVE FOR DELETE TO public USING (is_account_member(account_id, 'agent'::account_role_enum));
COMMIT;
SELECT 'restored contacts_delete_qual=' || coalesce(qual,'(null)')
  FROM pg_policies WHERE schemaname='public' AND tablename='contacts' AND policyname='contacts_delete';
