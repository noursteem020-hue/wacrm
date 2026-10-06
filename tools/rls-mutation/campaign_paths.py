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

# The blob B2 was run against, i.e. this file as of commit 1bfa3929. It is NOT the
# current blob: commit f251f9a (B1.1) changed the probe. A run at this pin is
# testing the pre-B1.1 probe and will abort, which is the point -- the pin is an
# assertion, not a default.
EXPECTED_PROBE_BLOB = "52e545b839c214c798b0004a37d9fecc2c3de1e4"


def ensure_evidence_dir():
    os.makedirs(EVIDENCE, exist_ok=True)
    return EVIDENCE