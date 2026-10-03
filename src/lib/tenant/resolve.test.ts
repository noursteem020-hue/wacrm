import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resolveTenantFromHost } from "./resolve";

const ORIGINAL = process.env.LOCALHOST_TENANT;

beforeEach(() => {
  // Every case starts from an unset variable, so no case can inherit the
  // previous case's value through the ambient environment.
  delete process.env.LOCALHOST_TENANT;
});
afterEach(() => {
  // vi.stubEnv() records the pre-test value; unstubAllEnvs() is what puts it
  // back. Without it a stubbed LOCALHOST_TENANT leaks into every later test
  // in this file. The explicit ORIGINAL restore stays as the belt-and-braces
  // second layer, matching resolve.known-defects.test.ts:72-78.
  vi.unstubAllEnvs();
  if (ORIGINAL === undefined) delete process.env.LOCALHOST_TENANT;
  else process.env.LOCALHOST_TENANT = ORIGINAL;
});

describe("resolveTenantFromHost", () => {
  it("extracts the subdomain from crm.acme.com", () => {
    expect(resolveTenantFromHost("crm.acme.com")).toBe("acme");
  });

  it("strips a port", () => {
    expect(resolveTenantFromHost("crm.acme.com:3000")).toBe("acme");
  });

  it("lowercases the host before resolving", () => {
    expect(resolveTenantFromHost("CRM.Acme.COM")).toBe("acme");
  });

  it("strips a leading www.", () => {
    expect(resolveTenantFromHost("www.acme.com")).toBe("acme");
  });

  // NON-VACUOUS, TWO-SIDED. Before 2026-10-03 this test passed for the wrong
  // reason. vitest.config.ts:14-18 injects only ENCRYPTION_KEY and
  // META_APP_SECRET, so LOCALHOST_TENANT was simply ABSENT in the test
  // environment and the assertion below observed the null that a missing
  // variable produces — it never distinguished "the branch correctly found
  // LOCALHOST_TENANT falsy" from "the branch does not read it at all".
  // MEASURED 2026-10-03: mutating resolve.ts:28 to `return null;` outright,
  // or to read `process.env.LOCALHOST_TENANT_FALLBACK`, left this test GREEN.
  //
  // Declaring the precondition is necessary but NOT sufficient: stubbing an
  // absent variable to absent still returns null, so a single-sided
  // "unset -> null" assertion cannot fail under either mutation. The second
  // half below is what makes it load-bearing — it pins that the very same
  // host resolves differently once the variable is truthy, which is only
  // possible if resolve.ts:28 actually reads it.
  it("returns null for localhost when LOCALHOST_TENANT is unset", () => {
    // Declare the variable as KNOWN-AND-ABSENT rather than incidentally
    // absent: stubEnv records the pre-test value so unstubAllEnvs() can
    // restore it, and the delete then makes the state deterministic even if a
    // future vitest.config.ts starts injecting LOCALHOST_TENANT globally.
    vi.stubEnv("LOCALHOST_TENANT", "local-dev");
    delete process.env.LOCALHOST_TENANT;
    // The precondition itself is asserted, not assumed. If this ever fails,
    // the env injection changed and the reason is named here rather than
    // surfacing as a confusing resolver failure further down.
    expect(process.env.LOCALHOST_TENANT).toBeUndefined();
    expect(resolveTenantFromHost("localhost:3000")).toBeNull();

    // Same host, same test, variable now truthy: the difference in result is
    // produced ONLY by the read at resolve.ts:28.
    vi.stubEnv("LOCALHOST_TENANT", "local-dev");
    expect(resolveTenantFromHost("localhost:3000")).toBe("local-dev");
  });

  it("returns LOCALHOST_TENANT when set", () => {
    // Same precondition stated through the idiomatic stub helper rather than
    // by direct assignment, so vi.unstubAllEnvs() can restore it in afterEach.
    vi.stubEnv("LOCALHOST_TENANT", "acme");
    expect(resolveTenantFromHost("localhost:3000")).toBe("acme");
  });

  it("returns null for a bare IP address", () => {
    expect(resolveTenantFromHost("127.0.0.1:3000")).toBeNull();
  });

  it("returns null for a two-label host such as acme.com", () => {
    expect(resolveTenantFromHost("acme.com")).toBeNull();
  });

  it("returns null for null, undefined and empty input", () => {
    expect(resolveTenantFromHost(null)).toBeNull();
    expect(resolveTenantFromHost(undefined)).toBeNull();
    expect(resolveTenantFromHost("")).toBeNull();
  });

  it("never throws on malformed input", () => {
    for (const host of ["...", "::1", "%%%%", "a".repeat(500)]) {
      expect(() => resolveTenantFromHost(host)).not.toThrow();
    }
  });
});