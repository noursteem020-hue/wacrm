"""Paths for the RLS mutation campaign tools.

Everything resolves relative to this file's own location, so a clean clone works
from any directory. The previous versions hard-coded three absolute paths on one
machine:

    C:/Users/FX-tec/AppData/Local/hermes/profiles/jeff/cache/scratch
    C:/Users/FX-tec/Desktop/wacrm-work
    C:/Users/FX-tec/AppData/Local/Temp/evidence

which meant a MEASURED claim could not be reproduced by anyone else. Override
with environment variables if you need to write evidence elsewhere:

    WACRM_REPO      path to the working clone   (default: two levels up from here)
    WACRM_EVIDENCE  where raw output is written (default: <repo>/docs/evidence/b2)
    WACRM_DB_CONTAINER  docker container name  (default: supabase_db_wacrm)
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
# tools/rls-mutation/ -> repo root
REPO = os.environ.get("WACRM_REPO") or os.path.abspath(os.path.join(HERE, "..", ".."))

EVIDENCE = os.environ.get("WACRM_EVIDENCE") or os.path.join(REPO, "docs", "evidence", "b2")

MANIFEST = os.path.join(HERE, "mutations.yaml")
PROBE_REL = os.path.join("src", "lib", "tenant", "isolation.sql")
PROBE = os.path.join(REPO, PROBE_REL)

CONTAINER = os.environ.get("WACRM_DB_CONTAINER", "supabase_db_wacrm")

# B2's measured baseline. Both values were read off this database before any
# mutation ran; every run must end back here or it is a hard stop.
BASELINE_MD5 = "026fa63f24c5f54584758c4f5d314408"
EXPECTED_BASELINE_FP = "bfae0aea057682e5403f70c94f0b5f61"
EXPECTED_DEPENDENT_POLICIES = 98

# The blob the B2 campaign ran against. That is the tree at 1bfa3929, which is this
# file as of commit f251f9a's parent. The Executor used the value below to assert
# "the probe has not changed since the manifest was written"; at that pin a run
# tested the pre-B1.1 probe.
#
# After B1.1 (commit f251f9a) the probe changed, so the pin was re-read from git
# rather than from the working file:
#
#     git rev-parse f251f9a:src/lib/tenant/isolation.sql
#
# which returned f50a7d80a9e756e1a0c7aa3739bb935c9c80f463. MEASURED that this
# equals `git hash-object src/lib/tenant/isolation.sql` in a clean tree, so the
# value is git's own and not a worktree artefact of CRLF normalisation. Had the
# two disagreed, this pin would have recorded a hash that no clone could reproduce.
EXPECTED_PROBE_BLOB = "f50a7d80a9e756e1a0c7aa3739bb935c9c80f463"


# --- how to compare file bytes across git, and what not to do ------------------
#
# Verify a committed file is unchanged with git's own blob hash, never with md5sum
# on a pipe:
#
#     git rev-parse <commit>:<path>          # the committed bytes
#     git hash-object <path>                 # the working bytes
#
# Both must be equal in a clean tree. Do NOT do this:
#
#     git show <commit>:<path> | md5sum       # WRONG
#
# git's output goes through a pipe, and this repository's files carry CRLF: the
# blob at bd7ed58 and the worktree file hashed identically under `git hash-object`
# (9529a37d...) yet differed under `md5sum` on the piped form, because the pipe
# converted 14 CR bytes to LF. That difference is an artefact of the measurement,
# not of the file, and it cost a false alarm about fabricated evidence.
#
# This is the third time CR-vs-LF has bitten this campaign. The first two were in
# the restore path: a body that looked identical in an editor produced a third
# md5, and a restore that printed its definition instead of executing it. Both
# times the lesson was the same -- compare the bytes git holds, not bytes a pipe
# reassembled.


def ensure_evidence_dir():
    os.makedirs(EVIDENCE, exist_ok=True)
    return EVIDENCE