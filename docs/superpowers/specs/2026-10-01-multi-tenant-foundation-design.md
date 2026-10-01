# Multi-tenant foundation — design spec

**Date:** 2026-10-01 · **Branch:** `feat/multi-tenant-foundation`
**Base:** `477d017` on `noursteem020-hue/wacrm` (fork of `ArnasDon/wacrm`)
**Status:** awaiting review

---

## Purpose

Turn wacrm from a single-tenant CRM template into a system that can host
multiple client companies, each reached at its own subdomain, with one
operator (me) provisioning them.

Success means: a request to `crm.<client>.com` resolves to exactly that
client's data, and no request can ever reach another client's data.

---

## What already exists (measured, not assumed)

wacrm is **already multi-tenant at the data layer**. This is the single most
important finding of the design phase, and it substantially reduces the work:

| Existing asset | Detail |
|---|---|
| `accounts` table | `id, name, owner_user_id, default_currency, created_at, updated_at` |
| `account_id` column | present on **27 tables** (`contacts`, `conversations`, `deals`, `automations`, `broadcasts`, `flows`, `pipelines`, `message_templates`, `api_keys`, `ai_configs`, …) |
| Row Level Security | enabled across those tables; isolation **verified at runtime** with two accounts returning different rows for the same query |
| Roles | `profiles.account_role` ∈ `owner / admin / agent / viewer` |
| Team invites | `account_invitations` table exists |

**Therefore this project adds a control plane, not a tenancy system.** RLS
remains the single enforcement layer. Nothing in this design creates a second
authorization path.

---

## Decisions taken (and why)

| Decision | Rationale |
|---|---|
| Shared database + `account_id`, RLS isolation | Already built and verified. Chosen before inspecting the code, then confirmed. |
| Subdomain per tenant (`crm.acme.com`) | Clean separation; requires wildcard DNS + cert, deferred. |
| `proxy.ts`, not `middleware.ts` | Next 16 documents `proxy.ts` as the current convention (`node_modules/next/dist/docs/01-app/01-getting-started/16-proxy.md`). Only one proxy file is supported per project. |
| Single proxy file, logic in modules | Same doc: "Break out proxy functionalities into separate `.ts` files and import them into your main `proxy.ts`." |
| localhost-first | First milestone runs entirely on `localhost`; subdomains come after the first real client. |
| No control-panel UI in this milestone | Explicitly out of scope, and not a success condition. |

---

## Scope

**In:**
- `accounts.slug` — unique, validated tenant identifier
- `tenants` table — operator's record of which account belongs to whom
- slug validation module + reserved-word list
- hostname → slug resolution module
- `proxy.ts` — hostname → `x-tenant-slug` request header
- Tests: slug, hostname resolution, **RLS isolation (integration)**, proxy header

**Out — not a success condition, not started here:**
- Control-panel UI, signup flow, billing, renewals
- SSO, custom branding, white-label theming
- Automated backups, per-tenant upgrades, multi-region
- Wildcard DNS + certificate provisioning

---

## Components

### 1. `accounts.slug` (migration 043)

```sql
ALTER TABLE accounts ADD COLUMN slug TEXT;
```

Added nullable, with a separate step to backfill and then constrain:

1. `ADD COLUMN slug TEXT` — nullable, so the migration cannot fail on existing rows
2. backfill each existing account with a generated slug from its name
3. `CREATE UNIQUE INDEX ... ON accounts(slug)`
4. new accounts require a slug at the application layer

**Why staged:** a single `ADD COLUMN ... NOT NULL UNIQUE` fails if any row has a
null slug, and this codebase has accounts created by the `handle_new_user`
trigger. Staging keeps the migration re-runnable, matching the convention used
by all 42 existing migrations ("Idempotent — safe to run multiple times").

### 2. `tenants` table (migration 043)

```sql
CREATE TABLE IF NOT EXISTS tenants (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug         TEXT NOT NULL UNIQUE,
  account_id   UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  owner_email  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'active',
  plan         TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Deliberately separate from `accounts`.** `accounts` holds a client's
business data; `tenants` is the operator's operational record — who owns this,
is it active, what plan. Upgrading a client and billing a client are different
jobs. Mixing them in one table makes both harder.

`slug` is duplicated across both tables on purpose: `tenants.slug` is the
operator's authoritative claim, `accounts.slug` is what RLS-adjacent code reads.
The proxy resolves against `tenants`; the application reads `accounts`. A test
asserts they agree.

### 3. `src/lib/tenant/slug.ts` — validation

```ts
validateSlug(input: string): { ok: true; slug: string } | { ok: false; error: string }
isReservedSlug(slug: string): boolean
```

Rules:
- lowercase letters, digits, `-` only
- must start and end alphanumeric
- 3–63 characters
- no consecutive hyphens
- reserved: `www, api, admin, mail, app, crm, smtp, ftp, dev, staging, test`

Pure functions, no I/O.

### 4. `src/lib/tenant/resolve.ts` — hostname → slug

```ts
resolveTenantFromHost(host: string | null): string | null
```

Handles: bare hostname, `Host` with port (`localhost:3000`), IP addresses
(→ `null`), `www.` prefix (stripped), uppercase (lowercased).

**`LOCALHOST_TENANT` env var.** On localhost there is no subdomain to read, so
this names the single tenant. Defaults to `'default'`. Without it the proxy
cannot resolve anything locally, which would block the whole milestone.

### 5. `src/proxy.ts` — the entry point

Renames `src/middleware.ts` → `src/proxy.ts`, export `middleware` → `proxy`,
keeping the **Supabase session-refresh logic exactly as-is**. That logic
rotates refresh tokens and rewrites cookies; it is load-bearing and must not
change.

Tenant resolution is added *before* the existing Supabase client is created:

```
request → read hostname → resolveTenantFromHost() → set x-tenant-slug header
        → existing session-refresh code, unchanged
```

**The proxy never selects a database and never authorizes anything.** It
answers "which subdomain is this?" and nothing else. RLS continues to answer
"what may this user see?".

This is the load-bearing architectural boundary: if the resolved slug does not
match `profiles.account_id` for the session, the request still cannot read
another account's data, because `auth.uid() → profiles.account_id` is the only
path to data. A wrong slug degrades to "unknown tenant", never to "wrong
tenant's data".

---

## Data flow

```
GET crm.acme.com/dashboard
  → proxy: hostname → "acme" → header x-tenant-slug: acme
  → layout: display tenant name (from accounts where slug = 'acme')
  → Supabase query: RLS filters by profiles.account_id
  → rows, or none if the session's account ≠ "acme"
```

---

## Testing

| Test | Type | Asserts |
|---|---|---|
| `slug.test.ts` | unit | valid/invalid forms, reserved words, length bounds, normalization |
| `resolve.test.ts` | unit | ports, IPs, `www.`, case, null/empty, `LOCALHOST_TENANT` |
| `proxy-header.test.ts` | unit | header set correctly; unchanged when hostname unresolvable |
| **`tenant-isolation.test.ts`** | **integration** | **two accounts, same query, different rows** |
| `tenants-agreement.test.ts` | integration | `tenants.slug` matches `accounts.slug` for the tenant |

**The isolation test is the only one that proves multi-tenancy.** Everything
else could pass while tenant data leaks. It is not optional.

Existing suite must stay green: 1073/1073.

---

## Risks

| Risk | Mitigation |
|---|---|
| `proxy.ts` rename breaks routing | The rename is mechanical; the full suite plus a manual login/dashboard check gate it |
| Session refresh silently breaks | Logic is moved verbatim; a login flow is verified manually before merge |
| A new authorization path gets introduced | The proxy is explicitly forbidden from touching data; only the isolation test guards this, and it is the reason that test is mandatory |
| Slug collision at signup | Reserved list + unique index; collision is an explicit error, never a silent overwrite |
| `uuid_generate_v4()` on hosted Supabase | Known issue, already fixed in a stashed edit (`001`). New migrations follow the same pattern; hosted deploy is a later milestone |

---

## Out of scope, restated

Billing, SSO, custom branding, the control panel, backups, multi-region, and
DNS provisioning are **not part of this milestone and not success conditions**.
Provisioning happens via SQL or psql until a panel exists.

---

## Definition of done

1. `feat/multi-tenant-foundation` on the fork, migration 043 applied locally
2. `src/proxy.ts` replaces `src/middleware.ts`; login, dashboard, inbox, and
   contacts all still work in a real browser
3. Two accounts cannot read each other's data — proven by the integration test
4. A request with a mismatched slug returns no data rather than wrong data
5. Existing suite green (1073/1073), `tsc --noEmit` and ESLint clean
6. No second authorization path introduced