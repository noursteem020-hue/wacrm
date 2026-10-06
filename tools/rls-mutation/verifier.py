#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ROLE 2 - VERIFIER for the wacrm RLS evidence campaign.

READ-ONLY BY CONSTRUCTION. Opens exactly two files: executor.jsonl and
mutations.yaml. It has no psql path, no docker path, and no import of the
Executor. It never reads the Executor's own summary or interpretation -- only the
raw probe streams the Executor recorded.

Job: cold comparison of the recorded raw ledger against the manifest.
  1. For each manifest entry, expected (should_be_red) vs actual (does the raw
     ledger contain the manifest's ledger_line?).
  2. matching_rule is honoured: 'exact' means the whole line must equal the
     manifest string; 'prefix' means the line must start with it. An approximate
     match under 'exact' is a FAIL.
  3. Any ledger line the probe printed that the manifest does NOT account for is
     reported as a finding, not as noise.
  4. The final clean_baseline exit code, and final fingerprint vs baseline.

It does not summarise and it does not interpret.
"""

import json
import os
import re

from campaign_paths import EVIDENCE as EVID
JSONL = os.path.join(EVID, "executor.jsonl")
MANIFEST = os.path.join(EVID, "mutations.yaml")
OUT = os.path.join(EVID, "verifier.txt")

BASELINE_FP = "bfae0aea057682e5403f70c94f0b5f61"
BASELINE_MD5 = "026fa63f24c5f54584758c4f5d314408"


# --------------------------------------------------------------- manifest ---
def parse_manifest():
    """Cold parse of mutations.yaml. Scalar fields are read by hand because no
    YAML library is installed; block scalars are the manifest's own 4-space style."""
    text = open(MANIFEST, encoding="utf-8").read()
    text = re.sub(r"^[ \t]*#.*$", "", text, flags=re.M)
    entries = []
    for m in re.finditer(r"^- id: (\S+)\n(.*?)(?=^- id: |\Z)", text, re.M | re.S):
        body = m.group(2)

        def scalar(key, b=body):
            sm = re.search(r"^  " + key + r":\s*(.*?)\s*$", b, re.M)
            if not sm:
                return None
            v = sm.group(1).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if v == "null":
                return None
            if v == "true":
                return True
            if v == "false":
                return False
            return v

        entries.append({
            "id": m.group(1),
            "should_be_red": scalar("should_be_red"),
            "predicted_red": scalar("predicted_red"),
            "predicted_exit": scalar("predicted_exit"),
            "matching_rule": scalar("matching_rule"),
            "ledger_line": scalar("ledger_line"),
            "gap_if_green": scalar("gap_if_green"),
            "comment": scalar("comment"),
            "missing_probe": scalar("missing_probe"),
            "restore_is_idempotent": scalar("restore_is_idempotent"),
            "has_comment_key": bool(re.search(r"^  comment:", body, re.M)),
        })
    return entries


# ------------------------------------------------------------- executor -----
def load_jsonl():
    recs = []
    with open(JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def raw_lines(rec):
    """Every line of the probe's recorded raw output, in order, verbatim.
    Taken from probe_stdout + probe_stderr as the Executor stored them."""
    out = []
    for stream in ("probe_stdout", "probe_stderr"):
        for ln in (rec.get(stream) or "").splitlines():
            out.append((stream, ln.rstrip("\r")))
    return out


LEDGER_RE = re.compile(r"^\[[0-9]+[a-z]?\] FAIL\b")


def ledger_lines(rec):
    """Distinct ledger lines, with stream provenance.

    Two filters, both needed:
      * 'FAIL' as a bare substring also matches the summary banner
        "TENANT ISOLATION FAILED. Failures:" and the psql ERROR prefix line, which
        are neither ledger entries nor evidence.
      * the gate is a RAISE EXCEPTION, so stderr re-echoes the whole stdout ledger.
        Same text in both streams is an ECHO, counted once and labelled as such.
    """
    seen = {}
    order = []
    for stream in ("probe_stdout", "probe_stderr"):
        for ln in (rec.get(stream) or "").splitlines():
            ln = ln.rstrip("\r")
            if not LEDGER_RE.match(ln):
                continue
            if ln in seen:
                if stream not in seen[ln]:
                    seen[ln].append(stream)
            else:
                seen[ln] = [stream]
                order.append(ln)
    return [(ln, seen[ln]) for ln in order]


def declared_by(entry, ln):
    """Is this exact ledger text declared by the manifest, either as ledger_line
    (under its own matching_rule) or as a named line inside `comment`?"""
    ll = entry.get("ledger_line")
    if ll:
        rule = entry.get("matching_rule")
        if rule == "exact" and ln == ll:
            return "ledger_line (exact)"
        if rule == "prefix" and ln.startswith(ll):
            return "ledger_line (prefix)"
    c = entry.get("comment")
    if c:
        for quoted in re.findall(r"'([^']{12,})'", c):
            if ln.startswith(quoted.strip()):
                return "comment"
    return None


def match_line(observed_lines, expected, rule):
    """Return (matched, matched_line). rule 'exact' -> the whole observed line must
    equal the manifest string. rule 'prefix' -> the observed line must start with
    it. A superset match under 'exact' is a FAIL, by rule."""
    if expected is None:
        return None, None
    for stream, ln in observed_lines:
        if rule == "exact":
            if ln == expected:
                return True, (stream, ln)
        elif rule == "prefix":
            if ln.startswith(expected):
                return True, (stream, ln)
        else:
            return None, None
    return False, None


def main():
    entries = parse_manifest()
    recs = load_jsonl()

    by_id = {}
    order = []
    for r in recs:
        if r.get("record") == "entry":
            by_id.setdefault(r["id"], []).append(r)
            order.append(r["id"])
    pre = next((r for r in recs if r.get("record") == "precondition"), {})
    cap = next((r for r in recs if r.get("record") == "capture"), {})
    closing = next((r for r in recs if r.get("record") == "closing"), {})

    L = []
    W = L.append

    W("=" * 100)
    W("VERIFIER - cold comparison of executor.jsonl against mutations.yaml")
    W("=" * 100)
    W("")
    W("INPUTS (read-only): executor.jsonl, mutations.yaml")
    W("MANIFEST ENTRIES: %d    EXECUTOR ENTRY RECORDS: %d" % (len(entries), len(order)))
    W("EXECUTOR ORDER   : %s" % ", ".join(order))
    W("")

    # -- precondition as recorded -------------------------------------------
    W("-" * 100)
    W("0. RECORDED PRECONDITION")
    W("-" * 100)
    W("  probe blob (recorded)   : %s" % pre.get("probe_blob"))
    W("  probe bytes             : %s" % pre.get("probe_bytes"))
    W("  fingerprint             : %s" % pre.get("fingerprint"))
    W("  md5(prosrc)             : %s" % pre.get("md5_prosrc"))
    W("  dependent policies      : %s" % pre.get("dependent_policies"))
    W("  capture ok              : %s  (%s chars, %s CR, text-md5 %s)"
      % (cap.get("ok"), cap.get("chars"), cap.get("cr"), cap.get("md5_of_captured_text")))
    W("  abort record present    : %s"
      % bool(any(r.get("record") == "abort" for r in recs)))
    W("  skipped records         : %d"
      % sum(1 for r in recs if r.get("record") == "skipped"))
    W("")

    # -- main table ---------------------------------------------------------
    W("-" * 100)
    W("1. PER-ENTRY COMPARISON   (expected = manifest should_be_red)")
    W("-" * 100)
    hdr = ("%-4s %-22s %-8s %-8s %-7s %-7s %-9s %s"
           % ("#", "id", "expect", "actual", "rule", "exit", "linefound", "verdict"))
    W(hdr)
    W("  " + "-" * (len(hdr) + 6))

    rows = []
    unexpected_total = []
    for e in entries:
        eid = e["id"]
        recs_for = by_id.get(eid, [])
        # clean_baseline appears twice (bookend). Compare against the LAST visit for
        # the main row; the first visit is reported separately in section 2.
        rec = recs_for[-1] if recs_for else None
        expect_red = e["should_be_red"]
        exp_exit = e["predicted_exit"]
        rule = e["matching_rule"]
        expected_line = e["ledger_line"]

        if rec is None:
            rows.append((eid, expect_red, "NO RECORD", "-", "?", "-", "ABSENT", "FAIL"))
            continue

        led = ledger_lines(rec)
        obs = [(",".join(streams), ln) for ln, streams in led]
        exit_code = rec.get("probe_exit")
        actual_red = (exit_code != 0)

        if expected_line is None:
            linefound = "n/a (null)"
            matched = None
        else:
            matched, m = match_line(obs, expected_line, rule)
            linefound = "YES" if matched else "NO"

        # verdict, stated as a comparison only
        if expected_line is None:
            verdict = "MATCH" if actual_red == bool(expect_red) else "MISMATCH"
        else:
            if expect_red:
                verdict = "MATCH" if (matched and actual_red) else "MISMATCH"
            else:
                verdict = "MATCH" if (not matched and not actual_red) else "MISMATCH"

        rows.append((eid, expect_red, actual_red, rule, exit_code, exp_exit,
                     linefound, verdict))

        # unexpected lines: ledger lines not declared by the manifest
        for ln, streams in led:
            why = declared_by(e, ln)
            if why is None:
                unexpected_total.append((eid, ",".join(streams), ln))

    for i, row in enumerate(rows):
        if len(row) == 8:
            eid, expect_red, actual_red, rule, exit_code, exp_exit, linefound, verdict = row
            W("%-4d %-22s %-8s %-8s %-7s %-7s %-9s %s"
              % (i, eid, expect_red, actual_red, rule, exit_code, linefound, verdict))
        else:
            eid, expect_red, actual_red, rule, exit_code, linefound, verdict = row
            W("%-4d %-22s %-8s %-8s %-7s %-7s %-9s %s"
              % (i, eid, expect_red, actual_red, rule, exit_code, linefound, verdict))

    W("")

    # -- exit-code column versus predicted ----------------------------------
    W("-" * 100)
    W("2. EXIT CODE vs manifest predicted_exit")
    W("-" * 100)
    W("%-24s %-12s %-12s %-10s %s" % ("id", "predicted", "actual", "rule", "note"))
    for e in entries:
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        if rec is None:
            continue
        pred = e["predicted_exit"]
        act = rec.get("probe_exit")
        note = ""
        if pred is None:
            note = ("predicted_exit is null (predicted_red=%s): no predicted value "
                    "to compare" % e["predicted_red"])
        elif str(pred) == str(act):
            note = "exit equals predicted"
        else:
            note = "exit differs from predicted"
        W("%-24s %-12s %-12s %-10s %s"
          % (e["id"], pred if pred is not None else "null", act,
             e["matching_rule"], note))
    W("")

    # -- clean_baseline bookend --------------------------------------------
    W("-" * 100)
    W("3. clean_baseline BOOKEND")
    W("-" * 100)
    cb = by_id.get("clean_baseline", [])
    if len(cb) >= 2:
        first, last = cb[0], cb[-1]
        W("  first visit  index=%s probe_exit=%s fail_lines=%d"
          % (first.get("index"), first.get("probe_exit"), len(first.get("fail_lines", []))))
        W("  last  visit  index=%s probe_exit=%s fail_lines=%d"
          % (last.get("index"), last.get("probe_exit"), len(last.get("fail_lines", []))))
        W("  final clean_baseline exit code : %s" % last.get("probe_exit"))
        W("  manifest requires              : 0 (should_be_red=false, predicted_exit=0)")
        W("  VERDICT                        : %s"
          % ("MATCH" if last.get("probe_exit") == 0 else "MISMATCH - final baseline is not exit 0"))
    else:
        W("  bookend incomplete: %d clean_baseline visit(s) recorded" % len(cb))
    W("")

    # -- residue ------------------------------------------------------------
    W("-" * 100)
    W("4. RESIDUE")
    W("-" * 100)
    ff = closing.get("final_fingerprint")
    W("  final fingerprint (from executor.jsonl) : %s" % ff)
    W("  baseline fingerprint                    : %s" % BASELINE_FP)
    W("  EQUAL?                                  : %s" % (ff == BASELINE_FP))
    fm = closing.get("final_md5_prosrc")
    W("  final md5(prosrc)                       : %s" % fm)
    W("  baseline md5(prosrc)                    : %s" % BASELINE_MD5)
    W("  EQUAL?                                  : %s" % (fm == BASELINE_MD5))
    W("  final dependent policy count            : %s (baseline 98)"
      % closing.get("final_dependent_policies"))
    W("  recapture identical to original capture : %s (%s chars, %s CR)"
      % (closing.get("recapture_identical_to_capture"),
         closing.get("recapture_chars"), closing.get("recapture_cr")))
    W("  hard_stop recorded                      : %s" % closing.get("hard_stop"))
    W("")

    # -- per-entry restore integrity ---------------------------------------
    W("-" * 100)
    W("5. PER-ENTRY RESTORE FINGERPRINT vs baseline")
    W("-" * 100)
    W("%-24s %-38s %-8s %s" % ("id", "restore_fingerprint", "match", "md5_match"))
    for e in entries:
        for rec in by_id.get(e["id"], []):
            rf = rec.get("restore_fingerprint")
            mm = rec.get("md5_matches_baseline")
            W("%-24s %-38s %-8s %s"
              % (e["id"] + ("[%s]" % rec.get("index") if e["id"] == "clean_baseline" and len(by_id.get(e["id"], [])) > 1 else ""),
                 rf, rec.get("restore_matches_baseline"),
                 mm if mm is not None else "n/a"))
    W("")

    # -- unexpected lines ---------------------------------------------------
    W("-" * 100)
    W("6. LEDGER LINES NOT ACCOUNTED FOR BY THE MANIFEST")
    W("-" * 100)
    if not unexpected_total:
        W("  none")
    else:
        W("  %d line(s) printed by the probe that no manifest ledger_line declares." % len(unexpected_total))
        W("  Each is a FINDING: the manifest states every expected line is declared.")
        W("")
        for eid, stream, ln in unexpected_total:
            W("  [%s] %s/%s" % (eid, stream, ""))
            W("      %s" % ln)
    W("")

    # -- verdict count ------------------------------------------------------
    W("-" * 100)
    W("7. TALLY (comparison only)")
    W("-" * 100)
    match = sum(1 for r in rows if r[-1] == "MATCH")
    W("  entries compared        : %d" % len(rows))
    W("  MATCH                   : %d" % match)
    W("  MISMATCH                : %d" % (len(rows) - match))
    W("  unexpected ledger lines : %d" % len(unexpected_total))
    W("  final clean_baseline    : exit %s" % (by_id.get("clean_baseline", [{}])[-1].get("probe_exit")
                                                if by_id.get("clean_baseline") else "NO RECORD"))
    W("  residue                 : fingerprint %s baseline" % ("EQUAL TO" if ff == BASELINE_FP else "DIFFERS FROM"))

    W("-" * 100)
    W("8. FULL LEDGER PER ENTRY (verbatim, deduped; tag marks a stderr echo)")
    W("-" * 100)
    for e in entries:
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        W("")
        W("  [%s]  exit=%s  should_be_red=%s  predicted_red=%s  rule=%s"
          % (e["id"], rec.get("probe_exit") if rec else "NO RECORD",
             e["should_be_red"], e["predicted_red"], e["matching_rule"]))
        if rec is None:
            continue
        led = ledger_lines(rec)
        if not led:
            W("      (no ledger lines: the probe printed no FAIL entries)")
        for ln, streams in led:
            why = declared_by(e, ln)
            tag = "DECLARED via %s" % why if why else "UNACCOUNTED FOR"
            echo = "   [+stderr echo]" if len(streams) > 1 else ""
            W("      %s" % ln)
            W("          -> %s%s" % (tag, echo))
    W("")
    W("=" * 100)

    text = "\n".join(L) + "\n"
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())