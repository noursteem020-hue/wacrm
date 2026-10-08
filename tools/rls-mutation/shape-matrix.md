# Measured shape matrix — supabase_db_wacrm, 2026-10-07, PostgreSQL 17.11

Measured by `measure_shapes.py` / `measure_all.py` (scratch), NOT inferred. Every
scenario is `BEGIN … ROLLBACK` for the probe body; the mutation is applied
committed and restored in a `finally`, and the fingerprint
(`bfae0aea057682e5403f70c94f0b5f61`) plus `md5(prosrc)`
(`026fa63f24c5f54584758c4f5d314408`) are compared after every restore.

Harness seed: 3 marker rows for A (2 on the main marker, 1 on the after-marker)
+ 1 for B + 1 pre-existing production row. The real section 9a seeds 2 for A, so
the counts below shift by one for the A-side; the SIGNS and the gating are the
part that carries over, and the acceptance runs re-measure the real file.

| shape | BASELINE | M4 update_with_check_true | M3 update_using_true | M7 update_policy_missing | M9 delete_using_true | M2 select_using_true | M1 rls_disabled |
|---|---|---|---|---|---|---|---|
| w1 in-place edit of own row (pos ctrl) | `ALLOWED rows=1` | `ALLOWED rows=1` | `ALLOWED rows=1` | `ALLOWED rows=0` | `ALLOWED rows=1` | `ALLOWED rows=1` | `ALLOWED rows=1` |
| w2 targeted cross-account move | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | `ALLOWED rows=0` | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | `ALLOWED rows=1` |
| w3 marker-only cross-account move | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | `ALLOWED rows=0` | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | `ALLOWED rows=2` |
| w4 unscoped column write | `ALLOWED rows=3` | `ALLOWED rows=3` | `ALLOWED rows=5` | `ALLOWED rows=0` | `ALLOWED rows=3` | `ALLOWED rows=3` | `ALLOWED rows=5` |
| w4 touched / leaked (as postgres) | 3 / 0 | 3 / 0 | 5 / **2** | 0 / 0 | 3 / 0 | 3 / 0 | 5 / **3** |
| w5 own-row edit after negatives | `ALLOWED rows=1` | `ALLOWED rows=1` | `ALLOWED rows=1` | `ALLOWED rows=0` | `ALLOWED rows=1` | `ALLOWED rows=1` | `ALLOWED rows=1` |
| **w6 unscoped cross-account MOVE (new, G1)** | `REJECTED_RLS rows=0` | **`ALLOWED rows=3`** | **`ALLOWED rows=5`** | `ALLOWED rows=0` | `REJECTED_RLS rows=0` | `REJECTED_RLS rows=0` | **`ALLOWED rows=5`** |
| w6 post A / B marker rows | 3 / 1 | **0 / 4** | **0 / 4** | 3 / 1 | 3 / 1 | 3 / 1 | **0 / 4** |
| d1 / d2 delete own rows (pos ctrls) | `1` / `1` | **`0` / `0`** | **`0` / `0`** | `1` / `1` | `1` / `1` | `1` / `1` | `1` / `1` |
| **d6 unscoped DELETE (new, G2)** | `ALLOWED rows=1` | `ALLOWED rows=0` | `ALLOWED rows=0` | `ALLOWED rows=1` | **`ALLOWED rows=3`** | `ALLOWED rows=1` | **`ALLOWED rows=3`** |
| d6 B marker rows after | **1** | 4 | 4 | **1** | **0** | **1** | **0** |
| d6 non-probe rows after | **1** | 1 | 1 | **1** | **0** | **1** | **0** |
| d6 whole table after | **2** | 5 | 5 | **2** | **0** | **2** | **0** |
| marker rows visible to ANON (wrong-role read) | 0 | 0 | 0 | 0 | 0 | **1** | 0 |

## What the matrix settles

**G1.** The UNSCOPED cross-account move is gated by `contacts_update` alone —
`USING` scopes the scan and `WITH CHECK` gates the resulting row. With
`WITH CHECK (true)` and `USING` intact, A's rows move to B (`A-owned 3 -> 0`,
`B-owned 1 -> 4`). The targeted shapes (w2, w3) stay `REJECTED_RLS rows=0`
under the same mutation: with a `WHERE`, the scan must READ the row, so
`contacts_select` applies and `is_account_member(B)` rejects it. That is why the
targeted shapes could never catch M4 and only the unscoped shape can.

**G2.** A `WHERE`d DELETE needs read privilege, so `contacts_select` still scopes
the scan and every `WHERE`d shape reads `rows=0` — including under
`delete_using_true`. The UNSCOPED DELETE needs no read privilege: under M9 it
removes 3 rows, including B's probe row and the pre-existing production row. The
per-account census read as postgres is what sees it; read as anon it is 0 in
every scenario.

**G3.** Under M7 (`contacts_update` dropped) every UPDATE shape returns
`ALLOWED rows=0`: nothing matched the scan. The old `LIKE 'ALLOWED%'` predicate
reported that as a leak. `rows > 0` is the correct predicate, and `rows = 0`
with no 42501 is a NARROWER policy, correctly reported by the positive-control
line and the drift line instead.

**Instrument for w6/d6.** `(rows > 0) = (the census shows an ownership change)`:
AGREE in every scenario above, so it can only fire when the census or the
statement's own count is broken — which is what the M-inst probe removes.
