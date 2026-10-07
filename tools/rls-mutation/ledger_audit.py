"""Check every FAIL line the run emitted against ledger-lines.md's classifications.

Written as a script rather than inline because four attempts at an inline heredoc
matcher produced three different answers: substring matching missed lines the doc
lists with N/M placeholders, and two truncation attempts cut both sides
mid-phrase so nothing ever matched. A checker whose own logic is unverified is
the exact defect class this campaign is about, so it gets its own file and its
own output.

The rule the document states about itself: every FAIL line must be classified as
required or allowed_collateral, or it is a defect. This script reports which, by
comparing each emitted line against the document's table rows with numbers and
placeholders treated as equal.
"""
import json
import pathlib
import re
import sys

DOC = pathlib.Path("C:/Users/FX-tec/Desktop/wacrm-work/tools/rls-mutation/ledger-lines.md")
JSONL = pathlib.Path("C:/Users/FX-tec/Desktop/wacrm-work/docs/evidence/b2r2/executor.jsonl")


def normalise(text):
    """Reduce a ledger line to comparable words.

    The tag is KEPT, because the document writes it: a row reads
    "`[9a] FAIL leak: ...`", so stripping the tag on one side and not the other
    guarantees a mismatch. That bug was in the first version of this script and it
    reported all 16 lines as unclassified.
    """
    t = text.replace("`", "").replace("…", "")
    t = re.sub(r"\(.*?\)", " ", t)          # drop parentheticals and their counts
    t = re.sub(r"[^A-Za-z ]", " ", t)       # digits, underscores, dashes -> space
    return re.sub(r"\s+", " ", t).strip().lower()


def main():
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and "FAIL" in line:
            k = normalise(line.split("|")[1])
            if k:
                rows.append(k)
    print(f"classified rows in the document : {len(rows)}")

    recs = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
    entries = [d for d in recs if d.get("record") == "entry"]

    stems = {}
    for d in entries:
        seen = set()
        for x in d.get("fail_lines") or []:
            s = x["line"].strip()
            if "FAIL" not in s or s == "TENANT ISOLATION FAILED. Failures:":
                continue
            if s.startswith("psql:"):          # the gate's stderr echo of the ledger
                continue
            seen.add(s)
        for s in seen:
            stems.setdefault(normalise(s), set()).add(d["id"])

    print(f"distinct FAIL lines in the run  : {len(stems)}")
    unclassified = []
    for stem, ids in sorted(stems.items()):
        if not any(stem.startswith(r) or r.startswith(stem) for r in rows):
            unclassified.append((sorted(ids), stem))

    print(f"UNCLASSIFIED                    : {len(unclassified)}")
    for ids, stem in unclassified:
        print(f"   !! {','.join(ids)}  {stem[:110]}")

    # Also report the inverse: doc rows never observed, which is allowed_collateral
    # declared for a mutation that may not have run in this particular campaign.
    emitted = set(stems)
    unused = [r for r in rows if not any(e.startswith(r) or r.startswith(e) for e in emitted)]
    print(f"\ndocument rows not seen this run : {len(unused)}")
    for u in unused:
        print(f"   -- {u[:110]}")
    return 1 if unclassified else 0


if __name__ == "__main__":
    sys.exit(main())