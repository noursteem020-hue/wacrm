-- ============================================================
-- 043_tenant_foundation.sql — Tenant slug + operator tenant registry
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
-- Two ordering constraints in the backfill are load-bearing:
--   * de-duplication happens in TRUNCATED space, so two names sharing a
--     63-char prefix cannot collapse onto one slug;
--   * the '-N' suffix is appended AFTER truncation, and the base is
--     shortened to leave room for it, so truncation can never erase the
--     suffix and re-create the duplicate it was meant to break.
-- Truncating after appending the suffix — the other order — does both.
--
-- Idempotent — safe to run multiple times.
-- ============================================================

-- ---------- accounts.slug ----------
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS slug TEXT;

-- Backfill any account that predates this migration. The slug is derived
-- from the account name, de-duplicated with a numeric suffix so the
-- unique index below can never be violated.
WITH normalized AS (
  SELECT
    a.id,
    a.created_at,
    -- NULLIF + COALESCE: a name with no usable characters ('   ', '***')
    -- normalizes to nothing, which must become a stable fallback rather
    -- than '' or NULL.
    COALESCE(
      NULLIF(
        trim(BOTH '-' FROM regexp_replace(lower(a.name), '[^a-z0-9]+', '-', 'g')),
        ''
      ),
      'tenant-' || a.id::text
    ) AS base_slug
  FROM accounts a
  WHERE a.slug IS NULL
),
candidates AS (
  SELECT
    id,
    left(base_slug, 63) AS capped_slug,
    ROW_NUMBER() OVER (
      PARTITION BY left(base_slug, 63)
      ORDER BY created_at, id
    ) AS n
  FROM normalized
)
UPDATE accounts a
SET slug = CASE
             WHEN c.n = 1 THEN c.capped_slug
             ELSE left(c.capped_slug, 63 - length('-' || c.n::text)) || '-' || c.n::text
           END
FROM candidates c
WHERE a.id = c.id AND a.slug IS NULL;

-- Reserved words can never be handed to an account.
UPDATE accounts SET slug = 'tenant-' || id::text WHERE slug IN (
  'www','api','admin','mail','app','crm','smtp','ftp','dev','staging','test'
);

CREATE UNIQUE INDEX IF NOT EXISTS accounts_slug_key ON accounts(slug);

-- ---------- tenants ----------
CREATE TABLE IF NOT EXISTS tenants (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
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