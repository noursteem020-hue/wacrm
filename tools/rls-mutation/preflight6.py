"""Preflight v6 for the B2 mutation campaign. MANDATORY before the Executor runs.

Built from preflight5.py, which is left untouched. v6 adds coverage for the one
manifest entry v5 skipped -- function_body_true -- on BOTH paths, with guards on
the captured restore text and break tests proving each guard can fire.

WHAT v5 GOT WRONG ABOUT THIS ENTRY, and why v6 is built the way it is
-------------------------------------------------------------------
1. v5 never replayed a restore for function_body_true. It read md5(prosrc) on the
   COMMITTED state and compared it to the baseline, which is the same "measure
   after the rollback" disease v2 was rejected for. A restore that changed the
   function's SECURITY DEFINER or search_path while leaving the body alone would
   have passed v5 silently.
2. prosrc IN THIS DATABASE CONTAINS 20 CARRIAGE RETURNS. Measured: prosrc is 510
   characters, of which 20 are chr(13) and 20 are chr(10). psql's default aligned
   output rewrites CRLF to LF as it prints, so a plainly-captured
   pg_get_functiondef comes back 738 bytes instead of 758 and NO LONGER EQUALS the
   server's definition. Replaying that text does not error -- it silently writes a
   different body and the baseline is never restored. Measured transports:
       -q -A -t  SELECT  -> 738 bytes, CRLF destroyed   (UNUSABLE)
       COPY TO STDOUT   -> 806 bytes, backslash-escaped (UNUSABLE)
       base64           -> 758 bytes, byte-exact       (USED HERE)
   This is DEFECT 1 again -- only the first line was ever hashed -- wearing a new
   hat. v6 transports the definition as base64 and decodes it locally.
3. THE FUNCTIONDEFINITION HAS NO TRAILING SEMICOLON. Replaying it without one
   fails with "syntax error at or near SELECT" at the fingerprint SELECT that
   follows. v6 appends the single terminator after asserting, via the
   dollar-quote-aware splitter, that the capture is exactly ONE complete
   statement. That is a guarded terminator, not a retyping.

THE CAPTURE IS NEVER RETYPED. It is read from pg_proc and replayed verbatim, which
is what restore_procedure step 3 demands. Everything the campaign restores for
this function comes from the capture, so the capture guards below are the only
thing standing between the Executor and a silently weaker function.

FINGERPRINT: extended to include pg_proc.proargdefaults, so the DEFAULT
'viewer'::account_role_enum on min_role is INSIDE the hash rather than checked
beside it. The FN line keeps the deterministic string_agg ORDER BY l. Adding a
column changes the baseline token; the new value is asserted against
EXPECTED_BASELINE_FP so a later drift in the database is caught rather than
absorbed.

v5 baseline token was fb8eda83289797ce49e634e44dc35091 (reproduced from v5's own
unmodified FINGERPRINT_SQL, not re-derived by hand).

Usage:
  python preflight6.py                     # all entries, both paths, in run order
  python preflight6.py --break-test        # weaken select_using_true restore, REQUIRE red
  python preflight6.py --self-test         # prove the fingerprint can detect a change
  python preflight6.py --dead-code         # ast check: no statement after a return
  python preflight6.py --break-m10-capture empty|no-secdef|no-searchpath|no-stable
  python preflight6.py --break-m10-baseline-md5     # simulate a post-manifest DB change
  python preflight6.py --break-m10-no-restore       # prove paths A/B detect the mutation
"""
import argparse
import ast
import base64
import hashlib
import re
import subprocess
import sys

from campaign_paths import (BASELINE_MD5, CONTAINER, EXPECTED_BASELINE_FP,
                            EXPECTED_DEPENDENT_POLICIES, MANIFEST)
V5_BASELINE_FP = "fb8eda83289797ce49e634e44dc35091"
EXPECTED_ENTRIES = 10
BREAK_TARGET = "select_using_true"
REGPROC = "'public.is_account_member(uuid,account_role_enum)'::regprocedure"
FN_ID = "function_body_true"
BASE_ID = "clean_baseline"

# Attributes the campaign measured as having to SURVIVE a restore. Each one is
# also hashed into the fingerprint; these string checks exist because a restore
# that drops one changes isolation instead of failing.
MANDATED_SUBSTRINGS = ("SECURITY DEFINER",)
ADDED_SUBSTRINGS = ("SET search_path", "STABLE")

# md5 is computed SERVER-SIDE: the whole fingerprint becomes a single 32-char
# token, so no client-side line parsing can silently drop rows from the hash.
# This is v5's query with proargdefaults appended to the FN line.
FINGERPRINT_SQL = """
SELECT md5(coalesce(string_agg(l, E'\\n' ORDER BY l), '(none)')) FROM (
  SELECT format('%s|%s|%s|%s|%s|%s|%s|%s', schemaname, tablename, policyname,
                permissive, roles::text, cmd, coalesce(qual,'-'), coalesce(with_check,'-')) AS l
    FROM pg_policies WHERE schemaname='public'
  UNION ALL
  SELECT format('RLS|%s|%s', c.relname, c.relrowsecurity::text || '/' || c.relforcerowsecurity::text)
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
  UNION ALL
  SELECT format('FN|%s|%s|%s|%s|%s|%s|%s|%s', p.oid::regprocedure::text, md5(p.prosrc),
                p.proowner::regrole, p.prosecdef,
                coalesce(array_to_string(p.proconfig,','),'-'),
                p.provolatile, p.proparallel, p.proargdefaults::text)
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.prokind = 'f'
) s
"""

# MULTI-LINE value: must NOT be read with -q -A -t. base64 is a single scalar, so
# psql's line-oriented formatting cannot touch the bytes inside it.
CAPTURE_DEF_SQL = (
    "SELECT encode(convert_to(pg_get_functiondef(" + REGPROC + "),'UTF8'),'base64');\n")

# Every column is a single scalar, so -q -A -t is correct for this one.
# NOTE: every concatenation operand is cast to ::text explicitly. `||` binds
# looser than `<>`, so an uncast boolean silently collapses the WHOLE expression
# to a single 't' -- measured, and caught by the field-count assertion below.
CAPTURE_ATTR_SQL = """
SELECT md5(prosrc) || ' ' || prosecdef::text || ' ' ||
       coalesce(array_to_string(proconfig,','),'-') || ' ' || provolatile::text || ' ' ||
       proparallel::text || ' ' || (p.proargdefaults::text <> '')::text || ' ' ||
       proowner::regrole::text || ' ' || prolang::regtype::text || ' ' ||
       coalesce(array_to_string(proargnames,','),'-') || ' ' || prokind::text || ' ' ||
       length(prosrc)::text || ' ' ||
       (length(prosrc) - length(replace(prosrc, chr(13), '')))::text
  FROM pg_proc p WHERE p.oid = """ + REGPROC + ";\n"

COMPOSITE_SQL = ("SELECT md5(prosrc) || '/' || prosecdef::text || '/' ||"
                 " coalesce(array_to_string(proconfig,','),'-') || '/' ||"
                 " (SELECT count(*) FROM pg_policies WHERE schemaname='public'"
                 "  AND (qual LIKE '%is_account_member%'"
                 "    OR with_check LIKE '%is_account_member%'))::text"
                 " FROM pg_proc WHERE oid = " + REGPROC + ";\n")

TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
COMPOSITE_RE = re.compile(r"^([0-9a-f]{32})/(\S*)/(\S*)/(\d+)$")


def psql(sql, multi_line=False):
    """Run SQL on stdin. multi_line=False -> scalar reads use -q -A -t.
    A multi-line value must be transported as base64 instead (see module docstring)."""
    args = ["docker", "exec", "-i", CONTAINER, "psql", "-U", "postgres", "-d", "postgres",
            "-v", "ON_ERROR_STOP=1"]
    if not multi_line:
        args += ["-q", "-A", "-t"]
    args += ["-f", "-"]
    p = subprocess.run(args, input=sql.encode("utf-8"), capture_output=True)
    return (p.returncode, p.stdout.decode("utf-8", "replace"),
            p.stderr.decode("utf-8", "replace"))


def sqlstate(text):
    m = re.search(r"\b([0-9A-Z]{5})\b", text or "")
    return m.group(1) if m else "-"


def split_statements(text):
    """Split on top-level semicolons only. A semicolon inside a dollar-quoted body
    is not a statement boundary."""
    out, buf, tag = [], [], None
    i = 0
    while i < len(text):
        if tag is None:
            m = re.compile(r"\$[A-Za-z_]*\$").search(text, i)
            if m:
                buf.append(text[i:m.end()])
                i = m.end()
                tag = m.group(0)
                continue
            if text[i] == ";":
                s = "".join(buf).strip()
                if s:
                    out.append(s)
                buf = []
                i += 1
                continue
            buf.append(text[i])
            i += 1
        else:
            m = text.find(tag, i)
            if m == -1:
                buf.append(text[i:])
                i = len(text)
            else:
                buf.append(text[i:m + len(tag)])
                i = m + len(tag)
                tag = None
    s = "".join(buf).strip()
    if s:
        out.append(s)
    return out


# ------------------------------------------------------------------ capture --
def capture_definition():
    """Read pg_get_functiondef from the server as base64 and decode it locally.

    Returns (text, err). err is non-empty on transport failure. Never returns a
    silently shortened value: the byte count and CR count are carried by the
    caller and asserted, because prosrc here really does contain CRs."""
    rc, out, err = psql(CAPTURE_DEF_SQL)
    if rc != 0:
        return None, "rc=%d sqlstate=%s %s" % (rc, sqlstate(err), err.strip()[:160])
    b64 = "".join(out.split())
    if not b64:
        return None, "base64 payload was empty"
    raw = base64.b64decode(b64)
    # psql -t prints ONE row terminator after the value; strip exactly that byte.
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    return raw.decode("utf-8"), None


def guard_capture_start(capture, expect_md5, lines):
    """GUARD 4a, the run-level guard. The database must still be the database the
    manifest was written against, or every restore measured here is against the
    wrong baseline. Failure ABORTS the run -- there is no continue."""
    rc, out, err = psql(CAPTURE_ATTR_SQL)
    if rc != 0:
        lines.append(f"  GUARD 4a  FAIL  could not read the pg_proc attribute set: "
                     f"rc={rc} sqlstate={sqlstate(err)}")
        lines.append("    " + " / ".join(e for e in err.strip().splitlines()[:2])[:180])
        return False
    fields = out.strip().split()
    if len(fields) != 12:
        lines.append(f"  GUARD 4a  FAIL  attribute read returned {len(fields)} fields, "
                     "expected 12; refusing to judge a partial read")
        lines.append("    " + out.strip()[:180])
        return False
    (md5, secdef, config, volat, parallel, has_default, owner, lang,
     argnames, kind, src_len, src_cr) = fields[:12]
    lines.append(f"  GUARD 4a  capture from pg_proc for oid = {REGPROC}")
    lines.append(f"    md5(prosrc)      = {md5}")
    lines.append(f"    prosecdef        = {secdef}   proconfig = {config}")
    lines.append(f"    provolatile      = {volat}    proparallel = {parallel}   "
                 f"prokind = {kind}")
    lines.append(f"    proowner         = {owner}    prolang = {lang}")
    lines.append(f"    proargnames      = {argnames}")
    lines.append(f"    has DEFAULT arg  = {has_default}   (proargdefaults is inside the hash)")
    lines.append(f"    prosrc length    = {src_len} chars, {src_cr} of them CR "
                 "(CR must be present, or the capture transport is lossy)")

    ok = True
    if md5 != expect_md5:
        lines.append(f"    ASSERTION VIOLATED: guard 'md5(prosrc) == {expect_md5}' -- "
                     f"database returned {md5}")
        ok = False
    else:
        lines.append(f"    OK md5(prosrc) == {expect_md5}")
    if md5 != BASELINE_MD5:
        lines.append(f"    ASSERTION VIOLATED: guard 'md5(prosrc) == the value the manifest "
                     f"documents ({BASELINE_MD5})' -- database returned {md5}")
        ok = False
    if int(src_cr or 0) <= 0:
        lines.append("    ASSERTION VIOLATED: guard 'prosrc contains CR characters' -- "
                     f"the DB reports {src_cr}; a CR-free prosrc means this transport "
                     "has silently normalised line endings")
        ok = False
    dep = dependent_policy_count()
    if dep != EXPECTED_DEPENDENT_POLICIES:
        lines.append(f"    ASSERTION VIOLATED: guard 'dependent policies == "
                     f"{EXPECTED_DEPENDENT_POLICIES}' -- database returned {dep}")
        ok = False
    else:
        lines.append(f"    OK dependent pg_policies rows referencing is_account_member "
                     f"= {dep}")
    return ok


def guard_capture_text(capture, lines, label=""):
    """GUARD 4b, applied immediately before the capture is used as a restore.
    Each assertion gets its own line so a break test can name the one it violated."""
    ok = True
    if not capture or not capture.strip():
        lines.append(f"  GUARD 4b{label}  ASSERTION VIOLATED: guard 'captured definition "
                     "is non-empty' -- the capture is empty")
        return False
    lines.append(f"  GUARD 4b{label}  OK   captured definition is non-empty "
                 f"({len(capture)} chars, {capture.count(chr(13))} CR preserved)")
    for needle in MANDATED_SUBSTRINGS:
        if needle in capture:
            lines.append(f"  GUARD 4b{label}  OK   captured definition contains {needle!r} "
                         "(mandated)")
        else:
            lines.append(f"  GUARD 4b{label}  ASSERTION VIOLATED: guard "
                         f"'captured definition contains {needle!r}' -- absent (mandated)")
            ok = False
    for needle in ADDED_SUBSTRINGS:
        if needle in capture:
            lines.append(f"  GUARD 4b{label}  OK   captured definition contains {needle!r} "
                         "(added: measured present, and dropping it changes isolation "
                         "instead of failing)")
        else:
            lines.append(f"  GUARD 4b{label}  ASSERTION VIOLATED: guard "
                         f"'captured definition contains {needle!r}' -- absent (added: "
                         "measured present, and dropping it changes isolation instead "
                         "of failing)")
            ok = False
    stmts = split_statements(capture)
    if len(stmts) != 1:
        lines.append(f"  GUARD 4b{label}  ASSERTION VIOLATED: guard 'capture is exactly "
                     f"one statement' -- the dollar-quote-aware splitter found {len(stmts)}")
        ok = False
    else:
        lines.append(f"  GUARD 4b{label}  OK   capture is exactly 1 statement by the "
                     "dollar-quote-aware splitter (a semicolon inside the body is not "
                     "a boundary)")
    if not capture.rstrip().endswith("$function$"):
        lines.append(f"  GUARD 4b{label}  ASSERTION VIOLATED: guard 'capture ends with "
                     f"the closing dollar tag' -- got {capture.rstrip()[-20:]!r}")
        ok = False
    else:
        lines.append(f"  GUARD 4b{label}  OK   capture ends with the closing dollar tag")
    return ok


def restore_sql_from_capture(capture):
    """The captured text carries no trailing semicolon, so exactly one terminator is
    appended. Guard 4b has already proved the capture is a single complete
    statement ending in its dollar tag, so this is a terminator, not a retyping."""
    return capture.rstrip() + ";"


# -------------------------------------------------------------- fingerprint --
def fingerprint_now():
    rc, out, err = psql(FINGERPRINT_SQL)
    toks = [l.strip() for l in out.splitlines() if TOKEN_RE.match(l.strip())]
    return (toks[0] if toks else None), rc, out, err


def read_in_txn(prelude, composite=False):
    """Run the prelude inside ONE transaction and read the result BEFORE the
    ROLLBACK. Every mutation in this tool goes through here, so nothing is ever
    applied outside BEGIN...ROLLBACK.

    The trailing read query MUST be terminated explicitly: FINGERPRINT_SQL has no
    semicolon of its own, so without the explicit ';' psql reads ROLLBACK as part
    of the preceding SELECT and reports 'syntax error at or near ROLLBACK'."""
    read_sql = COMPOSITE_SQL if composite else FINGERPRINT_SQL
    read_sql = read_sql.rstrip().rstrip(";") + ";\n"
    script = ("BEGIN;\n" + prelude.rstrip("\n") + "\n" + read_sql + "ROLLBACK;\n")
    rc, out, err = psql(script)
    if composite:
        m = next((COMPOSITE_RE.match(l.strip()) for l in out.splitlines()
                  if COMPOSITE_RE.match(l.strip())), None)
        return (m.groups() if m else None), rc, out, err
    toks = [l.strip() for l in out.splitlines() if TOKEN_RE.match(l.strip())]
    return (toks[0] if toks else None), rc, out, err


def dependent_policy_count():
    rc, out, err = psql("SELECT count(*) FROM pg_policies WHERE schemaname='public'"
                        " AND (qual LIKE '%is_account_member%'"
                        "   OR with_check LIKE '%is_account_member%');\n")
    try:
        return int(out.strip())
    except ValueError:
        return -1


def tool_selfcheck(base_fp):
    """Two identical reads must agree, or this checker cannot be trusted to issue
    a verdict at all."""
    fp, rc, out, err = fingerprint_now()
    if rc != 0 or fp is None:
        print("  TOOL FAILURE: the fingerprint query did not execute "
              f"(rc={rc} sqlstate={sqlstate(err)})")
        print("    " + " / ".join(e for e in (err or out).strip().splitlines()[:2])[:200])
        print("  No verdict is being issued about the manifest.")
        return False
    if fp != base_fp:
        print("  TOOL FAILURE: the fingerprint is unstable between two identical reads:")
        print(f"    {base_fp}  then  {fp}")
        print("  No verdict is being issued about the manifest.")
        return False
    print(f"  tool self-check: fingerprint stable across two reads ({fp})")
    return True


# ---------------------------------------------------------------- manifest --
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

        entries.append({
            "id": m.group(1),
            "apply": block("apply_sql"),
            "restore": block("restore_sql"),
            "noop": bool(re.search(r"^  noop: true\s*$", body, re.M)),
            "restore_proc": block("restore_procedure"),
        })
    return entries


def run_order(entries):
    """clean_baseline FIRST and clean_baseline LAST, function_body_true
    immediately before the final clean_baseline.

    The manifest holds exactly one clean_baseline entry, so the bookend is built by
    WALKING it twice: once to fingerprint the starting state, once at the end to
    fingerprint the state the run left behind. Without the trailing visit the
    final comparison would be against a value read before the run rather than one
    read after it, which is the v2 disease. The manifest file is not reordered and
    not edited; only the order this tool walks the entries in changes."""
    bases = [e for e in entries if e["id"] == BASE_ID]
    rest = [e for e in entries if e["id"] != BASE_ID and e["id"] != FN_ID]
    fns = [e for e in entries if e["id"] == FN_ID]
    return bases[:1] + rest + fns + bases[:1]


def check_drop_policy_idempotence(entries):
    """Item 7. A restore that says bare DROP POLICY is not runnable twice: the
    second run hits a missing policy and aborts. IF EXISTS is what makes these
    restores idempotent enough to replay on the clean baseline."""
    lines, ok = [], True
    policy_ids = [e["id"] for e in entries
                  if e["apply"] and re.search(r"\bDROP POLICY\b", e["apply"] or "")]
    lines.append(f"DROP POLICY check: {len(policy_ids)} entries whose apply drops a policy")
    for e in entries:
        if e["id"] not in policy_ids:
            continue
        r = e["restore"] or ""
        n_ifexists = len(re.findall(r"DROP POLICY IF EXISTS", r, re.I))
        bare = [l.strip() for l in r.splitlines()
                if re.search(r"\bDROP POLICY\b", l, re.I)
                and not re.search(r"DROP POLICY IF EXISTS", l, re.I)]
        if n_ifexists and not bare:
            lines.append(f"  {e['id']:24s} OK   {n_ifexists}x DROP POLICY IF EXISTS, "
                         "0 bare DROP POLICY")
        else:
            lines.append(f"  {e['id']:24s} FAIL {n_ifexists}x DROP POLICY IF EXISTS, "
                         f"{len(bare)} bare DROP POLICY: {bare}")
            ok = False
    return ok, lines


# ------------------------------------------------------------------ checks --
def check_entry(entry, base_fp, capture, break_it=False, m10_restore=None):
    eid = entry["id"]
    lines, ok = [], True
    apply_sql, restore_sql = entry["apply"], entry["restore"]
    m10 = (eid == FN_ID)

    if entry["noop"]:
        if apply_sql or restore_sql:
            return False, [f"  {eid:24s} FAIL noop entry carries SQL"]
        return True, [f"  {eid:24s} stmts=0 sqlstate=-  PASS  (declared no-op)"]

    for key in ("apply", "restore"):
        if not entry[key] and not entry["restore_proc"]:
            lines.append(f"  {eid:24s} FAIL empty {key}_sql with no noop flag")
            ok = False
    if not ok:
        return False, lines

    if break_it and not m10:
        before = restore_sql
        restore_sql = re.sub(r"\(is_account_member\(account_id\)\)", "(true)",
                             restore_sql or "", count=1)
        if restore_sql == before:
            lines.append(f"  {eid:24s} FAIL break test could not weaken this entry")
            return False, lines
        lines.append(f"  {eid:24s} ** break test: restore weakened to USING (true) **")

    # ---- function_body_true: both paths, restore = the captured text ---------
    if m10:
        # The guard must inspect the text that will ACTUALLY be replayed. In a
        # break-test mode that is the corrupted text, so the guard is what fails,
        # not an unrelated fingerprint mismatch further down.
        candidate = m10_restore if m10_restore is not None else capture
        if not guard_capture_text(candidate, lines):
            lines.append(f"  {eid:24s} FAIL capture guard refused to use the captured text: "
                         "the restore would not have been executed")
            lines.append(f"  {eid:24s} FAIL")
            return False, lines
        restore_sql = restore_sql_from_capture(candidate)
        lines.append(f"  {eid:24s} restore source = pg_get_functiondef captured verbatim "
                     f"({len(restore_sql)} chars incl. terminator)")
        if not apply_sql:
            lines.append(f"  {eid:24s} FAIL no apply_sql: the mutation could not be applied")
            return False, lines
        if not re.search(r"CREATE OR REPLACE FUNCTION", apply_sql, re.I):
            lines.append(f"  {eid:24s} FAIL apply_sql is not a CREATE OR REPLACE "
                         "(DROP FUNCTION is rejected by the dependency on 98 policies)")
            return False, lines
        lines.append(f"  {eid:24s} apply is CREATE OR REPLACE (DROP is impossible: "
                     f"{EXPECTED_DEPENDENT_POLICIES} dependent policies)")

    if not restore_sql:
        lines.append(f"  {eid:24s} FAIL nothing to replay")
        return False, lines

    # ---- path A: apply + restore, fingerprint read BEFORE the ROLLBACK ------
    prelude = (apply_sql or "").rstrip("\n") + "\n" + restore_sql.rstrip("\n")
    fp, rc, out, err = read_in_txn(prelude)
    if rc != 0:
        lines.append(f"  {eid:24s} FAIL path A rc={rc} sqlstate={sqlstate(err)}")
        lines.append("    " + (err or out).strip().splitlines()[0][:170])
        ok = False
    elif fp is None:
        lines.append(f"  {eid:24s} FAIL path A produced no fingerprint")
        ok = False
    elif fp != base_fp:
        lines.append(f"  {eid:24s} FAIL path A in-txn fp {fp[:16]} != baseline {base_fp[:16]}")
        lines.append("    restore did NOT return the baseline inside the transaction")
        ok = False
    else:
        comp, _, _, _ = read_in_txn(prelude, composite=True)
        cd = (f" md5={comp[0][:16]} secdef={comp[1]} config={comp[2]} deps={comp[3]}"
              if comp else " (composite read failed)")
        lines.append(f"  {eid:24s} path A  apply+restore  stmts="
                     f"{len(split_statements(prelude))} in-txn fp {fp[:16]} ok{cd}")

    # ---- path B: restore ALONE on the clean baseline ------------------------
    fp, rc, out, err = read_in_txn(restore_sql)
    if rc != 0:
        lines.append(f"  {eid:24s} FAIL path B rc={rc} sqlstate={sqlstate(err)}")
        lines.append("    " + (err or out).strip().splitlines()[0][:170])
        ok = False
    elif fp is None:
        lines.append(f"  {eid:24s} FAIL path B produced no fingerprint")
        ok = False
    elif fp != base_fp:
        lines.append(f"  {eid:24s} FAIL path B in-txn fp {fp[:16]} != baseline {base_fp[:16]}")
        lines.append("    restore is NOT idempotent: it altered the clean baseline")
        ok = False
    else:
        comp, _, _, _ = read_in_txn(restore_sql, composite=True)
        cd = (f" md5={comp[0][:16]} secdef={comp[1]} config={comp[2]} deps={comp[3]}"
              if comp else " (composite read failed)")
        lines.append(f"  {eid:24s} path B  restore alone   stmts="
                     f"{len(split_statements(restore_sql))} in-txn fp {fp[:16]} ok{cd}")

    lines.append(f"  {eid:24s} {'PASS' if ok else 'FAIL'}")
    return ok, lines


def self_test(base_fp):
    """Two different claims, both required:
      1. FIDELITY   drop + recreate the policy with its ORIGINAL text returns the
                    baseline fingerprint. If this fails, restores are lossy.
      2. DETECTION  drop + recreate with a WEAKENED body must NOT return it. If
                    this fails, the fingerprint is blind and every PASS here is
                    meaningless."""
    print("SELF-TEST 1 (fidelity): drop + recreate with the ORIGINAL text")
    print("SELF-TEST 2 (detection): drop + recreate with a WEAKENED body")
    orig = ("CREATE POLICY contacts_select ON public.contacts AS PERMISSIVE FOR SELECT"
            " TO public USING (is_account_member(account_id));")
    weak = orig.replace("is_account_member(account_id)", "true")
    assert weak != orig, "the weakened variant is identical to the original"

    ok = True
    fp_f, rc, out, err = read_in_txn("DROP POLICY contacts_select ON public.contacts;\n" + orig)
    fid = (rc == 0 and fp_f == base_fp)
    print(f"  fidelity : fp={fp_f}  == baseline: {fid}")
    if rc != 0:
        print("    " + (err or out).strip().splitlines()[0][:170])
    ok &= fid

    fp_d, rc, out, err = read_in_txn("DROP POLICY contacts_select ON public.contacts;\n" + weak)
    det = (rc == 0 and fp_d is not None and fp_d != base_fp)
    print(f"  detection: fp={fp_d}  != baseline: {det}")
    if rc != 0:
        print("    " + (err or out).strip().splitlines()[0][:170])
    ok &= det

    fp, _, _, _ = fingerprint_now()
    if fp != base_fp:
        print(f"  SELF-TEST FAILED: baseline not restored ({fp})")
        ok = False
    else:
        print(f"  baseline intact after the self-test: {fp}")
    if not ok:
        print("  SELF-TEST FAILED: the fingerprint is not fit to gate anything.")
    return ok


JUMP = (ast.Return, ast.Raise, ast.Break, ast.Continue)


def falls_through(stmt):
    """True if control can reach the statement AFTER `stmt` in the same list.

    Only control-flow constructs get special treatment. Everything else --
    including def/class/assign/expression -- completes normally by definition and
    falls through, so an earlier naive version that recursed into every `.body`
    reported every following top-level `def` as dead code.

      If     falls through if it has NO else (the implicit else falls through),
              or if any branch falls through.
      With   falls through iff its body does.
      Match  falls through iff some case body does.
      While/For/Try are conservatively ALWAYS falling through: resolving them
              needs full dataflow, and a wrong "does not fall through" would
              accuse live code of being dead. That costs a missed finding, never
              a false accusation."""
    if isinstance(stmt, JUMP):
        return False
    if isinstance(stmt, ast.If):
        if not stmt.orelse:
            return True
        return any(falls_through(s) for s in stmt.body) or \
            any(falls_through(s) for s in stmt.orelse)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return any(falls_through(s) for s in stmt.body)
    if hasattr(ast, "Match") and isinstance(stmt, ast.Match):
        return any(any(falls_through(s) for s in c.body) for c in stmt.cases)
    if isinstance(stmt, (ast.While, ast.For, ast.AsyncFor, ast.Try, ast.TryStar
                         if hasattr(ast, "TryStar") else ast.Try)):
        return True
    return True


def dead_code_check(path):
    """ast check for statements that control flow can never reach.

    Walks EVERY statement list in the tree (module, function, class, and every
    nested block) and reports any statement sitting after one that cannot fall
    through. Negative-controlled: run against a file that really does contain
    dead code and confirm it goes red -- the first version of this check passed
    a file with an obvious `if cond: return` followed by a print, which is the
    DEFECT 1 disease this whole tool exists to avoid."""
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src, filename=path)
    offenders = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for i, stmt in enumerate(block[:-1]):
                if not falls_through(stmt):
                    nxt = block[i + 1]
                    kind = type(stmt).__name__.lower()
                    offenders.append((getattr(node, "name", None),
                                      stmt.lineno, nxt.lineno, kind))
    funcs = [n.name for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    nonblank = sum(1 for ln in src.splitlines()
                   if ln.strip() and not ln.strip().startswith("#"))
    print(f"DEAD CODE CHECK (ast) on {path}")
    print(f"  parsed OK: {len(funcs)} functions: {', '.join(funcs)}")
    print(f"  non-blank, non-comment lines: {nonblank}")
    if offenders:
        print(f"  FAIL {len(offenders)} unreachable statement(s) after a jump:")
        for name, a, b, kind in offenders:
            where = f"in {name}" if name else "at module level"
            print(f"    {where}: line {b} is unreachable after the {kind} at line {a}")
        return False
    print("  OK   no statement follows a non-fall-through in any block")
    return True


# -------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--break-test", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--dead-code", action="store_true")
    ap.add_argument("--break-m10-capture",
                    choices=["empty", "no-secdef", "no-searchpath", "no-stable"])
    ap.add_argument("--break-m10-baseline-md5", action="store_true")
    ap.add_argument("--break-m10-no-restore", action="store_true")
    args = ap.parse_args()

    if args.dead_code:
        here = __file__ if __file__.endswith(".py") else "preflight6.py"
        return 0 if dead_code_check(here) else 1

    print("=" * 78)
    mode = ""
    if args.break_test:
        mode = "*** BREAK TEST: a red run is REQUIRED ***"
    elif args.self_test:
        mode = "*** SELF-TEST ***"
    elif args.break_m10_capture:
        mode = f"*** BREAK TEST (capture guard: {args.break_m10_capture}): a red run is REQUIRED ***"
    elif args.break_m10_baseline_md5:
        mode = "*** BREAK TEST (guard 4a): a red run is REQUIRED ***"
    elif args.break_m10_no_restore:
        mode = "*** BREAK TEST (paths A/B detect the mutation): a red run is REQUIRED ***"
    print("PREFLIGHT v6  " + mode)
    print("=" * 78)

    entries = parse_manifest()
    base_fp, rc, out, err = fingerprint_now()
    print(f"baseline fingerprint : {base_fp}")
    print(f"v5 baseline token    : {V5_BASELINE_FP}  (extended by pg_proc.proargdefaults)")
    print(f"entries parsed       : {len(entries)}")

    if base_fp is None or rc != 0:
        print(f"FAIL the fingerprint query did not run (rc={rc} sqlstate={sqlstate(err)})")
        return 3
    if base_fp != EXPECTED_BASELINE_FP:
        print(f"FAIL the v6 baseline token is {base_fp}, expected {EXPECTED_BASELINE_FP}.")
        print("The committed state changed since this definition was written.")
        return 3

    if not tool_selfcheck(base_fp):
        return 3
    if len(entries) != EXPECTED_ENTRIES:
        print(f"FAIL entry count {len(entries)} != {EXPECTED_ENTRIES}")
        return 1

    order = run_order(entries)
    print(f"run order            : {' -> '.join(e['id'] for e in order)}")
    ids = [e["id"] for e in order]
    want = [BASE_ID] + [e["id"] for e in entries
                        if e["id"] not in (BASE_ID, FN_ID)] + [FN_ID, BASE_ID]
    if ids != want:
        print("FAIL run order violates the contract")
        print(f"  got  {ids}")
        print(f"  want {want}")
        return 1
    if ids[0] != BASE_ID or ids[-1] != BASE_ID or ids[-2] != FN_ID:
        print(f"FAIL run order violates the contract (first={ids[0]} "
              f"second-last={ids[-2]} last={ids[-1]})")
        return 1
    if sorted(ids) != sorted([e["id"] for e in entries] + [BASE_ID]):
        print("FAIL run order did not preserve the manifest entry set")
        return 1
    print(f"  OK  {BASE_ID} first and last, {FN_ID} immediately before the final "
          f"{BASE_ID}, all {len(entries)} manifest entries walked once plus the "
          "closing baseline bookend")

    print("  guard 4b substrings  : mandated " + ", ".join(repr(s) for s in MANDATED_SUBSTRINGS)
          + "  |  added " + ", ".join(repr(s) for s in ADDED_SUBSTRINGS))

    # ---- GUARD 4a: the capture and the run-level baseline check ------------
    print()
    print("-- capture --")
    capture, cap_err = capture_definition()
    lines = []
    if cap_err:
        lines.append(f"  GUARD 4a  FAIL  could not capture the definition: {cap_err}")
        print("\n".join(lines))
        return 3
    lines.append(f"  capture: pg_get_functiondef over base64 = {len(capture)} chars, "
                 f"{capture.count(chr(13))} CR, sha256 "
                 f"{hashlib.sha256(capture.encode()).hexdigest()[:16]}")
    lines.append("  transport: base64, because -q -A -t rewrites the 20 CRLF pairs in "
                 "prosrc to LF and returns 738 of 758 bytes")
    guard_ok = guard_capture_start(capture, ("deadbeef" * 4 if args.break_m10_baseline_md5
                                             else BASELINE_MD5), lines)
    print("\n".join(lines))
    if not guard_ok:
        print()
        if args.break_m10_baseline_md5:
            # The guard fired exactly as designed. In break-test mode that is the
            # REQUIRED outcome, so issue the break-test verdict instead of the
            # abort verdict a real run would get.
            print("BREAK TEST PASSED -- guard 4a is red on a prosrc that does not match")
            print("the value the manifest was written against, and the run was refused.")
            print("The line above names the assertion that was violated.")
            return 0
        print("ABORT: the database is not the database the manifest was written against.")
        print("Nothing was applied. Do not run the Executor.")
        return 3

    if args.break_m10_capture:
        broken = capture
        if args.break_m10_capture == "empty":
            broken = ""
        elif args.break_m10_capture == "no-secdef":
            broken = capture.replace(" SECURITY DEFINER", "", 1)
        elif args.break_m10_capture == "no-searchpath":
            broken = re.sub(r" SET search_path TO 'public'", "", broken, count=1)
        elif args.break_m10_capture == "no-stable":
            broken = broken.replace(" STABLE", "", 1)
        print()
        print(f"-- break test: captured definition corrupted ({args.break_m10_capture}) --")
        print(f"   {len(capture)} chars -> {len(broken)} chars")

    m10_restore = None
    if args.break_m10_capture:
        m10_restore = broken
    elif args.break_m10_no_restore:
        # Simulate the failure mode this whole tool exists to catch: a "restore"
        # that keeps every isolation attribute but does NOT restore the body. All
        # capture guards therefore PASS by construction, so the only thing that
        # can catch it is the in-transaction fingerprint reading prosrc -- which is
        # exactly the property paths A and B are here to demonstrate.
        m10_restore = re.sub(r"(?s)\$function\$.*\$function\$",
                             "$function$\n  SELECT true;\n$function$", capture, count=1)
        print()
        print(f"-- break test: the restore keeps SECURITY DEFINER / search_path / STABLE "
              f"but swaps the body for SELECT true ({len(m10_restore)} chars) --")
        print("   GUARD 4b will PASS by construction; only the in-transaction "
              "fingerprint can catch this")

    if args.self_test:
        print()
        return 0 if self_test(base_fp) else 1

    all_ok = True
    for e in order:
        ok, elines = check_entry(e, base_fp, capture,
                                 break_it=(args.break_test and e["id"] == BREAK_TARGET),
                                 m10_restore=(m10_restore if e["id"] == FN_ID else None))
        all_ok &= ok
        print("\n".join(elines))
        print()

    print("\n".join(["", "=" * 78]))
    dp_ok, dp_lines = check_drop_policy_idempotence(entries)
    all_ok &= dp_ok
    print("\n".join(dp_lines))

    fp2, _, _, _ = fingerprint_now()
    if fp2 != base_fp:
        print(f"FAIL campaign left residue: {fp2} != {base_fp}")
        all_ok = False
    else:
        print(f"committed state unchanged after the whole run: {fp2}")
    comp, crc, _, cerr = read_in_txn("", composite=True)
    print(f"committed is_account_member in a read-only txn: "
          f"{'/'.join(comp) if comp else '(unreadable)'} rc={crc}")

    print("=" * 78)
    breaks = (args.break_test or args.break_m10_capture or args.break_m10_baseline_md5
              or args.break_m10_no_restore)
    if breaks:
        if all_ok:
            print("BREAK TEST FAILED -- the preflight passed a known-corrupted input.")
            print("It is decoration. Do not run the Executor against it.")
            return 2
        print("BREAK TEST PASSED -- preflight is red. The failure line above names the "
              "assertion that was violated.")
        return 0
    print("PREFLIGHT PASSED" if all_ok else "PREFLIGHT FAILED")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
