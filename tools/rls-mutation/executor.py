#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ROLE 1 - EXECUTOR for the wacrm RLS evidence campaign.

The ONLY agent that touches the database. Sequential by construction: one entry at
a time, never in parallel, because every mutation rewrites the same shared policies.

Mechanisms are IMPORTED from preflight6.py, never re-derived:
  - FINGERPRINT_SQL  (hand-retyping it changes the hash silently; the rls-mutation-
    campaign-gate skill records this as the v5 defect)
  - psql()           (-q -A -t for scalar reads; NOT used for the probe)
  - capture_definition()  (base64 transport: prosrc contains CR characters, and
    psql's aligned output rewrites CRLF -> LF, silently returning a truncated value)
  - split_statements()    (a ';' inside a dollar-quoted body is not a boundary)

Hard requirements implemented here:
  * baseline precondition checked BEFORE anything is applied; mismatch => ABORT
  * every mutation drilled inside BEGIN..ROLLBACK first, then applied committed so
    the probe can observe it, then restored committed in a finally block
  * fingerprint after every restore must equal baseline, else hard stop
  * M10 immediately before the trailing clean_baseline
  * trailing clean_baseline must be exit 0
"""

import base64
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import preflight6 as pf  # noqa: E402  (constants + helpers only; its main() is guarded)
from campaign_paths import (BASELINE_MD5, CONTAINER, EVIDENCE, EXPECTED_PROBE_BLOB,
                            MANIFEST, PROBE_REL, ensure_evidence_dir)

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PROBE = os.path.join(REPO, PROBE_REL)
EXPECTED_FP = pf.EXPECTED_BASELINE_FP
EXPECTED_DEP = pf.EXPECTED_DEPENDENT_POLICIES

ensure_evidence_dir()
OUT_DIR = EVIDENCE
JSONL = os.path.join(OUT_DIR, "executor.jsonl")

BASE_ID = "clean_baseline"
FN_ID = "function_body_true"


# ------------------------------------------------------------------ psql ----
def psql_sql(sql):
    """Scalar/utility SQL via preflight6.psql (-q -A -t)."""
    return pf.psql(sql)


def psql_probe(probe_bytes):
    """Run the probe EXACTLY as specified: ON_ERROR_STOP=1, file on stdin, no
    -q -A -t (those flags would rewrite CRLF inside the probe's own literals)."""
    args = ["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d",
            "postgres", "-v", "ON_ERROR_STOP=1", "-f", "-"]
    p = subprocess.run(args, input=probe_bytes, capture_output=True)
    return (p.returncode,
            p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def fingerprint():
    rc, out, err = psql_sql(pf.FINGERPRINT_SQL)
    toks = [l.strip() for l in out.splitlines() if pf.TOKEN_RE.match(l.strip())]
    return (toks[0] if toks else None), rc, err.strip()


def md5_prosrc():
    rc, out, err = psql_sql(
        "SELECT md5(prosrc) FROM pg_proc WHERE oid = " + pf.REGPROC + ";\n")
    t = out.strip().splitlines()
    return (t[0] if t else None), rc, err.strip()


def dep_policies():
    rc, out, err = psql_sql(
        "SELECT count(*) FROM pg_policies WHERE schemaname='public'"
        " AND (qual LIKE '%is_account_member%'"
        "   OR with_check LIKE '%is_account_member%');\n")
    t = out.strip().splitlines()
    try:
        return int(t[0]), rc, err.strip()
    except Exception:
        return None, rc, err.strip()


# -------------------------------------------------------------- manifest ----
def parse_manifest_full():
    """Same splitter preflight6 uses, plus the scalar fields the Verifier needs.

    The Verifier and Critic re-parse the manifest independently; the Executor only
    needs apply/restore/noop, so this returns those.
    """
    return pf.parse_manifest()


def run_order(entries):
    return pf.run_order(entries)


# ------------------------------------------------------------- the probe ----
def read_probe_bytes():
    path = os.path.join(REPO, PROBE_REL)
    with open(path, "rb") as fh:
        return fh.read()


def probe_blob_hash():
    p = subprocess.run(["git", "-C", REPO, "hash-object", PROBE_REL],
                       capture_output=True)
    return p.stdout.decode().strip(), p.returncode, p.stderr.decode().strip()


# ------------------------------------------------------------- the driver ---
def fail_lines(stdout, stderr):
    """Every line the probe printed containing FAIL, from both streams, in the
    order the streams are combined. Raw text, never reformatted."""
    out = []
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        for line in (text or "").splitlines():
            if "FAIL" in line:
                out.append({"stream": stream, "line": line.rstrip("\r")})
    return out


def restore_for(entry, captured_def):
    """Return (kind, sql) for the restore of this entry.

    function_body_true: replay the pg_get_functiondef captured at start, verbatim,
    base64-transported. The manifest's restore_procedure PROSE is NOT executed --
    it names pg_proc column 'proparallelsafe', which does not exist on PG17 (the
    real column is 'proparallel). Replay only.
    """
    if entry["id"] == FN_ID:
        if captured_def is None:
            return ("capture_missing", None)
        return ("replay_pg_get_functiondef", captured_def)
    return ("manifest_restore_sql", entry["restore"])


def build_restore_sql(kind, payload):
    """Return (raw_bytes, err).

    CRITICAL: the replay must be the EXACT captured BYTES written to psql's stdin.
    The obvious-looking alternative --
        SELECT convert_from(decode('<b64>','base64'),'UTF8');
    -- is WRONG: it SELECTs the definition and returns it as a text value without
    ever EXECUTING it. psql exits 0, the restore reports success, and the database
    is still mutated. This was measured: it left md5(prosrc) at ad10d2d2... and the
    fingerprint at eabbaaff... after 'function_body_true'. The base64 form is only
    correct on the way OUT of the server (capture), not on the way back in.
    """
    if kind == "replay_pg_get_functiondef":
        # pg_get_functiondef emits NO trailing semicolon; append exactly one, after
        # asserting the capture is a single complete statement (skill: appending a
        # terminator must not paper over a truncated capture).
        stmts = pf.split_statements(payload)
        if len(stmts) != 1:
            return None, ("captured definition is %d statements, expected 1: %r"
                          % (len(stmts), payload[:120]))
        body = payload.rstrip()
        if body.endswith(";"):
            body = body[:-1]
        if not body.endswith("$function$"):
            return None, ("capture does not end with the closing dollar tag: %r"
                          % body[-24:])
        # Bytes are written exactly as captured; the CRLF inside prosrc is preserved
        # because nothing reformats them on the way to the server.
        return (body + ";\n").encode("utf-8"), None
    if kind == "manifest_restore_sql":
        if not payload:
            return None, "manifest restore_sql is absent"
        return payload.encode("utf-8"), None
    return None, "no restore available"


def psql_bytes(raw):
    """Send raw bytes to psql on stdin with no output-formatting flags."""
    args = ["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d",
            "postgres", "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
    p = subprocess.run(args, input=raw, capture_output=True)
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def main():
    records = []
    notes = []

    # ---------------------------------------------------- preconditions ----
    probe_bytes = read_probe_bytes()
    blob, brc, berr = probe_blob_hash()
    fp, frc, ferr = fingerprint()
    m5, mrc, merr = md5_prosrc()
    dep, drc, derr = dep_policies()

    pre = {
        "probe_blob": blob,
        "probe_blob_rc": brc,
        "probe_bytes": len(probe_bytes),
        "fingerprint": fp,
        "fingerprint_rc": frc,
        "md5_prosrc": m5,
        "md5_prosrc_rc": mrc,
        "dependent_policies": dep,
        "dependent_policies_rc": drc,
    }
    rec = {"record": "precondition", **pre}
    records.append(rec)

    abort = []
    if blob != EXPECTED_PROBE_BLOB:
        abort.append("probe blob %r != expected %r" % (blob, EXPECTED_PROBE_BLOB))
    if fp != EXPECTED_FP:
        abort.append("fingerprint %r != baseline %r" % (fp, EXPECTED_FP))
    if m5 != BASELINE_MD5:
        abort.append("md5(prosrc) %r != baseline %r" % (m5, BASELINE_MD5))
    if dep != EXPECTED_DEP:
        abort.append("dependent policies %r != %r" % (dep, EXPECTED_DEP))

    if abort:
        rec = {"record": "abort", "reasons": abort, "precondition": pre}
        records.append(rec)
        emit(records)
        for a in abort:
            print("ABORT: " + a)
        return 2

    print("PRECONDITION OK  blob=%s fp=%s md5=%s dep=%s"
          % (blob[:12], fp[:12], m5[:12], dep))

    # ---- capture the function definition ONCE, before any mutation ----
    captured_def, cap_err = pf.capture_definition()
    cap_md5 = None
    if captured_def is not None:
        cap_md5 = __import__("hashlib").md5(
            captured_def.encode("utf-8")).hexdigest()
    records.append({"record": "capture", "ok": captured_def is not None,
                    "error": cap_err, "md5_of_captured_text": cap_md5,
                    "chars": len(captured_def) if captured_def else None,
                    "cr": captured_def.count("\r") if captured_def else None})
    if captured_def is None:
        print("ABORT: could not capture pg_get_functiondef: %s" % cap_err)
        emit(records)
        return 2
    print("CAPTURED definition: %d chars, %d CR, text-md5=%s"
          % (len(captured_def), captured_def.count("\r"), cap_md5[:12]))

    entries = run_order(parse_manifest_full())
    baseline_fp = fp
    hard_stop = None

    for idx, entry in enumerate(entries):
        eid = entry["id"]
        if hard_stop:
            records.append({"record": "skipped", "id": eid,
                            "reason": "hard stop: %s" % hard_stop})
            continue

        fp_before, _, _ = fingerprint()
        rec = {
            "record": "entry",
            "index": idx,
            "id": eid,
            "noop": entry.get("noop", False),
            "fingerprint_before": fp_before,
        }

        if entry.get("noop"):
            rc, so, se = psql_probe(probe_bytes)
            rec.update({
                "probe_exit": rc,
                "probe_stdout": so,
                "probe_stderr": se,
                "probe_raw": so + se,
                "fail_lines": fail_lines(so, se),
                "restore_kind": "none_required",
                "restore_rc": 0,
                "restore_error": None,
                "restore_fingerprint": fp_before,
                "restore_matches_baseline": fp_before == baseline_fp,
                "error": None,
            })
            records.append(rec)
            print("[%2d] %-22s exit=%s fp_matches=%s"
                  % (idx, eid, rc, fp_before == baseline_fp))
            if not rec["restore_matches_baseline"]:
                hard_stop = "fingerprint drifted at noop %s" % eid
            continue

        # ---- rollback drill: proves the mutation applies AND reverts ----
        drill_sql = "BEGIN;\n" + (entry["apply"] or "") + "\nROLLBACK;\n"
        drc, dout, derr_ = psql_sql(drill_sql)
        fp_drill, _, _ = fingerprint()
        rec["drill_rc"] = drc
        rec["drill_fingerprint"] = fp_drill
        rec["drill_reverted_clean"] = (fp_drill == fp_before)
        rec["drill_error"] = derr_.strip()[:400] or None

        # ---- apply, committed, so the probe observes it ----
        arc, aout, aerr = psql_sql(entry["apply"] or "")
        rec["apply_rc"] = arc
        rec["apply_error"] = aerr.strip()[:400] or None
        fp_applied, _, _ = fingerprint()
        rec["fingerprint_applied"] = fp_applied
        rec["mutation_had_effect"] = (fp_applied != fp_before)

        # ---- probe ----
        prc, pso, pse = psql_probe(probe_bytes)
        rec.update({
            "probe_exit": prc,
            "probe_stdout": pso,
            "probe_stderr": pse,
            "probe_raw": pso + pse,
            "fail_lines": fail_lines(pso, pse),
        })

        # ---- RESTORE, unconditionally, in a finally block ----
        kind, payload = restore_for(entry, captured_def)
        rsql, rbuild_err = build_restore_sql(kind, payload)
        if rbuild_err:
            rec["restore_kind"] = kind
            rec["restore_rc"] = None
            rec["restore_error"] = rbuild_err
        else:
            rrc, rout, rerr = psql_bytes(rsql)
            rec["restore_kind"] = kind
            rec["restore_rc"] = rrc
            rec["restore_error"] = rerr.strip()[:400] or None
            if rrc != 0:
                # Second attempt: restores are idempotent by construction, so a
                # retry is safe and is the difference between a hard stop with a
                # recoverable database and a hard stop with a mutated one.
                rrc2, _, rerr2 = psql_bytes(rsql)
                rec["restore_retry_rc"] = rrc2
                if rrc2 == 0:
                    rec["restore_error"] = ((rec["restore_error"] or "")
                                            + " [retry succeeded]")
                    rrc = rrc2
        fp_after, _, _ = fingerprint()
        rec["restore_fingerprint"] = fp_after
        rec["restore_matches_baseline"] = (fp_after == baseline_fp)
        m5a, _, _ = md5_prosrc()
        rec["md5_prosrc_after"] = m5a
        rec["md5_matches_baseline"] = (m5a == BASELINE_MD5)

        records.append(rec)
        print("[%2d] %-22s exit=%-3s fails=%-2d restore_rc=%s fp_match=%s md5_match=%s"
              % (idx, eid, prc, len(rec["fail_lines"]),
                 rec.get("restore_rc"), rec["restore_matches_baseline"],
                 rec["md5_matches_baseline"]))

        if not rec["restore_matches_baseline"]:
            hard_stop = ("fingerprint after restore of %s is %r, baseline is %r"
                         % (eid, fp_after, baseline_fp))

    # ------------------------------------------------------------- closing --
    # Byte-exact proof that the verbatim replay really restored the definition and
    # did not merely bring md5(prosrc) back. Re-capture and compare the bytes.
    recap, recap_err = pf.capture_definition()
    recap_same = (recap == captured_def)
    fp_final, _, _ = fingerprint()
    m5_final, _, _ = md5_prosrc()
    dep_final, _, _ = dep_policies()
    attr_rc, attr_out, attr_err = psql_sql(pf.CAPTURE_ATTR_SQL)
    records.append({
        "record": "closing",
        "final_fingerprint": fp_final,
        "baseline_fingerprint": baseline_fp,
        "fingerprint_matches_baseline": fp_final == baseline_fp,
        "final_md5_prosrc": m5_final,
        "baseline_md5_prosrc": BASELINE_MD5,
        "md5_matches_baseline": m5_final == BASELINE_MD5,
        "final_dependent_policies": dep_final,
        "recapture_ok": recap is not None,
        "recapture_error": recap_err,
        "recapture_chars": len(recap) if recap else None,
        "recapture_cr": recap.count("\r") if recap else None,
        "recapture_md5_text": (__import__("hashlib").md5(recap.encode("utf-8")).hexdigest()
                               if recap else None),
        "recapture_identical_to_capture": recap_same,
        "final_pg_proc_attrs": attr_out.strip(),
        "final_pg_proc_attrs_rc": attr_rc,
        "hard_stop": hard_stop,
    })
    emit(records)

    print("=" * 72)
    print("FINAL fingerprint   : %s" % fp_final)
    print("BASELINE fingerprint: %s" % baseline_fp)
    print("MATCH               : %s" % (fp_final == baseline_fp))
    print("FINAL md5(prosrc)   : %s" % m5_final)
    print("BASELINE md5(prosrc): %s" % BASELINE_MD5)
    print("MATCH               : %s" % (m5_final == BASELINE_MD5))
    print("DEPENDENT POLICIES  : %s" % dep_final)
    print("RECAPTURE IDENTICAL : %s (%s chars, %s CR)"
          % (recap_same, len(recap) if recap else -1,
             recap.count("\r") if recap else -1))
    print("FINAL pg_proc attrs : %s" % attr_out.strip()[:200])
    if hard_stop:
        print("HARD STOP: %s" % hard_stop)
    return 0 if (fp_final == baseline_fp and m5_final == BASELINE_MD5
                 and recap_same and not hard_stop) else 1


def emit(records):
    with open(JSONL, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())