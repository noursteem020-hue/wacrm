# Tenant model

> **Status of the isolation claim in this document.** The RLS policies on the four
> tables named below were read and confirmed to predicate access on `auth.uid()`,
> and the proxy's role was confirmed to be attribution only. **This covers 4 of
> the 27 tables that carry `account_id` — not all of them.** A behavioural check
> with real users is still `UNVERIFIED` — see
> [Scope of this evidence](#scope-of-this-evidence--read-before-drawing-conclusions)
> and [Isolation verification](#isolation-verification). Nothing in this document
> should be read as proof that isolation holds at runtime; it documents the
> *mechanism*, names how much of it was checked, and names the proof that is
> still owed.

## The model, in one paragraph

`accounts` is a tenant. `accounts.slug` is its subdomain label. `tenants` is the
operator's record of who owns which account. RLS policies predicate access on
`auth.uid()`, resolving the caller through `profiles.user_id`; the proxy only
attributes a request to a tenant and never decides access. Reading the policy
text does not by itself prove isolation at runtime — behavioural verification
with real users remains `UNVERIFIED` (Task 5, step 3).

## Provisioning a client by hand

Run this in the Supabase SQL Editor. **Which statement you use depends on
whether the user already has an account** — and the trigger decides that, not
you.

Every user who signed up through the app already has one, created by the
`handle_new_user` trigger on `auth.users`. That account has **no slug**, and
assigning one is the normal case:

```sql
UPDATE accounts SET slug = 'acme'
WHERE owner_user_id = '<auth-user-uuid>' AND slug IS NULL;
```

The `slug IS NULL` guard is load-bearing. Without it the statement silently
**overwrites the slug of an existing tenant** — verified by execution: dropping
the guard rewrote `hermes-probe` in place. Match 0 rows on a user who already
has a slug means there is nothing to do, not that something went wrong.

Only for a user with **no** account does the insert apply, and it must name a
real `auth.users` id — `accounts.owner_user_id` is a foreign key, so a
made-up uuid fails:

```sql
INSERT INTO accounts (name, slug, owner_user_id)
VALUES ('Acme Trading', 'acme', '<auth-user-uuid-from-auth-users>');
INSERT INTO tenants (slug, account_id, owner_email)
VALUES ('acme', (SELECT id FROM accounts WHERE slug = 'acme'), 'owner@acme.com');
```

Both statements above were executed against local Postgres on 2026-10-03 inside
transactions that were rolled back. The insert path required dropping
`idx_accounts_one_per_owner` for the probe only, because both existing owners
already hold an account; in real provisioning the owner genuinely has none.

**`idx_accounts_one_per_owner` is a plain index, not a constraint.** It is
created by `CREATE UNIQUE INDEX`, so `ALTER TABLE ... DROP CONSTRAINT` fails
with *"constraint does not exist"* — verified by execution. To remove it for a
probe, the statement is:

```sql
DROP INDEX idx_accounts_one_per_owner;
```

**The collision case is real and must not be worked around.** Two tenants cannot
share a subdomain label; the second insert fails on `accounts_slug_key`. Verified
by execution — two accounts, two distinct owners, the same slug:

```
ERROR:  duplicate key value violates unique constraint "accounts_slug_key"
DETAIL:  Key (slug)=(acme) already exists.
```

That is the intended behaviour. Resolve it by choosing a different slug, not by
dropping or deferring the index. The same constraint bounds the migration's
auto-assigned slugs to the 64 `name-N` variants per basename, so a 65th
same-named account needs an explicit slug.

## What is deliberately not here

No control panel. No billing. No automated backups. Provisioning is manual until
somebody builds those, and this document is the whole of the operator's
procedure.

## Going live on subdomains

What changes is entirely outside the codebase:

1. A base domain you control.
2. A wildcard `*.domain` DNS record pointing at the app.
3. A wildcard TLS certificate covering that domain.
4. `LOCALHOST_TENANT` removed from the environment.

**The resolver already handles this and no code changes are needed.**
`resolveTenantFromHost()` takes the second-to-last label of the hostname, so
`crm.acme.com` and `acme.com` both resolve to `acme`. A raw IP, or any host it
cannot map, resolves to `null` — and the proxy then fails closed, forwarding no
tenant header at all rather than guessing one.

One known limitation, documented rather than fixed: the resolver splits on dots
without a public-suffix list, so `crm.acme.co.uk` resolves to `co` rather than
`acme`. Add a PSL dependency before serving any two-label TLD.

## Scope of this evidence — read before drawing conclusions

The table below is **not** a statement that account isolation is verified. It
records exactly how much was checked.

**Checked: the four tables named in the table** — `contacts`, `deals`,
`conversations`, `automations`.

**Not checked: the other tables that carry `account_id`.** A census found 27 such
tables. The queries above cover 4 of them, so nothing here should be read as
having verified the remaining 23. The census also surfaced two tables whose
policies differ from the `is_account_member` model, and both differences are
legitimate rather than defects:

| Table | Model | Why it is not a defect |
|---|---|---|
| `notifications` | per-**user**: `auth.uid() = user_id` | The table is scoped per user, not per tenant. A different model, not a missing policy. |
| `automation_pending_executions` | RLS enabled, **zero policies** | With RLS on and no policy, Postgres denies everything. Protected by default, not exposed. |

`tenants` is likewise RLS-enabled with zero policies, which is why owner-only
visibility there would be a new policy rather than an existing one.

**This was a read of the schema. No request was made as a signed-in user**, so no
conclusion about runtime isolation is drawn from either table.

## Isolation verification

`src/lib/tenant/isolation.sql` is the script. It is deliberately **not** a vitest
file: the repo's tests mock Supabase, and a mocked RLS assertion proves nothing,
because the mock would *be* the assertion. It runs against the real local
database.

**Executed against local Postgres on 2026-10-03.** Results:

| # | Assertion | Tables | Result |
|---|---|---|---|
| 1 | tenant accounts exist and are distinct | `accounts` | `hermes-probe`, `nour-abbass` — 2 rows |
| 2 | SELECT policies predicate on `is_account_member(account_id)` | 4 named tables | confirmed on all four |
| 3 | `is_account_member` resolves the caller through `auth.uid()` | function | `uses_auth_uid = t` |
| 4 | no named table bypasses `is_account_member` on read | 4 named tables | `0` |

Assertions 3 and 4 are stop-the-line gates. Both passed. **Assertion 4's `0` is
scoped to those four tables** — it is not a statement about the other 23.

**What this does not establish.** Assertions 1–4 read policy *text*. They
confirm the mechanism exists and is wired to `auth.uid()`. They do not
demonstrate that a signed-in user of tenant A is refused tenant B's rows — that
requires two real tenants with real rows, each read back through PostgREST under
that tenant's own JWT. That check is **step 3 of Task 5 and is `UNVERIFIED`**.

It was attempted and deliberately stopped. The fixture path required inserting
users into `auth.users` directly, and GoTrue (`v2.197.0`) then failed to
authenticate them — `converting NULL to string is unsupported` on
`confirmation_token`. That error shows only what GoTrue could not read; it does
**not** establish that setting the column to an empty string is the fix, and the
default is inconsistent across the token columns (`email_change_token_current`
and `phone_change_token` default to `''`, `confirmation_token` and
`recovery_token` default to `NULL`). There is no `auth.create_user` function and
no admin API configured, so a direct insert is a bypass of provisioning rather
than a supported path. The fixtures were deleted and the database returned to
its recorded baseline (`2 / 2 / 2 / 1 / 2 / 10` across users, accounts,
profiles, contacts, identities, stages; zero leftover probe rows).

**To close it:** create both tenants through a supported path — the Supabase
admin API or the dashboard UI — then read `/rest/v1/contacts` as each tenant's
owner and confirm the result sets differ. Do not reproduce it by inserting into
`auth.users`.

## Signup provisioning is not solved

A user who signs up through the app gets an account from the `handle_new_user`
trigger, and that account has **no slug**. The slug backfill in migration 043
only assigns slugs to accounts that existed when it ran; it does not provision
for future signups. Until that is built, every new tenant needs the manual step
above, and the proxy will resolve that user to no tenant.