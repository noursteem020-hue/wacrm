## Summary

Adds a thin multi-tenant foundation on top of the existing `accounts` model. Each
tenant is an `accounts` row with a `slug` used as its subdomain label; requests are
attributed to a tenant at the proxy boundary, while access control stays entirely
in RLS policies keyed on `auth.uid()`.

### Tasks 1–3

- **`src/lib/tenant/slug.ts`** — slug validation with a reserved-word list.
- **`src/lib/tenant/resolve.ts`** — resolves a tenant slug from the request
  hostname (second-to-last label). Known limitation, documented in
  `docs/tenancy.md`: without a public-suffix list, `crm.acme.co.uk` resolves to
  `co`. Add a PSL dependency before serving any two-label TLD.
- **`supabase/migrations/043_tenant_foundation.sql`** — adds `accounts.slug` and a
  `tenants` table, with an idempotent backfill. The first attempt assigned
  `ROW_NUMBER()::text` for every row, which collapsed to `"1"` and failed the
  unique index; the fix allocates the lowest free `name-N` suffix. Known limit:
  64 same-basename variants per basename, so a 65th needs an explicit slug.

### Task 4 — proxy boundary

- `src/middleware.ts` → `src/proxy.ts` (Next 16 `proxy` convention), plus the
  matching test-file rename and its top-level import update.
- `src/lib/tenant/header.ts` sets or **deletes** `x-tenant-slug`. On a host the
  resolver cannot map it deletes the header entirely — including any
  client-supplied value — rather than falling back to a default tenant.
- Session refresh preserved: the `setAll` rebuild re-snapshots the request
  headers *after* the cookie write, so it forwards the rotated cookie instead of
  the stale pre-rotation snapshot (issue #288).

### Tasks 5–6

- `src/lib/tenant/isolation.sql` — policy-text verification, run against real
  Postgres. Deliberately not a vitest file: the repo's tests mock Supabase, so a
  mocked RLS assertion would be its own assertion.
- `docs/tenancy.md` — the model, manual provisioning, and what going live on
  subdomains requires. Every SQL statement in it was executed before committing.

### Housekeeping

- Stopped tracking `supabase/.temp/cli-latest` (a local CLI version stamp) and
  added a per-file ignore for it.

## Validation

Measured at **`9e1773c`** (the branch tip at push time), not inherited from an
earlier run:

```
npm test                → 94 test files passed / 1104 tests passed
npx tsc --noEmit        → no output
npx eslint src          → 0 errors, 41 warnings (pre-existing baseline)
```

## Task 5, step 3 — UNVERIFIED

**This is unresolved and should not be read as a proven defect.**

Steps 1–2 read policy *text*: every data table's SELECT policy predicates on
`is_account_member(account_id)`, and that function resolves the caller through
`auth.uid()`. Both stop-the-line assertions passed.

Step 3 — proving a signed-in user of tenant A cannot read tenant B's rows — was
attempted and deliberately stopped. The fixture path required inserting into
`auth.users` directly, and GoTrue `v2.197.0` then refused to authenticate the row
(`converting NULL to string is unsupported` on `confirmation_token`).

That error does **not** establish that setting the column to `''` is the fix. The
default is inconsistent across the token columns (`email_change_token_current` and
`phone_change_token` default to `''`; `confirmation_token` and `recovery_token`
default to `NULL`), there is no `auth.create_user` function, and no admin API is
configured — so a direct insert is a bypass of provisioning rather than a
supported path. All fixture rows were deleted and the database returned to its
recorded baseline (`2 / 2 / 2 / 1 / 2 / 10`; zero leftovers).

**To close it:** create both tenants through the Supabase admin API or the
dashboard UI, then read `/rest/v1/contacts` as each tenant's own owner and confirm
the result sets differ.

## Reviewer decision needed

**Step 3 is deferred explicitly, by owner decision — not closed, and not recorded
as a proven defect.** It is neither an accomplishment nor a confirmed
vulnerability: what is unverified is whether runtime isolation holds, because the
behavioural check was never executed to completion.

Setting up an Admin API just to close this step is deliberately **out of scope for
this branch**; widening it to provision test tenants is not part of the tenancy
foundation.

**Reviewer action:** this PR can be reviewed and approved as-is with step 3
carried as deferred verification. If a supported provisioning path is later judged
a prerequisite for enabling or merging multi-tenancy, that becomes a **separate
tracked piece of work** rather than a condition silently folded into this review.

Nothing in this PR should be read as "complete" in a way that quietly absorbs
that gap.

## Out of scope

- **`deals = 0` is deliberately unchanged.** No `deal` row was created, recreated
  or guessed.
- **Signup provisioning is unsolved.** A user who signs up gets an account with no
  slug; migration 043 only backfilled accounts that existed when it ran. Every new
  tenant needs manual provisioning until that is built.
- The "no slug" signal is deferred, and the default is to show it to nobody. An
  owner-only variant would require a new RLS policy on `tenants` plus an isolation
  test — hiding it in the UI would not be access control, since any member can
  read `profiles.account_role` and call PostgREST directly.

## Review note

Task 4 went through thirteen review rounds against this branch. Two plan defects
worth knowing about, both fixed in the tracked plan: a cookie-rotation recipe that
would have reintroduced #288, and an RLS catalogue query that counted INSERT
policies as a false bypass because it filtered on `qual IS NULL` without
`cmd = 'SELECT'`.
