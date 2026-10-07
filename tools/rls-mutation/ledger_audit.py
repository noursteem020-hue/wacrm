"""Check every FAIL line the run emitted against ledger-lines.md's classifications.

STRICT TEMPLATE MATCHING. Not overlap scoring -- and that change was forced by a
break test rather than chosen. History, all MEASURED:

  v1  stripped the [7a]/[9a] tag from one side of the comparison only, so the
      two sides could never agree. Reported 16 of 16 lines unclassified.
      FALSE NEGATIVES.
  v2  kept the tag, matched on full-string prefixes. Rows the document abbreviates
      with an ellipsis ("[9a] FAIL positive control before: ...") have too few
      words for a prefix test, so they read as gaps. 9 false gaps.
  v3  matched on >=2 shared word triples. Reported 0 unclassified -- which was
      worthless, because the break test showed it accepted a line it had to
      reject. FALSE POSITIVES, the more dangerous direction: every [9a] row
      shares triples like "a fail cross" and "cross account update", so any new
      [9a] line containing "cross-account UPDATE" passed as classified.

How it matches now. A document row is a TEMPLATE: prose with three kinds of slot.

  ...  or U+2026   an ellipsis: matches any run of characters
  (...)            a parenthetical: matches any single parenthetical
  N  M  any digits counts: folded to # on both sides

The literal prose between the slots must appear in the line, in order, verbatim
after normalisation. Scoring is gone: a line matches a row or it does not, and
every line reports the exact row it matched and that row's classification.

Tail rule: a template ending in a slot matches any continuation; a template ending
in literal prose must be followed by end-of-line, an open parenthesis, a comma, a
colon or a period. That stops a short template matching inside a longer clause it
has nothing to do with.

Break tests live in tools/rls-mutation/audit_breaktest.py. Both FAILED against v3:
  T1  delete one classified row from a copy of the document -> must report
      UNCLASSIFIED 1, naming the line that lost its row.
  T2  inject an undeclared but plausible line ->
      "[9a] FAIL cross-account UPDATE bypassed the census: ..." -> must be rejected.

A checker that has never been seen failing is not evidence. Run them.

The document's own rule, which this enforces -- every FAIL line the probe can emit
must be classified as required or allowed_collateral, or it is a defect.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOC = ROOT / "tools" / "rls-mutation" / "ledger-lines.md"
JSONL = ROOT / "docs" / "evidence" / "b2r2" / "executor.jsonl"

# What may legitimately follow a template that ends in literal prose. A space
# then "(" is the common case: "... owned by account A (rows found: 0), so ...".
TAIL = r"(\s*[\(\,:.]|$)"


def normalise(text):
    """Lowercase; fold counts and the N/M placeholders to #; tidy punctuation."""
    t = text.replace("`", "").replace("\u2014", " -- ").replace("\u2013", " -- ")
    t = t.replace("--", " -- ")
    # Fold the N/M placeholders to #. The tag is protected first: \b[NM]\b would
    # otherwise eat the N of a [9a] marker and turn the tag into [#a].
    t = re.sub(r"\[[0-9a-z]+\]", lambda m: m.group(0).upper(), t)
    t = re.sub(r"(?<![\w\]])[NM](?![\w\[])", "#", t)
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


def split_template(row):
    """-> (literal segments, ends_in_slot).

    The ellipsis is split FIRST. Splitting parentheticals first would swallow a
    row like "was ALLOWED (..., N row(s) actually moved)" whole, leaving its
    ellipsis inside one segment where it matched literally instead of as a slot.
    """
    raw = row.replace("`", "").replace("\u2026", " \u2026 ")
    segs, rest = [], raw
    for pat in ("\u2026", r"\([^()]*\)"):
        while True:
            m = re.search(pat, rest)
            if not m:
                break
            segs.append(rest[:m.start()])
            rest = rest[m.end():]
    segs.append(rest)
    ends_in_slot = bool(re.search(r"(\([^()]*\)|\u2026)\s*$", raw))
    return [s for s in segs if normalise(s)], ends_in_slot


def compile_row(row):
    """Build a regex from a document row, or None if it carries no literal prose."""
    segs, ends_in_slot = split_template(row)
    segs = [normalise(s) for s in segs]
    if not segs:
        return None
    parts = [re.escape(segs[0]).replace(r"\ ", " ")]
    for s in segs[1:]:
        parts.append(".*")
        parts.append(re.escape(s).replace(r"\ ", " "))
    return re.compile("^" + "".join(parts) + ("" if ends_in_slot else TAIL))


def rows_from_document(text):
    """Classified rows as (regex, cell1, classification, mutations)."""
    out = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue
        name, cls = cells[1], cells[2].lower()
        if "FAIL" not in name or name.lower() in ("mutation", "run", ""):
            continue
        if cls not in ("required", "allowed_collateral"):
            continue                     # not a classification row
        rx = compile_row(name)
        if rx:
            out.append((rx, cells[1], cls, cells[3] if len(cells) > 3 else ""))
    return out


def run_lines(text):
    """Distinct FAIL lines the run emitted, mapped to the mutations emitting them."""
    recs = [json.loads(l) for l in text.splitlines() if l.strip()]
    stems = {}
    for d in [r for r in recs if r.get("record") == "entry"]:
        seen = set()
        for x in d.get("fail_lines") or []:
            s = x["line"].strip()
            if "FAIL" not in s or s == "TENANT ISOLATION FAILED. Failures:":
                continue
            if s.startswith("psql:"):     # the gate's stderr echo of the ledger
                continue
            seen.add(s)
        for s in seen:
            stems.setdefault(s, set()).add(d["id"])
    return stems


def main():
    rows = rows_from_document(DOC.read_text(encoding="utf-8"))
    print(f"classified rows in the document : {len(rows)}")

    if not JSONL.exists():
        print(f"no run at {JSONL}")
        return 2

    stems = run_lines(JSONL.read_text(encoding="utf-8"))
    # MEASURED: the raw set holds 20 lines but normalises to 18. Two pairs differ
    # only in their counts -- "ALLOWED rows=5" versus "ALLOWED rows=2", under
    # function_body_true and update_with_check_true -- so one template each and
    # one classification each, not two gaps.
    print(f"distinct FAIL lines in the run  : {len(stems)}")

    unmatched = []
    for line in sorted(stems):
        nl = normalise(line)
        hits = [r for r in rows if r[0].search(nl)]
        ids = ",".join(sorted(stems[line]))
        if hits:
            rx, cell, cls, mut = hits[0]
            extra = f"  (+{len(hits) - 1} more row)" if len(hits) > 1 else ""
            print(f"  OK   [{ids}] {cls:<18} <- {cell[:60]}{extra}")
            print(f"       {line[:104]}")
        else:
            unmatched.append((ids, line))
            print(f"  MISS [{ids}] no document row classifies this line")
            print(f"       {line[:104]}")

    print(f"UNCLASSIFIED                    : {len(unmatched)}")
    for ids, line in unmatched:
        # Full line, not truncated: this is the defect list a reader has to act on,
        # and a truncated stem is not enough to identify which line is undeclared.
        print(f"   !! [{ids}] {line}")
        print("      a defect by the document's own rule: add a classified row for")
        print("      it, or prove the probe cannot emit it.")

    # ------------------------------------------------------------------ reverse
    # The forward direction asks "is every emitted line declared?". The reverse
    # asks "did every declared `required` line actually fire?", which catches a
    # different defect: a row that claims to be a mutation's declared signal while
    # no mutation has ever emitted it. Such a row is either a dead assertion or an
    # unmeasured gap, and either way it is not `required`.
    #
    # Rows whose only evidence is the M-inst negative controls are exempt: the
    # instrument lines exist to fire when the CENSUS is broken, and no mutation in
    # the manifest breaks a census. They are proven by minst/ instead, which is why
    # the mutation cell names M-inst. Exempting them by name would be an
    # allow-list that hides regressions, so the exemption is derived: a row is
    # exempt only if its declared mutations are all M-inst.
    silent = []
    for rx, cell, cls, mut in rows:
        if cls != "required":
            continue
        fired = [ln for ln in stems if rx.search(normalise(ln))]
        if fired:
            continue
        declared = [x.strip(" `") for x in mut.replace("(", "").replace(")", "").split(",")]
        if declared and all(d.startswith("M-inst") for d in declared):
            print(f"  EXEMPT [required, proven by M-inst] {cell[:60]}")
            print(f"       {mut[:100]}")
            continue
        silent.append((cell, mut))
        print(f"  SILENT [required, never fired] {cell[:66]}")
        print(f"       declared for: {mut[:90]}")

    print(f"SILENT REQUIRED ROWS            : {len(silent)}")
    for cell, mut in silent:
        print(f"   !! {cell}")
        print(f"      declared for: {mut[:90]}")
        print("      no mutation emitted this line. Either it is a dead assertion or")
        print("      an unmeasured gap; it may not stay `required` until one is shown.")
    return 1 if (unmatched or silent) else 0


if __name__ == "__main__":
    sys.exit(main())