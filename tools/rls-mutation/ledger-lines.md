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
| `[7a] FAIL user A sees N probe row(s) of account B` | required | `select_using_true`, `select_plus_update_true` | declared signal for both select mutations |
| `[7a] FAIL user A reads 0 of its own rows` | required | — | positive control: a policy so narrow the caller sees nothing |
| `[7b] FAIL user B sees …` / `user B reads 0 …` | allowed_collateral | `select_using_true`, `rls_disabled`, `function_body_true` | symmetric second witness; never the sole evidence |

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
| `[9a] FAIL leak: an unscoped UPDATE touched N row(s) not owned by the caller` | required | `update_with_check_true` (B1.1) | **MEASURED verbatim:** `[9a] FAIL leak: the unscoped UPDATE moved 3 probe row(s) into account B (ALLOWED rows=2, 2 row(s) written)` — the declared `update_with_check_true` signal, which B2 never reached |
| `[9a] FAIL drift: the unscoped UPDATE touched N row(s), the baseline is M` | required | `update_policy_missing`, `update_with_check_true` (B1.1) | **MEASURED in both directions, which is the whole point of the line.** Under `update_with_check_true` the policy is WIDER than the baseline: `after the unscoped UPDATE account A owns 0 of its 2 seeded probe row(s) (ALLOWED rows=2)` — A handed its own rows to B. Under `update_policy_missing` the policy is NARROWER: `the unscoped UPDATE touched 0 row(s), the baseline is 2` — nothing moved at all. Both are real failures, so one line covers both directions and neither run is mistaken for the other |
| `[9a] FAIL positive control after: … the write path is broken, not merely strict` | allowed_collateral | `update_with_check_true` (B1.1) | **MEASURED verbatim:** `ALLOWED rows=0`. It goes red for a CASCADE reason: the earlier unscoped move already relocated the row, so the after-control can no longer find it. It is a consequence of the leak, not an independent signal — classifying it `required` would make any row-moving mutation look like it fired two declared signals |
| `[9a] FAIL cross-account UPDATE left N row(s) owned by account B and M row(s) owned by an account that is neither caller nor B` | allowed_collateral | `update_with_check_true` (B1.1) | **MEASURED verbatim:** `left 3 row(s) owned by account B and 0 row(s) owned by an account that is neither caller nor B`. Post-state witness for the same leak the `leak` line already proves; counted as postgres |
| `[9a] FAIL instrument: the unscoped UPDATE's ROW_COUNT and the ownership census disagree` | required | M-inst (w6 variant) | the G1 shape's own instrument |
| `[9a] FAIL cross-account UPDATE left N row(s) owned by account B` | allowed_collateral | `update_using_true`, `rls_disabled` | post-state witness for the same leak |

### `[9b]` — DELETE shapes

| line | class | observed under | note |
|---|---|---|---|
| `[9b] FAIL positive control before: …` | required | `rls_disabled` | A's own row must be deletable |
| `[9b] FAIL positive control after: …` | allowed_collateral | `rls_disabled` | after-write path |
| `[9b] FAIL cross-account DELETE: account B's probe row did not survive (rows found as postgres: 0, expected 1)` | required | `delete_using_true` (B1.1), `rls_disabled`, `function_body_true` | **the declared `delete_using_true` signal, which B2 never reached** — that was gap G2 |

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

## Defects fixed in the tooling, not the probe

Found while proving the above; each is a check that could not fail.

1. `:'u9a_w2_rows'` / `:'u9a_w3_rows'` — `\gset r9a_` defines `r9a_*`, so the probe
   died with `syntax error at or near ":"` and never ran. Fixed to `:'r9a_w2_rows'`.
2. `iso.d_probe_a` was read at three sites and never set. Fixed with an explicit
   `set_config`.
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