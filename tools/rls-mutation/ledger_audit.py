"""Check every FAIL line the run emitted against ledger-lines.md's classifications.

Kept in the tree because its own history is part of the record. The version
committed first reported 9 of the 16 lines as unclassified; all 9 were in fact
classified. Two bugs, in order:

  1. It stripped the [7a]/[9a] tag on one side of the comparison only. The
     document writes the tag inside its table cell, so the two sides could never
     agree and it reported 16 of 16 as unclassified.
  2. With the tag kept, it matched on full-string prefixes. Several document rows
     are abbreviated with an ellipsis ("[9a] FAIL positive control before: …"),
     which leaves too few words for a prefix test, so those read as gaps.

It now compares on word triples with stopwords removed, which survives both
abbreviation and differing counts. A row the document states very tersely can
still be reported as a gap, and the output says so on each line. Read the list
before acting on it: a checker that reports a false negative is
indistinguishable from one that found real defects.

The document's own rule, which this enforces -- every FAIL line the probe can
emit must be classified as required or allowed_collateral, or it is a defect.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DOC = ROOT / "tools" / "rls-mutation" / "ledger-lines.md"
JSONL = ROOT / "docs" / "evidence" / "b2r2" / "executor.jsonl"

STOP = {"fail", "row", "rows", "the", "a", "an", "of", "its", "own", "to",
        "and", "was", "is", "not", "must", "this", "by", "s", "n", "m",
        "every", "below", "above"}


def normalise(text):
    """Keep the tag; drop parentheses, digits and punctuation; lowercase."""
    t = text.replace("`", "").replace("…", "")
    t = re.sub(r"\(.*?\)", " ", t)             # parentheticals and their counts
    t = re.sub(r"[^A-Za-z\[\]0-9 ]", " ", t)   # dashes, underscores -> space
    return re.sub(r"\s+", " ", t).strip().lower()


def triples(text):
    """Word triples with digits unified, so a differing count cannot matter."""
    words = [x for x in re.sub(r"\d+", "#", text).split() if x not in STOP]
    return {" ".join(words[i:i + 3]) for i in range(len(words) - 2)}


def classified(line_key, rows):
    lt = triples(line_key)
    if not lt:
        return False
    return any(len(lt & triples(r)) >= 2 for r in rows)


def main():
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and "FAIL" in line:
            k = normalise(line.split("|")[1])
            if len(k) > 12:                 # skips header-ish cells like "mutation"
                rows.append(k)
    print(f"classified rows in the document : {len(rows)}")

    if not JSONL.exists():
        print(f"no run at {JSONL}")
        return 2

    recs = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    stems = {}
    for d in [x for x in recs if x.get("record") == "entry"]:
        seen = set()
        for x in d.get("fail_lines") or []:
            s = x["line"].strip()
            if "FAIL" not in s or s == "TENANT ISOLATION FAILED. Failures:":
                continue
            if s.startswith("psql:"):        # the gate's stderr echo of the ledger
                continue
            seen.add(s)
        for s in seen:
            stems.setdefault(normalise(s), set()).add(d["id"])

    print(f"distinct FAIL lines in the run  : {len(stems)}")
    unclassified = [(sorted(ids), k) for k, ids in sorted(stems.items())
                    if not classified(k, rows)]

    print(f"UNCLASSIFIED                    : {len(unclassified)}")
    for ids, k in unclassified:
        print(f"   !! {','.join(ids)}  {k[:100]}")
        print("      confirm by hand: the document may state this row so tersely")
        print("      that no three-word sequence survives the comparison.")
    return 1 if unclassified else 0


if __name__ == "__main__":
    sys.exit(main())