"""Break tests for ledger_audit.py. Required, because the auditor went from a
false NEGATIVE to a false POSITIVE, and the second is worse.

History of the matcher, all MEASURED:
  v1  stripped the [7a]/[9a] tag from one side only  -> 16 of 16 "unclassified"
  v2  kept the tag, matched on full-string prefix   ->  9 false gaps
  v3  matched on >=2 shared word triples            ->  0 unclassified, and the
                                                      break test below showed that
                                                      number was meaningless

v3's threshold is what this file exists to catch. Every [9a] row shares triples
like "a fail cross" and "cross account update", so a threshold of 2 will call any
new [9a] line containing "cross-account UPDATE" classified even though it was
never declared. "0 unclassified" then means nothing: it may mean the document is
complete, or it may mean the matcher is blind. Both tests below FAILED against v3.

  T1  remove one classified row from a COPY of the document. Every run line that
      row covered must become unclassified, and nothing else may.
  T2  add a plausible but undeclared line to a COPY of the run, worded like a real
      one -- "[9a] FAIL cross-account UPDATE bypassed the census: ..." -- and it
      must be rejected.

Neither expectation is hardcoded. T1 derives its count from the baseline instead,
because a fixed "1" would have been wrong: the w6 leak row classifies two run
lines that differ only in their counts (ALLOWED rows=2 and ALLOWED rows=5), so
removing it correctly orphans two. A hardcoded number would have turned the tool's
right answer into a reported failure, which is the same class of error as the bug
this file is guarding against.

The fix, once template matching was in place, was not scoring but equality: a
document row is prose with slots for counts, parentheticals and ellipses, and the
literal prose between them must appear in the line in order. A line matches a row
or it does not, and every match is printed with the row it matched and that row's
classification -- "classified" without naming the row cannot be checked.
"""
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile

REPO = pathlib.Path("C:/Users/FX-tec/Desktop/wacrm-work")
AUDIT = REPO / "tools" / "rls-mutation" / "ledger_audit.py"
DOC = REPO / "tools" / "rls-mutation" / "ledger-lines.md"
JSONL = REPO / "docs" / "evidence" / "b2r2" / "executor.jsonl"

UNDECLARED = ("[9a] FAIL cross-account UPDATE bypassed the census: the ownership "
              "census was not consulted, counted as postgres before rollback")


def load_auditor():
    spec = importlib.util.spec_from_file_location("la", str(AUDIT))
    la = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(la)
    return la


def run_audit(la, doc=DOC, jsonl=JSONL):
    """Run the auditor against a given document and run; parse its verdict."""
    src = AUDIT.read_text(encoding="utf-8")
    src = src.replace('DOC = ROOT / "tools" / "rls-mutation" / "ledger-lines.md"',
                      f'DOC = pathlib.Path(r"{doc}")')
    src = src.replace('JSONL = ROOT / "docs" / "evidence" / "b2r2" / "executor.jsonl"',
                      f'JSONL = pathlib.Path(r"{jsonl}")')
    tmp = pathlib.Path(tempfile.mkdtemp()) / "la.py"
    tmp.write_text(src, encoding="utf-8")
    p = subprocess.run([sys.executable, str(tmp)], capture_output=True, text=True)
    out = p.stdout + p.stderr
    m = re.search(r"UNCLASSIFIED\s+:\s+(\d+)", out)
    count = int(m.group(1)) if m else None
    flagged = re.findall(r"!! (\S+(?:,\S+)*)\s+(.*)", out)
    return count, flagged, out


def main():
    work = pathlib.Path(tempfile.mkdtemp(prefix="ledger-break-"))
    la = load_auditor()

    print("=" * 74)
    print("BASELINE: the committed document and the committed run")
    print("=" * 74)
    base_count, _base_flagged, base_out = run_audit(la)
    print(base_out.strip())
    if base_count != 0:
        print(f"\nABORT: the baseline is not clean ({base_count}), so neither break")
        print("test below could be attributed to its own cause. Fix the baseline.")
        return 2

    # ---------------------------------------------------------------- T1
    print()
    print("=" * 74)
    print("T1  delete one classified row; expect exactly the lines it covered")
    print("=" * 74)
    doc1 = work / "ledger-lines.md"
    lines = DOC.read_text(encoding="utf-8").splitlines(keepends=True)
    victim = None
    for i, line in enumerate(lines):
        if line.startswith("|") and "FAIL leak: the unscoped UPDATE moved" in line:
            victim = i
            break
    if victim is None:
        print("ABORT: could not find the w6 leak row to remove")
        return 2
    print("removed row:")
    print("   " + lines[victim].strip()[:150])

    doc_rows = la.rows_from_document(DOC.read_text(encoding="utf-8"))
    stems = la.run_lines(JSONL.read_text(encoding="utf-8"))
    victim_cell = next(c for _, c, _cls, _m in doc_rows
                       if c.replace("`", "").strip() in lines[victim].strip())
    victim_rx = next(rx for rx, c, _cls, _m in doc_rows if c == victim_cell)
    covered = sorted(ln for ln in stems if victim_rx.search(la.normalise(ln)))
    print(f"\nMEASURED: that row classifies {len(covered)} distinct line(s) here:")
    for ln in covered:
        print(f"    {ln[:96]}")

    doc1.write_text("".join(lines[:victim] + lines[victim + 1:]), encoding="utf-8")
    c1, f1, _o1 = run_audit(la, doc=doc1)
    print(f"\nUNCLASSIFIED = {c1}   (expected {len(covered)})")
    for ids, stem in f1:
        print(f"   flagged [{ids}] {stem[:90]}")

    # Compare the two sets. normalise() folds counts to #, which would collapse
    # "ALLOWED rows=2" and "ALLOWED rows=5" into one key and hide a genuine
    # mismatch, so compare the verbatim line instead and only use normalise() for
    # membership tests that do not need the counts kept apart.
    def identity(ln):
        return re.sub(r"\s+", " ", ln).strip()

    flagged_raw = {identity(stem) for _ids, stem in f1}
    covered_raw = {identity(ln) for ln in covered}
    t1_ok = (c1 == len(covered)) and flagged_raw == covered_raw
    for extra in sorted(flagged_raw - covered_raw):
        print(f"   flagged a line that row did NOT cover: {extra[:80]}")
    for missed in sorted(covered_raw - flagged_raw):
        print(f"   failed to flag an orphaned line: {missed[:80]}")
    print(f"T1 {'PASS' if t1_ok else 'FAIL -- the auditor cannot see a removed classification'}")

    # ---------------------------------------------------------------- T2
    print()
    print("=" * 74)
    print("T2  add an undeclared line; expect it to be rejected")
    print("=" * 74)
    print("injected:", UNDECLARED[:100])
    json2 = work / "executor.jsonl"
    recs = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    target = next(d for d in recs if d.get("record") == "entry")
    target["fail_lines"].append({"stream": "stdout", "line": UNDECLARED})
    json2.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n",
                     encoding="utf-8")

    c2, f2, _o2 = run_audit(la, jsonl=json2)
    print(f"\nUNCLASSIFIED = {c2}   (expected 1, the injected line)")
    for ids, stem in f2:
        print(f"   flagged [{ids}] {stem[:90]}")
    t2_ok = c2 == 1 and any("bypassed the census" in s for _i, s in f2)
    print(f"T2 {'PASS' if t2_ok else 'FAIL -- the injected line was accepted as classified'}")

    # ---------------------------------------------------------------- T3
    print()
    print("=" * 74)
    print("T3  declare a required row no mutation fires; expect SILENT REQUIRED 1")
    print("=" * 74)
    print("This is the REVERSE direction: the forward check asks whether every")
    print("emitted line is declared, this asks whether every declared `required`")
    print("line was ever emitted. A row claiming to be a mutation's signal while no")
    print("mutation produces it is either a dead assertion or an unmeasured gap.")
    print()
    print("How the row is made silent: a NEW required row is added whose template no")
    print("run line matches. Repointing an existing row is not enough -- the line it")
    print("names still fires under some other mutation, so the row is not silent.")
    # Take the wording from a line the probe really emits, and alter it into a
    # plausible variant no mutation produces: the same opening, a claim nobody
    # makes. This is the reverse of T2's injection, aimed at the other direction.
    real = next(ln for ln in la.run_lines(JSONL.read_text(encoding="utf-8"))
                if "cross-account DELETE" in ln)
    # Vary the CLAIM, not a wildcard region: "did not survive" is literal prose in
    # the real row, so changing it makes the template match nothing. Appending words
    # after it would land inside a slot and still match.
    silent_row = real.replace("account B's probe row did not survive",
                              "account B's probe row did not survive the unscoped sweep")
    silent_row = silent_row.split("Read AS POSTGRES")[0].strip().rstrip(",")
    doc3 = work / "ledger-lines-3.md"
    lines3 = DOC.read_text(encoding="utf-8").splitlines(keepends=True)
    anchor = next(i for i, l in enumerate(lines3)
                  if l.startswith("|") and "cross-account DELETE" in l and "| required |" in l)
    # Build the row from its parsed cells, keeping the leading pipe structure right:
    # a row that starts "||" puts the classification in the wrong cell and is
    # silently skipped, which is what made an earlier version of this test report a
    # PASS-shaped failure.
    cells = [c.strip() for c in lines3[anchor].split("|")]
    new_cells = [f"`{silent_row}`", "required", "`delete_using_true`",
                 "added by T3: a required row no mutation emits", ""]
    lines3.insert(anchor + 1, "|" + "|".join(new_cells) + "|\n")
    doc3.write_text("".join(lines3), encoding="utf-8")
    print("inserted as required, for delete_using_true:")
    print(f"    {silent_row}")
    # Prove the row is parseable and unmatchable BEFORE trusting the verdict: a
    # malformed row would also report SILENT 0, for the wrong reason.
    probe = la.rows_from_document(doc3.read_text(encoding="utf-8"))
    parsed = [c for _rx, c, _k, _m in probe if "unscoped sweep" in c]
    if not parsed:
        print("ABORT: the inserted row does not parse as a classification row")
        return 2
    if any(rx.search(la.normalise(ln)) for rx, c, _k, _m in probe if "unscoped sweep" in c
           for ln in la.run_lines(JSONL.read_text(encoding="utf-8"))):
        print("ABORT: the inserted row matches a real line, so it is not silent")
        return 2
    print("pre-check: the row parses and matches no run line -- it is genuinely silent")

    _c3, _f3, out3 = run_audit(la, doc=doc3)
    m3 = re.search(r"SILENT REQUIRED ROWS\s+:\s+(\d+)", out3)
    n3 = int(m3.group(1)) if m3 else None
    print(f"\nSILENT REQUIRED ROWS = {n3}   (expected 1)")
    for ln in out3.splitlines():
        if ln.startswith("  SILENT"):
            print(f"   {ln.strip()[:118]}")
    t3_ok = n3 == 1 and "unscoped sweep" in out3
    print(f"T3 {'PASS' if t3_ok else 'FAIL -- the auditor cannot see an untriggered required row'}")

    print()
    print("=" * 74)
    if t1_ok and t2_ok and t3_ok:
        print("ALL THREE PASS. Both directions of the audit have been seen failing:")
        print("removal (T1), injection (T2), and a row that never fires (T3).")
        print("Its 0/0 on the baseline is now evidence, not a shrug.")
        return 0
    print("AT LEAST ONE FAILED. The baseline's zeros prove nothing until the")
    print("auditor has been seen failing in that direction too.")
    return 1


if __name__ == "__main__":
    sys.exit(main())