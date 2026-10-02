# Multi-tenant Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give wacrm a tenant identifier per account, resolve that identifier from the request hostname, and prove — by test — that one client can never read another's data.

**Architecture:** wacrm is already multi-tenant at the data layer (`accounts` + `account_id` on 27 tables, RLS via `is_account_member()`). This adds only a control plane: a `slug` on `accounts`, an operator-facing `tenants` table, and a hostname→slug resolver invoked from `proxy.ts`. The proxy answers "which subdomain is this" and nothing else; RLS remains the only enforcement layer.

**Tech Stack:** Next.js 16.3.5 (`proxy.ts` convention), TypeScript, Supabase (Postgres 17), Vitest 4.1.11 (`environment: "node"`), Node 20 in CI.

**Spec:** `docs/superpowers/specs/2026-10-01-multi-tenant-foundation-design.md`

## Global Constraints

- Slug rules are fixed: lowercase letters, digits, `-`; must start and end alphanumeric; 3–63 chars; no consecutive hyphens. No other character is permitted anywhere.
- Reserved slugs are exactly: `www, api, admin, mail, app, crm, smtp, ftp, dev, staging, test`.
- `LOCALHOST_TENANT` is read only by the resolver and is **unset by default**; the resolver then returns `null`. An earlier draft of this plan said it "defaults to `default`", which contradicted the review focus below and is withdrawn. See the Task 4 note on how localhost gets a tenant.
- The proxy MUST NOT query Postgres, MUST NOT read `x-tenant-slug` from the client as authority, and MUST NOT select from `accounts` or `tenants`. It reads the request hostname and writes one header. The pre-existing `createServerClient` / `getUser` session-refresh call is not a tenant lookup and is out of scope.
- The existing Supabase session-refresh body of `src/middleware.ts` moves **verbatim**. No refactor, no "while I'm here."
- Migration file is `supabase/migrations/043_tenant_foundation.sql`, must be idempotent, and must not use `NOT NULL` on the new column in the same statement that adds it.
- Existing suite must remain green, with zero lint **errors** (the repo carries a standing set of warnings; "ESLint clean" means 0 errors, not 0 findings). Record the observed file/test counts rather than hardcoding them.
- No new runtime dependencies. No control-panel UI, billing, SSO, branding, backups, or DNS work in this plan.

## Review Focus

The spec is a vision document; these input classes it does not pin are the ones most likely to bite a real user. Each has a test in the task that owns the code.

1. **Stale/hostile `x-tenant-slug` header from the client.** A user can send any header they like. If proxy trusts it, isolation is bypassed. → Task 4 tests that the header is always overwritten, never read.
2. **Slug collision at creation time.** Two accounts wanting `acme` — the second must fail loudly, never silently take the first's slug. → Task 6 verifies the unique index rejects a duplicate. (An earlier draft said Task 3, which has no test file and performs no such check.)
3. **Unknown or absent hostname** (`localhost`, raw IP, garbage `Host`). Must degrade to "unknown tenant", never to a default account's data. → Task 2 tests `null` input returns no slug (`resolve.test.ts`).
4. **`www.` prefixed and uppercase hostnames.** `CRM.Acme.com` must resolve identically to `crm.acme.com`. → Task 2 tests case-folding and `www.` stripping (`resolve.test.ts`).
5. **Account with no slug yet** (created by the `handle_new_user` trigger between migration and backfill). Must not crash the proxy. → Task 2 tests the resolver never throws on any input (`resolve.test.ts`).

---

## File Structure

| File | Responsibility |
|---|---|
| `supabase/migrations/043_tenant_foundation.sql` | Add `accounts.slug`, backfill, unique index, create `tenants` |
| `src/lib/tenant/slug.ts` | Pure slug validation + reserved-word check. No I/O. |
| `src/lib/tenant/slug.test.ts` | Unit tests for the above |
| `src/lib/tenant/resolve.ts` | Hostname → slug, including `LOCALHOST_TENANT` fallback |
| `src/lib/tenant/header.ts` | Writes `x-tenant-slug`, overwriting any client-supplied value |
| `src/lib/tenant/resolve.test.ts` | Unit tests for the above |
| `src/lib/tenant/isolation.sql` | Verification SQL: proves `is_account_member` isolates two accounts. **Not** a vitest file. |
| `src/proxy.ts` | Replaces `src/middleware.ts`. Session refresh (verbatim) + tenant header. |
| `src/lib/tenant/proxy-header.test.ts` | Asserts header is overwritten, not trusted |

`src/lib/tenant/` groups all three modules so they move together. The migration sits with the other 42. The SQL verification file is separate because vitest cannot run SQL.

---

## Task 1: Slug validation module

**Files:**
- Create: `src/lib/tenant/slug.ts`
- Test: `src/lib/tenant/slug.test.ts`

**Interfaces:**
- Consumes: nothing (first task; no dependencies)
- Produces:
  ```ts
  export const RESERVED_SLUGS: readonly string[]
  export function isReservedSlug(slug: string): boolean
  export function validateSlug(input: string):
    | { ok: true; slug: string }
    | { ok: false; error: string }
  ```
  `validateSlug` trims and lowercases before validating; on success returns the normalized slug.

- [ ] **Step 1: Write the failing test**

Create `src/lib/tenant/slug.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { isReservedSlug, validateSlug } from "./slug";

describe("validateSlug", () => {
  it("accepts a plain lowercase slug", () => {
    expect(validateSlug("acme")).toEqual({ ok: true, slug: "acme" });
  });

  it("normalizes case and surrounding whitespace", () => {
    expect(validateSlug("  Acme  ")).toEqual({ ok: true, slug: "acme" });
  });

  it("rejects an empty or whitespace-only input", () => {
    expect(validateSlug("").ok).toBe(false);
    expect(validateSlug("   ").ok).toBe(false);
  });

  it("rejects slugs shorter than 3 characters", () => {
    expect(validateSlug("ab").ok).toBe(false);
  });

  it("accepts a slug of exactly 63 characters", () => {
    expect(validateSlug("a".repeat(63)).ok).toBe(true);
  });

  it("rejects a slug longer than 63 characters", () => {
    expect(validateSlug("a".repeat(64)).ok).toBe(false);
  });

  it("rejects a slug that does not start or end alphanumerically", () => {
    expect(validateSlug("-acme").ok).toBe(false);
    expect(validateSlug("acme-").ok).toBe(false);
  });

  it("rejects consecutive hyphens", () => {
    expect(validateSlug("ac--me").ok).toBe(false);
  });

  it("rejects underscores, dots and non-ascii characters", () => {
    expect(validateSlug("ac_me").ok).toBe(false);
    expect(validateSlug("ac.me").ok).toBe(false);
    expect(validateSlug("acmé").ok).toBe(false);
  });

  it("rejects a reserved slug", () => {
    expect(validateSlug("admin").ok).toBe(false);
    expect(validateSlug("www").ok).toBe(false);
  });

  it("carries an error message on failure", () => {
    const result = validateSlug("ac--me");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.error.length).toBeGreaterThan(0);
  });
});

describe("isReservedSlug", () => {
  it("is true for every reserved word", () => {
    expect(isReservedSlug("admin")).toBe(true);
    expect(isReservedSlug("staging")).toBe(true);
  });

  it("is false for an ordinary slug", () => {
    expect(isReservedSlug("acme")).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/tenant/slug.test.ts`

Expected: FAIL — `Cannot find module './slug'`

- [ ] **Step 3: Implement `src/lib/tenant/slug.ts`**

```ts
export const RESERVED_SLUGS = [
  "www", "api", "admin", "mail", "app", "crm",
  "smtp", "ftp", "dev", "staging", "test",
] as const;

export function isReservedSlug(slug: string): boolean {
  return (RESERVED_SLUGS as readonly string[]).includes(slug.toLowerCase());
}

export function validateSlug(
  input: string,
): { ok: true; slug: string } | { ok: false; error: string } {
  const slug = input.trim().toLowerCase();
  if (!slug) return { ok: false, error: "Slug is required" };
  if (slug.length < 3 || slug.length > 63)
    return { ok: false, error: "Slug must be 3-63 characters" };
  if (!/^[a-z0-9][a-z0-9-]*[a-z0-9]$/.test(slug))
    return { ok: false, error: "Slug must use lowercase letters, digits and hyphens, and start and end with a letter or digit" };
  if (slug.includes("--"))
    return { ok: false, error: "Slug must not contain consecutive hyphens" };
  if (isReservedSlug(slug))
    return { ok: false, error: `"${slug}" is a reserved slug` };
  return { ok: true, slug };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/tenant/slug.test.ts`

Expected: PASS, 13 tests

- [ ] **Step 5: Commit**

```bash
git add src/lib/tenant/slug.ts src/lib/tenant/slug.test.ts
git commit -m "feat(tenant): slug validation with reserved-word list"
```

---

## Task 2: Hostname resolution module

**Files:**
- Create: `src/lib/tenant/resolve.ts`
- Test: `src/lib/tenant/resolve.test.ts`

**Interfaces:**
- Consumes: nothing from Task 1 (deliberately independent — resolution does not validate)
- Produces:
  ```ts
  export function resolveTenantFromHost(host: string | null | undefined): string | null
  ```
  Returns the slug, or `null` when the host carries no subdomain. **Never throws.**

- [ ] **Step 1: Write the failing test**

Create `src/lib/tenant/resolve.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resolveTenantFromHost } from "./resolve";

const ORIGINAL = process.env.LOCALHOST_TENANT;

beforeEach(() => {
  delete process.env.LOCALHOST_TENANT;
});
afterEach(() => {
  if (ORIGINAL === undefined) delete process.env.LOCALHOST_TENANT;
  else process.env.LOCALHOST_TENANT = ORIGINAL;
});

describe("resolveTenantFromHost", () => {
  it("extracts the subdomain from crm.acme.com", () => {
    expect(resolveTenantFromHost("crm.acme.com")).toBe("acme");
  });

  it("strips a port", () => {
    expect(resolveTenantFromHost("crm.acme.com:3000")).toBe("acme");
  });

  it("lowercases the host before resolving", () => {
    expect(resolveTenantFromHost("CRM.Acme.COM")).toBe("acme");
  });

  it("strips a leading www.", () => {
    expect(resolveTenantFromHost("www.acme.com")).toBe("acme");
  });

  it("returns null for localhost when LOCALHOST_TENANT is unset", () => {
    expect(resolveTenantFromHost("localhost:3000")).toBeNull();
  });

  it("returns LOCALHOST_TENANT when set", () => {
    process.env.LOCALHOST_TENANT = "acme";
    expect(resolveTenantFromHost("localhost:3000")).toBe("acme");
  });

  it("returns null for a bare IP address", () => {
    expect(resolveTenantFromHost("127.0.0.1:3000")).toBeNull();
  });

  it("returns null for a two-label host such as acme.com", () => {
    expect(resolveTenantFromHost("acme.com")).toBeNull();
  });

  it("returns null for null, undefined and empty input", () => {
    expect(resolveTenantFromHost(null)).toBeNull();
    expect(resolveTenantFromHost(undefined)).toBeNull();
    expect(resolveTenantFromHost("")).toBeNull();
  });

  it("never throws on malformed input", () => {
    for (const host of ["...", "::1", "%%%%", "a".repeat(500)]) {
      expect(() => resolveTenantFromHost(host)).not.toThrow();
    }
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx vitest run src/lib/tenant/resolve.test.ts`

Expected: FAIL — `Cannot find module './resolve'`

- [ ] **Step 3: Implement `src/lib/tenant/resolve.ts`**

```ts
function isIpLiteral(host: string): boolean {
  // Callers pass a host whose port has already been split off, so it can
  // never contain ":". Only IPv4 needs matching here.
  return /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
}

export function resolveTenantFromHost(
  host: string | null | undefined,
): string | null {
  if (!host) return null;
  // Strip the port and lowercase.
  const name = host.trim().toLowerCase().split(":")[0];
  if (!name || name.includes("..")) return null;
  if (isIpLiteral(name)) return null;
  const parts = name.split(".").filter(Boolean);
  // Need at least 3 labels (crm.acme.com) for a subdomain to exist.
  if (parts.length < 3) {
    return process.env.LOCALHOST_TENANT || null;
  }
  // Take the second-to-last label, i.e. the first label of the registrable
  // domain (acme in acme.com), so crm.acme.com and www.acme.com both resolve
  // to "acme". Taking the label *before* that would be the subdomain, crm.
  //
  // No `www` special-case is needed or wanted: with three-or-more labels the
  // second-to-last label is the same whether or not `www` leads, so stripping
  // it could not change the result. Adding one back would be dead code.
  //
  // Known limitation: this is correct for a two-label public suffix only.
  // A multi-part suffix such as `crm.acme.co.uk` yields "co", and
  // `crm.acme.com.au` yields "com" — which passes validateSlug and would route
  // to the wrong tenant. Fix with a public-suffix list before wildcard DNS goes
  // live; RLS still blocks the read, because profiles.account_id governs data,
  // not this header.
  return parts[parts.length - 2] || null;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx vitest run src/lib/tenant/resolve.test.ts`

Expected: PASS, 10 tests

- [ ] **Step 5: Commit**

```bash
git add src/lib/tenant/resolve.ts src/lib/tenant/resolve.test.ts
git commit -m "feat(tenant): resolve tenant slug from request hostname"
```

---

## Task 3: Migration 043 — `accounts.slug` and `tenants`

**Files:**
- Create: `supabase/migrations/043_tenant_foundation.sql`

**Interfaces:**
- Consumes: nothing (SQL migration; slug rules already encoded in Task 1 for reference)
- Produces: `accounts.slug TEXT` (nullable, unique), and table `tenants(id, slug, account_id, owner_email, status, plan, created_at, updated_at)`

- [ ] **Step 1: Write the failing check**

Run this first to establish the baseline (it should report 0):

```bash
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -t -A -c \
  "select count(*) from information_schema.columns where table_schema='public' and table_name='accounts' and column_name='slug';"
```

Expected: `0`

- [ ] **Step 2: Create the migration**

Create `supabase/migrations/043_tenant_foundation.sql`:

```sql
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
-- Idempotent — safe to run multiple times.
-- ============================================================

-- ---------- accounts.slug ----------
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS slug TEXT;

-- Backfill any account that predates this migration.
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

-- Truncate anything the unconstrained backfill produced too long.
-- Truncation is inlined in the backfill (left(base_slug, 63)) so the dedupe
-- suffix is appended AFTER the cut. A separate truncate-after-the-fact pass
-- like `UPDATE accounts SET slug = left(slug, 63) WHERE length(slug) > 63` is
-- WRONG: it re-cuts a slug that already ends in "-2" and can re-create the
-- collision the suffix exists to prevent. Do not add it.

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
```

- [ ] **Step 3: Apply it locally**

> **DO NOT run `supabase db reset` on the local database.** It is destructive by
> definition, and while it holds the only copy of the session fixtures, a reset
> destroys them with no undo. This step, not the migration, was the actual cause
> of a data loss in this project. Verify the fresh-schema path without
> destruction instead: drop the target objects inside a transaction you roll
> back, run the file, assert, roll back.

```bash
cd C:/Users/FX-tec/Desktop/wacrm-work
# Non-destructive equivalent of a clean-slate check.
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f - <<'SQL'
BEGIN;
DROP TABLE IF EXISTS public.tenants CASCADE;
ALTER TABLE public.accounts DROP COLUMN IF EXISTS slug;
SQL
```

Then apply the file normally, re-run it to prove idempotency, and confirm the
rollback left the schema and data exactly as they were.

Expected: all 43 migrations present, no errors, existing data untouched.

- [ ] **Step 4: Verify the migration landed**

```bash
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -t -A -c \
  "select count(*) from information_schema.columns where table_name='accounts' and column_name='slug';
   select count(*) from information_schema.tables where table_name='tenants';
   select count(*) from accounts where slug is null;"
```

Expected: `1`, `1`, `0` — the last one is the important one: no account left without a slug.

- [ ] **Step 5: Verify idempotency by running the file twice**

```bash
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 -q -f - < supabase/migrations/043_tenant_foundation.sql
```

Expected: exit 0, no errors. This is the property every other migration in this repo claims, so it must hold.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/043_tenant_foundation.sql
git commit -m "feat(tenant): add accounts.slug and tenants table"
```

---

## Task 4: Tenant header in `proxy.ts`

**Files:**
- Create: `src/proxy.ts`
- Create: `src/lib/tenant/header.ts`
- Delete: `src/middleware.ts`
- Test: `src/lib/tenant/proxy-header.test.ts`
- **Rename: `src/middleware.test.ts` → `src/proxy.test.ts`** — REQUIRED, not optional.
  `src/middleware.test.ts:41` is a **top-level** `await import("./middleware")`, so renaming
  `src/middleware.ts` without renaming this file kills **all 15 tests** at collection time,
  not one. An earlier draft of this plan omitted it entirely; that omission was found in
  review and is the single most consequential defect this task ever had.

**Interfaces:**
- Consumes: `resolveTenantFromHost(host)` from Task 2
- Produces: request header `x-tenant-slug`, set on every matched request. Never read from the incoming request as authority.

- [ ] **Step 1: Read the current middleware before touching it**

```bash
cd C:/Users/FX-tec/Desktop/wacrm-work
cat src/middleware.ts
```

Keep this output — the session-refresh body is moved verbatim in Step 6 (an earlier draft said Step 4, which is the header-module step and was wrong).

- [ ] **Step 2: Write the failing test**

Create `src/lib/tenant/proxy-header.test.ts`. It asserts the trust boundary without booting Next.js, by importing the header-writing helper:

```ts
import { describe, expect, it } from "vitest";
import { TENANT_HEADER, withTenantHeader } from "./header";

describe("withTenantHeader", () => {
  it("names the header x-tenant-slug", () => {
    expect(TENANT_HEADER).toBe("x-tenant-slug");
  });

  it("overwrites a client-supplied slug", () => {
    const headers = new Headers({ "x-tenant-slug": "attacker-tenant" });
    withTenantHeader(headers, "acme");
    expect(headers.get("x-tenant-slug")).toBe("acme");
  });

  it("sets the slug when the client sent none", () => {
    const headers = new Headers();
    withTenantHeader(headers, "acme");
    expect(headers.get("x-tenant-slug")).toBe("acme");
  });

  it("removes the header when the host resolves to no tenant", () => {
    const headers = new Headers({ "x-tenant-slug": "attacker-tenant" });
    withTenantHeader(headers, null);
    expect(headers.get("x-tenant-slug")).toBeNull();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npx vitest run src/lib/tenant/proxy-header.test.ts`

Expected: FAIL — `Cannot find module '../tenant/header'`

- [ ] **Step 4: Create `src/lib/tenant/header.ts`**

```ts
export const TENANT_HEADER = "x-tenant-slug";

/**
 * Writes the resolved tenant slug onto outgoing request headers,
 * overwriting anything the client supplied. A client-sent
 * `x-tenant-slug` is attacker-controlled input and must never be
 * treated as authoritative.
 */
export function withTenantHeader(
  headers: Headers,
  slug: string | null,
): void {
  if (slug) headers.set(TENANT_HEADER, slug);
  else headers.delete(TENANT_HEADER);
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx vitest run src/lib/tenant/proxy-header.test.ts`

Expected: PASS, 4 tests

- [ ] **Step 5b: Rename the test file (required).**

```bash
git mv src/middleware.test.ts src/proxy.test.ts
```

Do NOT change its `await import("./middleware")` yet — the export is still named
`middleware` until Step 6.1 renames it. Update the import only after Step 6.1:
`const { proxy } = await import("./proxy");`, and change all five call sites of
`middleware(` to `proxy(`. Doing it in the other order imports `undefined` and the
tests fail in a way that looks like a broken assertion rather than a rename mistake.

- [ ] **Step 6: Rename the entry point and add tenant resolution**

```bash
cd C:/Users/FX-tec/Desktop/wacrm-work
git mv src/middleware.ts src/proxy.ts
```

In `src/proxy.ts`:
1. Rename the exported function `middleware` → `proxy`.
2. Add at the very top of the imports: `import { resolveTenantFromHost } from "@/lib/tenant/resolve"` and `import { TENANT_HEADER, withTenantHeader } from "@/lib/tenant/header"`.
3. As the **first statement** inside the function, before `createServerClient` is called:

```ts
const requestHeaders = new Headers(request.headers);
withTenantHeader(requestHeaders, resolveTenantFromHost(request.headers.get("host")));
```

4. Change `NextResponse.next({ request })` to `NextResponse.next({ request: { headers: requestHeaders } })` at **every occurrence EXCEPT the one inside `cookies.setAll`**. The `setAll` site must re-snapshot the headers *after* it writes the cookies, using a different local variable — see the Task 4 brief, which carries the required code **and the three proxy-level steps this plan does not reproduce: its 2b and 2c tests, and its 2d mutation checks**. Step 2b proves the header is overwritten on the forwarded request; step 2c proves it survives the `setAll` rebuild. Without those tests the Definition of Done item "a client-sent value is overwritten" cannot be satisfied.

   Applying the same substitution at both sites is WRONG and reintroduces issue #288: `requestHeaders` is snapshotted before `createServerClient` runs, so the `setAll` site would forward a pre-rotation cookie. This was proven by execution during review.

   There are **two** occurrences. **Do not trust line numbers from this plan** — the rename and the import block add lines above them, and repeated attempts to compute the post-edit numbers disagreed. Verify the count and locations yourself, against the file that exists at this point in the sequence (`src/proxy.ts`, after the `git mv`):
      ```bash
      grep -n "NextResponse.next" src/proxy.ts
      ```

The `cookies.setAll` callback rewrites `supabaseResponse`, so it must carry the tenant header — which is why it re-snapshots rather than reusing the top-of-function copy.

- [ ] **Step 7: Verify the app still boots and the session still refreshes**

```bash
npm run dev
```

Then in a browser: log in at `http://localhost:3000/login`, land on `/dashboard`, open `/contacts`. All three must render as before. This is the gate for the whole task — the session-refresh logic was moved verbatim and a silent break here logs out every user.

- [ ] **Step 8: Run the full suite**

Run: `npm test`

Expected: zero failures, with the 15 tests from `middleware.test.ts` still present under their new name. **Do not hardcode totals here** — an earlier draft asserted a fixed arithmetic total, which was stale and also miscounted the header tests (there are 4, not 3). Record the counts you actually observe.

- [ ] **Step 9: Commit**

```bash
git add src/proxy.ts src/proxy.test.ts src/lib/tenant/header.ts src/lib/tenant/proxy-header.test.ts
git commit -m "refactor(next): rename middleware to proxy and set tenant header"
```

---

## Task 5: RLS isolation verification

**Files:**
- Create: `src/lib/tenant/isolation.sql`

**Interfaces:**
- Consumes: the `accounts`, `profiles`, `contacts` tables and the `is_account_member()` function from the existing schema
- Produces: no code. A verification artifact, and a recorded result.

This task is a script, not a vitest file, because the repo's tests all mock Supabase and CI has no Postgres service. A mocked RLS test would prove nothing — the mock *is* the assertion. This must run against the real local database.

- [ ] **Step 1: Write the verification script**

Create `src/lib/tenant/isolation.sql`:

```sql
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
```

- [ ] **Step 2: Run it and confirm every assertion**

```bash
cd C:/Users/FX-tec/Desktop/wacrm-work
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -f - < src/lib/tenant/isolation.sql
```

Expected, and each line is a claim that must hold:

- **1** returns at least one row, every slug non-null
- **2** shows `qual = is_account_member(account_id)` on the SELECT policies
- **3** returns `is_account_member` with `uses_auth_uid = t`
- **4** returns `0`

Two of these are stop-the-line failures. If **3** shows `f`, isolation does not rest on `auth.uid()` and the architecture in the spec is wrong — stop and report. If **4** is non-zero, some data table has a different policy and the isolation claim is incomplete — stop and report.

- [ ] **Step 3: Prove the negative case with a real second account**

Create two accounts with one contact each, then read them back through PostgREST as an authenticated user, and confirm neither can see the other's row. This is the only step that exercises RLS as a *user* rather than reading the policy text.

```sql
-- run in the Supabase SQL Editor (browser) — creates the fixtures
-- Do NOT insert into `accounts` directly. `handle_new_user` is a trigger on
-- `auth.users` and provisions the account (and the owner profile) automatically.
-- An earlier draft inserted manually and died on two counts, both verified by
-- execution:
--   * `INSERT INTO accounts (name, slug)` omits `owner_user_id`, which is NOT NULL;
--   * inserting the account again after the trigger made it collide with
--     `idx_accounts_one_per_owner`, because the trigger had already created one.
-- So: create the user, let the trigger provision the account, then assign slugs.
INSERT INTO auth.users (id, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, aud, role, instance_id, created_at, updated_at)
VALUES
 (gen_random_uuid(), 'iso-a@probe.local', crypt('probe', gen_salt('bf')), now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{"full_name":"Iso A"}'::jsonb,
  'authenticated','authenticated','00000000-0000-0000-0000-000000000000', now(), now()),
 (gen_random_uuid(), 'iso-b@probe.local', crypt('probe', gen_salt('bf')), now(),
  '{"provider":"email","providers":["email"]}'::jsonb, '{"full_name":"Iso B"}'::jsonb,
  'authenticated','authenticated','00000000-0000-0000-0000-000000000000', now(), now());
UPDATE accounts SET slug = 'iso-a' WHERE name = 'Iso A' AND slug IS NULL;
UPDATE accounts SET slug = 'iso-b' WHERE name = 'Iso B' AND slug IS NULL;

-- One contact per tenant. Without these, the isolation assertion below reads an
-- empty set and cannot fail: an earlier draft created only accounts, so "only
-- iso-a's contacts appear" was unfalsifiable because there were no contacts at
-- all. `contacts.user_id` and `contacts.account_id` are both NOT NULL, and
-- `contacts.id` defaults to `uuid_generate_v4()` — supply gen_random_uuid()
-- explicitly, since that function is the one that fails on hosted Supabase.
-- Verified by execution (then rolled back): iso-a | Contact A, iso-b | Contact B.
INSERT INTO contacts (id, user_id, account_id, phone, name)
SELECT gen_random_uuid(), u.id, a.id, '+10000000001', 'Contact A'
  FROM auth.users u JOIN accounts a ON a.owner_user_id = u.id
 WHERE u.email = 'iso-a@probe.local';
INSERT INTO contacts (id, user_id, account_id, phone, name)
SELECT gen_random_uuid(), u.id, a.id, '+10000000002', 'Contact B'
  FROM auth.users u JOIN accounts a ON a.owner_user_id = u.id
 WHERE u.email = 'iso-b@probe.local';
```

Then, with the browser signed in as the owner of `iso-a`:

```
GET http://127.0.0.1:54321/rest/v1/contacts?select=id,name
```

Expected: only `iso-a`'s contacts appear. Repeat as `iso-b`'s owner and confirm the sets differ. If either account sees both sets, isolation is broken — stop and report immediately.

- [ ] **Step 4: Record the result in the spec**

Append a `## Verification results` section to the spec file with the actual output of Steps 2 and 3, and the date. A verification whose result is not written down is not a verification.

- [ ] **Step 5: Commit**

```bash
git add src/lib/tenant/isolation.sql docs/superpowers/specs/2026-10-01-multi-tenant-foundation-design.md
git commit -m "test(tenant): verify account isolation through real RLS policies"
```

## Task 6: Document the tenant model

**Files:**
- Create: `docs/tenancy.md`

**Interfaces:**
- Consumes: the finished behaviour of Tasks 1–5
- Produces: nothing consumed by code. This is the document a future operator (you, in three months) reads before provisioning a client by hand.

- [ ] **Step 1: Write the document**

`docs/tenancy.md` must contain, in this order:

1. **The one-paragraph model.** `accounts` is a tenant. `accounts.slug` is its subdomain label. `tenants` is the operator's record of who owns which account. RLS enforces isolation; the proxy only attributes a request to a tenant.
2. **Provisioning a client by hand.** The exact SQL, run in the Supabase SQL Editor:
   ```sql
   INSERT INTO accounts (name, slug, owner_user_id)
   VALUES ('Acme Trading', 'acme', '<auth-user-uuid>');
   INSERT INTO tenants (slug, account_id, owner_email)
   VALUES ('acme', (SELECT id FROM accounts WHERE slug='acme'), 'owner@acme.com');
   ```
   Note the collision case: the second `acme` insert fails on `accounts_slug_key` and must not be worked around.
3. **What is deliberately not here.** No control panel, no billing, no backups. Provisioning is manual until one exists.
4. **Going live on subdomains.** What changes: a base domain, a wildcard `*.domain` DNS record, a wildcard certificate, and `LOCALHOST_TENANT` removed. State plainly that the resolver already handles this and no code changes are needed.

- [ ] **Step 2: Verify every SQL statement in the document actually runs**

```bash
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c "BEGIN; INSERT INTO accounts (name, slug, owner_user_id) SELECT 'Doc Check','doc-check', u.id FROM auth.users u LIMIT 1; INSERT INTO tenants (slug, account_id, owner_email) SELECT 'doc-check', id, 'doc@example.test' FROM accounts WHERE slug='doc-check'; ROLLBACK;"
```

Expected: exit 0. Then repeat with a second insert of the same slug and confirm it fails:

```bash
docker exec -i $(docker ps --filter "name=supabase_db_wacrm" --format "{{.Names}}" | head -1) \
  psql -U postgres -d postgres -t -A -c "INSERT INTO accounts (name, slug, owner_user_id) SELECT 'Dup','doc-check', u.id FROM auth.users u OFFSET 1 LIMIT 1;
```

Expected: an `accounts_slug_key` unique-violation error. If it succeeds, the unique index is missing and Task 3 is incomplete.

- [ ] **Step 3: Commit**

```bash
git add docs/tenancy.md
git commit -m "docs: tenant model and manual provisioning"
```

---

## Definition of Done

Check every box before opening a PR.

- [ ] `npm test` → zero failures; the 15 renamed `proxy.test.ts` tests still present. Record the observed totals.
- [ ] `npx tsc --noEmit` → clean
- [ ] `npx eslint src --ext .ts,.tsx` → clean
- [ ] All 43 migrations are present and the tenant objects are correct. **Do NOT verify this with `supabase db reset`** — that act is forbidden above; verify by applying migration 043 to the live database and re-running it, or in a disposable environment.
- [ ] Running migration 043 twice is a no-op
- [ ] Login, `/dashboard` and `/contacts` all work in a real browser
- [ ] `x-tenant-slug` appears on requests and a client-sent value is overwritten
- [ ] Isolation verification output recorded in the spec
- [ ] `accounts.slug` has no NULL rows
- [ ] `tenants` table exists with RLS enabled
- [ ] No proxy code queries Supabase or selects a database
- [ ] A PR is open on `noursteem020-hue/wacrm` targeting `main`