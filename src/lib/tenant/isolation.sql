-- Proves account isolation through the real RLS policies.
-- Not a vitest file: the repo's unit tests mock Supabase, and a mocked
-- RLS assertion proves nothing — the mock would BE the assertion.
-- Run against local Supabase Postgres:
--   docker exec -i supabase_db_wacrm psql -U postgres -d postgres -f - \
--     < src/lib/tenant/isolation.sql
--
-- The predicate under test is exactly the one on every data table:
--     is_account_member(account_id)
-- which resolves the caller through profiles.user_id = auth.uid().
-- Nothing reads a request header to decide the account.
--
-- ---------------------------------------------------------------------------
-- EXIT CODE IS THE RESULT. THIS SCRIPT HAS A GATE.
--
--   exit 0 = every assertion below held
--   exit 3 = at least one assertion failed. psql exits 3 when ON_ERROR_STOP is
--            on and a statement raises.
--
-- Sections 1-6 are INFORMATIONAL CENSUS: they print, they never judge. Their
-- output is not a verdict and must not be read as one — a census row reading
-- "is_account_member" states which policy text exists, not that RLS works.
-- The gate is sections 7-9, which push real traffic through the real policies.
--
-- WHY NOT \quit: measured on this container, `\quit 7` exits 0 (psql warns
-- "extra argument ignored" and drops it). The only measured mechanism that
-- yields a non-zero exit is a raised error with ON_ERROR_STOP on -> exit 3.
-- So the gate is a RAISE EXCEPTION synthesised at the end of the file.
--
-- NOTHING IS COMMITTED. Every mutating block is BEGIN ... ROLLBACK, so the
-- script leaves zero rows behind and is safe to run twice in a row. Row-level
-- security state and policies are read, never changed.
-- ---------------------------------------------------------------------------

\set ON_ERROR_STOP on

\echo ''
\echo '=== INFORMATIONAL CENSUS 1-6 (reporting only, NOT a pass/fail gate) ==='

\echo ''
\echo '--- 1. tenant accounts exist and are distinct ---'
SELECT slug, name FROM accounts WHERE slug IS NOT NULL ORDER BY slug;

\echo ''
\echo '--- 2. the policy predicate on every data table ---'
SELECT tablename, policyname, cmd, qual
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('contacts', 'deals', 'conversations', 'automations')
ORDER BY tablename, policyname;

\echo ''
\echo '--- 3. account resolution is driven by auth.uid(), not a header ---'
SELECT proname,
       prosrc LIKE '%auth.uid()%' AS uses_auth_uid
FROM pg_proc
WHERE pronamespace = 'public'::regnamespace
  AND proname IN ('is_account_member', 'get_user_account_id')
ORDER BY proname;

\echo ''
\echo '--- 4. no data table bypasses is_account_member ---'
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

\echo ''
\echo '--- 5. which account-scoped tables exist, and does each one gate SELECT? ---'
-- All 27 tables carrying account_id, not just the four above. A table with NO
-- SELECT policy is fail-closed (RLS on, no permissive policy -> no rows), which
-- is a safe answer but a different one from "gated by is_account_member" -- so
-- the two states are reported separately instead of being lumped together.
WITH acct AS (
  SELECT table_name FROM information_schema.columns
  WHERE table_schema = 'public' AND column_name = 'account_id'
),
sel AS (
  SELECT tablename,
         count(*) AS select_policies,
         bool_or(qual IS NULL OR qual NOT LIKE '%is_account_member%') AS bypasses_member
  FROM pg_policies
  WHERE schemaname = 'public' AND cmd = 'SELECT'
  GROUP BY tablename
)
SELECT a.table_name,
       coalesce(s.select_policies, 0) AS select_policies,
       CASE
         WHEN coalesce(s.select_policies, 0) = 0 THEN 'no SELECT policy (fail-closed)'
         WHEN s.bypasses_member              THEN 'SELECT policy not is_account_member'
         ELSE 'is_account_member'
       END AS gate
FROM acct a LEFT JOIN sel s ON s.tablename = a.table_name
ORDER BY (coalesce(s.select_policies, 0) = 0) DESC, s.bypasses_member DESC NULLS LAST, a.table_name;

\echo ''
\echo '--- 6. profiles.user_id is UNIQUE -> user-scoped policy == account-scoped ---'
-- This is what makes a `uid() = user_id` policy (notifications) safe rather than
-- a hole: one user_id belongs to exactly one account, so user-scope and
-- account-scope cannot diverge. If this constraint is ever dropped, the
-- notifications policy becomes a cross-account leak.
SELECT conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint
WHERE conrelid = 'public.profiles'::regclass AND contype IN ('u', 'p')
ORDER BY conname;

-- ---------------------------------------------------------------------------
-- GATE
-- ---------------------------------------------------------------------------
\echo ''
\echo '=== 7-9. GATE: live cross-account traffic through the real policies ==='

-- Fixture defaults, each guarded so a -v override is never clobbered.
--   psql -v probe_a=<account uuid> -v uid_a=<auth user_id>
--   psql -v probe_b=<account uuid> -v uid_b=<auth user_id> -v nonce=<digits>
\if :{?probe_a}
\else
\set probe_a 0661e985-3c71-48e5-aafd-bc5277eca3a2
\endif
\if :{?uid_a}
\else
\set uid_a 1f1cb02d-8af2-4198-b070-95b89cd995b6
\endif
\if :{?probe_b}
\else
\set probe_b e4e71025-bef5-4247-a403-0d4f76e78d38
\endif
\if :{?uid_b}
\else
\set uid_b 8f4a20fa-dadb-4acb-95e7-ebf0f4fe3e85
\endif

-- The nonce IS genuinely per-run now: derived from md5(random()), not from a
-- constant. The previous file derived it from a hardcoded uid via `cut -c1-4`,
-- so it was the literal '1f1c' on every run while a comment claimed it was
-- per-run; runs never collided only because every block rolls back.
--
-- It exists so probe rows cannot collide on the partial unique index
-- idx_contacts_account_phone_normalized, and so probe rows are identifiable.
-- Digits only: phone_normalized strips non-digits and the seeded phones are
-- built by concatenation, so punctuation here would corrupt phone_normalized.
\if :{?nonce}
\else
SELECT lpad((abs(('x' || substr(md5(random()::text), 1, 7))::bit(32)::bigint) % 900000
             + 100000)::text, 6, '0') AS nonce \gset iso_nonce_
\set nonce :iso_nonce_nonce
\endif

-- The probe marker lives in `email`: nullable, no unique index, no constraint.
-- It identifies WHICH rows are probe rows and nothing else. It deliberately does
-- NOT encode who owns a row. The previous version encoded the owner in `name`
-- ('iso-1f1c-A-B') and then counted LIKE 'iso-%-B-%' as the leak -- which does
-- not match a row actually owned by B. Ownership is now read from account_id
-- itself, so the leak predicate is a direct column comparison and cannot be
-- blind to a change of shape.
SELECT 'iso-' || :'nonce' || '@isolation.probe.invalid' AS marker \gset iso_marker_
-- Unquoted form: psql's :var is fine for \echo. Use :'marker' when the value
-- goes into a SQL literal. Setting it quoted here made \echo print the SQL
-- quotes and broke the echoed line.
\set marker :iso_marker_marker

\echo ''
\echo '--- run nonce:' :nonce '  probe marker:' :marker
\echo '--- probe A: account' :probe_a '  user' :uid_a
\echo '--- probe B: account' :probe_b '  user' :uid_b

-- Failure ledger. Accumulated in a psql variable so one run reports EVERY broken
-- assertion instead of stopping at the first, then raised once at the end.
\set ledger ''

-- Seed INSIDE each user's own transaction. An earlier draft seeded once and
-- then asserted for a second user after the ROLLBACK, so that second assertion
-- ran against an unseeded table and read own_rows = 0 -- vacuous in exactly the
-- way this section exists to prevent. Each block is self-contained: seed,
-- measure, roll back.

\echo ''
\echo '--- 7a. as user A: sees its own seeded row, and NOTHING outside its account ---'
BEGIN;
INSERT INTO contacts (account_id, user_id, phone, email)
SELECT c.acct, c.uid,
       '+1555' || :'nonce' || c.tag || substr(replace(c.acct::text, '-', ''), 1, 3),
       :'marker'
FROM (VALUES
  (:'probe_a'::uuid, :'uid_a'::uuid, 'a'),
  (:'probe_b'::uuid, :'uid_b'::uuid, 'b')
) AS c(acct, uid, tag);

-- Measured as anon with a real JWT, so the policies under test are the ones
-- that decide what is visible. SET LOCAL ROLE is transactional and never
-- committed.
SET LOCAL ROLE anon;
SELECT set_config('request.jwt.claims',
  json_build_object('sub', :'uid_a', 'role', 'authenticated')::text, true);

-- own_rows is compared directly against account_id; no name pattern is
-- involved. leak_general also catches a row from ANY account other than the
-- caller's, so it fails on a leak that the two-fixture shape would miss.
SELECT count(*) FILTER (WHERE account_id = :'probe_a'::uuid) AS own_rows,
       count(*) FILTER (WHERE account_id = :'probe_b'::uuid) AS other_account_leak,
       count(*) FILTER (WHERE account_id <> :'probe_a'::uuid)  AS leak_general,
       count(*) AS probe_rows_visible
FROM contacts WHERE email = :'marker' \gset a_
\echo '   own_rows=' :a_own_rows '  other_account_leak=' :a_other_account_leak '  leak_general=' :a_leak_general '  probe_rows_visible=' :a_probe_rows_visible

-- own_rows > 0 is what makes the run non-vacuous: if the predicate denied
-- everything, every counter would read 0 and "no leak" would be meaningless.
SELECT :'ledger' || CASE WHEN :'a_own_rows'::bigint = 0
  THEN E'[7a] FAIL user A reads 0 of its own rows (own_rows=0): the isolation predicate is broken, not merely strict\n'
  ELSE '' END AS ledger \gset
SELECT :'ledger' || CASE WHEN :'a_other_account_leak'::bigint > 0
  THEN format(E'[7a] FAIL user A sees %s probe row(s) owned by account B -- cross-account SELECT leak\n',
              :'a_other_account_leak') ELSE '' END AS ledger \gset
SELECT :'ledger' || CASE WHEN :'a_leak_general'::bigint > 0
  THEN format(E'[7a] FAIL user A sees %s probe row(s) outside its own account (any account, not only B)\n',
              :'a_leak_general') ELSE '' END AS ledger \gset
ROLLBACK;

\echo ''
\echo '--- 7b. as user B: sees its own seeded row, and NOTHING outside its account ---'
BEGIN;
INSERT INTO contacts (account_id, user_id, phone, email)
SELECT c.acct, c.uid,
       '+1555' || :'nonce' || c.tag || substr(replace(c.acct::text, '-', ''), 1, 3),
       :'marker'
FROM (VALUES
  (:'probe_a'::uuid, :'uid_a'::uuid, 'a'),
  (:'probe_b'::uuid, :'uid_b'::uuid, 'b')
) AS c(acct, uid, tag);

SET LOCAL ROLE anon;
SELECT set_config('request.jwt.claims',
  json_build_object('sub', :'uid_b', 'role', 'authenticated')::text, true);

SELECT count(*) FILTER (WHERE account_id = :'probe_b'::uuid) AS own_rows,
       count(*) FILTER (WHERE account_id = :'probe_a'::uuid) AS other_account_leak,
       count(*) FILTER (WHERE account_id <> :'probe_b'::uuid)  AS leak_general,
       count(*) AS probe_rows_visible
FROM contacts WHERE email = :'marker' \gset b_
\echo '   own_rows=' :b_own_rows '  other_account_leak=' :b_other_account_leak '  leak_general=' :b_leak_general '  probe_rows_visible=' :b_probe_rows_visible

SELECT :'ledger' || CASE WHEN :'b_own_rows'::bigint = 0
  THEN E'[7b] FAIL user B reads 0 of its own rows (own_rows=0): the isolation predicate is broken, not merely strict\n'
  ELSE '' END AS ledger \gset
SELECT :'ledger' || CASE WHEN :'b_other_account_leak'::bigint > 0
  THEN format(E'[7b] FAIL user B sees %s probe row(s) owned by account A -- cross-account SELECT leak\n',
              :'b_other_account_leak') ELSE '' END AS ledger \gset
SELECT :'ledger' || CASE WHEN :'b_leak_general'::bigint > 0
  THEN format(E'[7b] FAIL user B sees %s probe row(s) outside its own account (any account, not only A)\n',
              :'b_leak_general') ELSE '' END AS ledger \gset
ROLLBACK;

\echo ''
\echo '--- 7c. seed completeness: the admin view really holds one row per account ---'
-- Guards the opposite direction: if seeding silently wrote both rows to one
-- account, 7a/7b would be measuring the wrong shape.
BEGIN;
INSERT INTO contacts (account_id, user_id, phone, email)
SELECT c.acct, c.uid,
       '+1555' || :'nonce' || 's' || substr(replace(c.acct::text, '-', ''), 1, 3),
       :'marker'
FROM (VALUES
  (:'probe_a'::uuid, :'uid_a'::uuid, 'a'),
  (:'probe_b'::uuid, :'uid_b'::uuid, 'b')
) AS c(acct, uid, tag);
SELECT count(DISTINCT account_id) AS accounts_seeded, count(*) AS rows_seeded
FROM contacts WHERE email = :'marker' \gset s_
\echo '   accounts_seeded=' :s_accounts_seeded '  rows_seeded=' :s_rows_seeded
SELECT :'ledger' || CASE WHEN :'s_accounts_seeded'::bigint <> 2
  THEN format(E'[7c] FAIL seed covered %s account(s), expected 2: 7a/7b would be measuring the wrong shape\n',
              :'s_accounts_seeded') ELSE '' END AS ledger \gset
ROLLBACK;

-- ===========================================================================
\echo ''
\echo '--- 8. NEGATIVE: INSERT into another account must be REJECTED by WITH CHECK ---'
-- The whole probe lives inside ONE DO block, so the offending INSERT runs in a
-- subtransaction we can catch. The outcome is recorded in GUCs and the row
-- count is taken from INSIDE that block, BEFORE anything is rolled back.
--
-- The previous version did SAVEPOINT / INSERT / ROLLBACK TO SAVEPOINT and only
-- then counted, so the rollback destroyed the evidence first and leaked_rows was
-- structurally always 0 -- it printed 0 and exited 0 even with RLS disabled.
--
-- The assertion is on the OUTCOME, not on the count. A cross-account INSERT
-- rejected for the wrong reason (a unique violation, say) is not isolation, so
-- only SQLSTATE 42501 counts as a pass and anything else fails the run.
-- ===========================================================================
BEGIN;
-- psql does NOT interpolate :'var' inside a dollar-quoted DO body -- the text
-- goes to the server verbatim and you get "syntax error at or near :". So the
-- fixtures are handed to the block through custom GUCs, which the block reads
-- with current_setting(). Measured working: current_setting() is readable
-- inside a DO block after SET LOCAL ROLE anon.
SELECT set_config('iso.probe_b', :'probe_b', true);
SELECT set_config('iso.uid_a',   :'uid_a',   true);
SELECT set_config('iso.marker',  :'marker',  true);

DO $iso_section8$
DECLARE
  v_state  text   := 'ALLOWED';
  v_leaked bigint := 0;
BEGIN
  SET LOCAL ROLE anon;
  PERFORM set_config('request.jwt.claims',
    json_build_object('sub', current_setting('iso.uid_a'), 'role', 'authenticated')::text, true);

  BEGIN
    INSERT INTO contacts (account_id, user_id, phone, email)
    VALUES (current_setting('iso.probe_b')::uuid,
            current_setting('iso.uid_a')::uuid,
            '+1555' || current_setting('iso.probe_b')::text,  -- unique per run
            current_setting('iso.marker'));
    v_state := 'ALLOWED';
  EXCEPTION WHEN SQLSTATE '42501' THEN
    v_state := 'REJECTED_RLS';
  WHEN OTHERS THEN
    v_state := 'REJECTED_OTHER_' || SQLSTATE;
  END;

  -- Counted here, while a row user A inserted into account B is still present.
  SELECT count(*) INTO v_leaked FROM contacts WHERE email = current_setting('iso.marker');
  RESET ROLE;
  PERFORM set_config('iso.section8.outcome', v_state, true);
  PERFORM set_config('iso.section8.leaked', v_leaked::text, true);
END $iso_section8$;

SELECT current_setting('iso.section8.outcome') AS outcome,
       current_setting('iso.section8.leaked')::bigint AS leaked_rows,
       (SELECT count(*) FROM contacts WHERE email = :'marker') AS cross_account_row_count
\gset s8_
\echo '   outcome=' :s8_outcome '  leaked_rows_at_probe_time=' :s8_leaked_rows '  cross_account_row_count=' :s8_cross_account_row_count

SELECT :'ledger' || CASE
  WHEN :'s8_outcome' = 'ALLOWED'
    THEN E'[8] FAIL cross-account INSERT was ALLOWED: user A wrote a row into account B and the WITH CHECK policy did not stop it\n'
  WHEN :'s8_outcome' <> 'REJECTED_RLS'
    THEN format(E'[8] FAIL cross-account INSERT was rejected for the WRONG REASON (%s), not by row-level security (42501): a rejection that is not RLS is not isolation\n',
                :'s8_outcome')
  ELSE '' END AS ledger \gset
ROLLBACK;

\echo ''
\echo '--- 9. fail-closed census: tables with no SELECT policy (reporting only) ---'
-- Reporting, not gating: a SELECT policy added to tenants or
-- automation_pending_executions would legitimately change these numbers, and a
-- non-zero "visible" here is not by itself proof of a leak.
BEGIN;
SET LOCAL ROLE anon;
SELECT set_config('request.jwt.claims',
  json_build_object('sub', :'uid_a', 'role', 'authenticated')::text, true);
SELECT (SELECT count(*) FROM tenants)                       AS tenants_visible,
       (SELECT count(*) FROM automation_pending_executions) AS pending_visible,
       (SELECT count(*) FROM contacts)                       AS contacts_visible;
ROLLBACK;

-- ---------------------------------------------------------------------------
-- THE GATE. One raised error, so psql exits non-zero (measured: exit 3).
-- ---------------------------------------------------------------------------
\echo ''
\echo '--- gate ---'
SELECT (length(:'ledger') > 0) AS has_failures \gset g_
\if :g_has_failures
\echo 'TENANT ISOLATION FAILED. Failures:'
\echo :ledger
-- RAISE EXCEPTION rather than \quit (measured to exit 0), with ON_ERROR_STOP on
-- (set at the top of this file) -> psql exits 3. ISO01 is a custom SQLSTATE so
-- it can never be confused with a 42501 raised by the policy being probed.
--
-- The outer format() tag is $fmt$, NOT $iso$: a nested dollar quote with the
-- SAME tag closes the outer literal early, which produced
-- "syntax error at or near BEGIN" and made the run fail for the wrong reason.
SELECT format($fmt$DO $iso$ BEGIN RAISE EXCEPTION %L USING ERRCODE = 'ISO01'; END $iso$;$fmt$,
              'tenant isolation FAILED: ' || :'ledger') AS iso_raise \gset iso_raise_
SELECT :'iso_raise_iso_raise' AS iso_raise_stmt \gexec
\else
\echo 'TENANT ISOLATION OK: own_rows > 0 for both users, 0 cross-account probe rows visible, cross-account INSERT rejected with 42501.'
\endif
