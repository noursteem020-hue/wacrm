# M-inst — negative control on the probe's own INSTRUMENTS

## What this is

`src/lib/tenant/isolation.sql` carries two self-checks that are supposed to catch a
broken **MEASUREMENT**, not just a broken policy. Both report AGREE at baseline and
neither has, until now, ever been seen red:

| instrument | GUC | the two sides it compares | instrument FAIL line |
|---|---|---|---|
| `w4_instrument` | `iso.section9a.w4_instrument` | `(SELECT count(*) FROM contacts WHERE company = <w4 tag>)` **vs** the unscoped UPDATE's own `ROW_COUNT` | `[9a] FAIL instrument: ROW_COUNT and the counted rows disagree (...)` (probe line 832) |
| `d6_instrument` | `iso.section9b.d6_instrument` | the DELETE's `ROW_COUNT` **vs** `census_before - census_after` | (computed and echoed; **no FAIL line references it — see "Not achieved" below**) |
| `w6_instrument` | `iso.section9a.w6_instrument` | `(v_n > 0)` **vs** `w6_b > a_expected_b` | `[9a] FAIL instrument: the unscoped UPDATE's ROW_COUNT and the ownership census disagree (...)` (probe line 802) |

If the counting is wrong, every leak verdict derived from it is worthless, so the
probe asserts and goes red on the instrument line rather than reporting a clean
sweep. A claim like that is decoration until something turns it red.

## The dangling reference this resolves

`src/lib/tenant/isolation.sql`, lines 669–671, the comment directly above
`iso.section9a.w6_instrument`:

```
  -- of this assertion had. AGREE is measured under baseline and under every
  -- policy mutation in the campaign; DISAGREE is reachable only by breaking the
  -- counting itself (see the M-inst probe in tools/rls-mutation/).
```

The path the comment names is `tools/rls-mutation/`; the probe referenced here is
`tools/rls-mutation/isolation-minst-w6.sql` plus `tools/rls-mutation/minst/`
(the evidence). No file named "M-inst" existed at that path before this task.

The comment attaches to **w6**, so the primary mutant in this directory breaks
**w6** — that is what makes the comment literally true rather than true by analogy.
`w4` is broken as well, in a second copy, because the brief asked for the w4 shape.

## What was broken, and why it is a MEASUREMENT break

### Primary mutant — `isolation-minst-w6.sql`, one line, line 673

```diff
-    CASE WHEN (v_n > 0) = (current_setting('iso.section9a.w6_b')::bigint > current_setting('iso.a_expected_b')::bigint)
+    CASE WHEN (v_n > 0) = (current_setting('iso.section9a.w6_b')::bigint > current_setting('iso.section9a.w6_elsewhere')::bigint)
```

The instrument's right-hand side stops reading the **fixture's** pre-write
expectation for account B (`iso.a_expected_b`, seeded as postgres where the fixture
is built) and starts reading a **post-write** counter (`iso.section9a.w6_elsewhere`,
rows owned by neither A nor B). Both are legitimate readings of the census; comparing
against the wrong one makes the two sides of the instrument disagree. No policy
expression, no UPDATE, no DELETE, and no verdict line is touched — the leak, drift
and positive-control readings are all byte-identical to baseline. At baseline both
readings are 1 and 0, so the swap is a pure comparison error.

### Secondary mutant — `../isolation-minst.sql`, two lines, 604 and 607

```diff
-    CASE WHEN (SELECT count(*) FROM contacts WHERE company = current_setting('iso.a_w4_tag')) = v_n
+    CASE WHEN (SELECT count(*) FROM contacts WHERE company = 'M-INST-NO-SUCH-COMPANY') = v_n
          THEN 'AGREE'
          ELSE 'DISAGREE rows_counted=' ||
-              (SELECT count(*) FROM contacts WHERE company = current_setting('iso.a_w4_tag')) ||
+              (SELECT count(*) FROM contacts WHERE company = 'M-INST-NO-SUCH-COMPANY') ||
```

A filter that cannot match anything. The instrument still runs, still runs as
postgres, and now counts 0 against a `ROW_COUNT` of 2. The census that the leak
verdict actually uses (`iso.section9a.w4_touched` / `w4_leaked`, lines 593–597) is
untouched and still prints `touched= 2  leaked= 0` — the run is red because the
measurement disagrees with itself, not because a policy moved.

## Results, all measured

| run | file | policy mutation | exit | instrument lines | FAIL lines |
|---|---|---|---|---|---|
| 1 | unmodified copy | none | **0** | w4 AGREE, w6 AGREE, d6 AGREE | none |
| 2 | `../isolation-minst.sql` (w4 broken) | none | **3** | w4 **DISAGREE rows_counted=0 row_count=2** | w4 instrument only |
| 3 | `../isolation-minst.sql` (w4 broken) | `contacts_delete USING (true)` | **3** | w4 **DISAGREE rows_counted=0 row_count=2** | w4 instrument + 9b cross-account DELETE |
| 5 | `isolation-minst-w6.sql` (w6 broken) | `contacts_delete USING (true)` | **3** | w6 **DISAGREE rows=0 a_owned_after=2 b_owned_before=1 b_owned_after=1** | w6 instrument + 9b cross-account DELETE |
| 6 | `isolation-minst-w6.sql` (w6 broken) | none | **3** | w6 **DISAGREE rows=0 a_owned_after=2 b_owned_before=1 b_owned_after=1** | w6 instrument only |

Run 2 vs run 3 and run 6 vs run 5 are the requested contrast: the instrument line
fires with **and** without a policy mutation applied, byte-identical either way, so
it is independent of which policy is broken. Run 2/6 also show the run goes red on
the instrument **alone** — the only FAIL line in the run is the instrument one, and
no leak or drift line appears.

Baseline after every run: `prosrc_md5=026fa63f24c5f54584758c4f5d314408
prosrc_len=510 cr=20`, `fingerprint_token=bfae0aea057682e5403f70c94f0b5f61`,
`contacts_delete_qual=is_account_member(account_id, 'agent'::account_role_enum)`,
`policy_deps_on_is_account_member=98`. `src/lib/tenant/isolation.sql` is
byte-identical to its state at task start (`md5 6f4ab27e7fc69bddd21631b578e0959c`).

## Files

Mutants (copies; the live probe was never modified):
- `../isolation-minst.sql` — copy of the live probe, w4 instrument broken (2 lines)
- `isolation-minst-w6.sql` — copy of the live probe, w6 instrument broken (1 line)

Diffs against the live probe:
- `../isolation-minst.diff`, `isolation-minst-w6.diff`

Helper SQL:
- `baseline_state.sql` — md5(prosrc), the preflight6 `FINGERPRINT_SQL` token,
  `contacts_delete` qual, dependent-policy count. Both `--baseline_md5_match` and
  `--fingerprint_match` are asserted server-side against the recorded constants.
- `apply_contacts_delete_using_true.sql` — the policy mutation, committed in its own
  transaction so the probe's own `BEGIN…ROLLBACK` cannot discard it.
- `restore_contacts_delete.sql` — restore, `DROP POLICY **IF EXISTS**` +
  `CREATE POLICY … USING (is_account_member(account_id, 'agent'::account_role_enum))`.

Captured output, one file per run, each ending in its own `EXIT=` line:
- `run1_baseline_unmodified.txt`
- `run2_instrument_broken_no_policy.txt`
- `run3_instrument_broken_with_policy.txt`
- `run4_final_baseline_state.txt`
- `run5a_apply_policy.txt`, `run5b_w6_instrument_broken_with_policy.txt`, `run5c_restore.txt`
- `run6_w6_instrument_broken_no_policy.txt`

## Reproducing a run

```bash
cd /c/Users/FX-tec/Desktop/wacrm-work
docker exec -i supabase_db_wacrm psql -U postgres -d postgres -v ON_ERROR_STOP=1 -f - \
  < tools/rls-mutation/minst/isolation-minst-w6.sql ; echo "EXIT=$?"
```

## Not achieved

**`d6_instrument` has no FAIL line anywhere in the probe.** It is computed
(`iso.section9b.d6_instrument`, probe lines 1029–1044) and echoed at line 1073
(`[9b] census after … instrument= …`), but no assertion in the file compares
`d9b_d6_instrument` to `'AGREE'`. Grep for it returns exactly three hits — the
`set_config`, the `current_setting` in the `\gset` SELECT, and the `\echo`. There
is no third. So `d6_instrument` cannot make a run go red; it is reporting-only, and
it is **not** a second load-bearing instrument. That is a real coverage gap in the
probe and it is recorded here rather than papered over.

Breaking the `d6` census filter was therefore not attempted as an acceptance case:
it would have produced an `instrument= DISAGREE …` echo and still exited 0, which
is exactly the "printed result is not an assertion" defect the skill warns about.
The load-bearing instruments in this file are `w4_instrument` and `w6_instrument`,
and both were seen green at baseline and red under an instrument-only mutation.
