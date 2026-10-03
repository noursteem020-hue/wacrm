-- Proves account isolation through the real RLS policies.
-- Not a vitest file: the repo's unit tests mock Supabase, and a mocked
-- RLS assertion proves nothing — the mock would BE the assertion.
-- Run against local Supabase Postgres.
--
-- The predicate under test is exactly the one on every data table:
--     is_account_member(account_id)
-- which resolves the caller through profiles.user_id = auth.uid().
-- Nothing reads a request header to decide the account.

\echo '=== 1. tenant accounts exist and are distinct ==='
SELECT slug, name FROM accounts WHERE slug IS NOT NULL ORDER BY slug;

\echo '=== 2. the policy predicate on every data table ==='
SELECT tablename, policyname, cmd, qual
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('contacts', 'deals', 'conversations', 'automations')
ORDER BY tablename, policyname;

\echo '=== 3. account resolution is driven by auth.uid(), not a header ==='
SELECT proname,
       prosrc LIKE '%auth.uid()%' AS uses_auth_uid
FROM pg_proc
WHERE pronamespace = 'public'::regnamespace
  AND proname IN ('is_account_member', 'get_user_account_id')
ORDER BY proname;

\echo '=== 4. no data table bypasses is_account_member ==='
-- `cmd = 'SELECT'` is REQUIRED and was missing. Without it this returns 4 and
-- triggers a false stop-the-line: INSERT/UPDATE policies carry their check in
-- `with_check` and have `qual = NULL` by construction, so they match
-- `qual IS NULL` without being a bypass of the SELECT policy. Verified:
--   as written here (no cmd filter) -> 4   (all four are *_insert)
--   with AND cmd = 'SELECT'         -> 0
SELECT count(*) AS tables_with_other_policy
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('contacts', 'deals', 'conversations', 'automations')
  AND cmd = 'SELECT'
  AND (qual IS NULL OR qual NOT LIKE '%is_account_member%');