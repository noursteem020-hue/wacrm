-- ============================================================
-- 047_tenant_foundation.sql — Tenant slug + operator tenant registry
--
-- Adds a stable, human-readable identifier to each account so a
-- request can be attributed to a tenant, and a separate operator-facing
-- `tenants` table recording who owns which account.
--
-- Staged deliberately: the column is added nullable first, then
-- backfilled, then constrained. A single ADD COLUMN ... NOT NULL UNIQUE
-- would fail on any account lacking a slug — and accounts are created by
-- the handle_new_user() trigger, not by a provisioning UI.
--
-- Idempotent — safe to run any number of times, INCLUDING against a
-- database that already holds assigned slugs. That case is routine, not
-- exotic: handle_new_user() derives the account name from full_name, so a
-- duplicate-name signup landing after 043 has run is normal traffic.
--
-- Four properties of the backfill are load-bearing:
--   * de-duplication happens in TRUNCATED space, so two names sharing a
--     63-char prefix cannot collapse onto one slug;
--   * the '-N' suffix is appended AFTER truncation, and the base is
--     shortened to leave room for it, so truncation can never erase the
--     suffix and re-create the duplicate it was meant to break;
--   * "already taken" means "taken by any row in accounts", not "taken
--     inside this pass". Ranking only the NULL-slug subset — the naive
--     version — is blind to slugs assigned earlier, so n = 1 claims a slug
--     another row already holds and the re-run dies on accounts_slug_key;
--   * the emitted value is shaped for validateSlug() in
--     src/lib/tenant/slug.ts: 3-63 chars, first and last character
--     alphanumeric, no '--', never reserved. A slug validateSlug()
--     rejects is an account that can never be served at its subdomain.
-- ============================================================

-- ---------- accounts.slug ----------
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS slug TEXT;

-- Backfill every account with a NULL slug — both those predating this
-- migration and those the handle_new_user() trigger created afterwards.
--
-- This runs as a fixpoint LOOP rather than one UPDATE, and that is the fix
-- for the ranking blindness described in the header. Each pass asks, for
-- every NULL-slug account: "what is the lowest slot in MY '-N' family that
-- no committed row holds?", commits only the pass winners, and repeats
-- until no row can be assigned. Ranking therefore always evaluates against
-- a consistent snapshot of accounts.slug that excludes speculative
-- candidates, and every pass strictly shrinks the NULL-slug set, so the
-- loop terminates — a row, once assigned, is never reassigned.
--
-- Four details, each of which previously produced a slug validateSlug()
-- rejects:
--   1. slugs already held by ANY row count as taken, not merely those held
--      by rows inside this pass;
--   2. the 63-char cap is applied BEFORE the final hyphen trim, because
--      capping after trimming is what left a dangling '-' at position 63;
--   3. rtrim() also runs inside the '-N' slot builder, so a hyphen the
--      truncation cut exposed is dropped before the suffix is appended —
--      without that, '--' can appear;
--   4. a normalized name shorter than 3 characters falls back to
--      'tenant-<uuid>' rather than emitting an unusable 1-2 char slug.
DO $$
DECLARE
  v_assigned INTEGER := 0;
BEGIN
  LOOP
    WITH slugified AS (
      -- '[^a-z0-9]+' collapses every run of unusable characters to a
      -- single '-', so the result never contains '--'; the trim strips the
      -- leading/trailing '-' that names like '-  acme -' would produce.
      SELECT
        a.id,
        a.created_at,
        trim(BOTH '-' FROM regexp_replace(lower(a.name), '[^a-z0-9]+', '-', 'g')) AS trimmed_name
      FROM accounts a
      WHERE a.slug IS NULL
    ),
    normalized AS (
      SELECT
        s.id,
        s.created_at,
        -- The empty-name case ('   ', '***') and the too-short-name case
        -- ('ab') collapse to the same stable fallback: 'tenant-<uuid>'
        -- rather than '', NULL, or a 2-char slug.
        CASE
          WHEN length(s.capped_name) >= 3 THEN s.capped_name
          ELSE 'tenant-' || s.id::text
        END AS base_slug
      FROM (
        SELECT
          id,
          created_at,
          -- Cap THEN trim: trimming first and capping second is what left
          -- a trailing '-' at position 63 (see header note 2).
          rtrim(left(trimmed_name, 63), '-') AS capped_name
        FROM slugified
      ) s
    ),
    ranked AS (
      -- Within one family the oldest account keeps the bare name and later
      -- ones take '-2', '-3', ... id is the deterministic tiebreaker.
      SELECT
        id,
        base_slug,
        ROW_NUMBER() OVER (
          PARTITION BY base_slug
          ORDER BY created_at, id
        ) AS k
      FROM normalized
    ),
    slots AS (
      -- Slot j of a family: j = 1 is the bare base, j >= 2 is the base
      -- truncated to leave room for '-j'. rtrim() drops a hyphen the
      -- truncation cut exposed, so no slot can contain '--'.
      -- 64 slots per family is far more than any real family consumes. A row
      -- whose entire family is saturated is left NULL rather than blocking
      -- the loop. There is no provisioning path that assigns a slug today
      -- (no trigger, no RPC, no application write touches accounts.slug), so
      -- such a row is simply not servable at a hostname. It is not orphaned:
      -- profiles.account_id still links and RLS still resolves, and the whole
      -- app keys off account_id, not slug. Re-running this file after a slot
      -- frees up assigns it.
      SELECT
        r.id,
        r.k,
        g.j,
        CASE
          WHEN g.j = 1 THEN r.base_slug
          ELSE rtrim(left(r.base_slug, 63 - length('-' || g.j::text)), '-') || '-' || g.j::text
        END AS cand
      FROM ranked r
      CROSS JOIN generate_series(1, 64) AS g(j)
    ),
    scored AS (
      SELECT
        s.id,
        s.k,
        s.cand,
        s.is_free,
        -- free_rank = index of this slot among the family's FREE slots, so
        -- the row that is k-th in line takes the k-th free slot rather than
        -- the k-th slot. This is what lets a row whose older siblings
        -- already hold slugs land on a genuinely empty one.
        SUM(CASE WHEN s.is_free THEN 1 ELSE 0 END) OVER (
          PARTITION BY s.id
          ORDER BY s.j
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS free_rank
      FROM (
        SELECT
          sl.id,
          sl.k,
          sl.j,
          sl.cand,
          -- Free = no committed row holds it, and it is not reserved.
          -- Checked against real rows only, never against the speculative
          -- slots in this CTE.
          (NOT EXISTS (SELECT 1 FROM accounts t WHERE t.slug = sl.cand))
          AND sl.cand NOT IN (
            'www','api','admin','mail','app','crm','smtp','ftp','dev','staging','test'
          ) AS is_free
        FROM slots sl
      ) s
    ),
    claimed AS (
      SELECT
        id,
        cand,
        -- Two different families can still converge on one value ('acme'
        -- plus duplicates, and a separate 'acme-3' account). Only one row
        -- commits it this pass; the loser simply takes a higher slot on
        -- the next pass, once the winner's slug is committed.
        ROW_NUMBER() OVER (PARTITION BY cand ORDER BY id) AS claim
      FROM scored
      WHERE is_free AND free_rank = k
    )
    UPDATE accounts a
    SET slug = c.cand
    FROM claimed c
    WHERE a.id = c.id AND a.slug IS NULL AND c.claim = 1;

    GET DIAGNOSTICS v_assigned = ROW_COUNT;
    EXIT WHEN v_assigned = 0;
  END LOOP;
END
$$;

-- Reserved words can never be handed to an account. The backfill above
-- already skips reserved slots, so this is a defensive second line for
-- rows the loop could not reach (still NULL slug).
--
-- The scope is deliberately narrow: only rows the backfill did not reach
-- (slug IS NULL) whose normalized name is itself reserved. Without the
-- slug IS NULL guard this statement matched ANY row currently holding a
-- reserved word, so every run after the first silently rewrote a
-- deliberately operator-assigned 'admin' to 'tenant-<uuid>' — and the
-- subdomain DNS for that account then pointed at nothing. Re-run safety is
-- worth more than the belt-and-braces it costs.
UPDATE accounts a
SET slug = 'tenant-' || a.id::text
WHERE a.slug IS NULL
  AND trim(BOTH '-' FROM regexp_replace(lower(a.name), '[^a-z0-9]+', '-', 'g')) IN (
    'www','api','admin','mail','app','crm','smtp','ftp','dev','staging','test'
  );

CREATE UNIQUE INDEX IF NOT EXISTS accounts_slug_key ON accounts(slug);

-- ---------- tenants ----------
CREATE TABLE IF NOT EXISTS tenants (
  -- gen_random_uuid(), not uuid_generate_v4(): the latter lives in the
  -- `extensions` schema on hosted Supabase and does not resolve without an
  -- explicit search_path — the same blocker already recorded for the cloud
  -- migration. Migrations 026/028/029/030/033 in this repo already use
  -- gen_random_uuid().
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        TEXT NOT NULL UNIQUE,
  account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  owner_email TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'active',
  plan        TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenants_account_id_idx ON tenants(account_id);

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;

-- ---------- updated_at trigger ----------
CREATE OR REPLACE FUNCTION public.set_tenants_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS tenants_set_updated_at ON tenants;
CREATE TRIGGER tenants_set_updated_at
BEFORE UPDATE ON tenants
FOR EACH ROW
EXECUTE FUNCTION public.set_tenants_updated_at();