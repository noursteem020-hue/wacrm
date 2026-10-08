# Ledger line accounting — `src/lib/tenant/isolation.sql`

Every `FAIL` line the probe can emit is classified here as `required` or
`allowed_collateral`. A line that is neither is a **defect**, not noise: it means
the probe can turn red for a reason nobody declared.

**Origin:** the B2 campaign found 26 emitted `FAIL` lines that no manifest entry
declared. Most were a legitimate cascade from broad mutations, but one was not — a
line that asserted a leak when zero rows had moved (now fixed, see G3 below).

**Rule for reading this table:**

- `required` — this line is the declared signal for at least one mutation in
  `mutations.yaml`. Its absence under a mutation that should trigger it is a gap.
- `required_untested` — **a declared signal whose triggering mutation is missing
  from the manifest.** The line never fired in any run, because no entry exercises
  it. It is neither `required` (nothing proved it) nor `allowed_collateral` (it is
  not a secondary detection of the same breakage — usually it is the only witness
  for a *narrower*-than-baseline policy, a direction nothing else reports). The row
  must name the gap and the mutation that would close it, e.g.
  `G4 select_using_false`; a row with this class and no named gap is `MALFORMED` and
  fails the audit, because that is indistinguishable from a silent downgrade.
- `allowed_collateral` — a correct secondary detection of the *same* breakage.
  Legitimate under a broad mutation, but it must never be the *only* evidence, and
  it earns nothing on its own.
- **Neither** — the probe asserts something false, or reports a policy verdict
  derived from a measurement that does not support it. That is a bug.

Every "observed under" entry below is **MEASURED**, read from
`C:/Users/FX-tec/AppData/Local/Temp/evidence/executor.jsonl` (the B2 raw logs),
cross-checked against the FAIL-emitting sites grepped out of the probe. Nothing
here is inferred from the probe's comments.

---

## Measured: which FAIL tag each mutation actually produced (B2 raw logs)

| mutation | FAIL tags observed | verdict |
|---|---|---|
| `clean_baseline` | *(none)* | green, as required |
| `rls_disabled` | 7a 7b 8 9a 9b | cascade — legitimate |
| `function_body_true` | 7a 7b 8 9a 9b | cascade — legitimate, 98 policies open at once |
| `select_using_true` | 7a 7b | declared |
| `insert_with_check_true` | 8 | declared |
| `select_plus_update_true` | 7a 7b 9a | declared + collateral 9a |
| `update_using_true` | 9a | declared |
| `update_policy_missing` | 9a | declared |
| `update_with_check_true` | *(none)* | **gap G1 — closed in B1.1** |
| `delete_using_true` | *(none)* | **gap G2 — closed in B1.1** |

---

## The inventory

### `[7a]` / `[7b]` — SELECT census, users A and B

| line | class | observed under | note |
|---|---|---|---|
| `[7a] FAIL user A sees N probe row(s) owned by account B -- cross-account SELECT leak` | required | `select_using_true`, `select_plus_update_true`, `rls_disabled`, `function_body_true` | declared signal for both select mutations |
| `[7a] FAIL user A sees N probe row(s) outside its own account (any account, not only B)` | required | `select_using_true`, `select_plus_update_true`, `rls_disabled`, `function_body_true` | the same leak stated generally, so a third tenant's row would be caught too; not a duplicate of the line above |
| `[7a] FAIL user A reads 0 of its own rows` | required_untested | G4 `select_using_false` | positive control: a policy so narrow the caller sees nothing. **Never fired in B2 or B2.2**, so it is NOT a declared signal of any measured mutation — it is a positive control whose triggering mutation is missing from the manifest. Guard read at HEAD: `WHEN :'a_own_rows'::bigint = 0` (probe line 243). Recorded as gap **G4**; the mutation that would exercise it belongs to B3, not here. It must not be `allowed_collateral`: nothing else in the table reports "the policy is too narrow" |
| `[7b] FAIL user B sees N probe row(s) owned by account A -- cross-account SELECT leak` | allowed_collateral | `select_using_true`, `rls_disabled`, `function_body_true` | symmetric second witness; never the sole evidence |
| `[7b] FAIL user B sees N probe row(s) outside its own account (any account, not only A)` | allowed_collateral | `select_using_true`, `select_plus_update_true`, `rls_disabled`, `function_body_true` | as above, stated generally |

### `[8]` — INSERT WITH CHECK

| line | class | observed under | note |
|---|---|---|---|
| `[8] FAIL cross-account INSERT was ALLOWED: …` | required | `insert_with_check_true` | declared signal |
| `[8] FAIL cross-account INSERT was rejected for the WRONG REASON` | allowed_collateral | `rls_disabled`, `function_body_true` | a rejection that is not 42501 is not isolation |

### `[9a]` — UPDATE shapes

| line | class | observed under | note |
|---|---|---|---|
| `[9a] FAIL positive control before: …` | required | `update_policy_missing` | the declared signal for a missing UPDATE policy |
| `[9a] FAIL positive control after: …` | allowed_collateral | `update_policy_missing` | the after-write path also broken |
| `[9a] FAIL the positive-control row was not actually modified` | allowed_collateral | `update_policy_missing` | "allowed was reported without the write landing" |
| `[9a] FAIL the positive-control row is no longer owned by account A` | allowed_collateral | `update_using_true` | row moved accounts |
| `[9a] FAIL cross-account UPDATE was ALLOWED (…, N row(s) actually moved)` | required | `update_using_true` | **G3: predicate is now `rows > 0`, extracted by `regexp_match`** |
| `[9a] FAIL cross-account UPDATE was rejected for the WRONG REASON` | allowed_collateral | — | non-RLS rejection is not isolation |
| `[9a] FAIL cross-account UPDATE with no account filter was ALLOWED (… N row(s) actually moved)` | required | `update_using_true` | second write shape |
| `[9a] FAIL cross-account UPDATE with no account filter was rejected for the WRONG REASON` | allowed_collateral | — | as above |
| `[9a] FAIL instrument: ROW_COUNT and the counted rows disagree` | required | M-inst | **load-bearing.** proven by `tools/rls-mutation/minst/` |
| `[9a] FAIL leak: the unscoped UPDATE moved N probe row(s) into account B` | required | `update_with_check_true` (B1.1) | the B1.1 wording of the w6 leak arm. **MEASURED verbatim:** `moved 3 probe row(s) into account B (ALLOWED rows=2, 2 row(s) written)`, counted as postgres before rollback. This is the declared `update_with_check_true` signal, which B2 never reached. The older `touched N row(s) not owned by the caller` wording below is the pre-B1.1 w4 arm and is still emitted by the broader mutations |
| `[9a] FAIL drift: after the unscoped UPDATE account A owns N of its M seeded probe row(s)` | required | `update_with_check_true`, `update_using_true`, `rls_disabled`, `function_body_true`, `select_plus_update_true` | the B1.1 w6 drift arm, stated in ownership terms rather than a raw row count. **MEASURED:** `account A owns 0 of its 2 seeded probe row(s) (ALLOWED rows=2)` — the policy is WIDER than the baseline, A handed its own rows to B |
| `[9a] FAIL positive control after: … the write path is broken, not merely strict` | allowed_collateral | `update_with_check_true` (B1.1) | **MEASURED verbatim:** `ALLOWED rows=0`. It goes red for a CASCADE reason: the earlier unscoped move already relocated the row, so the after-control can no longer find it. It is a consequence of the leak, not an independent signal — classifying it `required` would make any row-moving mutation look like it fired two declared signals |
| `[9a] FAIL cross-account UPDATE left N row(s) owned by account B and M row(s) owned by an account that is neither caller nor B` | allowed_collateral | `update_with_check_true` (B1.1) | **MEASURED verbatim:** `left 3 row(s) owned by account B and 0 row(s) owned by an account that is neither caller nor B`. Post-state witness for the same leak the `leak` line already proves; counted as postgres |
| `[9a] FAIL instrument: the unscoped UPDATE's ROW_COUNT and the ownership census disagree` | required | M-inst (w6 variant) | the G1 shape's own instrument |
| `[9a] FAIL leak: an unscoped UPDATE touched N row(s) not owned by the caller` | required | `update_using_true`, `rls_disabled`, `function_body_true`, `select_plus_update_true` | the pre-B1.1 **w4** leak arm, counted across every account the caller does not own. Still emitted by the broader mutations; the narrower `update_with_check_true` case is caught by the `moved N probe row(s) into account B` row above. **Reverse-audited, declaration CONFIRMED:** MEASURED in B2.2 that exactly those four mutations emit this line — `update_using_true` (3 rows) and `function_body_true` / `rls_disabled` / `select_plus_update_true` (4 rows) — and that `update_with_check_true` does NOT, consistent with it being the narrower w6 case |
| `[9a] FAIL drift: the unscoped UPDATE touched N row(s), the baseline is M` | required | `update_policy_missing` | the pre-B1.1 **w4** drift arm. **MEASURED under `update_policy_missing`:** `the unscoped UPDATE touched 0 row(s), the baseline is 2` — the policy is NARROWER than the baseline and nothing moved at all, which is a real failure and must not be read as safety |
| `[9a] FAIL cross-account UPDATE left N row(s) owned by account B` | allowed_collateral | `update_using_true`, `rls_disabled`, `function_body_true` | post-state witness for the same leak, counted as postgres before the rollback |

### `[9b]` — DELETE shapes

| line | class | observed under | note |
|---|---|---|---|
| `[9b] FAIL positive control before: …` | required_untested | G5 `delete_policy_missing` | A's own row must be deletable. **Never fired in B2 or B2.2**, so it is not a declared signal of any measured mutation — it is a positive control whose triggering mutation is missing. Guard read at HEAD: `WHEN :'d9b_d1' <> 'ALLOWED rows=1'` (probe line 1086); `rls_disabled` opens RLS rather than breaking A's own delete, which is why it stayed silent under it. Recorded as gap **G5**; the mutation belongs to B3. It must not be `allowed_collateral`: it detects the policy being too narrow, which no other line in the `[9b]` section reports |
| `[9b] FAIL positive control after: …` | allowed_collateral | `rls_disabled` | after-write path |
| `[9b] FAIL cross-account DELETE: account B's probe row did not survive (rows found as postgres: 0, expected 1)` | required | `delete_using_true` (B1.1), `rls_disabled`, `function_body_true` | **the declared `delete_using_true` signal, which B2 never reached** — that was gap G2 |
| `[9b] FAIL instrument: the unscoped DELETE's ROW_COUNT and the ownership census disagree` | required | M-inst (`isolation-minst-d6.sql`) | the `[9b]` census instrument, added by B1.1. Absent from B2.2 by design: it fires only when the census itself is broken, which no mutation in the manifest does. **Proven load-bearing** by `tools/rls-mutation/minst/run8_d6_instrument_broken.txt` — see the census section below |

### `[9b]` census instrument — **fixed in B1.1**

`iso.section9b.d6_instrument` was computed (line 1029), carried through `\gset`
(line 1058) and echoed (line 1073), and **never asserted** — MEASURED before the
fix: `grep -cE "WHEN :'d9b_d6_instrument'"` returned `0`. Breaking the census printed
`instrument= DISAGREE` and the run still **exited 0**, so the `[9b]` DELETE verdicts
were taken on a measurement nothing watched.

Closed by one `WHEN :'d9b_d6_instrument' <> 'AGREE'` arm, mirroring `[9a]`'s
`w4_instrument` and `w6_instrument`. Proved load-bearing by
`tools/rls-mutation/minst/isolation-minst-d6.sql`, which changes the census's third
term (`d6_other` → `0`) and nothing else:

| run | exit | instrument | FAIL lines |
|---|---|---|---|
| live probe, baseline | **0** | `[9b] instrument= AGREE` | 0 |
| d6 mutant, no policy mutation | **3** | `[9b] instrument= DISAGREE rows=0 census_before=2 census_after=2` | 1, and it is the instrument |

MEASURED verbatim:
```
[9b] FAIL instrument: the unscoped DELETE's ROW_COUNT and the ownership census disagree
(DISAGREE rows=0 census_before=2 census_after=2). The DELETE verdict above would be meaningless
```

All three instruments are now load-bearing: `w4`, `w6`, `d6`.

---

## What was fixed in B1.1

| gap | defect | fix | proven by |
|---|---|---|---|
| **G1** | `update_with_check_true` went green: the unscoped `account_id` move was never asserted | new `w6` shape + `leak`/`drift`/`instrument` assertions | MEASURED: exit 3, 5 FAIL lines, line present |
| **G2** | `delete_using_true` went green: all four `DELETE`s in `[9b]` had a `WHERE`, so `contacts_select` scoped the scan | added the unscoped `DELETE FROM contacts` shape, counted per account as postgres | MEASURED: exit 3, 2 FAIL lines |
| **G3** | false red: `WHEN :'u9a_w2' LIKE 'ALLOWED%'` fired on `ALLOWED rows=0` | predicate is now `:'r9a_w2_rows' > 0`, the count extracted by `regexp_match` | MEASURED: the `ALLOWED rows=0` line is absent under `update_policy_missing` |

## Known gaps — deferred to B3, not closed here

Found by the reverse audit: a positive control that has never fired because the
manifest contains no mutation that exercises it. Classified `required_untested`,
which is a real class and not a euphemism — see the rule below the table.

| gap | line | missing mutation | why it is not collateral |
|---|---|---|---|
| **G4** | `[7a] FAIL user A reads 0 of its own rows` | `select_using_false` | Detects a SELECT policy **narrower** than the baseline — A sees nothing at all. Nothing else in the table reports that direction; every other `[7a]` row reports a policy that is too wide. Downgrading it would delete the only narrow-side witness. Guard read at HEAD: `WHEN :'a_own_rows'::bigint = 0` (probe line 243). |
| **G5** | `[9b] FAIL positive control before: …` | `delete_policy_missing` | Detects a DELETE policy **narrower** than the baseline — A cannot delete its own row. No other line in the `[9b]` section reports that; the other two report leaks and after-path breakage. Guard read at HEAD: `WHEN :'d9b_d1' <> 'ALLOWED rows=1'` (probe line 1086). |

Both were found after B1.1, in B2.2, by asking which declared `required` rows had
never fired. The mistake made first — and recorded here because it is the same one
this whole campaign exists to prevent — was to relabel them `allowed_collateral`,
which would have made the audit pass while hiding two untested failure directions
behind a reclassification. A line that never fired is not thereby secondary; for a
positive control it means the MANIFEST is missing a mutation.

`required_untested` is accepted by `ledger_audit.py` only when the row **names the
gap**, in the form `G4 select_using_false`. A row with that class and no named gap is
reported `MALFORMED` and fails the audit — because without the name it is
indistinguishable from the silent downgrade it was introduced to prevent. T4 in
`tools/rls-mutation/audit_breaktest.py` blanks the name and requires the failure,
and preflight6.py runs that test before it touches the database.

## Comparing file bytes: use git's blob hash, not md5sum on a pipe

Recorded here because it nearly cost a false accusation of fabricated evidence, and
because the same CR-vs-LF trap has now bitten this campaign three times.

```
git rev-parse <commit>:<path>     # committed bytes   -> 9529a37d...
git hash-object <path>            # working bytes     -> 9529a37d...   equal
git show <commit>:<path> | md5sum # measured 44e3a6d: ALSO equal
md5sum <path>                     # working bytes     -> differs from both
```

`executor.jsonl` is byte-identical between `bd7ed58` and `HEAD`. CORRECTION to
what this section previously claimed: the difference is not produced by a pipe.
MEASURED at `44e3a6d` on `docs/evidence/b2/executor.jsonl`, `git show` piped to
`md5sum` returns the blob's md5 exactly (`1b72deae…`, 14 LF, 0 CR), as does
`git cat-file blob`. The working copy returns `bb3bf376…` with 14 CR. The
conversion is `core.autocrlf=true` acting on **checkout**; a pipe never sees it.
`git hash-object` normalises the working file back to the blob oid
(`21e4a738…`), which is why it is the right command and `md5sum` is not.

Count CR with `tr -dc '\r' | wc -c`, which counts bytes and cannot be
made to count anything else. `grep -c` counts LINES, so on these files it answers
a different question. The numbers are measured below. The reason the two
spellings disagree is NOT established, and this section does not guess at it.

```
printf 'one\ntwo\nthr%se\nfour\nfive\n' "$(printf \r)" > five.txt
printf 'plain\n' > none.txt
tr -dc '\r' < five.txt | wc -c
grep -c $'\r' five.txt
```

```
1
5
```

One real CR, in a five-line file, and `grep -c $'\r'` answered **5**; a file with
no CR at all answered **1**. Both equal the LINE COUNT of their file.

An earlier version of this section said the pattern "arrives empty" and matches
every line. That is false, and one command disproves it:
`printf '%s' $'\r' | od -An -tx1` prints `0d`, a real carriage return. What makes
`grep -c` return the line count rather than the CR count is not measured. Do not
spend a round on it: `tr -dc '\r' | wc -c` closes the question.

## The spellings, measured on files known to hold one CR and none

| spelling | 1 CR in 5 lines | 0 CR in 1 line | reads as |
|---|---|---|---|
| `grep -c $'\r'` | 5 | 1 | line count; cause not established |
| `grep -c "$CR"` (CR in a variable) | 1 | 0 | the true CR count |
| `grep -cF '\r'` (fixed string) | 0 | 0 | a search for two characters |
| `grep -c '\r'` (GNU BRE) | 2 | 0 | lines containing the letter r |

Only the second reports carriage returns honestly, because a CR held in a
variable reaches grep as an argument rather than through shell expansion.

`grep -c '\r'` is not the literal spelling, and the last row counts the letter
r, not line endings: in a GNU BRE `\r` is the letter r, so `echo r | grep -c
'\r'` returns 1 and a file with no r in it returns 0. Only `-F` searches for
the two characters themselves.

The `0` reported earlier in this campaign came from the same spelling written
inside a double-quoted `bash -c` string. Deeper nesting is a real hazard here,
because `verify-pr-body.py` runs every claim through `bash -c`, so a surprising 0
from a claim containing `$'...'` is a suspect spelling before it is a fact about
the repository.

The practical rule, and the one to check against: **a count of CR bytes is only
trustworthy from `tr -dc '\r' | wc -c`, and a negative from `grep -c` is not a
negative at all.** `docs/evidence/b2/executor.jsonl` measures 14 CR bytes in the
working tree and 0 in the blob by the first command; the second returns 14 in
both, which is its 14 lines.


The blob hash is the only statement about bytes here that survives a reviewer
running it on another machine. The rule is in `tools/rls-mutation/campaign_paths.py`

Count CR in the COMMITTED bytes, not the checkout: `git cat-file -p HEAD:<path> | tr -dc '\r' | wc -c`
returns 0 for this file: git stores it LF-only, while the working copy carries CR bytes
that git will strip again on the next add. The file is MIXED, not uniformly CRLF, so
quote no total at all: it moves on every edit. What is stable is the direction —
`cat-file` reads 0 CR from the commit while `md5sum` and `tr` read CR from the checkout
— and that is what decides which command answers a question about committed bytes.
beside `EXPECTED_PROBE_BLOB`, which is itself a `git rev-parse` value for the same
reason.

## Defects fixed in the tooling, not the probe

Found while proving the above; each is a check that could not fail.

Two of the entries in this list were checked by an independent reviewer and are
**withdrawn**, because no commit is in the state they describe. Both were written
from memory rather than from a command, and both are kept here so the error is
visible rather than quietly deleted:

1. **WITHDRAWN.** The claim was that the probe "died with `syntax error at or near
   ":"`" because `:'u9a_w2_rows'` did not exist. That identifier appears in no
   commit — grepping the pre-B1.1 blob `52e545b8` for `u9a_w2_rows` returns 0
   matches. What the tip actually carries is TWO prefixes on purpose: `\gset u9a_`
   (line 622) defines the outcome strings `u9a_w1..w6`, and `\gset r9a_` (line 768)
   defines the counts parsed from them. The assertions read both —
   `:'r9a_w2_rows' > 0` (line 772) for the number, `:'u9a_w2'` (line 774) for the
   message. The two coexist by design.
2. **WITHDRAWN.** The claim was "`iso.d_probe_a` was read at three sites and never
   set". It is read at TWO sites (lines 966 and 1014) and set at line 884, all inside
   `f251f9a` — and `git log -S'iso.d_probe_a'` shows that commit INTRODUCES the
   symbol, so no earlier commit had it unset.
3. The restore used `SELECT convert_from(decode(…))`, which **prints** a definition
   instead of executing it: psql exits 0, the restore reports success, the database
   stays mutated. `executor.py`'s fingerprint gate **does** catch it — proven, not
   assumed, by `tools/rls-mutation/restore-breaktest.md` (four variants, three fire,
   one passes).

## Still open

- `restore_procedure` prose in `mutations.yaml` names the `pg_proc` column
  `proparallelsafe`, which does not exist on PostgreSQL 17 (`information_schema`
  returns 0 rows; the real column is `proparallel`). So the manifest does not
  describe what the Executor actually ran. Separate commit — it is an attribute
  correction, not isolation logic.
- `executor.py` pins `EXPECTED_PROBE_BLOB` to this file's **pre-B1.1** blob
  (`52e545b8…`, which equals `HEAD`). B1.1 changes the probe, so the pin is stale and
  a re-run of B2 aborts at its precondition until it is re-pinned to the B1.1 commit's
  blob. Separate commit, after this one lands.

## Where the evidence lives

Everything above is backed by raw output in the repository, not in a temp directory:

- `docs/evidence/b2/` — the B2 campaign: `executor.jsonl` (raw per-entry records),
  `verifier.txt`, `critic.txt`, `b1.1-acceptance.txt`.
- `tools/rls-mutation/` — `mutations.yaml`, this file, `shape-matrix.md`,
  `restore-breaktest.md`, and the M-inst mutants under `minst/`.

An earlier version of this document pointed at `C:/Users/.../AppData/Local/Temp/…`,
which is machine-local and survives nothing. All evidence a reader needs is now under
version control.

## A claim I made and then withdrew: "the suite is flaky"

MEASURED at `1bfa392` in a detached worktree, `npm ci` then `npm test`, five
consecutive runs:

```
Tests 1110 passed | 7 skipped (1117)   rc=0   runs 1,2,3,4,5
```

An earlier draft of PR #4 and PR #3 said the suite was flaky, citing one run that
failed on `src/i18n/icu-safety.test.ts` ("every {{...}} / raw-HTML message is
consumed via t.raw() or t.rich()"). That failure did not reproduce in five runs, and
`git grep -ci 'icu|hostile'` over both committed suite files returns 0, so no
committed evidence supports it. Claim withdrawn, and the reason it cannot be settled
is recorded rather than papered over: that run's output was never kept. See "The
defect that caused this" at the end of this document — that rule is the actual
lesson, and it is now in `AGENTS.md`.

## The defect that caused this: output that was never kept

The lesson from the ICU failure is not the cause, which is still unknown. It is that
a single unexplained failure consumed four hypothesis rounds and one reviewer cycle
because the run's output was piped through `grep` and discarded. With the output in
hand the question would have been answerable in one minute.

Two rules, now in `AGENTS.md`:

- **Capture before reading.** `npm test 2>&1 | tee runs/<sha>-<n>.txt`, relative
  path, never piped straight into `grep`. Sanitise before it is committed: strip
  absolute paths and wall-clock timestamps.
- **A failure is copied to evidence before it is re-run.** Re-running is how the
  original output gets lost.

This is recorded here because the same campaign already lost evidence twice, and a
rule that only lives in a commit message is lost with the commit.

## Two hypotheses for the ICU failure, both tested, neither confirmed

HYPOTHESIS 1 (the one I first wrote down): a vitest transform-cache race on a cold
run, which is consistent with a guard that reads source text. TESTED and NOT
CONFIRMED: in a worktree created fresh at `1bfa392`, `npm ci` then two runs, both
gave `1110 passed | 7 skipped (1117)`, rc=0, and neither output mentions `icu` or
`hostile`.

```
git grep -il 'hostile' 1bfa392 -- '*.test.*'   -> src/i18n/icu-safety.test.ts
                                                     src/i18n/messages.test.ts
```

The test file is real and was present at `1bfa392`; it is not an invented name.

HYPOTHESIS 2: the failing run happened in the mis-pathed worktree created earlier in
this session at `C:/c/Users/FX-tec/Desktop/w868` (a literal `/c` prefix, so no
`node_modules` and the wrong tree). Consistent with what I saw, but UNTESTED — that
directory is gone and the run's output was not kept, so it cannot be tested now.

Neither hypothesis is recorded as the cause. A first-run failure of that guard after
a fresh install remains unexplained by anything measured here, and I am not going to
name a mechanism I cannot reproduce.

## tsc: `--ignoreConfig` was not the blind check I suspected

MEASURED, at `20fa752`, deliberately broken file each time, then removed:

| check | `-p tsconfig.json` | `--ignoreConfig` |
|---|---|---|
| `const x: number = "s"` | rc=2, caught | rc=1, caught |
| strict-only error (`unknown.toFixed()`) | rc=2, caught | rc=1, caught |
| wrong member on a `@/` aliased import | rc=2, caught | rc=1, caught |
| a file under `.claude/` | rc=0, skipped | rc=0, skipped |
| files checked, `--listFilesOnly` | 2453 | 2453, identical set |

So `--ignoreConfig` was not vacuous: it saw the same 2453 files and caught every
error. Two real differences remain, both MEASURED:

- it exits `1` where the configured project exits `2`:
  `const a: number = "s"` -> `-p tsconfig.json` rc=2, `--ignoreConfig` rc=1,
  reproducibly across runs.

That exit code is the ONLY difference I could measure. In particular my earlier
sentence "it silently drops `paths` and `exclude`" was NOT measured, and the
evidence does not support it: `--showConfig` reports the same `strict` and the same
`paths` for both invocations; `@/lib/tenant/resolve` resolves to
`src/lib/tenant/resolve.ts` under both; the alias `paths` does not define,
`@components/nope`, fails as `TS2307` under both; and `--traceResolution` under
`--ignoreConfig` still prints `'paths' option is specified`. The claim is withdrawn
rather than reworded.

So the honest summary is: same files, same errors caught, same files skipped, same
resolution behaviour on this toolchain, and a different exit code. A reviewer cannot
tell which invocation ran from a bare `rc=0`, which is the whole reason the claims
now name `-p tsconfig.json` explicitly — correct by construction, break test `rc=2`.

Also recorded, because it nearly became a wrong claim: the `.claude/` row shows that
`-p` does NOT check every file on disk — it honours `include`/`exclude`. Neither
invocation is a "check the entire repository" button.

Also measured while fixing this: the committed run output contained my absolute
home path (`C:/Users/FX-tec/Desktop/w868`) and a wall-clock timestamp. Both are now
elided in the committed files, since an evidence artefact that carries the author's
absolute path is not portable and not re-checkable by a reviewer.

## A status report of mine that was wrong, and the commit message that carried it

Two claims reached the user without a command behind them. Both are recorded here
rather than corrected quietly, because the reason they escaped is the reusable part.

**1. `backup-b11` never existed.** In a handoff report I wrote "`backup-b11` still
exists as a safety net", and the user built a decision on that sentence ("leave it
until the three PRs merge, then delete it"). Nothing was ever verified. MEASURED now:

```
git reflog --all | grep -i b11                              -> (nothing)
git for-each-ref --format='%(refname)' | grep -i b11       -> rc=1, no such ref
git branch --list | grep backup                            -> backup/isolation-6add58a
                                                                 backup/pre-reword
                                                                 backup/pre-security-2026-10-04
```

The one reflog hit for `b11` is `db11f76 refs/remotes/origin/test/foundational-vitest-suite`
— an unrelated branch whose SHA happens to contain those characters. The B1.1 work
is reachable regardless: `git merge-base --is-ancestor f251f9a HEAD` returns 0, and
three `backup/*` branches plus the reflog hold the chain.

The rule this breaks is the one already in `AGENTS.md`: a report to the user is a
claim, and "still exists" needs the command that says so. I had that rule written and
still asserted it from memory — the same failure as the `icu|hostile` BRE and the
`tsc` project flag, in a third form.

**2. `35017d9` claimed a measurement it did not make.** Its message says the suite was
"re-ran at 867ced9 and replaced npm-test-tip.txt". The commit's only file change:

```
git show 35017d9 --name-status --format=''   -> M  tools/rls-mutation/minst/README.md
git log --format=%h -1 -- docs/evidence/suite/npm-test-tip.txt   -> acc352d
```

Not amended. `d13df3d` made the run for real. New rule, in `AGENTS.md`: **a commit
message describes the change and never asserts a measurement.** Measurements live in
`docs/evidence/`, which is the only place anything is guarded — by
`tools/verify-pr-body.py` for descriptions, by nothing for messages, which is exactly
why the error survived a review round that passed.
