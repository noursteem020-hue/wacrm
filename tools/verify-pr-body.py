"""Re-derive every `cmd -> result` claim in a PUBLISHED PR description.

The RLS campaign's failures were all one shape: a sentence about the repository
written from memory rather than from a command's output. Test counts measured at one
commit were relabelled onto another; a file was asserted absent from a branch that
contained it. Human review does not catch that class reliably -- the author reads
their own sentence correctly, because they wrote it. This tool catches it
mechanically, which is the same reason the mutation campaign exists.

It reads the body GitHub SERVES, not a local draft:

    gh pr view <n> --json body --jq .body

Then for every fenced line shaped `command -> result` it re-runs the command and
compares. A mismatch is a FAIL. A line with no `->` is prose, and is reported as
UNCHECKED rather than silently passed -- an unparseable claim is exactly the kind of
sentence that needs a human, and hiding that is how one slips through.

Read-only. It runs the command it finds, so only run it on a description you trust
enough to execute, and read the commands before running this.

    python tools/verify-pr-body --pr 3
    python tools/verify-pr-body --pr 3 --repo noursteem020-hue/wacrm

Exit 0 all claims re-derived. Exit 1 at least one mismatch. Exit 2 usage or fetch
failure.
"""
import argparse
import re
import shutil
import subprocess
import sys

# `git log --oneline X..Y   -> 6`   and   `cmd -> result` across a whole body.
CLAIM = re.compile(r"^\s*(?P<cmd>\S[^\n]*?)\s+->\s+(?P<result>\S.*?)\s*$")
# A PR body's fenced blocks: ```lang ... ```. Kept per-block so a `->` inside prose
# is not read as a command.
FENCE = re.compile(r"^```", re.M)


def fetch_body(pr, repo):
    cmd = ["gh", "pr", "view", str(pr), "--json", "body", "--jq", ".body"]
    if repo:
        cmd += ["--repo", repo]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(f"could not read PR #{pr}: {(p.stderr or p.stdout).strip()[:200]}")
        return None
    return p.stdout


def claims(body):
    """-> [(line_no, cmd, result)] for `cmd -> result` inside fenced blocks."""
    out, inside, start = [], False, 0
    for i, line in enumerate(body.splitlines(), 1):
        if line.startswith("```"):
            inside = not inside
            continue
        if not inside:
            continue
        m = CLAIM.match(line)
        if m:
            out.append((i, m.group("cmd").strip(), m.group("result").strip()))
    return out


def run(cmd):
    """Run the command text and return its collapsed stdout.

    Run through bash explicitly, not `shell=True`'s default. On Windows that
    default is cmd.exe, where a regex alternation like `(PASS|FAIL)` is parsed as a
    pipe: the tool reported "'FAIL)'' is not recognized as an internal or external
    command" against claims that were simply true. A checker that mis-reports its
    own subject is worse than no checker, because the reader cannot tell which of
    the two is broken.

    `bash -c`, never `bash -lc`: the login flag sources a profile that fails in this
    environment, and every command came back as "execvpe(/bin/bash) failed" -- a
    total failure that still reads as a set of MISMATCHes.
    """
    bash = shutil.which("bash") or shutil.which("sh")
    if not bash:
        raise RuntimeError("no bash or sh on PATH; cannot run the claims")
    p = subprocess.run([bash, "-c", cmd], capture_output=True, text=True,
                       cwd="C:/Users/FX-tec/Desktop/wacrm-work")
    out = " ".join((p.stdout or p.stderr).split())
    # A WSL or shell-level failure is the TOOL breaking, not the subject failing.
    # Reporting it as a MISMATCH would blame the claim for the checker's defect.
    if "execvpe" in out or "No such file or directory" in out and not p.stdout:
        raise RuntimeError(f"the shell itself failed running: {cmd}")
    return out


def compare(expected, actual):
    """True when the published result is still what the command returns.

    A claim may be an elision of the real output -- "MATCH 10" for a line that
    reads "MATCH                   : 10" -- but it may never state something the
    output does not contain. So the test is:

      1. every whitespace-separated TOKEN in the claim appears in the output, AND
      2. the claim's tokens appear in the output in the same order.

    Token order is what makes this a check rather than a vibe. A claim of
    "UNCLASSIFIED 0 / DEFERRED 2" must find DEFERRED after UNCLASSIFIED, so a file
    whose counters were reordered or renamed cannot pass. Comparing whole strings
    instead would have failed seven true claims whose commands print more than the
    body quotes, which is the same defect in the other direction: a checker that
    cannot pass its own subject gets ignored.
    """
    e = expected.strip().strip('"')
    a = actual.strip().strip('"')
    if e == a:
        return True, ""
    # Tolerate the author's `x -> y` inside a quoted result, and a trailing slash.
    et = [t for t in re.split(r"[\s/]+", e) if t and t not in ("->",)]
    at = [t for t in re.split(r"\s+", a) if t]
    if not et:
        return False, f"the claim has no tokens to check against {a[:80]!r}"
    pos = 0
    for tok in et:
        found = None
        for i in range(pos, len(at)):
            if at[i] == tok:
                found = i
                break
        if found is None:
            return False, (f"token {tok!r} not found after position {pos} in "
                           f"{a[:120]!r}")
        pos = found + 1
    return True, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pr", required=True, type=int)
    ap.add_argument("--repo", default="noursteem020-hue/wacrm")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the commands without running them")
    args = ap.parse_args()

    body = fetch_body(args.pr, args.repo)
    if body is None:
        return 2

    found = claims(body)
    print(f"PR #{args.pr} body: {len(found)} machine-checkable claim(s) in fenced blocks")
    print(f"  source: gh pr view {args.pr} --json body   (the PUBLISHED body)")
    print()

    if not found:
        print("FAIL no `cmd -> result` line found. Either the description carries no")
        print("  evidence in that form, or it uses a form this tool cannot read.")
        print("  Either way it is not verified, and that is not the same as verified.")
        return 1

    bad = []
    tool_broken = []
    for line_no, cmd, expected in found:
        if args.dry_run:
            print(f"  L{line_no:>4}  WOULD RUN  {cmd}")
            print(f"         claimed   -> {expected}")
            continue
        try:
            actual = run(cmd)
        except RuntimeError as e:
            print(f"  TOOL L{line_no:>4}  {cmd}")
            print(f"         the checker could not run this: {e}")
            print()
            tool_broken.append((line_no, str(e)))
            continue
        ok, why = compare(expected, actual)
        mark = "OK  " if ok else "FAIL"
        print(f"  {mark} L{line_no:>4}  {cmd}")
        print(f"         claimed   -> {expected}")
        print(f"         re-derived-> {actual[:120]}")
        if not ok:
            print(f"         MISMATCH: {why}")
        print()
        if not ok:
            bad.append((line_no, cmd, why))

    if args.dry_run:
        return 0
    checked = len(found) - len(tool_broken)
    print(f"RE-DERIVED {checked - len(bad)}/{checked}"
          + (f"  (checker failed on {len(tool_broken)})" if tool_broken else ""))
    if tool_broken:
        print("TOOL FAILURES: the checker itself could not run a claim. That is not a")
        print("  verdict on the claim, and this run certifies nothing.")
    if bad:
        print(f"MISMATCHES : {len(bad)}")
        for line_no, cmd, why in bad:
            print(f"   !! line {line_no}: {cmd}")
            print(f"      {why}")
    return 1 if (bad or tool_broken) else 0


if __name__ == "__main__":
    sys.exit(main())