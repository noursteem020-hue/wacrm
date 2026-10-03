import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { resolveTenantFromHost } from "./resolve";

const ORIGINAL = process.env.LOCALHOST_TENANT;

beforeEach(() => {
  delete process.env.LOCALHOST_TENANT;
});
afterEach(() => {
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

  it("returns null for localhost when LOCALHOST_TENANT is unset", () => {
    expect(resolveTenantFromHost("localhost:3000")).toBeNull();
  });

  it("returns LOCALHOST_TENANT when set", () => {
    process.env.LOCALHOST_TENANT = "acme";
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