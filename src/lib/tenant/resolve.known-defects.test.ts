/**
 * Known-defect evidence for `resolveTenantFromHost`.
 *
 * WHAT THIS FILE IS FOR
 * ---------------------
 * It records, as executable assertions, behaviour that is KNOWN WRONG but
 * that the codebase does not yet fix. Every wrong case is `it.skip`, so the
 * suite stays green and nothing here can break CI. Each skipped case carries
 * a comment naming the exact assertion it will make once someone decides how
 * to fix it. Flipping `.skip` -> `()` (or `.only`) is the whole "enable" step;
 * the assertion body below is already correct.
 *
 * NO FIX IS IMPLEMENTED HERE, AND NONE IS RECOMMENDED. See docs/tenancy.md.
 * The choice between "config map", "public-suffix list", "exact-N-labels +
 * allowlist" and "leave it, document it" is the owner's, not this file's.
 *
 * DEFECT 1 — the loopback fallback also answers for real domains
 * ----------------------------------------------------------
 * (READ from src/lib/tenant/resolve.ts:27-29)
 *   if (parts.length < 3) {
 *     return process.env.LOCALHOST_TENANT || null;
 *   }
 * The `< 3 labels` branch is entered by `acme.com` exactly as it is by
 * `localhost`. The function cannot tell a bare registrable domain from a
 * loopback name, so one env var decides both. With LOCALHOST_TENANT=local-dev
 * (MEASURED present at .env.local:42) the host `acme.com` resolves to
 * "local-dev" instead of "acme". MEASURED by direct probe:
 *   LOCALHOST_TENANT=local-dev -> acme.com = "local-dev"  (should be "acme")
 *   LOCALHOST_TENANT unset     -> acme.com = null
 * Note it only takes TWO labels to trip this. `acme.co.uk` has THREE and so
 * does NOT reach this branch — it falls into defect 2 below and returns "co".
 *
 * DEFECT 2 — the second-to-last label assumes a two-label public suffix
 *
 * A SECOND, INDEPENDENT DEFECT (READ from resolve.ts:38-42, already
 * acknowledged in that file's own comment): the resolver takes the
 * second-to-last label, which is only correct for a two-label public suffix.
 * MEASURED: `crm.acme.co.uk` -> "co" and `crm.acme.com.au` -> "com". The two
 * are NOT equally dangerous. "com" PASSES validateSlug (slug.ts:15 allows
 * 3-63 chars) and is a valid-looking wrong slug. "co" FAILS it — 2 chars is
 * below the minimum — so the .co.uk case would be rejected at validation
 * rather than silently routed to a wrong tenant. Silent-wrong is worse than
 * loud-failure, and only the "com" shape is silent-wrong. This defect is NOT
 * gated on LOCALHOST_TENANT and persists when the variable is unset.
 *
 * WHY THE ENV VAR IS SET EXPLICITLY IN EVERY CASE
 * -----------------------------------------------
 * vitest.config.ts (READ, lines 14-18) injects only ENCRYPTION_KEY and
 * META_APP_SECRET; .env.local is not loaded into process.env by the runner.
 * So without an explicit `process.env.LOCALHOST_TENANT = ...` these
 * assertions would pass VACUOUSLY (env unset -> the <3-label branch returns
 * null -> "the expected value" and "null" would coincide by accident).
 * Every case below therefore sets or deletes the variable on purpose.
 *
 * THE ACTIVE CASES ARE DELIBERATE
 * -------------------------------
 * Six cases assert current CORRECT or current-characterization behaviour and
 * run live rather than being skipped: `crm.acme.com` (x2, env set and unset),
 * `localhost`, `crm.acme.com:3000`, `acme.com` with the env var unset (which
 * returns null), and `crm.acme.co.uk` returning today's wrong "co". They are
 * asserted live so this file cannot silently rot: if a future change breaks
 * the thing that currently works, that shows up as a real failure instead of
 * being hidden behind a wall of skips.
 *
 * CASE COUNTS (MEASURED via `npx vitest run ... --reporter=verbose`)
 * 13 cases total = 6 active + 7 it.skip. The 7th skip was added last; the
 * other 6 predate it. Adding a skip raises the total and the skipped count
 * together, so the ACTIVE count is the number that must stay fixed.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resolveTenantFromHost } from "./resolve";

// Captured at module load, before any test mutates it.
const ORIGINAL = process.env.LOCALHOST_TENANT;

// Same discipline as resolve.test.ts:6-12 — delete before every test so no
// case inherits the previous case's value, restore after so this file leaks
// nothing into the rest of the suite.
beforeEach(() => {
  delete process.env.LOCALHOST_TENANT;
});
afterEach(() => {
  if (ORIGINAL === undefined) delete process.env.LOCALHOST_TENANT;
  else process.env.LOCALHOST_TENANT = ORIGINAL;
});

describe("KNOWN DEFECT: bare registrable domains are indistinguishable from loopback", () => {
  it.skip("acme.com must resolve to its own tenant, not to LOCALHOST_TENANT", () => {
    // Would assert: a bare registrable domain resolves from the DOMAIN, and
    // the loopback env var must not be able to answer for it.
    // Correct today only when LOCALHOST_TENANT happens to be unset.
    process.env.LOCALHOST_TENANT = "local-dev";
    expect(resolveTenantFromHost("acme.com")).toBe("acme");
  });

  it.skip("acme.co.uk must resolve to acme, not to the suffix label co", () => {
    // Would assert: a two-label public suffix does not shift the answer.
    // This is DEFECT 2, not defect 1 — `acme.co.uk` splits into THREE labels
    // so it never reaches the <3 branch at all, and LOCALHOST_TENANT is
    // irrelevant here. MEASURED: returns "co" both set and unset. LOW SEVERITY:
    // validateSlug rejects "co" (2 chars, minimum is 3 — slug.ts:15), so this
    // fails loudly at validation rather than silently misrouting. Contrast the
    // .com.au case below, which does NOT fail loudly. Left in this describe
    // block because it was nominated for the suite.
    expect(resolveTenantFromHost("acme.co.uk")).toBe("acme");
  });

  it.skip("localhost and acme.com must not resolve to the same slug", () => {
    // Would assert: the resolver can TELL the two apart, i.e. the loopback
    // fallback is scoped to loopback and does not fire for a real domain.
    // This is the single assertion that most directly states the defect.
    process.env.LOCALHOST_TENANT = "local-dev";
    expect(resolveTenantFromHost("localhost")).toBe("local-dev");
    expect(resolveTenantFromHost("acme.com")).not.toBe("local-dev");
  });

  it.skip("127.0.0.1 must be handled consistently with localhost", () => {
    // Would assert: a loopback IP gets the SAME loopback answer as the name
    // `localhost`. resolve.ts:24 returns null for an IP literal BEFORE the
    // env fallback at line 28 is ever reached, so today a raw IP and the
    // loopback name disagree — and which of the two is "right" is an owner
    // decision, not an obvious bug. Kept skipped so the asymmetry stays
    // visible and the choice stays deliberate.
    process.env.LOCALHOST_TENANT = "local-dev";
    expect(resolveTenantFromHost("127.0.0.1")).toBe("local-dev");
  });

  it("LOCALHOST_TENANT unset: acme.com returns null (characterization)", () => {
    // PASSING, and it is the reason this file is not vacuous. With the env
    // var unset the <3-label branch returns null, so the defect above is
    // silent in any environment where LOCALHOST_TENANT is absent. That is the
    // operator-facing workaround: unset the variable and the wrong slug
    // disappears. It does not make the resolver correct.
    expect(resolveTenantFromHost("acme.com")).toBeNull();
  });
});

describe("KNOWN DEFECT: second-to-last label assumes a two-label public suffix", () => {
  it.skip("crm.acme.co.uk must resolve to acme, not to the suffix label co", () => {
    // Would assert: multi-part public suffixes do not shift the answer.
    // INDEPENDENT of the defect above — no env var is set here, and it is
    // still wrong. Would need a public-suffix list or a config map; an
    // "exact-N-labels" rule does NOT fix this one.
    expect(resolveTenantFromHost("crm.acme.co.uk")).toBe("acme");
  });

  it.skip("crm.acme.com.au must not resolve to the suffix label com", () => {
    // Would assert: the failure is general, not specific to .co.uk. Named in
    // resolve.ts:39 as the motivating example. THIS IS THE HIGH-SEVERITY SHAPE:
    // MEASURED validateSlug("com") -> ok=true, so "com" is a valid-looking
    // wrong slug that survives validation. No env var is set and it is still
    // wrong, so it is not fixed by any LOCALHOST_TENANT discipline.
    expect(resolveTenantFromHost("crm.acme.com.au")).toBe("acme");
  });

  it.skip("acme.com.au must resolve to acme — same label count as crm.acme.com, opposite answer", () => {
    // Would assert: a bare registrable domain under a multi-part suffix still
    // names its own tenant, and — the part that matters for choosing a fix —
    // that the label COUNT carries no usable signal about the answer.
    //
    // WHY THIS CASE EXISTS ALONGSIDE crm.acme.com.au
    // MEASURED side by side (probe against this exact working tree):
    //   crm.acme.com    -> "acme"   CORRECT   (3 labels)
    //   acme.com.au     -> "com"    WRONG     (3 labels)  <- this case
    // Two hosts, the SAME number of labels, OPPOSITE correctness. That single
    // pair is the refutation of every part-count rule: an "exact-N-labels"
    // fix, or any threshold on parts.length, cannot separate these two inputs,
    // because it cannot see the difference between them. MEASURED "com" is 3
    // chars and validateSlug("com") -> ok=true, so this is the same
    // VALID-LOOKING-BUT-WRONG shape as crm.acme.com.au, not the loud
    // validateSlug failure that the .co.uk shape produces.
    //
    // ROOT CAUSE THIS WOULD PIN DOWN: resolve.ts:27 and resolve.ts:43 both
    // reason about label position/count, never about where the public suffix
    // ends. "com" is the second-to-last label of "acme.com.au" only because
    // the resolver assumes the suffix is always two labels. Fixing it needs
    // the tenant name to come from somewhere that knows the real suffix
    // boundary — a public-suffix list, or an explicit domain -> tenant table.
    // Until the owner picks one of those, this stays skipped: flipping it to
    // active would assert an answer the code cannot currently produce, and
    // would fail the suite rather than document the defect.
    //
    // NOT gated on LOCALHOST_TENANT: 3 labels never reaches the <3 branch at
    // resolve.ts:27, so the env var cannot influence the answer either way.
    // Kept with no env var set, matching the crm.acme.com.au case above.
    expect(resolveTenantFromHost("acme.com.au")).toBe("acme");
  });

  it("crm.acme.co.uk returns 'co' today (characterization, asserted live)", () => {
    // PASSING characterization of the CURRENT wrong value. This is the one
    // place the bad output is deliberately locked in, so that a future change
    // to the suffix logic cannot alter behaviour without this file noticing.
    // If a fix lands, this test fails first and forces the intent to be
    // re-declared — which is the point.
    expect(resolveTenantFromHost("crm.acme.co.uk")).toBe("co");
  });
});

describe("CONTROLS: current correct behaviour that must not regress", () => {
  it("crm.acme.com resolves to acme with LOCALHOST_TENANT set", () => {
    // The three-or-more-label path already ignores the env var, so this is
    // the behaviour the defect DOES NOT break. Asserted with the var SET
    // deliberately: that is precisely the asymmetry between this case and
    // the skipped acme.com case above.
    process.env.LOCALHOST_TENANT = "local-dev";
    expect(resolveTenantFromHost("crm.acme.com")).toBe("acme");
  });

  it("crm.acme.com resolves to acme with LOCALHOST_TENANT unset", () => {
    expect(resolveTenantFromHost("crm.acme.com")).toBe("acme");
  });

  it("localhost resolves to LOCALHOST_TENANT — the fallback's intended use", () => {
    // The loopback fallback is intentional. Only its SCOPE is wrong, so any
    // fix must preserve this exact behaviour.
    process.env.LOCALHOST_TENANT = "local-dev";
    expect(resolveTenantFromHost("localhost")).toBe("local-dev");
  });

  it("crm.acme.com:3000 still strips the port before resolving", () => {
    // Guards against a fix that trims labels naively and breaks port
    // handling, or that validates the host string before stripping ":".
    process.env.LOCALHOST_TENANT = "local-dev";
    expect(resolveTenantFromHost("crm.acme.com:3000")).toBe("acme");
  });
});
