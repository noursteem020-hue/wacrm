# feat/multi-tenant-foundation -> main

Tenant resolution, proxy header plumbing and the RLS foundation for multi-tenant
deals. Every measurement below is a command and its result, so it can be
re-derived:

```
python tools/verify-pr-body.py --pr 1 --file docs/evidence/pr1/pr1-draft.md
```

That runs on a published body only after this text is pushed to PR #1. Until
then it reads the file above, which is why the path is named here.

**Where the test numbers come from.** Every `npx vitest` claim below was run in a
throwaway worktree checked out at head-sha, not in the working tree of the docs
branch. An earlier draft ran them on the docs branch and reported them as
measurements of this PR; that was the same error as relabelling a suite count
from one commit to another.

Setup, run once before the gate, from the repository root:

```
git worktree add ../wt-pr1 9e1773c709dc01cc209612b403ed0c90d741798d
cd ../wt-pr1 && npm ci
```

The two preconditions below are claims in this description, so the gate fails
loudly if the toolchain is not what produced these numbers:

```
cd ../wt-pr1 && git rev-parse HEAD -> 9e1773c709dc01cc209612b403ed0c90d741798d
```

```
cd ../wt-pr1 && test -x node_modules/.bin/vitest; echo "rc=$?" -> rc=0
```

Every `npx` below is `npx --no-install`, so a missing vitest is an error
rather than a silent download.

Each claim below creates that worktree itself and leaves it for the next claim;
repository's own working tree is modified to produce any number here.

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
whitespace-separated tokens, and a path list contains slashes.

## Tests: three added, one rewritten

```
git diff --name-status 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d -- 'src/**/*.test.ts' -> M src/lib/currency.test.ts A src/lib/tenant/proxy-header.test.ts A src/lib/tenant/resolve.test.ts A src/lib/tenant/slug.test.ts
```

Three files are added and one is rewritten. Run at head-sha in the worktree:

```
cd ../wt-pr1 && npx --no-install vitest run src/lib/tenant/resolve.test.ts src/lib/tenant/slug.test.ts src/lib/tenant/proxy-header.test.ts 2>&1 | grep -E 'Test Files|Tests ' | paste -sd' ' -; echo -> Test Files 3 passed (3) Tests 27 passed (27)
```

```
cd ../wt-pr1 && npx --no-install vitest run src/lib/currency.test.ts 2>&1 | grep -E 'Test Files|Tests ' | paste -sd' ' -; echo -> Test Files 1 passed (1) Tests 10 passed (10)
```

## The tests can say no: a mutation, run and restored

A pass count is only worth reading if the test can fail. This one is checked by
breaking the code it covers and confirming the failure, in the worktree at
head-sha. `src/lib/tenant/resolve.ts:43` returns the second-to-last label of a
hostname; the mutation returns the last label instead, so `crm.acme.com` resolves
to `com` instead of `acme`:

```
cd ../wt-pr1 && sed -i 's/parts.length - 2/parts.length - 1/' src/lib/tenant/resolve.ts && npx --no-install vitest run src/lib/tenant/resolve.test.ts 2>&1 | grep -E 'AssertionError' | head -1 | paste -sd' ' -; echo -> AssertionError: expected 'com' to be 'acme' // Object.is equality
```

The failure count for the same mutation:

```
cd ../wt-pr1 && sed -i 's/parts.length - 2/parts.length - 1/' src/lib/tenant/resolve.ts && npx --no-install vitest run src/lib/tenant/resolve.test.ts 2>&1 | grep -E '^ +Tests ' | paste -sd' ' -; echo -> Tests 4 failed | 6 passed (10)
```

The unmutated control on the same tree:

```
cd ../wt-pr1 && git checkout -- src/lib/tenant/resolve.ts && npx --no-install vitest run src/lib/tenant/resolve.test.ts 2>&1 | grep -E 'Test Files|Tests ' | paste -sd' ' -; echo -> Test Files 1 passed (1) Tests 10 passed (10)
```

Restored, and the worktree is clean afterwards:

```
```

Both runs, each with its own header: `docs/evidence/pr1/mutation-resolve-head.txt`
(the mutation, exit 1) and `docs/evidence/pr1/mutation-resolve-control.txt` (the
control, exit 0). The test runs at head-sha are in
`docs/evidence/pr1/tests-at-headsha.txt`.

## Why there is no base-vs-head break-test

A break-test proves coverage when the new test fails on the old code. That cannot
be built here, because the code the new tests exercise does not exist at base:

```
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/tenant/resolve.ts; echo "rc=$?" -> rc=128
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/tenant/slug.ts; echo "rc=$?" -> rc=128
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/tenant/header.ts; echo "rc=$?" -> rc=128
git cat-file -e 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/proxy.ts; echo "rc=$?" -> rc=128
```

Head's test cannot run against base's tree at all. The mutation above is the
coverage evidence that replaces it.

## The rewritten currency test is a fix to the test, not the product

This PR does not touch `src/lib/currency.ts`, so there is no product behaviour
here to cover:

```
git diff --name-only 45e80ad9e23b91f5c02ab9f935edbae67810e59d 9e1773c709dc01cc209612b403ed0c90d741798d -- src/lib/currency.ts | wc -l -> 0
git rev-parse 45e80ad9e23b91f5c02ab9f935edbae67810e59d:src/lib/currency.ts -> 471bd3f964cc6a4b00f5a4a8f814c1ec02656771
git rev-parse 9e1773c709dc01cc209612b403ed0c90d741798d:src/lib/currency.ts -> 471bd3f964cc6a4b00f5a4a8f814c1ec02656771
```

The blob is identical at both ends. The old test asserted ASCII digits and
separators, which holds only under an en-US-ish locale; the rewrite derives its
expectations from `Intl` at the ambient locale, so the test stops depending on
the machine it runs on.

An earlier draft claimed this proved something about behaviour here. It did not,
for two reasons, both measured below: it is a test fix, and the locale it was
reported under was not the locale it ran under.

## The locale, measured

Neither `LC_ALL` nor `LANG` reaches Node on this machine; the ambient locale is
`ar-SA` and no environment variable changes it:

```
LC_ALL=de-DE.UTF-8 node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
LC_ALL=en-US.UTF-8 node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
LANG=de-DE.UTF-8 node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
node -e "console.log(Intl.NumberFormat().resolvedOptions().locale)" -> ar-SA
```

A locale passed in code does take effect, which is the control showing the
environment is not simply broken:

```
node -e "console.log(new Intl.NumberFormat('de-DE').format(1234.5))" -> 1.234,5
node -e "console.log(new Intl.NumberFormat('ar-SA').format(1234.5))" -> ١٬٢٣٤٫٥
```

So the four failures an earlier draft reported as "under de-DE" were ar-SA
failures: base's test asserts ASCII digits and separators, and the ambient locale
renders neither. At head-sha, in the worktree:

```
cd ../wt-pr1 && npx --no-install vitest run src/lib/currency.test.ts 2>&1 | grep -c 'AssertionError' -> 4
```

Restored with `git -C ../wt-pr1 checkout -- .`; the worktree is clean afterwards.
The runs are in `docs/evidence/pr1/currency-basetest-on-head-code.txt`.

## Not measured

- **Coverage beyond the one mutation.** One mutation was run, on
  `resolve.ts`. `slug.ts`, `header.ts` and `proxy.ts` have added tests that were
  not mutated, so for those the claim is only that the tests pass at head-sha.
- **The RLS policies.** `src/lib/tenant/isolation.sql` and
  `supabase/migrations/043_tenant_foundation.sql` are both present at head. No
  migration was applied and no database was touched while writing this, so
  nothing here says whether either is correct.
- **CI.** No CI run is shown. Whether this branch passes on a clean checkout in
  CI is unmeasured.
- **Environments other than this one.** Windows, Node `v24.21.0`, system locale
  `ar-SA`. Whether the suite behaves differently where `LC_ALL` does reach Node
  is unmeasured.
- **Whether head still merges cleanly.** `gh pr view 1` reported
  `mergeable=MERGEABLE` when this was written. That is the platform's cached
  answer, not a fresh trial merge.
