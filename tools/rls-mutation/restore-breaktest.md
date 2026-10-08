# B1.1-RESTORE-TEST — does executor.py's post-restore `hard_stop` catch a silent restore failure?

**Answer: YES, the gate is real.** It reads the fingerprint **after** the restore and compares it to
the value captured before any mutation. Proven by execution, not by reading — the exact defect that
shipped in the previous run (a `SELECT convert_from(decode(...))` "restore" that prints the
definition instead of executing it) is caught, with the gate firing and the DB genuinely off
baseline. The gate also discriminates: three corrupted restores fire it, the correct restore passes
it clean.

One caveat, stated because it is a real difference and not a defect: `hard_stop` fires on the
**fingerprint alone** (line 362). `md5(prosrc)` is computed and recorded at line 352–354 but is not
part of the firing condition. In this database the fingerprint's `FN|` line embeds `md5(prosrc)`
(`preflight6.py:94`), so the two agree on every variant measured below. A drift that changed some
*other* hashed object without touching `prosrc` would fire on the fingerprint; a hypothetical drift
touching only `prosrc` in a way the fingerprint missed would be recorded in the JSONL and would not
stop the run. That path is unreachable with the current fingerprint SQL.

## The gate, cited

`executor.py` lines 349–364, quoted from the file (the script re-reads and asserts these five
statements on every run, so this citation cannot rot):

```
   349|         fp_after, _, _ = fingerprint()
   350|         rec["restore_fingerprint"] = fp_after
   351|         rec["restore_matches_baseline"] = (fp_after == baseline_fp)
   352|         m5a, _, _ = md5_prosrc()
   353|         rec["md5_prosrc_after"] = m5a
   354|         rec["md5_matches_baseline"] = (m5a == BASELINE_MD5)
   ...
   362|         if not rec["restore_matches_baseline"]:
   363|             hard_stop = ("fingerprint after restore of %s is %r, baseline is %r"
   364|                          % (eid, fp_after, baseline_fp))
```

* Line 349 — fingerprint read **after** `psql_bytes(rsql)` returned (line 335). Not before.
* Line 259 — `baseline_fp = fp`, assigned from the *precondition* fingerprint taken at line 202,
  before the capture and before any mutation. So the comparison target is the untouched baseline.
* Line 351 / 362 — the gate.
* Line 354 — md5 recorded, not gated.
* Line 411 — the run's exit code also requires `fp_final == baseline_fp`, `m5_final == BASELINE_MD5`
  and `not hard_stop`, so drift is caught a second time in the closing block and at the top-level
  return.

The restore itself is unconditional: `psql_bytes` is called at line 335 with no `if`, and a non-zero
`rrc` triggers one retry (lines 339–348) rather than a skip. There is no code path that reaches the
fingerprint read at line 349 while skipping the restore.

## The three variants + the control

Each variant starts from a measured baseline, applies the real `function_body_true` mutation from
`mutations.yaml` (committed), performs its restore through `executor.psql_bytes`, then runs the
gate. Every variant is repaired with the shipped correct mechanism before the next one.

| variant | restore mechanism | restore rc | md5 after apply → after restore | fp after restore | gate |
|---|---|---|---|---|---|
| 1 | `SELECT convert_from(decode(<b64>,'base64'),'UTF8');` — **the shipped defect** | 0 | `ad10d2d2` → `ad10d2d2` | `eabbaaff` | **FIRE** |
| 2 | correct mechanism, `$function$` body replaced by `SELECT true` | 0 | `ad10d2d2` → `241c389d` | `047d9898` | **FIRE** |
| 2b | correct mechanism, every CR stripped (510/20 → 490/0) | 0 | `ad10d2d2` → `23a5e9eb` | `891d74d6` | **FIRE** |
| 3 | `build_restore_sql('replay_pg_get_functiondef', capture)` → `psql_bytes` — **control** | 0 | `ad10d2d2` → `026fa63f` | `bfae0aea` | **PASS** |

Variant 1 is the historical defect reproduced exactly: psql exits **0**, stderr empty, and
`md5(prosrc)` is still `ad10d2d2…` afterwards — the database was never restored. The gate read
`eabbaaff…` against baseline `bfae0aea…` and fired:

```
HARD STOP: fingerprint after restore is 'eabbaaff560639f834e7ed76dcf46acf', baseline is 'bfae0aea057682e5403f70c94f0b5f61'
```

Variant 2b is the same restore text an operator would approve by eye — one that looks correct in any
editor — differing only in invisible CR bytes. It produces a *third* md5 (`23a5e9eb…`, matching the
value previously recorded for "replay the saved file as-is") and is caught. Variant 3 passing is
what makes the other three meaningful: the gate is not simply always-fail.

Each variant additionally asserts the gate fired **for the right reason** — `fired == (the database
actually drifted)`. It did, in all four.

## Final baseline proof

```
FINAL                  md5(prosrc)=026fa63f24c5f54584758c4f5d314408 fp=bfae0aea057682e5403f70c94f0b5f61 dep=98
prosrc stats (read): 510 chars, 20 CR
pg_proc attrs: 026fa63f24c5f54584758c4f5d314408 true search_path=public s u true postgres 14 target_account_id,min_role f 510 20
md5(prosrc) == baseline  : True
fingerprint == baseline  : True
dependent policies == 98 : True
```

The script exits 0 only if every variant behaved as required, the gate both fired and passed, and
the database ended at baseline.

## Reproduce

```
python C:/Users/FX-tec/AppData/Local/hermes/profiles/jeff/cache/scratch/restore_breaktest.py
```

Writes `C:/Users/FX-tec/AppData/Local/Temp/evidence/restore-breaktest.txt`. Imports `executor.py` and
`preflight6.py` and calls their real functions (`build_restore_sql`, `psql_bytes`, `fingerprint`,
`md5_prosrc`, `capture_definition`) — it never re-derives the fingerprint query or the restore
builder. Nothing was modified in the repo, the manifest, or either script.

## Notes for the campaign record

* The function is a **transport trap**, as the task brief warns: `prosrc` at baseline is 510 chars
  containing 20 real carriage returns. Only the verbatim captured bytes sent to `psql` stdin
  reproduce `026fa63f…`. Variant 2b is the standing proof that any editor-mediated, CR-normalising
  path is a silent mutation.
* If a future run leaves the DB off baseline, `restore_baseline.py` in the same scratch dir
  repairs it and verifies.