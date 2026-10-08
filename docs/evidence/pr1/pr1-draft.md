# feat/multi-tenant-foundation -> main

Tenant resolution, proxy header plumbing and the RLS foundation for multi-tenant
deals. Every measurement below is written as a command and its result so that
`tools/verify-pr-body.py` can re-derive it. The SHAs come first, because
everything else is measured against them.

## Reference

```
gh pr view 1 --repo noursteem020-hue/wacrm --json headRefOid --jq .headRefOid -> 9e1773c709dc01cc209612b403ed0c90d741798d
gh pr view 1 --repo noursteem020-hue/wacrm --json baseRefOid --jq .baseRefOid -> 45e80ad9e23b91f5c02ab9f935edbae67810e59d
```

```
head-sha = 9e1773c709dc01cc209612b403ed0c90d741798d
base-sha = 45e80ad9e23b91f5c02ab9f935edbae67810e59d
```

```
git merge-base 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d -> 45e80ad9e23b91f5c02ab9f935edbae67810e59d
```

The merge-base is the base oid itself. Every other measurement below is taken at
head-sha, or across base-sha to head-sha.

## The change

Seventeen files change. The count, and the three files that matter most for the
behaviour below:

```
git diff --name-only 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d | wc -l -> 17
```

```
git diff --stat 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d | tail -1 -> 17 files changed, 2167 insertions(+), 151 deletions(-)
```

```
git cat-file -e 9e1773c709dc01cc209612b403ed0c90d741798d:src/lib/tenant/isolation.sql && echo present -> present
git cat-file -e 9e1773c709dc01cc209612b403ed0c90d741798d:supabase/migrations/043_tenant_foundation.sql && echo present -> present
git cat-file -e 9e1773c709dc01cc209612b403ed0c90d741798d:src/proxy.ts && echo present -> present
```

`src/middleware.ts` becomes `src/proxy.ts`:

```
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/middleware.ts && echo present -> present
git cat-file -e 9e1773c709dc01cc209612b403ed0c90d741798d:src/middleware.ts; echo "rc=$?" -> rc=128
```

The full list is in `docs/evidence/pr1/pr1-filelist.txt`, one path per line with
its own header. It is not inlined here because the gate compares whitespace-
separated tokens and a path list contains slashes.

## Behaviour: the locale-dependent currency assertions

This is the break-test. `src/lib/currency.test.ts` exists at both ends and this
PR rewrites its assertions. Under a non-C locale, base's assertions fail against
this code and head's pass.

Base's copy of the file, at head-sha. **This claim rewrites a tracked file in
the working tree** — it is here for the record and the gate re-runs it, so run
`git checkout -- src/lib/currency.test.ts` after reading the result:

```
git show 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/currency.test.ts > src/lib/currency.test.ts && LC_ALL=de-DE.UTF-8 LANG=de-DE.UTF-8 npx vitest run src/lib/currency.test.ts 2>&1 | grep -E 'Test Files|Tests ' | tr -d '\n'; echo -> Test Files 1 failed (1) Tests 4 failed | 6 passed (10)
```

Head's copy of the same file, same locale, same code. The claim restores the
tracked file first, so it measures head's test whichever order the claims are
re-run in:

```
git checkout -- src/lib/currency.test.ts && LC_ALL=de-DE.UTF-8 LANG=de-DE.UTF-8 npx vitest run src/lib/currency.test.ts 2>&1 | grep -E 'Test Files|Tests ' | tr -d '\n'; echo -> Test Files 1 passed (1) Tests 10 passed (10)
```

The old test hardcoded ASCII digits and separators. That fails under `de-DE`,
and it could pass for the wrong reason under `ar-SA`, where the decimal separator
is never `.`, so `not.toContain(".00")` is satisfied without the code being
right. Head derives its expectations from `Intl` at the ambient locale instead.

Full runs, each with its own header:
`docs/evidence/pr1/currency-breaktest-base-test.txt` and
`docs/evidence/pr1/currency-breaktest-head.txt`.

## Behaviour: the tenant tests this PR adds

These three files are added by this PR, which is why the run below cannot have
happened at base:

```
git diff --name-status 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d -- 'src/lib/tenant/*.test.ts' -> A src/lib/tenant/proxy-header.test.ts A src/lib/tenant/resolve.test.ts A src/lib/tenant/slug.test.ts
```

```
npx vitest run src/lib/tenant/resolve.test.ts src/lib/tenant/slug.test.ts src/lib/tenant/proxy-header.test.ts 2>&1 | grep -E 'Test Files|Tests ' | tr -d '\n'; echo -> Test Files 3 passed (3) Tests 27 passed (27)
```

Full run: `docs/evidence/pr1/tenant-tests-head.txt`.

## Not measured

- **Coverage of the changed paths.** The two runs above are the ones I ran. I
  did not establish which changed source paths have no test touching them, and
  `src/proxy.ts` and `src/lib/tenant/header.ts` appear in neither run.
- **The RLS policies.** `src/lib/tenant/isolation.sql` and
  `supabase/migrations/043_tenant_foundation.sql` are both present at head, as
  the two `cat-file` claims above show. No migration was applied and no database
  was touched while writing this, so nothing here says whether either is correct.
- **CI.** No CI run is shown. Whether this branch passes on a clean checkout in
  CI is unmeasured.
- **Environments other than this machine.** Windows, `core.autocrlf=true`, Node
  `v24.21.0`. The `de-DE` run relies on Node's full ICU; a small-icu build
  would answer differently.
- **Whether head still merges cleanly.** `gh pr view 1` reported
  `mergeable=MERGEABLE` when this was written. That is the platform's cached
  answer, not a fresh trial merge.
- **The reason `grep -c` with a carriage return returns a line count on some
  spellings and not others.** Not investigated; `tools/rls-mutation/ledger-lines.md`
  records the measurements and leaves the cause open.
