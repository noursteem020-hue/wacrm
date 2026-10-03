# Multi-tenant foundation — design spec

**Date:** 2026-10-01 · **Branch:** `feat/multi-tenant-foundation`
**Base:** `477d017` on `noursteem020-hue/wacrm` (fork of `ArnasDon/wacrm`)
**Status:** implemented; under evidence review on `docs/rls-evidence-boundaries`

> **Evidence conventions used below.** Every claim carries one of:
> **MEASURED** — an executed check is named (a test run, a `psql` catalog query, a
> `node` probe of the resolver). **READ** — read from source, schema, or vendor
> docs with the file named; it describes what the code or the policy text *says*,
> not what the system *does*. **INFERRED** — a conclusion drawn from READ
> evidence, explicitly not a measurement.
>
> No sentence here asserts runtime behaviour that no executed check covers. Where
> a section was written before the implementation it is retained as design
> rationale and marked where it now describes shipped state. Figures quoted as
> MEASURED were measured on the commit named beside them and drift as the tree
> moves.

---

## Purpose

Turn wacrm from a single-tenant CRM template into a system that can host
multiple client companies, each reached at its own subdomain, with one
operator (me) provisioning them.

Success means: a request to `crm.<client>.com` resolves to exactly that
client's data, and no request can ever reach another client's data.

**Status of that success condition: NOT YET DEMONSTRATED.** The mechanisms
below were read in source and schema; the behavioural two-account check that
would demonstrate the second half has not been run. See
[Testing, and the isolation check](#testing-and-the-isolation-check). This
spec therefore describes a design and its supporting mechanism, not a proof.

---

## What already exists (labelled MEASURED / READ — see the isolation status note)

wacrm is **already multi-tenant at the data layer** — structurally. The schema
carries a per-tenant column and RLS is switched on; this is the most important
finding of the design phase, because it substantially reduces the work. The
word to watch is *structurally*: the mechanism is in place, and whether it
isolates correctly at runtime is the open question recorded below.

| Existing asset | Detail | Evidence |
|---|---|---|
| `accounts` table | `id, name, owner_user_id, created_at, updated_at, default_currency, slug` | **MEASURED** — `information_schema.columns` on `public.accounts`, commit `ac8ce30`. Ordinal order as listed. `slug` is nullable and was added by migration 043 (see below); it was **not** part of the pre-existing table. |
| `account_id` column | present on **27 tables** (`contacts`, `conversations`, `deals`, `automations`, `broadcasts`, `flows`, `pipelines`, `message_templates`, `api_keys`, `ai_configs`, …) | **MEASURED** — `SELECT count(*) FROM information_schema.columns WHERE column_name='account_id' AND table_schema='public'` returned `27` on `ac8ce30`. |
| Row Level Security | RLS is enabled on all 27 of those tables | **MEASURED** — `pg_class.relrowsecurity` census restricted to tables carrying `account_id` returned `27` with RLS on, `0` with RLS off. |
| Row Level Security *behaviour* | **UNVERIFIED.** Policies were inspected; **no two-account runtime isolation check has been run.** See the status note below. | **READ** — `src/lib/tenant/isolation.sql` (catalog only). **Not** MEASURED. |
| Roles | `profiles.account_role` ∈ `owner / admin / agent / viewer` | **MEASURED** — `pg_enum` for `account_role_enum` returned exactly `owner/admin/agent/viewer`. |
| Team invites | `account_invitations` table exists | **MEASURED** — `to_regclass('public.account_invitations')` returned `account_invitations`. |

### Isolation status: what has and has not been shown

**An earlier revision of this document claimed isolation was "verified at runtime
with two accounts returning different rows for the same query." That claim was
wrong and is withdrawn.** No such two-account query has been executed. What
exists is `src/lib/tenant/isolation.sql`, which is **catalog-only**: all four of
its assertions read `pg_policies`, `pg_proc`, or `accounts` rows. It never sets a
JWT, never acts as a signed-in user, and never compares the rows two accounts
would see. Its own header states it is "not a vitest file," and its commit
(`254bf69`) message — "verify account isolation through real RLS policies" —
overstates what the script does.

What the script *does* show, by reading policy text (READ, scope-limited):

| # | What was read | Scope | Result |
|---|---|---|---|
| 1 | tenant accounts exist and are distinct | `accounts` | 2 rows: `hermes-probe`, `nour-abbass` |
| 2 | SELECT policies predicate on `is_account_member(account_id)` | 4 named tables only | confirmed on `contacts`, `deals`, `conversations`, `automations` |
| 3 | `is_account_member` resolves the caller via `auth.uid()` | the function body | `uses_auth_uid = t` |
| 4 | no named table has a SELECT policy bypassing `is_account_member` | 4 named tables only | `0` |

**Assertion 4's `0` covers 4 tables, not 27.** The remaining 23 were not
examined by that script. Two tables carrying `account_id` depart from the
`is_account_member` model: `notifications` is scoped per **user**
(`auth.uid() = user_id`), and `automation_pending_executions` has RLS enabled
with zero policies. **Whether either departure is intended is unsettled, and
neither this document nor `docs/tenancy.md` settles it.** READ of
`docs/tenancy.md` on 2026-10-03: it states that it "does not settle whether
either difference is intended," that for `notifications` the intended scope is
"a design judgement; nothing measured here settles it," and that for
`automation_pending_executions` the intendedness of its reachability is likewise
unsettled. It further records that no ADR, design note, or issue in this repo
accepts either deviation. So the two are **known departures whose intent is
unexplained** — not defects this document can call defects, and not decisions
anyone has recorded accepting. That question belongs to the owner. An earlier
revision of this paragraph said `docs/tenancy.md` "records both as deliberate
rather than defects"; that attribution is **withdrawn**, because the file says
the opposite. **MEASURED** — my own census confirmed 27 tables with
`account_id`; the count of *policies inspected* remains 4.

### "Zero policies" is not "not exposed"

The earlier wording in this section and in Components §2 described a
zero-policy, RLS-enabled table as closed by default. That is over-strong and is
withdrawn. Three statements replace it, and they are not equally supported:

1. **Ordinary PostgREST clients (`anon`, `authenticated`) are denied every row.**
   With RLS enabled and no policy, there is no `USING` clause to satisfy, so
   nothing passes. **INFERRED** — this follows from RLS semantics plus the
   migration text; it is not a measurement of either table, and no request has
   been made against them here.
2. **Server-side service-role paths bypass RLS entirely and therefore get no
   protection whatsoever from the zero policy count.** **READ** —
   `src/lib/automations/admin-client.ts` builds its client from
   `process.env.SUPABASE_SERVICE_ROLE_KEY` in `supabaseAdmin()`, and every read
   and write of `automation_pending_executions` in the tree goes through it.
   There are exactly four, named by their operation rather than by line number,
   because line numbers drift:

   | Operation | Site |
   |---|---|
   | `insert` — the `wait`-step suspension, inside `executeStepsFrom` | `src/lib/automations/engine.ts` |
   | `update` by row `id` — `markPending(id, status)` | `src/lib/automations/engine.ts` |
   | `select('*')` — due-row drain | `src/app/api/automations/cron/route.ts` |
   | `update({ status: 'running' })` by `id` + `status` — the claim | `src/app/api/automations/cron/route.ts` |

   To confirm the set is still exactly these four, run
   `grep -rn "automation_pending_executions" src/` and read the hits; the two
   in `engine.ts` and the two in the cron route are the table accesses, and
   the remaining hits are comments. The zero-policy state is therefore *not*
   what keeps that table private; it is not what keeps it anything.
3. **Safety of such a path depends on that path's own authentication.** Nothing
   about the policy set backs up a service-role route. A service-role route with
   no credential check is unauthenticated, and the table's RLS configuration is
   silent about it.

**The one service-role path that reaches this table does authenticate. 🟡 —
documentation, not a finding.** READ, `src/app/api/automations/cron/route.ts`
(74 lines, read in full on 2026-10-03). The credential check precedes every
database call — `supabaseAdmin()` is first constructed at line 33, after the
comparison returns:

```
19|  const expected = process.env.AUTOMATION_CRON_SECRET
20|  if (!expected) {
21|    return NextResponse.json({ error: 'cron not configured' }, { status: 503 })
22|  }
23|  const supplied = request.headers.get('x-cron-secret') ?? ''
24|  const suppliedBuf = Buffer.from(supplied)
25|  const expectedBuf = Buffer.from(expected)
26|  if (
27|    suppliedBuf.length !== expectedBuf.length ||
28|    !timingSafeEqual(suppliedBuf, expectedBuf)
29|  ) {
30|    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 })
31|  }
```

- **Before any database query: yes.** Lines 19–31 are the whole gate; the first
  Supabase call is line 34. No query, no `supabaseAdmin()` construction, no
  request-body read precedes it.
- **Which credential it checks:** the `x-cron-secret` request header against
  `AUTOMATION_CRON_SECRET` (lines 19, 23). It does **not** read a variable named
  `CRON_SECRET`, and it does **not** read the standard `Authorization` request
  header at all. A pinger must set the custom `x-cron-secret` header; sending a
  bearer token in `Authorization` would be ignored and the request rejected 401.
- **Timing-safe: partly.** Line 28 uses `node:crypto`'s `timingSafeEqual`
  (imported line 1), but line 27 short-circuits on a length comparison first,
  so the secret's length is disclosed and the byte comparison never runs on a
  length mismatch. The length guard is also load-bearing — `timingSafeEqual`
  throws on unequal lengths. This is the standard, defensible pattern; it is not
  a constant-time rejection of all inputs.
- **With the secret unset: it does not run.** Line 20 treats an unset *or empty*
  `AUTOMATION_CRON_SECRET` as unconfigured and returns **503**, so there is no
  code path in which the table is touched without a configured, matching secret.
  Fail-closed.
- **Row data returned to the caller: no.** The only success bodies are
  `{ processed: 0 }` (line 43) and `{ processed }` (line 73) — a count, no rows.
  Error bodies are `error.message` from Supabase (line 42) plus a status. The
  `select('*')` at line 36 stays server-side.
- **`account_id` is passed through, but the engine does not consume it.** Line 61
  forwards `account_id: row.account_id`. **READ**, `engine.ts:154-158`, says so
  in its own doc comment: *"Audit-only; the automation row carries account_id for
  tenancy"* and *"this field is just here to mirror the row shape and keep the
  cron's pass-through self-documenting."* A grep for `pending.account_id` across
  `engine.ts` returns nothing. Tenant scoping instead comes from re-reading the
  automation row at `engine.ts:168-172` and filtering every downstream query on
  `automation.account_id` (`engine.ts:83, 106, 124, 210, 311, 395, 520, 596, 799`).

  One consequence worth naming, **INFERRED**: that re-read filters on `id` alone
  (`engine.ts:168-172`), so nothing compares `pending.account_id` against
  `automation.account_id`. If those two ever disagreed on a pending row, the work
  would follow the *automation* row's account and the mismatch would pass
  silently. The `account_id` column is `NOT NULL` and is written from
  `automation.account_id` (`engine.ts:310-311`), so they agree on every row this
  code writes; nothing has checked that they agree on rows written elsewhere.

Per the classification rule: this path authenticates, so it is 🟡 documentation.
No 🔴 is recorded, because no service-role path to either table was found running
without a credential check.

**What would settle it.** A behavioural check that, for two accounts in the same
database, sets each session's JWT claims and runs the same `SELECT` as both,
asserting the row sets are disjoint and each is a subset of that account's own
rows. `src/lib/tenant/isolation.sql` is not that script — it reads catalog
metadata and cannot fail if a policy were subtly wrong in a way the text does
not reveal. Until that check is executed and passes, treat account isolation as
**unverified**, and treat every sentence downstream about isolation as a
statement about *mechanism* rather than *observed behaviour*.

**Therefore this project adds a control plane, not a tenancy system.** RLS
remains the single *enforcement* layer — that is a claim about design intent,
and it holds by construction here because nothing in this design adds a second
predicate on the data path. Whether that layer enforces isolation correctly at
runtime is the open question above, and it is not answered by this document.

---

## Decisions taken (and why)

| Decision | Rationale |
|---|---|
| Shared database + `account_id`, RLS isolation | Chosen before inspecting the code, then confirmed by reading the schema. **READ**, not a behavioural verification — see the isolation status note. |
| Subdomain per tenant (`crm.acme.com`) | Clean separation; requires wildcard DNS + cert, deferred. |
| `proxy.ts`, not `middleware.ts` | Next 16 documents `proxy.ts` as the current convention (**READ** — Next.js docs: "File-system conventions: proxy.js", present in this tree). Only one proxy file is supported per project. |
| Single proxy file, logic in modules | Same doc, line 37: "Break out proxy functionalities into separate `.ts` or `.js` files and import them into your main `proxy.ts` file." **READ** — the spec's earlier paraphrase dropped "or `.js`". |
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

Steps 1–3 are **shipped**, not planned. **MEASURED** on `ac8ce30` —
`accounts.slug` exists and is nullable, and `pg_indexes` returns
`CREATE UNIQUE INDEX accounts_slug_key ON public.accounts USING btree (slug)`.
Two rows currently carry slugs: `hermes-probe`, `nour-abbass`.

**Step 4 is not implemented.** READ — a repo-wide search for `accounts.slug`
outside `src/lib/tenant/` returns no application read and no write. Nothing in
the codebase currently requires a slug on account creation, and no trigger,
RPC, or route assigns one. `accounts.slug` is therefore nullable in practice, and
migration 043's own header says as much: "There is no provisioning path that
assigns a slug today."

**Why staged:** a single `ADD COLUMN ... NOT NULL UNIQUE` fails if any row has a
null slug, and this codebase has accounts created by the `handle_new_user`
trigger. Staging keeps the migration re-runnable.

**Correction to the migration-convention claim.** The earlier text said staging
"match[es] the convention used by all 42 existing migrations ('Idempotent —
safe to run multiple times')." **MEASURED** — 42 migrations precede 043, and
**38 of the 42** contain the word "idempotent"; the other four
(`021_account_default_currency.sql`, `027_notifications.sql`,
`032_fix_ai_knowledge_membership.sql`, `035_interactive_messages.sql`) do not.
The convention is real and dominant but not universal, so "all 42" is
withdrawn.

### 2. `tenants` table (migration 043) — shipped

```sql
-- as designed in this spec
CREATE TABLE IF NOT EXISTS tenants (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  slug         TEXT NOT NULL UNIQUE,
  ...
);

-- as actually shipped in supabase/migrations/043_tenant_foundation.sql
CREATE TABLE IF NOT EXISTS tenants (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  ...
);
CREATE INDEX IF NOT EXISTS tenants_account_id_idx ON tenants(account_id);
ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
```

**Correction: `uuid_generate_v4()` in the block above is not what shipped.**
READ — the migration uses `gen_random_uuid()`, with a comment explaining why:
`uuid_generate_v4()` lives in the `extensions` schema on hosted Supabase and
does not resolve without an explicit `search_path`. This also contradicts the
Risks table's own "stashed edit" row, which claims the fix was needed and
deferred; it was in fact applied in 043. **MEASURED** —
`information_schema.columns` for `public.tenants` reports
`column_default = gen_random_uuid()`.

**MEASURED — the shipped table matches the spec's intended shape**, in this
ordinal order: `id, slug, account_id, owner_email, status, plan, created_at,
updated_at`. Nullability matches too: `plan` is the only nullable column.
**MEASURED** — `tenants` has `relrowsecurity = t` and **0 policies**. The table
currently holds no rows (`SELECT` returns 0), so nothing has been provisioned
through it, and no `src/` code queries it at all (grep, 2026-10-03). What the
zero policy count does and does not establish is set out in
["Zero policies" is not "not exposed"](#zero-policies-is-not-not-exposed):
`anon`/`authenticated` clients get nothing from it, but a service-role client
bypasses RLS entirely, so this state confers no protection on any server-side
path that chooses to read the table — and whether such a path is safe depends
entirely on its own authentication, not on this policy set.

**Deliberately separate from `accounts`.** `accounts` holds a client's
business data; `tenants` is the operator's operational record — who owns this,
is it active, what plan. Upgrading a client and billing a client are different
jobs. Mixing them in one table makes both harder.

`slug` is duplicated across both tables on purpose: `tenants.slug` is the
operator's authoritative claim, `accounts.slug` is what a hostname resolves to.

**Correction: the sentence this replaces claimed "The proxy resolves against
`tenants`; the application reads `accounts`." Neither happens.** READ —
`src/proxy.ts` performs no database access at all; it calls
`resolveTenantFromHost()` and writes a header, with no Supabase query on that
path. And no application code reads `accounts.slug` either. The duplication
therefore has **no current reader**: it exists as schema, not as wiring.

**The test said to exist here does not exist.** This section previously stated
"A test asserts they agree," and the Testing table listed
`tenants-agreement.test.ts` as an integration test. There is no such file —
`src/lib/tenant/` contains `slug.ts`, `resolve.ts`, `header.ts`, `slug.test.ts`,
`resolve.test.ts`, `proxy-header.test.ts`, and `isolation.sql`. Nothing compares
`tenants.slug` against `accounts.slug`. The design intent stands; the assertion
is unbuilt and is carried in [Definition of done](#definition-of-done) as
outstanding rather than assumed present.

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

Handles: bare hostname, `Host` with port (`localhost:3000`), IPv4 literals
(→ `null`), uppercase (lowercased).

**Correction to "`www.` prefix (stripped)": there is no `www` branch in the
code.** READ — `resolve.ts:33-36` says so explicitly: "No `www` special-case is
needed or wanted: with three-or-more labels the second-to-last label is the
same whether or not `www` leads." `www.acme.com` → `acme` as a *consequence* of
taking the second-to-last label, not as a stripping step. The observable result
matches, so the Testing row asserting `www.` coverage is accurate as an
*outcome*; the mechanism description above was wrong and is corrected.

Also note the function signature here is abbreviated. The implementation is
`resolveTenantFromHost(host: string | null | undefined)` — it accepts
`undefined` as well, and returns `null` for it. **READ**, via the test that
pins it: the case titled *`"returns null for null, undefined and empty input"`*
in `src/lib/tenant/resolve.test.ts`, which asserts
`resolveTenantFromHost(undefined)` is null. Cite it by that title, not by line
number — the file is being edited concurrently in this round, so a line range
here would not hold. To find it yourself:
`grep -n "null, undefined and empty input" src/lib/tenant/resolve.test.ts`.

**`LOCALHOST_TENANT` env var.** On localhost there is no subdomain to read, so
this names the single tenant.

**There is no code-level default. When the variable is unset the resolver
returns `null`.** READ — `src/lib/tenant/resolve.ts:28` reads
`return process.env.LOCALHOST_TENANT || null;`, with no literal fallback
anywhere in the file. The earlier wording here said the variable "defaults to
`'default'`"; no such default exists and the claim is withdrawn. The plan at
`docs/superpowers/plans/2026-10-01-multi-tenant-foundation.md` already records
the same withdrawal.

**Consequence of it being unset** — MEASURED, by executing the resolver's own
algorithm under `node` against a `LOCALHOST_TENANT`-free environment:

| Host | `LOCALHOST_TENANT` unset | `LOCALHOST_TENANT=nour-abbass` |
|---|---|---|
| `localhost:3000` | `null` | `nour-abbass` |
| `acme.com` | `null` | `nour-abbass` |
| `crm.acme.com` | `acme` | `acme` |
| `www.acme.com` | `acme` | `acme` |
| `CRM.Acme.COM` | `acme` | `acme` |
| `127.0.0.1:3000` | `null` | `null` |
| `crm.acme.co.uk` | `co` | `co` |

Three things follow, and they are properties of the code path rather than
guesses:

1. **A bare two-label host also consumes the variable**, not just `localhost` —
   `acme.com` returns `nour-abbass` when it is set. Any host with fewer than
   three labels takes the same branch (READ — `resolve.ts:27-29`).
2. **Unset means "no tenant", not "tenant named `default`".** With it unset,
   `withTenantHeader()` (`src/lib/tenant/header.ts:13-14`) *deletes*
   `x-tenant-slug` rather than setting a placeholder, so downstream sees no
   header at all.
3. **`127.0.0.1` ignores the variable entirely** — the IPv4 check at
   `resolve.ts:24` returns `null` before the variable is consulted.

**Known limitation, unresolved — and the shape that matters is the three-label
one, not the `.co.uk` one.** The resolver splits on dots with no public-suffix
list and returns the second-to-last label, so a multi-part suffix produces a
label that is a *public-suffix fragment* rather than a tenant slug. What that
label then does to `validateSlug` splits the cases in two, and the split is
worth stating precisely because the earlier text here got it backwards:

- **`crm.acme.co.uk` → `co`, which does NOT pass `validateSlug`.** MEASURED
  by importing both real functions and running them together:

  ```
  crm.acme.co.uk   -> co     validateSlug: {"ok":false,"error":"Slug must be 3-63 characters"}
  crm.acme.com.au  -> com    validateSlug: {"ok":true,"slug":"com"}
  ```

  `co` is two characters and `slug.ts` rejects anything under three, so it is
  rejected as *too short*. The resolver still returns it — resolution is
  deliberately independent of validation (READ, `resolve.ts:3-6`: "resolution
  says *which* label a host claims, validation decides whether that label is a
  legal tenant"), so nothing on the hostname path ever calls `validateSlug`.
- **`crm.acme.com.au` → `com`, which DOES pass `validateSlug`.** MEASURED, same
  run. `com` is three characters, alphanumeric at both ends, and not in
  `RESERVED_SLUGS`. **This is the dangerous shape**: it is a well-formed slug
  that passes validation and could name a real account, so it fails silently
  rather than obviously. `resolve.ts:38-42` names exactly this case in its own
  comment — *"`crm.acme.com.au` -> `com`, which passes validateSlug and would
  route to the wrong tenant"* — and that comment is correct where this
  paragraph was not.

So the risk in the Risks table below is real but its worst form is
`.com.au`, not `.co.uk`. The earlier text here said `crm.acme.co.uk` resolves to
"a slug that passes `validateSlug`"; that is **withdrawn as wrong** — `co`
fails on length. Fix before wildcard DNS goes live: add a public-suffix list.

**How localhost gets a tenant in practice:** set `LOCALHOST_TENANT` in
`.env.local` (gitignored) to the slug local requests should resolve to. With it
unset, localhost development proceeds without a tenant header; nothing in the
milestone is blocked, contrary to the earlier claim here that it "would block
the whole milestone."

### 5. `src/proxy.ts` — the entry point

Renames `src/middleware.ts` → `src/proxy.ts`, export `middleware` → `proxy`.

**Correction: the session-refresh logic was not moved "exactly as-is".** READ —
`git diff 786a6fd^:src/middleware.ts 786a6fd:src/proxy.ts` shows a 97 → 119
line change. The `withRefreshedCookies()` helper and its issue-#288 rationale
are unchanged, but the `setAll()` cookie callback **was edited**: the original
`NextResponse.next({ request })` became
`NextResponse.next({ request: { headers: refreshed } })` with a re-snapshot of
headers after the cookie writes, because the top-of-function
`requestHeaders` copy is stale by then. The stated intent — that the rotated
refresh cookie must not be lost, and the tenant header must survive alongside
it — is preserved, but "verbatim" is withdrawn; it was a deliberate edit to the
cookie callback.

Tenant resolution is added *before* the existing Supabase client is created:

```
request → read hostname → resolveTenantFromHost() → set x-tenant-slug header
        → existing session-refresh code, unchanged
```

**The proxy never selects a database and never authorizes anything.** READ —
`src/proxy.ts:10-14` is the whole tenant path: it calls
`resolveTenantFromHost(request.headers.get('host'))` and passes the result to
`withTenantHeader()`. There is no Supabase query, no `.select()`, and no
`service_role` client on that path. It answers "which subdomain is this?" and
nothing else.

**The proxy overwrites rather than trusts the client's header.** READ —
`header.ts:9-14` calls `headers.set()` when a slug is present and
`headers.delete()` when it is `null`, so an inbound `x-tenant-slug` supplied by
a client cannot survive. `src/proxy.test.ts:174-182` asserts this against an
`ATTACKER` value, so here there *is* an executed check — and it is a unit test
over a mocked request, not a live request.

This is the load-bearing architectural boundary, and it is worth being precise
about what it does and does not claim. There are two independent questions:

1. **Attribution** — the proxy maps a hostname to a slug. It performs no lookup,
   so a wrong or unknown hostname cannot by itself select another tenant's rows.
2. **Isolation** — RLS decides what the session may read, and it does so from
   `auth.uid() → profiles.user_id → profiles.account_id`. The resolved slug is
   not consulted on that path at all.

So a mismatched slug **cannot reach another account's data** by way of the proxy,
because the slug is never consulted on the path RLS uses. That is the security
property that matters, and it follows from the policy text rather than from a
measurement.

**What a mismatched slug does to the data the session can read is not
established here.** Reading the policy and the helper's definition suggests the
request stays scoped to the account the session already belongs to, since
`is_account_member()` resolves the caller's own `account_id` from `auth.uid()`
and grants on that basis — so it would produce the session's own account rather
than an empty result. **That is a bounded inference from the policy text, not
verified system behaviour**, and it is part of what the outstanding behavioural
check would settle (see [Testing, and the isolation
check](#testing-and-the-isolation-check)). Earlier wording in this spec stated
it more strongly and has been corrected.

---

## Data flow

```
GET crm.acme.com/dashboard
  → proxy: hostname → "acme" → header x-tenant-slug: acme
  → layout: display tenant name (from accounts where slug = 'acme')   ← NOT IMPLEMENTED
  → Supabase query: RLS filters by profiles.account_id
  → rows, or none if the session's account ≠ "acme"                   ← NOT DEMONSTRATED
```

**Two lines above describe intended behaviour, not shipped behaviour.**

- Line 3 is **not implemented**. READ — no application code queries `accounts`
  by `slug`; a repo-wide search finds no `accounts.slug` read outside
  `src/lib/tenant/`. There is no tenant-name display wired up.
- Line 5's conditional is **INFERRED**, not observed. It follows from reading
  `is_account_member`'s body (`auth.uid()` → `profiles.account_id`), not from
  running two sessions. Whether a mismatched slug yields "no rows" or "the
  session's own rows" is exactly what the outstanding behavioural check would
  decide, and the section above already declines to claim either.

Lines 1, 2, and 4 describe code that exists — READ, with the caveat that line 4
describes the *policy's* predicate, not a confirmed outcome.

---

## Testing, and the isolation check

| Test | Type | Asserts | Status |
|---|---|---|---|
| `slug.test.ts` | unit | valid/invalid forms, reserved words, length bounds, normalization | **PRESENT**, green |
| `resolve.test.ts` | unit | ports, IPs, `www.`, case, null/empty, `LOCALHOST_TENANT` | **PRESENT**, green |
| `proxy-header.test.ts` | unit | header set correctly; deleted when hostname unresolvable | **PRESENT**, green |
| `src/proxy.test.ts` | unit | proxy overwrites a client-supplied `x-tenant-slug`; re-applies after cookie rotation | **PRESENT**, green |
| **`tenant-isolation.test.ts`** | **integration** | **two accounts, same query, different rows** | **DOES NOT EXIST** |
| `tenants-agreement.test.ts` | integration | `tenants.slug` matches `accounts.slug` for the tenant | **DOES NOT EXIST** |
| `src/lib/tenant/isolation.sql` | manual script | reads policy text from the catalog | **PRESENT** — catalog only, **not** a behavioural check |

**The row this table previously claimed to have does not exist.** Neither
`tenant-isolation.test.ts` nor `tenants-agreement.test.ts` is in the tree; see
Components §2. The design intent is unchanged and both remain required, but
they are carried below as outstanding rather than listed as if written.

The row `proxy-header.test.ts` previously described as asserting the header is
"unchanged when hostname unresolvable" is more precisely: the header is
**deleted**. That is a stronger property than "unchanged", and it is what
`header.ts:14` implements.

**The isolation test is the only one that would demonstrate multi-tenancy.**
Everything else could pass while tenant data leaks. It is not optional — and it
is also not built, which is why the success condition is marked NOT YET
DEMONSTRATED at the top of this document. The note in the "What already
exists" section describes what a behavioural version would have to do.

### Existing suite

**Do not read a test count here as a current fact. No figure is asserted as
current in this document, and none should be added.** A hardcoded figure in a
design document goes stale the moment tests are added, and the numbers
previously in this file had already done so:

- `1073` — the pre-milestone baseline, recorded in
  `.superpowers/sdd/2026-10-01-multi-tenant-foundation/task-1-report.md:47`
- `1086` — that baseline plus 13 new tests, recorded in
  `.superpowers/sdd/2026-10-01-multi-tenant-foundation/task-1-report.md:47`
  (and `progress.md` beside it)
- A larger four-figure run, **not restated here and not to be read as current.**
  The ledger records it at
  `.superpowers/sdd/2026-10-01-multi-tenant-foundation/progress.md:399` — *"Validation
  measured 2026-10-03 at `254bf69`"* — so it is **bound to commit `254bf69`**,
  which is an ancestor of the branch tip. The count is deliberately absent from
  this document: the authoritative place for a figure is the ledger it was
  recorded in, and this section's job is to send you there rather than to
  keep a copy that drifts. **MEASURED** here only as to *drift*: running
  `npx vitest run` on the current working tree during this edit exits 0 and
  reports **more** files and a **larger** total than the ledger's run did,
  because a new test file
  (`src/lib/tenant/resolve.known-defects.test.ts`) and a strengthened
  `resolve.test.ts` case have landed since. No number from that run is written
  here; the next one belongs in `progress.md` alongside its commit.

**Where the authoritative figures are recorded, and what counts.** Three
places, in this order of authority:

1. **The output of `npx vitest run` on the working tree — authoritative for
   "how many tests pass right now."** No document can be. Run it, and record
   the result together with the commit it was taken at; a count without its
   commit is not evidence.
2. **`.superpowers/sdd/2026-10-01-multi-tenant-foundation/progress.md` and
   `task-1-report.md` — authoritative for what was measured *at a named
   commit* during the milestone.** They are where the historical figures above
   came from and where a new figure belongs.
3. **This section — not authoritative for any count.** It exists to say the
   number is not to be read here.

**These figures are already known to be in motion.** A separate agent is fixing
a vacuous test in `src/lib/tenant/resolve.test.ts` by stubbing
`LOCALHOST_TENANT` inside the test — **not** by editing this document, and that
file is being changed concurrently, so cite it by test *title*, not by line
number. The test in question is *"returns null for localhost when
LOCALHOST_TENANT is unset"*.

**READ**, and offered only as context for why the count is moving — this
document makes no claim about the other agent's fix, which may land after this
commit. As read on 2026-10-03: the file-level `beforeEach` deleted
`LOCALHOST_TENANT` for every case, so the "unset" assertion could not
distinguish an unset variable from an absent one and stayed green under a
mutation of `resolve.ts:28` from `process.env.LOCALHOST_TENANT || null` to a
bare `return null;`. `vitest.config.ts:14-18` injects only `ENCRYPTION_KEY` and
`META_APP_SECRET`, so `LOCALHOST_TENANT` is genuinely absent in the test
environment. The in-flight fix makes the precondition explicit — it stubs and
deletes the variable inside the test and asserts the precondition before the
call — and restores the environment via `vi.unstubAllEnvs()`.

Whether that fix adds, removes, or merely strengthens cases is **not stated
here and is not predicted here.** Do not write a resulting number into this
section, and do not "correct" the ledger's figure against a future run. When the
suite is next run, the result goes to `progress.md` with its commit. Anyone
reading this document needs to run `npx vitest run` to learn the current figure.
The historical numbers are not mutually contradictory — they were measured at
different commits — but quoting any one of them as "the suite size" is exactly
the error this section exists to prevent.

---

## Risks

| Risk | Mitigation |
|---|---|
| `proxy.ts` rename breaks routing | **DONE** — READ: `src/middleware.ts` no longer exists and `src/proxy.ts` does. **MEASURED**: `npx vitest run` green on `ac8ce30`, including `src/proxy.test.ts`. The *manual* browser check (login, dashboard, inbox, contacts) has **not** been recorded, so routing is verified by test and not by hand. |
| Session refresh silently breaks | The issue-#288 cookie-preservation logic is present in `src/proxy.ts:50-65` (READ). Verified by `src/proxy.test.ts`; a manual login flow is still owed before merge. |
| A new authorization path gets introduced | The proxy is explicitly forbidden from touching data — and READ confirms it does not (no Supabase query on the tenant path). Note the mitigation is weaker than stated: the isolation test that would guard this **does not exist yet**, so today this risk is guarded only by code review. |
| Slug collision at signup | Reserved list + unique index. MEASURED — `accounts_slug_key` exists, so a collision surfaces as a constraint error rather than a silent overwrite. No signup path exists to collide yet. |
| Multi-part public suffixes route to the wrong tenant | **The dangerous shape is `crm.acme.com.au` → `com`, not `crm.acme.co.uk` → `co`.** MEASURED by executing both real functions together: `com` is three characters and **passes** `validateSlug`, so it is a well-formed slug that could name a real account and fails silently; `co` is two characters and **fails** `validateSlug` on the 3-character minimum, so it is obvious rather than dangerous. Same mechanism either way — no public-suffix list — so the fix is the same and is owed before wildcard DNS. Not in the original risk table. See Components §4. |
| `uuid_generate_v4()` on hosted Supabase | **This row was stale.** It claimed the problem was "already fixed in a stashed edit (`001`)" and that new migrations follow that pattern. In fact migration 043 ships `gen_random_uuid()` directly, so the risk is already closed for this migration; the stashed `001-extension-search-path` edit is a separate, still-unapplied change. Hosted deploy remains a later milestone. |

---

## Out of scope, restated

Billing, SSO, custom branding, the control panel, backups, multi-region, and
DNS provisioning are **not part of this milestone and not success conditions**.
Provisioning happens via SQL or psql until a panel exists.

---

## Definition of done

Status against each item as of commit `ac8ce30`, branch
`docs/rls-evidence-boundaries`. "Outstanding" means not satisfied yet — it does
not mean satisfied by a passing test elsewhere.

1. ~~`feat/multi-tenant-foundation` on the fork, migration 043 applied locally~~
   — **MET.** MEASURED: `accounts.slug` and `tenants` both exist in the local
   database; the branch exists locally.
2. ~~`src/proxy.ts` replaces `src/middleware.ts`; login, dashboard, inbox, and
   contacts all still work in a real browser~~ — **PARTIAL.** The rename is
   done (READ) and the suite is green (MEASURED), but **no manual browser check
   is recorded**, so the second half of this item is outstanding.
3. ~~Two accounts cannot read each other's data — proven by the integration
   test~~ — **NOT MET.** The integration test does not exist and the behavioural
   check has not been run. This is the milestone's central success condition and
   it remains **unverified**. See Components "Isolation status".
4. ~~A request with a mismatched slug returns no data rather than wrong
   data~~ — **NOT MET, and the expectation itself is INFERRED.** Item 3 and this
   item also conflict: a mismatched slug plausibly returns the session's *own*
   rows, which is "no wrong data" but not "no data". The requirement should be
   restated as "never returns another account's rows", which is the property
   this design can actually support. Flagged for the owner.
5. ~~Existing suite green (1073/1073)~~ — **MET at a different number.**
   The suite is green, and it is green at a figure **other than** the one this
   item was written against. MEASURED: `npx vitest run` on the current working
   tree during this edit exits 0, and on commit `ac8ce30` — for which no suite
   run is recorded in this document — the ledger's figure is the one at
   `progress.md`, **bound to `254bf69` and not a current fact.** The number
   itself is deliberately not restated here; see
   [Existing suite](#existing-suite) for why no count is authoritative in this
   document and where the real ones live. The count is not part of the
   requirement: run the suite and record the figure with its commit.
   `tsc --noEmit` and ESLint were **not** run in this review and remain
   unverified.
6. ~~No second authorization path introduced~~ — **MET by inspection.** READ:
   `src/proxy.ts` performs no data access on the tenant path, and
   `header.ts` overwrites rather than trusts a client-supplied header. This is a
   statement about the code as written, not about what it will do under load or
   future edits.

**Net: items 3 and 4 are the ones that matter, and neither is satisfied.** The
design's mechanism is in place and its policy text reads correctly; nothing has
yet demonstrated that it holds for two accounts at runtime.
