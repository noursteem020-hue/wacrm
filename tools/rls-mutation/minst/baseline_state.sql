-- Baseline / restore proof for the M-inst campaign.
-- The FINGERPRINT blocks below are FINGERPRINT_SQL from preflight6.py, byte-identical
-- (the E'\n' separator included), so the token is comparable with
--   EXPECTED_BASELINE_FP = bfae0aea057682e5403f70c94f0b5f61
--   BASELINE_MD5         = 026fa63f24c5f54584758c4f5d314408
SELECT 'prosrc_md5=' || md5(p.prosrc)
    || ' prosrc_len=' || length(p.prosrc)
    || ' cr=' || (length(p.prosrc) - length(replace(p.prosrc, chr(13), '')))
    || ' baseline_md5_match=' || (md5(p.prosrc) = '026fa63f24c5f54584758c4f5d314408')::text
FROM pg_proc p WHERE p.oid = 'public.is_account_member(uuid,account_role_enum)'::regprocedure
UNION ALL
SELECT 'fingerprint_token=' || (
SELECT md5(coalesce(string_agg(l, E'\n' ORDER BY l), '(none)')) FROM (
  SELECT format('%s|%s|%s|%s|%s|%s|%s|%s', schemaname, tablename, policyname,
                permissive, roles::text, cmd, coalesce(qual,'-'), coalesce(with_check,'-')) AS l
    FROM pg_policies WHERE schemaname='public'
  UNION ALL
  SELECT format('RLS|%s|%s', c.relname, c.relrowsecurity::text || '/' || c.relforcerowsecurity::text)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
  UNION ALL
  SELECT format('FN|%s|%s|%s|%s|%s|%s|%s|%s', p.oid::regprocedure::text, md5(p.prosrc),
                p.proowner::regrole, p.prosecdef,
                coalesce(array_to_string(p.proconfig,','),'-'),
                p.provolatile, p.proparallel, p.proargdefaults::text)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prokind = 'f'
) s)
UNION ALL
SELECT 'fingerprint_match=' || ((
SELECT md5(coalesce(string_agg(l, E'\n' ORDER BY l), '(none)')) FROM (
  SELECT format('%s|%s|%s|%s|%s|%s|%s|%s', schemaname, tablename, policyname,
                permissive, roles::text, cmd, coalesce(qual,'-'), coalesce(with_check,'-')) AS l
    FROM pg_policies WHERE schemaname='public'
  UNION ALL
  SELECT format('RLS|%s|%s', c.relname, c.relrowsecurity::text || '/' || c.relforcerowsecurity::text)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
  UNION ALL
  SELECT format('FN|%s|%s|%s|%s|%s|%s|%s|%s', p.oid::regprocedure::text, md5(p.prosrc),
                p.proowner::regrole, p.prosecdef,
                coalesce(array_to_string(p.proconfig,','),'-'),
                p.provolatile, p.proparallel, p.proargdefaults::text)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prokind = 'f'
) s) = 'bfae0aea057682e5403f70c94f0b5f61')::text
UNION ALL
SELECT 'contacts_delete_qual=' || coalesce(qual,'(null)')
  FROM pg_policies WHERE schemaname='public' AND tablename='contacts' AND policyname='contacts_delete'
UNION ALL
SELECT 'policy_deps_on_is_account_member=' || count(*) FROM pg_policies
 WHERE schemaname='public' AND (coalesce(qual,'')||coalesce(with_check,'')) LIKE '%is_account_member%';
