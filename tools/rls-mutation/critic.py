#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ROLE 3 - CRITIC for the wacrm RLS evidence campaign.

READ-ONLY BY CONSTRUCTION. Opens exactly two files: executor.jsonl and
mutations.yaml. No psql, no docker, no import of the Executor or the Verifier.

Its single question, per entry whose probe exit was non-zero:

    DID IT GO RED FOR THE EXPECTED REASON?

A non-zero exit proves nothing on its own: a SQL syntax error, a missing column and
a genuine isolation failure all produce non-zero. The evidence is the ledger. So for
every red run this classifies the run and cites the exact ledger line or the exact
error text that justifies the classification. A classification with no citation is
not output.

Classes:
  RIGHT REASON   the manifest's declared ledger_line is present (honouring its
                 matching_rule). The probe observed the hole it was written to see.
  FALSE RED      exit non-zero but the declared line is absent: the run went red via
                 a DIFFERENT ledger line, or via no ledger line at all (a raw SQL or
                 psql error masquerading as a security failure).
  GREEN AS EXPECTED  exit 0 and the manifest predicts green.

For predicted_red == 'unknown' the observed result is reported plainly and either
"the probe caught it" or "a coverage gap in the probe", in the manifest's own
gap_if_green terms. A green run is NEVER reported as proof of safety.
"""

import json
import os
import re

from campaign_paths import EVIDENCE as EVID
JSONL = os.path.join(EVID, "executor.jsonl")
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mutations.yaml")
OUT = os.path.join(EVID, "critic.txt")

LEDGER_RE = re.compile(r"^\[[0-9]+[a-z]?\] FAIL\b")


def parse_manifest():
    text = open(MANIFEST, encoding="utf-8").read()
    text = re.sub(r"^[ \t]*#.*$", "", text, flags=re.M)
    entries = []
    for m in re.finditer(r"^- id: (\S+)\n(.*?)(?=^- id: |\Z)", text, re.M | re.S):
        body = m.group(2)

        def block(key, b=body):
            bm = re.search(r"^  " + key + r": \|\n((?:    .*\n)+)", b, re.M)
            if not bm:
                return None
            return "\n".join(l[4:] for l in bm.group(1).rstrip("\n").splitlines())

        def scalar(key, b=body):
            sm = re.search(r"^  " + key + r":\s*(.*?)\s*$", b, re.M)
            if not sm:
                return None
            v = sm.group(1).strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            return None if v == "null" else v

        entries.append({
            "id": m.group(1),
            "should_be_red": scalar("should_be_red") == "true",
            "predicted_red": scalar("predicted_red"),
            "matching_rule": scalar("matching_rule"),
            "ledger_line": scalar("ledger_line"),
            "gap_if_green": scalar("gap_if_green"),
            "missing_probe": scalar("missing_probe"),
            "comment": scalar("comment"),
            "restore_procedure": block("restore_procedure"),
        })
    return entries


def load_jsonl():
    recs = []
    with open(JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    return recs


def ledger(rec):
    """Distinct ledger lines the probe printed, first-appearance order."""
    seen, order = set(), []
    for stream in ("probe_stdout", "probe_stderr"):
        for ln in (rec.get(stream) or "").splitlines():
            ln = ln.rstrip("\r")
            if LEDGER_RE.match(ln) and ln not in seen:
                seen.add(ln)
                order.append(ln)
    return order


def errors(rec):
    """Non-ledger error text: psql/ERROR lines and RAISE EXCEPTION messages."""
    out = []
    for ln in (rec.get("probe_stderr") or "").splitlines():
        ln = ln.rstrip("\r")
        if re.search(r"\b(ERROR|FATAL)\b", ln) and not LEDGER_RE.match(ln):
            out.append(ln)
    return out


def matches(expected, observed, rule):
    if expected is None:
        return False
    for ln in observed:
        if rule == "exact" and ln == expected:
            return True
        if rule == "prefix" and ln.startswith(expected):
            return True
    return False


def wrap(s, width=96, indent=8):
    pad = " " * indent
    words, line, lines = s.split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w) if line else w
    if line:
        lines.append(line)
    return ("\n" + pad).join(lines)


def main():
    entries = parse_manifest()
    recs = load_jsonl()
    by_id = {}
    for r in recs:
        if r.get("record") == "entry":
            by_id.setdefault(r["id"], []).append(r)
    closing = next((r for r in recs if r.get("record") == "closing"), {})

    L = []
    W = L.append

    W("=" * 100)
    W("CRITIC - did each red run go red for the EXPECTED reason?")
    W("=" * 100)
    W("")
    W("INPUTS (read-only): executor.jsonl, mutations.yaml")
    W("A non-zero probe exit proves nothing by itself: a SQL typo and a real")
    W("cross-tenant leak both exit non-zero. The ONLY evidence is the ledger.")
    W("")

    # ---------------------------------------------------------------- 1 ----
    W("-" * 100)
    W("1. EVERY NON-ZERO PROBE EXIT, CLASSIFIED WITH CITATION")
    W("-" * 100)
    red_count = right = false_red = 0
    for e in entries:
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        if rec is None:
            continue
        exit_code = rec.get("probe_exit")
        if exit_code == 0:
            continue
        red_count += 1
        led = ledger(rec)
        errs = errors(rec)
        eid = e["id"]
        rule = e["matching_rule"]
        want = e["ledger_line"]

        W("")
        W("  [%s]  probe_exit=%s" % (eid, exit_code))
        W("      expected line (%s): %r" % (rule, want))
        if want and matches(want, led, rule):
            right += 1
            cls = "RIGHT REASON"
            W("      CLASSIFICATION  : %s" % cls)
            W("      CITATION        : the declared line is present verbatim in the "
              "raw ledger.")
            for ln in led:
                if (rule == "exact" and ln == want) or (rule == "prefix" and ln.startswith(want)):
                    W("        probe printed -> %s" % ln)
                    break
            if len(led) > 1:
                W("      also printed   : %d further ledger line(s); the classification "
                  "rests on the declared one, not on the count." % (len(led) - 1))
        else:
            false_red += 1
            cls = "FALSE RED"
            W("      CLASSIFICATION  : %s" % cls)
            W("      CITATION        : the declared line is ABSENT from the ledger. "
              "The run is red, but not for the reason the manifest declares.")
            if led:
                W("      red instead via : %s" % " | ".join(led[:4]))
            else:
                W("      red instead via : NO ledger line at all -- the run went red "
                  "without ever printing a FAIL entry.")
            for en in errs[:4]:
                W("      error text      : %s" % en)
        # Was the red legitimate for this entry at all?
        if e["should_be_red"]:
            W("      manifest says   : should_be_red=true (probe SHOULD be red here)")
        else:
            W("      manifest says   : should_be_red=false, yet the probe went red")
    W("")
    W("  red runs examined : %d" % red_count)
    W("  RIGHT REASON      : %d" % right)
    W("  FALSE RED         : %d" % false_red)
    W("")

    # ---------------------------------------------------------------- 2 ----
    W("-" * 100)
    W("2. ENTRIES THE MANIFEST PREDICTS GREEN THAT WENT RED  (is the prediction wrong?)")
    W("-" * 100)
    wrong = 0
    for e in entries:
        pr = e["predicted_red"]
        if pr not in ("false",):
            continue
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        if rec is None:
            continue
        if rec.get("probe_exit") == 0:
            continue
        wrong += 1
        led = ledger(rec)
        W("")
        W("  [%s]  predicted_red=false but probe_exit=%s" % (e["id"], rec.get("probe_exit")))
        W("      THE MANIFEST'S PREDICTION WAS WRONG.")
        for ln in led[:6]:
            W("        ledger -> %s" % ln)
        for en in errors(rec)[:3]:
            W("        error  -> %s" % en)
    if not wrong:
        W("  none. Every entry with predicted_red=false exited 0 as predicted:")
        for e in entries:
            if e["predicted_red"] == "false":
                recs_for = by_id.get(e["id"], [])
                rec = recs_for[-1] if recs_for else None
                W("      %-24s exit=%s  (no ledger line printed, no error text)"
                  % (e["id"], rec.get("probe_exit") if rec else "NO RECORD"))
    W("")
    W("  count: %d" % wrong)
    W("")

    # ---------------------------------------------------------------- 3 ----
    W("-" * 100)
    W("3. predicted_red == 'unknown'  (observed result, classified in the manifest's terms)")
    W("-" * 100)
    for e in entries:
        if e["predicted_red"] != "unknown":
            continue
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        if rec is None:
            continue
        led = ledger(rec)
        ec = rec.get("probe_exit")
        eid = e["id"]
        W("")
        W("  [%s]  probe_exit=%s   ledger lines printed: %d" % (eid, ec, len(led)))
        W("      declared line : %r" % e["ledger_line"])
        if ec != 0 and matches(e["ledger_line"], led, e["matching_rule"]):
            W("      CLASSIFICATION  : THE PROBE CAUGHT IT.")
            for ln in led:
                if ln.startswith(e["ledger_line"]):
                    W("        citation -> %s" % ln)
                    break
            W("      Note: this entry was NOT MEASURED before this run; the result is "
              "observed, and it is a positive observation of detection.")
        elif ec == 0:
            W("      CLASSIFICATION  : A COVERAGE GAP IN THE PROBE.")
            W("        citation -> the probe exited 0 and printed %d ledger line(s); the "
              "declared line %r" % (len(led), e["ledger_line"]))
            W("                      never appeared." )
            W("      THIS IS NOT EVIDENCE THAT THE DELETE POLICY IS SAFE. The manifest's")
            W("      gap_if_green, verbatim:")
            W("        " + wrap(e["gap_if_green"] or "", indent=8))
            if e["comment"]:
                W("      manifest comment, verbatim:")
                W("        " + wrap(e["comment"], indent=8))
        else:
            W("      CLASSIFICATION  : red, but NOT via the declared line -> FALSE RED.")
            for ln in led[:4]:
                W("        citation -> %s" % ln)
            for en in errors(rec)[:3]:
                W("        error    -> %s" % en)
        # show what it actually printed, if anything
        if led:
            W("      ledger actually printed:")
            for ln in led:
                W("        %s" % ln)
        else:
            W("      ledger actually printed: (nothing)")
    W("")

    # ---------------------------------------------------------------- 4 ----
    W("-" * 100)
    W("4. GAPS LIST - every row where observed != expected")
    W("-" * 100)
    gaps = []
    for e in entries:
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        if rec is None:
            gaps.append((e["id"], "NO RECORD", e["should_be_red"], e["gap_if_green"]))
            continue
        ec = rec.get("probe_exit")
        actual_red = ec != 0
        eid = e["id"]
        if e["ledger_line"] is None:
            if actual_red != bool(e["should_be_red"]):
                gaps.append((eid, "red=%s" % actual_red, e["should_be_red"], e["gap_if_green"]))
            continue
        got = matches(e["ledger_line"], ledger(rec), e["matching_rule"])
        if e["should_be_red"]:
            if not (got and actual_red):
                gaps.append((eid, "red=%s ledgerline=%s" % (actual_red, got),
                             e["should_be_red"], e["gap_if_green"]))
        else:
            if got or actual_red:
                gaps.append((eid, "red=%s ledgerline=%s" % (actual_red, got),
                             e["should_be_red"], e["gap_if_green"]))
    if not gaps:
        W("  none")
    for i, (eid, obs, exp, gap) in enumerate(gaps, 1):
        W("")
        W("  GAP %d: [%s]" % (i, eid))
        W("      expected red : %s" % exp)
        W("      observed      : %s" % obs)
        W("      predicted_red : %s" % next(e["predicted_red"] for e in entries if e["id"] == eid))
        if gap:
            W("      gap_if_green, verbatim:")
            W("        " + wrap(gap, indent=8))
        else:
            W("      gap_if_green : (manifest declares null for this entry)")
    W("")
    W("  total gaps: %d" % len(gaps))
    W("")

    # ---------------------------------------------------------------- 5 ----
    W("-" * 100)
    W("5. WHAT THE CLEAN GREEN RUNS DO AND DO NOT EARN")
    W("-" * 100)
    for e in entries:
        recs_for = by_id.get(e["id"], [])
        rec = recs_for[-1] if recs_for else None
        if rec is None or rec.get("probe_exit") != 0:
            continue
        eid = e["id"]
        if eid == "clean_baseline":
            W("  [%s] exit 0." % eid)
            W("      Earns: the probe is green on the unmodified database, so every")
            W("      red run in this campaign is attributable to its mutation rather than")
            W("      to a probe that is broken at baseline.")
            W("      Does NOT earn: any claim that the database is correct. A green run")
            W("      proves the probe did not observe a hole, nothing more.")
            continue
        if e["predicted_red"] == "false":
            W("  [%s] exit 0, and the manifest PREDICTED green." % eid)
            W("      Earns: the manifest's own stated gap. Verbatim gap_if_green:")
            W("        " + wrap(e["gap_if_green"] or "", indent=8))
            if e["missing_probe"]:
                W("      The manifest names the missing probe explicitly:")
                W("        " + wrap(e["missing_probe"], indent=8))
            W("      This is a documented, EXPECTED finding about the probe's coverage.")
            W("      It is NOT a bug in this harness and NOT evidence the mutation is safe.")
            continue
        W("  [%s] exit 0." % eid)
        W("      Earns: NOTHING about the safety of the mutated object. The probe did")
        W("      not observe the mutation; that is a blind spot in the probe.")
        if e["gap_if_green"]:
            W("      gap_if_green, verbatim:")
            W("        " + wrap(e["gap_if_green"], indent=8))
    W("")

    # ---------------------------------------------------------------- 6 ----
    W("-" * 100)
    W("6. CAMPAIGN INTEGRITY AS SEEN FROM THE RAW RECORDS")
    W("-" * 100)
    W("  final fingerprint : %s" % closing.get("final_fingerprint"))
    W("  baseline          : %s" % closing.get("baseline_fingerprint"))
    W("  equal             : %s" % closing.get("fingerprint_matches_baseline"))
    W("  final md5(prosrc) : %s" % closing.get("final_md5_prosrc"))
    W("  equal to baseline : %s" % closing.get("md5_matches_baseline"))
    W("  dependent policies: %s" % closing.get("final_dependent_policies"))
    W("  hard_stop         : %s" % closing.get("hard_stop"))
    cb = by_id.get("clean_baseline", [])
    if cb:
        W("  final clean_baseline exit : %s" % cb[-1].get("probe_exit"))
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