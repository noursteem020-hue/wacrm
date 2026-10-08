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

Seventeen files change.

```
git diff --name-only 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d | wc -l -> 17
```

```
git diff --stat 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d | tail -1 -> 17 files changed, 2167 insertions(+), 151 deletions(-)
```

`src/middleware.ts` becomes `src/proxy.ts`:

```
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/middleware.ts && echo present -> present
git cat-file -e 9e1773c709dc01cc209612b403ed0c90d741798d:src/middleware.ts; echo "rc=$?" -> rc=128
```

The full list is in `docs/evidence/pr1/pr1-filelist.txt`, one path per line with
its own header. It is not inlined here because the gate compares
whitespace-separated tokens and a path list contains slashes.

## Tests this PR adds, and the break-test that does not exist

Four test files are added, and one is rewritten:

```
git diff --name-status 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d -- 'src/**/*.test.ts' -> M src/lib/currency.test.ts A src/lib/tenant/proxy-header.test.ts A src/lib/tenant/resolve.test.ts A src/lib/tenant/slug.test.ts
```

```
npx vitest run src/lib/tenant/resolve.test.ts src/lib/tenant/slug.test.ts src/lib/tenant/proxy-header.test.ts 2>&1 | grep -E 'Test Files|Tests ' | paste -sd' ' -; echo -> Test Files 3 passed (3) Tests 27 passed (27)
```

```
npx vitest run src/lib/currency.test.ts 2>&1 | grep -E 'Test Files|Tests ' | paste -sd' ' -; echo -> Test Files 1 passed (1) Tests 10 passed (10)
```

**There is no behavioural break-test for this PR, and a reviewer should know
that before reading anything else.** A break-test proves coverage when the new
test fails on the old code and passes on the new. That cannot be built here,
because the code the new tests exercise does not exist at base:

```
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/tenant/resolve.ts; echo "rc=$?" -> rc=128
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/tenant/slug.ts; echo "rc=$?" -> rc=128
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/tenant/header.ts; echo "rc=$?" -> rc=128
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/proxy.ts; echo "rc=$?" -> rc=128
```

Every source file those tests import is absent at base, so head's test cannot be
run against base's tree at all — not "it passes", it cannot run.

The one rewritten test is `src/lib/currency.test.ts`, and the code it covers is
unchanged by this PR:

```
git diff --name-only 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d -- src/lib/currency.ts | wc -l -> 0
git rev-parse 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/currency.ts -> 471bd3f964cc6a4b00f5a4a8f814c1ec02656771
git rev-parse 9e1773c709dc01cc209612b403ed0c90d741798d:src/lib/currency.ts -> 471bd3f964cc6a4b00f5a4a8f814c1ec02656771
```

The blob is the same at both ends. So running head's test against base's copy of
`currency.ts` is a no-op, and it passes:

```
git checkout 45e80ad9e23b91f5c02ab9f935edbae67810e59d -- src/lib/currency.ts && npx vitest run src/lib/currency.test.ts 2>&1 | grep -E 'Test Files|Tests ' | paste -sd' ' -; echo -> Test Files 1 passed (1) Tests 10 passed (10)
```

That claim rewrites a tracked file; `git checkout -- .` afterwards leaves the
tree clean.

What the currency test change actually does is make an existing test portable
across locales, which is a property of the test, not of the product code. An
earlier draft of this description claimed it proved something about the
behaviour here. It does not, and the reason it appeared to is below.

## The locale measurement, and why the earlier draft was wrong about it

The rewritten assertions derive their expectations from `Intl` at the ambient
locale instead of hardcoding ASCII. On this machine the ambient locale is
`ar-SA`, and neither `LC_ALL` nor `LANG` changes it:

```
LC_ALL=de-DE.UTF-8 node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
LC_ALL=en-US.UTF-8 node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
LANG=de-DE.UTF-8 node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
```

A locale passed in code does take effect, which is the control that shows the
environment is not simply broken:

```
node -e "console.log(new Intl.NumberFormat('de-DE').format(1234.5))" -> 1.234,5
node -e "console.log(new Intl.NumberFormat('ar-SA').format(1234.5))" -> ١٬٢٣٤٫٥
```

So the four failures an earlier draft reported as "under de-DE" were ar-SA
failures: base's test asserts ASCII digits and separators, and the ambient locale
renders neither.

```
git show 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/currency.test.ts > src/lib/currency.test.ts && npx vitest run src/lib/currency.test.ts 2>&1 | grep -c 'AssertionError' -> 4
```

The claim rewrites a tracked file; restore with `git checkout -- .` before
reading the next claim. Full runs, each with its own header:
`docs/evidence/pr1/currency-basetest-on-head-code.txt` and
`docs/evidence/pr1/currency-headtest-on-head-code.txt`.

## Not measured

- **Behavioural coverage of the new capability.** Stated above and not measured:
  no test in this PR was shown to fail on base's code, because the code under
  test does not exist there. The 27 tenant tests pass at head and that is the
  whole of the behavioural evidence.
- **Coverage of the changed paths.** The three runs above are the ones I ran.
  I did not establish which changed source paths have no test touching them, and
  `src/proxy.ts` and `src/lib/tenant/header.ts` are in neither tenant run.
- **The RLS policies.** `src/lib/tenant/isolation.sql` and
  `supabase/migrations/043_tenant_foundation.sql` are both present at head. No
  migration was applied and no database was touched while writing this, so
  nothing here says whether either is correct.
- **CI.** No CI run is shown. Whether this branch passes on a clean checkout in
  CI is unmeasured.
- **Environments other than this one.** Windows, Node `v24.21.0`, system locale
  `ar-SA`. Whether the suite behaves differently under a POSIX host where
  `LC_ALL` reaches Node is unmeasured.
- **Whether head still merges cleanly.** `gh pr view 1` reported
  `mergeable=MERGEABLE` when this was written. That is the platform's cached
  answer, not a fresh trial merge.
